from pydantic import BaseModel, Field, EmailStr


class LoginRequest(BaseModel):
    """Схема запроса на логин"""
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=6)


class RegisterRequest(BaseModel):
    """Схема запроса на регистрацию"""
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=6, max_length=128)


class TokenResponse(BaseModel):
    """Схема ответа с токеном"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Время жизни токена в секундах")


class TokenData(BaseModel):
    """Данные из JWT токена"""
    user_id: int
    email: str
    is_admin: bool = False
    exp: int
