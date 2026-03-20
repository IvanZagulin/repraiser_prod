"""Activity log router — paginated log and Excel export."""

import io
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from app.database import get_db
from app.models import User
from app.models.activity_log import ActivityLog
from app.api.auth import get_current_user

router = APIRouter()


@router.get("/")
def list_activity(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    event_type: Optional[str] = Query(None),
    product_id: Optional[int] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Paginated activity log for the current user."""
    query = db.query(ActivityLog).filter(ActivityLog.user_id == current_user.id)

    if event_type:
        query = query.filter(ActivityLog.event_type == event_type)
    if product_id is not None:
        query = query.filter(ActivityLog.product_id == product_id)
    if date_from is not None:
        query = query.filter(ActivityLog.created_at >= date_from)
    if date_to is not None:
        query = query.filter(ActivityLog.created_at <= date_to)

    total = query.count()

    items = (
        query.order_by(ActivityLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return {
        "items": [
            {
                "id": item.id,
                "event_type": item.event_type,
                "product_id": item.product_id,
                "account_id": item.account_id,
                "details": item.details,
                "created_at": item.created_at,
            }
            for item in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/export")
def export_activity(
    event_type: Optional[str] = Query(None),
    product_id: Optional[int] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Export activity log as Excel (.xlsx) file."""
    from openpyxl import Workbook

    query = db.query(ActivityLog).filter(ActivityLog.user_id == current_user.id)

    if event_type:
        query = query.filter(ActivityLog.event_type == event_type)
    if product_id is not None:
        query = query.filter(ActivityLog.product_id == product_id)
    if date_from is not None:
        query = query.filter(ActivityLog.created_at >= date_from)
    if date_to is not None:
        query = query.filter(ActivityLog.created_at <= date_to)

    items = query.order_by(ActivityLog.created_at.desc()).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Activity Log"

    # Header row
    headers = ["ID", "Event Type", "Product ID", "Account ID", "Details", "Created At"]
    ws.append(headers)

    for item in items:
        ws.append([
            item.id,
            item.event_type,
            item.product_id,
            item.account_id,
            item.details,
            item.created_at.isoformat() if item.created_at else None,
        ])

    # Write to buffer
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=activity_log.xlsx"},
    )
