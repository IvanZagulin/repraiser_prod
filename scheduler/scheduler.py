import asyncio
import logging
import sys
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import os

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

# Добавляем путь к приложению
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

import redis as redis_lib

from app.config import settings
from app.services.telegram import telegram_notifier
from app.services.proxy_pool import proxy_pool

# Redis-клиент для проверки run-локов перед постановкой задач в очередь
_redis_client: redis_lib.Redis = None


def _get_redis() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_lib.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


def _is_run_locked(user_id: int) -> bool:
    """Проверяет, идёт ли уже прогон для пользователя (без захвата лока)."""
    try:
        return bool(_get_redis().exists(f"repricer:run_lock:{user_id}"))
    except Exception as e:
        logger.warning(f"Redis exists check failed for user={user_id}: {e}")
        return False  # при недоступности Redis — разрешаем (воркер сам защитится)

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

# Фиксированный тик — каждые 5 минут проверяем всех пользователей
TICK_MINUTES = 5


def _user_should_run(db, user_id: int) -> bool:
    """
    Проверяет, нужно ли запускать репрайсер для пользователя:
    1. План/триал активен
    2. Текущее время попадает в рабочие часы
    3. Текущий день не в паузе
    4. Прошло достаточно времени с последнего прогона
    """
    # Проверка плана/триала
    user_row = db.execute(
        text("""
            SELECT plan_id, trial_ends_at, is_admin
            FROM users WHERE id = :uid AND is_active = TRUE
        """),
        {"uid": user_id}
    ).fetchone()

    if not user_row:
        return False

    plan_id, trial_ends_at, is_admin = user_row
    if not is_admin and not plan_id and (not trial_ends_at or trial_ends_at < datetime.utcnow()):
        logger.info(f"  user={user_id}: план/триал истёк, пропускаем")
        return False

    # Читаем пользовательское расписание
    sched_row = db.execute(
        text("""
            SELECT interval_minutes, work_start_hour, work_end_hour,
                   timezone, paused_days, is_active
            FROM repricer_schedules WHERE user_id = :uid
        """),
        {"uid": user_id}
    ).fetchone()

    if sched_row:
        interval_minutes, work_start, work_end, tz_name, paused_days, is_sched_active = sched_row

        if not is_sched_active:
            logger.info(f"  user={user_id}: расписание отключено, пропускаем")
            return False

        # Текущее время в таймзоне пользователя
        try:
            tz = ZoneInfo(tz_name or "Europe/Moscow")
        except Exception:
            tz = ZoneInfo("Europe/Moscow")

        now_user = datetime.now(tz)
        current_hour = now_user.hour
        current_dow = now_user.weekday()  # 0=Mon, 6=Sun

        # Проверка рабочих часов
        if work_end == 24:
            if current_hour < work_start:
                logger.info(f"  user={user_id}: вне рабочих часов ({current_hour}h < {work_start}h)")
                return False
        else:
            if current_hour < work_start or current_hour >= work_end:
                logger.info(f"  user={user_id}: вне рабочих часов ({current_hour}h)")
                return False

        # Проверка дней паузы (формат: "0,6" = Пн, Вс; используем Python weekday 0=Mon)
        if paused_days:
            paused = set()
            for d in paused_days.split(","):
                d = d.strip()
                if d.isdigit():
                    paused.add(int(d))
            if current_dow in paused:
                logger.info(f"  user={user_id}: день паузы ({current_dow})")
                return False
    else:
        interval_minutes = 60  # default

    # Проверка: прошло ли достаточно времени с последнего прогона
    last_run = db.execute(
        text("""
            SELECT started_at FROM repricer_runs
            WHERE user_id = :uid
            ORDER BY started_at DESC LIMIT 1
        """),
        {"uid": user_id}
    ).fetchone()

    if last_run and last_run[0]:
        elapsed = (datetime.utcnow() - last_run[0]).total_seconds() / 60
        if elapsed < interval_minutes - 2:  # 2-минутный буфер
            logger.info(f"  user={user_id}: ждём интервал ({elapsed:.0f}/{interval_minutes} мин)")
            return False
        else:
            logger.info(f"  user={user_id}: интервал прошёл ({elapsed:.0f}>={interval_minutes} мин), запускаем")

    return True


