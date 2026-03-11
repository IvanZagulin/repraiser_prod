from typing import Annotated, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc, or_, cast, String
from slowapi import Limiter

from app.database import get_db
from app.models import Product, ProductConfig, PriceSnapshot, Account
from app.models.user import User
from app.models.competitor_entry import CompetitorEntry
from app.schemas.product import (
    ProductCreate, ProductResponse, ProductWithPriceResponse,
    ProductListResponse, ProductConfigCreate, ProductConfigUpdate,
    ProductConfigResponse
)
from app.api.auth import get_current_user
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
    query = (
        db.query(Product)
        .join(Account, Product.account_id == Account.id)
        .filter(Account.user_id == current_user.id)
        .join(ProductConfig, Product.id == ProductConfig.product_id, isouter=True)
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

    # Настройки для расчёта маржи (per-user)
    uid = current_user.id
    commission_type  = _get_setting(db, "commission_type",  "pct", uid)
    commission_value = float(_get_setting(db, "commission_value", "0.0", uid))
    logistics_type   = _get_setting(db, "logistics_type",   "rub", uid)
    logistics_value  = float(_get_setting(db, "logistics_value",  "0.0", uid))
    acquiring_type   = _get_setting(db, "acquiring_type",   "pct", uid)
    acquiring_value  = float(_get_setting(db, "acquiring_value",  "0.0", uid))

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


@router.post("", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(
    product_data: ProductCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
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
        raise HTTPException(status_code=502, detail=str(e))

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
        raise HTTPException(status_code=502, detail=str(e))

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
    db.delete(product)
    db.commit()
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
