from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from slowapi import Limiter
import logging

from app.database import get_db
from app.models import Account
from app.models.user import User
from app.schemas.account import (
    AccountCreate, AccountUpdate, AccountResponse, AccountListResponse,
    CredentialsCheckResponse,
)
from app.api.auth import get_current_user
from app.services.activity_logger import log_activity
from app.utils import get_real_ip
from app.utils.jwt import get_token_expiration, get_token_ttl_days

logger = logging.getLogger(__name__)
router = APIRouter()
limiter = Limiter(key_func=get_real_ip)


def _check_ownership(account: Account, current_user: User):
    """404 если аккаунт не принадлежит текущему пользователю"""
    if account.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Account not found")


def _mask(secret: str | None) -> str | None:
    """Хвост ключа для UI: по нему видно, какой ключ стоит, но не сам ключ."""
    if not secret:
        return None
    tail = secret[-4:] if len(secret) >= 4 else secret
    return f"••••{tail}"


def _build_response(account: Account) -> AccountResponse:
    is_wb = account.marketplace == "wb"
    return AccountResponse(**{
        'id': account.id,
        'name': account.name,
        'marketplace': account.marketplace,
        'is_active': account.is_active,
        'token_expires_at': account.token_expires_at,
        'created_at': account.created_at,
        'updated_at': account.updated_at,
        'token_ttl_days': get_token_ttl_days(account.wb_api_token) if is_wb and account.wb_api_token else None,
        'wb_token_masked': _mask(account.wb_api_token) if is_wb else None,
        'ozon_client_id': account.ozon_client_id if not is_wb else None,
        'ozon_api_key_masked': _mask(account.ozon_api_key) if not is_wb else None,
        'has_credentials': bool(account.wb_api_token) if is_wb
                           else bool(account.ozon_client_id and account.ozon_api_key),
    })


