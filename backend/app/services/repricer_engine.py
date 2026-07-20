import asyncio
import json
import logging
import time
from types import SimpleNamespace
from typing import Optional, Dict, List, Tuple
from decimal import Decimal, ROUND_UP
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import select

from app.models import (
    Product, ProductConfig, Account, PriceSnapshot,
    RepricerRun, PriceChange, WBSubjectCommission
)
from app.models.app_setting import AppSetting
from app.services import (
    catalog_parser, create_wb_client, create_ozon_client, telegram_notifier
)
from app.utils.jwt import is_token_expired
from app.redis_client import cache_get, cache_set
from app.services.activity_logger import log_activity

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
        self._price_fetch_failed: set[int] = set()
        self._competitor_entries_map: Optional[Dict[int, list]] = None  # config_id -> [nm_id], прогрев на прогон
        self._catalog_single_fallbacks = 0
        self._catalog_single_fallback_failures = 0
        self._catalog_single_fallback_disabled = False

    def _marketplace(self, account: Optional[Account]) -> str:
        return getattr(account, "marketplace", "wb") or "wb"

    async def _get_ozon_current_price_data(self, product: Product, account: Account) -> Optional[Dict]:
        try:
            async with create_ozon_client(account.ozon_client_id, account.ozon_api_key) as client:
                prices = await client.get_prices(
                    product_ids=[product.nm_id] if product.nm_id else None,
                    offer_ids=[product.seller_sku] if product.seller_sku else None,
                )
                if not prices:
                    logger.warning("Ozon product product_id=%s offer_id=%s not found", product.nm_id, product.seller_sku)
                    return None
                price_data = prices[0]
                if price_data.price <= 0:
                    logger.warning("Ozon product product_id=%s has empty price", product.nm_id)
                    return None

                # Prices endpoint doesn't return product name/images — fetch via product info
                from app.services.ozon_api import OzonAPIClient as _OzClient
                name = price_data.name
                image_url = None
                if not name or name.startswith("Ozon product"):
                    info = await client.get_product_info(product_id=product.nm_id)
                    if info:
                        name = info.get("name") or name
                        image_url = _OzClient.extract_primary_image(info)

                return {
                    "seller_price": price_data.seller_price,
                    "discounted_price": price_data.final_price,
                    "discount": price_data.ozon_discount_pct,
                    "final_price": price_data.final_price,
                    "spp": Decimal(0),
                    "is_in_promo": False,
                    "total_quantity": 1,
                    "name": name,
                    "image_url": image_url,
                    "ozon_offer_id": price_data.offer_id,
                    "ozon_marketing_price": price_data.marketing_price,
                    "ozon_marketing_seller_price": price_data.marketing_seller_price,
                    "ozon_price_index": float(price_data.price_index),
                    "ozon_min_price": float(price_data.ozon_min_price),
                    "ozon_external_min_price": float(price_data.external_min_price),
                    "ozon_color_index": price_data.color_index,
                    "ozon_old_price": price_data.old_price,
                    "ozon_min_price_api": price_data.min_price,
                }
        except Exception as e:
            logger.error("Ошибка получения данных Ozon для product_id=%s: %s", product.nm_id, e)
            return None

    def _load_user_settings(self) -> Dict:
        """Load all repricer settings in 2 queries to avoid N+1 per product."""
        from app.models.app_setting import AppSetting
        keys = [
            "min_margin_pct", "wb_wallet_discount_pct",
            "commission_type", "commission_value",
            "logistics_type", "logistics_value",
            "acquiring_type", "acquiring_value",
            "brand_min_margin_rules",
        ]
        result: Dict = {
            "min_margin_pct": "0.0", "wb_wallet_discount_pct": "0",
            "commission_type": "pct", "commission_value": "0.0",
            "logistics_type": "rub", "logistics_value": "0.0",
            "acquiring_type": "pct", "acquiring_value": "0.0",
            "brand_min_margin_rules": json.dumps([
                {"brand": "Просвещение", "min_margin_pct": 20}
            ], ensure_ascii=False),
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
        result["brand_min_margin_rules_map"] = self._parse_brand_min_margin_rules(
            result.get("brand_min_margin_rules")
        )
        result["subject_commissions"] = {}
        try:
            for row in self.db.query(WBSubjectCommission).all():
                key = self._normalize_text(row.subject_name)
                if key:
                    result["subject_commissions"][key] = str(row.commission_pct)
        except Exception as e:
            logger.debug("WB subject commissions load skipped: %s", e)
        return result

    @staticmethod
    def _normalize_text(value) -> str:
        return " ".join(str(value or "").strip().casefold().replace("ё", "е").split())

    def _parse_brand_min_margin_rules(self, raw) -> Dict[str, Decimal]:
        if not raw:
            return {}
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            logger.warning("Invalid brand_min_margin_rules setting: %r", raw)
            return {}

        rules: Dict[str, Decimal] = {}
        if isinstance(parsed, dict):
            iterable = (
                {"brand": brand, "min_margin_pct": pct}
                for brand, pct in parsed.items()
            )
        elif isinstance(parsed, list):
            iterable = parsed
        else:
            return {}

        for item in iterable:
            if not isinstance(item, dict):
                continue
            brand_key = self._normalize_text(item.get("brand"))
            pct = item.get("min_margin_pct")
            if not brand_key or pct is None:
                continue
            try:
                rules[brand_key] = Decimal(str(pct))
            except Exception:
                continue
        return rules

    def _effective_min_margin_pct(self, config: ProductConfig, default_pct: Decimal) -> Decimal:
        product = getattr(config, "product", None)
        brand_key = self._normalize_text(getattr(product, "brand", None))
        if not brand_key:
            return default_pct
        rules = (self._user_settings or {}).get("brand_min_margin_rules_map") or {}
        for rule_brand, pct in rules.items():
            if rule_brand == brand_key or rule_brand in brand_key or brand_key in rule_brand:
                return Decimal(str(pct))
        return default_pct

    def _effective_fee_items(self, config: ProductConfig, settings_map: Dict) -> List[Tuple[str, Decimal]]:
        commission_type = settings_map.get("commission_type", "pct")
        commission_value = Decimal(str(settings_map.get("commission_value", "0")))

        product = getattr(config, "product", None)
        subject_key = self._normalize_text(getattr(product, "subject_name", None))
        subject_commissions = settings_map.get("subject_commissions") or {}
        if subject_key and subject_key in subject_commissions:
            commission_type = "pct"
            commission_value = Decimal(str(subject_commissions[subject_key]))

        return [
            (commission_type, commission_value),
            (
                settings_map.get("logistics_type", "rub"),
                Decimal(str(settings_map.get("logistics_value", "0"))),
            ),
            (
                settings_map.get("acquiring_type", "pct"),
                Decimal(str(settings_map.get("acquiring_value", "0"))),
            ),
        ]

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

    # Стратегия competitor должна стремиться к рассчитанной цене конкурента
    # (с учетом offset из конфига). Экономические ограничения применяются ниже:
    # min_price, max_price, margin guard и защита WB от резкого снижения.
    _TARGET_EPSILON_RUB = Decimal("0.01")

    # Cooldown для competitor-товаров: не переоценивать чаще чем раз в 4 часа
    # (6 раз в сутки). Защита от осцилляции и от отключения СПП Wildberries'ом.
    # Override: если финальная цена вышла из коридора (SPP drift) — немедленно.
    _COMPETITOR_COOLDOWN_HOURS = 4

    # Closed-loop verify+correct: после заливки СПП у WB готов за 3-7с (замер
    # 2026-06-10). Перепарсиваем факт у ВСЕХ обновлённых competitor-товаров и
    # доправляем вторым шагом тех, кто промахнулся через порог СПП (в т.ч.
    # cold-start: товар без истории осцилляции тоже может перелететь коридор).
    _CLOSED_LOOP_ENABLED = True
    _CLOSED_LOOP_SETTLE_SEC = 20
    _CLOSED_LOOP_CORRECTION_SETTLE_SEC = 5
    _CATALOG_SINGLE_FALLBACK_MAX_PER_RUN = 300
    _CATALOG_SINGLE_FALLBACK_MAX_FAILURES = 20

    def _competitor_target_final(self, config, competitor_price: Decimal) -> Decimal:
        """Return target final buyer price for competitor strategy."""
        offset_type = config.competitor_offset_type
        offset_direction = config.competitor_offset_direction
        offset_value = config.competitor_offset_value or Decimal(0)

        if offset_type == 'rub':
            if offset_direction == 'above':
                return competitor_price + offset_value
            return competitor_price - offset_value

        if offset_direction == 'above':
            return competitor_price * (1 + offset_value / 100)
        return competitor_price * (1 - offset_value / 100)

    def _has_rrp_target(self, config, spp: Decimal) -> bool:
        seller_price_direct, rrp = self._resolve_rrp(config, spp)
        return bool(seller_price_direct or rrp)

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
        Парсинг товара из WB Catalog API с кешированием в Redis.
        Не делает одиночных HTTP-запросов — только читает кеш,
        заполненный глобальным batch pre-fetch.
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

        stale = cache_get(f"wb_catalog_last:{nm_id}")
        if stale is not None:
            return SimpleNamespace(
                final_price=Decimal(str(stale['final_price'])),
                seller_price=Decimal(str(stale.get('seller_price', 0))),
                spp=Decimal(str(stale['spp'])) if stale['spp'] is not None else None,
                is_in_promo=stale['is_in_promo'],
                total_quantity=stale['total_quantity'],
                in_stock=stale['in_stock'],
                catalog_stale=True,
            )
        return None

    async def _parse_product_single_fallback(self, nm_id: int):
        """Try one live catalog request when batch/cache missed a product."""
        if self._catalog_single_fallback_disabled:
            return None
        if self._catalog_single_fallbacks >= self._CATALOG_SINGLE_FALLBACK_MAX_PER_RUN:
            self._catalog_single_fallback_disabled = True
            logger.warning(
                "Catalog single fallback disabled: per-run limit %d reached",
                self._CATALOG_SINGLE_FALLBACK_MAX_PER_RUN,
            )
            return None
        if self._catalog_single_fallback_failures >= self._CATALOG_SINGLE_FALLBACK_MAX_FAILURES:
            self._catalog_single_fallback_disabled = True
            logger.warning(
                "Catalog single fallback disabled: %d consecutive failures",
                self._catalog_single_fallback_failures,
            )
            return None

        self._catalog_single_fallbacks += 1
        try:
            product_data = await catalog_parser.parse_product(nm_id)
        except Exception as e:
            self._catalog_single_fallback_failures += 1
            logger.warning("Catalog single fallback failed for nm_id=%s: %s", nm_id, e)
            return None

        if product_data is None:
            self._catalog_single_fallback_failures += 1
            logger.warning("Catalog single fallback returned empty data for nm_id=%s", nm_id)
            return None

        self._catalog_single_fallback_failures = 0
        payload = product_data.to_dict()
        cache_set(f"wb_catalog:{nm_id}", payload, ttl=1800)
        cache_set(f"wb_catalog_last:{nm_id}", payload, ttl=86400)
        return SimpleNamespace(
            final_price=Decimal(str(payload['final_price'])),
            seller_price=Decimal(str(payload.get('seller_price', 0))),
            spp=Decimal(str(payload['spp'])) if payload['spp'] is not None else None,
            is_in_promo=payload['is_in_promo'],
            total_quantity=payload['total_quantity'],
            in_stock=payload['in_stock'],
            catalog_stale=False,
        )

    async def _prefetch_own_prices(self, products: list) -> None:
        # Fetch ALL seller prices per account (no filterNmID - WB rejects 100+ ids with 400)
        by_token = {}
        want_nm_ids = set()
        for product in products:
            account = product.account
            if not account or not account.is_active:
                continue
            if self._marketplace(account) != "wb":
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
        """Load all account prices once and retain pages fetched before errors."""
        accounts = self.db.execute(
            select(Account).filter(Account.user_id == self.user_id, Account.is_active == True)
        ).scalars().all()

        async def _fetch_account(account):
            if self._marketplace(account) != "wb":
                return
            if is_token_expired(account.wb_api_token):
                return
            fetched = 0
            async with create_wb_client(account.wb_api_token) as client:
                offset = 0
                consecutive_failures = 0
                while True:
                    try:
                        prices = await client.get_prices(nm_ids=None, limit=1000, offset=offset)
                        for p in prices:
                            self._prefetched_prices[p.nm_id] = p
                            fetched += 1
                        consecutive_failures = 0
                        if len(prices) < 1000:
                            break
                        offset += 1000
                    except Exception as e:
                        consecutive_failures += 1
                        if consecutive_failures >= 3:
                            logger.warning(
                                "Partial price prefetch for %s: retained %d items; "
                                "giving up after 3 consecutive failures at offset %d: %s",
                                account.name, fetched, offset, e,
                            )
                            break
                        logger.warning(
                            "Price prefetch for %s: error at offset %d (attempt %d/3), retrying: %s",
                            account.name, offset, consecutive_failures, e,
                        )
                        await asyncio.sleep(2.0 * consecutive_failures)
            if fetched:
                logger.info("Pre-fetch цен (global): %d товаров для аккаунта %s", fetched, account.name)

        await asyncio.gather(*[_fetch_account(a) for a in accounts])

    async def _prefetch_missing_prices(self, products: list) -> None:
        """Fetch missing prices with one POST batch per account.

        Failed or absent nmIDs are marked for this run so decision workers do
        not fall back to concurrent single-item GET requests.
        """
        by_token: Dict[str, dict] = {}
        for product in products:
            if product.nm_id in self._prefetched_prices:
                continue
            account = product.account
            if not account or not account.is_active:
                self._price_fetch_failed.add(product.nm_id)
                continue
            if self._marketplace(account) != "wb":
                continue
            if is_token_expired(account.wb_api_token):
                self._price_fetch_failed.add(product.nm_id)
                continue
            entry = by_token.setdefault(
                account.wb_api_token,
                {"account": account, "nm_ids": []},
            )
            entry["nm_ids"].append(product.nm_id)

        async def _fetch_batch(token: str, entry: dict) -> None:
            requested = list(dict.fromkeys(entry["nm_ids"]))
            account = entry["account"]
            for attempt in range(3):
                try:
                    async with create_wb_client(token) as client:
                        prices = await client.get_prices_by_articles(requested)
                    returned = set()
                    for price in prices:
                        self._prefetched_prices[price.nm_id] = price
                        returned.add(price.nm_id)
                    missing = set(requested) - returned
                    self._price_fetch_failed.update(missing)
                    logger.info(
                        "Price batch fallback for %s: %d/%d",
                        account.name,
                        len(returned),
                        len(requested),
                    )
                    return
                except Exception as e:
                    if attempt < 2:
                        logger.warning(
                            "Price batch fallback for %s (%d items): attempt %d/3 failed: %s",
                            account.name, len(requested), attempt + 1, e,
                        )
                        await asyncio.sleep(3.0 * (attempt + 1))
                    else:
                        self._price_fetch_failed.update(requested)
                        logger.warning(
                            "Price batch fallback failed for %s (%d items) after 3 attempts: %s",
                            account.name, len(requested), e,
                        )

        await asyncio.gather(
            *[_fetch_batch(token, entry) for token, entry in by_token.items()]
        )

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
            if self._marketplace(account) == "ozon":
                return await self._get_ozon_current_price_data(product, account)

            # Используем предзагруженные данные если есть (batch pre-fetch)
            if product.nm_id in self._prefetched_prices:
                price_data = self._prefetched_prices[product.nm_id]
            elif product.nm_id in self._price_fetch_failed:
                logger.warning(
                    "Price unavailable after batch fallback for nm_id=%s; "
                    "single-item GET skipped",
                    product.nm_id,
                )
                return None
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
                catalog_product = await self._parse_product_single_fallback(product.nm_id)
                if not catalog_product:
                    logger.warning(
                        "nm_id=%s: каталог недоступен, stale-кеш не используем для переоценки",
                        product.nm_id,
                    )
                    return {"catalog_stale": True}

            if not catalog_product:
                catalog_product = await self._parse_product_single_fallback(product.nm_id)
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

        is_ozon = getattr(product, 'account', None) and self._marketplace(product.account) == "ozon"
        if is_ozon:
            current_seller = current_data.get('seller_price') or Decimal(0)
            if abs(current_seller - rrp) <= self._TARGET_EPSILON_RUB:
                return None, "NO_CHANGE", f"Цена продавца уже равна РРЦ ({current_seller}₽)"
            return (
                rrp,
                "CALCULATED",
                f"РРЦ стратегия: целевая цена продавца {rrp}₽, текущая {current_seller}₽",
            )

        current_final = current_data['final_price']
        seller_discount = current_data.get('discount') or Decimal(0)

        consider_wallet = bool(getattr(config, 'rrp_consider_wallet', False))

        if consider_wallet and wallet_pct > 0:
            # Эффективная цена с учётом кошелька WB
            wallet_factor = 1 - wallet_pct / 100
            current_effective = current_final * wallet_factor

            logger.info(
                f"RRP+кошелёк стратегия: текущая с кошельком={current_effective}₽, "
                f"цель={rrp}₽, кошелёк={wallet_pct}%"
            )

            if abs(current_effective - rrp) <= self._TARGET_EPSILON_RUB:
                return None, "NO_CHANGE", (
                    f"Цена с кошельком уже равна РРЦ ({current_effective}₽)"
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
            logger.info(
                f"RRP стратегия: текущая={current_final}₽, "
                f"цель={rrp}₽"
            )

            if abs(current_final - rrp) <= self._TARGET_EPSILON_RUB:
                return None, "NO_CHANGE", f"Цена уже равна РРЦ ({current_final}₽)"

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
            instock = [(nm, price, s, b) for nm, price, s, b in prices if s and price > 0]
            if not instock:
                return None, None, None
            avg_final = sum(p for _, p, _, _ in instock) / len(instock)
            avg_basic = sum(b for _, _, _, b in instock) / len(instock)
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

            target_final = self._competitor_target_final(config, competitor_price)

            # Эффективный СПП берём из живого снапшота (final/discounted) БЕЗ
            # округления до целого процента. Хранимый true_spp округлён (строка
            # _get_current_price_data), из-за чего обратный расчёт давал ту же
            # цену продавца, что уже стоит, и зазор в несколько рублей до
            # конкурента не закрывался никогда — «застывшая точка».
            # Пример: артикул 3891012 — финальная 1588₽ при конкуренте 1593₽ и
            # СПП=34%: round_up(1593/0.66)=2414 == текущая цена → NO_CHANGE
            # навечно, хотя по факту мы на 5₽ дешевле конкурента.
            current_final = current_data.get('final_price') or Decimal(0)
            discounted = current_data.get('discounted_price') or Decimal(0)
            calc_spp = spp
            if discounted > 0 and current_final > 0:
                eff_spp = (Decimal(1) - current_final / discounted) * Decimal(100)
                if self._SPP_MIN <= eff_spp < Decimal(100):
                    calc_spp = eff_spp

            move = "lower" if current_final > target_final else "raise"
            if current_final > 0 and abs(current_final - target_final) <= self._TARGET_EPSILON_RUB:
                return None, "NO_CHANGE", (
                    f"Competitor ({rule}): финальная {current_final}₽ уже равна цели "
                    f"{target_final:.2f}₽ "
                    f"(конкурент {competitor_price}₽)"
                ), competitor_price, winning_nm_id

            # Обратный расчет цены продавца через эффективный СПП.
            try:
                new_seller_price = self._calculate_seller_price_from_final(target_final, calc_spp, seller_discount)

                reason_msg = (
                    f"Competitor ({rule}, {move}): конкурент nm_id={winning_nm_id} цена={competitor_price}₽, "
                    f"целевая {target_final:.2f}₽, СПП={calc_spp:.2f}%, новая цена продавца {new_seller_price}₽"
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
        min_margin_pct = self._effective_min_margin_pct(config, min_margin_pct)

        # Минимальный порог себестоимости: 1 руб.
        # При cost_price < 1 деление даёт некорректно огромную маржу.
        _COST_PRICE_MIN = Decimal("1")

        if min_margin_pct > 0 and config.product and config.product.cost_price:
            cost_price = Decimal(str(config.product.cost_price))
            if cost_price >= _COST_PRICE_MIN:
                sp = Decimal(str(new_price))
                # Применяем скидку продавца: реальная выручка = seller_price * (1 - discount/100)
                discount_factor = (1 - seller_discount / 100) if seller_discount > 0 else Decimal(1)
                eff_sp = sp * discount_factor
                fees = Decimal("0")
                fee_items = self._effective_fee_items(config, s)
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

        if config.max_price and new_price > config.max_price:
            logger.info(
                f"Цена после защиты маржи ({new_price}₽) превышает max_price ({config.max_price}₽) — ограничиваем"
            )
            new_price = config.max_price
            reason_code = "CLAMPED_TO_MAX"

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

        if new_price is None:
            _spp_for_rrp = current_data.get('spp') or Decimal(0)
            if reason_code == 'COMPETITOR_OOS' and self._has_rrp_target(config, _spp_for_rrp):
                logger.info(
                    "Конкурент OOS для nm_id=%s, fallback на РРЦ",
                    product.nm_id,
                )
                new_price, reason_code, reason_msg = await self._strategy_rrp(
                    product, config, current_data, wallet_pct
                )
                reason_msg = f"[Fallback РРЦ при OOS конкурента] {reason_msg}"

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

                if self._marketplace(account) == "wb" and is_token_expired(account.wb_api_token):
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
                if real_name and (not product.name or product.name.startswith('Товар ') or product.name.startswith('Ozon product ') or product.name == product.seller_sku):
                    product.name = real_name
                ozon_image = current_data.get('image_url', '')
                if ozon_image and not product.image_url:
                    product.image_url = ozon_image
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

        if self._marketplace(product.account) == "ozon" and config.strategy == "competitor":
            return {"product": product, "needs_update": False, "skip_code": "SKIPPED_INACTIVE",
                    "current_data": None, "new_price": None, "limit_reason": None,
                    "reason_msg": "Ozon competitor strategy is not supported yet", "_t0": _t0}

        # Cooldown для competitor: не переоценивать чаще раза в N ч.
        # OVERRIDE: если СПП дрейфнул и финальная цена ушла от цели конкурента —
        # реагируем немедленно вверх или вниз.
        from datetime import timedelta
        if config.strategy == "competitor" and self._COMPETITOR_COOLDOWN_HOURS > 0:
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=self._COMPETITOR_COOLDOWN_HOURS)
            recent = self.db.query(PriceChange).filter(
                PriceChange.product_id == product.id,
                PriceChange.reason_code.in_(["UPDATED", "CORRECTED", "CLAMPED_TO_MIN", "CLAMPED_TO_MAX", "CLAMPED_TO_MARGIN"]),
                PriceChange.changed_at >= cutoff,
            ).first()
            if recent:
                cooldown_override = False
                last_snap = self.db.query(PriceSnapshot).filter(
                    PriceSnapshot.product_id == product.id,
                    PriceSnapshot.competitor_price.isnot(None),
                ).order_by(PriceSnapshot.snapshot_at.desc()).first()
                if last_snap and last_snap.competitor_price:
                    try:
                        cat = await self._cached_parse_product(product.nm_id)
                        if cat and not getattr(cat, 'catalog_stale', False):
                            catalog_final = Decimal(str(getattr(cat, "final_price", 0) or 0))
                            total_quantity = getattr(cat, "total_quantity", None)
                            if total_quantity == 0 and catalog_final <= 0:
                                current_data = {
                                    "seller_price": Decimal(str(getattr(cat, "seller_price", 0) or 0)),
                                    "final_price": Decimal(0),
                                    "spp": Decimal(0),
                                    "is_in_promo": getattr(cat, "is_in_promo", False),
                                    "total_quantity": 0,
                                }
                                return {
                                    "product": product,
                                    "needs_update": False,
                                    "skip_code": "OUT_OF_STOCK",
                                    "current_data": current_data,
                                    "new_price": None,
                                    "limit_reason": None,
                                    "reason_msg": "Нет остатка (обновлено во время competitor cooldown)",
                                    "_t0": _t0,
                                }
                            if catalog_final > 0:
                                competitor_price = Decimal(str(last_snap.competitor_price))
                                target_final = self._competitor_target_final(config, competitor_price)
                                if abs(catalog_final - target_final) > self._TARGET_EPSILON_RUB:
                                    cooldown_override = True
                                    logger.warning(
                                        "Competitor cooldown OVERRIDE nm_id=%s: "
                                        "факт %.2f₽ != цель %.2f₽ (конкурент %.2f₽), SPP drift",
                                        product.nm_id, catalog_final, target_final, last_snap.competitor_price,
                                    )
                    except Exception as e:
                        logger.debug("Cooldown override check nm_id=%s: %s", product.nm_id, e)
                if not cooldown_override:
                    return {"product": product, "needs_update": False, "skip_code": "COOLDOWN",
                            "current_data": None, "new_price": None, "limit_reason": None,
                            "reason_msg": f"Competitor cooldown: последнее изменение {recent.changed_at.isoformat()}, "
                                          f"следующее через {self._COMPETITOR_COOLDOWN_HOURS}ч", "_t0": _t0}

        account = product.account
        if not account or not account.is_active:
            return {"product": product, "needs_update": False, "skip_code": "SKIPPED_INACTIVE",
                    "current_data": None, "new_price": None, "limit_reason": None, "reason_msg": None, "_t0": _t0}
        marketplace = self._marketplace(account)
        if marketplace == "ozon" and config.strategy == "competitor":
            return {"product": product, "needs_update": False, "skip_code": "SKIPPED_INACTIVE",
                    "current_data": None, "new_price": None, "limit_reason": None,
                    "reason_msg": "Ozon competitor strategy is not supported yet", "_t0": _t0}

        if dry_run:
            # Все числовые поля должны быть Decimal — их умножает/делит дальше
            # _calculate_seller_price_from_final, а int/int в Python 3 даёт float,
            # что несовместимо с Decimal-арифметикой (TypeError на float * Decimal).
            current_data = {
                "seller_price": Decimal(str(config.min_price or 1000)) + Decimal("50"),
                "final_price": Decimal(str(config.min_price or 1000)),
                "spp": Decimal("10"),
                "is_in_promo": False,
                "total_quantity": 5,
            }
        else:
            if marketplace == "wb" and is_token_expired(account.wb_api_token):
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

        wallet_pct = (self._user_settings or {}).get("wb_wallet_discount_pct", 0) if marketplace == "wb" else 0
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
            _spp_for_rrp = current_data.get('spp') or Decimal(0)
            if reason_code == "COMPETITOR_OOS" and self._has_rrp_target(config, _spp_for_rrp):
                logger.info("Конкурент OOS для nm_id=%s, fallback на РРЦ", product.nm_id)
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
        if marketplace == "wb" and _curr_sp_for_step > 0 and final_new_price < _curr_sp_for_step * Decimal("0.55"):
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
                _silent = ("OUT_OF_STOCK", "NO_CHANGE", "SKIPPED_INACTIVE", "SKIPPED_PROMO", "COOLDOWN")
                if reason_code not in _silent:
                    self._save_price_change(run, product, current_data, new_price, None, reason_code, reason_msg)
            logger.info("METRIC " + json.dumps({"metric": "product_done", "run_id": run.id,
                "nm_id": product.nm_id, "sku": product.seller_sku, "reason": reason_code,
                "duration_ms": int((time.monotonic() - _t0) * 1000)}))
            return reason_code

        if failed:
            self._save_price_snapshot(product, current_data, competitor_nm_id=snap_competitor_nm_id, competitor_price=snap_competitor_price)
            marketplace_label = "Ozon" if self._marketplace(product.account) == "ozon" else "WB"
            _err_detail = error_text or f"Не удалось применить цену в {marketplace_label} API (batch)"
            _full_msg = f"{reason_msg}. Отказ {marketplace_label}: {_err_detail}" if reason_msg else _err_detail
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

    async def _verify_and_correct_competitor(self, run, items: list) -> int:
        """Closed-loop PHASE 4: перепарсивает факт у ВСЕХ обновлённых competitor-
        товаров.

        Две задачи:
        1. ВСЕГДА обновляет snapshot РЕАЛЬНОЙ final_price (а не предсказанной) —
           иначе UI показывает фантомную цену СПП.
        2. Если факт ушёл от цели competitor — доправляет вверх или вниз.
        """
        if not items or not self._CLOSED_LOOP_ENABLED:
            return 0
        logger.info("Closed-loop: %d обновлённых competitor-товаров, пауза %dс на пересчёт СПП",
                    len(items), self._CLOSED_LOOP_SETTLE_SEC)
        await asyncio.sleep(self._CLOSED_LOOP_SETTLE_SEC)

        by_nm = {it["nm_id"]: it for it in items}
        try:
            fresh = await catalog_parser.parse_batch_ids(list(by_nm.keys()))
        except Exception as e:
            logger.error("Closed-loop: батч-парсинг факта упал: %s", e)
            return 0

        corrective: dict = {}
        plan: dict = {}
        snapshots_updated = 0

        for nm, it in by_nm.items():
            pd = fresh.get(nm)
            new_seller = it.get("new_price")
            if pd is None or not new_seller:
                continue
            actual_final = Decimal(str(getattr(pd, "final_price", 0) or 0))
            actual_spp = getattr(pd, "spp", None)
            new_seller = Decimal(str(new_seller))
            if actual_final <= 0 or new_seller <= 0:
                continue

            # Обновляем snapshot РЕАЛЬНОЙ финальной ценой и СПП.
            snap = dict(it["current_data"])
            snap["seller_price"] = new_seller
            snap["final_price"] = actual_final
            if actual_spp is not None:
                snap["spp"] = Decimal(str(actual_spp))
            try:
                self._save_price_snapshot(it["product"], snap,
                                          competitor_nm_id=it.get("competitor_nm_id"),
                                          competitor_price=it.get("competitor_price"))
                snapshots_updated += 1
            except Exception as e:
                logger.error("Closed-loop: ошибка обновления snapshot для nm=%s: %s", nm, e)

            comp = it.get("competitor_price")
            if not comp:
                continue
            comp = Decimal(str(comp))
            target_final = self._competitor_target_final(it["config"], comp)

            if abs(actual_final - target_final) <= self._TARGET_EPSILON_RUB:
                continue

            ratio = actual_final / new_seller
            if ratio <= 0:
                continue
            corrected = Decimal(self._round_up(target_final / ratio))
            if corrected < new_seller * Decimal("0.55"):
                corrected = Decimal(self._round_up(new_seller * Decimal("0.55") + Decimal("1")))
            if corrected > new_seller * Decimal("3"):
                logger.warning(
                    "Closed-loop: аномальная коррекция nm=%s (seller=%s→%s, ratio=%.4f), пропускаем",
                    nm, new_seller, corrected, ratio,
                )
                continue
            try:
                corrected, _lr = self._apply_limits(
                    corrected, it["config"],
                    seller_discount=(it["current_data"].get("discount") or Decimal(0)),
                )
            except Exception:
                pass
            if corrected == new_seller:
                continue
            corrective.setdefault(it["token"], []).append({"nmID": nm, "price": int(corrected)})
            plan[nm] = (it, corrected, target_final, actual_final, ratio)

        if not corrective:
            logger.info("Closed-loop: snapshots обновлено=%d, коррекция не нужна (все у цели)", snapshots_updated)
            try:
                self.db.commit()
            except Exception as e:
                logger.error("Closed-loop: commit упал: %s", e)
                self.db.rollback()
            return 0

        applied = set()
        for token, updates in corrective.items():
            try:
                async with create_wb_client(token) as client:
                    await client.update_prices(updates)
                for u in updates:
                    applied.add(u["nmID"])
            except Exception as e:
                logger.error("Closed-loop: ошибка отправки коррекции: %s", e)

        verified_after_correction: dict = {}
        if applied:
            try:
                if self._CLOSED_LOOP_CORRECTION_SETTLE_SEC > 0:
                    await asyncio.sleep(self._CLOSED_LOOP_CORRECTION_SETTLE_SEC)
                verified_after_correction = await catalog_parser.parse_batch_ids(list(applied))
            except Exception as e:
                logger.error("Closed-loop: повторный парсинг после коррекции упал: %s", e)

        n = 0
        for nm, (it, corrected, target_final, actual_final, ratio) in plan.items():
            if nm not in applied:
                continue
            product = it["product"]
            base = dict(it["current_data"])
            base["seller_price"] = Decimal(str(it["new_price"]))
            base["final_price"] = actual_final
            new_final_est = corrected * ratio
            corrected_final = new_final_est
            corrected_spp = None
            verified_pd = verified_after_correction.get(nm)
            if verified_pd is not None:
                parsed_final = Decimal(str(getattr(verified_pd, "final_price", 0) or 0))
                if parsed_final > 0:
                    corrected_final = parsed_final
                    parsed_spp = getattr(verified_pd, "spp", None)
                    if parsed_spp is not None:
                        corrected_spp = Decimal(str(parsed_spp))
            snap = dict(base)
            snap["seller_price"] = corrected
            snap["final_price"] = corrected_final
            if corrected_spp is not None:
                snap["spp"] = corrected_spp
            try:
                self._save_price_snapshot(product, snap,
                                          competitor_nm_id=it.get("competitor_nm_id"),
                                          competitor_price=it.get("competitor_price"))
                self._save_price_change(
                    run, product, base, corrected, corrected_final, "CORRECTED",
                    f"Closed-loop: факт {actual_final}₽ != цели {target_final:.2f}₽ → "
                    f"seller {it['new_price']}→{corrected}",
                )
                n += 1
            except Exception as e:
                logger.error("Closed-loop: ошибка сохранения для nm=%s: %s", nm, e)
        try:
            self.db.commit()
        except Exception as e:
            logger.error("Closed-loop: commit упал: %s", e)
            self.db.rollback()
        logger.info("Closed-loop: snapshots обновлено=%d, доправлено к цели=%d", snapshots_updated, n)
        return n

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
            select(Product.id, ProductConfig.id, ProductConfig.competitor_nm_id, Account.marketplace)
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
        wb_config_ids                = [r[1] for r in id_rows if (r[3] or "wb") == "wb"]
        direct_competitor_nm_ids     = {r[2] for r in id_rows if r[2] and (r[3] or "wb") == "wb"}

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
            if wb_config_ids:
                rows = self.db.query(
                    CompetitorEntry.product_config_id, CompetitorEntry.nm_id
                ).filter(
                    CompetitorEntry.product_config_id.in_(wb_config_ids)
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
                        cache_set(f"wb_catalog:{nm_id}", product_data.to_dict(), ttl=1800)
                logger.info(f"Pre-fetch завершён: {sum(1 for v in batch_results.values() if v)} / {len(cold_ids)}")

            # Глобальный pre-fetch ВСЕХ собственных товаров одним батчем.
            # Убирает тысячи одиночных HTTP-запросов из основного цикла.
            all_own_nm_ids = list({r[0] for r in self.db.execute(
                select(Product.nm_id).filter(Product.id.in_(all_product_ids))
            ).all()})
            own_cold = [nm for nm in all_own_nm_ids if cache_get("wb_catalog:%d" % nm) is None]
            if own_cold:
                logger.info(f"Глобальный pre-fetch: {len(own_cold)} холодных из {len(all_own_nm_ids)} собственных товаров")
                own_results = await catalog_parser.parse_batch_ids(own_cold)
                cached_count = 0
                for nm_id, product_data in own_results.items():
                    if product_data is not None:
                        cache_set("wb_catalog:%d" % nm_id, product_data.to_dict(), ttl=1800)
                        cache_set("wb_catalog_last:%d" % nm_id, product_data.to_dict(), ttl=86400)
                        cached_count += 1
                if cached_count == 0 and len(own_cold) > 10:
                    logger.warning("Глобальный pre-fetch: 0 успехов, повтор через 10 сек...")
                    await asyncio.sleep(10)
                    own_results = await catalog_parser.parse_batch_ids(own_cold)
                    for nm_id, product_data in own_results.items():
                        if product_data is not None:
                            cache_set("wb_catalog:%d" % nm_id, product_data.to_dict(), ttl=1800)
                            cache_set("wb_catalog_last:%d" % nm_id, product_data.to_dict(), ttl=86400)
                            cached_count += 1
                logger.info(f"Глобальный pre-fetch завершён: {cached_count}/{len(own_cold)} закешировано")

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
            results = {'updated': 0, 'skipped': 0, 'errors': 0, 'catalog_unavailable': 0}
            CHUNK_SIZE  = 1000  # вдвое меньше чанков → меньше pre-fetch раундов
            COMMIT_EVERY = 200  # товаров между коммитами (меньше DB round-trips)

            def _commit_run_progress() -> None:
                # Живой прогресс в БД: UI и cleanup-логика видят, что run реально двигается,
                # даже если текущий чанк тянется долго из-за 429/backoff WB API.
                run.updated_count = results['updated']
                run.skipped_count = results['skipped']
                run.error_count = results['errors']
                self.db.commit()

            # Загружаем цены: для маленьких прогонов точечно, для больших — все разом
            if not dry_run:
                if total_count <= 500:
                    logger.info("Малый прогон (%d товаров): точечный pre-fetch цен", total_count)
                else:
                    await self._prefetch_all_prices_once()

            total_chunks = (total_count + CHUNK_SIZE - 1) // CHUNK_SIZE
            # Накопитель для closed-loop: ВСЕ обновлённые competitor-товары.
            # Факт перепроверяется одной пост-фазой после всех чанков.
            competitor_updates: list = []
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
                    # A failed pagination page leaves earlier pages usable.
                    # Fetch only the missing chunk items in one POST batch.
                    await self._prefetch_missing_prices(chunk_products)

                    # Per-chunk pre-fetch убран: глобальный pre-fetch уже прогрел кеш
                    pass

                # === ФАЗА 1: вычисляем решения параллельно (50 товаров одновременно) ===
                pending_updates = {}  # (marketplace, account_id) -> {account, updates}
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
                        product = dec["product"]
                        account = product.account
                        marketplace = self._marketplace(account)
                        key = (marketplace, account.id)
                        entry = pending_updates.setdefault(key, {"account": account, "updates": []})
                        if marketplace == "ozon":
                            ozon_update = {
                                "_product_pk": product.id,
                                "product_id": product.nm_id,
                                "offer_id": product.seller_sku,
                                "price": int(dec["new_price"]),
                            }
                            cd = dec.get("current_data") or {}
                            if cd.get("ozon_old_price"):
                                ozon_update["old_price"] = str(int(cd["ozon_old_price"]))
                            entry["updates"].append(ozon_update)
                        else:
                            entry["updates"].append({
                                "_product_pk": product.id,
                                "nmID": product.nm_id,
                                "price": int(dec["new_price"]),
                            })

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
                    from app.services.ozon_api import OzonAuthError
                    for (_marketplace, _account_id), entry in pending_updates.items():
                        account = entry["account"]
                        updates = entry["updates"]
                        upload_id = None
                        try:
                            if _marketplace == "ozon":
                                ozon_updates = [
                                    {k: v for k, v in u.items() if k != "_product_pk"}
                                    for u in updates
                                ]
                                async with create_ozon_client(account.ozon_client_id, account.ozon_api_key) as client:
                                    resp = await client.update_prices(ozon_updates)
                                    raw_statuses = client.parse_update_statuses(resp, ozon_updates)
                                statuses = {
                                    u["_product_pk"]: raw_statuses.get(
                                        str(u.get("product_id") or u.get("offer_id")),
                                        (True, ""),
                                    )
                                    for u in updates
                                }
                                logger.info("Ozon batch update: %d цен отправлено", len(updates))
                                for u in updates:
                                    st = statuses.get(u["_product_pk"])
                                    if st is None:
                                        update_fail.add(u["_product_pk"])
                                        update_errors[u["_product_pk"]] = "Статус задачи Ozon не получен"
                                    elif st[0]:
                                        update_ok.add(u["_product_pk"])
                                    else:
                                        update_fail.add(u["_product_pk"])
                                        update_errors[u["_product_pk"]] = st[1] or "Ozon отклонил цену"
                                continue

                            token = account.wb_api_token
                            async with create_wb_client(token) as client:
                                wb_updates = [
                                    {k: v for k, v in u.items() if k != "_product_pk"}
                                    for u in updates
                                ]
                                resp = await client.update_prices(wb_updates)
                                upload_id = (resp.get("data") or {}).get("id")
                                if resp.get("_already_queued"):
                                    # Задача уже в очереди WB, статуса нет —
                                    # помечаем как ok, следующий прогон уточнит.
                                    for u in updates:
                                        update_ok.add(u["_product_pk"])
                                    logger.info(
                                        "Batch update: %d цен — задача уже в очереди WB",
                                        len(updates),
                                    )
                                    continue
                                # Мелкие батчи (≤3 товара): не тратить 33с на verify.
                                # Если WB отклонит — поймаем в следующем прогоне.
                                if len(updates) <= 3:
                                    statuses = {u["nmID"]: (True, "") for u in updates}
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
                                    update_fail.add(u["_product_pk"])
                                    update_errors[u["_product_pk"]] = "Статус задачи WB не получен (таймаут опроса)"
                                elif st[0]:
                                    update_ok.add(u["_product_pk"])
                                else:
                                    update_fail.add(u["_product_pk"])
                                    err_text = st[1] or "WB отклонил цену"
                                    update_errors[u["_product_pk"]] = err_text
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
                                  and d["product"].account.id == account.id),
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
                                update_fail.add(u["_product_pk"])
                                update_errors[u["_product_pk"]] = f"401 от WB — аккаунт деактивирован: {e}"
                        except OzonAuthError as e:
                            logger.error("Аккаунт '%s' (id=%s): Ozon auth error — деактивируем", account.name, account.id)
                            account.is_active = False
                            for u in updates:
                                update_fail.add(u["_product_pk"])
                                update_errors[u["_product_pk"]] = f"401 от Ozon — аккаунт деактивирован: {e}"
                        except Exception as e:
                            logger.error("Ошибка batch update (uploadID=%s): %s", upload_id, e)
                            for u in updates:
                                update_fail.add(u["_product_pk"])
                                update_errors[u["_product_pk"]] = f"Исключение при отправке: {e}"

                elif dry_run:
                    for d in decisions:
                        if d["needs_update"]:
                            update_ok.add(d["product"].id)

                # === ФАЗА 3: сохраняем результаты ===
                pending = 0
                for dec in decisions:
                    if "skip_code" in dec and dec["current_data"] is None:
                        if dec["skip_code"] == "CATALOG_UNAVAILABLE":
                            results["catalog_unavailable"] += 1
                        if dec["skip_code"] in ("COOLDOWN", "SKIPPED_INACTIVE", "SKIPPED_QUARANTINE",
                                                "CATALOG_UNAVAILABLE", "OUT_OF_STOCK"):
                            results["skipped"] += 1
                        else:
                            results["errors"] += 1
                        continue
                    try:
                        product = dec["product"]
                        reason_code = self._finalize_decision(
                            run, dec,
                            product.id in update_ok,
                            product.id in update_fail,
                            error_text=update_errors.get(product.id),
                        )
                        if reason_code in ["UPDATED", "CLAMPED_TO_MIN", "CLAMPED_TO_MAX", "CLAMPED_TO_MARGIN"]:
                            results["updated"] += 1
                            # Цена применена для competitor-товара — ставим в
                            # очередь на closed-loop verify+correct. Примитивы
                            # снимаем сейчас (объекты живые до expire_all ниже).
                            if dec.get("snap_competitor_price") and not dry_run:
                                competitor_updates.append({
                                    "product": product,
                                    "nm_id": product.nm_id,
                                    "token": product.account.wb_api_token,
                                    "config": product.config,
                                    "new_price": dec["new_price"],
                                    "current_data": dec["current_data"],
                                    "competitor_price": dec.get("snap_competitor_price"),
                                    "competitor_nm_id": dec.get("snap_competitor_nm_id"),
                                })
                        elif reason_code in ["SKIPPED_PROMO", "SKIPPED_INACTIVE", "NO_CHANGE", "COMPETITOR_OOS", "OUT_OF_STOCK", "COOLDOWN"]:
                            results["skipped"] += 1
                        else:
                            results["errors"] += 1
                        pending += 1
                        if pending >= COMMIT_EVERY:
                            _commit_run_progress()
                            pending = 0
                    except Exception as e:
                        logger.error("Ошибка сохранения для %s: %s", dec["product"].seller_sku, e, exc_info=True)
                        results["errors"] += 1
                        self.db.rollback()
                        pending = 0

                if pending:
                    _commit_run_progress()

                # Даже если в чанке не было DB-изменений, фиксируем накопленные counters,
                # чтобы run не выглядел "зависшим" с нулями в течение часов.
                if pending == 0:
                    _commit_run_progress()

                self.db.expire_all()

                logger.info(
                    f"Чанк {chunk_idx + 1}/{total_chunks} готов "
                    f"({min(chunk_start + CHUNK_SIZE, total_count)}/{total_count} товаров)"
                )

            # === ФАЗА 4: closed-loop verify+correct обновлённых competitor-товаров ===
            # WB пересчитывает СПП за секунды, поэтому доправляем промахи (через
            # порог СПП) в том же прогоне, не дожидаясь следующего часа.
            if competitor_updates and not dry_run:
                try:
                    corrected_n = await self._verify_and_correct_competitor(run, competitor_updates)
                    results["updated"] += corrected_n
                except Exception as e:
                    logger.error("Closed-loop коррекция упала: %s", e, exc_info=True)

            # Обновляем статус прогона
            _duration_ms = int((time.monotonic() - _run_start) * 1000)
            catalog_unavailable = results.get("catalog_unavailable", 0)
            catalog_unavailable_pct = (
                catalog_unavailable / run.total_products * 100
                if run.total_products > 0
                else 0
            )
            catalog_outage = (
                catalog_unavailable > 10
                and run.total_products > 100
                and catalog_unavailable_pct > 50
            )
            run.updated_count = results['updated']
            run.skipped_count = results['skipped']
            run.error_count = results['errors']
            run.status = "failed" if catalog_outage else "completed"
            if catalog_outage:
                run.error_message = (
                    "WB catalog unavailable for most products "
                    f"({catalog_unavailable}/{run.total_products}); "
                    "check CATALOG_PROXY_URL / SSH reverse tunnel"
                )
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
                "catalog_unavailable": catalog_unavailable,
                "duration_ms": _duration_ms,
                "run_type": run_type,
                "dry_run": dry_run,
                "products_per_sec": round(run.total_products / (_duration_ms / 1000), 2) if _duration_ms > 0 else 0,
            }))

            if catalog_outage and not dry_run:
                await telegram_notifier.notify_catalog_parse_errors(
                    run_id=run.id,
                    total=run.total_products,
                    errors=catalog_unavailable,
                    error_pct=catalog_unavailable_pct,
                )

            if results['updated'] == 0 and run.total_products > 0 and not dry_run:
                await telegram_notifier.notify_zero_updates(
                    run_id=run.id,
                    total=run.total_products,
                    errors=results['errors'],
                    run_type=run_type,
                    duration_seconds=_duration_ms // 1000,
                )

            error_pct = (results['errors'] / run.total_products * 100) if run.total_products > 0 else 0
            if error_pct > 50 and run.total_products > 10 and not dry_run:
                await telegram_notifier.notify_catalog_parse_errors(
                    run_id=run.id,
                    total=run.total_products,
                    errors=results['errors'],
                    error_pct=error_pct,
                )

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
