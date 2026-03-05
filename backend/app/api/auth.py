import random
import string
from datetime import datetime, timedelta
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.user import User
from app.schemas.auth import (
    LoginRequest, RegisterRequest, TokenResponse, TokenData,
    VerifyEmailRequest, ResendCodeRequest,
    ForgotPasswordRequest, ResetPasswordRequest, MessageResponse,
)

router = APIRouter()
security = HTTPBearer()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ─── helpers ──────────────────────────────────────────────────────────────────

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def generate_code() -> str:
    """Генерирует 6-значный цифровой код."""
    return ''.join(random.choices(string.digits, k=6))


def create_access_token(user: User) -> str:
    expire = datetime.utcnow() + timedelta(hours=settings.JWT_EXPIRE_HOURS)
    payload = {
        "sub": user.email,
        "user_id": user.id,
        "is_admin": user.is_admin,
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
    return user


# ─── endpoints ────────────────────────────────────────────────────────────────

@router.post("/register", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def register(
    request: RegisterRequest,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
):
    """Регистрация нового пользователя — отправляет код подтверждения на email."""
    existing = db.query(User).filter(User.email == request.email.lower()).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Пользователь с таким email уже существует",
        )

    code = generate_code()
    expires_at = datetime.utcnow() + timedelta(minutes=15)

    user = User(
        email=request.email.lower().strip(),
        password_hash=hash_password(request.password),
        is_active=True,
        is_admin=False,
        is_verified=False,
        verification_code=code,
        verification_code_expires_at=expires_at,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    _copy_default_settings(db, user.id)

    # Код подтверждения — синхронно (нужен сразу)
    from app.services.email_service import send_verification_code, send_welcome_email
    send_verification_code(user.email, code)

    # Welcome email — в фоне
    background_tasks.add_task(send_welcome_email, user.email)

    return MessageResponse(message="Код подтверждения отправлен на ваш email")


@router.post("/verify-email", response_model=TokenResponse)
async def verify_email(
    request: VerifyEmailRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """Подтверждение email по 6-значному коду → возвращает JWT."""
    user = db.query(User).filter(User.email == request.email.lower()).first()
    if not user:
        raise HTTPException(status_code=400, detail="Неверный email или код")

    if user.is_verified:
        raise HTTPException(status_code=400, detail="Email уже подтверждён")

    if not user.verification_code or user.verification_code != request.code:
        raise HTTPException(status_code=400, detail="Неверный код подтверждения")

    if user.verification_code_expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Код истёк. Запросите новый")

    user.is_verified = True
    user.verification_code = None
    user.verification_code_expires_at = None
    db.commit()

    token = create_access_token(user)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.JWT_EXPIRE_HOURS * 3600,
    )


@router.post("/resend-code", response_model=MessageResponse)
async def resend_code(
    request: ResendCodeRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """Повторная отправка кода подтверждения email."""
    user = db.query(User).filter(User.email == request.email.lower()).first()

    if not user or user.is_verified:
        return MessageResponse(message="Если аккаунт существует и не подтверждён, код отправлен")

    code = generate_code()
    user.verification_code = code
    user.verification_code_expires_at = datetime.utcnow() + timedelta(minutes=15)
    db.commit()

    from app.services.email_service import send_verification_code
    send_verification_code(user.email, code)

    return MessageResponse(message="Если аккаунт существует и не подтверждён, код отправлен")


@router.post("/login", response_model=TokenResponse)
async def login(
    request: LoginRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """Вход в систему."""
    user = db.query(User).filter(User.email == request.email.lower()).first()

    if not user or not verify_password(request.password, user.password_hash):
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
async def forgot_password(
    request: ForgotPasswordRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """Отправляет код сброса пароля на email."""
    user = db.query(User).filter(User.email == request.email.lower()).first()

    if not user:
        return MessageResponse(message="Если аккаунт существует, код отправлен на email")

    code = generate_code()
    user.reset_code = code
    user.reset_code_expires_at = datetime.utcnow() + timedelta(minutes=15)
    db.commit()

    from app.services.email_service import send_reset_code
    send_reset_code(user.email, code)

    return MessageResponse(message="Если аккаунт существует, код отправлен на email")


@router.post("/reset-password", response_model=TokenResponse)
async def reset_password(
    request: ResetPasswordRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """Сброс пароля по коду → автологин (возвращает JWT)."""
    user = db.query(User).filter(User.email == request.email.lower()).first()
    if not user:
        raise HTTPException(status_code=400, detail="Неверный email или код")

    if not user.reset_code or user.reset_code != request.code:
        raise HTTPException(status_code=400, detail="Неверный код сброса")

    if user.reset_code_expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Код истёк. Запросите новый")

    user.password_hash = hash_password(request.new_password)
    user.reset_code = None
    user.reset_code_expires_at = None
    user.is_verified = True  # На случай если не был верифицирован
    db.commit()

    token = create_access_token(user)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.JWT_EXPIRE_HOURS * 3600,
    )


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
