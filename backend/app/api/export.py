import logging
from typing import Annotated, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status, UploadFile, File, Query
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from sqlalchemy import desc
from datetime import datetime, timedelta, timezone
from slowapi import Limiter

from app.database import get_db
from app.models import Product, ProductConfig, PriceSnapshot, RepricerRun, PriceChange, Account, WBCatalog
from app.models.rrp_spp_tier import RRPSPPTier
from app.models.user import User
from app.api.auth import get_current_user
from app.utils import get_user_id_key
from app.utils.excel import excel_exporter, excel_importer, ExcelImportError

logger = logging.getLogger(__name__)

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
        headers={"Content-Disposition": 'attachment; filename="template_costs.xlsx"'}
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
        headers={"Content-Disposition": 'attachment; filename="template_products.xlsx"'}
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
        headers={"Content-Disposition": 'attachment; filename="template_strategies.xlsx"'}
    )


@router.get("/template/characteristics")
async def download_characteristics_template(
    current_user: Annotated[User, Depends(get_current_user)]
):
    """Скачать пустой шаблон Excel для загрузки характеристик товаров"""
    excel_file = await run_in_threadpool(excel_exporter.generate_characteristics_template)
    return Response(
        content=excel_file.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="template_characteristics.xlsx"'}
    )


# ─────────────────── ЭКСПОРТ ТОВАРОВ ───────────────────



@router.get("/template/rrp-spp")
async def download_rrp_spp_template(
    current_user: Annotated[User, Depends(get_current_user)]
):
    """Скачать пустой шаблон Excel для загрузки РРЦ по уровням СПП"""
    excel_file = await run_in_threadpool(excel_exporter.generate_rrp_spp_template)
    return Response(
        content=excel_file.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="template_rrp_spp.xlsx"'}
    )

