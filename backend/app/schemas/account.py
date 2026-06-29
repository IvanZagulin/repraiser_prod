from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AccountBase(BaseModel):
    """Base seller account schema."""

    name: str = Field(..., min_length=1, max_length=255, description="Account name")
    marketplace: str = Field("wb", pattern="^(wb|ozon)$", description="Marketplace code")


class AccountCreate(AccountBase):
    """Create seller account."""

    wb_api_token: Optional[str] = Field(None, min_length=10, description="WB API JWT token")
    ozon_client_id: Optional[str] = Field(None, min_length=1, description="Ozon Seller API Client-Id")
    ozon_api_key: Optional[str] = Field(None, min_length=10, description="Ozon Seller API Api-Key")

    @model_validator(mode="after")
    def validate_credentials(self):
        if self.marketplace == "wb" and not self.wb_api_token:
            raise ValueError("wb_api_token is required for WB accounts")
        if self.marketplace == "ozon" and (not self.ozon_client_id or not self.ozon_api_key):
            raise ValueError("ozon_client_id and ozon_api_key are required for Ozon accounts")
        return self


class AccountUpdate(BaseModel):
    """Update seller account."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    marketplace: Optional[str] = Field(None, pattern="^(wb|ozon)$")
    wb_api_token: Optional[str] = Field(None, min_length=10)
    ozon_client_id: Optional[str] = Field(None, min_length=1)
    ozon_api_key: Optional[str] = Field(None, min_length=10)
    is_active: Optional[bool] = None


class AccountResponse(AccountBase):
    """Account response."""

    id: int
    is_active: bool
    token_expires_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    token_ttl_days: Optional[int] = Field(None, description="Days until WB token expiration")

    model_config = ConfigDict(from_attributes=True)


class AccountListResponse(BaseModel):
    """Account list response."""

    accounts: list[AccountResponse]
    total: int
