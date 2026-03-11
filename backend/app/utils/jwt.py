import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
import jwt

logger = logging.getLogger(__name__)


def decode_wb_token(token: str) -> Optional[dict]:
    """
    Декодирование JWT токена WB без проверки подписи

    Args:
        token: JWT токен WB API (может быть зашифрован Fernet — ENC:...)

    Returns:
        Payload токена или None при ошибке
    """
    try:
        if not token:
            return None

        # Если токен зашифрован Fernet — расшифровываем перед декодированием.
        # TypeDecorator должен делать это автоматически, но на случай прямого вызова
        # (например из tests или миграций) делаем явную проверку.
        if isinstance(token, str) and token.startswith("ENC:"):
            try:
                from app.utils.crypto import decrypt_field
                token = decrypt_field(token)
                if not token:
                    return None
            except Exception as dec_err:
                logger.warning(f"Не удалось расшифровать WB токен: {dec_err}")
                return None

        # Декодируем без проверки подписи (options={"verify_signature": False}).
        # ВАЖНО: здесь используется PyJWT (import jwt), а не python-jose.
        # PyJWT бросает jwt.exceptions.DecodeError — это НЕ подкласс jose.JWTError,
        # поэтому ловим широкий Exception.
        payload = jwt.decode(token, options={"verify_signature": False})
        return payload
    except Exception as e:
        logger.warning(f"Ошибка декодирования WB токена: {e}")
        return None


def get_token_expiration(token: str) -> Optional[datetime]:
    """
    Получение времени истечения токена
    
    Args:
        token: JWT токен WB API
        
    Returns:
        datetime или None
    """
    payload = decode_wb_token(token)
    if not payload:
        return None
    
    exp_timestamp = payload.get('exp')
    if not exp_timestamp:
        logger.warning("Токен не содержит поле 'exp'")
        return None
    
    try:
        return datetime.utcfromtimestamp(exp_timestamp)
    except (ValueError, OSError) as e:
        logger.error(f"Ошибка конвертации timestamp: {e}")
        return None


def get_token_ttl_days(token: str) -> Optional[int]:
    """
    Получение оставшегося времени жизни токена в днях
    
    Args:
        token: JWT токен WB API
        
    Returns:
        Количество дней до истечения или None
    """
    expiration = get_token_expiration(token)
    if not expiration:
        return None
    
    now = datetime.utcnow()
    delta = expiration - now
    
    return delta.days if delta.days >= 0 else 0


def is_token_expired(token: str) -> bool:
    """
    Проверка истек ли токен
    
    Args:
        token: JWT токен WB API
        
    Returns:
        True если истек
    """
    expiration = get_token_expiration(token)
    if not expiration:
        return True
    
    return datetime.utcnow() >= expiration


def is_token_expiring_soon(token: str, days_threshold: int = 7) -> bool:
    """
    Проверка истечет ли токен в ближайшие N дней
    
    Args:
        token: JWT токен WB API
        days_threshold: Порог в днях
        
    Returns:
        True если истекает в ближайшие days_threshold дней
    """
    ttl_days = get_token_ttl_days(token)
    if ttl_days is None:
        return True
    
    return ttl_days <= days_threshold
