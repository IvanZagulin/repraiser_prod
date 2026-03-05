from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Настройки приложения"""
    
    # Database
    DATABASE_URL: str
    
    # JWT
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 24
    
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
    
    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = 'ignore'


settings = Settings()
