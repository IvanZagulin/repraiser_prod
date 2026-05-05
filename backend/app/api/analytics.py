"""Analytics router — price dynamics, savings, competitor trends, summary."""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, desc
from slowapi import Limiter

from app.database import get_db
from app.models import Account, Product, User, ProductConfig
from app.models.price_snapshot import PriceSnapshot
from app.models.price_change import PriceChange
from app.models.repricer_run import RepricerRun
from app.api.auth import get_current_user
from app.api.utils import _user_account_ids, _user_product_ids
from app.utils import get_real_ip

router = APIRouter()
limiter = Limiter(key_func=get_real_ip)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
            "spp": float(s.spp) if s.spp else None,
            "discount": float(s.discount) if s.discount else None,
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
    # Only query products with competitor strategy — avoids scanning snapshots for all 30k products
    competitor_product_ids = (
        db.query(Product.id)
        .join(Account, Product.account_id == Account.id)
        .join(ProductConfig, ProductConfig.product_id == Product.id)
        .filter(
            Account.user_id == current_user.id,
            ProductConfig.strategy == "competitor",
        )
        .all()
    )
    competitor_product_ids = [r[0] for r in competitor_product_ids]
    if not competitor_product_ids:
        return []

    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    # Limit to top-50 products by snapshot count to avoid huge response
    rows = (
        db.query(
            PriceSnapshot.product_id,
            PriceSnapshot.snapshot_at,
            PriceSnapshot.competitor_price,
        )
        .filter(
            PriceSnapshot.product_id.in_(competitor_product_ids),
            PriceSnapshot.snapshot_at >= since,
            PriceSnapshot.competitor_price.isnot(None),
        )
        .order_by(PriceSnapshot.product_id, PriceSnapshot.snapshot_at.asc())
        .limit(10000)
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


@router.get("/margin-by-brand")
@limiter.limit("30/minute")
def margin_by_brand(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Average margin grouped by brand for products with cost_price set."""
    from app.models.app_setting import AppSetting
    uid = current_user.id
    fee_keys = ["commission_type", "commission_value", "logistics_type",
                "logistics_value", "acquiring_type", "acquiring_value"]
    fee_defaults = {"commission_type": "pct", "commission_value": "0",
                    "logistics_type": "rub", "logistics_value": "0",
                    "acquiring_type": "pct", "acquiring_value": "0"}
    fee_rows = db.query(AppSetting).filter(
        AppSetting.key.in_(fee_keys),
        AppSetting.user_id.in_([uid, None])
    ).all()
    fee_map = dict(fee_defaults)
    for row in fee_rows:
        if row.user_id is None and row.key not in {r.key for r in fee_rows if r.user_id == uid}:
            fee_map[row.key] = row.value
        elif row.user_id == uid:
            fee_map[row.key] = row.value

    commission_pct = float(fee_map["commission_value"]) / 100 if fee_map["commission_type"] == "pct" else 0
    logistics_rub  = float(fee_map["logistics_value"])  if fee_map["logistics_type"]  == "rub" else 0
    acquiring_pct  = float(fee_map["acquiring_value"])  / 100 if fee_map["acquiring_type"]  == "pct" else 0

    products = (
        db.query(Product)
        .join(Account, Product.account_id == Account.id)
        .filter(
            Account.user_id == uid,
            Product.cost_price.isnot(None),
            Product.cost_price > 0,
            Product.brand.isnot(None),
        )
        .all()
    )

    from collections import defaultdict
    brand_margins: dict = defaultdict(list)
    brand_skus: dict = defaultdict(set)

    for p in products:
        cost = float(p.cost_price)
        if cost <= 0:
            continue
        sku_key = p.seller_sku or str(p.nm_id)
        brand_skus[p.brand].add(sku_key)
        snap = (
            db.query(PriceSnapshot)
            .filter(PriceSnapshot.product_id == p.id)
            .order_by(PriceSnapshot.snapshot_at.desc())
            .first()
        )
        if not snap or not snap.seller_price:
            continue
        sp = float(snap.seller_price)
        fees = sp * (commission_pct + acquiring_pct) + logistics_rub
        margin = (sp - cost - fees) / cost * 100
        brand_margins[p.brand].append(margin)

    result = []
    for brand_name, skus in brand_skus.items():
        margins = brand_margins.get(brand_name, [])
        result.append({
            "brand": brand_name,
            "avg_margin": round(sum(margins) / len(margins), 1) if margins else None,
            "products_count": len(skus),
            "products_with_margin": len(margins),
        })
    result.sort(key=lambda x: (x["avg_margin"] is None, -(x["avg_margin"] or 0)))
    return result
