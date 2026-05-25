"""Analytics router — price dynamics, savings, competitor trends, summary."""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, desc, text
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

SUCCESSFUL_PRICE_CHANGE_CODES = (
    "UPDATED",
    "CLAMPED_TO_MIN",
    "CLAMPED_TO_MAX",
    "CLAMPED_TO_MARGIN",
)


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


def _get_fee_settings(db: Session, user_id: int) -> dict[str, str]:
    """Return user fee settings with global defaults as fallback."""
    from app.models.app_setting import AppSetting

    fee_keys = [
        "commission_type",
        "commission_value",
        "logistics_type",
        "logistics_value",
        "acquiring_type",
        "acquiring_value",
    ]
    fee_map = {
        "commission_type": "pct",
        "commission_value": "0",
        "logistics_type": "rub",
        "logistics_value": "0",
        "acquiring_type": "pct",
        "acquiring_value": "0",
    }
    fee_rows = (
        db.query(AppSetting)
        .filter(AppSetting.key.in_(fee_keys), AppSetting.user_id.in_([user_id, None]))
        .all()
    )
    user_keys = {row.key for row in fee_rows if row.user_id == user_id}
    for row in fee_rows:
        if row.user_id == user_id or (row.user_id is None and row.key not in user_keys):
            fee_map[row.key] = row.value
    return fee_map


def _to_float(value):
    return float(value) if value is not None else None


def _fee_amount(price: float, fee_map: dict[str, str]) -> float:
    total = 0.0
    for key in ("commission", "logistics", "acquiring"):
        fee_type = fee_map.get(f"{key}_type", "pct")
        fee_value = float(fee_map.get(f"{key}_value") or 0)
        total += price * fee_value / 100 if fee_type == "pct" else fee_value
    return total


def _calculate_margin_pct(
    seller_price,
    cost_price,
    fee_map: dict[str, str],
    discount=None,
):
    cost = _to_float(cost_price)
    price = _to_float(seller_price)
    if not cost or cost <= 0 or not price:
        return None

    discount_pct = _to_float(discount) or 0.0
    margin_price = price
    if 0 < discount_pct < 100:
        margin_price = price * (1 - discount_pct / 100)

    fees = _fee_amount(margin_price, fee_map)
    return (margin_price - cost - fees) / cost * 100


def _latest_successful_price_changes(db: Session, product_ids: list[int]) -> dict[int, object]:
    if not product_ids:
        return {}

    rows = db.execute(
        text(
            """
            SELECT scope.product_id,
                   latest.new_seller_price,
                   latest.new_final_price,
                   latest.changed_at
            FROM unnest(CAST(:product_ids AS integer[])) AS scope(product_id)
            JOIN LATERAL (
                SELECT pc.new_seller_price,
                       pc.new_final_price,
                       pc.changed_at
                FROM price_changes pc
                WHERE pc.product_id = scope.product_id
                  AND pc.reason_code = ANY(CAST(:reason_codes AS text[]))
                  AND pc.new_seller_price IS NOT NULL
                ORDER BY pc.changed_at DESC, pc.id DESC
                LIMIT 1
            ) AS latest ON TRUE
            """
        ),
        {
            "product_ids": product_ids,
            "reason_codes": list(SUCCESSFUL_PRICE_CHANGE_CODES),
        },
    ).all()
    return {row.product_id: row for row in rows}


def _latest_price_snapshots(db: Session, product_ids: list[int]) -> dict[int, object]:
    if not product_ids:
        return {}

    rows = db.execute(
        text(
            """
            SELECT scope.product_id,
                   latest.seller_price,
                   latest.final_price,
                   latest.competitor_price,
                   latest.spp,
                   latest.discount,
                   latest.total_quantity,
                   latest.snapshot_at
            FROM unnest(CAST(:product_ids AS integer[])) AS scope(product_id)
            JOIN LATERAL (
                SELECT ps.seller_price,
                       ps.final_price,
                       ps.competitor_price,
                       ps.spp,
                       ps.discount,
                       ps.total_quantity,
                       ps.snapshot_at
                FROM price_snapshots ps
                WHERE ps.product_id = scope.product_id
                ORDER BY ps.snapshot_at DESC, ps.id DESC
                LIMIT 1
            ) AS latest ON TRUE
            """
        ),
        {"product_ids": product_ids},
    ).all()
    return {row.product_id: row for row in rows}


def _stock_qty(snapshot) -> int:
    if not snapshot or snapshot.total_quantity is None:
        return 0
    return int(snapshot.total_quantity or 0)


