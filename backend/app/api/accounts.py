from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Account
from app.models.user import User
from app.schemas.account import (
    AccountCreate, AccountUpdate, AccountResponse, AccountListResponse
)
from app.api.auth import get_current_user
from app.utils.jwt import get_token_expiration, get_token_ttl_days

router = APIRouter()


def _check_ownership(account: Account, current_user: User):
    """404 если аккаунт не принадлежит текущему пользователю"""
    if account.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Account not found")


def _build_response(account: Account) -> AccountResponse:
    return AccountResponse(**{
        'id': account.id,
        'name': account.name,
        'is_active': account.is_active,
        'token_expires_at': account.token_expires_at,
        'created_at': account.created_at,
        'updated_at': account.updated_at,
        'token_ttl_days': get_token_ttl_days(account.wb_api_token),
    })


@router.get("", response_model=AccountListResponse)
async def list_accounts(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    skip: int = 0,
    limit: int = 100,
):
    """Получить список аккаунтов текущего пользователя"""
    q = db.query(Account).filter(Account.user_id == current_user.id)
    total = q.count()
    accounts = q.offset(skip).limit(limit).all()
    return AccountListResponse(
        accounts=[_build_response(a) for a in accounts],
        total=total,
    )


@router.post("", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
async def create_account(
    account_data: AccountCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Создать новый WB-аккаунт"""
    token_expires_at = get_token_expiration(account_data.wb_api_token)
    account = Account(
        user_id=current_user.id,
        name=account_data.name,
        wb_api_token=account_data.wb_api_token,
        token_expires_at=token_expires_at,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return _build_response(account)


@router.get("/{account_id}", response_model=AccountResponse)
async def get_account(
    account_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail=f"Account {account_id} not found")
    _check_ownership(account, current_user)
    return _build_response(account)


@router.put("/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: int,
    account_data: AccountUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail=f"Account {account_id} not found")
    _check_ownership(account, current_user)

    update_data = account_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(account, field, value)
    if "wb_api_token" in update_data:
        account.token_expires_at = get_token_expiration(account.wb_api_token)

    db.commit()
    db.refresh(account)
    return _build_response(account)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    account_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail=f"Account {account_id} not found")
    _check_ownership(account, current_user)
    db.delete(account)
    db.commit()
    return None
