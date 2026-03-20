import asyncio
import json
import logging
import time
from types import SimpleNamespace
from typing import Optional, Dict, List, Tuple
from decimal import Decimal, ROUND_UP
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models import (
    Product, ProductConfig, Account, PriceSnapshot,
    RepricerRun, PriceChange
)
from app.models.app_setting import AppSetting
from app.services import (
    catalog_parser, create_wb_client, telegram_notifier
)
from app.utils.jwt import is_token_expired
from app.redis_client import cache_get, cache_set

logger = logging.getLogger(__name__)


class RepricerEngine:
    """
    Движок репрайсера с двумя стратегиями ценообразования
    
    Стратегии:
    1. RRP - поддержание финальной цены в диапазоне [РРЦ - tolerance; РРЦ + tolerance]
    2. Competitor - привязка к цене конкурента с заданным смещением
    """
    
    def __init__(self, db: Session, user_id: Optional[int] = None):
        self.db = db
        self.user_id = user_id  # None → глобальные/дефолтные настройки
        self._user_settings: Optional[Dict] = None  # loaded once per run

    def _load_user_settings(self) -> Dict:
        """Load all repricer settings in 2 queries to avoid N+1 per product."""
        from app.models.app_setting import AppSetting
        keys = [
            "min_margin_pct", "wb_wallet_discount_pct",
            "commission_type", "commission_value",
            "logistics_type", "logistics_value",
            "acquiring_type", "acquiring_value",
        ]
        result: Dict = {
            "min_margin_pct": "0.0", "wb_wallet_discount_pct": "0",
            "commission_type": "pct", "commission_value": "0.0",
            "logistics_type": "rub", "logistics_value": "0.0",
            "acquiring_type": "pct", "acquiring_value": "0.0",
        }
        # Global defaults (user_id=None)
        for row in self.db.query(AppSetting).filter(
            AppSetting.key.in_(keys), AppSetting.user_id == None
        ).all():
            result[row.key] = row.value
        # User-specific overrides
        if self.user_id is not None:
            for row in self.db.query(AppSetting).filter(
                AppSetting.key.in_(keys), AppSetting.user_id == self.user_id
            ).all():
                result[row.key] = row.value
        return result

    def _round_up(self, value: Decimal) -> int:
        """
        Округление вверх до ближайшего целого рубля
        
        Args:
            value: Значение для округления
            
        Returns:
            Целое число рублей
        """
        return int(value.quantize(Decimal('1'), rounding=ROUND_UP))
    
    def _calculate_seller_price_from_final(
        self,
        final_price: Decimal,
        spp: Decimal
    ) -> Decimal:
        """
        Обратный расчет цены продавца из финальной цены
        
        final_price = seller_price * (1 - spp/100)
        seller_price = final_price / (1 - spp/100)
        
        Args:
            final_price: Желаемая финальная цена покупателя
            spp: СПП в процентах
            
        Returns:
            Цена продавца (округленная вверх)
        """
        if spp >= 100:
            raise ValueError(f"СПП {spp}% >= 100%, невозможно рассчитать цену продавца")
        
        seller_price = final_price / (1 - spp / 100)
        return Decimal(self._round_up(seller_price))
    
    async def _cached_parse_product(self, nm_id: int):
        """
        Парсинг товара из WB Catalog API с кешированием в Redis (TTL=180с).
        Снижает нагрузку на WB Catalog при прогоне по многим товарам.
        """
        cache_key = f"wb_catalog:{nm_id}"
        cached = cache_get(cache_key)
        if cached is not None:
            return SimpleNamespace(
                final_price=Decimal(str(cached['final_price'])),
                spp=Decimal(str(cached['spp'])) if cached['spp'] is not None else None,
                is_in_promo=cached['is_in_promo'],
                total_quantity=cached['total_quantity'],
                in_stock=cached['in_stock'],
            )

        product_data = await catalog_parser.parse_product(nm_id)
        if product_data:
            cache_set(cache_key, product_data.to_dict(), ttl=180)
        return product_data

    async def _get_current_price_data(
        self,
        product: Product,
        account: Account
    ) -> Optional[Dict]:
        """
        Получение текущих данных о цене товара из WB API
        
        Returns:
            dict с полями: seller_price, spp, is_in_promo или None
        """
        try:
            async with create_wb_client(account.wb_api_token) as client:
                prices = await client.get_prices(nm_ids=[product.nm_id])
                
                if not prices:
                    logger.warning(f"Товар nm_id={product.nm_id} не найден в WB API")
                    return None
                
                price_data = prices[0]
                
                # Парсим текущую цену через catalog для получения СПП (с Redis-кешем)
                catalog_product = await self._cached_parse_product(product.nm_id)

                if not catalog_product:
                    logger.warning(f"Не удалось спарсить nm_id={product.nm_id} из каталога")
                    return None
                
                return {
                    'seller_price': price_data.seller_price,
                    'final_price': catalog_product.final_price,
                    'spp': catalog_product.spp,
                    'is_in_promo': catalog_product.is_in_promo,
                    'total_quantity': catalog_product.total_quantity
                }
                
        except Exception as e:
            logger.error(f"Ошибка получения данных для nm_id={product.nm_id}: {e}")
            return None
    
    async def _strategy_rrp(
        self,
        product: Product,
        config: ProductConfig,
        current_data: Dict,
        wallet_pct: Decimal = Decimal(0)
    ) -> Tuple[Optional[Decimal], str, Optional[str]]:
        """
        Стратегия РРЦ (Recommended Retail Price)

        Цель: финальная цена покупателя (с учётом кошелька, если включено)
        в диапазоне [РРЦ - tolerance; РРЦ + tolerance]

        Args:
            wallet_pct: Скидка кошелька WB в процентах (из глобальных настроек)

        Returns:
            Tuple (новая_цена_продавца, reason_code, reason_message)
        """
        if not config.rrp_price:
            return None, "SKIPPED_INACTIVE", "РРЦ не настроен"

        rrp = config.rrp_price
        tolerance = config.rrp_tolerance or Decimal(5)
        current_final = current_data['final_price']
        spp = current_data['spp']

        if spp is None:
            return None, "PARSE_ERROR", "Не удалось определить СПП"

        consider_wallet = bool(getattr(config, 'rrp_consider_wallet', False))

        if consider_wallet and wallet_pct > 0:
            # Эффективная цена с учётом кошелька WB
            wallet_factor = 1 - wallet_pct / 100
            current_effective = current_final * wallet_factor

            min_effective = rrp - tolerance
            max_effective = rrp + tolerance

            logger.info(
                f"RRP+кошелёк стратегия: текущая с кошельком={current_effective}₽, "
                f"диапазон=[{min_effective}₽; {max_effective}₽], кошелёк={wallet_pct}%"
            )

            if min_effective <= current_effective <= max_effective:
                return None, "NO_CHANGE", (
                    f"Цена с кошельком в допуске РРЦ ({current_effective}₽)"
                )

            # Целевая финальная цена до применения кошелька
            target_final = rrp / wallet_factor

            try:
                new_seller_price = self._calculate_seller_price_from_final(target_final, spp)
                reason_msg = (
                    f"РРЦ+кошелёк стратегия: РРЦ {rrp}₽, кошелёк {wallet_pct}%, "
                    f"целевая до кошелька {target_final:.2f}₽, "
                    f"новая цена продавца {new_seller_price}₽"
                )
                return new_seller_price, "CALCULATED", reason_msg
            except ValueError as e:
                return None, "PARSE_ERROR", f"Ошибка расчета: {e}"

        else:
            # Стандартное поведение без кошелька
            min_final = rrp - tolerance
            max_final = rrp + tolerance

            logger.info(
                f"RRP стратегия: текущая={current_final}₽, "
                f"диапазон=[{min_final}₽; {max_final}₽]"
            )

            if min_final <= current_final <= max_final:
                return None, "NO_CHANGE", f"Цена в допуске РРЦ ({current_final}₽)"

            target_final = rrp

            try:
                new_seller_price = self._calculate_seller_price_from_final(target_final, spp)
                reason_msg = (
                    f"РРЦ стратегия: целевая финальная {target_final}₽, "
                    f"новая цена продавца {new_seller_price}₽"
                )
                return new_seller_price, "CALCULATED", reason_msg
            except ValueError as e:
                return None, "PARSE_ERROR", f"Ошибка расчета: {e}"
    
    async def _get_competitor_price(
        self,
        config: ProductConfig
    ) -> Tuple[Optional[Decimal], Optional[int]]:
        """
        Получение целевой цены конкурента с учётом MultiCompetitor.

        Строит список nm_id конкурентов (из competitor_entries если есть,
        иначе из legacy competitor_nm_id), получает цены и применяет правило выбора.

        Returns:
            Tuple (выбранная_цена_конкурента, winning_nm_id) или (None, None)
        """
        from app.models.competitor_entry import CompetitorEntry

        # Собираем список nm_id
        entries = self.db.query(CompetitorEntry).filter(
            CompetitorEntry.product_config_id == config.id
        ).all()

        if entries:
            nm_ids = [e.nm_id for e in entries]
        elif config.competitor_nm_id:
            nm_ids = [config.competitor_nm_id]
        else:
            return None, None

        rule = config.competitor_select_rule or "min_price"

        # Получаем данные по каждому конкуренту
        prices: List[Tuple[int, Decimal, bool]] = []  # (nm_id, price, in_stock)
        for nm_id in nm_ids:
            try:
                p = await self._cached_parse_product(nm_id)
                if p:
                    prices.append((nm_id, p.final_price, p.in_stock))
            except Exception as e:
                logger.warning(f"Не удалось получить цену конкурента nm_id={nm_id}: {e}")

        if not prices:
            return None, None

        if rule == "cheapest_instock":
            instock = [(nm, price, s) for nm, price, s in prices if s]
            if not instock:
                return None, None  # все out-of-stock → держим текущую цену
            winner = min(instock, key=lambda x: x[1])
        elif rule == "avg_price":
            avg = sum(p for _, p, _ in prices) / len(prices)
            # Возвращаем среднюю цену, winning_nm_id = None (агрегат)
            return Decimal(str(avg)), None
        else:  # min_price (по умолчанию) — минимальная среди всех (кто в наличии)
            instock = [(nm, price, s) for nm, price, s in prices if s]
            pool = instock if instock else prices
            winner = min(pool, key=lambda x: x[1])

        return winner[1], winner[0]

    async def _strategy_competitor(
        self,
        product: Product,
        config: ProductConfig,
        current_data: Dict
    ) -> Tuple[Optional[Decimal], str, Optional[str]]:
        """
        Стратегия Competitor (поддерживает несколько конкурентов).

        Returns:
            Tuple (новая_цена_продавца, reason_code, reason_message)
        """
        try:
            competitor_price, winning_nm_id = await self._get_competitor_price(config)

            if competitor_price is None:
                # Нет конкурентов или все OOS при правиле cheapest_instock
                from app.models.competitor_entry import CompetitorEntry
                entries = self.db.query(CompetitorEntry).filter(
                    CompetitorEntry.product_config_id == config.id
                ).all()
                if not entries and not config.competitor_nm_id:
                    return None, "SKIPPED_INACTIVE", "Конкурент не настроен"
                return None, "COMPETITOR_OOS", "Все конкуренты не в наличии"

            spp = current_data['spp']
            if spp is None:
                return None, "PARSE_ERROR", "Не удалось определить СПП"

            rule = config.competitor_select_rule or "min_price"
            logger.info(
                f"Competitor стратегия: правило={rule}, "
                f"цена конкурента={competitor_price}₽, winning_nm_id={winning_nm_id}"
            )

            # Рассчитываем целевую финальную цену со смещением
            offset_type = config.competitor_offset_type
            offset_direction = config.competitor_offset_direction
            offset_value = config.competitor_offset_value or Decimal(0)

            if offset_type == 'rub':
                if offset_direction == 'above':
                    target_final = competitor_price + offset_value
                else:
                    target_final = competitor_price - offset_value
            else:  # pct
                if offset_direction == 'above':
                    target_final = competitor_price * (1 + offset_value / 100)
                else:
                    target_final = competitor_price * (1 - offset_value / 100)

            # Обратный расчет цены продавца
            try:
                new_seller_price = self._calculate_seller_price_from_final(target_final, spp)

                reason_msg = (
                    f"Competitor ({rule}): конкурент nm_id={winning_nm_id} цена={competitor_price}₽, "
                    f"смещение {offset_direction} {offset_value}{offset_type}, "
                    f"целевая {target_final}₽, новая цена продавца {new_seller_price}₽"
                )

                return new_seller_price, "CALCULATED", reason_msg

            except ValueError as e:
                return None, "PARSE_ERROR", f"Ошибка расчета: {e}"

        except Exception as e:
            logger.error(f"Ошибка стратегии Competitor: {e}")
            return None, "PARSE_ERROR", f"Исключение при парсинге конкурента: {e}"
    
    def _apply_limits(
        self,
        new_price: Decimal,
        config: ProductConfig
    ) -> Tuple[Decimal, str]:
        """
        Применение защитных лимитов (min_price, max_price, min_margin_pct)

        Returns:
            Tuple (итоговая_цена, reason_code)
        """
        original_price = new_price
        reason_code = "UPDATED"

        # Проверка min_price (обязательно)
        if new_price < config.min_price:
            new_price = config.min_price
            reason_code = "CLAMPED_TO_MIN"
            logger.info(f"Цена ограничена минимумом: {original_price}₽ → {new_price}₽")

        # Проверка max_price (опционально)
        if config.max_price and new_price > config.max_price:
            new_price = config.max_price
            reason_code = "CLAMPED_TO_MAX"
            logger.info(f"Цена ограничена максимумом: {original_price}₽ → {new_price}₽")

        # Проверка минимальной маржи (Margin Guard) — per-user настройки (из кеша)
        s = self._user_settings or {}
        min_margin_pct = float(s.get("min_margin_pct", "0.0"))

        if min_margin_pct > 0 and config.product and config.product.cost_price:
            cost_price = float(config.product.cost_price)
            if cost_price > 0:
                commission_type  = s.get("commission_type",  "pct")
                commission_value = float(s.get("commission_value", "0.0"))
                logistics_type   = s.get("logistics_type",   "rub")
                logistics_value  = float(s.get("logistics_value",  "0.0"))
                acquiring_type   = s.get("acquiring_type",   "pct")
                acquiring_value  = float(s.get("acquiring_value",  "0.0"))

                sp = float(new_price)
                fees = 0.0
                for ftype, fval in [
                    (commission_type, commission_value),
                    (logistics_type,  logistics_value),
                    (acquiring_type,  acquiring_value),
                ]:
                    fees += sp * fval / 100 if ftype == "pct" else fval

                current_margin = (sp - cost_price - fees) / cost_price * 100

                if current_margin < min_margin_pct:
                    # Минимальная цена при которой маржа = min_margin_pct
                    # fees = fixed_fees + sp * pct_rate
                    # margin = (sp - cost - fees) / cost = min_margin_pct/100
                    # sp*(1 - pct_rate) - cost - fixed_fees = cost * min_margin_pct/100
                    # sp*(1 - pct_rate) = cost*(1 + min_margin_pct/100) + fixed_fees
                    pct_rate = sum(
                        fval / 100 if ftype == "pct" else 0
                        for ftype, fval in [
                            (commission_type, commission_value),
                            (logistics_type,  logistics_value),
                            (acquiring_type,  acquiring_value),
                        ]
                    )
                    fixed_fees = sum(
                        fval if ftype == "rub" else 0
                        for ftype, fval in [
                            (commission_type, commission_value),
                            (logistics_type,  logistics_value),
                            (acquiring_type,  acquiring_value),
                        ]
                    )
                    denominator = 1 - pct_rate
                    if denominator > 0:
                        min_price_by_margin = Decimal(str(
                            (cost_price * (1 + min_margin_pct / 100) + fixed_fees) / denominator
                        ))
                        # Округляем вверх до целого рубля
                        min_price_by_margin = Decimal(self._round_up(min_price_by_margin))
                        if new_price < min_price_by_margin:
                            logger.info(
                                f"Цена ограничена защитой маржи: {new_price}₽ → {min_price_by_margin}₽ "
                                f"(маржа {current_margin:.1f}% < {min_margin_pct}%)"
                            )
                            new_price = min_price_by_margin
                            reason_code = "CLAMPED_TO_MARGIN"

        return new_price, reason_code
    
    async def _update_price_in_wb(
        self,
        product: Product,
        account: Account,
        new_price: Decimal
    ) -> bool:
        """
        Обновление цены в WB API с проверкой применения
        
        Returns:
            True если цена успешно применилась
        """
        try:
            async with create_wb_client(account.wb_api_token) as client:
                # Отправляем обновление
                await client.update_prices([
                    {
                        "nmID": product.nm_id,
                        "price": int(new_price)
                    }
                ])
                
                logger.info(f"Цена отправлена в WB API: nm_id={product.nm_id}, price={new_price}₽")
                
                # Проверка применения
                verified = await client.verify_price_update(
                    nm_id=product.nm_id,
                    expected_price=new_price
                )
                
                return verified
                
        except Exception as e:
            logger.error(f"Ошибка обновления цены в WB API для nm_id={product.nm_id}: {e}")
            return False
    
    def _save_price_snapshot(
        self,
        product: Product,
        current_data: Dict,
        competitor_nm_id: Optional[int] = None,
        competitor_price: Optional[Decimal] = None,
        competitor_in_stock: Optional[bool] = None
    ):
        """Сохранение снимка цены в БД"""
        snapshot = PriceSnapshot(
            product_id=product.id,
            seller_price=current_data['seller_price'],
            final_price=current_data['final_price'],
            spp=current_data.get('spp'),
            competitor_nm_id=competitor_nm_id,
            competitor_price=competitor_price,
            competitor_in_stock=competitor_in_stock,
            is_in_promo=current_data.get('is_in_promo', False),
            total_quantity=current_data.get('total_quantity'),
            snapshot_at=datetime.utcnow()
        )
        self.db.add(snapshot)
    
    def _save_price_change(
        self,
        run: RepricerRun,
        product: Product,
        current_data: Dict,
        new_price: Optional[Decimal],
        new_final_price: Optional[Decimal],
        reason_code: str,
        reason_message: Optional[str]
    ):
        """Сохранение записи об изменении цены"""
        change = PriceChange(
            run_id=run.id,
            product_id=product.id,
            old_seller_price=current_data['seller_price'],
            old_final_price=current_data['final_price'],
            new_seller_price=new_price,
            new_final_price=new_final_price,
            reason_code=reason_code,
            reason_message=reason_message,
            changed_at=datetime.utcnow()
        )
        self.db.add(change)
    
    async def process_product(
        self,
        product: Product,
        run: RepricerRun,
        dry_run: bool = False,
    ) -> str:
        """
        Обработка одного товара

        Returns:
            reason_code (UPDATED, NO_CHANGE, SKIPPED_*, etc)
        """
        _t0 = time.monotonic()
        logger.info(f"Обработка товара: {product.seller_sku} (nm_id={product.nm_id})")

        # Получаем конфигурацию
        config = product.config
        if not config or not config.is_active:
            logger.info(f"Репрайсер отключен для {product.seller_sku}")
            _reason = "SKIPPED_INACTIVE"
            logger.info("METRIC " + json.dumps({"metric": "product_done", "run_id": run.id, "nm_id": product.nm_id, "sku": product.seller_sku, "reason": _reason, "duration_ms": int((time.monotonic() - _t0) * 1000)}))
            return _reason

        # Проверка min_price (обязательное поле)
        if config.min_price is None:
            logger.error(f"min_price не установлен для {product.seller_sku}")
            _reason = "SKIPPED_INACTIVE"
            logger.info("METRIC " + json.dumps({"metric": "product_done", "run_id": run.id, "nm_id": product.nm_id, "sku": product.seller_sku, "reason": _reason, "duration_ms": int((time.monotonic() - _t0) * 1000)}))
            return _reason

        # Получаем аккаунт
        account = product.account
        if not account or not account.is_active:
            logger.warning(f"Аккаунт неактивен для {product.seller_sku}")
            _reason = "SKIPPED_INACTIVE"
            logger.info("METRIC " + json.dumps({"metric": "product_done", "run_id": run.id, "nm_id": product.nm_id, "sku": product.seller_sku, "reason": _reason, "duration_ms": int((time.monotonic() - _t0) * 1000)}))
            return _reason

        if dry_run:
            # В dry_run режиме пропускаем все внешние вызовы
            current_data = {
                'seller_price': Decimal(str(config.min_price or 1000)) + Decimal('50'),
                'final_price': Decimal(str(config.min_price or 1000)),
                'spp': Decimal('10'),
                'is_in_promo': False,
                'total_quantity': 5,
            }
        else:
            # Проверяем токен
            if is_token_expired(account.wb_api_token):
                logger.error(f"Токен истек для аккаунта {account.name}")
                await telegram_notifier.notify_token_expired(account.name, account.id)
                _reason = "WB_API_ERROR"
                logger.info("METRIC " + json.dumps({"metric": "product_done", "run_id": run.id, "nm_id": product.nm_id, "sku": product.seller_sku, "reason": _reason, "duration_ms": int((time.monotonic() - _t0) * 1000)}))
                return _reason

            # Получаем текущие данные
            current_data = await self._get_current_price_data(product, account)
            if not current_data:
                _reason = "PARSE_ERROR"
                logger.info("METRIC " + json.dumps({"metric": "product_done", "run_id": run.id, "nm_id": product.nm_id, "sku": product.seller_sku, "reason": _reason, "duration_ms": int((time.monotonic() - _t0) * 1000)}))
                return _reason
        
        # Загружаем скидку кошелька из кеша (загружается один раз на прогон)
        wallet_pct = Decimal((self._user_settings or {}).get("wb_wallet_discount_pct", "0"))

        # Выполняем стратегию
        if config.strategy == 'rrp':
            new_price, reason_code, reason_msg = await self._strategy_rrp(
                product, config, current_data, wallet_pct
            )
        elif config.strategy == 'competitor':
            new_price, reason_code, reason_msg = await self._strategy_competitor(
                product, config, current_data
            )
        else:
            logger.error(f"Неизвестная стратегия: {config.strategy}")
            return "PARSE_ERROR"
        
        # Если цена не изменилась
        if new_price is None:
            self._save_price_snapshot(product, current_data)
            self._save_price_change(
                run, product, current_data, None, None,
                reason_code, reason_msg
            )
            return reason_code
        
        # Применяем лимиты
        final_new_price, limit_reason = self._apply_limits(new_price, config)
        
        # Если после лимитов цена не изменилась
        if final_new_price == current_data['seller_price']:
            self._save_price_snapshot(product, current_data)
            self._save_price_change(
                run, product, current_data, final_new_price, None,
                "NO_CHANGE", f"После применения лимитов цена осталась прежней ({final_new_price}₽)"
            )
            return "NO_CHANGE"
        
        # Обновляем цену в WB (в dry_run пропускаем реальный вызов)
        if dry_run:
            success = True
        else:
            success = await self._update_price_in_wb(product, account, final_new_price)

        if success:
            # Рассчитываем новую финальную цену
            spp = current_data.get('spp')
            if spp is not None and 0 <= spp < 100:
                new_final = final_new_price * (1 - spp / 100)
            else:
                if spp is not None and spp >= 100:
                    logger.warning(f"Некорректный SPP={spp} для nm_id={product.nm_id}, финальная цена не рассчитана")
                new_final = None

            # Сохраняем снимок с НОВОЙ ценой, чтобы UI сразу отображал обновление
            updated_data = dict(current_data)
            updated_data['seller_price'] = final_new_price
            if new_final:
                updated_data['final_price'] = new_final
            self._save_price_snapshot(product, updated_data)

            self._save_price_change(
                run, product, current_data, final_new_price, new_final,
                limit_reason if limit_reason != "UPDATED" else "UPDATED",
                f"{reason_msg}. Цена применена: {final_new_price}₽"
            )
            _reason = limit_reason if limit_reason != "UPDATED" else "UPDATED"
            logger.info("METRIC " + json.dumps({"metric": "product_done", "run_id": run.id, "nm_id": product.nm_id, "sku": product.seller_sku, "reason": _reason, "duration_ms": int((time.monotonic() - _t0) * 1000)}))
            return _reason
        else:
            self._save_price_snapshot(product, current_data)
            self._save_price_change(
                run, product, current_data, final_new_price, None,
                "WB_API_ERROR", "Не удалось применить цену в WB API"
            )
            logger.info("METRIC " + json.dumps({"metric": "product_done", "run_id": run.id, "nm_id": product.nm_id, "sku": product.seller_sku, "reason": "WB_API_ERROR", "duration_ms": int((time.monotonic() - _t0) * 1000)}))
            return "WB_API_ERROR"
    
    async def fetch_price_snapshots(
        self,
        product_ids: Optional[List[int]] = None
    ) -> Dict:
        """
        Получение текущих цен с WB и сохранение снимков без изменения цен.
        Работает для всех товаров, независимо от наличия стратегии.
        """
        logger.info("Получение снимков цен для всех товаров")

        query = select(Product)
        if product_ids:
            query = query.filter(Product.id.in_(product_ids))

        products = self.db.execute(query).scalars().all()
        logger.info(f"Товаров для обновления цен: {len(products)}")

        results = {'updated': 0, 'errors': 0, 'total': len(products)}

        for product in products:
            try:
                account = product.account
                if not account or not account.is_active:
                    results['errors'] += 1
                    continue

                if is_token_expired(account.wb_api_token):
                    logger.warning(f"Токен истек для аккаунта {account.name}")
                    results['errors'] += 1
                    continue

                current_data = await self._get_current_price_data(product, account)
                if not current_data:
                    results['errors'] += 1
                    continue

                self._save_price_snapshot(product, current_data)
                results['updated'] += 1
                if results['updated'] % 50 == 0:
                    self.db.commit()

            except Exception as e:
                logger.error(f"Ошибка получения цены для {product.seller_sku}: {e}")
                results['errors'] += 1
                self.db.rollback()

        if results['updated'] % 50 != 0:
            try:
                self.db.commit()
            except Exception:
                self.db.rollback()

        logger.info(f"Снимки цен: получено={results['updated']}, ошибок={results['errors']}")
        return results

    async def run_repricer(
        self,
        product_ids: Optional[List[int]] = None,
        run_type: str = "scheduled",
        dry_run: bool = False,
        existing_run_id: Optional[int] = None,
    ) -> RepricerRun:
        """
        Запуск репрайсера

        Args:
            product_ids: Список ID товаров (None = все активные)
            run_type: Тип запуска (scheduled или manual)
            dry_run: Если True — пропускает реальные вызовы WB API (нагрузочное тестирование)

        Returns:
            RepricerRun запись с результатами
        """
        _run_start = time.monotonic()
        logger.info(f"Запуск репрайсера (тип: {run_type}, dry_run={dry_run})")

        # Предзагружаем настройки пользователя один раз (избегаем N+1 запросов в цикле)
        self._user_settings = self._load_user_settings()

        # Сначала проверяем, есть ли вообще товары для обработки
        query = select(Product).join(ProductConfig).filter(
            ProductConfig.is_active == True
        )
        if product_ids:
            query = query.filter(Product.id.in_(product_ids))

        products = self.db.execute(query).scalars().all()

        if not products:
            logger.info("Нет активных товаров для обработки — прогон не создаётся")
            # Если был создан run из API (queued), помечаем его завершённым
            if existing_run_id:
                run = self.db.query(RepricerRun).filter(RepricerRun.id == existing_run_id).first()
                if run:
                    run.status = "completed"
                    run.total_products = 0
                    run.updated_count = 0
                    run.skipped_count = 0
                    run.error_count = 0
                    run.completed_at = datetime.utcnow()
                    run.error_message = "Нет активных товаров с настроенными стратегиями"
                    self.db.commit()
                    return run
            # Для scheduled прогонов — создаём фиктивный объект без записи в БД
            dummy = RepricerRun(
                user_id=self.user_id,
                run_type=run_type,
                status="completed",
                total_products=0,
                updated_count=0,
                skipped_count=0,
                error_count=0,
                started_at=datetime.utcnow(),
                completed_at=datetime.utcnow(),
            )
            return dummy

        # Используем существующую запись (из API) или создаём новую (Fix 6)
        if existing_run_id:
            run = self.db.query(RepricerRun).filter(RepricerRun.id == existing_run_id).first()
            if run:
                run.status = "running"
                self.db.commit()
            else:
                existing_run_id = None

        if not existing_run_id:
            run = RepricerRun(
                user_id=self.user_id,
                run_type=run_type,
                status="running",
                started_at=datetime.utcnow()
            )
            self.db.add(run)
            self.db.commit()

        try:
            logger.info(f"Найдено {len(products)} товаров для обработки")
            run.total_products = len(products)
            self.db.commit()

            logger.info("METRIC " + json.dumps({
                "metric": "run_start",
                "run_id": run.id,
                "total": len(products),
                "run_type": run_type,
                "dry_run": dry_run,
            }))

            # Обрабатываем товары
            results = {'updated': 0, 'skipped': 0, 'errors': 0}

            for product in products:
                try:
                    reason_code = await self.process_product(product, run, dry_run=dry_run)

                    if reason_code in ['UPDATED', 'CLAMPED_TO_MIN', 'CLAMPED_TO_MAX', 'CLAMPED_TO_MARGIN']:
                        results['updated'] += 1
                    elif reason_code in ['SKIPPED_PROMO', 'SKIPPED_INACTIVE', 'NO_CHANGE', 'COMPETITOR_OOS']:
                        results['skipped'] += 1
                    else:
                        results['errors'] += 1

                    self.db.commit()

                except Exception as e:
                    logger.error(f"Ошибка обработки товара {product.seller_sku}: {e}", exc_info=True)
                    results['errors'] += 1
                    self.db.rollback()

            # Обновляем статус прогона
            _duration_ms = int((time.monotonic() - _run_start) * 1000)
            run.updated_count = results['updated']
            run.skipped_count = results['skipped']
            run.error_count = results['errors']
            run.status = "completed"
            run.completed_at = datetime.utcnow()
            self.db.commit()

            logger.info(
                f"Репрайсер завершен: обновлено={results['updated']}, "
                f"пропущено={results['skipped']}, ошибок={results['errors']}"
            )
            logger.info("METRIC " + json.dumps({
                "metric": "run_complete",
                "run_id": run.id,
                "total": run.total_products,
                "updated": results['updated'],
                "skipped": results['skipped'],
                "errors": results['errors'],
                "duration_ms": _duration_ms,
                "run_type": run_type,
                "dry_run": dry_run,
                "products_per_sec": round(run.total_products / (_duration_ms / 1000), 2) if _duration_ms > 0 else 0,
            }))
            
            return run
            
        except Exception as e:
            logger.error(f"Критическая ошибка в репрайсере: {e}", exc_info=True)
            run.status = "failed"
            run.error_message = str(e)
            run.completed_at = datetime.utcnow()
            self.db.commit()
            
            # Уведомление в Telegram
            await telegram_notifier.notify_scheduler_error(str(e))
            
            raise
