"""Выгрузка цен, ценовых индексов и комиссий Ozon в снапшоты.

Только чтение из Ozon: задача не меняет цены и не вызывает
/v1/product/import/prices.

Снапшоты пишутся лишь по товарам, заведённым в репрайсере (на price_snapshots
висит внешний ключ на products). Кабинет при этом вычитывается целиком — это
13 запросов на 12 тысяч товаров, зато в логе видно, какая доля кабинета вообще
заведена в систему, а отчёт по всему кабинету строится тем же проходом
(app/scripts/ozon_price_report.py).
"""

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Optional

from app.celery_app import celery_app
from app.database import SessionLocal
from app.services.ozon_api import OzonAuthError, create_ozon_client
from app.services.ozon_buyer_price import estimate_from_price_data

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _or_none(value: Optional[Decimal]) -> Optional[Decimal]:
    """Ozon отдаёт «нет данных» нулём — в БД это должно быть NULL."""
    if value is None:
        return None
    return value if value > 0 else None


@celery_app.task(
    bind=True,
    name="ozon.sync_prices",
    max_retries=2,
    default_retry_delay=300,
    time_limit=1800,
)
def sync_ozon_prices_task(self, account_id: int, visibility: str = "IN_SALE"):
    """Снимает цены и индексы по одному Ozon-аккаунту."""
    from app.models.account import Account
    from app.models.price_snapshot import PriceSnapshot
    from app.models.product import Product

    db = SessionLocal()
    try:
        account = db.query(Account).filter(
            Account.id == account_id,
            Account.is_active == True,  # noqa: E712 — так написан весь проект
        ).first()
        if not account:
            logger.warning("[ozon.sync_prices] account_id=%s не найден или неактивен", account_id)
            return {"status": "skipped", "reason": "account not found"}
        if account.marketplace != "ozon":
            logger.info("[ozon.sync_prices] account_id=%s: marketplace=%s — пропуск",
                        account_id, account.marketplace)
            return {"status": "skipped", "reason": "not ozon"}
        if not account.ozon_client_id or not account.ozon_api_key:
            logger.warning("[ozon.sync_prices] account_id=%s: нет учётных данных Ozon", account_id)
            return {"status": "skipped", "reason": "no credentials"}

        # Товары, заведённые в репрайсере. nm_id у Ozon-аккаунта хранит
        # product_id Ozon, seller_sku — offer_id (см. api/products.py).
        products = db.query(Product).filter(Product.account_id == account_id).all()
        by_product_id: Dict[int, Product] = {p.nm_id: p for p in products if p.nm_id}
        by_offer_id: Dict[str, Product] = {p.seller_sku: p for p in products if p.seller_sku}

        logger.info("[ozon.sync_prices] account_id=%s (%s): в репрайсере %d товаров",
                    account_id, account.name, len(products))

        stats = asyncio.run(_collect(
            client_id=account.ozon_client_id,
            api_key=account.ozon_api_key,
            visibility=visibility,
            db=db,
            by_product_id=by_product_id,
            by_offer_id=by_offer_id,
            snapshot_model=PriceSnapshot,
        ))

        db.commit()
        logger.info(
            "[ozon.sync_prices] account_id=%s: кабинет=%d, снапшотов=%d, "
            "цена покупателя посчитана=%d, без индексов=%d, оценки разошлись >5%%=%d",
            account_id, stats["fetched"], stats["snapshots"],
            stats["with_buyer_price"], stats["without_index"], stats["unreliable"],
        )
        return {"status": "ok", "account_id": account_id, **stats}

    except OzonAuthError as exc:
        db.rollback()
        logger.error("[ozon.sync_prices] account_id=%s: ошибка авторизации Ozon — %s",
                     account_id, exc)
        return {"status": "error", "reason": "auth", "detail": str(exc)}
    except Exception as exc:
        db.rollback()
        logger.error("[ozon.sync_prices] account_id=%s: ошибка — %s", account_id, exc, exc_info=True)
        raise self.retry(exc=exc)
    finally:
        db.close()


async def _collect(
    *,
    client_id: str,
    api_key: str,
    visibility: str,
    db,
    by_product_id: Dict[int, object],
    by_offer_id: Dict[str, object],
    snapshot_model,
) -> Dict[str, int]:
    """Проходит кабинет страницами и складывает снапшоты по найденным товарам."""
    stats = {
        "fetched": 0,
        "snapshots": 0,
        "with_buyer_price": 0,
        "without_index": 0,
        "unreliable": 0,
        "pages": 0,
    }
    now = _utcnow()

    async with create_ozon_client(client_id, api_key) as client:
        async for page in client.iter_all_prices(visibility=visibility):
            stats["pages"] += 1
            stats["fetched"] += len(page)

            for price_data in page:
                estimate = estimate_from_price_data(price_data)
                if estimate is None:
                    stats["without_index"] += 1
                else:
                    stats["with_buyer_price"] += 1
                    if estimate.spread_pct > 5:
                        stats["unreliable"] += 1

                product = (
                    by_product_id.get(price_data.product_id)
                    or by_offer_id.get(price_data.offer_id)
                )
                if product is None:
                    continue

                seller_price = price_data.price
                buyer_price = estimate.buyer_price if estimate else None

                db.add(snapshot_model(
                    product_id=product.id,
                    seller_price=seller_price,
                    # final_price в модели NOT NULL. Если индексов нет, честнее
                    # записать цену продавца, чем выдумывать цену покупателя.
                    final_price=buyer_price if buyer_price is not None else seller_price,
                    old_price=_or_none(price_data.old_price),
                    min_price=_or_none(price_data.min_price),
                    ozon_competitor_min_price=_or_none(price_data.ozon_min_price),
                    ozon_price_index=_or_none(price_data.ozon_index_value),
                    external_competitor_min_price=_or_none(price_data.external_min_price),
                    external_price_index=_or_none(price_data.external_index_value),
                    self_mp_min_price=_or_none(price_data.self_mp_min_price),
                    self_mp_price_index=_or_none(price_data.self_mp_index_value),
                    buyer_price_estimate=buyer_price,
                    buyer_price_spread_pct=estimate.spread_pct if estimate else None,
                    buyer_price_sources=estimate.sources if estimate else None,
                    coinvest_ratio=estimate.ratio_to(seller_price) if estimate else None,
                    commission_percent=_or_none(price_data.sales_percent_fbs),
                    snapshot_at=now,
                ))
                stats["snapshots"] += 1

                if seller_price and seller_price > 0:
                    product.current_seller_price = seller_price

    return stats


@celery_app.task(name="ozon.sync_prices_all", time_limit=120)
def sync_all_ozon_prices_task():
    """Ставит sync_ozon_prices_task для всех активных Ozon-аккаунтов."""
    from app.models.account import Account

    db = SessionLocal()
    try:
        accounts = db.query(Account).filter(
            Account.is_active == True,  # noqa: E712
            Account.marketplace == "ozon",
        ).all()
        for account in accounts:
            sync_ozon_prices_task.delay(account.id)
        logger.info("[ozon.sync_prices_all] поставлено задач: %d", len(accounts))
        return {"status": "ok", "accounts": len(accounts)}
    finally:
        db.close()
