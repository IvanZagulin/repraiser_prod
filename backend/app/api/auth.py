import secrets
import string
from datetime import datetime, timedelta
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request, status, BackgroundTasks
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
from slowapi import Limiter
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.utils import get_real_ip
from app.database import get_db
from app.models.user import User
from app.schemas.auth import (
    LoginRequest, RegisterRequest, TokenResponse, TokenData,
    VerifyEmailRequest, ResendCodeRequest,
    ForgotPasswordRequest, ResetPasswordRequest, MessageResponse,
    ChangePasswordConfirmRequest,
)

router = APIRouter()
security = HTTPBearer()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
limiter = Limiter(key_func=get_real_ip)

# Максимум неудачных попыток ввода кода до инвалидации
MAX_CODE_ATTEMPTS = 5


# ─── helpers ──────────────────────────────────────────────────────────────────

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def generate_code() -> str:
    """Генерирует криптографически стойкий 6-значный цифровой код."""
    return ''.join(secrets.choice(string.digits) for _ in range(6))


def create_access_token(user: User) -> str:
    expire = datetime.utcnow() + timedelta(hours=settings.JWT_EXPIRE_HOURS)
    payload = {
        "sub": user.email,
        "user_id": user.id,
        "is_admin": user.is_admin,
        "token_version": user.token_version or 1,  # для инвалидации после смены пароля
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """Dependency — возвращает объект User из JWT токена"""
    exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
        user_id: int = payload.get("user_id")
        if user_id is None:
            raise exc
    except JWTError:
        raise exc

    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if user is None:
        raise exc

    # Проверка версии токена — инвалидирует старые JWT после смены пароля
    if payload.get("token_version", 1) != (user.token_version or 1):
        raise exc

    return user


# ─── endpoints ────────────────────────────────────────────────────────────────

@router.post("/register", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def register(
    request: Request,
    body: RegisterRequest,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
):
    """Регистрация нового пользователя — отправляет код подтверждения на email."""
    existing = db.query(User).filter(User.email == body.email.lower()).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Пользователь с таким email уже существует",
        )

    code = generate_code()
    expires_at = datetime.utcnow() + timedelta(minutes=15)

    user = User(
        email=body.email.lower().strip(),
        password_hash=hash_password(body.password),
        is_active=True,
        is_admin=False,
        is_verified=False,
        verification_code=code,
        verification_code_expires_at=expires_at,
        verification_attempts=0,
    )
    db.add(user)
    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Пользователь с таким email уже существует",
        )

    _copy_default_settings(db, user.id)

    # Код подтверждения — синхронно (нужен сразу)
    from app.services.email_service import send_verification_code, send_welcome_email
    send_verification_code(body.email.lower().strip(), code)

    # Welcome email — в фоне
    background_tasks.add_task(send_welcome_email, user.email)

    return MessageResponse(message="Код подтверждения отправлен на ваш email")


@router.post("/verify-email", response_model=TokenResponse)
@limiter.limit("10/minute")
async def verify_email(
    request: Request,
    body: VerifyEmailRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """Подтверждение email по 6-значному коду → возвращает JWT."""
    user = db.query(User).filter(User.email == body.email.lower()).first()
    if not user:
        raise HTTPException(status_code=400, detail="Неверный email или код")

    if user.is_verified:
        raise HTTPException(status_code=400, detail="Email уже подтверждён")

    if user.verification_code_expires_at and user.verification_code_expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Код истёк. Запросите новый")

    if not user.verification_code or user.verification_code != body.code:
        user.verification_attempts = (user.verification_attempts or 0) + 1
        if user.verification_attempts >= MAX_CODE_ATTEMPTS:
            user.verification_code = None
            user.verification_code_expires_at = None
            user.verification_attempts = 0
            db.commit()
            raise HTTPException(status_code=400, detail="Превышено число попыток. Запросите новый код")
        db.commit()
        raise HTTPException(status_code=400, detail=f"Неверный код. Осталось попыток: {MAX_CODE_ATTEMPTS - user.verification_attempts}")

    user.is_verified = True
    user.verification_code = None
    user.verification_code_expires_at = None
    user.verification_attempts = 0
    db.commit()

    token = create_access_token(user)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.JWT_EXPIRE_HOURS * 3600,
    )


@router.post("/resend-code", response_model=MessageResponse)
@limiter.limit("3/minute")
async def resend_code(
    request: Request,
    body: ResendCodeRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """Повторная отправка кода подтверждения email."""
    user = db.query(User).filter(User.email == body.email.lower()).first()

    if not user or user.is_verified:
        return MessageResponse(message="Если аккаунт существует и не подтверждён, код отправлен")

    code = generate_code()
    user.verification_code = code
    user.verification_code_expires_at = datetime.utcnow() + timedelta(minutes=15)
    user.verification_attempts = 0
    db.commit()

    from app.services.email_service import send_verification_code
    send_verification_code(user.email, code)

    return MessageResponse(message="Если аккаунт существует и не подтверждён, код отправлен")


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(
    request: Request,
    body: LoginRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """Вход в систему."""
    user = db.query(User).filter(User.email == body.email.lower()).first()

    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный email или пароль",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Аккаунт заблокирован")

    if not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email не подтверждён",
            headers={"X-Needs-Verification": "true"},
        )

    token = create_access_token(user)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.JWT_EXPIRE_HOURS * 3600,
    )