async def repricer_job():
    """
    Тик планировщика (каждые 5 мин): проверяет каждого пользователя
    и запускает репрайсер если подошло время по его расписанию.
    """
    logger.info("=" * 70)
    logger.info("Планировщик: проверка пользователей")
    logger.info("=" * 70)

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
                # Проверяем расписание, план, рабочие часы
                if not _user_should_run(db, user_id):
                    continue

                # Не ставим задачу в очередь если прогон уже идёт — защита от мусорных дублей
                if _is_run_locked(user_id):
                    logger.info(f"  user={user_id}: прогон уже идёт (Redis-лок), пропускаем")
                    continue

                # Проверяем наличие товаров С активными конфигами
                product_rows = db.execute(
                    text("""
                        SELECT p.id FROM products p
                        JOIN accounts a ON p.account_id = a.id
                        JOIN product_config pc ON pc.product_id = p.id
                        WHERE a.user_id = :uid
                          AND a.is_active = TRUE
                          AND pc.is_active = TRUE
                    """),
                    {"uid": user_id}
                ).fetchall()
                product_ids = [r[0] for r in product_rows]

                if not product_ids:
                    logger.info(f"  user={user_id}: нет активных товаров/аккаунтов, пропускаем")
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
            logger.critical("ВСЕ ПРОКСИ МЕРТВЫ!")
            await telegram_notifier.notify_all_proxies_dead()

    except Exception as e:
        logger.error(f"Ошибка в health_check_proxies: {e}", exc_info=True)


async def catalog_sync_job():
    """Ежедневно в 02:00 UTC (05:00 МСК): синхронизирует каталог карточек WB для всех аккаунтов."""
    logger.info("=" * 70)
    logger.info("Планировщик: еженедельная синхронизация каталога WB")
    try:
        from app.tasks.catalog_sync import sync_all_wb_catalogs_task
        sync_all_wb_catalogs_task.delay()
        logger.info("Задача sync_all_wb_catalogs_task поставлена в очередь")
    except Exception as e:
        logger.error(f"Ошибка при запуске catalog_sync_job: {e}", exc_info=True)
    logger.info("=" * 70)


async def cleanup_stale_runs():
    """Помечает зависшие прогоны (status='running' дольше TTL) как failed.

    Если Celery-воркер упал или был убит по time_limit, запись RepricerRun
    остаётся в статусе 'running' навсегда. Это портит UI и блокирует логику
    'последний прогон'. TTL = 70 мин (чуть больше Celery time_limit=3900s).
    """
    STALE_MINUTES = 70
    logger.info("Очистка зависших прогонов...")
    db = SessionLocal()
    try:
        result = db.execute(
            text("""
                UPDATE repricer_runs
                SET status = 'failed',
                    error_message = 'Прогон завис (превышен TTL) — помечен планировщиком',
                    completed_at = NOW()
                WHERE status = 'running'
                  AND started_at < NOW() - INTERVAL ':minutes minutes'
                RETURNING id, user_id
            """.replace(":minutes", str(STALE_MINUTES)))
        )
        rows = result.fetchall()
        db.commit()
        if rows:
            for run_id, user_id in rows:
                logger.warning(f"  Зависший прогон id={run_id} user={user_id} → failed")
                # Освобождаем Redis-лок если он ещё жив
                try:
                    _get_redis().delete(f"repricer:run_lock:{user_id}")
                except Exception:
                    pass
            logger.info(f"Очищено зависших прогонов: {len(rows)}")
        else:
            logger.info("Зависших прогонов нет")
    except Exception as e:
        logger.error(f"Ошибка при очистке зависших прогонов: {e}", exc_info=True)
    finally:
        db.close()


