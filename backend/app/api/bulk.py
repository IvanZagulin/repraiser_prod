"""Bulk operations router — apply strategy, activate, delete in batch."""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from slowapi import Limiter

from app.database import get_db
from app.models import Account, Product, User
from app.models.product_config import ProductConfig
from app.api.auth import get_current_user
from app.utils import get_real_ip
from app.services.activity_logger import log_activity

router = APIRouter()
limiter = Limiter(key_func=get_real_ip)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

# Поля конфигурации, которые можно менять массово
_BULK_CONFIG_FIELDS = {
    "is_active", "min_price", "max_price",
    "rrp_price", "rrp_tolerance", "rrp_consider_wallet",
    "competitor_nm_id", "competitor_offset_type", "competitor_offset_direction",
    "competitor_offset_value", "competitor_select_rule", "match_competitor_basic",
    "night_price_enabled", "night_start_msk", "night_end_msk", "night_seller_price",
    "skip_margin_check",
}


class BulkStrategyBody(BaseModel):
    product_ids: List[int]
    strategy: str  # "rrp" | "competitor"
    config: dict = {}


class BulkActivateBody(BaseModel):
    product_ids: List[int]
    is_active: bool


class BulkDeleteBody(BaseModel):
    product_ids: List[int]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_ownership(db: Session, product_ids: list[int], user: User) -> list[Product]:
    """Return products that belong to the user. Raise 403 if any do not."""
    products = (
        db.query(Product)
        .join(Account, Product.account_id == Account.id)
        .filter(Product.id.in_(product_ids), Account.user_id == user.id)
        .all()
    )
    owned_ids = {p.id for p in products}
    missing = set(product_ids) - owned_ids
    if missing:
        raise HTTPException(
            status_code=403,
            detail=f"Products not owned by user: {sorted(missing)}",
        )
    return products


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/strategy")
@limiter.limit("10/minute")
def bulk_strategy(
    request: Request,
    body: BulkStrategyBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Apply the same strategy config to multiple products."""
    if body.strategy not in ("rrp", "competitor"):
        raise HTTPException(status_code=422, detail="strategy must be 'rrp' or 'competitor'")

    unknown = set(body.config) - _BULK_CONFIG_FIELDS
    if unknown:
        # Раньше setattr() применялся к любому атрибуту конфига (product_id, id,
        # wb_blocked_until …) — можно было перенести конфиг на другой товар.
        raise HTTPException(status_code=422, detail=f"Недопустимые поля: {sorted(unknown)}")
    cfg_values = dict(body.config)
    for field in ("min_price", "max_price", "rrp_price"):
        value = cfg_values.get(field)
        if value is not None and float(value) < 0:
            raise HTTPException(status_code=422, detail=f"{field} не может быть отрицательным")
    if (
        cfg_values.get("min_price") is not None and cfg_values.get("max_price") is not None
        and float(cfg_values["max_price"]) < float(cfg_values["min_price"])
    ):
        raise HTTPException(status_code=422, detail="max_price меньше min_price")

    products = _validate_ownership(db, body.product_ids, current_user)

    if body.strategy == "rrp" and not cfg_values.get("rrp_price"):
        # Стратегия РРЦ без РРЦ молча не переоценивалась («РРЦ не настроен»)
        missing_rrp = [
            p.seller_sku or str(p.nm_id) for p in products
            if not (p.config and (p.config.rrp_price or p.config.rrp_spp_tiers))
        ]
        if missing_rrp:
            raise HTTPException(
                status_code=422,
                detail=f"Для стратегии РРЦ укажите РРЦ: нет РРЦ у {len(missing_rrp)} товаров ({', '.join(missing_rrp[:20])})",
            )

    updated = 0
    created = 0
    for product in products:
        cfg = product.config
        if cfg:
            cfg.strategy = body.strategy
            for key, value in cfg_values.items():
                setattr(cfg, key, value)
            updated += 1
        else:
            cfg = ProductConfig(
                product_id=product.id,
                strategy=body.strategy,
                is_active=True,
                min_price=cfg_values.get("min_price", 0),
            )
            for key, value in cfg_values.items():
                setattr(cfg, key, value)
            db.add(cfg)
            created += 1

    db.commit()
    return {"ok": True, "updated": updated, "created": created}


@router.post("/activate")
@limiter.limit("20/minute")
def bulk_activate(
    request: Request,
    body: BulkActivateBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Activate or deactivate repricer for multiple products."""
    products = _validate_ownership(db, body.product_ids, current_user)

    affected = 0
    for product in products:
        if product.config:
            product.config.is_active = body.is_active
            affected += 1

    db.commit()
    return {"ok": True, "affected": affected}


@router.post("/delete")
@limiter.limit("10/minute")
def bulk_delete(
    request: Request,
    body: BulkDeleteBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete multiple products. Returns count deleted."""
    products = _validate_ownership(db, body.product_ids, current_user)

    count = 0
    for product in products:
        db.delete(product)
        count += 1

    db.commit()
    return {"ok": True, "deleted": count}