@router.get("", response_model=AccountListResponse)
@limiter.limit("60/minute")
async def list_accounts(
    request: Request,
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
@limiter.limit("20/minute")
async def create_account(
    request: Request,
    account_data: AccountCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Создать новый WB-аккаунт"""
    # Plan limit check
    from app.api.plan_guard import check_account_limit
    check_account_limit(db, current_user)

    token_expires_at = (
        get_token_expiration(account_data.wb_api_token)
        if account_data.marketplace == "wb" and account_data.wb_api_token
        else None
    )
    account = Account(
        user_id=current_user.id,
        name=account_data.name,
        marketplace=account_data.marketplace,
        wb_api_token=account_data.wb_api_token,
        ozon_client_id=account_data.ozon_client_id,
        ozon_api_key=account_data.ozon_api_key,
        token_expires_at=token_expires_at,
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    try:
        log_activity(db, current_user.id, "account_created", account_id=account.id, details={"name": account.name})
        db.commit()
    except Exception as e:
        logger.warning(f"Failed to log activity 'account_created': {e}")

    # Фоновая синхронизация каталога WB (все карточки аккаунта)
    if account.marketplace == "wb":
        try:
            from app.tasks.catalog_sync import sync_wb_catalog_task
            sync_wb_catalog_task.delay(account.id)
        except Exception as e:
            logger.warning(f"Failed to start catalog sync for account {account.id}: {e}")

    return _build_response(account)


@router.get("/{account_id}", response_model=AccountResponse)
async def get_account(
    account_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    account = db.query(Account).filter(
        Account.id == account_id, Account.user_id == current_user.id
    ).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return _build_response(account)


@router.put("/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: int,
    account_data: AccountUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    account = db.query(Account).filter(
        Account.id == account_id, Account.user_id == current_user.id
    ).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    update_data = account_data.model_dump(exclude_unset=True)
    old_marketplace = account.marketplace
    token_changed = "wb_api_token" in update_data
    for field, value in update_data.items():
        setattr(account, field, value)
    if account.marketplace == "wb":
        if not account.wb_api_token:
            raise HTTPException(status_code=400, detail="wb_api_token is required for WB accounts")
        if token_changed or old_marketplace != account.marketplace:
            account.token_expires_at = get_token_expiration(account.wb_api_token)
    elif account.marketplace == "ozon":
        if not account.ozon_client_id or not account.ozon_api_key:
            raise HTTPException(status_code=400, detail="ozon_client_id and ozon_api_key are required for Ozon accounts")
        account.token_expires_at = None
    else:
        raise HTTPException(status_code=400, detail="Unsupported marketplace")

    db.commit()
    db.refresh(account)

    if token_changed or "ozon_api_key" in update_data:
        try:
            log_activity(db, current_user.id, "account_key_changed",
                         account_id=account.id, details={"name": account.name})
            db.commit()
        except Exception as e:
            logger.warning(f"Failed to log activity 'account_key_changed': {e}")

    # При смене токена — перекачиваем каталог с новым ключом
    if account.marketplace == "wb" and (token_changed or old_marketplace != account.marketplace):
        try:
            from app.tasks.catalog_sync import sync_wb_catalog_task
            sync_wb_catalog_task.delay(account.id)
        except Exception as e:
            logger.warning(f"Failed to start catalog sync for account {account.id}: {e}")

    return _build_response(account)


@router.post("/{account_id}/check-credentials", response_model=CredentialsCheckResponse)
@limiter.limit("10/minute")
async def check_credentials(
    request: Request,
    account_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Проверить сохранённый API-ключ живым запросом к маркетплейсу"""
    account = db.query(Account).filter(
        Account.id == account_id, Account.user_id == current_user.id
    ).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    if account.marketplace == "wb":
        if not account.wb_api_token:
            return CredentialsCheckResponse(ok=False, message="Ключ не задан")
        from app.services.wb_api import WBAPIClient
        ok, message = await WBAPIClient(account.wb_api_token).check_token()
        return CredentialsCheckResponse(
            ok=ok, message=message,
            token_ttl_days=get_token_ttl_days(account.wb_api_token),
        )

    if account.marketplace == "ozon":
        if not (account.ozon_client_id and account.ozon_api_key):
            return CredentialsCheckResponse(ok=False, message="Ключ не задан")
        from app.services.ozon_api import OzonAPIClient
        ok, message = await OzonAPIClient(
            account.ozon_client_id, account.ozon_api_key
        ).check_credentials()
        return CredentialsCheckResponse(ok=ok, message=message)

    raise HTTPException(status_code=400, detail="Unsupported marketplace")


@router.post("/{account_id}/sync-catalog", status_code=status.HTTP_202_ACCEPTED)
async def sync_catalog(
    account_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Запустить синхронизацию каталога WB (все карточки + фото) для аккаунта"""
    account = db.query(Account).filter(
        Account.id == account_id, Account.user_id == current_user.id
    ).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if account.marketplace != "wb":
        raise HTTPException(status_code=400, detail="Catalog sync is available for WB accounts only")

    try:
        from app.tasks.catalog_sync import sync_wb_catalog_task
        sync_wb_catalog_task.delay(account_id)
        return {"status": "queued", "message": "Синхронизация каталога запущена"}
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Celery недоступен при sync account_id={account_id}: {e}")
        raise HTTPException(status_code=503, detail="Сервис временно недоступен, попробуйте позже")



@router.post("/{account_id}/sync-products", status_code=status.HTTP_202_ACCEPTED)
async def sync_products(
    account_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Создать товары из wb_catalog (без вызова WB API) для аккаунта"""
    account = db.query(Account).filter(
        Account.id == account_id, Account.user_id == current_user.id
    ).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if account.marketplace != "wb":
        raise HTTPException(status_code=400, detail="Product sync is available for WB accounts only")

    try:
        from app.tasks.products_sync import sync_products_from_catalog_task
        sync_products_from_catalog_task.delay(account_id)
        return {"status": "queued", "message": "Синхронизация товаров запущена"}
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Celery недоступен при sync-products account_id={account_id}: {e}")
        raise HTTPException(status_code=503, detail="Сервис временно недоступен, попробуйте позже")

@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    account_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    account = db.query(Account).filter(
        Account.id == account_id, Account.user_id == current_user.id
    ).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    db.delete(account)
    db.commit()
    return None
