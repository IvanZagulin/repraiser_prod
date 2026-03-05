from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.app_setting import AppSetting
from app.schemas.auth import TokenData
from app.api.auth import get_current_user

router = APIRouter()

VALID_INTERVALS = [1, 5, 10, 15, 20, 30, 60, 120, 180, 360, 720, 1440]


class SettingsResponse(BaseModel):
    repricer_interval_minutes: int
    wb_wallet_discount_pct: float
    commission_type: str
    commission_value: float
    logistics_type: str
    logistics_value: float
    acquiring_type: str
    acquiring_value: float
    min_margin_pct: float


class SettingsUpdate(BaseModel):
    repricer_interval_minutes: int = Field(..., ge=1, le=1440)
    wb_wallet_discount_pct: float = Field(..., ge=0.0, le=10.0)
    commission_type: str = Field("pct", pattern="^(pct|rub)$")
    commission_value: float = Field(0.0, ge=0.0)
    logistics_type: str = Field("rub", pattern="^(pct|rub)$")
    logistics_value: float = Field(0.0, ge=0.0)
    acquiring_type: str = Field("pct", pattern="^(pct|rub)$")
    acquiring_value: float = Field(0.0, ge=0.0)
    min_margin_pct: float = Field(0.0, ge=0.0, le=1000.0)


def _get_setting(db: Session, key: str, default: str) -> str:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    return row.value if row else default


def _set_setting(db: Session, key: str, value: str):
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    db.commit()


@router.get("", response_model=SettingsResponse)
async def get_settings(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[TokenData, Depends(get_current_user)],
):
    """Получить текущие настройки"""
    interval = int(_get_setting(db, "repricer_interval_minutes", "60"))
    wallet_pct = float(_get_setting(db, "wb_wallet_discount_pct", "3.0"))
    commission_type = _get_setting(db, "commission_type", "pct")
    commission_value = float(_get_setting(db, "commission_value", "0.0"))
    logistics_type = _get_setting(db, "logistics_type", "rub")
    logistics_value = float(_get_setting(db, "logistics_value", "0.0"))
    acquiring_type = _get_setting(db, "acquiring_type", "pct")
    acquiring_value = float(_get_setting(db, "acquiring_value", "0.0"))
    min_margin_pct = float(_get_setting(db, "min_margin_pct", "0.0"))
    return SettingsResponse(
        repricer_interval_minutes=interval,
        wb_wallet_discount_pct=wallet_pct,
        commission_type=commission_type,
        commission_value=commission_value,
        logistics_type=logistics_type,
        logistics_value=logistics_value,
        acquiring_type=acquiring_type,
        acquiring_value=acquiring_value,
        min_margin_pct=min_margin_pct,
    )


@router.put("", response_model=SettingsResponse)
async def update_settings(
    data: SettingsUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[TokenData, Depends(get_current_user)],
):
    """Обновить настройки"""
    _set_setting(db, "repricer_interval_minutes", str(data.repricer_interval_minutes))
    _set_setting(db, "wb_wallet_discount_pct", str(data.wb_wallet_discount_pct))
    _set_setting(db, "commission_type", data.commission_type)
    _set_setting(db, "commission_value", str(data.commission_value))
    _set_setting(db, "logistics_type", data.logistics_type)
    _set_setting(db, "logistics_value", str(data.logistics_value))
    _set_setting(db, "acquiring_type", data.acquiring_type)
    _set_setting(db, "acquiring_value", str(data.acquiring_value))
    _set_setting(db, "min_margin_pct", str(data.min_margin_pct))
    return SettingsResponse(
        repricer_interval_minutes=data.repricer_interval_minutes,
        wb_wallet_discount_pct=data.wb_wallet_discount_pct,
        commission_type=data.commission_type,
        commission_value=data.commission_value,
        logistics_type=data.logistics_type,
        logistics_value=data.logistics_value,
        acquiring_type=data.acquiring_type,
        acquiring_value=data.acquiring_value,
        min_margin_pct=data.min_margin_pct,
    )
