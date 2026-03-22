from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Настройки приложения"""
    
    # Database
    DATABASE_URL: str
    
    # JWT
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 24  # 1 сутки (docker-compose может перекрыть)
    
    # Telegram
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None
    
    # SOCKS5 Proxies (format: ip:port:login:password;ip:port:login:password)
    SOCKS5_PROXIES: str = ""
    
    # WB API
    WB_API_BASE_URL: str = "https://discounts-prices-api.wildberries.ru"
    WB_CATALOG_BASE_URL: str = "https://card.wb.ru"
    
    # Repricer settings
    RETRY_ATTEMPTS: int = 5  # Для парсинга через прокси
    PRICE_CHECK_DELAY: int = 10  # Секунд между проверками применения цены
    PRICE_CHECK_ATTEMPTS: int = 3  # Попыток проверки применения

    # Admin (создаётся автоматически при первом запуске)
    ADMIN_EMAIL: str = "admin@example.com"
    ADMIN_PASSWORD: str = "changeme123"

    # Redis / Celery
    REDIS_URL: str = "redis://redis:6379/0"

    # Fernet key для шифрования WB API токенов в БД
    # Генерация: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    FERNET_KEY: Optional[str] = None

    # Sentry (опционально — оставь пустым чтобы отключить)
    SENTRY_DSN: Optional[str] = None

    # SMTP (для приветственных писем)
    SMTP_HOST: str = "smtp.yandex.ru"
    SMTP_PORT: int = 465
    SMTP_USER: str = ""
    SMTP_PASS: str = ""
    SMTP_FROM: str = ""
    APP_URL: str = "https://qvaz.quasar-x.ru"
    
    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = 'ignore'


settings = Settings()
