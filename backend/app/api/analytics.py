"""Analytics router — price dynamics, savings, competitor trends, summary."""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, desc
from slowapi import Limiter

from app.database import get_db
from app.models import Account, Product, User
from app.models.price_snapshot import PriceSnapshot
from app.models.price_change import PriceChange
from app.models.repricer_run import RepricerRun
from app.api.auth import get_current_user
from app.utils import get_real_ip

router = APIRouter()
limiter = Limiter(key_func=get_real_ip)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _user_account_ids(db: Session, user: User) -> list[int]:
    """Return list of account IDs belonging to the user."""
    return [
        aid for (aid,) in db.query(Account.id).filter(Account.user_id == user.id).all()
    ]


def _user_product_ids(db: Session, user: User) -> list[int]:
    """Return list of product IDs belonging to the user."""
    account_ids = _user_account_ids(db, user)
    if not account_ids:
        return []
    return [
        pid
        for (pid,) in db.query(Product.id)
        .filter(Product.account_id.in_(account_ids))
        .all()
    ]


def _verify_product_ownership(db: Session, product_id: int, user: User) -> Product:
    """Return product if it belongs to the user, else raise 404."""
    product = (
        db.query(Product)
        .join(Account, Product.account_id == Account.id)
        .filter(Product.id == product_id, Account.user_id == user.id)
        .first()
    )
    if not product:
        raise HTTPException(status_code=404, detail="product_not_found")
    return product


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/product/{product_id}/price-dynamics")
@limiter.limit("60/minute")
def price_dynamics(
    request: Request,
    product_id: int,
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Time series of price snapshots for a product."""
    _verify_product_ownership(db, product_id, current_user)

    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    snapshots = (
        db.query(PriceSnapshot)
        .filter(
            PriceSnapshot.product_id == product_id,
            PriceSnapshot.snapshot_at >= since,
        )
        .order_by(PriceSnapshot.snapshot_at.asc())
        .all()
    )

    return [
        {
            "snapshot_at": s.snapshot_at,
            "seller_price": float(s.seller_price) if s.seller_price else None,
            "final_price": float(s.final_price) if s.final_price else None,
            "competitor_price": float(s.competitor_price) if s.competitor_price else None,
        }
        for s in snapshots
    ]


@router.get("/savings")
@limiter.limit("60/minute")
def savings(
    request: Request,
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Calculate total savings from price reductions."""
    product_ids = _user_product_ids(db, current_user)
    if not product_ids:
        return {"total_savings_rub": 0, "changes_count": 0, "period_days": days}

    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    reason_codes = ("UPDATED", "CLAMPED_TO_MIN", "CLAMPED_TO_MAX", "CLAMPED_TO_MARGIN")

    changes = (
        db.query(PriceChange)
        .filter(
            PriceChange.product_id.in_(product_ids),
            PriceChange.changed_at >= since,
            PriceChange.reason_code.in_(reason_codes),
            PriceChange.new_seller_price < PriceChange.old_seller_price,
        )
        .all()
    )

    total_savings = sum(
        float(c.old_seller_price) - float(c.new_seller_price) for c in changes
    )

    return {
        "total_savings_rub": round(total_savings, 2),
        "changes_count": len(changes),
        "period_days": days,
    }


@router.get("/competitor-trends")
@limiter.limit("30/minute")
def competitor_trends(
    request: Request,
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Competitor price trends for products with competitor strategy."""
    product_ids = _user_product_ids(db, current_user)
    if not product_ids:
        return []

    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    # Get products that have at least one competitor snapshot
    rows = (
        db.query(
            PriceSnapshot.product_id,
            PriceSnapshot.snapshot_at,
            PriceSnapshot.competitor_price,
        )
        .filter(
            PriceSnapshot.product_id.in_(product_ids),
            PriceSnapshot.snapshot_at >= since,
            PriceSnapshot.competitor_price.isnot(None),
        )
        .order_by(PriceSnapshot.product_id, PriceSnapshot.snapshot_at.asc())
        .all()
    )

    # Group by product
    from collections import defaultdict

    grouped: dict[int, list] = defaultdict(list)
    for pid, snap_at, comp_price in rows:
        grouped[pid].append(
            {"snapshot_at": snap_at, "competitor_price": float(comp_price)}
        )

    # Fetch product names
    products = (
        db.query(Product.id, Product.name)
        .filter(Product.id.in_(list(grouped.keys())))
        .all()
    )
    name_map = {p.id: p.name for p in products}

    return [
        {
            "product_id": pid,
            "product_name": name_map.get(pid),
            "data": data,
        }
        for pid, data in grouped.items()
    ]


@router.get("/summary")
@limiter.limit("60/minute")
def summary(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Aggregated repricer statistics for the current user."""
    product_ids = _user_product_ids(db, current_user)

    # Total price changes
    total_price_changes = 0
    if product_ids:
        total_price_changes = (
            db.query(func.count(PriceChange.id))
            .filter(PriceChange.product_id.in_(product_ids))
            .scalar()
            or 0
        )

    # Total runs
    total_runs = (
        db.query(func.count(RepricerRun.id))
        .filter(RepricerRun.user_id == current_user.id)
        .scalar()
        or 0
    )

    # Average updates per run
    avg_update_per_run = 0.0
    if total_runs > 0:
        avg_result = (
            db.query(func.avg(RepricerRun.updated_count))
            .filter(RepricerRun.user_id == current_user.id)
            .scalar()
        )
        avg_update_per_run = round(float(avg_result), 2) if avg_result else 0.0

    # Most changed products (top 5)
    most_changed = []
    if product_ids:
        rows = (
            db.query(
                PriceChange.product_id,
                func.count(PriceChange.id).label("changes"),
            )
            .filter(PriceChange.product_id.in_(product_ids))
            .group_by(PriceChange.product_id)
            .order_by(desc("changes"))
            .limit(5)
            .all()
        )
        if rows:
            top_pids = [r.product_id for r in rows]
            products = (
                db.query(Product.id, Product.name, Product.nm_id)
                .filter(Product.id.in_(top_pids))
                .all()
            )
            prod_map = {p.id: {"name": p.name, "nm_id": p.nm_id} for p in products}
            most_changed = [
                {
                    "product_id": r.product_id,
                    "product_name": prod_map.get(r.product_id, {}).get("name"),
                    "nm_id": prod_map.get(r.product_id, {}).get("nm_id"),
                    "changes_count": r.changes,
                }
                for r in rows
            ]

    return {
        "total_price_changes": total_price_changes,
        "total_runs": total_runs,
        "avg_update_per_run": avg_update_per_run,
        "most_changed_products": most_changed,
    }
