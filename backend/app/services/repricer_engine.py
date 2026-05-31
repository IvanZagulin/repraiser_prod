import asyncio
import json
import logging
import time
from types import SimpleNamespace
from typing import Optional, Dict, List, Tuple
from decimal import Decimal, ROUND_UP
from datetime import datetime, timezone
from sqlalchemy.orm import Session, joinedload
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
        self._prefetched_prices: Dict[int, object] = {}  # nm_id -> PriceData, cleared per chunk
        self._competitor_entries_map: Optional[Dict[int, list]] = None  # config_id -> [nm_id], прогрев на прогон

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
    
    # Допустимый диапазон СПП: WB не даёт скидку больше 95%,
    # отрицательные значения технически возможны (клиент доплачивает)
    # но на практике не встречаются — ставим нижнюю границу -10%.
    _SPP_MIN = Decimal("-10")
    _SPP_MAX = Decimal("95")

    def _calculate_seller_price_from_final(
        self,
        final_price: Decimal,
        spp: Decimal,
        seller_discount: Decimal = Decimal(0)
    ) -> Decimal:
        """
        Обратный расчет базовой цены продавца из финальной цены покупателя:
        final_price = seller_price * (1 - seller_discount/100) * (1 - spp/100)
        seller_price = final_price / (1 - seller_discount/100) / (1 - spp/100)

        Args:
            final_price: Желаемая финальная цена покупателя
            spp: Истинный СПП WB в процентах (допустимый диапазон: -10% … 95%)
            seller_discount: Скидка продавца в процентах (0-99%)

        Returns:
            Базовая цена продавца (округленная вверх)

        Raises:
            ValueError: если spp или seller_discount вне допустимого диапазона
        """
        if spp < self._SPP_MIN or spp >= 100:
            raise ValueError(
                f"СПП {spp}% вне допустимого диапазона"
                f" [{self._SPP_MIN}%; 100%), невозможно рассчитать цену продавца"
            )
        if spp > self._SPP_MAX:
            logger.warning(
                f"СПП {spp}% выше нормального максимума ({self._SPP_MAX}%) — "
                f"расчёт продолжается, но проверьте данные"
            )

        spp_factor = 1 - spp / 100
        discount_factor = 1 - seller_discount / 100 if seller_discount > 0 else Decimal(1)
        seller_price = final_price / (spp_factor * discount_factor)
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
                seller_price=Decimal(str(cached.get('seller_price', 0))),
                spp=Decimal(str(cached['spp'])) if cached['spp'] is not None else None,
                is_in_promo=cached['is_in_promo'],
                total_quantity=cached['total_quantity'],
                in_stock=cached['in_stock'],
                catalog_stale=False,
            )

        product_data = await catalog_parser.parse_product(nm_id)
        if product_data:
            cache_set(cache_key, product_data.to_dict(), ttl=600)
            # Долгосрочный бэкап: при 403 используем стейл-данные (лучше чем ничего)
            cache_set(f"wb_catalog_last:{nm_id}", product_data.to_dict(), ttl=86400)  # 24ч
        else:
            # Каталог недоступен — пробуем стейл-бэкап (последний успешный парсинг)
            stale = cache_get(f"wb_catalog_last:{nm_id}")
            if stale is not None:
                logger.debug("nm_id=%s: каталог недоступен, используем стейл-кеш (24ч)", nm_id)
                return SimpleNamespace(
                    final_price=Decimal(str(stale['final_price'])),
                    seller_price=Decimal(str(stale.get('seller_price', 0))),
                    spp=Decimal(str(stale['spp'])) if stale['spp'] is not None else None,
                    is_in_promo=stale['is_in_promo'],
                    total_quantity=stale['total_quantity'],
                    in_stock=stale['in_stock'],
                    catalog_stale=True,
                )
        return product_data

    async def _prefetch_own_prices(self, products: list) -> None:
        # Fetch ALL seller prices per account (no filterNmID - WB rejects 100+ ids with 400)
        by_token = {}
        want_nm_ids = set()
        for product in products:
            account = product.account
            if not account or not account.is_active:
                continue
            if is_token_expired(account.wb_api_token):
                continue
            token = account.wb_api_token
            if token not in by_token:
                by_token[token] = account
            want_nm_ids.add(product.nm_id)

        for token, account in by_token.items():
            try:
                fetched = 0
                async with create_wb_client(token) as client:
                    offset = 0
                    while True:
                        prices = await client.get_prices(nm_ids=None, limit=1000, offset=offset)
                        for p in prices:
                            if p.nm_id in want_nm_ids:
                                self._prefetched_prices[p.nm_id] = p
                                fetched += 1
                        if len(prices) < 1000:
                            break
                        offset += 1000
                logger.info("Pre-fetch цен: %d/%d для аккаунта %s", fetched, len(want_nm_ids), account.name)
            except Exception as e:
                logger.warning("Ошибка pre-fetch цен для %s: %s", account.name, e)

    async def _prefetch_all_prices_once(self) -> None:
        """Загружает цены ВСЕХ аккаунтов параллельно (один раз до цикла чанков)."""
        accounts = self.db.execute(
            select(Account).filter(Account.user_id == self.user_id, Account.is_active == True)
        ).scalars().all()

        async def _fetch_account(account):
            if is_token_expired(account.wb_api_token):
                return
            try:
                fetched = 0
                async with create_wb_client(account.wb_api_token) as client:
                    offset = 0
                    while True:
                        prices = await client.get_prices(nm_ids=None, limit=1000, offset=offset)
                        for p in prices:
                            self._prefetched_prices[p.nm_id] = p
                            fetched += 1
                        if len(prices) < 1000:
                            break
                        offset += 1000
                logger.info("Pre-fetch цен (global): %d товаров для аккаунта %s", fetched, account.name)
            except Exception as e:
                logger.warning("Ошибка pre-fetch цен для %s: %s", account.name, e)

        await asyncio.gather(*[_fetch_account(a) for a in accounts])

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
            # Используем предзагруженные данные если есть (batch pre-fetch)
            if product.nm_id in self._prefetched_prices:
                price_data = self._prefetched_prices[product.nm_id]
            else:
                async with create_wb_client(account.wb_api_token) as client:
                    prices = await client.get_prices(nm_ids=[product.nm_id])
                    if not prices:
                        logger.warning("Товар nm_id=%s не найден в WB API", product.nm_id)
                        return None
                    price_data = prices[0]

            # Парсим текущую цену через catalog для получения СПП (с Redis-кешем)
            catalog_product = await self._cached_parse_product(product.nm_id)

            if catalog_product and getattr(catalog_product, "catalog_stale", False):
                logger.warning(
                    "nm_id=%s: каталог недоступен, stale-кеш не используем для переоценки",
                    product.nm_id,
                )
                return {"catalog_stale": True}

            if not catalog_product:
                logger.warning("nm_id=%s: каталог недоступен — пропускаем переоценку", product.nm_id)
                return {"catalog_stale": True}

            # Истинный СПП WB = скидка от цены после скидки продавца до финальной цены
            discounted_price = price_data.discounted_price
            if discounted_price > 0 and catalog_product.final_price > 0:
                true_spp = round((1 - catalog_product.final_price / discounted_price) * 100)
                true_spp = Decimal(str(max(0, true_spp)))
            elif catalog_product.total_quantity == 0:
                # Реально OOS: stock=0 и final=0 — это нормально, дальше отфильтруется.
                true_spp = Decimal(0)
            else:
                # final_price=0 при stock>0 — это баг каталога (пустой ответ WB или
                # 429 throttle). SPP=0 + такая ситуация = неправильный target.
                # Лучше пропустить товар чем посчитать неверно.
                logger.warning(
                    "nm_id=%s: catalog final_price=0 при stock=%s — PARSE_ERROR",
                    product.nm_id, catalog_product.total_quantity,
                )
                return None

            return {
                'seller_price': price_data.seller_price,
                'discounted_price': discounted_price,
                'discount': price_data.discount,
                'final_price': catalog_product.final_price,
                'spp': true_spp,
                'is_in_promo': catalog_product.is_in_promo,
                'total_quantity': catalog_product.total_quantity,
                'name': getattr(catalog_product, "name", None),
            }

        except Exception as e:
            logger.error("Ошибка получения данных для nm_id=%s: %s", product.nm_id, e)
            return None
    
    def _resolve_rrp(self, config, spp):
        """Returns (seller_price_direct, rrp_price) for the current SPP level.

        Checks rrp_spp_tiers first (ordered by spp_min ascending).
        Returns (tier.seller_price, tier.rrp_price) for the matching tier.
        If seller_price is set in the tier, the engine uses it directly
        (no backward calculation). Falls back to (None, config.rrp_price).
        Tier range: [spp_min, spp_max). spp_max=None means no upper limit.
        """
        tiers = getattr(config, 'rrp_spp_tiers', None) or []
        for tier in tiers:
            above_min = spp >= tier.spp_min
            below_max = tier.spp_max is None or spp < tier.spp_max
            if above_min and below_max:
                return getattr(tier, 'seller_price', None), tier.rrp_price
        return None, config.rrp_price

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
        spp = current_data.get('spp') or Decimal(0)
        seller_price_direct, rrp = self._resolve_rrp(config, spp)

        # Direct seller price mode: tier specifies exact WB seller price
        if seller_price_direct is not None:
            current_seller = current_data.get('seller_price') or Decimal(0)
            if current_seller == seller_price_direct:
                return None, "NO_CHANGE", (
                    f"Цена продавца уже {seller_price_direct}₽ (ступень СПП={spp}%)"
                )
            return (
                seller_price_direct,
                "CALCULATED",
                f"Ступень СПП: цена продавца {seller_price_direct}₽ при СПП={spp}%",
            )

        if not rrp:
            return None, "SKIPPED_INACTIVE", "РРЦ не настроен"

        tolerance = config.rrp_tolerance or Decimal(5)
        current_final = current_data['final_price']
        seller_discount = current_data.get('discount') or Decimal(0)

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
                new_seller_price = self._calculate_seller_price_from_final(target_final, spp, seller_discount)
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
                new_seller_price = self._calculate_seller_price_from_final(target_final, spp, seller_discount)
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
    ) -> Tuple[Optional[Decimal], Optional[int], Optional[Decimal]]:
        """
        Получение целевой цены конкурента с учётом MultiCompetitor.

        Строит список nm_id конкурентов (из competitor_entries если есть,
        иначе из legacy competitor_nm_id), получает цены и применяет правило выбора.

        Returns:
            Tuple (final_price конкурента, winning_nm_id, basic_price конкурента)
            или (None, None, None)
        """
        # Список nm_id конкурентов. Берём из прогретой карты (run_repricer),
        # иначе fallback на точечный запрос (например при вызове process_product
        # вне основного прогона).
        if self._competitor_entries_map is not None:
            nm_ids = list(self._competitor_entries_map.get(config.id, []))
        else:
            from app.models.competitor_entry import CompetitorEntry
            nm_ids = [
                e.nm_id for e in self.db.query(CompetitorEntry.nm_id).filter(
                    CompetitorEntry.product_config_id == config.id
                ).all()
            ]

        if not nm_ids:
            if config.competitor_nm_id:
                nm_ids = [config.competitor_nm_id]
            else:
                return None, None, None

        rule = config.competitor_select_rule or "min_price"

        # Получаем данные по каждому конкуренту: (nm_id, final, in_stock, basic)
        prices: List[Tuple[int, Decimal, bool, Decimal]] = []
        for nm_id in nm_ids:
            try:
                p = await self._cached_parse_product(nm_id)
                if p and not getattr(p, "catalog_stale", False):
                    basic = getattr(p, 'seller_price', None) or Decimal(0)
                    prices.append((nm_id, p.final_price, p.in_stock, basic))
            except Exception as e:
                logger.warning(f"Не удалось получить цену конкурента nm_id={nm_id}: {e}")

        if not prices:
            logger.warning("Конкурент для config_id=%s: каталог недоступен — stale snapshot не используем", config.id)
            return None, None, None

        if rule == "cheapest_instock":
            instock = [(nm, price, s, b) for nm, price, s, b in prices if s]
            if not instock:
                return None, None, None
            winner = min(instock, key=lambda x: x[1])
        elif rule == "avg_price":
            avg_final = sum(p for _, p, _, _ in prices) / len(prices)
            avg_basic = sum(b for _, _, _, b in prices) / len(prices)
            return Decimal(str(avg_final)), None, Decimal(str(avg_basic))
        else:  # min_price
            instock = [(nm, price, s, b) for nm, price, s, b in prices if s and price > 0]
            if not instock:
                return None, None, None
            winner = min(instock, key=lambda x: x[1])

        return winner[1], winner[0], winner[3]

    async def _strategy_competitor(
        self,
        product: Product,
        config: ProductConfig,
        current_data: Dict
    ) -> Tuple[Optional[Decimal], str, Optional[str], Optional[Decimal], Optional[int]]:
        """
        Стратегия Competitor (поддерживает несколько конкурентов).

        Returns:
            Tuple (новая_цена_продавца, reason_code, reason_message, competitor_price, winning_nm_id)
        """
        try:
            competitor_price, winning_nm_id, competitor_basic = await self._get_competitor_price(config)

            if competitor_price is None:
                # Нет конкурентов или все OOS при правиле cheapest_instock
                from app.models.competitor_entry import CompetitorEntry
                entries = self.db.query(CompetitorEntry).filter(
                    CompetitorEntry.product_config_id == config.id
                ).all()
                if not entries and not config.competitor_nm_id:
                    return None, "SKIPPED_INACTIVE", "Конкурент не настроен", None, None
                return None, "COMPETITOR_OOS", "Все конкуренты не в наличии", None, None

            spp = current_data.get('spp') or Decimal(0)
            seller_discount = current_data.get('discount') or Decimal(0)

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

            # Обратный расчет цены продавца (классический путь)
            try:
                new_seller_price = self._calculate_seller_price_from_final(target_final, spp, seller_discount)

                reason_msg = (
                    f"Competitor ({rule}): конкурент nm_id={winning_nm_id} цена={competitor_price}₽, "
                    f"смещение {offset_direction} {offset_value}{offset_type}, "
                    f"целевая {target_final}₽, новая цена продавца {new_seller_price}₽"
                )

                return new_seller_price, "CALCULATED", reason_msg, competitor_price, winning_nm_id

            except ValueError as e:
                return None, "PARSE_ERROR", f"Ошибка расчета: {e}", None, None

        except Exception as e:
            logger.error(f"Ошибка стратегии Competitor: {e}")
            return None, "PARSE_ERROR", f"Исключение при парсинге конкурента: {e}", None, None
    
    def _apply_limits(
        self,
        new_price: Decimal,
        config: ProductConfig,
        seller_discount: Decimal = Decimal(0),
    ) -> Tuple[Decimal, str]:
        """
        Применение защитных лимитов (min_price, max_price, min_margin_pct)

        seller_discount: скидка продавца в % (из WB снапшота). Используется для
                         расчёта реальной выручки при проверке минимальной маржи.

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
        # Если skip_margin_check=True для товара — пропускаем
        if getattr(config, 'skip_margin_check', False):
            return new_price, reason_code

        # Все расчёты в Decimal для точности финансовой математики
        s = self._user_settings or {}
        min_margin_pct = Decimal(s.get("min_margin_pct", "0"))

        # Минимальный порог себестоимости: 1 руб.
        # При cost_price < 1 деление даёт некорректно огромную маржу.
        _COST_PRICE_MIN = Decimal("1")

        if min_margin_pct > 0 and config.product and config.product.cost_price:
            cost_price = Decimal(str(config.product.cost_price))
            if cost_price >= _COST_PRICE_MIN:
                commission_type  = s.get("commission_type",  "pct")
                commission_value = Decimal(s.get("commission_value", "0"))
                logistics_type   = s.get("logistics_type",   "rub")
                logistics_value  = Decimal(s.get("logistics_value",  "0"))
                acquiring_type   = s.get("acquiring_type",   "pct")
                acquiring_value  = Decimal(s.get("acquiring_value",  "0"))

                sp = Decimal(str(new_price))
                # Применяем скидку продавца: реальная выручка = seller_price * (1 - discount/100)
                discount_factor = (1 - seller_discount / 100) if seller_discount > 0 else Decimal(1)
                eff_sp = sp * discount_factor
                fees = Decimal("0")
                fee_items = [
                    (commission_type, commission_value),
                    (logistics_type,  logistics_value),
                    (acquiring_type,  acquiring_value),
                ]
                for ftype, fval in fee_items:
                    fees += eff_sp * fval / 100 if ftype == "pct" else fval

                current_margin = (eff_sp - cost_price - fees) / cost_price * 100

                if current_margin < min_margin_pct:
                    # Минимальная цена при которой маржа = min_margin_pct
                    pct_rate = sum(
                        (fval / 100 if ftype == "pct" else Decimal("0"))
                        for ftype, fval in fee_items
                    )
                    fixed_fees = sum(
                        (fval if ftype == "rub" else Decimal("0"))
                        for ftype, fval in fee_items
                    )
                    denominator = Decimal("1") - pct_rate
                    if denominator > 0:
                        # С учётом скидки продавца: seller_price = effective_min / discount_factor
                        min_price_by_margin = (
                            cost_price * (1 + min_margin_pct / 100) + fixed_fees
                        ) / (denominator * discount_factor)
                        # Округляем вверх до целого рубля
                        min_price_by_margin = Decimal(self._round_up(min_price_by_margin))
                        if new_price < min_price_by_margin:
                            logger.info(
                                f"Цена ограничена защитой маржи: {new_price}₽ → {min_price_by_margin}₽ "
                                f"(маржа {current_margin:.1f}% < {min_margin_pct}%, скидка продавца {seller_discount}%)"
                            )
                            new_price = min_price_by_margin
                            reason_code = "CLAMPED_TO_MARGIN"

        return new_price, reason_code
    
    async def _update_price_in_wb(
        self,
        product: Product,
        account: Account,
        new_price: Decimal,
    ) -> bool:
        """Обновление цены в WB API с проверкой применения."""
        try:
            async with create_wb_client(account.wb_api_token) as client:
                payload = {"nmID": product.nm_id, "price": int(new_price)}
                await client.update_prices([payload])
                logger.info(f"Цена отправлена в WB API: nm_id={product.nm_id}, payload={payload}")
                verified = await client.verify_price_update(
                    nm_id=product.nm_id,
                    expected_price=new_price,
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
            discount=current_data.get('discount'),
            spp=current_data.get('spp'),
            competitor_nm_id=competitor_nm_id,
            competitor_price=competitor_price,
            competitor_in_stock=competitor_in_stock,
            is_in_promo=current_data.get('is_in_promo', False),
            total_quantity=current_data.get('total_quantity'),
            snapshot_at=datetime.now(timezone.utc).replace(tzinfo=None)
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
            changed_at=datetime.now(timezone.utc).replace(tzinfo=None)
        )
        self.db.add(change)
    
    def _is_night_window(self, config) -> bool:
        """Returns True if current Moscow time is within night_start..night_end."""
        if not getattr(config, 'night_price_enabled', False):
            return False
        ns = getattr(config, 'night_start_msk', None)
        ne = getattr(config, 'night_end_msk', None)
        if not ns or not ne or not config.night_seller_price:
            return False
        try:
            from datetime import timezone, timedelta, datetime as _dt
            now_msk = _dt.now(timezone(timedelta(hours=3))).strftime('%H:%M')
            if ns > ne:  # crosses midnight, e.g. 23:00-06:00
                return now_msk >= ns or now_msk < ne
            return ns <= now_msk < ne
        except Exception as e:
            logger.warning(f'Night window check failed: {e}')
            return False

    def _apply_night_boost(self, config, base_price):
        """Returns (night_price, 'NIGHT_BOOST', msg) or (base_price, None, None)."""
        if not self._is_night_window(config):
            return base_price, None, None
        night_price = config.night_seller_price
        if night_price < config.min_price:
            night_price = config.min_price
        msg = (
            f'Ночное окно МСК {config.night_start_msk}'
            f'–{config.night_end_msk}: '
            f'цена {night_price}₽ (повышение средней для акций WB)'
        )
        return night_price, 'NIGHT_BOOST', msg

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
            if current_data.get("catalog_stale"):
                _reason = "CATALOG_UNAVAILABLE"
                logger.info("METRIC " + json.dumps({"metric": "product_done", "run_id": run.id, "nm_id": product.nm_id, "sku": product.seller_sku, "reason": _reason, "duration_ms": int((time.monotonic() - _t0) * 1000)}))
                return _reason
        
        # Загружаем скидку кошелька из кеша (загружается один раз на прогон)
        wallet_pct = Decimal((self._user_settings or {}).get("wb_wallet_discount_pct", "0"))

        # Выполняем стратегию
        snap_competitor_price = None
        snap_competitor_nm_id = None
        if config.strategy == 'rrp':
            new_price, reason_code, reason_msg = await self._strategy_rrp(
                product, config, current_data, wallet_pct
            )
        elif config.strategy == 'competitor':
            new_price, reason_code, reason_msg, snap_competitor_price, snap_competitor_nm_id = await self._strategy_competitor(
                product, config, current_data
            )
        else:
            logger.error(f"Неизвестная стратегия: {config.strategy}")
            logger.info("METRIC " + json.dumps({"metric": "product_done", "run_id": run.id, "nm_id": product.nm_id, "sku": product.seller_sku, "reason": "PARSE_ERROR", "duration_ms": int((time.monotonic() - _t0) * 1000)}))
            return "PARSE_ERROR"
        
        # Если стратегия не требует изменений — всё равно прогоняем через защиту маржи.
        # Если текущая цена нарушает минимум маржи, _apply_limits поднимет её.
        # Если маржа в норме — final_new_price == current_seller_price → NO_CHANGE (ниже).
        _seller_discount = current_data.get('discount') or Decimal(0)
        if new_price is None:
            _curr_sp = current_data.get('seller_price') or Decimal(0)
            if _curr_sp > 0:
                new_price = _curr_sp  # передаём текущую цену в _apply_limits для проверки маржи
            else:
                self._save_price_snapshot(product, current_data, competitor_nm_id=snap_competitor_nm_id, competitor_price=snap_competitor_price)
                self._save_price_change(
                    run, product, current_data, None, None,
                    reason_code, reason_msg
                )
                logger.info("METRIC " + json.dumps({"metric": "product_done", "run_id": run.id, "nm_id": product.nm_id, "sku": product.seller_sku, "reason": reason_code, "duration_ms": int((time.monotonic() - _t0) * 1000)}))
                return reason_code

        # Применяем лимиты (min_price, max_price, защита маржи; учитываем скидку продавца)
        final_new_price, limit_reason = self._apply_limits(new_price, config, seller_discount=_seller_discount)

        # Защита от отказа WB: снижение цены > 2x WB отклоняет.
        _curr_sp_for_step = current_data.get('seller_price') or Decimal(0)
        # WB отвергает уже при ratio <= 0.5. Берём 0.55 для запаса.
        if _curr_sp_for_step > 0 and final_new_price < _curr_sp_for_step * Decimal('0.55'):
            stepped = Decimal(self._round_up(_curr_sp_for_step * Decimal('0.55') + Decimal('1')))
            logger.info(
                "Ступенчатое снижение для nm_id=%s: %s -> %s (полпути от %s)",
                product.nm_id, final_new_price, stepped, _curr_sp_for_step,
            )
            reason_msg = (reason_msg or '') + f'. Ступенчатое снижение: отправляем {stepped} (целевая {final_new_price})'
            final_new_price = stepped

        # Night price boost: temporarily raise price during MSK night hours
        # to increase the 30-day average price baseline before WB promotions.
        night_price, night_reason, night_msg = self._apply_night_boost(
            config, final_new_price
        )
        if night_reason:
            # Прогоняем ночную цену через margin guard: если night_seller_price
            # ниже минимума по марже — clamp вверх, а не в убыток.
            night_price, _night_limit = self._apply_limits(night_price, config, seller_discount=_seller_discount)
            final_new_price = night_price
            limit_reason    = _night_limit if _night_limit != 'UPDATED' else night_reason
            reason_msg      = night_msg
        
        # Если после лимитов цена не изменилась
        if final_new_price == current_data['seller_price']:
            self._save_price_snapshot(product, current_data, competitor_nm_id=snap_competitor_nm_id, competitor_price=snap_competitor_price)
            self._save_price_change(
                run, product, current_data, final_new_price, None,
                "NO_CHANGE", f"После применения лимитов цена осталась прежней ({final_new_price}₽)"
            )
            logger.info("METRIC " + json.dumps({"metric": "product_done", "run_id": run.id, "nm_id": product.nm_id, "sku": product.seller_sku, "reason": "NO_CHANGE", "duration_ms": int((time.monotonic() - _t0) * 1000)}))
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
            self._save_price_snapshot(product, updated_data, competitor_nm_id=snap_competitor_nm_id, competitor_price=snap_competitor_price)

            self._save_price_change(
                run, product, current_data, final_new_price, new_final,
                limit_reason if limit_reason != "UPDATED" else "UPDATED",
                f"{reason_msg}. Цена применена: {final_new_price}₽"
            )
            _reason = limit_reason if limit_reason != "UPDATED" else "UPDATED"
            logger.info("METRIC " + json.dumps({"metric": "product_done", "run_id": run.id, "nm_id": product.nm_id, "sku": product.seller_sku, "reason": _reason, "duration_ms": int((time.monotonic() - _t0) * 1000)}))
            return _reason
        else:
            self._save_price_snapshot(product, current_data, competitor_nm_id=snap_competitor_nm_id, competitor_price=snap_competitor_price)
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

        from sqlalchemy.orm import contains_eager
        query = select(Product).join(Account, Product.account_id == Account.id).options(
            contains_eager(Product.account)
        )
        if product_ids:
            query = query.filter(Product.id.in_(product_ids))
        # Defense-in-depth: фильтр по user_id чтобы не обработать чужие товары
        if self.user_id is not None:
            query = query.filter(Account.user_id == self.user_id)

        products = self.db.execute(query).scalars().unique().all()
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
                if current_data.get("catalog_stale"):
                    results['errors'] += 1
                    continue

                self._save_price_snapshot(product, current_data)
                # Обновляем название товара если оно ещё не подтянулось (fallback Товар {nm_id})
                real_name = current_data.get('name', '')
                if real_name and (not product.name or product.name.startswith('Товар ') or product.name == product.seller_sku):
                    product.name = real_name
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

    async def _decide_product(
        self,
        product,
        run,
        dry_run: bool = False,
    ) -> dict:
        """
        Вычисляет решение по цене товара без обращения к WB API.
        Возвращает dict с ключами:
          product, needs_update, new_price, current_data,
          limit_reason, reason_msg, skip_code (если нет обновления)
        """
        _t0 = time.monotonic()
        config = product.config
        if not config or not config.is_active:
            return {"product": product, "needs_update": False, "skip_code": "SKIPPED_INACTIVE",
                    "current_data": None, "new_price": None, "limit_reason": None, "reason_msg": None, "_t0": _t0}

        # Товар в WB Price Quarantine — WB будет отвергать любые изменения цены.
        # Не тратим время и не засоряем логи отказами; вернёмся после истечения.
        _blocked_until = getattr(config, 'wb_blocked_until', None)
        if _blocked_until and _blocked_until > datetime.now(timezone.utc).replace(tzinfo=None):
            return {"product": product, "needs_update": False, "skip_code": "SKIPPED_QUARANTINE",
                    "current_data": None, "new_price": None, "limit_reason": None,
                    "reason_msg": f"WB Price Quarantine до {_blocked_until.isoformat()}", "_t0": _t0}

        if config.min_price is None:
            return {"product": product, "needs_update": False, "skip_code": "SKIPPED_INACTIVE",
                    "current_data": None, "new_price": None, "limit_reason": None, "reason_msg": None, "_t0": _t0}

        account = product.account
        if not account or not account.is_active:
            return {"product": product, "needs_update": False, "skip_code": "SKIPPED_INACTIVE",
                    "current_data": None, "new_price": None, "limit_reason": None, "reason_msg": None, "_t0": _t0}

        if dry_run:
            current_data = {
                "seller_price": (config.min_price or 1000) + 50,
                "final_price": config.min_price or 1000,
                "spp": 10,
                "is_in_promo": False,
                "total_quantity": 5,
            }
        else:
            if is_token_expired(account.wb_api_token):
                logger.error("Токен истек для аккаунта %s", account.name)
                await telegram_notifier.notify_token_expired(account.name, account.id)
                return {"product": product, "needs_update": False, "skip_code": "WB_API_ERROR",
                        "current_data": None, "new_price": None, "limit_reason": None, "reason_msg": None, "_t0": _t0}

            current_data = await self._get_current_price_data(product, account)
            if not current_data:
                return {"product": product, "needs_update": False, "skip_code": "PARSE_ERROR",
                        "current_data": None, "new_price": None, "limit_reason": None, "reason_msg": None, "_t0": _t0}
            if current_data.get("catalog_stale"):
                return {"product": product, "needs_update": False, "skip_code": "CATALOG_UNAVAILABLE",
                        "current_data": None, "new_price": None, "limit_reason": None,
                        "reason_msg": "Каталог WB недоступен; stale СПП не используем", "_t0": _t0}

            # Товар закончился на складе — WB не возвращает итоговую цену, пропускаем
            if current_data.get("total_quantity", 1) == 0 and current_data.get("final_price", 1) == 0:
                return {"product": product, "needs_update": False, "skip_code": "OUT_OF_STOCK",
                        "current_data": current_data, "new_price": None, "limit_reason": None,
                        "reason_msg": "Нет остатка", "_t0": _t0}

        wallet_pct = (self._user_settings or {}).get("wb_wallet_discount_pct", 0)
        try:
            wallet_pct = Decimal(str(wallet_pct))
        except Exception:
            wallet_pct = Decimal(0)

        snap_competitor_price = None
        snap_competitor_nm_id = None
        if config.strategy == "rrp":
            new_price, reason_code, reason_msg = await self._strategy_rrp(product, config, current_data, wallet_pct)
        elif config.strategy == "competitor":
            new_price, reason_code, reason_msg, snap_competitor_price, snap_competitor_nm_id = await self._strategy_competitor(product, config, current_data)
        else:
            return {"product": product, "needs_update": False, "skip_code": "PARSE_ERROR",
                    "current_data": current_data, "new_price": None, "limit_reason": None,
                    "reason_msg": "Неизвестная стратегия", "_t0": _t0}

        if new_price is None:
            # Fallback на РРЦ если конкурент недоступен и РРЦ настроена
            if reason_code == "COMPETITOR_OOS" and config.rrp_price:
                logger.info(f"Конкурент OOS для nm_id={product.nm_id}, fallback на РРЦ {config.rrp_price}₽")
                new_price, reason_code, reason_msg = await self._strategy_rrp(product, config, current_data, wallet_pct)
                reason_msg = f"[Fallback РРЦ при OOS конкурента] {reason_msg}"

        # Даже если стратегия не требует изменений — проверяем маржу по текущей цене.
        # Если нарушена — _apply_limits поднимет цену. Если нет — NO_CHANGE.
        _seller_discount = current_data.get('discount') or Decimal(0)
        if new_price is None:
            _curr_sp = current_data.get('seller_price') or Decimal(0)
            if _curr_sp > 0:
                new_price = _curr_sp
            else:
                return {"product": product, "needs_update": False, "skip_code": reason_code,
                        "current_data": current_data, "new_price": None, "limit_reason": None,
                        "reason_msg": reason_msg, "_t0": _t0,
                        "snap_competitor_price": snap_competitor_price, "snap_competitor_nm_id": snap_competitor_nm_id}

        final_new_price, limit_reason = self._apply_limits(new_price, config, seller_discount=_seller_discount)

        # Защита от отказа WB: снижение цены > 2x за один шаг отвергается
        # с ошибкой "New prices are more than twice lower". Шагаем по половинам,
        # следующий прогон опустит дальше.
        _curr_sp_for_step = current_data.get("seller_price") or Decimal(0)
        # WB отвергает уже при ratio <= 0.5 (включительно), а при повторных
        # отказах кладёт товар в Price Quarantine. Берём порог 0.55 чтобы
        # точно остаться в безопасной зоне.
        if _curr_sp_for_step > 0 and final_new_price < _curr_sp_for_step * Decimal("0.55"):
            stepped = Decimal(self._round_up(_curr_sp_for_step * Decimal("0.55") + Decimal("1")))
            logger.info(
                "Ступенчатое снижение для nm_id=%s: целевая %s < половины текущей %s, отправляем %s",
                product.nm_id, final_new_price, _curr_sp_for_step, stepped,
            )
            reason_msg = (reason_msg or "") + f". Ступенчатое снижение: отправляем {stepped} (целевая {final_new_price}, текущая {_curr_sp_for_step})"
            final_new_price = stepped

        if final_new_price == current_data["seller_price"]:
            return {"product": product, "needs_update": False, "skip_code": "NO_CHANGE",
                    "current_data": current_data, "new_price": final_new_price,
                    "limit_reason": limit_reason, "reason_msg": reason_msg, "_t0": _t0,
                    "snap_competitor_price": snap_competitor_price, "snap_competitor_nm_id": snap_competitor_nm_id}

        return {"product": product, "needs_update": True, "skip_code": None,
                "current_data": current_data, "new_price": final_new_price,
                "limit_reason": limit_reason, "reason_msg": reason_msg, "_t0": _t0,
                "snap_competitor_price": snap_competitor_price, "snap_competitor_nm_id": snap_competitor_nm_id}

    def _finalize_decision(
        self,
        run,
        dec: dict,
        success: bool,
        failed: bool,
        error_text: Optional[str] = None,
    ) -> str:
        """
        Сохраняет снимок и запись об изменении цены, возвращает reason_code.
        """
        product = dec["product"]
        current_data = dec["current_data"]
        new_price = dec["new_price"]
        limit_reason = dec["limit_reason"]
        reason_msg = dec["reason_msg"]
        _t0 = dec.get("_t0", time.monotonic())
        snap_competitor_price = dec.get("snap_competitor_price")
        snap_competitor_nm_id = dec.get("snap_competitor_nm_id")

        if not dec["needs_update"]:
            reason_code = dec["skip_code"] or "SKIPPED_INACTIVE"
            if current_data:
                # Снэпшот пишем всегда (UI и аналитика на них опираются).
                self._save_price_snapshot(product, current_data, competitor_nm_id=snap_competitor_nm_id, competitor_price=snap_competitor_price)
                # В price_changes пишем только реальные события — OUT_OF_STOCK и
                # NO_CHANGE были 95% мусора (раздули таблицу до 7+ GB). Пропускаем.
                _silent = ("OUT_OF_STOCK", "NO_CHANGE", "SKIPPED_INACTIVE", "SKIPPED_PROMO")
                if reason_code not in _silent:
                    self._save_price_change(run, product, current_data, new_price, None, reason_code, reason_msg)
            logger.info("METRIC " + json.dumps({"metric": "product_done", "run_id": run.id,
                "nm_id": product.nm_id, "sku": product.seller_sku, "reason": reason_code,
                "duration_ms": int((time.monotonic() - _t0) * 1000)}))
            return reason_code

        if failed:
            self._save_price_snapshot(product, current_data, competitor_nm_id=snap_competitor_nm_id, competitor_price=snap_competitor_price)
            _err_detail = error_text or "Не удалось применить цену в WB API (batch)"
            _full_msg = f"{reason_msg}. Отказ WB: {_err_detail}" if reason_msg else _err_detail
            self._save_price_change(run, product, current_data, new_price, None,
                                    "WB_API_ERROR", _full_msg)
            logger.info("METRIC " + json.dumps({"metric": "product_done", "run_id": run.id,
                "nm_id": product.nm_id, "sku": product.seller_sku, "reason": "WB_API_ERROR",
                "duration_ms": int((time.monotonic() - _t0) * 1000)}))
            return "WB_API_ERROR"

        # success
        spp = current_data.get("spp")
        if spp is not None and 0 <= spp < 100:
            new_final = new_price * (1 - spp / 100)
        else:
            new_final = None
        updated_data = dict(current_data)
        updated_data["seller_price"] = new_price
        if new_final:
            updated_data["final_price"] = new_final
        self._save_price_snapshot(product, updated_data, competitor_nm_id=snap_competitor_nm_id, competitor_price=snap_competitor_price)
        reason_code = limit_reason if limit_reason != "UPDATED" else "UPDATED"
        self._save_price_change(run, product, current_data, new_price, new_final,
                                reason_code, str(reason_msg) + ". Цена применена: " + str(new_price) + "₽")
        try:
            old_price = current_data.get("seller_price") if isinstance(current_data, dict) else getattr(current_data, "seller_price", None)
            log_activity(
                self.db, run.user_id, "price_changed",
                product_id=product.id, account_id=product.account_id,
                details={"nm_id": product.nm_id, "product_name": product.name,
                         "old_price": old_price, "new_price": int(new_price), "run_id": run.id}
            )
        except Exception as _e:
            logger.debug("log_activity price_changed failed: %s", _e)
        logger.info("METRIC " + json.dumps({"metric": "product_done", "run_id": run.id,
            "nm_id": product.nm_id, "sku": product.seller_sku, "reason": reason_code,
            "duration_ms": int((time.monotonic() - _t0) * 1000)}))
        return reason_code

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

        # Шаг 1: получаем только ID товаров + config_id (без загрузки ORM-объектов в память).
        # На 100к товаров это ~3-5 MB вместо ~800 MB при полной загрузке.
        id_query = (
            select(Product.id, ProductConfig.id, ProductConfig.competitor_nm_id)
            .join(ProductConfig)
            .join(Account, Product.account_id == Account.id)
            .filter(ProductConfig.is_active == True)
        )
        if product_ids:
            id_query = id_query.filter(Product.id.in_(product_ids))
        # Defense-in-depth: фильтр по user_id чтобы не обработать чужие товары
        if self.user_id is not None:
            id_query = id_query.filter(Account.user_id == self.user_id)

        id_rows = self.db.execute(id_query).all()
        all_product_ids              = [r[0] for r in id_rows]
        all_config_ids               = [r[1] for r in id_rows]
        direct_competitor_nm_ids     = {r[2] for r in id_rows if r[2]}

        # Шаг 2: Pre-fetch цен конкурентов батчами перед основным циклом.
        # Собираем все уникальные nm_id конкурентов, исключаем уже закешированные,
        # затем делаем batch-запросы к WB (100 товаров за раз) и кладём в Redis-кеш.
        # _cached_parse_product в цикле ниже будет читать только из кеша.
        if all_product_ids:
            from app.models.competitor_entry import CompetitorEntry
            competitor_nm_ids: set[int] = set(direct_competitor_nm_ids)
            # Один запрос вместо N: строим карту config_id -> [nm_id] и заодно
            # собираем уникальные nm_id конкурентов для прогрева каталога.
            self._competitor_entries_map = {}
            if all_config_ids:
                rows = self.db.query(
                    CompetitorEntry.product_config_id, CompetitorEntry.nm_id
                ).filter(
                    CompetitorEntry.product_config_id.in_(all_config_ids)
                ).all()
                for cfg_id, nm in rows:
                    self._competitor_entries_map.setdefault(cfg_id, []).append(nm)
                    competitor_nm_ids.add(nm)
            cold_ids = [
                nm_id for nm_id in competitor_nm_ids
                if cache_get(f"wb_catalog:{nm_id}") is None
            ]
            if cold_ids:
                logger.info(f"Pre-fetch конкурентов: {len(cold_ids)} холодных из {len(competitor_nm_ids)} уникальных")
                batch_results = await catalog_parser.parse_batch_ids(cold_ids)
                for nm_id, product_data in batch_results.items():
                    if product_data is not None:
                        cache_set(f"wb_catalog:{nm_id}", product_data.to_dict(), ttl=600)
                logger.info(f"Pre-fetch завершён: {sum(1 for v in batch_results.values() if v)} / {len(cold_ids)}")

        if not all_product_ids:
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
                    run.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
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
                started_at=datetime.now(timezone.utc).replace(tzinfo=None),
                completed_at=datetime.now(timezone.utc).replace(tzinfo=None),
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
                started_at=datetime.now(timezone.utc).replace(tzinfo=None)
            )
            self.db.add(run)
            self.db.commit()

        try:
            total_count = len(all_product_ids)
            logger.info(f"Найдено {total_count} товаров для обработки")
            run.total_products = total_count
            self.db.commit()

            logger.info("METRIC " + json.dumps({
                "metric": "run_start",
                "run_id": run.id,
                "total": total_count,
                "run_type": run_type,
                "dry_run": dry_run,
            }))

            # Обрабатываем товары чанками по CHUNK_SIZE штук:
            # — не грузим всё в RAM разом (100к товаров × ~8 KB = ~800 MB → OOM)
            # — коммитим каждые COMMIT_EVERY товаров вместо round-trip на каждый
            results = {'updated': 0, 'skipped': 0, 'errors': 0}
            CHUNK_SIZE  = 1000  # вдвое меньше чанков → меньше pre-fetch раундов
            COMMIT_EVERY = 200  # товаров между коммитами (меньше DB round-trips)

            # Загружаем цены ВСЕХ аккаунтов один раз до цикла (не повторять per-chunk)
            if not dry_run:
                await self._prefetch_all_prices_once()

            total_chunks = (total_count + CHUNK_SIZE - 1) // CHUNK_SIZE
            for chunk_idx, chunk_start in enumerate(range(0, total_count, CHUNK_SIZE)):
                chunk_ids = all_product_ids[chunk_start:chunk_start + CHUNK_SIZE]
                chunk_query = (
                    select(Product)
                    .filter(Product.id.in_(chunk_ids))
                    .options(joinedload(Product.config), joinedload(Product.account))
                )
                chunk_products = self.db.execute(chunk_query).scalars().unique().all()

                # === PRE-FETCH для текущего чанка ===
                if not dry_run:
                    # Прогрев каталога собственных товаров (цены уже загружены глобально)
                    # OOS товарам каталог не нужен (57% = OUT_OF_STOCK → пропускаются).
                    # Проверяем по prefetched_prices: если seller_price=0, товар нет на складе.
                    own_cold_ids = [
                        p.nm_id for p in chunk_products
                        if cache_get("wb_catalog:%d" % p.nm_id) is None
                        and (
                            p.nm_id not in self._prefetched_prices
                            or getattr(self._prefetched_prices.get(p.nm_id), 'seller_price', 0) > 0
                        )
                    ]
                    if own_cold_ids:
                        logger.info("Pre-fetch каталога: %d холодных товаров в чанке", len(own_cold_ids))
                        own_batch = await catalog_parser.parse_batch_ids(own_cold_ids)
                        for nm_id, data in own_batch.items():
                            if data is not None:
                                cache_set("wb_catalog:%d" % nm_id, data.to_dict(), ttl=600)

                # === ФАЗА 1: вычисляем решения параллельно (50 товаров одновременно) ===
                pending_updates = {}  # token -> list of {nmID, price}
                _sem = asyncio.Semaphore(50)

                async def _decide_safe(product):
                    async with _sem:
                        try:
                            return await self._decide_product(product, run, dry_run=dry_run)
                        except Exception as e:
                            logger.error("Ошибка решения для %s: %s", product.seller_sku, e, exc_info=True)
                            results["errors"] += 1
                            return {"product": product, "needs_update": False, "skip_code": "PARSE_ERROR",
                                    "current_data": None, "new_price": None, "limit_reason": None, "reason_msg": str(e)}

                decisions = list(await asyncio.gather(*[_decide_safe(p) for p in chunk_products]))

                for dec in decisions:
                    if dec["needs_update"] and not dry_run:
                        token = dec["product"].account.wb_api_token
                        if token not in pending_updates:
                            pending_updates[token] = []
                        pending_updates[token].append({"nmID": dec["product"].nm_id, "price": int(dec["new_price"])})

                # === ФАЗА 2: батч-обновление (один POST на аккаунт) ===
                # WB обрабатывает /upload/task асинхронно и может отклонить
                # отдельные товары (например, при снижении цены > 2x). Проверяем
                # per-nm статус через /history/goods/task, не доверяем тому что
                # POST вернул 200.
                update_ok = set()
                update_fail = set()
                update_errors: dict = {}

                if not dry_run and pending_updates:
                    # Импортируем тут чтобы не ломать существующие импорты в начале файла.
                    from app.services.wb_api import WBAuthError
                    from datetime import timedelta
                    for token, updates in pending_updates.items():
                        upload_id = None
                        try:
                            async with create_wb_client(token) as client:
                                resp = await client.update_prices(updates)
                                upload_id = (resp.get("data") or {}).get("id")
                                if resp.get("_already_queued"):
                                    # Задача уже в очереди WB, статуса нет —
                                    # помечаем как ok, следующий прогон уточнит.
                                    for u in updates:
                                        update_ok.add(u["nmID"])
                                    logger.info(
                                        "Batch update: %d цен — задача уже в очереди WB",
                                        len(updates),
                                    )
                                    continue
                                # Мелкие батчи (≤3 товара): не тратить 33с на verify.
                                # Если WB отклонит — поймаем в следующем прогоне.
                                if len(updates) <= 3:
                                    statuses = {u['nmID']: (True, '') for u in updates}
                                    logger.info('Batch ≤3: skip verify (uploadID=%s)', upload_id)
                                else:
                                    statuses = await client.verify_batch_update(upload_id)
                            logger.info(
                                "Batch update: %d цен отправлено (uploadID=%s, статусов=%d)",
                                len(updates), upload_id, len(statuses),
                            )
                            # Карты nm_id → product_config для пометки quarantine.
                            _nm_to_config = {
                                d["product"].nm_id: d["product"].config
                                for d in decisions if d.get("product") and d["product"].config
                            }
                            for u in updates:
                                nm = u["nmID"]
                                st = statuses.get(nm)
                                if st is None:
                                    update_fail.add(nm)
                                    update_errors[nm] = "Статус задачи WB не получен (таймаут опроса)"
                                elif st[0]:
                                    update_ok.add(nm)
                                else:
                                    update_fail.add(nm)
                                    err_text = st[1] or "WB отклонил цену"
                                    update_errors[nm] = err_text
                                    # Price Quarantine: помечаем wb_blocked_until=+24h.
                                    if 'quarantine' in err_text.lower() and nm in _nm_to_config:
                                        cfg = _nm_to_config[nm]
                                        _now = datetime.now(timezone.utc).replace(tzinfo=None)
                                        cfg.wb_blocked_until = _now + timedelta(hours=24)
                                        logger.warning(
                                            "nm_id=%s: WB Price Quarantine — блокируем на 24ч (до %s)",
                                            nm, cfg.wb_blocked_until.isoformat()
                                        )
                        except WBAuthError as e:
                            # 401 от WB → токен невалиден/отозван. Деактивируем аккаунт.
                            _acc = next(
                                (d["product"].account for d in decisions
                                 if d.get("product") and d["product"].account
                                 and d["product"].account.wb_api_token == token),
                                None,
                            )
                            if _acc:
                                logger.error("Аккаунт '%s' (id=%s): WB вернул 401 — деактивируем", _acc.name, _acc.id)
                                _acc.is_active = False
                                try:
                                    await telegram_notifier.notify_token_expired(_acc.name, _acc.id)
                                except Exception:
                                    pass
                            for u in updates:
                                update_fail.add(u["nmID"])
                                update_errors[u["nmID"]] = f"401 от WB — аккаунт деактивирован: {e}"
                        except Exception as e:
                            logger.error("Ошибка batch update (uploadID=%s): %s", upload_id, e)
                            for u in updates:
                                update_fail.add(u["nmID"])
                                update_errors[u["nmID"]] = f"Исключение при отправке: {e}"

                elif dry_run:
                    for d in decisions:
                        if d["needs_update"]:
                            update_ok.add(d["product"].nm_id)

                # === ФАЗА 3: сохраняем результаты ===
                pending = 0
                for dec in decisions:
                    if "skip_code" in dec and dec["current_data"] is None:
                        # Товар не обработан (PARSE_ERROR, WB_API_ERROR, etc.)
                        # без current_data → нечего сохранять, но СЧИТАЕМ в errors.
                        results["errors"] += 1
                        continue
                    try:
                        product = dec["product"]
                        reason_code = self._finalize_decision(
                            run, dec,
                            product.nm_id in update_ok,
                            product.nm_id in update_fail,
                            error_text=update_errors.get(product.nm_id),
                        )
                        if reason_code in ["UPDATED", "CLAMPED_TO_MIN", "CLAMPED_TO_MAX", "CLAMPED_TO_MARGIN"]:
                            results["updated"] += 1
                        elif reason_code in ["SKIPPED_PROMO", "SKIPPED_INACTIVE", "NO_CHANGE", "COMPETITOR_OOS", "OUT_OF_STOCK"]:
                            results["skipped"] += 1
                        else:
                            results["errors"] += 1
                        pending += 1
                        if pending >= COMMIT_EVERY:
                            self.db.commit()
                            pending = 0
                    except Exception as e:
                        logger.error("Ошибка сохранения для %s: %s", dec["product"].seller_sku, e, exc_info=True)
                        results["errors"] += 1
                        self.db.rollback()
                        pending = 0

                if pending:
                    self.db.commit()

                self.db.expire_all()

                logger.info(
                    f"Чанк {chunk_idx + 1}/{total_chunks} готов "
                    f"({min(chunk_start + CHUNK_SIZE, total_count)}/{total_count} товаров)"
                )

            # Обновляем статус прогона
            _duration_ms = int((time.monotonic() - _run_start) * 1000)
            run.updated_count = results['updated']
            run.skipped_count = results['skipped']
            run.error_count = results['errors']
            run.status = "completed"
            run.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
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
            run.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            self.db.commit()
            
            # Уведомление в Telegram
            await telegram_notifier.notify_scheduler_error(str(e))
            
            raise
