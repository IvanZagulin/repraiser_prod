from typing import Annotated, List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Account
from app.schemas.account import (
    AccountCreate, AccountUpdate, AccountResponse, AccountListResponse
)
from app.schemas.auth import TokenData
from app.api.auth import get_current_user
from app.utils.jwt import get_token_expiration, get_token_ttl_days

router = APIRouter()


@router.get("", response_model=AccountListResponse)
async def list_accounts(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[TokenData, Depends(get_current_user)],
    skip: int = 0,
    limit: int = 100
):
    """Получить список всех аккаунтов"""
    accounts = db.query(Account).offset(skip).limit(limit).all()
    total = db.query(Account).count()
    
    # Добавляем информацию о TTL токенов
    response_accounts = []
    for account in accounts:
        # Создаем dict вручную
        account_data = {
            'id': account.id,
            'name': account.name,
            'is_active': account.is_active,
            'token_expires_at': account.token_expires_at,
            'created_at': account.created_at,
            'updated_at': account.updated_at,
            'token_ttl_days': get_token_ttl_days(account.wb_api_token)
        }
        
        response_accounts.append(AccountResponse(**account_data))
    
    return AccountListResponse(
        accounts=response_accounts,
        total=total
    )


@router.post("", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
async def create_account(
    account_data: AccountCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[TokenData, Depends(get_current_user)]
):
    """Создать новый аккаунт WB"""
    
    # Получаем срок действия токена
    token_expires_at = get_token_expiration(account_data.wb_api_token)
    
    # Создаем аккаунт
    account = Account(
        name=account_data.name,
        wb_api_token=account_data.wb_api_token,
        token_expires_at=token_expires_at
    )
    
    db.add(account)
    db.commit()
    db.refresh(account)
    
    # Формируем ответ с TTL
    response = AccountResponse.model_validate(account)
    response.token_ttl_days = get_token_ttl_days(account.wb_api_token)
    
    return response


@router.get("/{account_id}", response_model=AccountResponse)
async def get_account(
    account_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[TokenData, Depends(get_current_user)]
):
    """Получить аккаунт по ID"""
    account = db.query(Account).filter(Account.id == account_id).first()
    
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Account {account_id} not found"
        )
    
    account_data = {
        'id': account.id,
        'name': account.name,
        'is_active': account.is_active,
        'token_expires_at': account.token_expires_at,
        'created_at': account.created_at,
        'updated_at': account.updated_at,
        'token_ttl_days': get_token_ttl_days(account.wb_api_token)
    }
    
    return AccountResponse(**account_data)


@router.put("/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: int,
    account_data: AccountUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[TokenData, Depends(get_current_user)]
):
    """Обновить аккаунт"""
    account = db.query(Account).filter(Account.id == account_id).first()
    
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Account {account_id} not found"
        )
    
    # Обновляем поля
    update_data = account_data.model_dump(exclude_unset=True)
    
    for field, value in update_data.items():
        setattr(account, field, value)
    
    # Если обновили токен - обновляем срок действия
    if "wb_api_token" in update_data:
        account.token_expires_at = get_token_expiration(account.wb_api_token)
    
    db.commit()
    db.refresh(account)
    
    response_data = {
        'id': account.id,
        'name': account.name,
        'is_active': account.is_active,
        'token_expires_at': account.token_expires_at,
        'created_at': account.created_at,
        'updated_at': account.updated_at,
        'token_ttl_days': get_token_ttl_days(account.wb_api_token)
    }
    
    return AccountResponse(**response_data)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    account_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[TokenData, Depends(get_current_user)]
):
    """Удалить аккаунт (каскадно удалятся все товары)"""
    account = db.query(Account).filter(Account.id == account_id).first()
    
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Account {account_id} not found"
        )
    
    db.delete(account)
    db.commit()
    
    return None
