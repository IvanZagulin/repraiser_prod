from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from typing import Optional


class AccountBase(BaseModel):
    """Базовая схема аккаунта"""
    name: str = Field(..., min_length=1, max_length=255, description="Название аккаунта")


class AccountCreate(AccountBase):
    """Схема для создания аккаунта"""
    wb_api_token: str = Field(..., min_length=10, description="JWT токен WB API")


class AccountUpdate(BaseModel):
    """Схема для обновления аккаунта"""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    wb_api_token: Optional[str] = Field(None, min_length=10)
    is_active: Optional[bool] = None


class AccountResponse(AccountBase):
    """Схема ответа с аккаунтом"""
    id: int
    is_active: bool
    token_expires_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    
    # Дополнительная информация
    token_ttl_days: Optional[int] = Field(None, description="Дней до истечения токена")
    
    model_config = ConfigDict(from_attributes=True)


class AccountListResponse(BaseModel):
    """Схема списка аккаунтов"""
    accounts: list[AccountResponse]
    total: int