@router.get("/products")
@limiter.limit("10/minute")
async def export_products(
    request: Request,
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
    from sqlalchemy import func
    from sqlalchemy.orm import joinedload

    # IDOR fix + eager load (устраняет N+1 на account и config)
    query = (
        db.query(Product)
        .join(Account, Product.account_id == Account.id)
        .filter(Account.user_id == current_user.id)
        .join(ProductConfig, Product.id == ProductConfig.product_id, isouter=True)
        .options(joinedload(Product.account), joinedload(Product.config))
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

    # Один JOIN-запрос вместо батчевых IN-списков
    last_snapshots: dict = {}
    if products:
        latest_snap_subq = (
            db.query(
                PriceSnapshot.product_id,
                func.max(PriceSnapshot.snapshot_at).label("max_at")
            )
            .join(Product, PriceSnapshot.product_id == Product.id)
            .join(Account, Product.account_id == Account.id)
            .filter(Account.user_id == current_user.id)
            .group_by(PriceSnapshot.product_id)
            .subquery()
        )
        snap_rows = (
            db.query(PriceSnapshot)
            .join(latest_snap_subq,
                  (PriceSnapshot.product_id == latest_snap_subq.c.product_id) &
                  (PriceSnapshot.snapshot_at == latest_snap_subq.c.max_at))
            .all()
        )
        last_snapshots = {s.product_id: s for s in snap_rows}
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
            "brand":                product.brand,
            "nm_id":                product.nm_id,
            "account_id":         product.account.name if product.account else "",
            "config":               cfg_dict,
            "current_seller_price": last_snapshot.seller_price if last_snapshot else None,
            "current_final_price":  last_snapshot.final_price  if last_snapshot else None,
            "last_updated":         (last_snapshot.snapshot_at + timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S') if last_snapshot else "",
        })

    excel_file = await run_in_threadpool(excel_exporter.export_products_with_config, products_data)
    filename = f"products_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.xlsx"

    return Response(
        content=excel_file.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.get("/history")
@limiter.limit("10/minute")
async def export_history(
    request: Request,
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

    # Batch-загрузка снимков (fix N+1) с ограничением общего кол-ва строк
    # чтобы не тянуть миллионы снимков в память при большом историческом объёме
    product_ids = [p.id for p in products]
    MAX_TOTAL_SNAPSHOTS = limit * len(product_ids)
    if MAX_TOTAL_SNAPSHOTS > 500_000:
        MAX_TOTAL_SNAPSHOTS = 500_000  # hard cap: защита от OOM
    from sqlalchemy import and_
    all_snapshots = (
        db.query(PriceSnapshot)
        .filter(PriceSnapshot.product_id.in_(product_ids))
        .order_by(desc(PriceSnapshot.snapshot_at))
        .limit(MAX_TOTAL_SNAPSHOTS)
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
                'snapshot_at': (snapshot.snapshot_at + timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S')
            })

    excel_file = await run_in_threadpool(excel_exporter.export_price_history, history_data)
    filename = f"price_history_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.xlsx"

    return Response(
        content=excel_file.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.get("/run/{run_id}")
@limiter.limit("20/minute")
async def export_run(
    request: Request,
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
        'started_at': (run.started_at + timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S'),
        'completed_at': (run.completed_at + timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S') if run.completed_at else '',
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
            'changed_at': (change.changed_at + timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S')
        })

    excel_file = await run_in_threadpool(excel_exporter.export_repricer_run, run_data, changes_data)
    filename = f"repricer_run_{run_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.xlsx"

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
        # Обновляем все товары с этим артикулом у пользователя (по всем кабинетам)
        matched = (
            db.query(Product)
            .join(Account, Product.account_id == Account.id)
            .filter(
                Product.seller_sku == item['seller_sku'],
                Account.user_id == current_user.id
            )
            .all()
        )
        if not matched:
            results['errors'].append(f"Товар {item['seller_sku']!r} не найден в системе")
            continue
        for product in matched:
            product.cost_price = item['cost_price']
        results['updated'] += len(matched)

    # Один коммит в конце (fix: commit в цикле)
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.exception("import_costs commit failed")
        raise HTTPException(status_code=500, detail=f"Ошибка сохранения данных: {e}")

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

    processed_product_ids: set = set()

    for config_data in configs:
        # IDOR fix: ищем товар только среди товаров текущего пользователя
        q = (
            db.query(Product)
            .join(Account, Product.account_id == Account.id)
            .filter(
                Product.seller_sku == config_data['seller_sku'],
                Account.user_id == current_user.id
            )
        )
        # Если указан кабинет — фильтруем по нему (поддерживаем имя или числовой ID)
        account_name_or_id = config_data.get('account_name_or_id')
        account_id = None
        if account_name_or_id:
            try:
                account_id = int(account_name_or_id)
                q = q.filter(Account.id == account_id)
            except (ValueError, TypeError):
                q = q.filter(Account.name == account_name_or_id)
                account_id = account_name_or_id  # используется ниже как признак что фильтр задан
        products_found = q.all()

        if not products_found:
            # Try to auto-create from wb_catalog
            catalog_entry = (
                db.query(WBCatalog)
                .join(Account, WBCatalog.account_id == Account.id)
                .filter(
                    WBCatalog.vendor_code == config_data['seller_sku'],
                    Account.user_id == current_user.id,
                )
            )
            if account_id:
                try:
                    catalog_entry = catalog_entry.filter(Account.id == int(account_id))
                except (ValueError, TypeError):
                    catalog_entry = catalog_entry.filter(Account.name == str(account_id))
            catalog_entry = catalog_entry.first()
            if catalog_entry:
                new_product = Product(
                    account_id=catalog_entry.account_id,
                    nm_id=catalog_entry.nm_id,
                    seller_sku=catalog_entry.vendor_code,
                    name=catalog_entry.name if catalog_entry.name and catalog_entry.name != catalog_entry.vendor_code else None,
                    image_url=catalog_entry.image_url,
                    subject_name=catalog_entry.subject_name,
                )
                db.add(new_product)
                db.flush()  # get id without commit
                products_found = [new_product]
            else:
                results['errors'].append(f"Товар {config_data['seller_sku']} не найден (нет в каталоге WB)")
                continue
        if len(products_found) > 1 and not account_id:
            acc_names = ', '.join(p.account.name for p in products_found)
            results['errors'].append(
                f"Артикул {config_data['seller_sku']} найден в нескольких кабинетах ({acc_names}). "
                f"Укажите колонку 'Кабинет (название)' в файле."
            )
            continue
        product = products_found[0]

        if product.id in processed_product_ids:
            # Дубль — применяем, если РРЦ в этой строке выше уже обработанного
            new_rrp = config_data.get('rrp_price')
            if new_rrp is not None:
                db.flush()
                dup_config = db.query(ProductConfig).filter(
                    ProductConfig.product_id == product.id
                ).first()
                if dup_config and (dup_config.rrp_price is None or new_rrp > dup_config.rrp_price):
                    for key, value in config_data.items():
                        if key not in ('seller_sku', 'account_id', 'account_name_or_id') and value is not None:
                            setattr(dup_config, key, value)
            continue
        processed_product_ids.add(product.id)

        # flush перед запросом — autoflush=False, иначе pending-объекты не видны
        db.flush()
        existing_config = db.query(ProductConfig).filter(
            ProductConfig.product_id == product.id
        ).first()

        if existing_config:
            for key, value in config_data.items():
                if key not in ('seller_sku', 'account_id', 'account_name_or_id') and value is not None:
                    setattr(existing_config, key, value)
            results['updated'] += 1
        else:
            if not config_data.get('strategy'):
                if config_data.get('is_active', True):
                    results['errors'].append(
                        f"Товар {config_data['seller_sku']}: strategy обязателен для новой активной стратегии"
                    )
                    continue
                config_data['strategy'] = 'rrp'
            if config_data.get('min_price') is None:
                if config_data.get('is_active', True):
                    results['errors'].append(
                        f"Товар {config_data['seller_sku']}: min_price обязателен для новой активной стратегии"
                    )
                    continue
                config_data['min_price'] = 0
            config = ProductConfig(
                product_id=product.id,
                **{k: v for k, v in config_data.items() if k not in ('seller_sku', 'account_id', 'account_name_or_id')}
            )
            db.add(config)
            results['created'] += 1

    # Один коммит в конце (fix: commit в цикле)
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.exception("import_strategy commit failed")
        raise HTTPException(status_code=500, detail=f"Ошибка сохранения данных: {e}")

    return {
        "success": True,
        "total": len(configs),
        "created": results['created'],
        "updated": results['updated'],
        "errors": results['errors']
    }



@router.post("/import/rrp-spp")
@limiter.limit("10/minute")
async def import_rrp_spp(
    request: Request,
    file: Annotated[UploadFile, File(description="Excel файл со ступенями РРЦ по СПП")],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)]
):
    """
    Импорт РРЦ по уровням СПП из Excel.

    Для каждого артикула создаёт ступени (RRPSPPTier). Перед загрузкой
    все существующие ступени для данного товара (в данном кабинете) удаляются
    и заменяются строками из файла.

    Формат: Артикул | СПП от (%) | СПП до (%) | РРЦ (₽) | ID кабинета
    """
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be Excel (.xlsx or .xls)"
        )

    content_bytes = await file.read()
    if len(content_bytes) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Файл слишком большой (максимум 10 МБ)"
        )

    try:
        tiers = await run_in_threadpool(excel_importer.import_rrp_spp_tiers, content_bytes)
    except ExcelImportError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    results = {"created": 0, "replaced": 0, "errors": []}

    # Group by (seller_sku, account_id) to do one delete-and-insert per product
    from collections import defaultdict
    groups = defaultdict(list)
    for tier in tiers:
        key = (tier["seller_sku"], tier.get("account_id"))
        groups[key].append(tier)

    for (seller_sku, account_id), rows in groups.items():
        q = (
            db.query(Product)
            .join(Account, Product.account_id == Account.id)
            .filter(
                Product.seller_sku == seller_sku,
                Account.user_id == current_user.id,
            )
        )
        if account_id:
            q = q.filter(Account.id == account_id)
        products_found = q.all()

        if not products_found:
            results["errors"].append(f"Товар {seller_sku!r} не найден")
            continue
        if len(products_found) > 1 and not account_id:
            acc_names = ", ".join(p.account.name for p in products_found)
            results["errors"].append(
                f"Артикул {seller_sku!r} найден в нескольких кабинетах ({acc_names}). "
                "Укажите ID кабинета."
            )
            continue

        product = products_found[0]
        db.flush()
        config = db.query(ProductConfig).filter(
            ProductConfig.product_id == product.id
        ).first()

        if not config:
            # Auto-create a minimal config so SPP tiers work standalone
            # min_price comes from first row of this group (if provided)
            first_row_min = next(
                (r.get("min_price") for r in rows if r.get("min_price") is not None),
                None
            )
            from decimal import Decimal as _D
            first_skip = next(
                (r.get("skip_margin_check") for r in rows if r.get("skip_margin_check")),
                False
            )
            config = ProductConfig(
                product_id=product.id,
                strategy="rrp",
                is_active=True,
                min_price=first_row_min if first_row_min is not None else _D("0"),
                skip_margin_check=bool(first_skip),
            )
            db.add(config)
            db.flush()
            results["created"] += 1

        # Update skip_margin_check from first row (for existing config too)
        first_skip_existing = next(
            (r.get("skip_margin_check") for r in rows if r.get("skip_margin_check") is not None),
            None
        )
        if first_skip_existing is not None and config:
            config.skip_margin_check = bool(first_skip_existing)

        # Delete existing tiers for this config
        existing = db.query(RRPSPPTier).filter(
            RRPSPPTier.product_config_id == config.id
        ).all()
        for t in existing:
            db.delete(t)
        db.flush()

        # Insert new tiers
        for row in rows:
            db.add(RRPSPPTier(
                product_config_id=config.id,
                spp_min=row["spp_min"],
                spp_max=row["spp_max"],
                seller_price=row.get("seller_price"),
                rrp_price=row.get("rrp_price"),
            ))

        if existing:
            results["replaced"] += 1
        else:
            results["created"] += 1

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.exception("import_rrp_spp commit failed")
        raise HTTPException(status_code=500, detail=f"Ошибка сохранения: {e}")

    return {
        "success": True,
        "total_tiers": len(tiers),
        "products_created": results["created"],
        "products_replaced": results["replaced"],
        "errors": results["errors"],
        "message": (
            f"Автоматически создана стратегия RRP для {results['created']} товаров."
            if results["created"] else None
        ),
    }


@router.post("/import/characteristics")
@limiter.limit("10/minute")
async def import_characteristics(
    request: Request,
    file: Annotated[UploadFile, File(description="Excel файл с характеристиками")],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)]
):
    """Импорт характеристик товаров (бренд, себестоимость, мин. цена) из Excel."""
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="File must be Excel (.xlsx or .xls)")

    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail="Файл слишком большой (максимум 10 МБ)")

    try:
        items = await run_in_threadpool(excel_importer.import_characteristics, content)
    except ExcelImportError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.exception("import_characteristics parse error")
        raise HTTPException(status_code=500, detail=f"Ошибка разбора файла: {e}")

    results = {'updated': 0, 'errors': []}

    for item in items:
        q = (
            db.query(Product)
            .join(Account, Product.account_id == Account.id)
            .filter(Product.seller_sku == item['seller_sku'], Account.user_id == current_user.id)
        )
        if item.get('account_id'):
            q = q.filter(Account.id == item['account_id'])
        products = q.all()

        if not products:
            results['errors'].append(f"Товар {item['seller_sku']!r} не найден")
            continue

        for product in products:
            if item.get('brand') is not None:
                product.brand = item['brand']
            if item.get('cost_price') is not None:
                product.cost_price = item['cost_price']
            if item.get('min_price') is not None:
                cfg = db.query(ProductConfig).filter(ProductConfig.product_id == product.id).first()
                if cfg:
                    cfg.min_price = item['min_price']
        results['updated'] += len(products)

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.exception("import_characteristics commit failed")
        raise HTTPException(status_code=500, detail=f"Ошибка сохранения данных: {e}")

    return {
        "success": True,
        "total": len(items),
        "updated": results['updated'],
        "errors": results['errors'],
    }


