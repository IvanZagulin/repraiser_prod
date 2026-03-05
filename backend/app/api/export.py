from typing import Annotated, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc
from datetime import datetime

from app.database import get_db
from app.models import Product, ProductConfig, PriceSnapshot, RepricerRun, PriceChange, Account
from app.schemas.auth import TokenData
from app.api.auth import get_current_user
from app.utils.excel import excel_exporter, excel_importer, ExcelImportError

router = APIRouter()


# ─────────────────── ШАБЛОНЫ ───────────────────

@router.get("/template/costs")
async def download_costs_template(
    current_user: Annotated[TokenData, Depends(get_current_user)]
):
    """Скачать пустой шаблон Excel для загрузки себестоимостей"""
    excel_file = excel_exporter.generate_costs_template()
    return StreamingResponse(
        excel_file,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=template_costs.xlsx"}
    )


@router.get("/template/products")
async def download_products_template(
    current_user: Annotated[TokenData, Depends(get_current_user)]
):
    """Скачать пустой шаблон Excel для импорта товаров"""
    excel_file = excel_exporter.generate_products_template()
    filename = "template_products.xlsx"
    return StreamingResponse(
        excel_file,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/template/strategy")
async def download_strategy_template(
    current_user: Annotated[TokenData, Depends(get_current_user)]
):
    """Скачать пустой шаблон Excel для настройки стратегий"""
    excel_file = excel_exporter.generate_strategy_template()
    filename = "template_strategies.xlsx"
    return StreamingResponse(
        excel_file,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# ─────────────────── ЭКСПОРТ ТОВАРОВ ───────────────────

@router.get("/products")
async def export_products(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[TokenData, Depends(get_current_user)],
    status_filter: Optional[str] = Query(
        None,
        description="Фильтр по статусу: active | inactive | unconfigured | all"
    ),
    strategy_filter: Optional[str] = Query(
        None,
        description="Фильтр по стратегии: rrp | competitor | all"
    ),
):
    """
    Экспорт товаров с конфигурацией и текущими ценами в Excel.
    Строки раскрашены по статусу репрайсера.
    Поддерживает фильтры по статусу и стратегии.
    """
    query = db.query(Product).join(
        ProductConfig, Product.id == ProductConfig.product_id, isouter=True
    )

    # Фильтр по статусу
    if status_filter == "active":
        query = query.filter(ProductConfig.is_active == True)
    elif status_filter == "inactive":
        query = query.filter(ProductConfig.is_active == False)
    elif status_filter == "unconfigured":
        query = query.filter(ProductConfig.id == None)

    # Фильтр по стратегии
    if strategy_filter in ("rrp", "competitor"):
        query = query.filter(ProductConfig.strategy == strategy_filter)

    products = query.all()

    products_data = []
    for product in products:
        # Последний снимок цены
        last_snapshot = (
            db.query(PriceSnapshot)
            .filter(PriceSnapshot.product_id == product.id)
            .order_by(desc(PriceSnapshot.snapshot_at))
            .first()
        )

        cfg = product.config
        cfg_dict = {}
        if cfg:
            cfg_dict = {
                "is_active":                   cfg.is_active,
                "strategy":                     cfg.strategy,
                "min_price":                    cfg.min_price,
                "max_price":                    cfg.max_price,
                "rrp_price":                    cfg.rrp_price,
                "rrp_tolerance":                cfg.rrp_tolerance,
                "rrp_consider_wallet":          cfg.rrp_consider_wallet,
                "competitor_nm_id":             cfg.competitor_nm_id,
                "competitor_offset_type":       cfg.competitor_offset_type,
                "competitor_offset_direction":  cfg.competitor_offset_direction,
                "competitor_offset_value":      cfg.competitor_offset_value,
            }

        products_data.append({
            "seller_sku":           product.seller_sku,
            "name":                 product.name,
            "nm_id":                product.nm_id,
            "account_name":         product.account.name if product.account else "",
            "config":               cfg_dict,
            "current_seller_price": last_snapshot.seller_price if last_snapshot else None,
            "current_final_price":  last_snapshot.final_price  if last_snapshot else None,
            "last_updated":         last_snapshot.snapshot_at.strftime('%Y-%m-%d %H:%M:%S') if last_snapshot else "",
        })

    excel_file = excel_exporter.export_products_with_config(products_data)
    filename = f"products_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

    return StreamingResponse(
        excel_file,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/history")
async def export_history(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[TokenData, Depends(get_current_user)],
    seller_skus: Optional[str] = Query(None, description="Comma-separated SKUs"),
    limit: int = Query(1000, ge=1, le=10000)
):
    """
    Экспорт истории цен в Excel
    
    Параметры:
    - seller_skus: Артикулы через запятую (если не указано - все товары)
    - limit: Максимум записей на товар
    """
    
    # Получаем товары
    query = db.query(Product)
    
    if seller_skus:
        sku_list = [sku.strip() for sku in seller_skus.split(',')]
        query = query.filter(Product.seller_sku.in_(sku_list))
    
    products = query.all()
    
    if not products:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No products found"
        )
    
    # Собираем данные
    history_data = []
    
    for product in products:
        snapshots = db.query(PriceSnapshot).filter(
            PriceSnapshot.product_id == product.id
        ).order_by(desc(PriceSnapshot.snapshot_at)).limit(limit).all()
        
        for snapshot in snapshots:
            history_data.append({
                'seller_sku': product.seller_sku,
                'product_name': product.name,
                'nm_id': product.nm_id,
                'seller_price': snapshot.seller_price,
                'final_price': snapshot.final_price,
                'spp': snapshot.spp,
                'in_stock': snapshot.total_quantity > 0 if snapshot.total_quantity else False,
                'is_in_promo': snapshot.is_in_promo,
                'snapshot_at': snapshot.snapshot_at.strftime('%Y-%m-%d %H:%M:%S')
            })
    
    # Генерируем Excel
    excel_file = excel_exporter.export_price_history(history_data)
    
    # Возвращаем файл
    filename = f"price_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    
    return StreamingResponse(
        excel_file,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/run/{run_id}")
async def export_run(
    run_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[TokenData, Depends(get_current_user)]
):
    """Экспорт прогона репрайсера в Excel"""
    
    # Получаем прогон
    run = db.query(RepricerRun).filter(RepricerRun.id == run_id).first()
    
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found"
        )
    
    # Получаем изменения
    changes = db.query(PriceChange).filter(
        PriceChange.run_id == run_id
    ).all()
    
    # Формируем данные
    run_data = {
        'id': run.id,
        'run_type': run.run_type,
        'status': run.status,
        'started_at': run.started_at.strftime('%Y-%m-%d %H:%M:%S'),
        'completed_at': run.completed_at.strftime('%Y-%m-%d %H:%M:%S') if run.completed_at else '',
        'total_products': run.total_products,
        'updated_count': run.updated_count,
        'skipped_count': run.skipped_count,
        'error_count': run.error_count
    }
    
    changes_data = []
    for change in changes:
        product = db.query(Product).filter(Product.id == change.product_id).first()
        changes_data.append({
            'seller_sku': product.seller_sku,
            'product_name': product.name,
            'old_seller_price': change.old_seller_price,
            'new_seller_price': change.new_seller_price,
            'old_final_price': change.old_final_price,
            'new_final_price': change.new_final_price,
            'reason_code': change.reason_code,
            'reason_message': change.reason_message,
            'changed_at': change.changed_at.strftime('%Y-%m-%d %H:%M:%S')
        })
    
    # Генерируем Excel
    excel_file = excel_exporter.export_repricer_run(run_data, changes_data)
    
    filename = f"repricer_run_{run_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    
    return StreamingResponse(
        excel_file,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.post("/import/costs")
async def import_costs(
    file: Annotated[UploadFile, File(description="Excel файл с себестоимостями")],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[TokenData, Depends(get_current_user)]
):
    """
    Импорт себестоимостей товаров из Excel.

    Формат: артикул продавца | себестоимость (₽)
    """
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be Excel (.xlsx or .xls)"
        )

    content = await file.read()

    try:
        costs_data = excel_importer.import_costs(content)
    except ExcelImportError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    results = {'updated': 0, 'errors': []}

    for item in costs_data:
        try:
            product = db.query(Product).filter(
                Product.seller_sku == item['seller_sku']
            ).first()
            if not product:
                results['errors'].append(f"Товар {item['seller_sku']!r} не найден в системе")
                continue
            product.cost_price = item['cost_price']
            results['updated'] += 1
        except Exception as e:
            db.rollback()
            results['errors'].append(f"{item['seller_sku']}: {str(e)}")

    db.commit()

    return {
        "success": True,
        "total": len(costs_data),
        "updated": results['updated'],
        "errors": results['errors'],
    }


@router.post("/import/strategy")
async def import_strategy(
    file: Annotated[UploadFile, File(description="Excel файл с конфигурациями")],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[TokenData, Depends(get_current_user)]
):
    """
    Импорт конфигураций стратегий из Excel
    
    Формат файла (заголовки в первой строке):
    - seller_sku | strategy | is_active | min_price | max_price | 
      rrp_price | rrp_tolerance | competitor_nm_id | competitor_offset_type |
      competitor_offset_direction | competitor_offset_value
    """
    
    # Проверка типа файла
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be Excel (.xlsx or .xls)"
        )
    
    # Читаем файл
    content = await file.read()
    
    try:
        configs = excel_importer.import_strategy_config(content)
    except ExcelImportError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    
    # Применяем конфигурации
    results = {
        'created': 0,
        'updated': 0,
        'errors': []
    }
    
    for config_data in configs:
        try:
            # Находим товар по seller_sku
            product = db.query(Product).filter(
                Product.seller_sku == config_data['seller_sku']
            ).first()
            
            if not product:
                results['errors'].append(f"Товар {config_data['seller_sku']} не найден")
                continue
            
            # Создаем или обновляем конфиг
            if product.config:
                # Обновляем существующий
                for key, value in config_data.items():
                    if key != 'seller_sku' and value is not None:
                        setattr(product.config, key, value)
                results['updated'] += 1
            else:
                # Создаем новый
                config = ProductConfig(
                    product_id=product.id,
                    **{k: v for k, v in config_data.items() if k != 'seller_sku'}
                )
                db.add(config)
                results['created'] += 1
            
            db.commit()
            
        except Exception as e:
            db.rollback()
            results['errors'].append(f"Ошибка для {config_data['seller_sku']}: {str(e)}")
    
    return {
        "success": True,
        "total": len(configs),
        "created": results['created'],
        "updated": results['updated'],
        "errors": results['errors']
    }


@router.post("/import/products")
async def import_products(
    file: Annotated[UploadFile, File(description="Excel файл с товарами")],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[TokenData, Depends(get_current_user)]
):
    """
    Импорт товаров из Excel.

    Обязательные колонки: nmID, ID аккаунта.
    Опциональные: артикул продавца, название (будут автоматически загружены из WB API).
    """
    from app.services.wb_api import create_wb_client
    from collections import defaultdict

    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be Excel (.xlsx or .xls)"
        )

    content = await file.read()

    try:
        products_data = excel_importer.import_products(content)
    except ExcelImportError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    results = {
        'created': 0,
        'skipped': 0,
        'errors': []
    }

    # Кэш WB клиентов по account_id
    wb_clients: dict = {}

    for product_data in products_data:
        nm_id      = product_data.get('nm_id')       # может быть None
        seller_sku = product_data.get('seller_sku')   # может быть None
        account_id = product_data['account_id']
        name       = product_data.get('name')

        # Метка для сообщений об ошибках
        label = f"nmID={nm_id}" if nm_id else f"артикул={seller_sku!r}"

        try:
            # Проверяем аккаунт (один раз на account_id через кэш)
            account = db.query(Account).filter(Account.id == account_id).first()
            if not account:
                results['errors'].append(f"{label}: аккаунт {account_id} не найден")
                continue

            # Инициализируем WB клиент для этого аккаунта
            if account_id not in wb_clients:
                wb_clients[account_id] = create_wb_client(account.wb_api_token)
            wb = wb_clients[account_id]

            image_url = None

            # ── Случай 1: есть только seller_sku → ищем nm_id через WB API ──
            if not nm_id and seller_sku:
                try:
                    card_info = await wb.get_card_info_by_sku(seller_sku)
                    if card_info:
                        nm_id = card_info.get("nm_id")
                        if not name:
                            name = card_info.get("name")
                        image_url = card_info.get("image_url")
                except Exception:
                    pass

                if not nm_id:
                    results['errors'].append(
                        f"артикул={seller_sku!r}: nmID не найден в WB — проверьте артикул и аккаунт"
                    )
                    continue

            # ── Случай 2: есть nm_id, но нет seller_sku или name → получаем из WB ──
            elif nm_id and (not seller_sku or not name):
                try:
                    card_info = await wb.get_card_info(nm_id)
                    if card_info:
                        if not seller_sku:
                            seller_sku = card_info.get("seller_sku")
                        if not name:
                            name = card_info.get("name")
                        image_url = card_info.get("image_url")
                except Exception:
                    pass

            # Проверяем существование по nm_id + account_id
            existing = db.query(Product).filter(
                Product.nm_id == nm_id,
                Product.account_id == account_id,
            ).first()

            if existing:
                results['skipped'] += 1
                continue

            # Финальные заглушки
            if not seller_sku:
                seller_sku = str(nm_id)
            if not name:
                name = f"Товар {nm_id}"

            product = Product(
                nm_id=nm_id,
                account_id=account_id,
                seller_sku=seller_sku,
                name=name,
                image_url=image_url,
            )
            db.add(product)
            db.commit()
            results['created'] += 1

        except Exception as e:
            db.rollback()
            results['errors'].append(f"{label}: {str(e)}")

    return {
        "success": True,
        "total": len(products_data),
        "created": results['created'],
        "skipped": results['skipped'],
        "errors": results['errors']
    }
