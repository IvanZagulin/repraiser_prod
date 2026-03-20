"""Bulk operations router — apply strategy, activate, delete in batch."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Account, Product, User
from app.models.product_config import ProductConfig
from app.api.auth import get_current_user

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

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
def bulk_strategy(
    body: BulkStrategyBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Apply the same strategy config to multiple products."""
    if body.strategy not in ("rrp", "competitor"):
        raise HTTPException(status_code=422, detail="strategy must be 'rrp' or 'competitor'")

    products = _validate_ownership(db, body.product_ids, current_user)

    updated = 0
    created = 0
    for product in products:
        cfg = product.config
        if cfg:
            cfg.strategy = body.strategy
            # Apply extra config fields
            for key, value in body.config.items():
                if hasattr(cfg, key):
                    setattr(cfg, key, value)
            updated += 1
        else:
            cfg = ProductConfig(
                product_id=product.id,
                strategy=body.strategy,
                is_active=True,
                min_price=body.config.get("min_price", 0),
            )
            for key, value in body.config.items():
                if hasattr(cfg, key):
                    setattr(cfg, key, value)
            db.add(cfg)
            created += 1

    db.commit()
    return {"ok": True, "updated": updated, "created": created}


@router.post("/activate")
def bulk_activate(
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
def bulk_delete(
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
