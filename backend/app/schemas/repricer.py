from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from typing import Optional, List
from decimal import Decimal


class ManualRunRequest(BaseModel):
    """Схема запроса ручного запуска репрайсера"""
    seller_skus: Optional[List[str]] = Field(None, description="Список артикулов продавца. Если не указан — запуск по всем активным товарам")
    dry_run: bool = Field(False, description="Если True — пропускает реальные вызовы WB API (для нагрузочного тестирования)")


class PriceChangeResponse(BaseModel):
    """Схема изменения цены"""
    id: int
    product_id: int
    seller_sku: str
    product_name: str
    
    old_seller_price: Decimal
    old_final_price: Decimal
    new_seller_price: Optional[Decimal]
    new_final_price: Optional[Decimal]
    
    reason_code: str
    reason_message: Optional[str]
    changed_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class RepricerRunResponse(BaseModel):
    """Схема ответа прогона репрайсера"""
    id: int
    run_type: str
    total_products: int
    updated_count: int
    skipped_count: int
    error_count: int
    status: str
    error_message: Optional[str]
    started_at: datetime
    completed_at: Optional[datetime]
    
    # Детали изменений (опционально)
    changes: Optional[List[PriceChangeResponse]] = None
    
    model_config = ConfigDict(from_attributes=True)


class RepricerRunListResponse(BaseModel):
    """Схема списка прогонов"""
    runs: List[RepricerRunResponse]
    total: int
    total_updated: int = 0
    total_completed: int = 0


class PriceHistoryResponse(BaseModel):
    """Схема истории цен товара"""
    product_id: int
    seller_sku: str
    product_name: str
    snapshots: List[dict]  # Упрощенная схема, детализируем позже
