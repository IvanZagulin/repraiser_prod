"""Schedule configuration router — manage repricer run schedule."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, validator
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app.models import User
from app.models.repricer_schedule import RepricerSchedule
from app.api.auth import get_current_user

router = APIRouter()

ALLOWED_INTERVALS = {15, 30, 60, 120}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ScheduleOut(BaseModel):
    interval_minutes: int = 60
    work_start_hour: int = 0
    work_end_hour: int = 24
    timezone: str = "Europe/Moscow"
    paused_days: Optional[str] = None
    is_active: bool = True

    class Config:
        from_attributes = True


class ScheduleUpdate(BaseModel):
    interval_minutes: int = 60
    work_start_hour: int = 0
    work_end_hour: int = 24
    timezone: str = "Europe/Moscow"
    paused_days: Optional[str] = None
    is_active: bool = True

    @validator("interval_minutes")
    def validate_interval(cls, v):
        if v not in ALLOWED_INTERVALS:
            raise ValueError(f"interval_minutes must be one of {sorted(ALLOWED_INTERVALS)}")
        return v

    @validator("work_start_hour")
    def validate_start(cls, v):
        if not (0 <= v <= 23):
            raise ValueError("work_start_hour must be 0-23")
        return v

    @validator("work_end_hour")
    def validate_end(cls, v):
        if not (1 <= v <= 24):
            raise ValueError("work_end_hour must be 1-24")
        return v

    @validator("paused_days")
    def validate_paused_days(cls, v):
        if v is None or v == "":
            return None
        parts = v.split(",")
        for p in parts:
            p = p.strip()
            if not p.isdigit() or int(p) not in range(7):
                raise ValueError("paused_days must be comma-separated digits 0-6")
        return v


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=ScheduleOut)
def get_schedule(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return current user's schedule, or defaults if none exists."""
    schedule = (
        db.query(RepricerSchedule)
        .filter(RepricerSchedule.user_id == current_user.id)
        .first()
    )
    if not schedule:
        return ScheduleOut()

    return ScheduleOut(
        interval_minutes=schedule.interval_minutes,
        work_start_hour=schedule.work_start_hour,
        work_end_hour=schedule.work_end_hour,
        timezone=schedule.timezone,
        paused_days=schedule.paused_days,
        is_active=schedule.is_active,
    )


@router.put("", response_model=ScheduleOut)
def update_schedule(
    body: ScheduleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create or update the user's repricer schedule."""
    schedule = (
        db.query(RepricerSchedule)
        .filter(RepricerSchedule.user_id == current_user.id)
        .first()
    )

    if schedule:
        schedule.interval_minutes = body.interval_minutes
        schedule.work_start_hour = body.work_start_hour
        schedule.work_end_hour = body.work_end_hour
        schedule.timezone = body.timezone
        schedule.paused_days = body.paused_days
        schedule.is_active = body.is_active
    else:
        schedule = RepricerSchedule(
            user_id=current_user.id,
            interval_minutes=body.interval_minutes,
            work_start_hour=body.work_start_hour,
            work_end_hour=body.work_end_hour,
            timezone=body.timezone,
            paused_days=body.paused_days,
            is_active=body.is_active,
        )
        db.add(schedule)

    db.commit()
    db.refresh(schedule)

    return ScheduleOut(
        interval_minutes=schedule.interval_minutes,
        work_start_hour=schedule.work_start_hour,
        work_end_hour=schedule.work_end_hour,
        timezone=schedule.timezone,
        paused_days=schedule.paused_days,
        is_active=schedule.is_active,
    )
