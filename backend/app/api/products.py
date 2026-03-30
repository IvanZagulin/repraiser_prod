from typing import Annotated, Optional
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, status, Query, Body
from sqlalchemy.orm import Session
from sqlalchemy import desc, or_, cast, String, func, and_
from slowapi import Limiter

from app.database import get_db
from app.models import Product, ProductConfig, PriceSnapshot, Account, ConfigChangeLog
from app.models.user import User
from app.models.competitor_entry import CompetitorEntry
from app.models.price_change import PriceChange
from app.models.pricing_rule import PricingRule
from app.schemas.product import (
    ProductCreate, ProductResponse, ProductWithPriceResponse,
    ProductListResponse, ProductConfigCreate, ProductConfigUpdate,
    ProductConfigResponse
)
from app.api.auth import get_current_user
from app.services.activity_logger import log_activity
from app.api.settings import _get_setting
from app.services.wb_api import create_wb_client, WBAPIError
from app.utils import get_real_ip

router = APIRouter()
limiter = Limiter(key_func=get_real_ip)


def _user_product(db: Session, product_id: int, user: User) -> Product:
    """Возвращает товар если он принадлежит пользователю, иначе 404."""
    product = (
        db.query(Product)
        .join(Account, Product.account_id == Account.id)
        .filter(Product.id == product_id, Account.user_id == user.id)
        .first()
    )
    if not product:
        raise HTTPException(status_code=404, detail=f"Product {product_id} not found")
    return product


