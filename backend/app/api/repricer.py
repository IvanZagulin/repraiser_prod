from typing import Annotated, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database import get_db
from app.models import Product, RepricerRun, PriceChange, Account
from app.models.user import User
from app.schemas.repricer import (
    ManualRunRequest, RepricerRunResponse, RepricerRunListResponse,
    PriceChangeResponse
)
from app.api.auth import get_current_user
from app.services.repricer_engine import RepricerEngine

router = APIRouter()


def _user_product_ids(db: Session, user_id: int, seller_skus: Optional[List[str]] = None):
    """Возвращает ID товаров текущего пользователя (с фильтром по SKU если задан)."""
    q = (
        db.query(Product)
        .join(Account, Product.account_id == Account.id)
        .filter(Account.user_id == user_id)
    )
    if seller_skus:
        q = q.filter(Product.seller_sku.in_(seller_skus))
    return [p.id for p in q.all()]


@router.post("/run", response_model=RepricerRunResponse)
async def manual_run(
    request: ManualRunRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Ручной запуск репрайсера (через Celery — не блокирует API)"""
    product_ids = _user_product_ids(db, current_user.id, request.seller_skus or None)

    if not product_ids:
        raise HTTPException(status_code=404, detail="No products found")

    from app.tasks.repricer_task import run_repricer_for_user
    run_repricer_for_user.delay(current_user.id, product_ids, "manual")

    return {
        "id": 0,
        "run_type": "manual",
        "total_products": len(product_ids),
        "updated_count": 0,
        "skipped_count": 0,
        "error_count": 0,
        "status": "queued",
        "started_at": None,
        "completed_at": None,
    }


@router.post("/run-sync", response_model=RepricerRunResponse)
async def manual_run_sync(
    request: ManualRunRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Ручной СИНХРОННЫЙ запуск репрайсера"""
    product_ids = _user_product_ids(db, current_user.id, request.seller_skus or None)

    if not product_ids:
        raise HTTPException(status_code=404, detail="No products found")

    engine = RepricerEngine(db)
    run = await engine.run_repricer(product_ids=product_ids, run_type="manual")

    changes = db.query(PriceChange).filter(PriceChange.run_id == run.id).all()
    change_responses = []
    for change in changes:
        product = db.query(Product).filter(Product.id == change.product_id).first()
        change_responses.append(PriceChangeResponse(
            id=change.id,
            product_id=change.product_id,
            seller_sku=product.seller_sku,
            product_name=product.name,
            old_seller_price=change.old_seller_price,
            old_final_price=change.old_final_price,
            new_seller_price=change.new_seller_price,
            new_final_price=change.new_final_price,
            reason_code=change.reason_code,
            reason_message=change.reason_message,
            changed_at=change.changed_at,
        ))

    response = RepricerRunResponse.model_validate(run)
    response.changes = change_responses
    return response


@router.post("/fetch-prices")
async def fetch_prices(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Получить текущие цены с WB и сохранить снимки (без изменения цен)."""
    product_ids = _user_product_ids(db, current_user.id)
    engine = RepricerEngine(db)
    result = await engine.fetch_price_snapshots(product_ids=product_ids)
    return result


@router.get("/runs", response_model=RepricerRunListResponse)
async def list_runs(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    run_type: Optional[str] = Query(None, regex="^(scheduled|manual)$"),
    status: Optional[str] = Query(None, regex="^(running|completed|failed)$"),
    limit: int = Query(50, ge=1, le=100),
):
    query = db.query(RepricerRun)
    if run_type:
        query = query.filter(RepricerRun.run_type == run_type)
    if status:
        query = query.filter(RepricerRun.status == status)
    query = query.order_by(desc(RepricerRun.started_at))
    total = query.count()
    runs = query.limit(limit).all()
    return RepricerRunListResponse(
        runs=[RepricerRunResponse.model_validate(r) for r in runs],
        total=total,
    )


@router.get("/runs/{run_id}", response_model=RepricerRunResponse)
async def get_run(
    run_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    include_changes: bool = Query(False),
):
    run = db.query(RepricerRun).filter(RepricerRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    response = RepricerRunResponse.model_validate(run)

    if include_changes:
        changes = db.query(PriceChange).filter(PriceChange.run_id == run_id).all()
        change_responses = []
        for change in changes:
            product = db.query(Product).filter(Product.id == change.product_id).first()
            change_responses.append(PriceChangeResponse(
                id=change.id,
                product_id=change.product_id,
                seller_sku=product.seller_sku,
                product_name=product.name,
                old_seller_price=change.old_seller_price,
                old_final_price=change.old_final_price,
                new_seller_price=change.new_seller_price,
                new_final_price=change.new_final_price,
                reason_code=change.reason_code,
                reason_message=change.reason_message,
                changed_at=change.changed_at,
            ))
        response.changes = change_responses

    return response
