from typing import Annotated, Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc
from pydantic import BaseModel
from datetime import datetime

from app.database import get_db
from app.models import Product, Account, ConfigChangeLog
from app.models.user import User
from app.api.auth import get_current_user

router = APIRouter()


class AuditLogEntry(BaseModel):
    id: int
    product_id: int
    field_name: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    changed_at: datetime

    class Config:
        from_attributes = True


@router.get("/product/{product_id}", response_model=List[AuditLogEntry])
async def get_product_audit_log(
    product_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    limit: int = Query(100, ge=1, le=500),
):
    """Лог изменений конфига товара."""
    # IDOR: проверяем что товар принадлежит пользователю
    product = (
        db.query(Product)
        .join(Account, Product.account_id == Account.id)
        .filter(Product.id == product_id, Account.user_id == current_user.id)
        .first()
    )
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    logs = (
        db.query(ConfigChangeLog)
        .filter(ConfigChangeLog.product_id == product_id)
        .order_by(desc(ConfigChangeLog.changed_at))
        .limit(limit)
        .all()
    )
    return [AuditLogEntry.model_validate(log) for log in logs]


@router.get("/recent", response_model=List[AuditLogEntry])
async def get_recent_audit_log(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    limit: int = Query(50, ge=1, le=200),
):
    """Последние изменения по всем товарам пользователя."""
    logs = (
        db.query(ConfigChangeLog)
        .filter(ConfigChangeLog.user_id == current_user.id)
        .order_by(desc(ConfigChangeLog.changed_at))
        .limit(limit)
        .all()
    )
    return [AuditLogEntry.model_validate(log) for log in logs]
