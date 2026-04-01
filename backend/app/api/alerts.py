from typing import Annotated, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import desc
from pydantic import BaseModel
from datetime import datetime
from decimal import Decimal
from slowapi import Limiter

from app.database import get_db
from app.models.competitor_alert import CompetitorAlert
from app.models.user import User
from app.api.auth import get_current_user
from app.utils import get_real_ip

router = APIRouter()
limiter = Limiter(key_func=get_real_ip)


class AlertResponse(BaseModel):
    id: int
    product_id: int
    competitor_nm_id: int
    old_price: Decimal
    new_price: Decimal
    change_pct: Decimal
    is_read: bool
    created_at: datetime

    class Config:
        from_attributes = True


class AlertCountResponse(BaseModel):
    unread: int


@router.get("", response_model=List[AlertResponse])
@limiter.limit("60/minute")
async def list_alerts(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    is_read: Optional[bool] = None,
    limit: int = Query(50, ge=1, le=200),
):
    query = db.query(CompetitorAlert).filter(
        CompetitorAlert.user_id == current_user.id
    )
    if is_read is not None:
        query = query.filter(CompetitorAlert.is_read == is_read)
    alerts = query.order_by(desc(CompetitorAlert.created_at)).limit(limit).all()
    return [AlertResponse.model_validate(a) for a in alerts]


@router.get("/count", response_model=AlertCountResponse)
@limiter.limit("60/minute")
async def alert_count(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    count = db.query(CompetitorAlert).filter(
        CompetitorAlert.user_id == current_user.id,
        CompetitorAlert.is_read == False,
    ).count()
    return AlertCountResponse(unread=count)


@router.put("/{alert_id}/read")
@limiter.limit("30/minute")
async def mark_alert_read(
    request: Request,
    alert_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    alert = db.query(CompetitorAlert).filter(
        CompetitorAlert.id == alert_id,
        CompetitorAlert.user_id == current_user.id,
    ).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.is_read = True
    db.commit()
    return {"status": "ok"}


@router.put("/read-all")
@limiter.limit("20/minute")
async def mark_all_read(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    updated = (
        db.query(CompetitorAlert)
        .filter(
            CompetitorAlert.user_id == current_user.id,
            CompetitorAlert.is_read == False,
        )
        .update({"is_read": True}, synchronize_session=False)
    )
    db.commit()
    return {"marked_read": updated}
