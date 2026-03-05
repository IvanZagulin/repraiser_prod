import logging
from datetime import datetime, timedelta
from typing import Optional
import jwt
from jose import JWTError

logger = logging.getLogger(__name__)


def decode_wb_token(token: str) -> Optional[dict]:
    """
    Декодирование JWT токена WB без проверки подписи
    
    Args:
        token: JWT токен WB API
        
    Returns:
        Payload токена или None при ошибке
    """
    try:
        # Декодируем без проверки подписи (options={"verify_signature": False})
        payload = jwt.decode(token, options={"verify_signature": False})
        return payload
    except JWTError as e:
        logger.error(f"Ошибка декодирования WB токена: {e}")
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
        return datetime.fromtimestamp(exp_timestamp)
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
