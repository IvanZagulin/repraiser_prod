from typing import Annotated, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database import get_db
from app.models import Product, RepricerRun, PriceChange
from app.schemas.repricer import (
    ManualRunRequest, RepricerRunResponse, RepricerRunListResponse,
    PriceChangeResponse
)
from app.schemas.auth import TokenData
from app.api.auth import get_current_user
from app.services.repricer_engine import RepricerEngine

router = APIRouter()


async def run_repricer_task(
    product_ids: Optional[List[int]],
    run_type: str,
    db: Session
):
    """Background task для запуска репрайсера"""
    engine = RepricerEngine(db)
    await engine.run_repricer(product_ids=product_ids, run_type=run_type)


@router.post("/run", response_model=RepricerRunResponse)
async def manual_run(
    request: ManualRunRequest,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[TokenData, Depends(get_current_user)]
):
    """
    Ручной запуск репрайсера для указанных артикулов
    
    Запускается в фоне, возвращает немедленно с ID прогона
    """
    
    # Находим товары по seller_sku (если не указаны — все товары)
    if request.seller_skus:
        products = db.query(Product).filter(
            Product.seller_sku.in_(request.seller_skus)
        ).all()
    else:
        products = db.query(Product).all()

    if not products:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No products found"
        )

    product_ids = [p.id for p in products]

    # Запускаем репрайсер в фоне
    background_tasks.add_task(
        run_repricer_task,
        product_ids=product_ids,
        run_type="manual",
        db=db
    )
    
    # Возвращаем пустой ответ (прогон еще не создан)
    return {
        "id": 0,
        "run_type": "manual",
        "total_products": len(products),
        "updated_count": 0,
        "skipped_count": 0,
        "error_count": 0,
        "status": "queued",
        "started_at": None,
        "completed_at": None
    }


@router.post("/run-sync", response_model=RepricerRunResponse)
async def manual_run_sync(
    request: ManualRunRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[TokenData, Depends(get_current_user)]
):
    """
    Ручной СИНХРОННЫЙ запуск репрайсера
    
    Ожидает завершения и возвращает полный результат
    Используется для единичных товаров из UI
    """
    
    # Находим товары (если seller_skus не указаны — все товары)
    if request.seller_skus:
        products = db.query(Product).filter(
            Product.seller_sku.in_(request.seller_skus)
        ).all()
    else:
        products = db.query(Product).all()

    if not products:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No products found"
        )

    product_ids = [p.id for p in products]

    # Запускаем репрайсер синхронно
    engine = RepricerEngine(db)
    run = await engine.run_repricer(product_ids=product_ids, run_type="manual")
    
    # Получаем детали изменений
    changes = db.query(PriceChange).filter(
        PriceChange.run_id == run.id
    ).all()
    
    change_responses = []
    for change in changes:
        product = db.query(Product).filter(Product.id == change.product_id).first()
        change_responses.append(
            PriceChangeResponse(
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
                changed_at=change.changed_at
            )
        )
    
    response = RepricerRunResponse.model_validate(run)
    response.changes = change_responses
    
    return response


@router.post("/fetch-prices")
async def fetch_prices(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[TokenData, Depends(get_current_user)]
):
    """
    Получить текущие цены с WB и сохранить снимки для всех товаров.
    Не меняет цены. Работает для товаров без стратегии (статус «Не настроен»).
    """
    engine = RepricerEngine(db)
    result = await engine.fetch_price_snapshots()
    return result


@router.get("/runs", response_model=RepricerRunListResponse)
async def list_runs(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[TokenData, Depends(get_current_user)],
    run_type: Optional[str] = Query(None, regex="^(scheduled|manual)$"),
    status: Optional[str] = Query(None, regex="^(running|completed|failed)$"),
    limit: int = Query(50, ge=1, le=100)
):
    """Получить список прогонов репрайсера"""
    
    query = db.query(RepricerRun)
    
    # Фильтры
    if run_type:
        query = query.filter(RepricerRun.run_type == run_type)
    
    if status:
        query = query.filter(RepricerRun.status == status)
    
    # Сортировка по времени (новые первые)
    query = query.order_by(desc(RepricerRun.started_at))
    
    total = query.count()
    runs = query.limit(limit).all()
    
    return RepricerRunListResponse(
        runs=[RepricerRunResponse.model_validate(r) for r in runs],
        total=total
    )


@router.get("/runs/{run_id}", response_model=RepricerRunResponse)
async def get_run(
    run_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[TokenData, Depends(get_current_user)],
    include_changes: bool = Query(False, description="Включить детали изменений")
):
    """Получить прогон по ID"""
    
    run = db.query(RepricerRun).filter(RepricerRun.id == run_id).first()
    
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found"
        )
    
    response = RepricerRunResponse.model_validate(run)
    
    # Если запрошены детали изменений
    if include_changes:
        changes = db.query(PriceChange).filter(
            PriceChange.run_id == run_id
        ).all()
        
        change_responses = []
        for change in changes:
            product = db.query(Product).filter(Product.id == change.product_id).first()
            change_responses.append(
                PriceChangeResponse(
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
                    changed_at=change.changed_at
                )
            )
        
        response.changes = change_responses
    
    return response