async def cleanup_old_snapshots():
    """Удаляет price_snapshots старше 180 дней.

    При 1000 товаров × 24 прогона/день × 180 дней ≈ 4.3M строк —
    без очистки таблица растёт бесконечно и замедляет запросы.
    Запуск раз в сутки, батчами по 50K строк чтобы не блокировать БД.
    """
    RETENTION_DAYS = 180
    BATCH_SIZE = 50_000
    logger.info(f"Очистка снимков цен старше {RETENTION_DAYS} дней...")
    db = SessionLocal()
    total_deleted = 0
    try:
        while True:
            result = db.execute(
                text(f"""
                    DELETE FROM price_snapshots
                    WHERE id IN (
                        SELECT id FROM price_snapshots
                        WHERE snapshot_at < NOW() - INTERVAL '{RETENTION_DAYS} days'
                        LIMIT {BATCH_SIZE}
                    )
                """)
            )
            deleted = result.rowcount
            db.commit()
            total_deleted += deleted
            if deleted < BATCH_SIZE:
                break
        if total_deleted > 0:
            logger.info(f"Удалено старых снимков: {total_deleted}")
        else:
            logger.info("Старых снимков нет")
    except Exception as e:
        logger.error(f"Ошибка очистки снимков: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()


async def competitor_monitor_job():
    """Каждые 30 минут: мониторинг цен конкурентов для всех пользователей."""
    logger.info("Планировщик: мониторинг цен конкурентов")
    try:
        import sys
        sys.path.insert(0, "/app")
        from app.database import SessionLocal
        from app.models.user import User
        from app.tasks.competitor_monitor import monitor_competitors_task
        db = SessionLocal()
        try:
            user_ids = [uid for (uid,) in db.query(User.id).filter(User.is_active == True).all()]
        finally:
            db.close()
        for uid in user_ids:
            monitor_competitors_task.delay(uid)
        logger.info(f"Задача monitor_competitors_task поставлена в очередь для {len(user_ids)} пользователей")
    except Exception as e:
        logger.error(f"Ошибка при запуске competitor_monitor_job: {e}", exc_info=True)



async def products_sync_job():
    """Ежедневно в полночь: создаёт товары из wb_catalog для всех активных аккаунтов."""
    logger.info("=" * 70)
    logger.info("Планировщик: ежедневная синхронизация товаров из каталога")
    try:
        from app.tasks.products_sync import sync_all_products_task
        sync_all_products_task.delay()
        logger.info("Задача sync_all_products_task поставлена в очередь")
    except Exception as e:
        logger.error(f"Ошибка при запуске products_sync_job: {e}", exc_info=True)
    logger.info("=" * 70)


def setup_scheduler() -> AsyncIOScheduler:
    """Настройка и запуск планировщика"""
    scheduler = AsyncIOScheduler(timezone="UTC")

    # Единый тик каждые 5 минут — внутри проверяется per-user расписание
    scheduler.add_job(
        repricer_job,
        trigger=IntervalTrigger(minutes=TICK_MINUTES),
        id="repricer_job",
        name="Repricer Tick",
        replace_existing=True,
        max_instances=1
    )
    logger.info(f"Задача 'repricer_job' зарегистрирована (тик каждые {TICK_MINUTES} мин.)")

    scheduler.add_job(
        health_check_proxies,
        trigger=IntervalTrigger(minutes=30),
        id="proxy_health_check",
        name="Proxy Health Check",
        replace_existing=True
    )
    logger.info("Задача 'proxy_health_check' зарегистрирована (каждые 30 мин.)")

    scheduler.add_job(
        catalog_sync_job,
        trigger=CronTrigger(hour=2, minute=0),
        id="catalog_sync_job",
        name="WB Catalog Sync (daily 02:00 UTC)",
        replace_existing=True,
        max_instances=1,
    )
    logger.info("Задача 'catalog_sync_job' зарегистрирована (ежедневно в 02:00 UTC)")

    scheduler.add_job(
        products_sync_job,
        trigger=CronTrigger(hour=3, minute=0),
        id="products_sync_job",
        name="Products Sync from Catalog (daily 03:00 UTC)",
        replace_existing=True,
        max_instances=1,
    )
    logger.info("Задача 'products_sync_job' зарегистрирована (ежедневно в 03:00 UTC = 06:00 МСК)")

    scheduler.add_job(
        competitor_monitor_job,
        trigger=IntervalTrigger(minutes=30),
        id="competitor_monitor_job",
        name="Competitor Price Monitor (30 min)",
        replace_existing=True,
        max_instances=1,
    )
    logger.info("Задача 'competitor_monitor_job' зарегистрирована (каждые 30 мин.)")

    scheduler.add_job(
        cleanup_stale_runs,
        trigger=IntervalTrigger(minutes=15),
        id="cleanup_stale_runs",
        name="Cleanup Stale Runs (15 min)",
        replace_existing=True,
        max_instances=1,
    )
    logger.info("Задача 'cleanup_stale_runs' зарегистрирована (каждые 15 мин.)")

    scheduler.add_job(
        cleanup_old_snapshots,
        trigger=IntervalTrigger(hours=24),
        id="cleanup_old_snapshots",
        name="Cleanup Old Snapshots (daily)",
        replace_existing=True,
        max_instances=1,
    )
    logger.info("Задача 'cleanup_old_snapshots' зарегистрирована (раз в сутки)")

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
        logger.info("Подключение к БД успешно")
    except Exception as e:
        logger.error(f"Ошибка подключения к БД: {e}")
        raise

    logger.info("=" * 70)
    logger.info("Scheduler готов к работе")
    logger.info("=" * 70)


async def main():
    """Главная функция"""
    global _scheduler

    try:
        await startup()

        _scheduler = setup_scheduler()
        _scheduler.start()
        logger.info("Планировщик запущен. Per-user расписания активны.")

        while True:
            await asyncio.sleep(60)

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