@router.get("", response_model=ProductListResponse)
async def list_products(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    account_id: Optional[int] = None,
    strategy: Optional[str] = Query(None, regex="^(rrp|competitor)$"),
    is_active: Optional[bool] = None,
    search: Optional[str] = Query(None, min_length=1, max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
):
    from sqlalchemy.orm import joinedload as _jl
    query = (
        db.query(Product)
        .join(Account, Product.account_id == Account.id)
        .filter(Account.user_id == current_user.id)
        .join(ProductConfig, Product.id == ProductConfig.product_id, isouter=True)
        .options(_jl(Product.config))  # fix N+1: загружаем config в одном запросе
    )

    if account_id:
        query = query.filter(Product.account_id == account_id)
    if strategy:
        query = query.filter(ProductConfig.strategy == strategy)
    if is_active is not None:
        query = query.filter(ProductConfig.is_active == is_active)
    if search:
        term = f"%{search.strip()}%"
        query = query.filter(or_(
            Product.seller_sku.ilike(term),
            Product.name.ilike(term),
            cast(Product.nm_id, String).ilike(term),
        ))

    total = query.count()
    products = query.offset((page - 1) * page_size).limit(page_size).all()

    # Настройки для расчёта маржи — батчевая загрузка (1 запрос вместо 6-12)
    uid = current_user.id
    from app.models.app_setting import AppSetting as _AS
    _fee_keys = ["commission_type", "commission_value", "logistics_type",
                 "logistics_value", "acquiring_type", "acquiring_value"]
    _fee_defaults = {"commission_type": "pct", "commission_value": "0.0",
                     "logistics_type": "rub", "logistics_value": "0.0",
                     "acquiring_type": "pct", "acquiring_value": "0.0"}
    _fee_rows = db.query(_AS).filter(_AS.key.in_(_fee_keys), _AS.user_id.in_([uid, None])).all()
    _fee_map = dict(_fee_defaults)  # start with defaults
    for row in _fee_rows:
        if row.user_id is None and row.key not in {r.key for r in _fee_rows if r.user_id == uid}:
            _fee_map[row.key] = row.value
        elif row.user_id == uid:
            _fee_map[row.key] = row.value
    commission_type  = _fee_map["commission_type"]
    commission_value = float(_fee_map["commission_value"])
    logistics_type   = _fee_map["logistics_type"]
    logistics_value  = float(_fee_map["logistics_value"])
    acquiring_type   = _fee_map["acquiring_type"]
    acquiring_value  = float(_fee_map["acquiring_value"])

    # Batch-загрузка последних снимков цен (fix N+1: 1 запрос вместо N)
    from sqlalchemy import func as sa_func
    product_ids_page = [p.id for p in products]
    last_snapshots: dict = {}
    if product_ids_page:
        subq = (
            db.query(
                PriceSnapshot.product_id,
                sa_func.max(PriceSnapshot.snapshot_at).label("max_at")
            )
            .filter(PriceSnapshot.product_id.in_(product_ids_page))
            .group_by(PriceSnapshot.product_id)
            .subquery()
        )
        snap_rows = (
            db.query(PriceSnapshot)
            .join(subq, (PriceSnapshot.product_id == subq.c.product_id) &
                        (PriceSnapshot.snapshot_at == subq.c.max_at))
            .all()
        )
        last_snapshots = {s.product_id: s for s in snap_rows}

    # Batch-load account names
    _acc_ids = {p.account_id for p in products}
    _accounts_map = {a.id: a.name for a in db.query(Account).filter(Account.id.in_(_acc_ids)).all()} if _acc_ids else {}

    response_products = []
    for product in products:
        product_data = {
            'id': product.id,
            'seller_sku': product.seller_sku,
            'name': product.name,
            'account_id': product.account_id,
            'nm_id': product.nm_id,
            'image_url': product.image_url,
            'created_at': product.created_at,
            'updated_at': product.updated_at,
            'config': None,
            'current_seller_price': None,
            'current_final_price': None,
            'last_updated': None,
            'cost_price': product.cost_price,
            'margin': None,
            'account_name': _accounts_map.get(product.account_id, ""),
        }

        if product.config:
            product_data['config'] = ProductConfigResponse.model_validate(product.config)

        latest_snapshot = last_snapshots.get(product.id)

        if latest_snapshot:
            product_data['current_seller_price'] = latest_snapshot.seller_price
            product_data['current_final_price']  = latest_snapshot.final_price
            product_data['last_updated']          = latest_snapshot.snapshot_at

            cost_price = float(product.cost_price) if product.cost_price else None
            if cost_price and cost_price > 0:
                sp = float(latest_snapshot.seller_price)
                fees = 0.0
                for ftype, fval in [
                    (commission_type, commission_value),
                    (logistics_type,  logistics_value),
                    (acquiring_type,  acquiring_value),
                ]:
                    fees += sp * fval / 100 if ftype == "pct" else fval
                product_data['margin'] = round((sp - cost_price - fees) / cost_price * 100, 1)

        response_products.append(ProductWithPriceResponse(**product_data))

    return ProductListResponse(
        products=response_products,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.patch("/{product_id}/cost")
async def update_product_cost(
    product_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    cost_price: float = Body(..., embed=True),
):
    """Обновить себестоимость товара"""
    product = _get_product_or_404(product_id, db, current_user)
    from decimal import Decimal
    product.cost_price = Decimal(str(cost_price)) if cost_price > 0 else None
    db.commit()
    return {"id": product_id, "cost_price": float(product.cost_price) if product.cost_price else None}


@router.post("", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(
    product_data: ProductCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    # Plan limit check
    from app.api.plan_guard import check_sku_limit
    check_sku_limit(db, current_user)

    # Проверяем что аккаунт принадлежит текущему пользователю
    account = db.query(Account).filter(
        Account.id == product_data.account_id,
        Account.user_id == current_user.id,
    ).first()
    if not account:
        raise HTTPException(status_code=404, detail=f"Account {product_data.account_id} not found")

    existing = db.query(Product).filter(
        Product.nm_id == product_data.nm_id,
        Product.account_id == product_data.account_id,
    ).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Product with nm_id={product_data.nm_id} already exists for this account",
        )

    seller_sku = product_data.seller_sku
    name = product_data.name
    image_url = None

    if not seller_sku or not name:
        try:
            wb_client = create_wb_client(account.wb_api_token)
            card_info = await wb_client.get_card_info(product_data.nm_id)
            if card_info:
                if not seller_sku:
                    seller_sku = card_info.get("seller_sku") or str(product_data.nm_id)
                if not name:
                    name = card_info.get("name") or f"Товар {product_data.nm_id}"
                image_url = card_info.get("image_url")
        except Exception:
            pass

    if not seller_sku:
        seller_sku = str(product_data.nm_id)
    if not name:
        name = f"Товар {product_data.nm_id}"

    product = Product(
        account_id=product_data.account_id,
        nm_id=product_data.nm_id,
        seller_sku=seller_sku,
        name=name,
        image_url=image_url,
    )
    db.add(product)
    db.commit()
    db.refresh(product)

    if not image_url:
        try:
            wb_client = create_wb_client(account.wb_api_token)
            fetched_url = await wb_client.get_card_photo(product_data.nm_id)
            if fetched_url:
                product.image_url = fetched_url
                db.commit()
                db.refresh(product)
        except Exception:
            pass

    try:
        log_activity(db, current_user.id, "product_created", product_id=product.id,
                     account_id=product.account_id,
                     details={"nm_id": product.nm_id, "name": product.name})
        db.commit()
    except Exception:
        pass

    return ProductResponse.model_validate(product)


@router.get("/search-wb")
@limiter.limit("30/minute")
async def search_wb_product(
    request: Request,
    account_id: int,
    text: str,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    from app.services.catalog_parser import catalog_parser

    account = db.query(Account).filter(
        Account.id == account_id, Account.user_id == current_user.id
    ).first()
    if not account:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")

    wb_client = create_wb_client(account.wb_api_token)

    try:
        cards = await wb_client.search_cards(text_search=text, limit=100)
    except WBAPIError as e:
        import logging
        logging.getLogger(__name__).error(f"WB API search error: {e}")
        raise HTTPException(status_code=502, detail="Ошибка при обращении к WB API")

    if cards:
        card = cards[0]
        return {
            "nm_id": card.get("nmID"),
            "seller_sku": card.get("vendorCode"),
            "name": card.get("title", ""),
        }

    try:
        nm_id_int = int(text.strip())
    except ValueError:
        raise HTTPException(status_code=404, detail="Товар не найден в WB")

    try:
        prices = await wb_client.get_prices(nm_ids=[nm_id_int])
    except WBAPIError as e:
        import logging
        logging.getLogger(__name__).error(f"WB API get_prices error: {e}")
        raise HTTPException(status_code=502, detail="Ошибка при обращении к WB API")

    if not prices:
        raise HTTPException(status_code=404, detail="Товар не найден в WB")

    price_data = prices[0]
    catalog_product = await catalog_parser.parse_product(nm_id_int)
    name = catalog_product.name if catalog_product else ""

    return {
        "nm_id": price_data.nm_id,
        "seller_sku": price_data.vendor_code,
        "name": name,
    }



@router.get("/stats")
async def get_products_stats(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Быстрая статистика по товарам пользователя (без пагинации)."""
    from sqlalchemy import func
    from app.models.product_config import ProductConfig

    # Все товары пользователя
    total = (
        db.query(func.count(Product.id))
        .join(Account, Product.account_id == Account.id)
        .filter(Account.user_id == current_user.id)
        .scalar()
    )

    # Активные (есть конфиг с is_active=True)
    active = (
        db.query(func.count(Product.id))
        .join(Account, Product.account_id == Account.id)
        .join(ProductConfig, ProductConfig.product_id == Product.id)
        .filter(Account.user_id == current_user.id, ProductConfig.is_active == True)
        .scalar()
    )

    # Неактивные (есть конфиг с is_active=False)
    inactive = (
        db.query(func.count(Product.id))
        .join(Account, Product.account_id == Account.id)
        .join(ProductConfig, ProductConfig.product_id == Product.id)
        .filter(Account.user_id == current_user.id, ProductConfig.is_active == False)
        .scalar()
    )

    return {
        "total": total,
        "active": active,
        "inactive": inactive,
        "no_config": total - active - inactive,
    }



@router.get("/by-sku/{seller_sku}")
async def get_products_by_sku(
    seller_sku: str,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """All products with the same seller_sku for this user (different accounts/nm_ids)."""
    products = (
        db.query(Product)
        .join(Account, Product.account_id == Account.id)
        .filter(Account.user_id == current_user.id, Product.seller_sku == seller_sku)
        .all()
    )
    result = []
    for p in products:
        account = db.query(Account).filter(Account.id == p.account_id).first()
        config = p.config
        latest_snap = (
            db.query(PriceSnapshot)
            .filter(PriceSnapshot.product_id == p.id)
            .order_by(desc(PriceSnapshot.snapshot_at))
            .first()
        )
        result.append({
            "id": p.id,
            "nm_id": p.nm_id,
            "seller_sku": p.seller_sku,
            "name": p.name,
            "account_id": p.account_id,
            "account_name": account.name if account else None,
            "current_seller_price": float(latest_snap.seller_price) if latest_snap and latest_snap.seller_price else None,
            "current_final_price": float(latest_snap.final_price) if latest_snap and latest_snap.final_price else None,
            "strategy": config.strategy if config else None,
            "is_active": config.is_active if config else None,
            "min_price": float(config.min_price) if config and config.min_price else None,
            "max_price": float(config.max_price) if config and config.max_price else None,
            "rrp_price": float(config.rrp_price) if config and config.rrp_price else None,
        })
    return {"products": result, "total": len(result)}


@router.get("/{product_id}", response_model=ProductResponse)
async def get_product(
    product_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    product = _user_product(db, product_id, current_user)
    return ProductResponse.model_validate(product)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    product = _user_product(db, product_id, current_user)
    _nm_id = product.nm_id
    _name = product.name
    _account_id = product.account_id
    db.delete(product)
    db.commit()
    try:
        log_activity(db, current_user.id, "product_deleted", product_id=product_id,
                     account_id=_account_id,
                     details={"nm_id": _nm_id, "name": _name})
        db.commit()
    except Exception:
        pass
    return None


# === ProductConfig endpoints ===

@router.post("/{product_id}/config", response_model=ProductConfigResponse,
             status_code=status.HTTP_201_CREATED)
async def create_product_config(
    product_id: int,
    config_data: ProductConfigCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    product = _user_product(db, product_id, current_user)

    if product.config:
        raise HTTPException(status_code=400, detail=f"Config already exists for product {product_id}")

    config = ProductConfig(product_id=product_id, **config_data.model_dump())
    db.add(config)
    db.commit()
    db.refresh(config)
    return ProductConfigResponse.model_validate(config)


@router.put("/{product_id}/config", response_model=ProductConfigResponse)
async def update_product_config(
    product_id: int,
    config_data: ProductConfigUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    product = _user_product(db, product_id, current_user)

    if not product.config:
        raise HTTPException(status_code=404, detail=f"Config not found for product {product_id}")

    update_data = config_data.model_dump(exclude_unset=True, exclude={"competitors"})
    for field, value in update_data.items():
        old_value = getattr(product.config, field, None)
        if str(old_value) != str(value):
            db.add(ConfigChangeLog(
                user_id=current_user.id,
                product_id=product_id,
                field_name=field,
                old_value=str(old_value) if old_value is not None else None,
                new_value=str(value) if value is not None else None,
            ))
        setattr(product.config, field, value)

    if config_data.competitors is not None:
        db.query(CompetitorEntry).filter(
            CompetitorEntry.product_config_id == product.config.id
        ).delete()
        for entry in config_data.competitors:
            db.add(CompetitorEntry(
                product_config_id=product.config.id,
                nm_id=entry.nm_id,
                alias=entry.alias,
            ))

    db.commit()
    db.refresh(product.config)

    # Log strategy activation/deactivation
    if "is_active" in update_data:
        try:
            evt = "strategy_activated" if update_data["is_active"] else "strategy_deactivated"
            log_activity(db, current_user.id, evt, product_id=product_id,
                         account_id=product.account_id,
                         details={"nm_id": product.nm_id, "name": product.name})
            db.commit()
        except Exception:
            pass

    return ProductConfigResponse.model_validate(product.config)


@router.get("/{product_id}/history")
async def get_product_history(
    product_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    limit: int = Query(100, ge=1, le=1000),
):
    product = _user_product(db, product_id, current_user)

    snapshots = (
        db.query(PriceSnapshot)
        .filter(PriceSnapshot.product_id == product_id)
        .order_by(desc(PriceSnapshot.snapshot_at))
        .limit(limit)
        .all()
    )

    return {
        "product_id": product_id,
        "seller_sku": product.seller_sku,
        "product_name": product.name,
        "snapshots": [
            {
                "seller_price": float(s.seller_price),
                "final_price":  float(s.final_price),
                "spp":          float(s.spp) if s.spp else None,
                "in_stock":     s.total_quantity > 0 if s.total_quantity else False,
                "is_in_promo":  s.is_in_promo,
                "snapshot_at":  s.snapshot_at.isoformat(),
                "competitor_price":  float(s.competitor_price) if s.competitor_price else None,
                "competitor_nm_id":  s.competitor_nm_id,
            }
            for s in snapshots
        ],
    }


@router.get("/{product_id}/detail")
async def get_product_detail(
    product_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Full product detail: info, config, latest snapshot, price changes, stats."""
    product = _user_product(db, product_id, current_user)

    # Settings for margin calculation
    uid = current_user.id
    commission_type = _get_setting(db, "commission_type", "pct", uid)
    commission_value = float(_get_setting(db, "commission_value", "0.0", uid))
    logistics_type = _get_setting(db, "logistics_type", "rub", uid)
    logistics_value = float(_get_setting(db, "logistics_value", "0.0", uid))
    acquiring_type = _get_setting(db, "acquiring_type", "pct", uid)
    acquiring_value = float(_get_setting(db, "acquiring_value", "0.0", uid))

    # Latest snapshot
    latest_snap = (
        db.query(PriceSnapshot)
        .filter(PriceSnapshot.product_id == product_id)
        .order_by(desc(PriceSnapshot.snapshot_at))
        .first()
    )

    # Snapshots for chart (last 30 days)
    since_30d = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
    snapshots = (
        db.query(PriceSnapshot)
        .filter(PriceSnapshot.product_id == product_id, PriceSnapshot.snapshot_at >= since_30d)
        .order_by(PriceSnapshot.snapshot_at.asc())
        .all()
    )

    # Price changes (last 50)
    changes = (
        db.query(PriceChange)
        .filter(PriceChange.product_id == product_id)
        .order_by(desc(PriceChange.changed_at))
        .limit(50)
        .all()
    )

    # Stats
    total_changes = (
        db.query(func.count(PriceChange.id))
        .filter(PriceChange.product_id == product_id)
        .scalar() or 0
    )
    changes_7d = (
        db.query(func.count(PriceChange.id))
        .filter(
            PriceChange.product_id == product_id,
            PriceChange.changed_at >= datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7),
        )
        .scalar() or 0
    )
    total_snapshots = (
        db.query(func.count(PriceSnapshot.id))
        .filter(PriceSnapshot.product_id == product_id)
        .scalar() or 0
    )

    # Min/max prices over 30d
    price_min_30d = None
    price_max_30d = None
    if snapshots:
        prices = [float(s.seller_price) for s in snapshots if s.seller_price]
        if prices:
            price_min_30d = min(prices)
            price_max_30d = max(prices)

    # Margin calculation
    margin = None
    fees_total = None
    if latest_snap and product.cost_price and float(product.cost_price) > 0:
        sp = float(latest_snap.seller_price)
        cost = float(product.cost_price)
        fees = 0.0
        for ftype, fval in [
            (commission_type, commission_value),
            (logistics_type, logistics_value),
            (acquiring_type, acquiring_value),
        ]:
            fees += sp * fval / 100 if ftype == "pct" else fval
        fees_total = round(fees, 2)
        margin = round((sp - cost - fees) / cost * 100, 1)

    # Config with competitors
    config_data = None
    if product.config:
        competitors = []
        if product.config.competitors:
            competitors = [
                {"nm_id": c.nm_id, "alias": c.alias}
                for c in product.config.competitors
            ]
        config_data = {
            "id": product.config.id,
            "strategy": product.config.strategy,
            "is_active": product.config.is_active,
            "min_price": float(product.config.min_price) if product.config.min_price else None,
            "max_price": float(product.config.max_price) if product.config.max_price else None,
            "rrp_price": float(product.config.rrp_price) if product.config.rrp_price else None,
            "rrp_tolerance": float(product.config.rrp_tolerance) if product.config.rrp_tolerance else None,
            "rrp_consider_wallet": product.config.rrp_consider_wallet,
            "competitor_nm_id": product.config.competitor_nm_id,
            "competitor_offset_type": product.config.competitor_offset_type,
            "competitor_offset_direction": product.config.competitor_offset_direction,
            "competitor_offset_value": float(product.config.competitor_offset_value) if product.config.competitor_offset_value else None,
            "competitor_select_rule": product.config.competitor_select_rule,
            "competitors": competitors,
            "updated_at": product.config.updated_at.isoformat() if product.config.updated_at else None,
        }

    return {
        "product": {
            "id": product.id,
            "nm_id": product.nm_id,
            "seller_sku": product.seller_sku,
            "name": product.name,
            "image_url": product.image_url,
            "cost_price": float(product.cost_price) if product.cost_price else None,
            "account_id": product.account_id,
            "created_at": product.created_at.isoformat(),
            "updated_at": product.updated_at.isoformat(),
        },
        "config": config_data,
        "current_price": {
            "seller_price": float(latest_snap.seller_price) if latest_snap else None,
            "final_price": float(latest_snap.final_price) if latest_snap else None,
            "spp": float(latest_snap.spp) if latest_snap and latest_snap.spp else None,
            "discount": float(latest_snap.discount) if latest_snap and latest_snap.discount else None,
            "competitor_price": float(latest_snap.competitor_price) if latest_snap and latest_snap.competitor_price else None,
            "competitor_nm_id": latest_snap.competitor_nm_id if latest_snap else None,
            "in_stock": latest_snap.total_quantity > 0 if latest_snap and latest_snap.total_quantity else False,
            "is_in_promo": latest_snap.is_in_promo if latest_snap else False,
            "snapshot_at": latest_snap.snapshot_at.isoformat() if latest_snap else None,
        } if latest_snap else None,
        "margin": {
            "value": margin,
            "fees_total": fees_total,
            "cost_price": float(product.cost_price) if product.cost_price else None,
        },
        "stats": {
            "total_changes": total_changes,
            "changes_7d": changes_7d,
            "total_snapshots": total_snapshots,
            "price_min_30d": price_min_30d,
            "price_max_30d": price_max_30d,
        },
        "snapshots": [
            {
                "snapshot_at": s.snapshot_at.isoformat(),
                "seller_price": float(s.seller_price),
                "final_price": float(s.final_price),
                "competitor_price": float(s.competitor_price) if s.competitor_price else None,
                "spp": float(s.spp) if s.spp else None,
                "is_in_promo": s.is_in_promo,
                "in_stock": s.total_quantity > 0 if s.total_quantity else False,
            }
            for s in snapshots
        ],
        "changes": [
            {
                "id": c.id,
                "old_seller_price": float(c.old_seller_price),
                "new_seller_price": float(c.new_seller_price) if c.new_seller_price else None,
                "old_final_price": float(c.old_final_price),
                "new_final_price": float(c.new_final_price) if c.new_final_price else None,
                "reason_code": c.reason_code,
                "reason_message": c.reason_message,
                "changed_at": c.changed_at.isoformat(),
            }
            for c in changes
        ],
    }


# ---------------------------------------------------------------------------
# Pricing Rules (F9) — CRUD на /api/products/{product_id}/rules
# ---------------------------------------------------------------------------

from pydantic import BaseModel as _BaseModel
from typing import Optional as _Optional

class RuleCreate(_BaseModel):
    condition_type: str = "always"
    condition_value: _Optional[str] = None
    action_type: str = "set_margin_pct"
    action_value: str = "10"
    priority: int = 0
    is_active: bool = True

class RuleUpdate(_BaseModel):
    condition_type: _Optional[str] = None
    condition_value: _Optional[str] = None
    action_type: _Optional[str] = None
    action_value: _Optional[str] = None
    priority: _Optional[int] = None
    is_active: _Optional[bool] = None

class RuleResponse(_BaseModel):
    id: int
    product_config_id: int
    condition_type: str
    condition_value: _Optional[str]
    action_type: str
    action_value: str
    priority: int
    is_active: bool

    class Config:
        from_attributes = True


def _get_product_config_for_user(product_id: int, user: User, db: Session) -> ProductConfig:
    cfg = (
        db.query(ProductConfig)
        .join(Product, ProductConfig.product_id == Product.id)
        .join(Account, Product.account_id == Account.id)
        .filter(Product.id == product_id, Account.user_id == user.id)
        .first()
    )
    if not cfg:
        raise HTTPException(status_code=404, detail="Product config not found")
    return cfg


@router.get("/{product_id}/rules", response_model=list[RuleResponse])
async def get_product_rules(
    product_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    cfg = _get_product_config_for_user(product_id, current_user, db)
    rules = db.query(PricingRule).filter(
        PricingRule.product_config_id == cfg.id
    ).order_by(PricingRule.priority).all()
    return [RuleResponse.model_validate(r) for r in rules]


@router.post("/{product_id}/rules", response_model=RuleResponse, status_code=status.HTTP_201_CREATED)
async def create_product_rule(
    product_id: int,
    data: RuleCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    cfg = _get_product_config_for_user(product_id, current_user, db)
    rule = PricingRule(
        product_config_id=cfg.id,
        condition_type=data.condition_type,
        condition_value=data.condition_value,
        action_type=data.action_type,
        action_value=data.action_value,
        priority=data.priority,
        is_active=data.is_active,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return RuleResponse.model_validate(rule)


@router.put("/{product_id}/rules/{rule_id}", response_model=RuleResponse)
async def update_product_rule(
    product_id: int,
    rule_id: int,
    data: RuleUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    cfg = _get_product_config_for_user(product_id, current_user, db)
    rule = db.query(PricingRule).filter(
        PricingRule.id == rule_id,
        PricingRule.product_config_id == cfg.id,
    ).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)
    db.commit()
    db.refresh(rule)
    return RuleResponse.model_validate(rule)


@router.delete("/{product_id}/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product_rule(
    product_id: int,
    rule_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    cfg = _get_product_config_for_user(product_id, current_user, db)
    rule = db.query(PricingRule).filter(
        PricingRule.id == rule_id,
        PricingRule.product_config_id == cfg.id,
    ).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    db.delete(rule)
    db.commit()
    return None
