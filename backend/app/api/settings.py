import json
from typing import Annotated, Optional
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from slowapi import Limiter

from app.database import get_db
from app.models.app_setting import AppSetting
from app.models.user import User
from app.api.auth import get_current_user
from app.utils import get_real_ip

router = APIRouter()
limiter = Limiter(key_func=get_real_ip)

VALID_INTERVALS = [1, 5, 10, 15, 20, 30, 60, 120, 180, 360, 720, 1440]
DEFAULT_BRAND_MIN_MARGIN_RULES = [{"brand": "Просвещение", "min_margin_pct": 20.0}]


class BrandMinMarginRule(BaseModel):
    brand: str = Field(..., min_length=1, max_length=255)
    min_margin_pct: float = Field(..., ge=0.0, le=1000.0)


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
    brand_min_margin_rules: list[BrandMinMarginRule] = Field(default_factory=list)


class SettingsUpdate(BaseModel):
    repricer_interval_minutes: Optional[int] = Field(None, ge=1, le=1440)
    wb_wallet_discount_pct: float    = Field(..., ge=0.0, le=10.0)
    commission_type: str             = Field("pct", pattern="^(pct|rub)$")
    commission_value: float          = Field(0.0, ge=0.0, le=100.0)
    logistics_type: str              = Field("rub", pattern="^(pct|rub)$")
    logistics_value: float           = Field(0.0, ge=0.0, le=10000.0)
    acquiring_type: str              = Field("pct", pattern="^(pct|rub)$")
    acquiring_value: float           = Field(0.0, ge=0.0, le=100.0)
    min_margin_pct: float            = Field(0.0, ge=0.0, le=1000.0)
    brand_min_margin_rules: list[BrandMinMarginRule] = Field(default_factory=list)


# ─── helpers (также экспортируются для repricer_engine) ───────────────────────

def _get_setting(db: Session, key: str, default: str, user_id: Optional[int] = None) -> str:
    """Читает настройку: сначала user-специфичную, потом глобальный дефолт (user_id=NULL)."""
    if user_id is not None:
        row = db.query(AppSetting).filter(
            AppSetting.key == key, AppSetting.user_id == user_id
        ).first()
        if row:
            return row.value
    # Fallback: глобальный дефолт
    row = db.query(AppSetting).filter(
        AppSetting.key == key, AppSetting.user_id == None
    ).first()
    return row.value if row else default


def _set_setting(db: Session, key: str, value: str, user_id: Optional[int] = None, *, auto_commit: bool = True):
    """Upsert настройки для конкретного user_id."""
    row = db.query(AppSetting).filter(
        AppSetting.key == key, AppSetting.user_id == user_id
    ).first()
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value, user_id=user_id))
    if auto_commit:
        db.commit()


def _get_brand_min_margin_rules(db: Session, user_id: Optional[int]) -> list[BrandMinMarginRule]:
    raw = _get_setting(
        db,
        "brand_min_margin_rules",
        json.dumps(DEFAULT_BRAND_MIN_MARGIN_RULES, ensure_ascii=False),
        user_id,
    )
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = DEFAULT_BRAND_MIN_MARGIN_RULES
    if not isinstance(parsed, list):
        parsed = DEFAULT_BRAND_MIN_MARGIN_RULES

    rules = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        brand = str(item.get("brand") or "").strip()
        pct = item.get("min_margin_pct")
        if not brand or pct is None:
            continue
        try:
            rules.append(BrandMinMarginRule(brand=brand, min_margin_pct=float(pct)))
        except Exception:
            continue
    return rules


# ─── endpoints ────────────────────────────────────────────────────────────────

@router.get("", response_model=SettingsResponse)
@limiter.limit("30/minute")
async def get_settings(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    uid = current_user.id
    return SettingsResponse(
        repricer_interval_minutes=int(_get_setting(db, "repricer_interval_minutes", "60", uid)),
        wb_wallet_discount_pct=float(_get_setting(db, "wb_wallet_discount_pct", "3.0", uid)),
        commission_type=_get_setting(db, "commission_type", "pct", uid),
        commission_value=float(_get_setting(db, "commission_value", "0.0", uid)),
        logistics_type=_get_setting(db, "logistics_type", "rub", uid),
        logistics_value=float(_get_setting(db, "logistics_value", "0.0", uid)),
        acquiring_type=_get_setting(db, "acquiring_type", "pct", uid),
        acquiring_value=float(_get_setting(db, "acquiring_value", "0.0", uid)),
        min_margin_pct=float(_get_setting(db, "min_margin_pct", "0.0", uid)),
        brand_min_margin_rules=_get_brand_min_margin_rules(db, uid),
    )


@router.put("", response_model=SettingsResponse)
@limiter.limit("20/minute")
async def update_settings(
    request: Request,
    data: SettingsUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    uid = current_user.id
    if data.repricer_interval_minutes is not None:
        _set_setting(db, "repricer_interval_minutes", str(data.repricer_interval_minutes), uid, auto_commit=False)
    _set_setting(db, "wb_wallet_discount_pct",    str(data.wb_wallet_discount_pct),    uid, auto_commit=False)
    _set_setting(db, "commission_type",            data.commission_type,                uid, auto_commit=False)
    _set_setting(db, "commission_value",           str(data.commission_value),          uid, auto_commit=False)
    _set_setting(db, "logistics_type",             data.logistics_type,                 uid, auto_commit=False)
    _set_setting(db, "logistics_value",            str(data.logistics_value),           uid, auto_commit=False)
    _set_setting(db, "acquiring_type",             data.acquiring_type,                 uid, auto_commit=False)
    _set_setting(db, "acquiring_value",            str(data.acquiring_value),           uid, auto_commit=False)
    _set_setting(db, "min_margin_pct",             str(data.min_margin_pct),            uid, auto_commit=False)
    _set_setting(
        db,
        "brand_min_margin_rules",
        json.dumps([
            (rule.model_dump() if hasattr(rule, "model_dump") else rule.dict())
            for rule in data.brand_min_margin_rules
        ], ensure_ascii=False),
        uid,
        auto_commit=False,
    )
    db.commit()
    return SettingsResponse(
        repricer_interval_minutes=data.repricer_interval_minutes or int(_get_setting(db, "repricer_interval_minutes", "60", uid)),
        wb_wallet_discount_pct=data.wb_wallet_discount_pct,
        commission_type=data.commission_type,
        commission_value=data.commission_value,
        logistics_type=data.logistics_type,
        logistics_value=data.logistics_value,
        acquiring_type=data.acquiring_type,
        acquiring_value=data.acquiring_value,
        min_margin_pct=data.min_margin_pct,
        brand_min_margin_rules=data.brand_min_margin_rules,
    )
