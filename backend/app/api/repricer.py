from typing import Annotated, List, Optional
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from slowapi import Limiter
import logging

from app.database import get_db
from app.models import Product, RepricerRun, PriceChange, Account
from app.models.user import User
from app.schemas.repricer import (
    ManualRunRequest, RepricerRunResponse, RepricerRunListResponse,
    PriceChangeResponse
)
from app.api.auth import get_current_user

logger = logging.getLogger(__name__)
from app.services.repricer_engine import RepricerEngine
from app.services.activity_logger import log_activity
from app.utils import get_user_id_key

router = APIRouter()

# Per-user rate limiter: один пользователь — один ключ вне зависимости от IP
limiter = Limiter(key_func=get_user_id_key)


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


def _build_change_responses(db: Session, changes: list) -> list:
    """Строит список PriceChangeResponse за 1 запрос вместо N (fix N+1)."""
    if not changes:
        return []
    product_ids = list({c.product_id for c in changes})
    product_map = {
        p.id: p for p in db.query(Product).filter(Product.id.in_(product_ids)).all()
    }
    result = []
    for change in changes:
        product = product_map.get(change.product_id)
        result.append(PriceChangeResponse(
            id=change.id,
            product_id=change.product_id,
            seller_sku=product.seller_sku if product else None,
            product_name=product.name if product else None,
            old_seller_price=change.old_seller_price,
            old_final_price=change.old_final_price,
            new_seller_price=change.new_seller_price,
            new_final_price=change.new_final_price,
            reason_code=change.reason_code,
            reason_message=change.reason_message,
            changed_at=change.changed_at,
        ))
    return result


@router.post("/run", response_model=RepricerRunResponse)
@limiter.limit("5/minute")
async def manual_run(
    request: Request,
    body: ManualRunRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Ручной запуск репрайсера (через Celery — не блокирует API)"""
    product_ids = _user_product_ids(db, current_user.id, body.seller_skus or None)
    priority = bool(body.seller_skus)

    if not product_ids:
        raise HTTPException(status_code=404, detail="No products found")

    # Создаём запись в БД заранее — чтобы вернуть реальный ID на фронт (fix: id=0)
    run = RepricerRun(
        user_id=current_user.id,
        run_type="manual",
        status="queued",
        total_products=len(product_ids),
        updated_count=0,
        skipped_count=0,
        error_count=0,
        error_message=None,
        started_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        log_activity(db, current_user.id, "repricer_run", details={
            "run_id": run.id,
            "run_type": "manual",
            "dry_run": body.dry_run,
            "priority": priority,
        })
        db.commit()
    except Exception as e:
        logger.warning(f"Failed to log activity 'repricer_run': {e}")

    from app.tasks.repricer_task import run_repricer_for_user
    run_repricer_for_user.apply_async(
        args=[
            current_user.id,
            product_ids,
            "manual",
            run.id,
            body.dry_run,
        ],
        kwargs={"priority": priority},
        priority=0 if priority else 5,
    )

    return RepricerRunResponse.model_validate(run)


@router.post("/run-sync", response_model=RepricerRunResponse)
@limiter.limit("5/minute")
async def manual_run_sync(
    request: Request,
    body: ManualRunRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Ручной СИНХРОННЫЙ запуск репрайсера"""
    product_ids = _user_product_ids(db, current_user.id, body.seller_skus or None)

    if not product_ids:
        raise HTTPException(status_code=404, detail="No products found")

    # Тот же run-лок, что и у Celery-задачи: без него синхронный запуск идёт
    # параллельно с автоматическим прогоном (инцидент 2026-06-10).
    from app.redis_client import acquire_run_lock, new_run_lock_owner, release_run_lock
    lock_owner = new_run_lock_owner("run-sync")
    if not acquire_run_lock(current_user.id, owner=lock_owner):
        raise HTTPException(status_code=409, detail="Прогон репрайсера уже идёт — дождитесь завершения")

    try:
        engine = RepricerEngine(db, user_id=current_user.id)
        run = await engine.run_repricer(
            product_ids=product_ids,
            run_type="manual",
            dry_run=body.dry_run,
        )
    finally:
        release_run_lock(current_user.id, owner=lock_owner)

    try:
        log_activity(db, current_user.id, "repricer_run", details={"run_id": run.id, "run_type": "manual", "dry_run": body.dry_run, "status": run.status})
        db.commit()
    except Exception as e:
        logger.warning(f"Failed to log activity 'repricer_run': {e}")

    changes = db.query(PriceChange).filter(PriceChange.run_id == run.id).all()
    response = RepricerRunResponse.model_validate(run)
    response.changes = _build_change_responses(db, changes)
    return response


@router.post("/fetch-prices")
@limiter.limit("10/minute")
async def fetch_prices(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Получить текущие цены с WB и сохранить снимки (без изменения цен)."""
    product_ids = _user_product_ids(db, current_user.id)
    engine = RepricerEngine(db, user_id=current_user.id)
    result = await engine.fetch_price_snapshots(product_ids=product_ids)
    return result


@router.get("/runs", response_model=RepricerRunListResponse)
async def list_runs(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    run_type: Optional[str] = Query(None, regex="^(scheduled|manual)$"),
    status: Optional[str] = Query(None, regex="^(running|completed|failed|queued)$"),
    limit: int = Query(50, ge=1, le=10000),
):
    # Фильтруем по user_id — каждый видит только свои прогоны
    base_query = db.query(RepricerRun).filter(RepricerRun.user_id == current_user.id)
    if run_type:
        base_query = base_query.filter(RepricerRun.run_type == run_type)
    if status:
        base_query = base_query.filter(RepricerRun.status == status)

    # Агрегаты по всем запускам (до пагинации)
    agg = db.query(
        func.count(RepricerRun.id),
        func.coalesce(func.sum(RepricerRun.updated_count), 0),
        func.count(RepricerRun.id).filter(RepricerRun.status == 'completed'),
    ).filter(RepricerRun.user_id == current_user.id).one()
    total_all, total_updated, total_completed = agg

    query = base_query.order_by(desc(RepricerRun.started_at))
    total = query.count()
    runs = query.limit(limit).all()
    return RepricerRunListResponse(
        runs=[RepricerRunResponse.model_validate(r) for r in runs],
        total=total,
        total_updated=int(total_updated),
        total_completed=int(total_completed),
    )


@router.get("/runs/{run_id}", response_model=RepricerRunResponse)
async def get_run(
    run_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    include_changes: bool = Query(False),
):
    # IDOR fix: прогон должен принадлежать текущему пользователю
    run = db.query(RepricerRun).filter(
        RepricerRun.id == run_id,
        RepricerRun.user_id == current_user.id,
    ).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    response = RepricerRunResponse.model_validate(run)

    if include_changes:
        changes = db.query(PriceChange).filter(PriceChange.run_id == run_id).all()
        response.changes = _build_change_responses(db, changes)

    return response
