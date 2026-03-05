from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Схема запроса на логин"""
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=6)


class TokenResponse(BaseModel):
    """Схема ответа с токеном"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Время жизни токена в секундах")


class TokenData(BaseModel):
    """Данные из JWT токена"""
    username: str
    exp: int