@router.post("/import/products")
@limiter.limit("10/minute")
async def import_products(
    request: Request,
    file: Annotated[UploadFile, File(description="Excel файл с товарами")],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)]
):
    """Фоновый импорт товаров из Excel. Возвращает task_id немедленно."""
    import base64

    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="File must be Excel (.xlsx or .xls)")

    content_bytes = await file.read()
    if len(content_bytes) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="Файл слишком большой (максимум 10 МБ)")

    # Валидируем структуру файла синхронно (быстро)
    try:
        await run_in_threadpool(excel_importer.import_products, content_bytes)
    except ExcelImportError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Отправляем тяжёлую работу в Celery
    try:
        from app.tasks.products_sync import import_products_task
        file_b64 = base64.b64encode(content_bytes).decode()
        task = import_products_task.delay(file_b64, current_user.id)
        return {"task_id": task.id, "status": "queued", "message": "Импорт запущен в фоне"}
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Celery недоступен: {e}")
        raise HTTPException(status_code=503, detail="Сервис временно недоступен")


@router.get("/task/{task_id}")
async def get_task_status(
    task_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Статус фоновой задачи (импорт товаров и т.п.)"""
    from celery.result import AsyncResult
    from app.celery_app import celery_app

    result = AsyncResult(task_id, app=celery_app)
    state = result.state  # PENDING, STARTED, SUCCESS, FAILURE

    if state == "SUCCESS":
        return {"status": "done", "result": result.result}
    elif state == "FAILURE":
        return {"status": "error", "error": str(result.result)}
    else:
        return {"status": "pending"}

