import asyncio
import logging
import sys
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import os

# Добавляем путь к приложению
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from app.config import settings
from app.services.telegram import telegram_notifier
from app.services.proxy_pool import proxy_pool

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/app/logs/scheduler.log')
    ]
)

logger = logging.getLogger(__name__)

# Database setup
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Глобальная ссылка на планировщик
_scheduler: AsyncIOScheduler = None
_current_interval_minutes: int = 60


def _read_interval_from_db() -> int:
    """
    Читает минимальный интервал из настроек всех пользователей.
    """
    try:
        db = SessionLocal()
        try:
            row = db.execute(
                text("SELECT value FROM app_settings "
                     "WHERE key = 'repricer_interval_minutes' AND user_id IS NULL")
            ).fetchone()
            if row:
                return max(1, int(row[0]))
            row = db.execute(
                text("SELECT MIN(value::int) FROM app_settings "
                     "WHERE key = 'repricer_interval_minutes'")
            ).fetchone()
            if row and row[0]:
                return max(1, row[0])
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Не удалось прочитать интервал из БД: {e}")
    return 60


async def repricer_job():
    """
    Читает активных пользователей и отправляет задачу репрайсера
    в Celery-воркер для каждого. Воркеры обрабатывают пользователей параллельно.
    """
    global _scheduler, _current_interval_minutes

    logger.info("=" * 70)
    logger.info("Планировщик: отправка задач репрайсера в Celery")
    logger.info("=" * 70)

    new_interval = _read_interval_from_db()
    if new_interval != _current_interval_minutes and _scheduler is not None:
        logger.info(f"Интервал изменён: {_current_interval_minutes} → {new_interval} мин.")
        _current_interval_minutes = new_interval
        _scheduler.reschedule_job('repricer_job', trigger=IntervalTrigger(minutes=new_interval))

    db = SessionLocal()
    try:
        users = db.execute(
            text("SELECT id FROM users WHERE is_active = TRUE")
        ).fetchall()

        logger.info(f"Активных пользователей: {len(users)}")

        from app.tasks.repricer_task import run_repricer_for_user

        dispatched = 0
        for (user_id,) in users:
            try:
                product_rows = db.execute(
                    text("""
                        SELECT p.id FROM products p
                        JOIN accounts a ON p.account_id = a.id
                        WHERE a.user_id = :uid
                    """),
                    {"uid": user_id}
                ).fetchall()
                product_ids = [r[0] for r in product_rows]

                if not product_ids:
                    continue

                task = run_repricer_for_user.delay(user_id, product_ids, "scheduled")
                logger.info(
                    f"  user={user_id}: задача {task.id} поставлена "
                    f"в очередь ({len(product_ids)} товаров)"
                )
                dispatched += 1

            except Exception as e:
                logger.error(f"Ошибка при отправке задачи для user_id={user_id}: {e}", exc_info=True)

        logger.info(f"Отправлено задач: {dispatched}")

    except Exception as e:
        logger.error(f"Критическая ошибка в repricer_job: {e}", exc_info=True)
        await telegram_notifier.notify_scheduler_error(str(e))

    finally:
        db.close()

    logger.info("=" * 70)


async def health_check_proxies():
    """Периодическая проверка прокси — каждые 30 минут."""
    logger.info("Запуск health check прокси...")

    try:
        if not proxy_pool._initialized:
            await proxy_pool.initialize()
        else:
            await proxy_pool.health_check_all()

        stats = proxy_pool.get_stats()
        logger.info(f"Health check завершен: {stats}")

        if stats['alive'] == 0 and stats['total'] > 0:
            logger.critical("⚠️ ВСЕ ПРОКСИ МЕРТВЫ!")
            await telegram_notifier.notify_all_proxies_dead()

    except Exception as e:
        logger.error(f"Ошибка в health_check_proxies: {e}", exc_info=True)


def setup_scheduler(interval_minutes: int) -> AsyncIOScheduler:
    """Настройка и запуск планировщика"""
    scheduler = AsyncIOScheduler(timezone="UTC")

    scheduler.add_job(
        repricer_job,
        trigger=IntervalTrigger(minutes=interval_minutes),
        id="repricer_job",
        name="Repricer Job",
        replace_existing=True,
        max_instances=1
    )
    logger.info(f"✓ Задача 'repricer_job' зарегистрирована (каждые {interval_minutes} мин.)")

    scheduler.add_job(
        health_check_proxies,
        trigger=IntervalTrigger(minutes=30),
        id="proxy_health_check",
        name="Proxy Health Check",
        replace_existing=True
    )
    logger.info("✓ Задача 'proxy_health_check' зарегистрирована (каждые 30 мин.)")

    return scheduler


async def startup():
    """Startup процедуры"""
    logger.info("=" * 70)
    logger.info("WB Repricer Scheduler запускается...")
    logger.info("=" * 70)

    logger.info("Инициализация proxy pool...")
    await proxy_pool.initialize()

    stats = proxy_pool.get_stats()
    logger.info(f"Proxy pool готов: {stats}")

    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        logger.info("✓ Подключение к БД успешно")
    except Exception as e:
        logger.error(f"✗ Ошибка подключения к БД: {e}")
        raise

    logger.info("=" * 70)
    logger.info("Scheduler готов к работе")
    logger.info("=" * 70)


async def main():
    """Главная функция"""
    global _scheduler, _current_interval_minutes

    try:
        await startup()

        _current_interval_minutes = _read_interval_from_db()
        logger.info(f"Интервал репрайсера из БД: {_current_interval_minutes} мин.")

        _scheduler = setup_scheduler(_current_interval_minutes)
        _scheduler.start()
        logger.info("Планировщик запущен. Задачи отправляются в Celery.")

        while True:
            await asyncio.sleep(60)
            new_interval = _read_interval_from_db()
            if new_interval != _current_interval_minutes:
                logger.info(
                    f"Интервал изменён в БД: {_current_interval_minutes} → {new_interval} мин."
                )
                _current_interval_minutes = new_interval
                _scheduler.reschedule_job(
                    'repricer_job',
                    trigger=IntervalTrigger(minutes=new_interval)
                )

    except KeyboardInterrupt:
        logger.info("Получен сигнал завершения (Ctrl+C)")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        await telegram_notifier.notify_scheduler_error(str(e))
        raise
    finally:
        logger.info("Scheduler остановлен")


if __name__ == "__main__":
    asyncio.run(main())
