"""Shared helpers for API routers."""

from sqlalchemy.orm import Session

from app.models import Account, Product, User


def _user_account_ids(db: Session, user: User) -> list:
    """Return list of account IDs belonging to the user."""
    return [aid for (aid,) in db.query(Account.id).filter(Account.user_id == user.id).all()]


def _user_product_ids(db: Session, user: User) -> list:
    """Return list of product IDs belonging to the user."""
    account_ids = _user_account_ids(db, user)
    if not account_ids:
        return []
    return [pid for (pid,) in db.query(Product.id).filter(Product.account_id.in_(account_ids)).all()]
