import json
import logging
from typing import Iterable, Optional, Set

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


def cache_delete(key: str) -> None:
    """Удалить ключ из Redis-кеша. Тихо игнорирует отсутствующий ключ."""
    try:
        _get_client().delete(key)
    except Exception as e:
        logger.warning(f"Redis cache_delete({key!r}) error: {e}")


def acquire_run_lock(user_id: int, ttl: int = 10860) -> bool:
    """
    Попытка захватить эксклюзивную блокировку прогона для пользователя.
    Возвращает True если блокировка получена, False если прогон уже идёт.
    TTL = 181 мин (чуть больше time_limit=10800s Celery-задачи repricer.run_for_user).
    Иначе лок истекает раньше задачи и планировщик запускает параллельный прогон
    (инцидент 2026-06-10: медленный прогон из-за шторма 429 WB пережил свой лок).
    """
    try:
        key = f"repricer:run_lock:{user_id}"
        return bool(_get_client().set(key, "1", nx=True, ex=ttl))
    except Exception as e:
        logger.error(f"Redis acquire_run_lock(user={user_id}) error: {e} — блокируем запуск во избежание дублирования")
        return False  # если Redis недоступен — запрещаем запуск (safer than allowing concurrent runs)


def release_run_lock(user_id: int) -> None:
    """Освободить блокировку прогона для пользователя."""
    try:
        _get_client().delete(f"repricer:run_lock:{user_id}")
    except Exception as e:
        logger.warning(f"Redis release_run_lock(user={user_id}) error: {e}")


_RELEASE_OWNED_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


def _product_lock_key(user_id: int, product_id: int) -> str:
    return f"repricer:product_lock:{user_id}:{product_id}"


def acquire_product_locks(
    user_id: int,
    product_ids: Iterable[int],
    owner: str,
    ttl: int = 900,
) -> Optional[Set[int]]:
    """Acquire per-product locks in one Redis pipeline.

    A lock already held by the same owner is treated as re-entrant and its TTL
    is refreshed.  Returning an empty/partial set is fail-closed: callers must
    not process product IDs absent from the returned set.
    """
    ids = list(dict.fromkeys(int(product_id) for product_id in product_ids))
    if not ids:
        return set()
    try:
        client = _get_client()
        keys = [_product_lock_key(user_id, product_id) for product_id in ids]
        pipe = client.pipeline(transaction=False)
        for key in keys:
            pipe.set(key, owner, nx=True, ex=ttl)
        created = pipe.execute()
        acquired = {
            product_id
            for product_id, was_created in zip(ids, created)
            if was_created
        }

        blocked = [
            (product_id, key)
            for product_id, key, was_created in zip(ids, keys, created)
            if not was_created
        ]
        if blocked:
            current_owners = client.mget([key for _, key in blocked])
            reentrant = [
                (product_id, key)
                for (product_id, key), current_owner in zip(blocked, current_owners)
                if current_owner == owner
            ]
            if reentrant:
                renew = client.pipeline(transaction=False)
                for _, key in reentrant:
                    renew.expire(key, ttl)
                renew.execute()
                acquired.update(product_id for product_id, _ in reentrant)
        return acquired
    except Exception as e:
        logger.error(
            "Redis acquire_product_locks(user=%s, count=%s) error: %s",
            user_id,
            len(ids),
            e,
        )
        return None


def release_product_locks(
    user_id: int,
    product_ids: Iterable[int],
    owner: str,
) -> None:
    """Release only product locks still owned by ``owner``."""
    ids = list(dict.fromkeys(int(product_id) for product_id in product_ids))
    if not ids:
        return
    try:
        pipe = _get_client().pipeline(transaction=False)
        for product_id in ids:
            pipe.eval(
                _RELEASE_OWNED_LOCK_SCRIPT,
                1,
                _product_lock_key(user_id, product_id),
                owner,
            )
        pipe.execute()
    except Exception as e:
        logger.warning(
            "Redis release_product_locks(user=%s, count=%s) error: %s",
            user_id,
            len(ids),
            e,
        )


# ─── Catalog sync per-account lock ────────────────────────────────────────────
# Чтобы каталог-синк не дублировался при шторме (рестарт backend / повторный
# /accounts PUT и т.п.), захватываем эксклюзивный lock на account_id.

def acquire_catalog_sync_lock(account_id: int, ttl: int = 3600) -> bool:
    """Получить эксклюзивный lock на catalog sync для конкретного аккаунта.

    TTL = 1 час: больше типичного времени sync'а, страховка от deadlock'а
    при креше воркера. Если Redis недоступен — отказываем (safer default),
    лучше пропустить синк, чем устроить дубликат-шторм.
    """
    try:
        key = f"catalog:sync_lock:{account_id}"
        return bool(_get_client().set(key, "1", nx=True, ex=ttl))
    except Exception as e:
        logger.error(
            f"Redis acquire_catalog_sync_lock(account={account_id}) error: {e} — "
            "блокируем запуск во избежание дублирования"
        )
        return False


def release_catalog_sync_lock(account_id: int) -> None:
    """Освободить lock catalog-sync."""
    try:
        _get_client().delete(f"catalog:sync_lock:{account_id}")
    except Exception as e:
        logger.warning(f"Redis release_catalog_sync_lock(account={account_id}) error: {e}")
