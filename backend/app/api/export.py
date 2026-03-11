from typing import Annotated, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status, UploadFile, File, Query
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from sqlalchemy import desc
from datetime import datetime
from slowapi import Limiter

from app.database import get_db
from app.models import Product, ProductConfig, PriceSnapshot, RepricerRun, PriceChange, Account
from app.models.user import User
from app.api.auth import get_current_user
from app.utils import get_user_id_key
from app.utils.excel import excel_exporter, excel_importer, ExcelImportError

router = APIRouter()

# Per-user rate limiter для тяжёлых import-эндпоинтов
limiter = Limiter(key_func=get_user_id_key)

MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 МБ


# ─────────────────── ШАБЛОНЫ ───────────────────

@router.get("/template/costs")
async def download_costs_template(
    current_user: Annotated[User, Depends(get_current_user)]
):
    """Скачать пустой шаблон Excel для загрузки себестоимостей"""
    excel_file = await run_in_threadpool(excel_exporter.generate_costs_template)
    return Response(
        content=excel_file.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename="template_costs.xlsx""}
    )


@router.get("/template/products")
async def download_products_template(
    current_user: Annotated[User, Depends(get_current_user)]
):
    """Скачать пустой шаблон Excel для импорта товаров"""
    excel_file = await run_in_threadpool(excel_exporter.generate_products_template)
    return Response(
        content=excel_file.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename="template_products.xlsx""}
    )


@router.get("/template/strategy")
async def download_strategy_template(
    current_user: Annotated[User, Depends(get_current_user)]
):
    """Скачать пустой шаблон Excel для настройки стратегий"""
    excel_file = await run_in_threadpool(excel_exporter.generate_strategy_template)
    return Response(
        content=excel_file.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename="template_strategies.xlsx""}
    )


# ─────────────────── ЭКСПОРТ ТОВАРОВ ───────────────────

