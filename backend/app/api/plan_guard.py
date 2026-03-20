"""Plan enforcement dependencies for route-level access control."""

from datetime import datetime
from typing import Optional

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import Account, Product, User
from app.models.plan import Plan
from app.api.auth import get_current_user


# Default trial limits (Business plan equivalent)
TRIAL_MAX_SKU = 5000
TRIAL_MAX_ACCOUNTS = 5


def check_plan_active(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """Verify the user has an active plan or trial. Admins always pass."""
    if current_user.is_admin:
        return current_user

    # Check active paid plan
    if current_user.plan_id and current_user.plan:
        if current_user.plan.is_active:
            return current_user

    # Check active trial
    if current_user.trial_ends_at and current_user.trial_ends_at > datetime.utcnow():
        return current_user

    raise HTTPException(status_code=403, detail="plan_expired")


def get_user_limits(user: User) -> dict:
    """Return SKU and account limits based on user's plan/trial status.

    Returns:
        {"max_sku": int|None, "max_accounts": int|None}
        None means unlimited.
    """
    if user.is_admin:
        return {"max_sku": None, "max_accounts": None}

    if user.plan_id and user.plan and user.plan.is_active:
        return {
            "max_sku": user.plan.max_sku,
            "max_accounts": user.plan.max_accounts,
        }

    # Trial — use Business plan defaults
    if user.trial_ends_at and user.trial_ends_at > datetime.utcnow():
        return {
            "max_sku": TRIAL_MAX_SKU,
            "max_accounts": TRIAL_MAX_ACCOUNTS,
        }

    # No plan, no trial — return zeros (effectively locked out)
    return {"max_sku": 0, "max_accounts": 0}


def check_sku_limit(
    db: Session = Depends(get_db),
    current_user: User = Depends(check_plan_active),
) -> User:
    """Raise 403 if user has exceeded their SKU (product) limit."""
    limits = get_user_limits(current_user)
    max_sku = limits["max_sku"]

    if max_sku is None:
        return current_user

    account_ids = [a.id for a in current_user.accounts]
    if not account_ids:
        return current_user

    product_count = (
        db.query(func.count(Product.id))
        .filter(Product.account_id.in_(account_ids))
        .scalar()
    )

    if product_count >= max_sku:
        raise HTTPException(status_code=403, detail="sku_limit_exceeded")

    return current_user


def check_account_limit(
    db: Session = Depends(get_db),
    current_user: User = Depends(check_plan_active),
) -> User:
    """Raise 403 if user has exceeded their account limit."""
    limits = get_user_limits(current_user)
    max_accounts = limits["max_accounts"]

    if max_accounts is None:
        return current_user

    account_count = (
        db.query(func.count(Account.id))
        .filter(Account.user_id == current_user.id)
        .scalar()
    )

    if account_count >= max_accounts:
        raise HTTPException(status_code=403, detail="account_limit_exceeded")

    return current_user
