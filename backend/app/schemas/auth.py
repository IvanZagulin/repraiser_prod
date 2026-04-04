from pydantic import BaseModel, Field, EmailStr, field_validator


COMMON_PASSWORDS = {
    "password", "password1", "password123", "12345678", "123456789",
    "qwerty", "qwerty123", "abc123", "letmein", "admin",
    "welcome", "iloveyou", "11111111", "123123123", "12341234",
}


def validate_password(v: str) -> str:
    """Validate password strength."""
    if len(v) < 8:
        raise ValueError("Минимум 8 символов")
    if not any(c.isupper() for c in v):
        raise ValueError("Нужна хотя бы одна заглавная буква")
    if not any(c.isdigit() for c in v):
        raise ValueError("Нужна хотя бы одна цифра")
    if v.lower() in COMMON_PASSWORDS:
        raise ValueError("Этот пароль слишком распространён")
    return v



class LoginRequest(BaseModel):
    """Схема запроса на логин"""
    email: EmailStr = Field(..., max_length=255)
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def validate_pw(cls, v): return validate_password(v)


class RegisterRequest(BaseModel):
    """Схема запроса на регистрацию"""
    email: EmailStr = Field(..., max_length=255)
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def validate_pw(cls, v): return validate_password(v)


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
    email: EmailStr = Field(..., max_length=255)
    code: str = Field(..., min_length=6, max_length=6)


class ResendCodeRequest(BaseModel):
    """Повторная отправка кода подтверждения"""
    email: EmailStr = Field(..., max_length=255)


class ForgotPasswordRequest(BaseModel):
    """Запрос сброса пароля"""
    email: EmailStr = Field(..., max_length=255)


class ResetPasswordRequest(BaseModel):
    """Установка нового пароля по коду"""
    email: EmailStr = Field(..., max_length=255)
    code: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def validate_pw(cls, v): return validate_password(v)


class MessageResponse(BaseModel):
    """Общий ответ с сообщением"""
    message: str


class ChangePasswordConfirmRequest(BaseModel):
    """Подтверждение смены пароля по коду из email"""
    code: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def validate_pw(cls, v): return validate_password(v)