@router.get("/products")
async def export_products(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
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
    # IDOR fix: фильтруем только товары текущего пользователя
    query = (
        db.query(Product)
        .join(Account, Product.account_id == Account.id)
        .filter(Account.user_id == current_user.id)
        .join(ProductConfig, Product.id == ProductConfig.product_id, isouter=True)
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

    # Batch-загрузка последних снимков цен (fix N+1)
    product_ids = [p.id for p in products]
    last_snapshots: dict = {}
    if product_ids:
        # Для каждого product_id берём последний снимок через subquery
        from sqlalchemy import func
        subq = (
            db.query(
                PriceSnapshot.product_id,
                func.max(PriceSnapshot.snapshot_at).label("max_at")
            )
            .filter(PriceSnapshot.product_id.in_(product_ids))
            .group_by(PriceSnapshot.product_id)
            .subquery()
        )
        rows = (
            db.query(PriceSnapshot)
            .join(subq, (PriceSnapshot.product_id == subq.c.product_id) &
                        (PriceSnapshot.snapshot_at == subq.c.max_at))
            .all()
        )
        last_snapshots = {s.product_id: s for s in rows}

    products_data = []
    for product in products:
        last_snapshot = last_snapshots.get(product.id)

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

    excel_file = await run_in_threadpool(excel_exporter.export_products_with_config, products_data)
    filename = f"products_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

    return Response(
        content=excel_file.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.get("/history")
async def export_history(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    seller_skus: Optional[str] = Query(None, description="Comma-separated SKUs"),
    limit: int = Query(1000, ge=1, le=10000)
):
    """
    Экспорт истории цен в Excel

    Параметры:
    - seller_skus: Артикулы через запятую (если не указано - все товары)
    - limit: Максимум записей на товар
    """
    # IDOR fix: только товары текущего пользователя
    query = (
        db.query(Product)
        .join(Account, Product.account_id == Account.id)
        .filter(Account.user_id == current_user.id)
    )

    if seller_skus:
        sku_list = [sku.strip() for sku in seller_skus.split(',')]
        query = query.filter(Product.seller_sku.in_(sku_list))

    products = query.all()

    if not products:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No products found"
        )

    # Batch-загрузка снимков (fix N+1)
    product_ids = [p.id for p in products]
    from sqlalchemy import and_
    all_snapshots = (
        db.query(PriceSnapshot)
        .filter(PriceSnapshot.product_id.in_(product_ids))
        .order_by(desc(PriceSnapshot.snapshot_at))
        .all()
    )
    # Группируем по product_id с лимитом
    from collections import defaultdict
    snap_by_product: dict = defaultdict(list)
    for s in all_snapshots:
        if len(snap_by_product[s.product_id]) < limit:
            snap_by_product[s.product_id].append(s)

    product_map = {p.id: p for p in products}

    history_data = []
    for pid, snapshots in snap_by_product.items():
        product = product_map[pid]
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

    excel_file = await run_in_threadpool(excel_exporter.export_price_history, history_data)
    filename = f"price_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

    return Response(
        content=excel_file.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.get("/run/{run_id}")
async def export_run(
    run_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)]
):
    """Экспорт прогона репрайсера в Excel"""

    # IDOR fix: фильтруем прогон сразу по user_id — чужой прогон вернёт 404
    run = db.query(RepricerRun).filter(
        RepricerRun.id == run_id,
        RepricerRun.user_id == current_user.id,
    ).first()

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

    # Batch-загрузка товаров (fix N+1)
    product_ids = list({c.product_id for c in changes})
    product_map = {
        p.id: p for p in db.query(Product).filter(Product.id.in_(product_ids)).all()
    } if product_ids else {}

    changes_data = []
    for change in changes:
        product = product_map.get(change.product_id)
        changes_data.append({
            'seller_sku': product.seller_sku if product else '',
            'product_name': product.name if product else '',
            'old_seller_price': change.old_seller_price,
            'new_seller_price': change.new_seller_price,
            'old_final_price': change.old_final_price,
            'new_final_price': change.new_final_price,
            'reason_code': change.reason_code,
            'reason_message': change.reason_message,
            'changed_at': change.changed_at.strftime('%Y-%m-%d %H:%M:%S')
        })

    excel_file = await run_in_threadpool(excel_exporter.export_repricer_run, run_data, changes_data)
    filename = f"repricer_run_{run_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

    return Response(
        content=excel_file.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.post("/import/costs")
@limiter.limit("10/minute")
async def import_costs(
    request: Request,
    file: Annotated[UploadFile, File(description="Excel файл с себестоимостями")],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)]
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
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail="Файл слишком большой (максимум 10 МБ)")

    try:
        costs_data = await run_in_threadpool(excel_importer.import_costs, content)
    except ExcelImportError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    results = {'updated': 0, 'errors': []}

    for item in costs_data:
        # IDOR fix: ищем товар только среди товаров текущего пользователя
        product = (
            db.query(Product)
            .join(Account, Product.account_id == Account.id)
            .filter(
                Product.seller_sku == item['seller_sku'],
                Account.user_id == current_user.id
            )
            .first()
        )
        if not product:
            results['errors'].append(f"Товар {item['seller_sku']!r} не найден в системе")
            continue
        product.cost_price = item['cost_price']
        results['updated'] += 1

    # Один коммит в конце (fix: commit в цикле)
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Ошибка сохранения данных")

    return {
        "success": True,
        "total": len(costs_data),
        "updated": results['updated'],
        "errors": results['errors'],
    }


@router.post("/import/strategy")
@limiter.limit("10/minute")
async def import_strategy(
    request: Request,
    file: Annotated[UploadFile, File(description="Excel файл с конфигурациями")],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)]
):
    """
    Импорт конфигураций стратегий из Excel

    Формат файла (заголовки в первой строке):
    - seller_sku | strategy | is_active | min_price | max_price |
      rrp_price | rrp_tolerance | competitor_nm_id | competitor_offset_type |
      competitor_offset_direction | competitor_offset_value
    """

    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be Excel (.xlsx or .xls)"
        )

    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail="Файл слишком большой (максимум 10 МБ)")

    try:
        configs = await run_in_threadpool(excel_importer.import_strategy_config, content)
    except ExcelImportError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    results = {
        'created': 0,
        'updated': 0,
        'errors': []
    }

    for config_data in configs:
        # IDOR fix: ищем товар только среди товаров текущего пользователя
        product = (
            db.query(Product)
            .join(Account, Product.account_id == Account.id)
            .filter(
                Product.seller_sku == config_data['seller_sku'],
                Account.user_id == current_user.id
            )
            .first()
        )

        if not product:
            results['errors'].append(f"Товар {config_data['seller_sku']} не найден")
            continue

        if product.config:
            for key, value in config_data.items():
                if key != 'seller_sku' and value is not None:
                    setattr(product.config, key, value)
            results['updated'] += 1
        else:
            config = ProductConfig(
                product_id=product.id,
                **{k: v for k, v in config_data.items() if k != 'seller_sku'}
            )
            db.add(config)
            results['created'] += 1

    # Один коммит в конце (fix: commit в цикле)
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Ошибка сохранения данных")

    return {
        "success": True,
        "total": len(configs),
        "created": results['created'],
        "updated": results['updated'],
        "errors": results['errors']
    }


