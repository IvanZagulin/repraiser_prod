import asyncio
import logging
from typing import Optional

from app.celery_app import celery_app
from app.database import SessionLocal
from app.redis_client import acquire_run_lock, release_run_lock

logger = logging.getLogger(__name__)

# Ошибки соединения/сети — допускают retry; логические ошибки — нет
_NETWORK_ERRORS = (ConnectionError, TimeoutError, OSError)


@celery_app.task(
    bind=True,
    name="repricer.run_for_user",
    max_retries=2,
    default_retry_delay=60,
    time_limit=3900,   # 65 мин — жёсткий лимит (прерывает задачу)
)
def run_repricer_for_user(
    self,
    user_id: int,
    product_ids: list,
    run_type: str = "scheduled",
    existing_run_id: Optional[int] = None,
    dry_run: bool = False,
):
    """Запускает репрайсер для одного пользователя в Celery-воркере."""
    # Защита от двойного запуска для одного пользователя
    if not acquire_run_lock(user_id):
        logger.warning(f"[Celery] user={user_id}: прогон уже идёт, пропускаем дублирующийся запуск")
        return {"status": "skipped", "reason": "already running"}

    db = SessionLocal()
    try:
        # Проверяем что пользователь существует и активен — защита от
        # передачи произвольного user_id через Celery/Redis
        from app.models.user import User
        user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
        if not user:
            logger.warning(f"[Celery] user_id={user_id} не найден или заблокирован — задача пропущена")
            return {"status": "skipped", "reason": "user not found or inactive"}

        from app.services.repricer_engine import RepricerEngine
        engine = RepricerEngine(db, user_id=user_id)
        run = asyncio.run(engine.run_repricer(
            product_ids=product_ids,
            run_type=run_type,
            existing_run_id=existing_run_id,
            dry_run=dry_run,
        ))
        logger.info(
            f"[Celery] user={user_id} run={run.id}: "
            f"updated={run.updated_count} skipped={run.skipped_count} errors={run.error_count}"
        )
        return {"run_id": run.id, "status": run.status, "updated": run.updated_count}
    except Exception as exc:
        logger.error(f"[Celery] user={user_id} repricer failed: {exc}", exc_info=True)
        # Retry только при сетевых/connection ошибках
        if isinstance(exc, _NETWORK_ERRORS):
            raise self.retry(exc=exc)
        raise
    finally:
        db.close()
        release_run_lock(user_id)
