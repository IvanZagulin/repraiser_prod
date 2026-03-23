"""
Periodic cleanup tasks — prevent unbounded table growth.

Registered as Celery beat tasks in scheduler or called manually via API.
"""

import logging
from datetime import datetime, timedelta, timezone

from app.celery_app import celery_app
from app.database import SessionLocal

logger = logging.getLogger(__name__)

# Хранить снимки цен не дольше 90 дней (48k строк/день × 90 = ~4.3M строк макс.)
SNAPSHOT_RETENTION_DAYS = 90

# Хранить записи из activity log не дольше 180 дней
ACTIVITY_RETENTION_DAYS = 180


@celery_app.task(
    name="cleanup.old_snapshots",
    time_limit=600,
    soft_time_limit=540,
)
def cleanup_old_snapshots(retention_days: int = SNAPSHOT_RETENTION_DAYS) -> dict:
    """Удаляет снимки цен старше retention_days.

    Удаление батчами по 5000 строк чтобы не блокировать таблицу надолго.
    """
    from app.models.price_snapshot import PriceSnapshot

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=retention_days)
    db = SessionLocal()
    total_deleted = 0
    batch_size = 5000

    try:
        while True:
            # Удаляем батчами — PostgreSQL не блокирует всю таблицу
            ids = (
                db.query(PriceSnapshot.id)
                .filter(PriceSnapshot.snapshot_at < cutoff)
                .limit(batch_size)
                .all()
            )
            if not ids:
                break
            id_list = [row[0] for row in ids]
            deleted = (
                db.query(PriceSnapshot)
                .filter(PriceSnapshot.id.in_(id_list))
                .delete(synchronize_session=False)
            )
            db.commit()
            total_deleted += deleted
            logger.info(f"[cleanup] Deleted {deleted} old snapshots (total: {total_deleted})")

        logger.info(
            f"[cleanup] Snapshot cleanup done: {total_deleted} rows deleted "
            f"(cutoff: {cutoff.isoformat()})"
        )
        return {"deleted": total_deleted, "cutoff": cutoff.isoformat()}
    except Exception as e:
        db.rollback()
        logger.error(f"[cleanup] Snapshot cleanup failed: {e}", exc_info=True)
        raise
    finally:
        db.close()


@celery_app.task(
    name="cleanup.old_activity_logs",
    time_limit=300,
    soft_time_limit=240,
)
def cleanup_old_activity_logs(retention_days: int = ACTIVITY_RETENTION_DAYS) -> dict:
    """Удаляет записи activity log старше retention_days."""
    from sqlalchemy import text

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=retention_days)
    db = SessionLocal()
    total_deleted = 0

    try:
        # activity_log может не существовать (не у всех версий БД)
        result = db.execute(
            text("DELETE FROM activity_log WHERE created_at < :cutoff"),
            {"cutoff": cutoff},
        )
        total_deleted = result.rowcount
        db.commit()
        logger.info(f"[cleanup] Activity log cleanup: {total_deleted} rows deleted")
        return {"deleted": total_deleted, "cutoff": cutoff.isoformat()}
    except Exception as e:
        db.rollback()
        logger.warning(f"[cleanup] Activity log cleanup skipped: {e}")
        return {"deleted": 0, "error": str(e)}
    finally:
        db.close()