@router.post("/forgot-password", response_model=MessageResponse)
@limiter.limit("3/minute")
async def forgot_password(
    request: Request,
    body: ForgotPasswordRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """Отправляет код сброса пароля на email."""
    user = db.query(User).filter(User.email == body.email.lower()).first()

    if not user:
        return MessageResponse(message="Если аккаунт существует, код отправлен на email")

    code = generate_code()
    user.reset_code = code
    user.reset_code_expires_at = datetime.utcnow() + timedelta(minutes=15)
    user.reset_attempts = 0
    db.commit()

    from app.services.email_service import send_reset_code
    send_reset_code(user.email, code)

    return MessageResponse(message="Если аккаунт существует, код отправлен на email")


@router.post("/reset-password", response_model=TokenResponse)
@limiter.limit("10/minute")
async def reset_password(
    request: Request,
    body: ResetPasswordRequest,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
):
    """Сброс пароля по коду → автологин (возвращает JWT)."""
    user = db.query(User).filter(User.email == body.email.lower()).first()
    if not user:
        raise HTTPException(status_code=400, detail="Неверный email или код")

    if not user.reset_code or user.reset_code != body.code:
        user.reset_attempts = (user.reset_attempts or 0) + 1
        if user.reset_attempts >= MAX_CODE_ATTEMPTS:
            user.reset_code = None
            user.reset_code_expires_at = None
            user.reset_attempts = 0
            db.commit()
            raise HTTPException(status_code=400, detail="Превышено число попыток. Запросите новый код")
        db.commit()
        raise HTTPException(status_code=400, detail=f"Неверный код. Осталось попыток: {MAX_CODE_ATTEMPTS - user.reset_attempts}")

    if not user.reset_code_expires_at or user.reset_code_expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Код истёк. Запросите новый")

    user.password_hash = hash_password(body.new_password)
    user.reset_code = None
    user.reset_code_expires_at = None
    user.reset_attempts = 0
    user.is_verified = True  # На случай если не был верифицирован
    user.token_version = (user.token_version or 1) + 1  # Инвалидируем старые JWT
    db.commit()

    # Уведомляем пользователя о смене пароля в фоне
    from app.services.email_service import send_password_changed_notification
    background_tasks.add_task(send_password_changed_notification, user.email)

    token = create_access_token(user)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.JWT_EXPIRE_HOURS * 3600,
    )


@router.post("/change-password/send-code", response_model=MessageResponse)
@limiter.limit("3/minute")
async def change_password_send_code(
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Отправляет код подтверждения смены пароля на email текущего пользователя."""
    code = generate_code()
    current_user.reset_code = code
    current_user.reset_code_expires_at = datetime.utcnow() + timedelta(minutes=15)
    current_user.reset_attempts = 0
    db.commit()

    from app.services.email_service import send_change_password_code
    send_change_password_code(current_user.email, code)

    return MessageResponse(message="Код подтверждения отправлен на ваш email")


@router.post("/change-password/confirm", response_model=MessageResponse)
@limiter.limit("10/minute")
async def change_password_confirm(
    request: Request,
    body: ChangePasswordConfirmRequest,
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Подтверждает смену пароля по коду из email."""
    if not current_user.reset_code or current_user.reset_code != body.code:
        current_user.reset_attempts = (current_user.reset_attempts or 0) + 1
        if current_user.reset_attempts >= MAX_CODE_ATTEMPTS:
            current_user.reset_code = None
            current_user.reset_code_expires_at = None
            current_user.reset_attempts = 0
            db.commit()
            raise HTTPException(status_code=400, detail="Превышено число попыток. Запросите новый код")
        db.commit()
        raise HTTPException(
            status_code=400,
            detail=f"Неверный код. Осталось попыток: {MAX_CODE_ATTEMPTS - current_user.reset_attempts}"
        )

    if not current_user.reset_code_expires_at or current_user.reset_code_expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Код истёк. Запросите новый")

    current_user.password_hash = hash_password(body.new_password)
    current_user.reset_code = None
    current_user.reset_code_expires_at = None
    current_user.reset_attempts = 0
    current_user.token_version = (current_user.token_version or 1) + 1  # Инвалидируем старые JWT
    db.commit()

    # Уведомляем пользователя о смене пароля в фоне
    from app.services.email_service import send_password_changed_notification
    background_tasks.add_task(send_password_changed_notification, current_user.email)

    return MessageResponse(message="Пароль успешно изменён")


@router.get("/verify")
async def verify_token(current_user: Annotated[User, Depends(get_current_user)]):
    """Проверка валидности токена."""
    return {
        "user_id": current_user.id,
        "email": current_user.email,
        "is_admin": current_user.is_admin,
    }


# ─── internal helpers ─────────────────────────────────────────────────────────

def _copy_default_settings(db: Session, user_id: int):
    """Копирует глобальные настройки (user_id=NULL) в настройки нового пользователя."""
    from app.models.app_setting import AppSetting
    defaults = db.query(AppSetting).filter(AppSetting.user_id == None).all()
    for d in defaults:
        exists = db.query(AppSetting).filter(
            AppSetting.key == d.key, AppSetting.user_id == user_id
        ).first()
        if not exists:
            db.add(AppSetting(key=d.key, value=d.value, user_id=user_id))
    db.commit()
