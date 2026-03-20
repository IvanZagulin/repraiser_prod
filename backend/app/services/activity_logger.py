"""Simple service for logging user activity events."""

import json
import logging
from sqlalchemy.orm import Session
from app.models.activity_log import ActivityLog

logger = logging.getLogger(__name__)


def log_activity(
    db: Session,
    user_id: int,
    event_type: str,
    product_id=None,
    account_id=None,
    details=None,
):
    """Log user activity event.

    Note: Does not commit — the caller manages the transaction.
    """
    entry = ActivityLog(
        user_id=user_id,
        event_type=event_type,
        product_id=product_id,
        account_id=account_id,
        details=json.dumps(details, ensure_ascii=False) if details else None,
    )
    db.add(entry)
