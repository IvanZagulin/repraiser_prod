from celery import Celery
from app.config import settings

celery_app = Celery(
    "repricer",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.repricer_task", "app.tasks.catalog_sync"],
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
)
