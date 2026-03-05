from datetime import datetime, timedelta
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings
from app.schemas.auth import LoginRequest, TokenResponse, TokenData

router = APIRouter()
security = HTTPBearer()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# Временные учетные данные (в продакшене использовать БД)
USERS = {
    "admin": {
        "username": "admin",
        "password_hash": pwd_context.hash("admin123"),  # Изменить в продакшене!
    }
}


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Проверка пароля"""
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict) -> str:
    """Создание JWT токена"""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(hours=settings.JWT_EXPIRE_HOURS)
    to_encode.update({"exp": expire})
    
    encoded_jwt = jwt.encode(
        to_encode,
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM
    )
    return encoded_jwt


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]
) -> TokenData:
    """
    Dependency для получения текущего пользователя из JWT токена
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        token = credentials.credentials
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM]
        )
        username: str = payload.get("sub")
        exp: int = payload.get("exp")
        
        if username is None or exp is None:
            raise credentials_exception
        
        token_data = TokenData(username=username, exp=exp)
        return token_data
        
    except JWTError:
        raise credentials_exception


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    """
    Вход в систему и получение JWT токена
    
    Дефолтные креды для тестирования:
    - username: admin
    - password: admin123
    """
    user = USERS.get(request.username)
    
    if not user or not verify_password(request.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Создаем токен
    access_token = create_access_token(data={"sub": user["username"]})
    
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.JWT_EXPIRE_HOURS * 3600  # В секундах
    )


@router.get("/verify")
async def verify_token(current_user: Annotated[TokenData, Depends(get_current_user)]):
    """Проверка валидности токена"""
    return {
        "username": current_user.username,
        "expires_at": datetime.fromtimestamp(current_user.exp).isoformat()
    }
