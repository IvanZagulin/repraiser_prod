import json
import logging
from typing import Optional

import redis as redis_lib

from app.config import settings

logger = logging.getLogger(__name__)

_client: Optional[redis_lib.Redis] = None


def _get_client() -> redis_lib.Redis:
    global _client
    if _client is None:
        _client = redis_lib.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


def cache_get(key: str) -> Optional[dict]:
    """Получить значение из Redis-кеша. Возвращает dict или None."""
    try:
        raw = _get_client().get(key)
        if raw:
            return json.loads(raw)
    except Exception as e:
        logger.warning(f"Redis cache_get({key!r}) error: {e}")
    return None


def cache_set(key: str, value: dict, ttl: int = 180) -> None:
    """Записать dict в Redis-кеш с TTL в секундах."""
    try:
        _get_client().setex(key, ttl, json.dumps(value))
    except Exception as e:
        logger.warning(f"Redis cache_set({key!r}) error: {e}")


def acquire_run_lock(user_id: int, ttl: int = 3900) -> bool:
    """
    Попытка захватить эксклюзивную блокировку прогона для пользователя.
    Возвращает True если блокировка получена, False если прогон уже идёт.
    TTL = 65 мин (чуть больше жёсткого лимита Celery-задачи).
    """
    try:
        key = f"repricer:run_lock:{user_id}"
        return bool(_get_client().set(key, "1", nx=True, ex=ttl))
    except Exception as e:
        logger.warning(f"Redis acquire_run_lock(user={user_id}) error: {e}")
        return True  # если Redis недоступен — разрешаем запуск


def release_run_lock(user_id: int) -> None:
    """Освободить блокировку прогона для пользователя."""
    try:
        _get_client().delete(f"repricer:run_lock:{user_id}")
    except Exception as e:
        logger.warning(f"Redis release_run_lock(user={user_id}) error: {e}")
