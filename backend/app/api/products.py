from typing import Annotated, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database import get_db
from app.models import Product, ProductConfig, PriceSnapshot
from app.models.app_setting import AppSetting
from app.models.competitor_entry import CompetitorEntry
from app.schemas.product import (
    ProductCreate, ProductResponse, ProductWithPriceResponse,
    ProductListResponse, ProductConfigCreate, ProductConfigUpdate,
    ProductConfigResponse
)
from app.schemas.auth import TokenData
from app.api.auth import get_current_user
from app.services.wb_api import create_wb_client, WBAPIError

router = APIRouter()


@router.get("", response_model=ProductListResponse)
async def list_products(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[TokenData, Depends(get_current_user)],
    account_id: Optional[int] = None,
    strategy: Optional[str] = Query(None, regex="^(rrp|competitor)$"),
    is_active: Optional[bool] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000)
):
    """
    Получить список товаров с фильтрацией
    
    Фильтры:
    - account_id: ID аккаунта
    - strategy: Стратегия (rrp или competitor)
    - is_active: Активность репрайсера
    """
    query = db.query(Product).join(
        ProductConfig, Product.id == ProductConfig.product_id, isouter=True
    )
    
    # Применяем фильтры
    if account_id:
        query = query.filter(Product.account_id == account_id)
    
    if strategy:
        query = query.filter(ProductConfig.strategy == strategy)
    
    if is_active is not None:
        query = query.filter(ProductConfig.is_active == is_active)
    
    # Пагинация
    total = query.count()
    products = query.offset((page - 1) * page_size).limit(page_size).all()

    # Настройки комиссий для расчёта маржи
    def _gs(key, default):
        row = db.query(AppSetting).filter(AppSetting.key == key).first()
        return row.value if row else default

    commission_type  = _gs("commission_type",  "pct")
    commission_value = float(_gs("commission_value", "0.0"))
    logistics_type   = _gs("logistics_type",   "rub")
    logistics_value  = float(_gs("logistics_value",  "0.0"))
    acquiring_type   = _gs("acquiring_type",   "pct")
    acquiring_value  = float(_gs("acquiring_value",  "0.0"))

    # Добавляем текущие цены
    response_products = []
    for product in products:
        # Создаем базовый dict из ORM объекта
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

        # Добавляем config если есть
        if product.config:
            product_data['config'] = ProductConfigResponse.model_validate(product.config)

        # Получаем последний снимок цены
        latest_snapshot = db.query(PriceSnapshot).filter(
            PriceSnapshot.product_id == product.id
        ).order_by(desc(PriceSnapshot.snapshot_at)).first()

        if latest_snapshot:
            product_data['current_seller_price'] = latest_snapshot.seller_price
            product_data['current_final_price'] = latest_snapshot.final_price
            product_data['last_updated'] = latest_snapshot.snapshot_at

            # Расчёт маржи: (цена - себестоимость - расходы) / себестоимость * 100
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
        page_size=page_size
    )


@router.post("", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(
    product_data: ProductCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[TokenData, Depends(get_current_user)]
):
    """Создать новый товар"""
    
    # Проверяем существование аккаунта
    from app.models import Account
    account = db.query(Account).filter(Account.id == product_data.account_id).first()
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Account {product_data.account_id} not found"
        )
    
    # Проверяем уникальность nm_id
    existing = db.query(Product).filter(
        Product.nm_id == product_data.nm_id,
        Product.account_id == product_data.account_id
    ).first()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Product with nm_id={product_data.nm_id} already exists for this account"
        )
    
    # Если не указаны seller_sku или name — пробуем загрузить из WB Content API
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

    # Финальные заглушки если API тоже не помог
    if not seller_sku:
        seller_sku = str(product_data.nm_id)
    if not name:
        name = f"Товар {product_data.nm_id}"

    # Создаем товар
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

    # Если image_url ещё не получили (seller_sku и name были заполнены) — подгружаем фото отдельно
    if not image_url:
        try:
            wb_client = create_wb_client(account.wb_api_token)
            fetched_url = await wb_client.get_card_photo(product_data.nm_id)
            if fetched_url:
                product.image_url = fetched_url
                db.commit()
                db.refresh(product)
        except Exception:
            pass  # Фото не критично

    return ProductResponse.model_validate(product)


