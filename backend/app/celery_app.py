import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s", force=True)

from celery import Celery
from celery.signals import worker_process_init
from app.config import settings

celery_app = Celery(
    "repricer",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.repricer_task", "app.tasks.catalog_sync", "app.tasks.competitor_monitor", "app.tasks.cleanup", "app.tasks.products_sync"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    result_expires=3600,
    # Перезапускать воркер каждые 200 задач — защита от утечек памяти
    worker_max_tasks_per_child=200,
    # Ack задачу ПОСЛЕ выполнения — если воркер упал, задача вернётся в очередь
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Автореконнект к Redis-брокеру при разрыве
    broker_connection_retry_on_startup=True,
    broker_connection_max_retries=10,
    # Visibility timeout должен превышать самую длинную задачу (time_limit=10800s),
    # иначе Redis-брокер повторно доставляет ещё выполняющуюся задачу другому
    # воркеру (инцидент 2026-06-10: дубль прогона через час после старта).
    broker_transport_options={"visibility_timeout": 10860},
)


@worker_process_init.connect
def dispose_db_connections(**kwargs):
    """
    After forking, dispose the inherited DB engine so that each worker
    process creates its own fresh connections instead of sharing the parent's.
    """
    try:
        from app.database import engine
        engine.dispose()
        logging.getLogger(__name__).info("DB engine disposed after fork")
    except Exception as e:
        logging.getLogger(__name__).warning(f"dispose_db_connections error: {e}")