def _dedupe_in_stock_products(rows, snap_map: dict[int, object]) -> list[tuple[object, object]]:
    best_by_sku: dict[str, tuple[object, object, int]] = {}
    for row in rows:
        snap = snap_map.get(row.id)
        qty = _stock_qty(snap)
        if qty <= 0:
            continue

        sku_key = row.seller_sku or str(row.nm_id)
        current = best_by_sku.get(sku_key)
        if not current:
            best_by_sku[sku_key] = (row, snap, qty)
            continue

        _, current_snap, current_qty = current
        current_at = current_snap.snapshot_at if current_snap else None
        snap_at = snap.snapshot_at if snap else None
        if qty > current_qty or (qty == current_qty and snap_at and (not current_at or snap_at > current_at)):
            best_by_sku[sku_key] = (row, snap, qty)

    return [(row, snap) for row, snap, _ in best_by_sku.values()]


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
    uid = current_user.id
    fee_map = _get_fee_settings(db, uid)

    # current_seller_price updates from snapshots, while PriceChange stores the
    # price immediately after repricing. Prefer the latest successful repricing
    # result so margin changes are visible before the next external sync.
    rows = (
        db.query(
            Product.id,
            Product.brand,
            Product.seller_sku,
            Product.nm_id,
            Product.cost_price,
            Product.current_seller_price,
        )
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

    if not rows:
        return []

    snap_map = _latest_price_snapshots(db, [row.id for row in rows])
    products_for_margin = _dedupe_in_stock_products(rows, snap_map)
    if not products_for_margin:
        return []

    latest_changes = _latest_successful_price_changes(
        db, [row.id for row, _ in products_for_margin]
    )

    for row, _snap in products_for_margin:
        current_price = row.current_seller_price
        latest_change = latest_changes.get(row.id)
        if latest_change and latest_change.new_seller_price is not None:
            current_price = latest_change.new_seller_price

        sku_key = row.seller_sku or str(row.nm_id)
        brand_skus[row.brand].add(sku_key)
        margin = _calculate_margin_pct(current_price, row.cost_price, fee_map)
        if margin is None:
            continue
        brand_margins[row.brand].append(margin)

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


@router.get("/export/brand")
@limiter.limit("10/minute")
async def export_brand_report(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    brands: Optional[str] = Query(None, description="Бренды через запятую, пустое = все"),
):
    """Экспорт отчёта по бренду/брендам: товар, цена, маржа, РРЦ, цена до СПП, цена конкурента."""
    from app.utils.excel import excel_exporter

    uid = current_user.id
    fee_map = _get_fee_settings(db, uid)

    q = (
        db.query(
            Product.id,
            Product.seller_sku,
            Product.name,
            Product.brand,
            Product.nm_id,
            Product.cost_price,
            Product.current_seller_price,
            Account.name.label("account_name"),
            ProductConfig.rrp_price,
            ProductConfig.min_price,
            ProductConfig.strategy,
            ProductConfig.is_active,
        )
        .join(Account, Product.account_id == Account.id)
        .outerjoin(ProductConfig, ProductConfig.product_id == Product.id)
        .filter(Account.user_id == uid)
    )
    if brands:
        brand_list = [b.strip() for b in brands.split(',') if b.strip()]
        if brand_list:
            q = q.filter(Product.brand.in_(brand_list))

    products = q.all()

    if not products:
        raise HTTPException(status_code=404, detail="Товары не найдены")

    product_ids = [p.id for p in products]
    snap_map = _latest_price_snapshots(db, product_ids)
    products_for_report = _dedupe_in_stock_products(products, snap_map)
    if not products_for_report:
        raise HTTPException(status_code=404, detail="Товары с остатком не найдены")

    latest_changes = _latest_successful_price_changes(
        db, [p.id for p, _ in products_for_report]
    )

    rows = []
    for p, snap in products_for_report:
        latest_change = latest_changes.get(p.id)
        use_change = bool(
            latest_change
            and (
                not snap
                or not snap.snapshot_at
                or latest_change.changed_at >= snap.snapshot_at
            )
        )

        seller_price = (
            _to_float(latest_change.new_seller_price)
            if use_change
            else _to_float(p.current_seller_price)
        )
        if seller_price is None and snap:
            seller_price = _to_float(snap.seller_price)

        final_price = _to_float(latest_change.new_final_price) if use_change else None
        if final_price is None and snap:
            final_price = _to_float(snap.final_price)

        competitor_price = _to_float(snap.competitor_price) if snap else None
        spp = _to_float(snap.spp) if snap else None
        discount = _to_float(snap.discount) if snap else None
        stock_quantity = _stock_qty(snap)
        cost = _to_float(p.cost_price) if p.cost_price and float(p.cost_price) > 0 else None
        margin_pct = _calculate_margin_pct(seller_price, cost, fee_map, discount)
        margin = round(margin_pct, 2) if margin_pct is not None else None

        rrp_price = _to_float(p.rrp_price)
        min_price = _to_float(p.min_price)

        rows.append({
            "seller_sku":       p.seller_sku or "",
            "name":             p.name or "",
            "brand":            p.brand or "",
            "nm_id":            p.nm_id,
            "account":          p.account_name or "",
            "seller_price":     seller_price,
            "final_price":      final_price,
            "spp":              spp,
            "stock_quantity":   stock_quantity,
            "competitor_price": competitor_price,
            "rrp_price":        rrp_price,
            "min_price":        min_price,
            "cost_price":       cost,
            "margin":           margin,
            "strategy":         p.strategy,
            "is_active":        p.is_active,
        })

    excel_file = await run_in_threadpool(excel_exporter.export_brand_report, rows)
    from datetime import datetime, timezone as tz
    filename = f"brand_report_{datetime.now(tz.utc).strftime('%Y%m%d_%H%M%S')}.xlsx"
    return Response(
        content=excel_file.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )
