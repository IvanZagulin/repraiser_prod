import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Account, ConfigChangeLog, Product, ProductConfig, PromoPauseRegistry
from app.services.wb_api import create_wb_client
from app.utils.jwt import is_token_expired


logger = logging.getLogger(__name__)


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class PromoPauseSyncResult:
    prices: Dict[int, object] = field(default_factory=dict)
    paused_product_ids: List[int] = field(default_factory=list)
    resumed_product_ids: List[int] = field(default_factory=list)
    checked: int = 0


async def sync_promo_pauses(db: Session, user_id: int) -> PromoPauseSyncResult:
    """Pause discounted WB products and resume only rows paused by this registry.

    A missing/partial Prices API response aborts the run. Repricing without a
    complete discount check could overwrite prices just uploaded by 1C.
    """

    # МЕХАНИЗМ АВТО-ПАУЗЫ ПО АКЦИИ ОТКЛЮЧЁН (Иван, 19.10.2026 — дорабатываем позже).
    # Товары по скидке WB больше НЕ выключаются и не включаются этим механизмом.
    return PromoPauseSyncResult()

    rows = db.execute(
        select(Product, ProductConfig, Account, PromoPauseRegistry)
        .join(ProductConfig, ProductConfig.product_id == Product.id)
        .join(Account, Account.id == Product.account_id)
        .outerjoin(
            PromoPauseRegistry,
            PromoPauseRegistry.product_config_id == ProductConfig.id,
        )
        .where(Account.user_id == user_id)
        .where(Account.is_active.is_(True))
        .where(Account.marketplace == "wb")
        .where(or_(
            ProductConfig.is_active.is_(True),
            PromoPauseRegistry.is_paused.is_(True),
        ))
    ).all()

    by_account = {}
    for product, config, account, registry in rows:
        by_account.setdefault(account.id, {
            "account": account,
            "rows": [],
        })["rows"].append((product, config, registry))

    result = PromoPauseSyncResult()
    now = _utcnow()
    try:
        for entry in by_account.values():
            account = entry["account"]
            account_rows = entry["rows"]
            if is_token_expired(account.wb_api_token):
                raise RuntimeError(
                    f"Нельзя проверить скидки кабинета {account.name}: токен WB истёк"
                )

            requested = list(dict.fromkeys(
                product.nm_id for product, _, _ in account_rows
            ))
            async with create_wb_client(account.wb_api_token) as client:
                prices = await client.get_prices_by_articles(requested)
            by_nm = {price.nm_id: price for price in prices}
            missing = sorted(set(requested) - set(by_nm))
            if missing:
                raise RuntimeError(
                    f"Prices API кабинета {account.name} не вернул "
                    f"{len(missing)} из {len(requested)} карточек; переоценка остановлена"
                )

            result.prices.update(by_nm)
            result.checked += len(requested)
            for product, config, registry in account_rows:
                price = by_nm[product.nm_id]
                discount = Decimal(str(price.discount or 0))
                if discount > 0:
                    if registry is None:
                        registry = PromoPauseRegistry(
                            product_config_id=config.id,
                            product_id=product.id,
                            account_id=account.id,
                            nm_id=product.nm_id,
                            seller_sku=product.seller_sku,
                            discount_pct=discount,
                            paused_at=now,
                            last_checked_at=now,
                            zero_streak=0,
                            is_paused=True,
                        )
                        db.add(registry)
                    else:
                        registry.discount_pct = discount
                        registry.last_checked_at = now
                        registry.zero_streak = 0
                        registry.is_paused = True
                        registry.resumed_at = None
                    if config.is_active:
                        config.is_active = False
                        db.add(ConfigChangeLog(
                            user_id=user_id,
                            product_id=product.id,
                            field_name="promo_auto_pause",
                            old_value="active",
                            new_value=f"discount={discount}%",
                        ))
                        result.paused_product_ids.append(product.id)
                elif registry is not None and registry.is_paused:
                    # Не включаем товар по одному ответу discount=0: повторная
                    # проверка через следующий прогон защищает от краткого сбоя WB.
                    registry.discount_pct = Decimal("0")
                    registry.last_checked_at = now
                    registry.zero_streak = registry.zero_streak + 1
                    if registry.zero_streak >= 2:
                        registry.is_paused = False
                        registry.resumed_at = now
                        if not config.is_active:
                            config.is_active = True
                            db.add(ConfigChangeLog(
                                user_id=user_id,
                                product_id=product.id,
                                field_name="promo_auto_pause",
                                old_value="discount",
                                new_value="active",
                            ))
                            result.resumed_product_ids.append(product.id)

        db.commit()
    except Exception:
        db.rollback()
        raise
    logger.info(
        "Promo pause sync: checked=%d paused=%d resumed=%d",
        result.checked,
        len(result.paused_product_ids),
        len(result.resumed_product_ids),
    )
    return result

