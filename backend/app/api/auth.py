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
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, TokenData

router = APIRouter()
security = HTTPBearer()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ─── helpers ──────────────────────────────────────────────────────────────────

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


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

@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    request: RegisterRequest,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
):
    """Регистрация нового пользователя"""
    existing = db.query(User).filter(User.email == request.email.lower()).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Пользователь с таким email уже существует",
        )

    user = User(
        email=request.email.lower().strip(),
        password_hash=hash_password(request.password),
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Копируем глобальные дефолтные настройки новому пользователю
    _copy_default_settings(db, user.id)

    # Отправляем welcome email в фоне (не блокирует ответ)
    from app.services.email_service import send_welcome_email
    background_tasks.add_task(send_welcome_email, user.email)

    token = create_access_token(user)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.JWT_EXPIRE_HOURS * 3600,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    request: LoginRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """Вход в систему"""
    user = db.query(User).filter(User.email == request.email.lower()).first()

    if not user or not verify_password(request.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный email или пароль",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Аккаунт заблокирован",
        )

    token = create_access_token(user)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.JWT_EXPIRE_HOURS * 3600,
    )


@router.get("/verify")
async def verify_token(current_user: Annotated[User, Depends(get_current_user)]):
    """Проверка валидности токена"""
    return {
        "user_id": current_user.id,
        "email": current_user.email,
        "is_admin": current_user.is_admin,
    }


# ─── internal helpers ─────────────────────────────────────────────────────────

def _copy_default_settings(db: Session, user_id: int):
    """Копирует глобальные настройки (user_id=NULL) в настройки нового пользователя"""
    from app.models.app_setting import AppSetting
    defaults = db.query(AppSetting).filter(AppSetting.user_id == None).all()
    for d in defaults:
        exists = db.query(AppSetting).filter(
            AppSetting.key == d.key, AppSetting.user_id == user_id
        ).first()
        if not exists:
            db.add(AppSetting(key=d.key, value=d.value, user_id=user_id))
    db.commit()
