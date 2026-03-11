from pydantic import BaseModel, Field, EmailStr


class LoginRequest(BaseModel):
    """Схема запроса на логин"""
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=8)


class RegisterRequest(BaseModel):
    """Схема запроса на регистрацию"""
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)


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


class VerifyEmailRequest(BaseModel):
    """Подтверждение email по коду"""
    email: str = Field(..., min_length=3, max_length=255)
    code: str = Field(..., min_length=6, max_length=6)


class ResendCodeRequest(BaseModel):
    """Повторная отправка кода подтверждения"""
    email: str = Field(..., min_length=3, max_length=255)


class ForgotPasswordRequest(BaseModel):
    """Запрос сброса пароля"""
    email: str = Field(..., min_length=3, max_length=255)


class ResetPasswordRequest(BaseModel):
    """Установка нового пароля по коду"""
    email: str = Field(..., min_length=3, max_length=255)
    code: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=8, max_length=128)


class MessageResponse(BaseModel):
    """Общий ответ с сообщением"""
    message: str


class ChangePasswordConfirmRequest(BaseModel):
    """Подтверждение смены пароля по коду из email"""
    code: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=8, max_length=128)
