import asyncio
import logging

from app.celery_app import celery_app
from app.database import SessionLocal

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="repricer.run_for_user",
    max_retries=2,
    default_retry_delay=60,
    time_limit=3900,   # 65 мин — жёсткий лимит (прерывает задачу)
)
def run_repricer_for_user(self, user_id: int, product_ids: list, run_type: str = "scheduled", dry_run: bool = False):
    """Запускает репрайсер для одного пользователя в Celery-воркере."""
    db = SessionLocal()
    try:
        from app.services.repricer_engine import RepricerEngine
        engine = RepricerEngine(db, user_id=user_id)
        run = asyncio.run(engine.run_repricer(product_ids=product_ids, run_type=run_type, dry_run=dry_run))
        logger.info(
            f"[Celery] user={user_id} run={run.id}: "
            f"updated={run.updated_count} skipped={run.skipped_count} errors={run.error_count}"
        )
        return {"run_id": run.id, "status": run.status, "updated": run.updated_count}
    except Exception as exc:
        logger.error(f"[Celery] user={user_id} repricer failed: {exc}", exc_info=True)
        raise self.retry(exc=exc)
    finally:
        db.close()
