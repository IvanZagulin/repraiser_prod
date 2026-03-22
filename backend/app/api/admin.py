"""Admin API router — user management and plan assignment."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import Account, Product, User
from app.models.plan import Plan
from app.api.auth import get_current_user

router = APIRouter()


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Raise 403 if the current user is not an admin."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="admin_required")
    return current_user


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class UserOut(BaseModel):
    id: int
    email: str
    plan_code: str | None = None
    trial_ends_at: datetime | None = None
    is_active: bool
    created_at: datetime
    accounts_count: int = 0
    products_count: int = 0

    class Config:
        from_attributes = True


class SetPlanBody(BaseModel):
    plan_code: str


class PlanOut(BaseModel):
    id: int
    code: str
    name: str
    max_sku: int | None = None
    max_accounts: int | None = None
    price_rub: float | None = None
    is_active: bool

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/users", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """List all users with plan info and counts."""
    users = db.query(User).all()
    result = []
    for u in users:
        accounts_count = (
            db.query(func.count(Account.id))
            .filter(Account.user_id == u.id)
            .scalar()
        )
        products_count = (
            db.query(func.count(Product.id))
            .join(Account, Product.account_id == Account.id)
            .filter(Account.user_id == u.id)
            .scalar()
        )
        result.append(
            UserOut(
                id=u.id,
                email=u.email,
                plan_code=u.plan.code if u.plan else None,
                trial_ends_at=u.trial_ends_at,
                is_active=u.is_active,
                created_at=u.created_at,
                accounts_count=accounts_count,
                products_count=products_count,
            )
        )
    return result


@router.put("/users/{user_id}/plan")
def set_user_plan(
    user_id: int,
    body: SetPlanBody,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Assign a plan to a user by plan_code."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="user_not_found")

    plan = db.query(Plan).filter(Plan.code == body.plan_code).first()
    if not plan:
        raise HTTPException(status_code=404, detail="plan_not_found")

    user.plan_id = plan.id
    user.plan_assigned_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    db.refresh(user)

    return {
        "ok": True,
        "user_id": user.id,
        "plan_code": plan.code,
        "plan_assigned_at": user.plan_assigned_at,
    }


@router.get("/plans", response_model=list[PlanOut])
def list_plans(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """List all available plans."""
    plans = db.query(Plan).all()
    return [
        PlanOut(
            id=p.id,
            code=p.code,
            name=p.name,
            max_sku=p.max_sku,
            max_accounts=p.max_accounts,
            price_rub=float(p.price_rub) if p.price_rub is not None else None,
            is_active=p.is_active,
        )
        for p in plans
    ]
