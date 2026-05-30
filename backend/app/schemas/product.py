from pydantic import BaseModel, Field, field_validator, ConfigDict
from datetime import datetime
from typing import Optional, List
from decimal import Decimal


class CompetitorEntrySchema(BaseModel):
    """Конкурент в списке MultiCompetitor"""
    nm_id: int = Field(..., gt=0, description="nmID конкурента на WB")
    alias: Optional[str] = Field(None, max_length=100, description="Произвольная метка")

    model_config = ConfigDict(from_attributes=True)


class ProductConfigBase(BaseModel):
    """Базовая схема конфигурации репрайсера"""
    strategy: str = Field(..., pattern="^(rrp|competitor)$", description="Стратегия ценообразования")
    is_active: bool = Field(True, description="Включен ли репрайсер")

    # Защитные лимиты
    min_price: Decimal = Field(..., ge=0, description="Минимальная цена (0 = отключено)")
    max_price: Optional[Decimal] = Field(None, gt=0, description="Максимальная цена")

    # RRP настройки
    rrp_price: Optional[Decimal] = Field(None, gt=0, description="РРЦ")
    rrp_tolerance: Optional[Decimal] = Field(5, ge=0, description="Допуск РРЦ в рублях")
    rrp_consider_wallet: bool = Field(False, description="Учитывать скидку кошелька WB при расчёте РРЦ")

    # Competitor настройки
    competitor_nm_id: Optional[int] = Field(None, gt=0, description="nmID конкурента (legacy)")
    competitor_offset_type: Optional[str] = Field(None, pattern="^(rub|pct)$", description="Тип смещения")
    competitor_offset_direction: Optional[str] = Field(None, pattern="^(above|below)$", description="Направление")
    competitor_offset_value: Optional[Decimal] = Field(None, ge=0, description="Величина смещения")
    competitor_select_rule: Optional[str] = Field("min_price", pattern="^(min_price|avg_price|cheapest_instock)$",
                                                   description="Правило выбора цены среди конкурентов")
    match_competitor_basic: bool = Field(False, description="Имитировать зачёркнутую цену конкурента (priceU+discount)")

    @field_validator('max_price')
    @classmethod
    def validate_max_price(cls, v, info):
        if v is not None and 'min_price' in info.data:
            min_price = info.data['min_price']
            if v < min_price:
                raise ValueError('max_price должна быть >= min_price')
        return v


class ProductConfigCreate(ProductConfigBase):
    """Схема создания конфигурации"""
    competitors: Optional[List[CompetitorEntrySchema]] = None


class ProductConfigUpdate(BaseModel):
    """Схема обновления конфигурации"""
    strategy: Optional[str] = Field(None, pattern="^(rrp|competitor)$")
    is_active: Optional[bool] = None
    min_price: Optional[Decimal] = Field(None, ge=0)
    max_price: Optional[Decimal] = Field(None, gt=0)
    rrp_price: Optional[Decimal] = Field(None, gt=0)
    rrp_tolerance: Optional[Decimal] = Field(None, ge=0)
    rrp_consider_wallet: Optional[bool] = None
    competitor_nm_id: Optional[int] = Field(None, gt=0)
    competitor_offset_type: Optional[str] = Field(None, pattern="^(rub|pct)$")
    competitor_offset_direction: Optional[str] = Field(None, pattern="^(above|below)$")
    competitor_offset_value: Optional[Decimal] = Field(None, ge=0)
    competitor_select_rule: Optional[str] = Field(None, pattern="^(min_price|avg_price|cheapest_instock)$")
    match_competitor_basic: Optional[bool] = None
    competitors: Optional[List[CompetitorEntrySchema]] = None


class ProductConfigResponse(ProductConfigBase):
    """Схема ответа с конфигурацией"""
    id: int
    product_id: int
    updated_at: datetime
    competitors: List[CompetitorEntrySchema] = []

    model_config = ConfigDict(from_attributes=True)


class ProductBase(BaseModel):
    """Базовая схема товара"""
    seller_sku: Optional[str] = Field(None, max_length=255, description="Артикул продавца (будет загружен из WB если не указан)")
    name: Optional[str] = Field(None, max_length=500, description="Название товара (будет загружено из WB если не указано)")


class ProductCreate(ProductBase):
    """Схема создания товара"""
    nm_id: int = Field(..., gt=0, description="nmID WB")
    account_id: int = Field(..., gt=0, description="ID аккаунта")


class ProductResponse(ProductBase):
    """Схема ответа с товаром"""
    id: int
    account_id: int
    nm_id: int
    image_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    # Вложенная конфигурация (если есть)
    config: Optional[ProductConfigResponse] = None

    model_config = ConfigDict(from_attributes=True)


class ProductWithPriceResponse(ProductResponse):
    """Схема товара с текущей ценой"""
    current_seller_price: Optional[Decimal] = None
    current_final_price: Optional[Decimal] = None
    last_updated: Optional[datetime] = None
    cost_price: Optional[Decimal] = None
    margin: Optional[float] = None
    account_name: Optional[str] = None
    total_quantity: Optional[int] = None


class ProductListResponse(BaseModel):
    """Схема списка товаров"""
    products: list[ProductWithPriceResponse]
    total: int
    page: int
    page_size: int