@router.get("/search-wb")
async def search_wb_product(
    account_id: int,
    text: str,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[TokenData, Depends(get_current_user)],
):
    """
    Поиск карточки товара в WB по nmID или артикулу продавца.
    Сначала ищет через Content API (textSearch).
    Если не найдено и текст — число, пробует Prices API по nmID + каталог для названия.
    """
    from app.models import Account
    from app.services.catalog_parser import catalog_parser

    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")

    wb_client = create_wb_client(account.wb_api_token)

    # --- Шаг 1: Content API (textSearch) ---
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

    # --- Шаг 2: Фолбек — Prices API по nmID (только если текст числовой) ---
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

    # Получаем название из публичного каталога
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
    current_user: Annotated[TokenData, Depends(get_current_user)]
):
    """Получить товар по ID"""
    product = db.query(Product).filter(Product.id == product_id).first()
    
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found"
        )
    
    return ProductResponse.model_validate(product)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[TokenData, Depends(get_current_user)]
):
    """Удалить товар"""
    product = db.query(Product).filter(Product.id == product_id).first()
    
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found"
        )
    
    db.delete(product)
    db.commit()
    
    return None


# === ProductConfig endpoints ===

@router.post("/{product_id}/config", response_model=ProductConfigResponse, status_code=status.HTTP_201_CREATED)
async def create_product_config(
    product_id: int,
    config_data: ProductConfigCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[TokenData, Depends(get_current_user)]
):
    """Создать конфигурацию репрайсера для товара"""
    
    # Проверяем существование товара
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found"
        )
    
    # Проверяем что конфиг еще не создан
    if product.config:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Config already exists for product {product_id}"
        )
    
    # Создаем конфиг
    config = ProductConfig(
        product_id=product_id,
        **config_data.model_dump()
    )
    
    db.add(config)
    db.commit()
    db.refresh(config)
    
    return ProductConfigResponse.model_validate(config)


@router.put("/{product_id}/config", response_model=ProductConfigResponse)
async def update_product_config(
    product_id: int,
    config_data: ProductConfigUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[TokenData, Depends(get_current_user)]
):
    """Обновить конфигурацию репрайсера"""
    
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found"
        )
    
    if not product.config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Config not found for product {product_id}"
        )
    
    # Обновляем скалярные поля (исключая competitors)
    update_data = config_data.model_dump(exclude_unset=True, exclude={"competitors"})

    for field, value in update_data.items():
        setattr(product.config, field, value)

    # Обновляем список конкурентов если передан
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
    current_user: Annotated[TokenData, Depends(get_current_user)],
    limit: int = Query(100, ge=1, le=1000)
):
    """Получить историю цен товара"""
    
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found"
        )
    
    snapshots = db.query(PriceSnapshot).filter(
        PriceSnapshot.product_id == product_id
    ).order_by(desc(PriceSnapshot.snapshot_at)).limit(limit).all()
    
    return {
        "product_id": product_id,
        "seller_sku": product.seller_sku,
        "product_name": product.name,
        "snapshots": [
            {
                "seller_price": float(s.seller_price),
                "final_price": float(s.final_price),
                "spp": float(s.spp) if s.spp else None,
                "in_stock": s.total_quantity > 0 if s.total_quantity else False,
                "is_in_promo": s.is_in_promo,
                "snapshot_at": s.snapshot_at.isoformat(),
                "competitor_price": float(s.competitor_price) if s.competitor_price else None,
                "competitor_nm_id": s.competitor_nm_id,
            }
            for s in snapshots
        ]
    }
