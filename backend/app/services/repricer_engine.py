import asyncio
import json
import logging
import time
import uuid
from collections import Counter
from types import SimpleNamespace
from typing import Optional, Dict, List, Tuple
from decimal import Decimal, ROUND_UP, ROUND_FLOOR
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import select

from app.models import (
    Product, ProductConfig, Account, PriceSnapshot,
    RepricerRun, PriceChange, WBSubjectCommission
)
from app.config import settings
from app.models.app_setting import AppSetting
from app.services import (
    catalog_parser, create_wb_client, create_ozon_client, telegram_notifier
)
from app.services.ozon_buyer_price import estimate_from_price_data
from app.services.ozon_site_price import SiteRow, fetch_site_rows
from app.utils.jwt import is_token_expired
from app.redis_client import (
    acquire_product_locks,
    cache_get,
    cache_set,
    cache_ttl,
    cache_delete,
    guard_active,
    release_product_locks,
)
from app.services.activity_logger import log_activity

logger = logging.getLogger(__name__)


class RepricerEngine:
    """
    Движок репрайсера с двумя стратегиями ценообразования
    
    Стратегии:
    1. RRP - поддержание финальной цены в диапазоне [РРЦ - tolerance; РРЦ + tolerance]
    2. Competitor - привязка к цене конкурента с заданным смещением
    """
    
    def __init__(
        self,
        db: Session,
        user_id: Optional[int] = None,
        product_lock_owner: Optional[str] = None,
        hold_product_locks: bool = False,
        force_catalog_refresh: bool = False,
        bypass_competitor_cooldown: bool = False,
    ):
        self.db = db
        self.user_id = user_id  # None → глобальные/дефолтные настройки
        self._user_settings: Optional[Dict] = None  # loaded once per run
        self._prefetched_prices: Dict[int, object] = {}  # nm_id -> PriceData, cleared per chunk
        # Ozon: «Цена на сайте» по кабинету — одна выгрузка отчёта на прогон
        # (account_id -> {offer_id: цена}); кабинет, по которому отчёт не
        # собрался, помечаем, чтобы не дёргать его на каждом товаре.
        self._ozon_site_prices: Dict[int, Dict[str, SiteRow]] = {}
        self._ozon_site_failed: set = set()
        # Товары кабинета считаются параллельно, а отчёт нужен один —
        # замок не даёт заказать его несколько раз сразу.
        self._ozon_site_lock: Optional[asyncio.Lock] = None
        self._price_fetch_failed: set[int] = set()
        self._competitor_entries_map: Optional[Dict[int, list]] = None  # config_id -> [nm_id], прогрев на прогон
        self._catalog_single_fallbacks = 0
        self._catalog_single_fallback_failures = 0
        self._catalog_single_fallback_disabled = False
        self._product_lock_owner = product_lock_owner or f"engine:{uuid.uuid4().hex}"
        self._hold_product_locks = hold_product_locks
        self._force_catalog_refresh = force_catalog_refresh
        self._bypass_competitor_cooldown = bypass_competitor_cooldown
        # Отказы WB «Price Quarantine» за прогон: (кабинет, артикул, nm_id)
        self._quarantine_hits: List[Tuple[str, str, int]] = []
        # config_id, для которых каталог конкурентов был недоступен в этом прогоне
        self._competitor_unavailable: set = set()
        # config_id, для которых загрузились не все конкуренты: цель посчитана
        # по части списка, поэтому цену по ней только снижаем
        self._competitor_partial: set = set()
        # Пробный прогон ничего не пишет в БД (снимки/изменения/активность)
        self._dry_run = False
        # Итоговые коды по всем товарам прогона (в т.ч. не записанным в историю):
        # сохраняются в stage_progress["reason_counts"] и показываются в UI.
        self._reason_counts: Counter = Counter()

    def _marketplace(self, account: Optional[Account]) -> str:
        return getattr(account, "marketplace", "wb") or "wb"

    def _ozon_buyer_price(self, price_data):
        """Цена покупателя на Ozon и доля доинвестирования, поданная как СПП.

        Возвращает (цена_покупателя | None, спп, оценка | None). None в первом
        элементе означает «витрину посчитать не удалось»: у товара нет ценовых
        индексов либо они противоречат друг другу. В этом случае вызывающий код
        оставляет прежнее поведение и запрещает снижение — опускать цену по
        неизвестной витрине опаснее, чем не трогать её вовсе.
        """
        estimate = estimate_from_price_data(price_data)
        if estimate is None:
            return None, Decimal(0), None

        if estimate.spread_pct > self._OZON_BUYER_PRICE_MAX_SPREAD_PCT:
            logger.warning(
                "Ozon product_id=%s: ценовые индексы разошлись на %.2f%% "
                "(%s) — цену покупателя не используем",
                price_data.product_id, estimate.spread_pct, estimate.per_source,
            )
            return None, Decimal(0), estimate

        ratio = estimate.ratio_to(price_data.seller_price)
        if not ratio or ratio <= 0:
            return None, Decimal(0), estimate

        implied_spp = (Decimal(1) - ratio) * 100
        if not (self._SPP_MIN <= implied_spp <= self._SPP_MAX):
            logger.warning(
                "Ozon product_id=%s: доинвестирование %.2f%% вне допустимого "
                "диапазона [%s; %s] — цену покупателя не используем",
                price_data.product_id, implied_spp, self._SPP_MIN, self._SPP_MAX,
            )
            return None, Decimal(0), estimate

        return estimate.buyer_price, implied_spp, estimate

    async def _ozon_site_prices_for_account(self, account) -> Dict[str, SiteRow]:
        """«Цена на сайте» по всему кабинету: одна выгрузка отчёта на прогон.

        Отчёт строится около минуты и весит мегабайты, поэтому держим его
        результат в памяти прогона и ставим в Redis короткий замок — чтобы
        ручной прогон сразу за плановым не заказывал отчёт заново. Если
        отчёта нет, товары возьмут прошлый замер из product_config, а если и
        его нет — витрину посчитает обратный счёт по ценовому индексу.
        """
        key = int(getattr(account, "id", 0) or 0)
        cached = self._ozon_site_prices.get(key)
        if cached is not None:
            return cached
        if key in self._ozon_site_failed:
            return {}
        if not getattr(account, "ozon_client_id", None) or not getattr(account, "ozon_api_key", None):
            self._ozon_site_failed.add(key)
            return {}

        if self._ozon_site_lock is None:
            self._ozon_site_lock = asyncio.Lock()
        async with self._ozon_site_lock:
            return await self._ozon_fetch_site_prices(account, key)

    async def _ozon_fetch_site_prices(self, account, key: int) -> Dict[str, SiteRow]:
        # Под замком проверяем заново: пока ждали, отчёт мог уже приехать.
        cached = self._ozon_site_prices.get(key)
        if cached is not None:
            return cached
        if key in self._ozon_site_failed:
            return {}

        guard = f"ozon_site_report:{key}"
        if guard_active(guard):
            self._ozon_site_failed.add(key)
            logger.info(
                "Ozon кабинет %s: отчёт заказывали меньше %d с назад — "
                "берём прошлый замер витрины",
                getattr(account, "name", key), self._OZON_SITE_REPORT_LOCK_SEC,
            )
            return {}

        cache_set(guard, {"at": time.time()}, ttl=self._OZON_SITE_REPORT_LOCK_SEC)
        try:
            async with create_ozon_client(account.ozon_client_id, account.ozon_api_key) as client:
                prices = await fetch_site_rows(client)
        except Exception as e:  # noqa: BLE001 — сбой отчёта не должен ронять прогон
            self._ozon_site_failed.add(key)
            logger.warning(
                "Ozon кабинет %s: отчёт с ценой на сайте не получен (%s) — "
                "витрину считаем по ценовым индексам",
                getattr(account, "name", key), e,
            )
            return {}

        self._ozon_site_prices[key] = prices
        logger.info("Ozon кабинет %s: цена на сайте получена по %d товарам",
                    getattr(account, "name", key), len(prices))
        return prices

    async def _ozon_site_measurement(self, product, account, price_data) -> Optional[Dict]:
        """Прямой замер витрины Ozon по одному товару.

        Возвращает {'price', 'implied_spp', 'stale'} либо None, если замера
        нет или он неправдоподобен. `stale` — отчёт ещё показывает витрину от
        прежней цены продавца: Ozon пересчитывает доинвестирование не сразу,
        и если принять такой замер за правду, цикл начнёт раскачивать цену.
        """
        seller = price_data.seller_price
        if not seller or seller <= 0:
            return None

        config = getattr(product, "config", None)
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        rows = await self._ozon_site_prices_for_account(account)
        row = None
        for candidate in (price_data.offer_id, getattr(product, "seller_sku", None)):
            if candidate:
                row = rows.get(str(candidate))
                if row is not None:
                    break

        price = row.price if row is not None else None
        stock = row.stock if row is not None else None

        if price is None and config is not None:
            # Отчёта в этом прогоне нет — опираемся на прошлый замер, но
            # только если он снят при той же цене продавца и ещё не протух.
            stored = getattr(config, "ozon_site_price", None)
            stored_at = getattr(config, "ozon_site_price_at", None)
            stored_seller = getattr(config, "ozon_site_seller_price", None)
            fresh = (
                stored is not None and stored_at is not None
                and stored_seller is not None
                and Decimal(str(stored_seller)) == seller
                and (now - stored_at) <= timedelta(minutes=self._OZON_SITE_PRICE_TTL_MIN)
            )
            if fresh:
                price = Decimal(str(stored))

        if price is None:
            return None

        price = Decimal(str(price))
        k = price / seller
        if not (self._OZON_K_MIN <= k <= self._OZON_K_MAX):
            logger.warning(
                "Ozon product_id=%s: цена на сайте %s при цене продавца %s даёт "
                "k=%s вне [%s; %s] — замер не используем",
                price_data.product_id, price, seller,
                k.quantize(Decimal("0.0001")), self._OZON_K_MIN, self._OZON_K_MAX,
            )
            return None

        prev_price = getattr(config, "ozon_site_price", None) if config is not None else None
        prev_seller = getattr(config, "ozon_site_seller_price", None) if config is not None else None
        accepted_at = getattr(config, "ozon_k_updated_at", None) if config is not None else None

        stale = bool(
            prev_price is not None and prev_seller is not None
            and Decimal(str(prev_price)) == price
            and Decimal(str(prev_seller)) != seller
        )
        if stale and accepted_at is not None and (
            now - accepted_at > timedelta(minutes=self._OZON_SITE_STALE_MAX_MIN)
        ):
            # Витрина не шевелится слишком долго — значит она такая и есть.
            stale = False

        if config is not None and not self._dry_run:
            config.ozon_site_price = price
            config.ozon_site_seller_price = seller
            config.ozon_site_price_at = now

        return {
            "price": price,
            "implied_spp": (Decimal(1) - k) * 100,
            "stale": stale,
            "stock": stock,
        }

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

                # Цена покупателя на Ozon ниже нашей: маркетплейс доинвестирует
                # скидку (медиана по кабинету — 65% от выставленной цены). В API
                # её нет, но она восстанавливается из ценовых индексов. Без этого
                # движок считал ценой покупателя нашу собственную цену и, целясь
                # в РРЦ, опускал витрину примерно на треть ниже цели.
                #
                # Долю доинвестирования подаём как СПП: вся обратная математика
                # (_calculate_seller_price_from_final) тогда работает без правок,
                # ровно как для WB.
                buyer_price, implied_spp, buyer_estimate = self._ozon_buyer_price(price_data)
                buyer_source = "price_index" if buyer_price else None

                # Прямой замер витрины важнее обратного счёта по индексу:
                # «Цена на сайте» из отчёта — ровно та цена, которую видит
                # покупатель, без округления индекса и без поправок к нему.
                site = await self._ozon_site_measurement(product, account, price_data)
                if site is not None:
                    buyer_price = site["price"]
                    implied_spp = site["implied_spp"]
                    buyer_source = "site_report"

                return {
                    "seller_price": price_data.seller_price,
                    "discounted_price": buyer_price or price_data.final_price,
                    # k уже включает скидку продавца, иначе она учлась бы дважды
                    "discount": Decimal(0) if buyer_price else price_data.ozon_discount_pct,
                    "final_price": buyer_price or price_data.final_price,
                    "spp": implied_spp,
                    # Витрина неизвестна — разрешаем только повышение.
                    # ozon_seller_mode снимает этот запрет: там РРЦ понимается
                    # как цена продавца, и снижать до неё ничем не рискованно.
                    **({} if buyer_price else {"catalog_fallback": True, "ozon_seller_mode": True}),
                    "ozon_buyer_price": float(buyer_price) if buyer_price else None,
                    "ozon_buyer_source": buyer_source,
                    "ozon_site_price": float(site["price"]) if site else None,
                    "ozon_site_price_stale": bool(site and site["stale"]),
                    "ozon_buyer_price_spread": (
                        float(buyer_estimate.spread_pct) if buyer_estimate else None
                    ),
                    "ozon_buyer_price_sources": (
                        buyer_estimate.sources if buyer_estimate else 0
                    ),
                    "is_in_promo": False,
                    # Остаток знаем только из отчёта; без него считаем, что
                    # товар в продаже, — так вело себя прежнее поведение.
                    "total_quantity": (
                        site["stock"] if site and site["stock"] is not None else 1
                    ),
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
            # Кошелёк по умолчанию 3% — как в API настроек и в SPP Guard.
            "min_margin_pct": "0.0", "wb_wallet_discount_pct": "3.0",
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
        # Правило срабатывает, если название из правила входит в бренд товара
        # («Просвещение» → «Издательство Просвещение»). Обратное направление
        # убрано: бренд «Про» получал маржу «Просвещения».
        for rule_brand, pct in rules.items():
            if rule_brand == brand_key or rule_brand in brand_key:
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

    # Ozon: оценку цены покупателя принимаем, только если независимые ценовые
    # индексы сошлись. На живом кабинете расхождение укладывается в 5% у
    # 12 303 из 12 311 товаров, так что порог почти ничего не отсекает, но
    # защищает от единичных карточек с противоречивыми индексами.
    _OZON_BUYER_PRICE_MAX_SPREAD_PCT = Decimal("5")

    # Ozon замкнутый цикл: сглаживание реального k по EMA (0..1, чем меньше —
    # тем инертнее), шаг не больше ±15% за прогон (гасит раскачку, если индекс
    # на один прогон отстал от только что отправленной цены), и коридор, в
    # котором k считаем правдоподобным.
    _OZON_K_EMA_ALPHA = Decimal("0.5")
    _OZON_MAX_STEP_RATIO = Decimal("0.15")
    _OZON_K_MIN = Decimal("0.20")
    _OZON_K_MAX = Decimal("1.20")

    # Прямой замер витрины («Цена на сайте» из отчёта Ozon) живёт этот срок.
    # Отчёт строится раз в прогон; если он не собрался, разрешаем опереться на
    # прошлый замер, пока он не старше этого окна, — иначе уходим на индекс.
    _OZON_SITE_PRICE_TTL_MIN = 90
    # Сколько терпим «Ozon ещё не пересчитал витрину». Пока замер не обновился
    # после нашей правки цены, цену не трогаем, но не вечно: если витрина не
    # шевелится столько времени, значит она такая и есть (например, товар не
    # в продаже), и работаем по тому, что видим.
    _OZON_SITE_STALE_MAX_MIN = 360
    # Замок на заказ отчёта: защищает от повторного заказа, если прогоны
    # идут подряд (ручной сразу за плановым).
    _OZON_SITE_REPORT_LOCK_SEC = 600

    # Мёртвая зона РРЦ для Ozon — процент от РРЦ вверх (ниже РРЦ не опускаемся
    # никогда, поэтому коридор односторонний). Зона нужна, чтобы не дёргать
    # цену из-за шума замера: частая смена цены сбивает бустинг Ozon.
    #
    # Ширина зоны — это цена ошибки измерения, и платит её покупатель: цена
    # приходит к коридору сверху и замирает у его верхней границы. Пока
    # витрину угадывали по ценовому индексу (ошибка до пары процентов), зона
    # в 1,5% держала весь кабинет выше РРЦ на 22…170 ₽. Поэтому зон теперь
    # две: широкая для оценки по индексу и узкая для прямого замера «Цены на
    # сайте», где ошибки измерения нет.
    _OZON_RRP_DEADBAND_PCT = Decimal("1.5")
    _OZON_RRP_MEASURED_DEADBAND_PCT = Decimal("0.3")

    # Не трогаем цену ради копеек: правка меньше этого шага ничего не меняет
    # на витрине, но стоит смены цены. Работает только когда витрина ВЫШЕ РРЦ —
    # если она ниже, правим на сколько нужно, РРЦ это пол.
    _OZON_MIN_STEP_RUB = Decimal("2")
    _OZON_MIN_STEP_RATIO = Decimal("0.001")

    # Стратегия competitor должна стремиться к рассчитанной цене конкурента
    # (с учетом offset из конфига). Экономические ограничения применяются ниже:
    # min_price, max_price, margin guard и защита WB от резкого снижения.
    _TARGET_EPSILON_RUB = Decimal("0.01")

    # Конкурента читаем в каждом прогоне. Если цена конкурента не изменилась,
    # а из коридора вышла только наша цена из-за дрейфа СПП, требуем два
    # одинаковых наблюдения. Переход конкурента в OOS также подтверждаем дважды.
    _COMPETITOR_CONFIRM_TTL_SEC = 2 * 3600

    # Closed-loop verify+correct: после заливки СПП у WB готов за 3-7с (замер
    # 2026-06-10). Перепарсиваем факт у ВСЕХ обновлённых competitor-товаров и
    # доправляем вторым шагом тех, кто промахнулся через порог СПП (в т.ч.
    # cold-start: товар без истории осцилляции тоже может перелететь коридор).
    _CLOSED_LOOP_ENABLED = True
    _CLOSED_LOOP_SETTLE_SEC = 20
    _CLOSED_LOOP_CORRECTION_SETTLE_SEC = 5
    _CATALOG_SINGLE_FALLBACK_MAX_PER_RUN = 300
    _CATALOG_SINGLE_FALLBACK_MAX_FAILURES = 20
    # Карточки нет в успешном ответе каталога (удалена/скрыта): помним час и не
    # считаем это сбоем загрузки — такой конкурент приравнивается к «нет в наличии».
    _CATALOG_MISSING_TTL_SEC = 3600
    # Бюджеты времени на батч-парсинг каталога. Обычно своих товаров хватает
    # минуты; без бюджета упавший туннель держал прогон часами.
    _CATALOG_PREFETCH_BUDGET_SEC = 15 * 60
    _CLOSED_LOOP_PARSE_BUDGET_SEC = 5 * 60
    # Расчёт одного товара (включая одиночный запрос в каталог и WB API).
    _DECIDE_TIMEOUT_SEC = 300
    # ── Пропуск товаров в акции WB (SKIPPED_PROMO) ─────────────────────────
    # Зачем добавлялось: в 4:00 из 1С выгружаются цены, посчитанные по марже
    # 27% (20% по «Просвещению»). Размер скидки в акции WB считает от цены,
    # которую до этого наценил репрайсер, — она выше. После выгрузки из 1С
    # база становится ниже, процент скидки остаётся прежним, и акционная цена
    # уезжает вниз вместе с маржой. Чтобы репрайсер не разгонял базу товарам
    # в акциях, он их полностью пропускал.
    #
    # Почему выключено: `is_in_promo` в каталоге WB — это просто непустой
    # список `promotions` (акции, в которых товар числится), он взведён
    # примерно у 2/3 ассортимента. С порогом в 1% скидки продавца под скип
    # попадало ~8000 активных товаров, и они переставали переоцениваться
    # вообще — ни конкурент, ни РРЦ, ни защита маржи не отрабатывали, потому
    # что проверка стоит до вызова стратегии.
    #
    # Механизм оставлен в коде и включается через PROMO_SKIP_ENABLED=true;
    # порог настраивается через PROMO_DISCOUNT_SKIP_THRESHOLD.
    # Для исходной задачи (не разгонять базу перед выгрузкой из 1С) лучше
    # подходит признак реального реверта цены со стороны WB — детектор
    # Promo-lock в _decide_product.
    _PROMO_SKIP_ENABLED = bool(settings.PROMO_SKIP_ENABLED)
    _PROMO_DISCOUNT_SKIP_THRESHOLD = Decimal(str(settings.PROMO_DISCOUNT_SKIP_THRESHOLD))

    # ── Пол РРЦ (стратегия rrp) ──────────────────────────────────────────
    # Цель ставим ровно на минимальную целую цену, достаточную для РРЦ.
    # Фактическое округление WB проверяет closed-loop и при необходимости
    # поднимает цену продавца на следующий достижимый рубль.
    _RRP_FLOOR_MARGIN_RUB = Decimal("0")
    # Коридор над РРЦ, внутри которого цену не трогаем: РРЦ … РРЦ + допуск
    # (rrp_tolerance из настроек товара). Ниже РРЦ цена не опускается никогда.
    # Верхняя граница берётся ровно из настройки rrp_tolerance.
    _RRP_MIN_CORRIDOR_RUB = Decimal("0")
    # Повышение до РРЦ одним шагом (ночная выгрузка 1С роняет цену на ~22%,
    # при старом лимите +25% товар оставался ниже РРЦ до следующего прогона).
    _RRP_MAX_RAISE_RATIO = Decimal("1.6")
    # Повышение за один прогон для стратегии «конкурент».
    _COMPETITOR_MAX_RAISE_RATIO = Decimal("1.25")
    # Каталог WB отдаёт новую цену продавца со старой ценой покупателя ещё
    # какое-то время после изменения. Из такой пары СПП выходит ложным
    # (45-56% вместо 31%), и движок то разгонял цену вдвое, то ронял её ниже
    # РРЦ. Признак отставания: цена продавца сдвинулась больше чем на 1%,
    # а цена покупателя в каталоге осталась той же до копейки. В этом случае
    # берём СПП из последнего достоверного наблюдения и цену только повышаем.
    _CATALOG_LAG_PRICE_DELTA = Decimal("0.01")
    _CATALOG_LAG_MAX_AGE_SEC = 3 * 3600
    # Скачок СПП больше чем на 5 п.п. сразу после смены цены продавца —
    # тоже признак несогласованной пары (или порога СПП у WB): цену только повышаем.
    _CATALOG_LAG_SPP_JUMP_PP = Decimal("5")
    _OBS_TTL_SEC = 24 * 3600
    # Последний известный СПП для товара без остатка / при недоступном каталоге
    # ищем в снимках не старше двух недель.
    _LAST_SPP_LOOKBACK_DAYS = 14
    # Уведомление о карантине WB: один и тот же товар не чаще раза в 6 часов.
    _QUARANTINE_NOTIFY_DEDUP_SEC = 6 * 3600
    # После отказа WB «карантин» не снижаем цену товара 6 часов (повышать можно).
    _QUARANTINE_HOLD_SEC = 6 * 3600
    # Снижение за один шаг: WB отправляет в карантин уже при −20…45%.
    _MAX_DROP_RATIO = Decimal("0.8")
    # После отката SPP Guard час не снижаем цену (повышать можно).
    _SPP_GUARD_COOLDOWN_SEC = 3600
    # Откат/доправка closed-loop больше чем в 1.6 раза — признак битых данных.
    _CLOSED_LOOP_MAX_RATIO = Decimal("1.6")
    # Каталог: свои товары перечитываем, если запись старше 20 минут
    # (то есть каждый прогон), и не используем данные старше 45 минут.
    _CATALOG_REFRESH_AGE_SEC = 20 * 60
    _CATALOG_MAX_AGE_SEC = 45 * 60
    _CATALOG_CACHE_TTL_SEC = 7200
    # Коридор стратегии «конкурент»: цена покупателя от цели (округлённой вверх
    # до рубля) до цели + max(1 ₽, 0.3%). Без него округление WB вниз давало
    # пинг-понг SPP Guard ↔ основной расчёт.
    _COMPETITOR_TOLERANCE_PCT = Decimal("0.3")
    _COMPETITOR_TOLERANCE_MIN_RUB = Decimal("1")
    # 401 от WB: кабинет выключаем только после 3 отказов за 2 часа.
    _WB_AUTH_FAILS_TO_DEACTIVATE = 3
    _WB_AUTH_FAIL_WINDOW_SEC = 2 * 3600
    _TOKEN_NOTIFY_DEDUP_SEC = 6 * 3600
    # Коды «товар пропущен» (не ошибка). Всё остальное, кроме обновлений, —
    # ошибка прогона (PARSE_ERROR, WB_API_ERROR).
    _SKIP_CODES = frozenset({
        "SKIPPED_PROMO", "SKIPPED_INACTIVE", "SKIPPED_QUARANTINE", "NO_CHANGE",
        "COMPETITOR_OOS", "COMPETITOR_PARTIAL", "OUT_OF_STOCK", "COOLDOWN",
        "COOLDOWN_SPP_GUARD", "COMPETITOR_CONFIRM", "COMPETITOR_OOS_CONFIRM",
        "CATALOG_UNAVAILABLE", "LIMITED_BY_MIN", "LIMITED_BY_MAX",
        "LIMITED_BY_MARGIN",
    })
    _UPDATED_CODES = ("UPDATED", "CLAMPED_TO_MIN", "CLAMPED_TO_MAX", "CLAMPED_TO_MARGIN")
    # Повторяющиеся пропуски пишем в историю не чаще раза в 6 часов на товар:
    # ~740 товаров с конкурентами без остатка дали бы ~35 тыс. строк в сутки.
    # Счётчики прогона (reason_counts) учитывают все.
    _SKIP_LOG_DEDUP_SEC = 6 * 3600
    _DEDUP_SKIP_CODES = ("COMPETITOR_OOS", "CATALOG_UNAVAILABLE", "COMPETITOR_PARTIAL")

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

    def _competitor_tolerance(self, target_final: Decimal) -> Decimal:
        return max(self._COMPETITOR_TOLERANCE_MIN_RUB, target_final * self._COMPETITOR_TOLERANCE_PCT / 100)

    def _competitor_in_corridor(self, final: Decimal, target_final: Decimal) -> bool:
        """Цена покупателя в коридоре [ceil(цель); ceil(цель) + допуск]."""
        target_int = Decimal(self._round_up(target_final))
        return target_int <= final <= target_int + self._competitor_tolerance(target_final)

    @staticmethod
    def _competitor_guard_key(product_id: int, kind: str) -> str:
        return f"repricer:competitor_{kind}:{product_id}"

    def _confirm_competitor_observation(
        self,
        product_id: int,
        kind: str,
        payload: Dict,
    ) -> bool:
        """Return True only after the same competitor condition is seen twice."""
        key = self._competitor_guard_key(product_id, kind)
        normalized = {name: str(value) for name, value in payload.items()}
        previous = cache_get(key)
        if previous == normalized:
            cache_delete(key)
            return True
        cache_set(key, normalized, ttl=self._COMPETITOR_CONFIRM_TTL_SEC)
        return False

    def _has_rrp_target(self, config, spp: Decimal) -> bool:
        seller_price_direct, rrp = self._resolve_rrp(config, spp)
        return bool(seller_price_direct or rrp)

    def _is_discounted_wb_promo(self, current_data: Dict, account: Account) -> bool:
        """Return True when a WB promo has a seller discount above the threshold.

        Выключено по умолчанию — см. комментарий у _PROMO_SKIP_ENABLED.
        """
        if not self._PROMO_SKIP_ENABLED:
            return False
        if self._marketplace(account) != "wb" or not current_data.get("is_in_promo"):
            return False
        try:
            seller_discount = Decimal(str(current_data.get("discount") or 0))
        except (TypeError, ValueError, ArithmeticError):
            logger.warning(
                "Некорректная скидка продавца для promo-check: %r",
                current_data.get("discount"),
            )
            return False
        return seller_discount > self._PROMO_DISCOUNT_SKIP_THRESHOLD

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
    
    async def _preflight_catalog_check(
        self,
        sample_nm_ids: Optional[List[int]] = None,
        max_wait: int = 300,
        interval: int = 30,
    ) -> bool:
        """Проверяет, что каталог WB отвечает через CATALOG_PROXY_URL той же
        сессией, что и парсер, и ждёт до max_wait секунд.

        Раньше проверка шла GET-ом прямо на адрес прокси и принимала 407 за
        успех — то есть не проверяла ни туннель до WB, ни сессию.
        """
        if not getattr(settings, "CATALOG_PROXY_URL", ""):
            return True
        nm_ids = [n for n in (sample_nm_ids or []) if n][:3] or [282199514]
        url = catalog_parser._build_batch_url(nm_ids)
        waited = 0
        while True:
            data = await catalog_parser._request_via_catalog_proxy(url)
            if data is not None:
                if waited > 0:
                    logger.info("Catalog proxy restored after %ds wait", waited)
                else:
                    logger.info("Catalog proxy OK (pre-flight)")
                return True
            if waited >= max_wait:
                break
            logger.warning("Catalog proxy unavailable (pre-flight, waited %ds/%ds)", waited, max_wait)
            await asyncio.sleep(interval)
            waited += interval
        logger.error("Catalog proxy still down after %ds — proceeding anyway", max_wait)
        return False

    async def _cached_parse_product(self, nm_id: int):
        """
        Парсинг товара из WB Catalog API с кешированием в Redis.
        Не делает одиночных HTTP-запросов — только читает кеш,
        заполненный глобальным batch pre-fetch.
        """
        cache_key = f"wb_catalog:{nm_id}"
        cached = cache_get(cache_key)
        if cached is not None:
            # Запись живёт 2 часа как запас на время аварии каталога, но в расчёт
            # идёт только свежая: старше 45 минут — считаем устаревшей.
            fetched_at = cached.get('fetched_at')
            stale = bool(fetched_at) and (time.time() - float(fetched_at)) > self._CATALOG_MAX_AGE_SEC
            return SimpleNamespace(
                final_price=Decimal(str(cached['final_price'])),
                seller_price=Decimal(str(cached.get('seller_price', 0))),
                spp=Decimal(str(cached['spp'])) if cached['spp'] is not None else None,
                is_in_promo=cached['is_in_promo'],
                total_quantity=cached['total_quantity'],
                in_stock=cached['in_stock'],
                catalog_stale=stale,
            )

        return None

    def _catalog_cache_put(self, nm_id: int, product_data) -> dict:
        payload = product_data.to_dict()
        payload['fetched_at'] = time.time()
        cache_set(f"wb_catalog:{nm_id}", payload, ttl=self._CATALOG_CACHE_TTL_SEC)
        return payload

    def _catalog_needs_refresh(self, nm_id: int) -> bool:
        return cache_ttl(f"wb_catalog:{nm_id}") < self._CATALOG_CACHE_TTL_SEC - self._CATALOG_REFRESH_AGE_SEC

    def _mark_catalog_missing(self, nm_ids) -> None:
        for nm_id in nm_ids:
            cache_set(f"wb_catalog_missing:{nm_id}", {"ts": time.time()}, ttl=self._CATALOG_MISSING_TTL_SEC)

    def _catalog_missing(self, nm_id: int) -> bool:
        """Каталог недавно ответил без этой карточки (удалена/скрыта)."""
        return cache_get(f"wb_catalog_missing:{nm_id}") is not None

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
        not_found: set = set()
        try:
            product_data = await catalog_parser.parse_product(nm_id, not_found=not_found)
        except Exception as e:
            self._catalog_single_fallback_failures += 1
            logger.warning("Catalog single fallback failed for nm_id=%s: %s", nm_id, e)
            return None

        if product_data is None:
            if nm_id in not_found:
                # Каталог ответил, карточки нет — это не сбой каталога
                self._mark_catalog_missing([nm_id])
                return None
            self._catalog_single_fallback_failures += 1
            logger.warning("Catalog single fallback returned empty data for nm_id=%s", nm_id)
            return None

        self._catalog_single_fallback_failures = 0
        payload = self._catalog_cache_put(nm_id, product_data)
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

            # Чистый СПП WB — скидка от цены ПОСЛЕ скидки продавца до цены покупателя.
            # «СПП» каталога (1 − product/basic) уже включает скидку продавца, а
            # _calculate_seller_price_from_final делит на скидку ещё раз: цена
            # завышалась в 1/(1−скидка) раз (+47% при скидке 32%). Пару цен берём
            # из каталога (basic и product согласованы по времени между собой),
            # скидку продавца — из WB API.
            discounted_price = price_data.discounted_price
            seller_discount = Decimal(str(price_data.discount or 0))
            catalog_final = catalog_product.final_price
            catalog_basic = Decimal(str(getattr(catalog_product, "seller_price", 0) or 0))
            # spp_exact — без округления, для обратного расчёта цены продавца
            # (округлённый до 0.01 СПП давал «застывшую точку» у round_up).
            spp_exact = None
            if catalog_final > 0 and catalog_basic > 0 and seller_discount < 100:
                base = catalog_basic * (1 - seller_discount / 100)
                spp_exact = (1 - catalog_final / base) * 100
                true_spp = spp_exact.quantize(Decimal("0.01"))
            elif catalog_final > 0 and discounted_price > 0:
                spp_exact = (1 - catalog_final / discounted_price) * 100
                true_spp = spp_exact.quantize(Decimal("0.01"))
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

            result = {
                'seller_price': price_data.seller_price,
                'discounted_price': discounted_price,
                'discount': price_data.discount,
                'final_price': catalog_product.final_price,
                'spp': true_spp,
                'spp_exact': spp_exact,
                'is_in_promo': catalog_product.is_in_promo,
                'total_quantity': catalog_product.total_quantity,
                'name': getattr(catalog_product, "name", None),
            }
            # Цена продавца в каталоге и в API должны совпадать. Если нет —
            # каталог ещё показывает пару цен для прежней цены продавца
            # (1С или ручная правка только что сменили цену).
            api_seller = Decimal(str(price_data.seller_price or 0))
            if (
                catalog_final > 0 and catalog_basic > 0 and api_seller > 0
                and abs(catalog_basic - api_seller) / api_seller > self._CATALOG_LAG_PRICE_DELTA
            ):
                result['catalog_price_mismatch'] = True
                result['catalog_basic'] = catalog_basic
            return result

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

    def _rrp_corridor_top(self, config, rrp: Decimal) -> Decimal:
        """Верхняя граница коридора цены покупателя: РРЦ + настроенный допуск."""
        tolerance = Decimal(str(getattr(config, "rrp_tolerance", None) or 0))
        return rrp + max(tolerance, self._RRP_MIN_CORRIDOR_RUB)

    def _rrp_floor_target(self, min_final: Decimal) -> Decimal:
        """Целевая цена покупателя, которая после округления WB не опустится ниже min_final."""
        return Decimal(self._round_up(min_final)) + self._RRP_FLOOR_MARGIN_RUB

    def _nudge_rrp_seller_down_if_safe(
        self,
        calculated_seller: Decimal,
        current_seller: Decimal,
        current_final: Decimal,
        rrp: Decimal,
        wallet_factor: Decimal = Decimal("1"),
    ) -> Decimal:
        """Убирает тупик двойного округления у верхней границы РРЦ.

        Иногда целевая цена покупателя и обратный расчёт цены продавца оба
        округляются вверх. В результате стратегия видит превышение коридора,
        но снова получает текущую цену продавца. Один рубль можно снять только
        если свежая согласованная пара seller/final показывает, что расчётная
        цена с кошельком после этого останется не ниже РРЦ.
        """
        if current_seller <= 1 or current_final <= 0 or calculated_seller < current_seller:
            return calculated_seller
        lower_seller = current_seller - Decimal("1")
        predicted_effective = (
            current_final * lower_seller / current_seller * wallet_factor
        )
        if predicted_effective >= rrp:
            return lower_seller
        return calculated_seller

    @staticmethod
    def _discounted(seller_price, discount) -> Decimal:
        """Цена после скидки продавца (база, от которой WB считает СПП)."""
        seller_price = Decimal(str(seller_price or 0))
        discount = Decimal(str(discount or 0))
        return seller_price * (1 - discount / 100)

    def _trusted_observation(self, product: Product, current_data: Dict) -> Dict:
        """Отсекает несогласованную пару «цена продавца / цена покупателя» из каталога WB.

        Последнее достоверное наблюдение (цена после скидки продавца, цена
        покупателя, чистый СПП) храним в Redis. Если с тех пор цена продавца
        сдвинулась больше чем на 1%, то:
        - цена покупателя в каталоге не изменилась ни на копейку — каталог ещё не
          пересчитал цену (так было с разгоном до 10-17 тыс.): берём СПП из
          наблюдения и помечаем catalog_lag;
        - СПП скакнул больше чем на 5 п.п. — это или отставание, или порог СПП
          у WB. Отличить нельзя, поэтому берём текущий СПП, но тоже помечаем
          catalog_lag.
        С флагом catalog_lag цену только повышаем: снижать по сомнительному СПП
        значит рисковать уйти ниже РРЦ / ниже конкурента.
        """
        key = f"price_obs:{product.nm_id}"
        cur_base = self._discounted(current_data.get("seller_price"), current_data.get("discount"))
        cur_final = Decimal(str(current_data.get("final_price") or 0))
        cur_spp = current_data.get("spp")
        data = dict(current_data)
        data["catalog_final_raw"] = cur_final
        if cur_base <= 0 or cur_final <= 0 or cur_spp is None:
            return data

        if current_data.get("catalog_price_mismatch"):
            # Цена продавца в API уже новая, а каталог отдаёт пару цен для
            # прежней. СПП из этой пары (одна карточка, один размер) верен для
            # прежней цены; цену покупателя при текущей цене оцениваем по нему.
            # Снижать по оценке не даём (catalog_lag), наблюдение не обновляем.
            spp_pair = Decimal(str(current_data.get("spp_exact", cur_spp)))
            est_final = (cur_base * (1 - spp_pair / 100)).quantize(Decimal("0.01"))
            logger.warning(
                "nm_id=%s: цена продавца в каталоге %s ≠ в API %s — каталог отстаёт; "
                "СПП %.2f%% из пары каталога, расчётная цена покупателя %s",
                product.nm_id, current_data.get("catalog_basic"), current_data.get("seller_price"),
                spp_pair, est_final,
            )
            data["final_price"] = est_final
            data["catalog_lag"] = True
            return data

        obs = cache_get(key)
        if obs:
            try:
                obs_base = Decimal(str(obs.get("base") or 0))
                obs_final = Decimal(str(obs.get("final") or 0))
                obs_spp = Decimal(str(obs.get("spp")))
                age = time.time() - float(obs.get("ts") or 0)
            except (TypeError, ValueError, ArithmeticError):
                obs_base = obs_final = Decimal(0)
                obs_spp = None
                age = float("inf")
            base_moved = (
                obs_base > 0
                and abs(cur_base - obs_base) / obs_base > self._CATALOG_LAG_PRICE_DELTA
            )
            if base_moved and obs_spp is not None and age < self._CATALOG_LAG_MAX_AGE_SEC:
                if cur_final == obs_final:
                    est_final = (cur_base * (1 - obs_spp / 100)).quantize(Decimal("0.01"))
                    logger.warning(
                        "nm_id=%s: каталог WB отстаёт (база %s→%s, покупатель %s без изменений) — "
                        "СПП %s%% из наблюдения %d мин назад, расчётная цена покупателя %s",
                        product.nm_id, obs_base, cur_base, cur_final, obs_spp, int(age // 60), est_final,
                    )
                    data["spp"] = obs_spp
                    data["spp_exact"] = obs_spp
                    data["final_price"] = est_final
                    data["catalog_lag"] = True
                    return data
                if abs(Decimal(str(cur_spp)) - obs_spp) > self._CATALOG_LAG_SPP_JUMP_PP:
                    logger.warning(
                        "nm_id=%s: СПП %s%% → %s%% сразу после смены цены (база %s→%s) — только повышение",
                        product.nm_id, obs_spp, cur_spp, obs_base, cur_base,
                    )
                    data["catalog_lag"] = True
                    return data

        cache_set(key, {
            "base": float(cur_base),
            "final": float(cur_final),
            "spp": float(cur_spp),
            "ts": time.time(),
        }, ttl=self._OBS_TTL_SEC)
        return data

    def _last_known_spp(self, product: Product) -> Optional[Decimal]:
        """Последний достоверный чистый СПП: из Redis, иначе из снимков цен."""
        obs = cache_get(f"price_obs:{product.nm_id}")
        if obs and obs.get("spp") is not None:
            try:
                return Decimal(str(obs["spp"]))
            except (TypeError, ValueError, ArithmeticError):
                pass
        try:
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=self._LAST_SPP_LOOKBACK_DAYS)
            # Только снимки товара в наличии: для товара без остатка старая логика
            # писала «прогнозный» снимок с СПП = 0 (цена покупателя = цене продавца).
            snap = self.db.query(PriceSnapshot).filter(
                PriceSnapshot.product_id == product.id,
                PriceSnapshot.final_price > 0,
                PriceSnapshot.seller_price > 0,
                PriceSnapshot.total_quantity > 0,
                PriceSnapshot.snapshot_at >= cutoff,
            ).order_by(PriceSnapshot.snapshot_at.desc()).first()
        except Exception as e:
            logger.warning("nm_id=%s: не удалось прочитать снимки для СПП: %s", product.nm_id, e)
            self.db.rollback()
            return None
        if not snap:
            return None
        base = self._discounted(snap.seller_price, snap.discount)
        if base <= 0:
            return None
        spp = ((1 - Decimal(str(snap.final_price)) / base) * 100).quantize(Decimal("0.01"))
        if not (self._SPP_MIN <= spp <= self._SPP_MAX):
            return None
        return spp

    def _fallback_price_data(self, product: Product, current_data: Optional[Dict] = None) -> Optional[Dict]:
        """Оценка цены покупателя по последнему известному СПП.

        Для случаев, когда каталог WB цену покупателя не отдаёт: каталог
        недоступен или товара нет в наличии (тогда в каталоге цена 0 и раньше
        считалось, что СПП = 0 — цена продавца падала до РРЦ/0.97 и после
        поставки покупатель видел −30% от РРЦ). Цена продавца есть в WB API
        всегда. Дальше разрешено только повышение (флаг catalog_fallback).
        """
        if current_data and current_data.get("seller_price"):
            seller_price = current_data.get("seller_price")
            discount = current_data.get("discount")
            discounted_price = current_data.get("discounted_price")
            total_quantity = current_data.get("total_quantity")
            is_in_promo = current_data.get("is_in_promo", False)
        else:
            price_data = self._prefetched_prices.get(product.nm_id)
            if price_data is None:
                return None
            seller_price = price_data.seller_price
            discount = price_data.discount
            discounted_price = price_data.discounted_price
            total_quantity = None
            is_in_promo = False
        spp = self._last_known_spp(product)
        if spp is None:
            return None
        base = self._discounted(seller_price, discount)
        if base <= 0:
            return None
        est_final = (base * (1 - spp / 100)).quantize(Decimal("0.01"))
        return {
            "seller_price": seller_price,
            "discounted_price": discounted_price,
            "discount": discount,
            "final_price": est_final,
            "catalog_final_raw": None,
            "spp": spp,
            "is_in_promo": is_in_promo,
            "total_quantity": total_quantity,
            "catalog_fallback": True,
        }

    def _ozon_update_realized_k(self, config, k_obs: Decimal) -> Decimal:
        """Обновляет сглаженный k товара по новому наблюдению и возвращает его.

        k_obs — мгновенное наблюдение (цена покупателя по индексу / цена
        продавца) за текущий прогон. Правдоподобные значения вливаем в EMA,
        неправдоподобные (индекс отстал, мусорная карточка) игнорируем и
        оставляем прежний k. Первое наблюдение задаёт k напрямую.

        В пробном прогоне состояние не трогаем.
        """
        prev = getattr(config, "ozon_realized_k", None)
        prev = Decimal(str(prev)) if prev is not None else None

        plausible = self._OZON_K_MIN <= k_obs <= self._OZON_K_MAX
        if not plausible:
            # Нечему верить в этом наблюдении — держим накопленное значение.
            return prev if prev is not None else k_obs

        if prev is None:
            k_eff = k_obs
        else:
            a = self._OZON_K_EMA_ALPHA
            k_eff = a * k_obs + (1 - a) * prev
        k_eff = k_eff.quantize(Decimal("0.0001"))

        if not self._dry_run:
            config.ozon_realized_k = k_eff
            config.ozon_k_updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        return k_eff

    def _ozon_rrp_closed_loop(self, product, config, current_data: Dict, rrp: Decimal):
        """Замкнутый цикл РРЦ для Ozon: доводим цену продавца до РРЦ на витрине.

        Ozon доинвестирует скидку поверх нашей цены, поэтому цель задаётся
        через долю k = витрина / цена_продавца. Откуда берётся k — решает
        источник витрины, и это главное различие:

        * **Прямой замер** («Цена на сайте» из отчёта Ozon). Витрина известна
          точно, k считается одним делением, сглаживать и поправлять нечего,
          мёртвая зона узкая. Единственная осторожность — отчёт отстаёт от
          нашей правки цены: пока он показывает прежнюю витрину, цену не
          трогаем, иначе цикл начнёт сам себя разгонять.
        * **Оценка по ценовому индексу** (запасной путь). Индекс округлён и
          пересчитывается у Ozon с задержкой, ошибка — проценты. Такой k
          сглаживаем по EMA, умножаем на замеренную поправку
          `ozon_storefront_factor`, если она есть, и держим мёртвую зону шире.
        """
        seller = Decimal(str(current_data.get("seller_price") or 0))
        buyer = Decimal(str(current_data.get("final_price") or 0))
        if seller <= 0 or buyer <= 0:
            return None, "SKIPPED_INACTIVE", "Нет цены продавца или витрины Ozon"

        measured = current_data.get("ozon_buyer_source") == "site_report"

        if measured:
            if current_data.get("ozon_site_price_stale"):
                return None, "NO_CHANGE", (
                    f"Ozon ещё не пересчитал витрину после смены цены продавца "
                    f"(на сайте прежние {buyer}₽) — ждём следующего замера"
                )
            k_eff = (buyer / seller).quantize(Decimal("0.0001"))
            if k_eff <= 0:
                return None, "SKIPPED_INACTIVE", "Не удалось оценить k для Ozon"
            if not self._dry_run:
                config.ozon_realized_k = k_eff
                config.ozon_k_updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            est_storefront = buyer
            deadband_pct = self._OZON_RRP_MEASURED_DEADBAND_PCT
            source_note = "витрина с сайта"
        else:
            factor = getattr(config, "ozon_storefront_factor", None)
            factor = Decimal(str(factor)) if factor else Decimal(1)

            k_obs = (buyer * factor) / seller
            k_eff = self._ozon_update_realized_k(config, k_obs)
            if k_eff <= 0:
                return None, "SKIPPED_INACTIVE", "Не удалось оценить k для Ozon"
            est_storefront = (seller * k_eff).quantize(Decimal("0.01"))
            deadband_pct = self._OZON_RRP_DEADBAND_PCT
            source_note = "витрина по индексу"

        # Односторонняя зона: ниже РРЦ не опускаемся никогда (как на WB).
        # Терпим только превышение до +deadband_pct; ниже РРЦ — поднимаем.
        band = (rrp * deadband_pct / 100).quantize(Decimal("0.01"))
        low, high = rrp, rrp + band
        if low <= est_storefront <= high:
            return None, "NO_CHANGE", (
                f"Витрина Ozon {est_storefront}₽ ({source_note}) в коридоре РРЦ "
                f"({low}₽ … {high}₽, +{deadband_pct}%), k={k_eff}"
            )

        target_seller = Decimal(self._round_up(rrp / k_eff))

        # Правку меньше минимального шага не отправляем: на витрине она ничего
        # не изменит, а смена цены сбивает бустинг Ozon. Исключение — когда
        # витрина ниже РРЦ: РРЦ это пол, его дотягиваем на сколько нужно.
        if est_storefront > high:
            min_step = max(self._OZON_MIN_STEP_RUB, seller * self._OZON_MIN_STEP_RATIO)
            if abs(target_seller - seller) < min_step:
                return None, "NO_CHANGE", (
                    f"Витрина Ozon {est_storefront}₽ выше РРЦ {rrp}₽ на "
                    f"{est_storefront - rrp}₽, но правка цены продавца "
                    f"({seller}₽ → {target_seller}₽) меньше шага {min_step:.2f}₽"
                )

        # Ограничиваем шаг: если замер на прогон отстал от только что
        # отправленной цены, k перекошен — большой скачок недопустим.
        max_up = Decimal(self._round_up(seller * (1 + self._OZON_MAX_STEP_RATIO)))
        min_down = Decimal(self._round_up(seller * (1 - self._OZON_MAX_STEP_RATIO)))
        capped = target_seller
        if capped > max_up:
            capped = max_up
        elif capped < min_down:
            capped = min_down

        step_note = "" if capped == target_seller else f" (шаг ограничен с {target_seller}₽)"
        reason = (
            f"Ozon РРЦ замкнутый цикл: {source_note} {est_storefront}₽, цель {rrp}₽, "
            f"k={k_eff}, новая цена продавца {capped}₽{step_note}"
        )
        return capped, "CALCULATED", reason

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
        if is_ozon and not current_data.get('ozon_buyer_price'):
            # Витрину Ozon посчитать не удалось (нет ценовых индексов либо они
            # противоречат друг другу). Работаем по старой схеме, где РРЦ
            # понимается как цена продавца; снижение при этом заблокировано
            # флагом catalog_fallback.
            current_seller = current_data.get('seller_price') or Decimal(0)
            if abs(current_seller - rrp) <= self._TARGET_EPSILON_RUB:
                return None, "NO_CHANGE", f"Цена продавца уже равна РРЦ ({current_seller}₽)"
            return (
                rrp,
                "CALCULATED",
                f"РРЦ стратегия (витрина Ozon неизвестна): целевая цена продавца "
                f"{rrp}₽, текущая {current_seller}₽",
            )

        if is_ozon:
            # Ozon с известной витриной: замкнутый цикл по реальному k.
            return self._ozon_rrp_closed_loop(product, config, current_data, rrp)

        current_final = current_data['final_price']
        seller_discount = current_data.get('discount') or Decimal(0)

        # Кошелёк — механика WB; на Ozon её нет, даже если флаг включён.
        consider_wallet = bool(getattr(config, 'rrp_consider_wallet', False)) and not is_ozon

        if consider_wallet and wallet_pct > 0:
            # Эффективная цена с учётом кошелька WB
            wallet_factor = 1 - wallet_pct / 100
            current_effective = current_final * wallet_factor

            logger.info(
                f"RRP+кошелёк стратегия: текущая с кошельком={current_effective}₽, "
                f"цель={rrp}₽, кошелёк={wallet_pct}%"
            )

            corridor_top = self._rrp_corridor_top(config, rrp)
            if rrp <= current_effective <= corridor_top:
                return None, "NO_CHANGE", (
                    f"Цена с кошельком {current_effective:.2f}₽ в коридоре РРЦ "
                    f"({rrp}₽ … {corridor_top}₽)"
                )

            # Целевая финальная цена до применения кошелька, с запасом на округление WB
            target_final = self._rrp_floor_target(rrp / wallet_factor)

            try:
                new_seller_price = self._calculate_seller_price_from_final(target_final, spp, seller_discount)
                if current_effective > corridor_top:
                    new_seller_price = self._nudge_rrp_seller_down_if_safe(
                        new_seller_price,
                        current_data.get('seller_price') or Decimal(0),
                        current_final,
                        rrp,
                        wallet_factor,
                    )
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

            corridor_top = self._rrp_corridor_top(config, rrp)
            if rrp <= current_final <= corridor_top:
                return None, "NO_CHANGE", (
                    f"Цена {current_final}₽ в коридоре РРЦ ({rrp}₽ … {corridor_top}₽)"
                )

            target_final = self._rrp_floor_target(rrp)

            try:
                new_seller_price = self._calculate_seller_price_from_final(target_final, spp, seller_discount)
                if current_final > corridor_top:
                    new_seller_price = self._nudge_rrp_seller_down_if_safe(
                        new_seller_price,
                        current_data.get('seller_price') or Decimal(0),
                        current_final,
                        rrp,
                    )
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
        # иначе fallback на точечный запрос (например при вызове вне основного прогона).
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
        self._competitor_unavailable.discard(config.id)
        self._competitor_partial.discard(config.id)

        # Получаем данные по каждому конкуренту: (nm_id, final, in_stock, basic).
        # failed — конкуренты, цену которых узнать не удалось (сбой каталога).
        # Раньше они молча выпадали из выборки: при недогруженном самом дешёвом
        # конкуренте цель считалась от следующего, и цена уходила выше рынка.
        prices: List[Tuple[int, Decimal, bool, Decimal]] = []
        failed: List[int] = []
        for nm_id in nm_ids:
            try:
                p = await self._cached_parse_product(nm_id)
                if not p or getattr(p, "catalog_stale", False):
                    if self._catalog_missing(nm_id):
                        continue  # карточки нет в каталоге — как «нет в наличии»
                    p = await self._parse_product_single_fallback(nm_id)
                if p and not getattr(p, "catalog_stale", False):
                    if p.in_stock and not (p.final_price and p.final_price > 0):
                        # «В наличии» без цены — сбой ответа каталога, а не OOS
                        failed.append(nm_id)
                        continue
                    basic = getattr(p, 'seller_price', None) or Decimal(0)
                    prices.append((nm_id, p.final_price, p.in_stock, basic))
                elif not self._catalog_missing(nm_id):
                    failed.append(nm_id)
            except Exception as e:
                logger.warning(f"Не удалось получить цену конкурента nm_id={nm_id}: {e}")
                failed.append(nm_id)

        instock = [(nm, price, s, b) for nm, price, s, b in prices if s and price > 0]

        # Без полного списка нельзя утверждать, что все конкуренты без остатка,
        # а среднее по части списка может уйти в любую сторону.
        if failed and (not instock or rule == "avg_price"):
            logger.warning(
                "Конкурент для config_id=%s: каталог не отдал %d из %d конкурентов — цену не трогаем",
                config.id, len(failed), len(nm_ids),
            )
            self._competitor_unavailable.add(config.id)
            return None, None, None

        if not instock:
            return None, None, None

        if rule == "avg_price":
            avg_final = sum(p for _, p, _, _ in instock) / len(instock)
            avg_basic = sum(b for _, _, _, b in instock) / len(instock)
            return Decimal(str(avg_final)), None, Decimal(str(avg_basic))

        # min_price и cheapest_instock: самый дешёвый конкурент в наличии
        winner = min(instock, key=lambda x: x[1])
        if failed:
            # Реальный минимум не выше найденного: снижать к нему безопасно,
            # повышать — нет (см. _strategy_competitor).
            self._competitor_partial.add(config.id)
            logger.info(
                "Конкурент для config_id=%s: загружено %d из %d — только снижение",
                config.id, len(nm_ids) - len(failed), len(nm_ids),
            )
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
            product_id = getattr(product, "id", None)
            competitor_price, winning_nm_id, competitor_basic = await self._get_competitor_price(config)

            if competitor_price is None:
                if config.id in self._competitor_unavailable:
                    # Каталог недоступен — это не «конкурент без остатка»: не
                    # переключаемся на РРЦ из-за сетевой ошибки, цену не трогаем.
                    if product_id is not None:
                        cache_delete(self._competitor_guard_key(product_id, "oos"))
                    return None, "CATALOG_UNAVAILABLE", "Каталог WB не отдал цены конкурентов", None, None
                # Нет конкурентов или все OOS при правиле cheapest_instock
                from app.models.competitor_entry import CompetitorEntry
                entries = self.db.query(CompetitorEntry).filter(
                    CompetitorEntry.product_config_id == config.id
                ).all()
                if not entries and not config.competitor_nm_id:
                    if product_id is not None:
                        cache_delete(self._competitor_guard_key(product_id, "oos"))
                    return None, "SKIPPED_INACTIVE", "Конкурент не настроен", None, None
                if (
                    product_id is not None
                    and
                    not self._bypass_competitor_cooldown
                    and not self._confirm_competitor_observation(
                        product_id,
                        "oos",
                        {"config_id": config.id},
                    )
                ):
                    return None, "COMPETITOR_OOS_CONFIRM", (
                        "Все конкуренты без остатка: ожидаем повторное наблюдение "
                        "перед переходом на РРЦ"
                    ), None, None
                return None, "COMPETITOR_OOS", "Все конкуренты не в наличии", None, None

            if product_id is not None:
                cache_delete(self._competitor_guard_key(product_id, "oos"))

            spp = current_data.get('spp') or Decimal(0)
            seller_discount = current_data.get('discount') or Decimal(0)

            rule = config.competitor_select_rule or "min_price"
            logger.info(
                f"Competitor стратегия: правило={rule}, "
                f"цена конкурента={competitor_price}₽, winning_nm_id={winning_nm_id}"
            )

            target_final = self._competitor_target_final(config, competitor_price)

            # СПП для обратного расчёта — из согласованной пары цен каталога
            # (цена продавца и цена покупателя одной карточки и одного размера),
            # без округления. Раньше он считался как цена покупателя из каталога
            # к цене продавца из API: числа разного возраста. После смены цены
            # (1С, ручная правка) СПП выходил ложным (6.7% вместо 30%), и цена
            # покупателя уходила до −25% от цели. Без округления — иначе
            # «застывшая точка»: артикул 3891012, финальная 1588₽ при конкуренте
            # 1593₽, round_up(1593/0.66)=2414 == текущая цена → NO_CHANGE навечно.
            current_final = current_data.get('final_price') or Decimal(0)
            calc_spp = spp
            spp_exact = current_data.get('spp_exact')
            if spp_exact is not None:
                spp_exact = Decimal(str(spp_exact))
                if self._SPP_MIN <= spp_exact < Decimal(100):
                    calc_spp = spp_exact

            move = "lower" if current_final > target_final else "raise"
            if current_final > 0 and self._competitor_in_corridor(current_final, target_final):
                if product_id is not None:
                    cache_delete(self._competitor_guard_key(product_id, "adjust"))
                return None, "NO_CHANGE", (
                    f"Competitor ({rule}): финальная {current_final}₽ в коридоре цели "
                    f"{target_final:.2f}₽ "
                    f"(конкурент {competitor_price}₽)"
                ), competitor_price, winning_nm_id

            # Обратный расчет цены продавца через эффективный СПП. Цель
            # округляем вверх до рубля: WB округляет цену покупателя вниз, и
            # только для целой цели ceil(цель/коэф.) гарантирует факт ≥ цели.
            try:
                target_int = Decimal(self._round_up(target_final))
                new_seller_price = self._calculate_seller_price_from_final(target_int, calc_spp, seller_discount)

                current_seller = current_data.get('seller_price') or Decimal(0)
                if (
                    config.id in self._competitor_partial
                    and current_seller > 0
                    and new_seller_price > current_seller
                ):
                    return None, "COMPETITOR_PARTIAL", (
                        f"Competitor ({rule}): каталог отдал не всех конкурентов, найденный минимум "
                        f"{competitor_price}₽ — повышение до {new_seller_price}₽ отложено"
                    ), competitor_price, winning_nm_id

                # Если цена конкурента не менялась, отклонение могло возникнуть
                # из-за краткого скачка СПП. Первое наблюдение только запоминаем,
                # второе такое же разрешает переоценку. Реальное изменение цены
                # конкурента применяется сразу.
                same_competitor_as_last_snapshot = False
                try:
                    if product_id is None:
                        raise LookupError("product id is unavailable")
                    last_snapshot = self.db.query(PriceSnapshot).filter(
                        PriceSnapshot.product_id == product_id,
                        PriceSnapshot.competitor_price.isnot(None),
                    ).order_by(
                        PriceSnapshot.snapshot_at.desc(),
                        PriceSnapshot.id.desc(),
                    ).first()
                    same_competitor_as_last_snapshot = bool(
                        last_snapshot
                        and last_snapshot.competitor_price is not None
                        and Decimal(str(last_snapshot.competitor_price)) == competitor_price
                    )
                except Exception as exc:
                    logger.debug(
                        "Не удалось прочитать прошлую цену конкурента product_id=%s: %s",
                        product_id,
                        exc,
                    )

                if (
                    product_id is not None
                    and same_competitor_as_last_snapshot
                    and not self._bypass_competitor_cooldown
                ):
                    confirmed = self._confirm_competitor_observation(
                        product_id,
                        "adjust",
                        {
                            "competitor_price": competitor_price,
                            "winning_nm_id": winning_nm_id or 0,
                            "target_final": target_int,
                        },
                    )
                    if not confirmed:
                        return None, "COMPETITOR_CONFIRM", (
                            f"Цена конкурента прежняя ({competitor_price}₽), "
                            "отклонение нашей цены подтверждаем следующим прогоном"
                        ), competitor_price, winning_nm_id
                elif product_id is not None:
                    cache_delete(self._competitor_guard_key(product_id, "adjust"))

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
        # WB принимает цену в целых рублях. Дробный min/max (999.50, 1499.99)
        # раньше отбрасывал копейки при int() и перезаливался каждый прогон:
        # минимумы округляем вверх, максимумы вниз.
        new_price = Decimal(self._round_up(Decimal(str(new_price))))
        min_price = Decimal(self._round_up(Decimal(str(config.min_price)))) if config.min_price is not None else Decimal(0)
        max_price = (
            Decimal(str(config.max_price)).quantize(Decimal("1"), rounding=ROUND_FLOOR)
            if config.max_price else None
        )
        original_price = new_price
        reason_code = "UPDATED"

        # Проверка min_price (обязательно)
        if new_price < min_price:
            new_price = min_price
            reason_code = "CLAMPED_TO_MIN"
            logger.info(f"Цена ограничена минимумом: {original_price}₽ → {new_price}₽")

        # Проверка max_price (опционально)
        if max_price and new_price > max_price:
            new_price = max_price
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

        if max_price and new_price > max_price:
            # max_price — жёсткий потолок, даже над защитой маржи (осознанное
            # решение, см. тест test_margin_guard_result_clamped_to_max_price).
            log = logger.warning if reason_code == "CLAMPED_TO_MARGIN" else logger.info
            log(f"Цена ({new_price}₽) выше max_price ({max_price}₽) — ограничиваем, маржа может быть ниже минимальной")
            new_price = max_price
            reason_code = "CLAMPED_TO_MAX"

        return new_price, reason_code
    
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
            # Ozon: прямой замер витрины, чтобы по истории было видно, по чему
            # считался k — по «Цене на сайте» или по ценовому индексу.
            ozon_site_price=current_data.get('ozon_site_price'),
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
        # Цены WB одним пакетом на кабинет, а не GET на каждый товар:
        # десятки тысяч одиночных запросов съедали лимит основного прогона.
        await self._prefetch_missing_prices(products)

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

        # Карантин WB больше не блокирует товар на 24 часа: блокировка держала
        # цену ниже РРЦ до суток и прятала реальную цену (не писались снимки).
        # Об отказах WB «Price Quarantine» приходит уведомление в Telegram,
        # а снижение цены такого товара откладывается на 6 часов.

        if config.min_price is None:
            return {"product": product, "needs_update": False, "skip_code": "SKIPPED_INACTIVE",
                    "current_data": None, "new_price": None, "limit_reason": None, "reason_msg": None, "_t0": _t0}

        if self._marketplace(product.account) == "ozon" and config.strategy == "competitor":
            return {"product": product, "needs_update": False, "skip_code": "SKIPPED_INACTIVE",
                    "current_data": None, "new_price": None, "limit_reason": None,
                    "reason_msg": "Ozon competitor strategy is not supported yet", "_t0": _t0}

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
                await self._notify_token_expired_once(account)
                return {"product": product, "needs_update": False, "skip_code": "WB_API_ERROR",
                        "current_data": None, "new_price": None, "limit_reason": None, "reason_msg": None, "_t0": _t0}

            current_data = await self._get_current_price_data(product, account)
            if not current_data:
                return {"product": product, "needs_update": False, "skip_code": "PARSE_ERROR",
                        "current_data": None, "new_price": None, "limit_reason": None, "reason_msg": None, "_t0": _t0}
            # A competitor config may also have an RRP fallback. When our own
            # card is OOS, WB omits the buyer price, but we still need the last
            # trusted SPP so the price is ready for the stock returning.
            _rrp_wb = (
                marketplace == "wb"
                and (
                    config.strategy == "rrp"
                    or self._has_rrp_target(config, Decimal(0))
                )
            )
            if current_data.get("catalog_stale"):
                fallback = self._fallback_price_data(product) if _rrp_wb else None
                if not fallback:
                    return {"product": product, "needs_update": False, "skip_code": "CATALOG_UNAVAILABLE",
                            "current_data": None, "new_price": None, "limit_reason": None,
                            "reason_msg": "Каталог WB недоступен; stale СПП не используем", "_t0": _t0}
                logger.warning(
                    "nm_id=%s: каталог WB недоступен — РРЦ по последнему СПП %s%%, только повышение",
                    product.nm_id, fallback["spp"],
                )
                current_data = fallback
            elif marketplace == "wb":
                current_data = self._trusted_observation(product, current_data)

            # Ozon без остатка: доинвестирования нет, и «цена на сайте»
            # равна цене продавца. Если поверить такому k=1 и снизить цену
            # продавца до РРЦ, то после поставки Ozon включит скидку и
            # витрина окажется сильно ниже РРЦ. Пока остатка нет — не трогаем.
            if marketplace == "ozon" and current_data.get("total_quantity", 1) == 0:
                return {"product": product, "needs_update": False, "skip_code": "OUT_OF_STOCK",
                        "current_data": current_data, "new_price": None, "limit_reason": None,
                        "reason_msg": "Нет остатка на Ozon — цену не трогаем", "_t0": _t0}

            # Нет остатка: каталог не отдаёт цену покупателя, СПП неизвестен.
            # Раньше считалось, что СПП = 0, и цена продавца падала до РРЦ/0.97 —
            # после поставки покупатель видел −30% от РРЦ. Теперь для РРЦ берём
            # последний известный СПП и только повышаем, остальное не трогаем.
            if current_data.get("total_quantity", 1) == 0 and not (current_data.get("final_price") or 0) > 0:
                estimate = self._fallback_price_data(product, current_data) if _rrp_wb else None
                if not estimate:
                    return {"product": product, "needs_update": False, "skip_code": "OUT_OF_STOCK",
                            "current_data": current_data, "new_price": None, "limit_reason": None,
                            "reason_msg": "Нет остатка", "_t0": _t0}
                logger.info(
                    "nm_id=%s: нет остатка — РРЦ по последнему СПП %s%%, только повышение",
                    product.nm_id, estimate["spp"],
                )
                current_data = estimate

        if self._is_discounted_wb_promo(current_data, account):
            seller_discount = Decimal(str(current_data.get("discount") or 0))
            logger.info(
                "Пропускаем nm_id=%s: товар в акции WB, скидка продавца %s%% > %s%%",
                product.nm_id,
                seller_discount,
                self._PROMO_DISCOUNT_SKIP_THRESHOLD,
            )
            return {"product": product, "needs_update": False, "skip_code": "SKIPPED_PROMO",
                    "current_data": current_data, "new_price": None, "limit_reason": None,
                    "reason_msg": f"Товар в акции WB со скидкой продавца {seller_discount}%",
                    "_t0": _t0}

        wallet_pct = (self._user_settings or {}).get("wb_wallet_discount_pct", 0) if marketplace == "wb" else 0
        try:
            wallet_pct = Decimal(str(wallet_pct))
        except Exception:
            wallet_pct = Decimal(0)

        snap_competitor_price = None
        snap_competitor_nm_id = None
        effective_strategy = config.strategy
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
                effective_strategy = "rrp"

        # Даже если стратегия не требует изменений — проверяем маржу по текущей цене.
        # Если нарушена — _apply_limits поднимет цену. Если нет — остаётся код
        # стратегии (CATALOG_UNAVAILABLE, COMPETITOR_OOS, PARSE_ERROR…): раньше
        # он подменялся на NO_CHANGE, и сбои каталога/расчёта нигде не были видны.
        _seller_discount = current_data.get('discount') or Decimal(0)
        strategy_skip_code = None
        if new_price is None:
            strategy_skip_code = reason_code
            _curr_sp = current_data.get('seller_price') or Decimal(0)
            if _curr_sp > 0:
                new_price = _curr_sp
            else:
                return {"product": product, "needs_update": False, "skip_code": reason_code,
                        "current_data": current_data, "new_price": None, "limit_reason": None,
                        "reason_msg": reason_msg, "_t0": _t0,
                        "snap_competitor_price": snap_competitor_price, "snap_competitor_nm_id": snap_competitor_nm_id,
                        "effective_strategy": effective_strategy}

        # Ночная цена (night_price_enabled): для РРЦ не ниже цены, нужной для РРЦ.
        _night_price, _night_code, _night_msg = self._apply_night_boost(config, new_price)
        if _night_code:
            _night_price = Decimal(str(_night_price))
            new_price = max(new_price, _night_price) if effective_strategy == "rrp" else _night_price
            reason_msg = f"{reason_msg}. {_night_msg}" if reason_msg else _night_msg

        final_new_price, limit_reason = self._apply_limits(new_price, config, seller_discount=_seller_discount)

        # max_price не может опустить РРЦ-товар ниже РРЦ: пол РРЦ приоритетнее.
        if effective_strategy == "rrp" and limit_reason == "CLAMPED_TO_MAX" and final_new_price < new_price:
            logger.warning(
                "nm_id=%s: max_price %s ниже цены для РРЦ %s — max_price игнорируется",
                product.nm_id, config.max_price, new_price,
            )
            reason_msg = (reason_msg or "") + f". max_price {config.max_price}₽ ниже цены для РРЦ — не применён"
            final_new_price = Decimal(self._round_up(Decimal(str(new_price))))
            limit_reason = "UPDATED"

        _curr_sp_for_step = current_data.get("seller_price") or Decimal(0)
        _lowering = _curr_sp_for_step > 0 and final_new_price < _curr_sp_for_step

        # Снижать нельзя, если данные сомнительные или WB/защита только что
        # сопротивлялись снижению. Повышать можно всегда.
        _hold_reason = None
        if _lowering:
            # В режиме ozon_seller_mode витрина неизвестна, и РРЦ понимается
            # как цена продавца — цель точная, задерживать снижение незачем.
            _uncertain = (
                current_data.get("catalog_lag")
                or (current_data.get("catalog_fallback")
                    and not current_data.get("ozon_seller_mode"))
            )
            if _uncertain:
                _hold_reason = "Каталог WB не обновился / цена покупателя оценочная — снижение отложено"
            elif guard_active(f"wb_quarantine_hold:{product.nm_id}"):
                _hold_reason = "WB недавно отправил цену в карантин — снижение отложено"
            elif guard_active(f"spp_guard_cooldown:{product.id}"):
                _hold_reason = "Недавно был откат SPP Guard — снижение отложено"
        if _hold_reason:
            return {"product": product, "needs_update": False, "skip_code": "NO_CHANGE",
                    "current_data": current_data, "new_price": _curr_sp_for_step,
                    "limit_reason": limit_reason,
                    "reason_msg": f"{reason_msg}. {_hold_reason}" if reason_msg else _hold_reason,
                    "_t0": _t0,
                    "snap_competitor_price": snap_competitor_price, "snap_competitor_nm_id": snap_competitor_nm_id,
                    "effective_strategy": effective_strategy}

        # Защита от карантина WB: снижаем не больше чем на 20% за прогон
        # (WB отправлял в карантин уже снижения на 20-45%).
        if marketplace == "wb" and _lowering and final_new_price < _curr_sp_for_step * self._MAX_DROP_RATIO:
            stepped = Decimal(self._round_up(_curr_sp_for_step * self._MAX_DROP_RATIO))
            logger.info(
                "Ступенчатое снижение для nm_id=%s: целевая %s < %s%% текущей %s, отправляем %s",
                product.nm_id, final_new_price, int(self._MAX_DROP_RATIO * 100), _curr_sp_for_step, stepped,
            )
            reason_msg = (reason_msg or "") + f". Ступенчатое снижение: отправляем {stepped} (целевая {final_new_price}, текущая {_curr_sp_for_step})"
            final_new_price = stepped

        _MAX_RAISE_RATIO = self._RRP_MAX_RAISE_RATIO if effective_strategy == "rrp" else self._COMPETITOR_MAX_RAISE_RATIO
        if marketplace == "wb" and _curr_sp_for_step > 0 and final_new_price > _curr_sp_for_step * _MAX_RAISE_RATIO:
            stepped_up = Decimal(self._round_up(_curr_sp_for_step * _MAX_RAISE_RATIO))
            logger.info(
                "Ступенчатое повышение для nm_id=%s: целевая %s > x%s текущей %s, отправляем %s",
                product.nm_id, final_new_price, _MAX_RAISE_RATIO, _curr_sp_for_step, stepped_up,
            )
            reason_msg = (reason_msg or "") + f". Ступенчатое повышение: отправляем {stepped_up} (целевая {final_new_price}, текущая {_curr_sp_for_step})"
            final_new_price = stepped_up

        if final_new_price == current_data["seller_price"]:
            # Preserve the real limiting reason when the desired strategy price
            # is below a floor that exactly equals the current seller price.
            # Reporting NO_CHANGE here hid thousands of margin-constrained cards
            # from operational audits.
            _effective_skip = strategy_skip_code or "NO_CHANGE"
            _limit_skip_codes = {
                "CLAMPED_TO_MARGIN": "LIMITED_BY_MARGIN",
                "CLAMPED_TO_MIN": "LIMITED_BY_MIN",
                "CLAMPED_TO_MAX": "LIMITED_BY_MAX",
            }
            if limit_reason in _limit_skip_codes:
                _effective_skip = _limit_skip_codes[limit_reason]
            return {"product": product, "needs_update": False, "skip_code": _effective_skip,
                    "current_data": current_data, "new_price": final_new_price,
                    "limit_reason": limit_reason, "reason_msg": reason_msg, "_t0": _t0,
                    "snap_competitor_price": snap_competitor_price, "snap_competitor_nm_id": snap_competitor_nm_id,
                    "effective_strategy": effective_strategy}

        return {"product": product, "needs_update": True, "skip_code": None,
                "current_data": current_data, "new_price": final_new_price,
                "limit_reason": limit_reason, "reason_msg": reason_msg, "_t0": _t0,
                "snap_competitor_price": snap_competitor_price, "snap_competitor_nm_id": snap_competitor_nm_id,
                "effective_strategy": effective_strategy}

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

        if self._dry_run:
            # Пробный прогон: фиктивные снимки и записи UPDATED раньше попадали в
            # UI, включали 4-часовой кулдаун competitor и сбивали аналитику.
            if not dec["needs_update"]:
                return dec["skip_code"] or "SKIPPED_INACTIVE"
            if failed:
                return "WB_API_ERROR"
            return limit_reason if limit_reason and limit_reason != "UPDATED" else "UPDATED"
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
                if reason_code not in _silent and self._should_log_skip(product.id, reason_code):
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

        # success: расчётная цена покупателя = цена × (1 − скидка продавца) × (1 − СПП)
        spp = current_data.get("spp")
        if spp is not None and 0 <= spp < 100:
            new_final = self._discounted(new_price, current_data.get("discount")) * (1 - spp / 100)
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

    def _should_log_skip(self, product_id: int, reason_code: str) -> bool:
        """Писать ли повторяющийся пропуск в историю (дедуп на 6 часов)."""
        if reason_code not in self._DEDUP_SKIP_CODES:
            return True
        key = f"skip_logged:{product_id}:{reason_code}"
        if cache_get(key):
            return False
        cache_set(key, {"ts": time.time()}, ttl=self._SKIP_LOG_DEDUP_SEC)
        return True

    async def _verify_and_correct_competitor(self, run, items: list) -> int:
        """Closed-loop PHASE 4: перепарсивает факт у ВСЕХ обновлённых товаров (RRP + Competitor).

        Задачи:
        1. ВСЕГДА обновляет snapshot РЕАЛЬНОЙ final_price (а не предсказанной) —
           иначе UI показывает фантомную цену СПП.
        2. SPP GUARD: если фактическая цена просела НИЖЕ РРЦ (с учетом кошелька)
           или НИЖЕ конкурента — немедленно откатывает цену назад (REVERTED_SPP_SPIKE)
           и устанавливает защитный кулдаун на 1 час.
        3. Для competitor: если факт ушёл от цели (но не просел в демпинг) — доправляет.
        """
        if not items or not self._CLOSED_LOOP_ENABLED:
            return 0
        logger.info("Closed-loop: %d обновлённых товаров, пауза %dс на пересчёт СПП",
                    len(items), self._CLOSED_LOOP_SETTLE_SEC)
        await asyncio.sleep(self._CLOSED_LOOP_SETTLE_SEC)

        by_nm = {it["nm_id"]: it for it in items}
        try:
            fresh = await catalog_parser.parse_batch_ids(
                list(by_nm.keys()), deadline_sec=self._CLOSED_LOOP_PARSE_BUDGET_SEC,
            )
        except Exception as e:
            logger.error("Closed-loop: батч-парсинг факта упал: %s", e)
            return 0

        corrective: dict = {}
        plan: dict = {}
        snapshots_updated = 0
        reverts_count = 0
        corrections_count = 0

        wallet_pct = Decimal(str((self._user_settings or {}).get("wb_wallet_discount_pct", "3.0")))

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

            strategy = it.get("strategy") or getattr(it["config"], "strategy", None)
            cd = it.get("current_data") or {}
            discount = Decimal(str(cd.get("discount") or 0))

            # Каталог ещё не пересчитал цену покупателя после заливки: цена продавца
            # новая, а цена покупателя та же, что до изменения. Такой «факт» дал бы
            # ложный СПП и ложный откат (так цена разгонялась до 10-17 тыс.).
            _pre_final = cd.get("catalog_final_raw", cd.get("final_price"))
            _old_seller = Decimal(str(it.get("old_seller_price") or 0))
            if (
                _pre_final is not None
                and _old_seller > 0
                and abs(new_seller - _old_seller) / _old_seller > self._CATALOG_LAG_PRICE_DELTA
                and actual_final == Decimal(str(_pre_final))
            ):
                logger.info(
                    "Closed-loop nm=%s: каталог ещё не пересчитал цену покупателя (%s₽) — проверку пропускаем",
                    nm, actual_final,
                )
                continue

            # Чистый СПП факта (без скидки продавца) — для снимка и ступеней РРЦ
            base_after = self._discounted(new_seller, discount)
            fact_spp = (
                ((1 - actual_final / base_after) * 100).quantize(Decimal("0.01"))
                if base_after > 0 else None
            )

            # Обновляем snapshot РЕАЛЬНОЙ финальной ценой и СПП
            snap = dict(cd)
            snap["seller_price"] = new_seller
            snap["final_price"] = actual_final
            if fact_spp is not None:
                snap["spp"] = fact_spp
            try:
                self._save_price_snapshot(it["product"], snap,
                                          competitor_nm_id=it.get("competitor_nm_id"),
                                          competitor_price=it.get("competitor_price"))
                snapshots_updated += 1
            except Exception as e:
                logger.error("Closed-loop: ошибка обновления snapshot для nm=%s: %s", nm, e)

            ratio = actual_final / new_seller
            if ratio <= 0:
                continue
            spp_str = f"{fact_spp}%" if fact_spp is not None else "?"

            # Ограничения шага считаем от цены ДО прогона. Основной расчёт уже
            # сделал шаг от неё, а доправка от new_seller удваивала его:
            # −20% и ещё −20% = −36% за минуту, ×1.25 × 1.6 = ×2 при повышении.
            base_seller = _old_seller if _old_seller > 0 else new_seller
            max_raise = (
                self._RRP_MAX_RAISE_RATIO if strategy == "rrp"
                else self._COMPETITOR_MAX_RAISE_RATIO
            )
            step_floor = Decimal(self._round_up(base_seller * self._MAX_DROP_RATIO))
            step_ceil = Decimal(self._round_up(base_seller * max_raise))

            def _limited(price: Decimal) -> Optional[Decimal]:
                """Цена после min/max/маржи и шага; None — если данные выглядят битыми."""
                if price > new_seller * self._CLOSED_LOOP_MAX_RATIO:
                    logger.warning(
                        "Closed-loop: аномальная цена nm=%s (seller=%s→%s, ratio=%.4f) — пропускаем",
                        nm, new_seller, price, ratio,
                    )
                    return None
                try:
                    price, _lr = self._apply_limits(price, it["config"], seller_discount=discount)
                except Exception as e:
                    logger.error("Closed-loop: _apply_limits упал для nm=%s: %s — пропускаем", nm, e)
                    return None
                return min(max(price, step_floor), step_ceil)

            # -------------------------------------------------------------
            # 1. ЗАЩИТА РРЦ (Стратегия RRP): проверка цены с кошельком vs РРЦ
            # -------------------------------------------------------------
            if strategy == 'rrp':
                seller_direct, rrp = self._resolve_rrp(it["config"], fact_spp if fact_spp is not None else Decimal(0))
                if seller_direct is not None or not rrp or rrp <= 0:
                    continue
                rrp = Decimal(str(rrp))
                consider_wallet = getattr(it["config"], "rrp_consider_wallet", False)
                wallet_factor = Decimal("1") - wallet_pct / Decimal("100") if (consider_wallet and wallet_pct > 0) else Decimal("1")
                actual_effective = actual_final * wallet_factor
                target_final = self._rrp_floor_target(rrp / wallet_factor)

                # Проверка нарушения: цена с кошельком ниже РРЦ
                if actual_effective < (rrp - self._TARGET_EPSILON_RUB):
                    # Минимальная безопасная цена продавца при фактическом СПП.
                    # Раньше брался max(старая, безопасная) — после снижения это
                    # возвращало заведомо завышенную старую цену.
                    revert_price = _limited(Decimal(self._round_up(target_final / ratio)))
                    if revert_price is not None and revert_price != new_seller:
                        corrective.setdefault(it["token"], []).append({"nmID": nm, "price": int(revert_price)})
                        msg = (
                            f"SPP Guard: факт с кошельком {actual_effective:.1f}₽ < РРЦ {rrp}₽ (СПП {spp_str}). "
                            f"Откат цены продавца {new_seller}₽ → {revert_price}₽"
                        )
                        plan[nm] = (it, revert_price, target_final, actual_final, ratio, "REVERTED_SPP_SPIKE", msg)
                        reverts_count += 1
                        logger.warning("SPP Guard REVERT (RRP) for nm=%s: %s", nm, msg)
                continue

            # -------------------------------------------------------------
            # 2. СТРАТЕГИЯ КОНКУРЕНТ (Competitor): проверка vs конкурента
            # -------------------------------------------------------------
            comp = it.get("competitor_price")
            if not comp:
                continue
            comp = Decimal(str(comp))
            target_final = self._competitor_target_final(it["config"], comp)
            target_int = Decimal(self._round_up(target_final))
            tolerance = self._competitor_tolerance(target_final)

            # Проверка нарушения: цена заметно ниже цели (WB поднял СПП)
            if actual_final < target_int - tolerance:
                revert_price = _limited(Decimal(self._round_up(target_int / ratio)))
                if revert_price is not None and it.get("competitor_partial") and base_seller >= new_seller:
                    # Цель посчитана не по всем конкурентам: реальный минимум
                    # может быть ниже. Откатываем не выше цены до прогона.
                    revert_price = min(revert_price, base_seller)
                if revert_price is not None and revert_price != new_seller:
                    corrective.setdefault(it["token"], []).append({"nmID": nm, "price": int(revert_price)})
                    msg = (
                        f"SPP Guard: факт {actual_final:.1f}₽ < цели {target_final:.1f}₽ (СПП {spp_str}). "
                        f"Откат цены продавца {new_seller}₽ → {revert_price}₽"
                    )
                    plan[nm] = (it, revert_price, target_final, actual_final, ratio, "REVERTED_SPP_SPIKE", msg)
                    reverts_count += 1
                    logger.warning("SPP Guard REVERT (Competitor) for nm=%s: %s", nm, msg)
            elif actual_final > target_int + tolerance:
                # Обычная Closed-loop корректировка (цена выше цели). Это
                # снижение: те же удержания, что и в основном расчёте.
                hold = None
                if cd.get("catalog_lag") or cd.get("catalog_fallback"):
                    hold = "данные каталога сомнительные"
                elif guard_active(f"wb_quarantine_hold:{nm}"):
                    hold = "WB недавно отправил цену в карантин"
                elif guard_active(f"spp_guard_cooldown:{it['product'].id}"):
                    hold = "недавно был откат SPP Guard"
                if hold:
                    logger.info("Closed-loop nm=%s: доправка вниз отложена — %s", nm, hold)
                    continue
                corrected = _limited(Decimal(self._round_up(target_int / ratio)))
                if corrected is not None and corrected != new_seller:
                    corrective.setdefault(it["token"], []).append({"nmID": nm, "price": int(corrected)})
                    msg = (
                        f"Closed-loop: факт {actual_final}₽ != цели {target_final:.2f}₽ → "
                        f"seller {new_seller}→{corrected}"
                    )
                    plan[nm] = (it, corrected, target_final, actual_final, ratio, "CORRECTED", msg)
                    corrections_count += 1

        if not corrective:
            logger.info("Closed-loop: snapshots обновлено=%d, коррекция/откат не требуются", snapshots_updated)
            try:
                self.db.commit()
            except Exception as e:
                logger.error("Closed-loop: commit упал: %s", e)
                self.db.rollback()
            return 0

        # Доправки проверяем так же, как основную заливку: раньше отказ WB
        # (карантин) не замечался, а в историю писалась непринятая цена.
        applied = set()
        rejected: Dict[int, str] = {}
        for token, updates in corrective.items():
            try:
                async with create_wb_client(token) as client:
                    statuses, verified = await self._wb_upload_verified(client, updates)
            except Exception as e:
                logger.error("Closed-loop: ошибка отправки пакета в WB: %s", e)
                for u in updates:
                    rejected[u["nmID"]] = f"Исключение при отправке: {e}"
                continue
            for u in updates:
                nm = u["nmID"]
                st = statuses.get(nm) if verified else None
                if st is not None and not st[0]:
                    err_text = st[1] or "WB отклонил цену"
                    rejected[nm] = err_text
                    logger.warning("Closed-loop nm=%s: WB не применил цену %s: %s", nm, u["price"], err_text)
                    if "quarantine" in err_text.lower():
                        product = by_nm[nm]["product"]
                        account_name = getattr(getattr(product, "account", None), "name", "") or ""
                        self._quarantine_hits.append((account_name, product.seller_sku or str(nm), nm))
                        cache_set(f"wb_quarantine_hold:{nm}", {"run_id": run.id}, ttl=self._QUARANTINE_HOLD_SEC)
                    continue
                if st is None:
                    logger.warning("Closed-loop nm=%s: статус WB не подтверждён", nm)
                applied.add(nm)
                cache_delete(f"wb_catalog:{nm}")

        # Кулдаун SPP Guard — только для реально применённых откатов
        for nm, entry in plan.items():
            if nm in applied and entry[5] == "REVERTED_SPP_SPIKE":
                cache_set(f"spp_guard_cooldown:{entry[0]['product'].id}", {"run_id": run.id},
                          ttl=self._SPP_GUARD_COOLDOWN_SEC)

        verified_after_correction: dict = {}
        if applied:
            try:
                if self._CLOSED_LOOP_CORRECTION_SETTLE_SEC > 0:
                    await asyncio.sleep(self._CLOSED_LOOP_CORRECTION_SETTLE_SEC)
                verified_after_correction = await catalog_parser.parse_batch_ids(
                    list(applied), deadline_sec=self._CLOSED_LOOP_PARSE_BUDGET_SEC,
                )
            except Exception as e:
                logger.error("Closed-loop: повторный парсинг после коррекции упал: %s", e)

        n = 0
        for nm, (it, corrected, target_final, actual_final, ratio, reason_code, reason_msg) in plan.items():
            product = it["product"]
            base = dict(it["current_data"])
            base["seller_price"] = Decimal(str(it["new_price"]))
            base["final_price"] = actual_final
            if nm in rejected:
                # Цена не применена: в истории — отказ, снимок не трогаем
                try:
                    self._save_price_change(
                        run, product, base, corrected, None, "WB_API_ERROR",
                        f"{reason_msg}. Отказ WB: {rejected[nm]}",
                    )
                    self._reason_counts["WB_API_ERROR"] += 1
                except Exception as e:
                    logger.error("Closed-loop: ошибка сохранения отказа для nm=%s: %s", nm, e)
                continue
            if nm not in applied:
                continue
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
                    run, product, base, corrected, corrected_final, reason_code,
                    reason_msg,
                )
                self._reason_counts[reason_code] += 1
                n += 1
            except Exception as e:
                logger.error("Closed-loop: ошибка сохранения для nm=%s: %s", nm, e)
        try:
            self.db.commit()
        except Exception as e:
            logger.error("Closed-loop: commit упал: %s", e)
            self.db.rollback()
        logger.info(
            "Closed-loop / SPP Guard: snapshots=%d, откатов (SPP Guard)=%d, доправок (Closed-loop)=%d, "
            "применено=%d, отклонено WB=%d",
            snapshots_updated, reverts_count, corrections_count, n, len(rejected),
        )
        return n

    async def _wb_upload_verified(self, client, wb_updates: List[dict]) -> Tuple[Dict[int, Tuple[bool, str]], bool]:
        """Отправляет цены в WB и читает статус каждого товара.

        Возвращает (statuses, verified). statuses: nm_id -> (принято, текст ошибки);
        товара нет в statuses — WB статус не подтвердил. verified=False — проверить
        было нечем (WB ответил «задача уже существует» без uploadID).
        """
        resp = await client.update_prices(wb_updates)
        upload_id = (resp.get("data") or {}).get("id")
        upload_ids = resp.get("_upload_ids") or ([upload_id] if upload_id else [])
        if not upload_ids:
            return {}, False
        # Каждый uploadID проверяем со своим списком товаров
        # (раньше при >1000 проверялся только последний чанк).
        nm_chunks = [
            [u["nmID"] for u in wb_updates[i:i + 1000]]
            for i in range(0, len(wb_updates), 1000)
        ]
        statuses: Dict[int, Tuple[bool, str]] = {}
        for idx, uid in enumerate(upload_ids):
            expected = nm_chunks[idx] if len(upload_ids) == len(nm_chunks) else None
            statuses.update(await client.verify_batch_update(uid, expected_nm_ids=expected))
        logger.info(
            "Batch update: %d цен отправлено (uploadID=%s, статусов=%d)",
            len(wb_updates), upload_ids, len(statuses),
        )
        return statuses, True

    async def _notify_token_expired_once(self, account) -> None:
        """Одно уведомление об истёкшем токене на кабинет за 6 часов (раньше — на каждый товар)."""
        key = f"wb_token_notified:{account.id}"
        if cache_get(key):
            return
        cache_set(key, {"ts": time.time()}, ttl=self._TOKEN_NOTIFY_DEDUP_SEC)
        try:
            await telegram_notifier.notify_token_expired(account.name, account.id)
        except Exception as e:
            logger.error("Уведомление об истёкшем токене не отправлено: %s", e)

    async def _handle_wb_auth_failure(self, account, error) -> bool:
        """401 от WB. Кабинет выключаем только после нескольких отказов подряд.

        Один случайный 401 (сбой авторизации WB) раньше навсегда выключал
        кабинет, и ~22 тыс. товаров молча переставали переоцениваться.
        Возвращает True, если кабинет выключен.
        """
        key = f"wb_auth_failures:{account.id}"
        state = cache_get(key) or {"count": 0}
        count = int(state.get("count", 0)) + 1
        cache_set(key, {"count": count, "last": time.time()}, ttl=self._WB_AUTH_FAIL_WINDOW_SEC)
        if count < self._WB_AUTH_FAILS_TO_DEACTIVATE:
            logger.error(
                "Аккаунт '%s' (id=%s): WB вернул 401 (%d из %d) — кабинет пока не выключаем: %s",
                account.name, account.id, count, self._WB_AUTH_FAILS_TO_DEACTIVATE, error,
            )
            await self._notify_token_expired_once(account)
            return False
        logger.error("Аккаунт '%s' (id=%s): %d ответов 401 подряд — деактивируем", account.name, account.id, count)
        account.is_active = False
        cache_delete(key)
        await self._notify_token_expired_once(account)
        return True

    async def _notify_quarantine(self, run_id: int) -> None:
        """Сводка в Telegram по товарам, которые WB отправил в карантин цен.

        Один и тот же товар попадает в уведомление не чаще раза в 6 часов,
        чтобы застрявшие в карантине товары не слали сообщение каждые 30 минут.
        """
        fresh: List[Tuple[str, str, int]] = []
        seen: set = set()
        for account_name, sku, nm in self._quarantine_hits:
            if nm in seen:
                continue
            seen.add(nm)
            key = f"wb_quarantine_notified:{nm}"
            if cache_get(key):
                continue
            cache_set(key, {"run_id": run_id}, ttl=self._QUARANTINE_NOTIFY_DEDUP_SEC)
            fresh.append((account_name, sku, nm))
        if not fresh:
            return

        by_account: Dict[str, List[str]] = {}
        for account_name, sku, _nm in fresh:
            by_account.setdefault(account_name, []).append(sku)
        lines = [f"⚠️ <b>Карантин цен WB: {len(fresh)} товаров</b>", ""]
        for account_name, skus in sorted(by_account.items()):
            codes = ", ".join(sorted(set(skus)))
            lines.append(f"<b>{account_name}</b> — {len(skus)}: {codes}")
        lines += ["", f"WB не применил новые цены (прогон #{run_id}). Уберите товары из карантина в кабинете WB."]
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:3990] + "…"
        await telegram_notifier.send_message(text)

    def _update_stage(self, run, stage_name: str, stage_idx: int, total_stages: int = 5, details: str = ""):
        """Сохраняет текущий этап выполнения прогона в БД для UI"""
        try:
            stages = [
                {"id": 1, "name": "Проверка каталога WB", "key": "preflight"},
                {"id": 2, "name": "Базовые цены WB API", "key": "wb_prices"},
                {"id": 3, "name": "Парсинг цен в каталоге", "key": "catalog"},
                {"id": 4, "name": "Расчет и отправка цен", "key": "pricing_upload"},
                {"id": 5, "name": "Контроль применения (Closed-loop)", "key": "closed_loop"},
            ]
            for s in stages:
                if s["id"] < stage_idx:
                    s["status"] = "completed"
                elif s["id"] == stage_idx:
                    s["status"] = "running"
                    s["details"] = details
                else:
                    s["status"] = "pending"
            
            if stage_idx > total_stages:
                run.current_stage = stage_name
            else:
                run.current_stage = f"[{stage_idx}/{total_stages}] {stage_name}"
            run.stage_progress = {
                "current_stage": stage_name,
                "stage_idx": min(stage_idx, total_stages),
                "total_stages": total_stages,
                "details": details,
                "stages": stages,
                "reason_counts": dict(self._reason_counts),
            }
            self.db.commit()
        except Exception as e:
            logger.warning("Не удалось обновить этап в БД: %s", e)

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
        self._dry_run = dry_run
        logger.info(f"Запуск репрайсера (тип: {run_type}, dry_run={dry_run})")

        # Предзагружаем настройки пользователя один раз (избегаем N+1 запросов в цикле)
        self._user_settings = self._load_user_settings()

        # До выборки активных товаров сверяем скидку продавца в Prices API.
        # Карточки с discount>0 выключаются только в своём кабинете; карточки,
        # ранее выключенные этим механизмом, возвращаются после двух нулевых
        # проверок. Неполный ответ WB прерывает прогон до любой смены цены.
        if not dry_run and self.user_id is not None:
            from app.services.promo_pause import sync_promo_pauses

            promo_sync = await sync_promo_pauses(self.db, self.user_id)
            self._prefetched_prices.update(promo_sync.prices)
            logger.info(
                "Promo protection before repricing: checked=%d paused=%d resumed=%d",
                promo_sync.checked,
                len(promo_sync.paused_product_ids),
                len(promo_sync.resumed_product_ids),
            )

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
                # Время старта — момент запуска, а не постановки в очередь: иначе
                # ожидание в очереди засчитывалось в порог зависания (185 мин),
                # и живой прогон помечался failed. Сообщение «Waiting for…»
                # из очереди больше не актуально.
                run.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
                run.error_message = None
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

        owned_product_lock_ids: set[int] = set()
        try:
            # Запись о прогоне создаётся до загрузки каталога: иначе ручной запуск
            # видел «лок есть, running-прогона нет» и снимал лок живого прогона.
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
                cold_ids = (
                    list(competitor_nm_ids)
                    if self._force_catalog_refresh
                    else [
                        nm_id for nm_id in competitor_nm_ids
                        if self._catalog_needs_refresh(nm_id)
                    ]
                )
                if cold_ids:
                    refresh_kind = "принудительно свежих" if self._force_catalog_refresh else "холодных"
                    logger.info(
                        "Pre-fetch конкурентов: %d %s из %d уникальных",
                        len(cold_ids), refresh_kind, len(competitor_nm_ids),
                    )
                    competitor_not_found: set = set()
                    batch_results = await catalog_parser.parse_batch_ids(
                        cold_ids,
                        deadline_sec=self._CATALOG_PREFETCH_BUDGET_SEC,
                        not_found=competitor_not_found,
                    )
                    for nm_id, product_data in batch_results.items():
                        if product_data is not None:
                            self._catalog_cache_put(nm_id, product_data)
                    self._mark_catalog_missing(competitor_not_found)
                    logger.info(f"Pre-fetch завершён: {sum(1 for v in batch_results.values() if v)} / {len(cold_ids)}")

                # Глобальный pre-fetch ВСЕХ собственных товаров одним батчем.
                # Убирает тысячи одиночных HTTP-запросов из основного цикла.
                all_own_nm_ids = list({r[0] for r in self.db.execute(
                    select(Product.nm_id).filter(Product.id.in_(all_product_ids))
                ).all()})
                own_cold = (
                    all_own_nm_ids
                    if self._force_catalog_refresh
                    else [
                        nm for nm in all_own_nm_ids
                        if self._catalog_needs_refresh(nm)
                    ]
                )
                if own_cold:
                    refresh_kind = "принудительно свежих" if self._force_catalog_refresh else "холодных"
                    logger.info(
                        "Глобальный pre-fetch: %d %s из %d собственных товаров",
                        len(own_cold), refresh_kind, len(all_own_nm_ids),
                    )
                    own_results = await catalog_parser.parse_batch_ids(
                        own_cold, deadline_sec=self._CATALOG_PREFETCH_BUDGET_SEC,
                    )
                    cached_count = 0
                    for nm_id, product_data in own_results.items():
                        if product_data is not None:
                            self._catalog_cache_put(nm_id, product_data)
                            cached_count += 1
                    if cached_count == 0 and len(own_cold) > 10:
                        logger.warning("Глобальный pre-fetch: 0 успехов, повтор через 10 сек...")
                        await asyncio.sleep(10)
                        own_results = await catalog_parser.parse_batch_ids(
                            own_cold, deadline_sec=self._CATALOG_PREFETCH_BUDGET_SEC,
                        )
                        for nm_id, product_data in own_results.items():
                            if product_data is not None:
                                self._catalog_cache_put(nm_id, product_data)
                                cached_count += 1
                    logger.info(f"Глобальный pre-fetch завершён: {cached_count}/{len(own_cold)} закешировано")

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
            results = {'updated': 0, 'skipped': 0, 'errors': 0, 'catalog_unavailable': 0, 'out_of_stock': 0}
            CHUNK_SIZE  = 1000  # вдвое меньше чанков → меньше pre-fetch раундов
            COMMIT_EVERY = 200  # товаров между коммитами (меньше DB round-trips)

            def _commit_run_progress() -> None:
                # Живой прогресс в БД: UI и cleanup-логика видят, что run реально двигается,
                # даже если текущий чанк тянется долго из-за 429/backoff WB API.
                run.updated_count = results['updated']
                run.skipped_count = results['skipped']
                run.error_count = results['errors']
                self.db.commit()

            # Pre-flight: проверяем доступность каталога WB (SSH-туннель)
            if not dry_run:
                self._update_stage(run, "Проверка каталога WB", 1, 5, "Шлюз Angie WAF активен")
                await self._preflight_catalog_check(sample_nm_ids=all_own_nm_ids[:3])

            # Загружаем цены: быстрый точечный POST-запрос только по переоцениваемым товарам
            if not dry_run:
                self._update_stage(run, "Базовые цены WB API", 2, 5, f"Точечный запрос цен для {total_count} товаров")
                logger.info("Быстрый точечный pre-fetch цен для %d целевых товаров", total_count)
                # Цены для чанков загрузятся автоматически батчами через get_prices_by_articles

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

                # Product locks allow a small priority/manual run to execute while
                # the user's full run is active without both workers writing the
                # same WB card at the same time.  Redis pipelining keeps this to
                # one round-trip per 1000-product chunk.
                chunk_product_ids = [product.id for product in chunk_products]
                if dry_run:
                    acquired_chunk_locks = set(chunk_product_ids)
                else:
                    acquired_chunk_locks = acquire_product_locks(
                        self.user_id or 0,
                        chunk_product_ids,
                        self._product_lock_owner,
                    )
                    if acquired_chunk_locks is None:
                        raise RuntimeError("Redis product locking unavailable")
                if not dry_run:
                    owned_product_lock_ids.update(acquired_chunk_locks)
                busy_count = len(chunk_product_ids) - len(acquired_chunk_locks)
                if busy_count:
                    logger.info(
                        "Skipping %d products reserved by a concurrent priority run",
                        busy_count,
                    )
                    results["skipped"] += busy_count
                chunk_products = [
                    product for product in chunk_products
                    if product.id in acquired_chunk_locks
                ]

                # === PRE-FETCH для текущего чанка ===
                if not dry_run:
                    # A failed pagination page leaves earlier pages usable.
                    # Fetch only the missing chunk items in one POST batch.
                    await self._prefetch_missing_prices(chunk_products)

                    # Per-chunk pre-fetch убран: глобальный pre-fetch уже прогрел кеш
                    pass

                # === ФАЗА 1: вычисляем решения параллельно (50 товаров одновременно) ===
                if not dry_run:
                    self._update_stage(
                        run, "Парсинг цен в каталоге", 3, 5,
                        f"Чанк {chunk_idx + 1}/{total_chunks} ({min(chunk_start + CHUNK_SIZE, total_count)}/{total_count} товаров)"
                    )
                pending_updates = {}  # (marketplace, account_id) -> {account, updates}
                _sem = asyncio.Semaphore(50)

                async def _decide_safe(product):
                    async with _sem:
                        try:
                            # Таймаут на товар: зависший запрос (каталог через
                            # туннель, WB API) не должен останавливать весь чанк.
                            return await asyncio.wait_for(
                                self._decide_product(product, run, dry_run=dry_run),
                                timeout=self._DECIDE_TIMEOUT_SEC,
                            )
                        except asyncio.TimeoutError:
                            logger.error(
                                "Расчёт товара %s завис дольше %d с — пропускаем",
                                product.seller_sku, self._DECIDE_TIMEOUT_SEC,
                            )
                            return {"product": product, "needs_update": False, "skip_code": "PARSE_ERROR",
                                    "current_data": None, "new_price": None, "limit_reason": None,
                                    "reason_msg": f"Расчёт завис дольше {self._DECIDE_TIMEOUT_SEC} с"}
                        except Exception as e:
                            logger.error("Ошибка решения для %s: %s", product.seller_sku, e, exc_info=True)
                            # Упавший запрос оставляет транзакцию в состоянии aborted —
                            # без rollback все следующие запросы чанка тоже падают.
                            try:
                                self.db.rollback()
                            except Exception:
                                pass
                            # Ошибку считаем один раз — в фазе 3 (current_data=None).
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
                    self._update_stage(
                        run, "Расчет и отправка цен", 4, 5,
                        f"Выгрузка обновлений для чанка {chunk_idx + 1}/{total_chunks}"
                    )
                    from app.services.wb_api import WBAuthError
                    from datetime import timedelta
                    from app.services.ozon_api import OzonAuthError
                    _nm_to_sku = {
                        d["product"].nm_id: d["product"].seller_sku
                        for d in decisions if d.get("product")
                    }

                    async def _update_account(_marketplace, _account_id, entry):
                        account = entry["account"]
                        updates = entry["updates"]
                        _ok = set()
                        _fail = set()
                        _errors = {}
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
                                        _fail.add(u["_product_pk"])
                                        _errors[u["_product_pk"]] = "Статус задачи Ozon не получен"
                                    elif st[0]:
                                        _ok.add(u["_product_pk"])
                                    else:
                                        _fail.add(u["_product_pk"])
                                        _errors[u["_product_pk"]] = st[1] or "Ozon отклонил цену"
                                return _ok, _fail, _errors

                            token = account.wb_api_token
                            async with create_wb_client(token) as client:
                                wb_updates = [
                                    {k: v for k, v in u.items() if k != "_product_pk"}
                                    for u in updates
                                ]
                                statuses, verified = await self._wb_upload_verified(client, wb_updates)
                                cache_delete(f"wb_auth_failures:{account.id}")
                                if not verified:
                                    # «Задача уже существует» без id — проверить нечем
                                    for u in updates:
                                        _ok.add(u["_product_pk"])
                                        cache_delete(f"wb_catalog:{u['nmID']}")
                                    logger.info(
                                        "Batch update: %d цен — задача уже в очереди WB, статус не проверить",
                                        len(updates),
                                    )
                                    return _ok, _fail, _errors
                            for u in updates:
                                nm = u["nmID"]
                                st = statuses.get(nm)
                                if st is None:
                                    # WB не подтвердил статус за ~90 с. Раньше это писалось
                                    # как ошибка WB, хотя цены применялись. Считаем цену
                                    # отправленной: closed-loop перечитает фактическую цену.
                                    _ok.add(u["_product_pk"])
                                    cache_delete(f"wb_catalog:{nm}")
                                    logger.warning("nm_id=%s: статус WB не подтверждён", nm)
                                elif st[0]:
                                    _ok.add(u["_product_pk"])
                                    cache_delete(f"wb_catalog:{nm}")
                                else:
                                    _fail.add(u["_product_pk"])
                                    err_text = st[1] or "WB отклонил цену"
                                    _errors[u["_product_pk"]] = err_text
                                    if 'quarantine' in err_text.lower():
                                        # Не блокируем товар: следующий прогон снова
                                        # попробует выставить цену. Пользователь получает
                                        # сводку в Telegram в конце прогона.
                                        sku = _nm_to_sku.get(nm) or str(nm)
                                        self._quarantine_hits.append((account.name, sku, nm))
                                        cache_set(f"wb_quarantine_hold:{nm}", {"run_id": run.id},
                                                  ttl=self._QUARANTINE_HOLD_SEC)
                                        logger.warning("nm_id=%s: WB Price Quarantine — цена не применена", nm)
                        except WBAuthError as e:
                            _acc = next(
                                (d["product"].account for d in decisions
                                 if d.get("product") and d["product"].account
                                  and d["product"].account.id == account.id),
                                None,
                            )
                            deactivated = await self._handle_wb_auth_failure(_acc or account, e)
                            for u in updates:
                                _fail.add(u["_product_pk"])
                                _errors[u["_product_pk"]] = (
                                    f"401 от WB — аккаунт деактивирован: {e}" if deactivated
                                    else f"401 от WB (кабинет пока активен): {e}"
                                )
                        except OzonAuthError as e:
                            logger.error("Аккаунт '%s' (id=%s): Ozon auth error — деактивируем", account.name, account.id)
                            account.is_active = False
                            for u in updates:
                                _fail.add(u["_product_pk"])
                                _errors[u["_product_pk"]] = f"401 от Ozon — аккаунт деактивирован: {e}"
                        except Exception as e:
                            logger.error("Ошибка batch update (%s): %s", account.name, e)
                            for u in updates:
                                _fail.add(u["_product_pk"])
                                _errors[u["_product_pk"]] = f"Исключение при отправке: {e}"
                        return _ok, _fail, _errors

                    _account_results = await asyncio.gather(*[
                        _update_account(mk, aid, ent)
                        for (mk, aid), ent in pending_updates.items()
                    ])
                    for _ok, _fail, _errors in _account_results:
                        update_ok.update(_ok)
                        update_fail.update(_fail)
                        update_errors.update(_errors)

                elif dry_run:
                    for d in decisions:
                        if d["needs_update"]:
                            update_ok.add(d["product"].id)

                # === ФАЗА 3: сохраняем результаты ===
                pending = 0
                for dec in decisions:
                    skip = dec.get("skip_code")
                    if skip == "OUT_OF_STOCK":
                        results["out_of_stock"] += 1
                        results["skipped"] += 1
                        self._reason_counts["OUT_OF_STOCK"] += 1
                        continue
                    if skip and dec["current_data"] is None:
                        self._reason_counts[skip] += 1
                        if skip == "CATALOG_UNAVAILABLE":
                            results["catalog_unavailable"] += 1
                        if skip in self._SKIP_CODES:
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
                        self._reason_counts[reason_code] += 1
                        if reason_code in self._UPDATED_CODES:
                            results["updated"] += 1
                            # Цена применена — ставим в очередь на closed-loop verify + SPP guard (и RRP, и Competitor).
                            if not dry_run:
                                competitor_updates.append({
                                    "product": product,
                                    "nm_id": product.nm_id,
                                    "token": product.account.wb_api_token,
                                    "config": product.config,
                                    "new_price": dec["new_price"],
                                    "current_data": dec["current_data"],
                                    "old_seller_price": dec["current_data"].get("seller_price") if dec.get("current_data") else None,
                                    "strategy": dec.get("effective_strategy") or getattr(product.config, "strategy", None),
                                    "competitor_price": dec.get("snap_competitor_price"),
                                    "competitor_nm_id": dec.get("snap_competitor_nm_id"),
                                    "competitor_partial": product.config.id in self._competitor_partial,
                                })
                        elif reason_code in self._SKIP_CODES:
                            results["skipped"] += 1
                            if reason_code == "CATALOG_UNAVAILABLE":
                                results["catalog_unavailable"] += 1
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

                if not dry_run and not self._hold_product_locks:
                    closed_loop_ids = {
                        item["product"].id for item in competitor_updates
                    }
                    releasable = acquired_chunk_locks - closed_loop_ids
                    release_product_locks(
                        self.user_id or 0,
                        releasable,
                        self._product_lock_owner,
                    )
                    owned_product_lock_ids.difference_update(releasable)
                    if owned_product_lock_ids:
                        renewed = acquire_product_locks(
                            self.user_id or 0,
                            owned_product_lock_ids,
                            self._product_lock_owner,
                        )
                        if renewed != owned_product_lock_ids:
                            raise RuntimeError("Lost product lock before closed-loop verification")

                # Abort if run was cancelled/failed externally (e.g. by scheduler orphan cleanup)
                if chunk_idx % 5 == 4:
                    self.db.refresh(run)
                    if run.status not in ("running",):
                        logger.warning(
                            "Run #%d was marked '%s' externally — aborting at chunk %d/%d",
                            run.id, run.status, chunk_idx + 1, total_chunks,
                        )
                        return run

            # === ФАЗА 4: closed-loop verify+correct обновлённых competitor-товаров ===
            # WB пересчитывает СПП за секунды, поэтому доправляем промахи (через
            # порог СПП) в том же прогоне, не дожидаясь следующего часа.
            if competitor_updates and not dry_run:
                self._update_stage(
                    run, "Контроль применения (Closed-loop)", 5, 5,
                    f"Проверка СПП для {len(competitor_updates)} товаров"
                )
                try:
                    corrected_n = await self._verify_and_correct_competitor(run, competitor_updates)
                    results["updated"] += corrected_n
                except Exception as e:
                    logger.error("Closed-loop коррекция упала: %s", e, exc_info=True)

            # Scheduler/API могут пометить прогон failed/cancelled между
            # контрольными точками. Не перезаписываем внешний терминальный
            # статус обратно в completed при финализации живой Celery-задачи.
            self.db.refresh(run)
            if run.status != "running":
                logger.warning(
                    "Run #%d was marked '%s' externally — preserving terminal status",
                    run.id,
                    run.status,
                )
                return run

            # Обновляем статус прогона
            _duration_ms = int((time.monotonic() - _run_start) * 1000)
            catalog_unavailable = results.get("catalog_unavailable", 0)
            out_of_stock = results.get("out_of_stock", 0)
            active_products = run.total_products - out_of_stock
            catalog_unavailable_pct = (
                catalog_unavailable / active_products * 100
                if active_products > 0
                else 0
            )
            catalog_outage = (
                catalog_unavailable > 10
                and active_products > 100
                and catalog_unavailable_pct > 50
            )
            # Считаем уникальные товары без задвоения Closed-loop доправок
            try:
                if dry_run:
                    raise RuntimeError("dry_run: изменения в БД не пишутся")
                from sqlalchemy import func, distinct
                from app.models.price_change import PriceChange
                unique_updated = self.db.query(func.count(distinct(PriceChange.product_id))).filter(
                    PriceChange.run_id == run.id,
                    PriceChange.reason_code.in_(["UPDATED", "CORRECTED", "CLAMPED_TO_MIN", "CLAMPED_TO_MAX", "CLAMPED_TO_MARGIN"])
                ).scalar()
                run.updated_count = unique_updated if unique_updated is not None else results['updated']
            except Exception:
                run.updated_count = results['updated']
            self._update_stage(
                run, "Завершено", 6, 5,
                f"Обработано {total_count} товаров: {run.updated_count} обновлено, {results['skipped']} пропущено"
            )
            run.current_stage = "Завершено"
            run.skipped_count = results['skipped']
            run.error_count = results['errors']
            run.status = "failed" if catalog_outage else "completed"
            if catalog_outage:
                run.error_message = (
                    "WB catalog unavailable for most active products "
                    f"({catalog_unavailable}/{active_products} active, {out_of_stock} OOS excluded); "
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
                "out_of_stock": out_of_stock,
                "duration_ms": _duration_ms,
                "run_type": run_type,
                "dry_run": dry_run,
                "products_per_sec": round(run.total_products / (_duration_ms / 1000), 2) if _duration_ms > 0 else 0,
            }))

            if self._quarantine_hits and not dry_run:
                try:
                    await self._notify_quarantine(run.id)
                except Exception as e:
                    logger.error("Уведомление о карантине WB не отправлено: %s", e)

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
        finally:
            if not self._hold_product_locks and owned_product_lock_ids:
                release_product_locks(
                    self.user_id or 0,
                    owned_product_lock_ids,
                    self._product_lock_owner,
                )