@router.post("/import/products")
@limiter.limit("10/minute")
async def import_products(
    request: Request,
    file: Annotated[UploadFile, File(description="Excel файл с товарами")],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)]
):
    """
    Импорт товаров из Excel.
    nmID и vendorCode берутся из локального каталога WB (wb_catalog),
    который синхронизируется при добавлении аккаунта и раз в неделю.

    Обязательные колонки: артикул продавца (B) или nmID (A), ID аккаунта (C).
    """
    from app.models.wb_catalog import WBCatalog

    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be Excel (.xlsx or .xls)"
        )

    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail="Файл слишком большой (максимум 10 МБ)")

    try:
        products_data = await run_in_threadpool(excel_importer.import_products, content)
    except ExcelImportError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    results = {'created': 0, 'skipped': 0, 'errors': []}

    # Кэш аккаунтов (IDOR-защита)
    account_cache: dict = {}

    def _get_account(account_id: int):
        if account_id not in account_cache:
            account_cache[account_id] = (
                db.query(Account)
                .filter(Account.id == account_id, Account.user_id == current_user.id)
                .first()
            )
        return account_cache[account_id]

    # Предзагружаем каталог для всех уникальных аккаунтов из файла
    account_ids_in_file = {pd.get('account_id') for pd in products_data if pd.get('account_id')}
    # catalog[account_id] = {vendor_code: nm_id, ...}  и  {nm_id: vendor_code, ...}
    catalog_by_sku: dict = {}   # account_id → {vendor_code: nm_id}
    catalog_by_nm:  dict = {}   # account_id → {nm_id: vendor_code}
    catalog_image:  dict = {}   # account_id → {nm_id: image_url}

    for acc_id in account_ids_in_file:
        if not _get_account(acc_id):
            continue
        rows = db.query(WBCatalog).filter(WBCatalog.account_id == acc_id).all()
        catalog_by_sku[acc_id] = {r.vendor_code: r.nm_id  for r in rows}
        catalog_by_nm[acc_id]  = {r.nm_id: r.vendor_code  for r in rows}
        catalog_image[acc_id]  = {r.nm_id: r.image_url    for r in rows if r.image_url}

    # ── Создаём товары ─────────────────────────────────────────────────────────
    for product_data in products_data:
        nm_id      = product_data.get('nm_id')
        seller_sku = product_data.get('seller_sku')
        account_id = product_data.get('account_id')
        name       = product_data.get('name') or ''

        label = f"nmID={nm_id}" if nm_id else f"артикул={seller_sku!r}"

        if not account_id:
            results['errors'].append(f"{label}: не указан ID аккаунта")
            continue

        account = _get_account(account_id)
        if not account:
            results['errors'].append(
                f"{label}: аккаунт {account_id} не найден или не принадлежит вам"
            )
            continue

        # Случай 1: есть vendorCode, нет nmID → ищем в каталоге
        if not nm_id and seller_sku:
            nm_id = catalog_by_sku.get(account_id, {}).get(seller_sku)
            if not nm_id:
                results['errors'].append(
                    f"артикул={seller_sku!r}: не найден в каталоге WB. "
                    f"Каталог обновляется автоматически — повторите импорт через несколько минут."
                )
                continue

        # Случай 2: есть nmID, нет vendorCode → ищем в каталоге
        if nm_id and not seller_sku:
            seller_sku = catalog_by_nm.get(account_id, {}).get(nm_id) or str(nm_id)

        # Дубликат?
        existing = db.query(Product).filter(
            Product.nm_id == nm_id,
            Product.account_id == account_id,
        ).first()
        if existing:
            results['skipped'] += 1
            continue

        db.add(Product(
            nm_id=nm_id,
            account_id=account_id,
            seller_sku=seller_sku or str(nm_id),
            name=name or f"Товар {nm_id}",
            image_url=catalog_image.get(account_id, {}).get(nm_id),  # фото из каталога
        ))
        results['created'] += 1

    if results['created'] > 0:
        try:
            db.commit()
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail="Ошибка сохранения данных")

    return {
        "success": True,
        "total": len(products_data),
        "created": results['created'],
        "skipped": results['skipped'],
        "errors": results['errors'],
        "message": (
            "Товары добавлены. Цены подтянутся автоматически в течение нескольких минут."
            if results['created'] > 0 else None
        ),
    }
