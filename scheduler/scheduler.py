import asyncio
import logging
import sys
from datetime import datetime, timezone
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
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_timeout=10,
    connect_args={"connect_timeout": 10},
)
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
    if not is_admin and not plan_id and (not trial_ends_at or trial_ends_at < datetime.now(timezone.utc).replace(tzinfo=None)):
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
            WHERE user_id = :uid AND run_type = 'scheduled'
            ORDER BY started_at DESC LIMIT 1
        """),
        {"uid": user_id}
    ).fetchone()

    if last_run and last_run[0]:
        elapsed = (datetime.now(timezone.utc).replace(tzinfo=None) - last_run[0]).total_seconds() / 60
        if elapsed < interval_minutes - 2:  # 2-минутный буфер
            logger.info(f"  user={user_id}: ждём интервал ({elapsed:.0f}/{interval_minutes} мин)")
            return False
        else:
            logger.info(f"  user={user_id}: интервал прошёл ({elapsed:.0f}>={interval_minutes} мин), запускаем")

    return True


def _repricer_job_sync():
    """Синхронная часть repricer_job — выполняется в отдельном потоке,
    чтобы блокирующие DB-вызовы не замораживали asyncio event loop."""
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
                if not _user_should_run(db, user_id):
                    continue

                if _is_run_locked(user_id):
                    logger.info(f"  user={user_id}: прогон уже идёт (Redis-лок), пропускаем")
                    continue

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
        logger.error(f"Критическая ошибка в _repricer_job_sync: {e}", exc_info=True)
        raise

    finally:
        db.close()

    logger.info("=" * 70)


async def repricer_job():
    """
    Тик планировщика (каждый час в :00): проверяет каждого пользователя
    и запускает репрайсер если подошло время по его расписанию.

    Синхронные DB-запросы выполняются в отдельном потоке (asyncio.to_thread),
    чтобы не замораживать event loop. asyncio.wait_for гарантирует прерывание
    за 25 секунд — защита от зависания при недоступной БД.
    """
    try:
        await asyncio.wait_for(
            asyncio.to_thread(_repricer_job_sync),
            timeout=25.0,
        )
    except asyncio.TimeoutError:
        logger.error(
            "repricer_job: превышен timeout (25s) — вероятно БД недоступна. "            "Задача пропущена, следующий запуск через час."
        )
        try:
            await telegram_notifier.notify_scheduler_error(
                "repricer_job завис и был прерван по timeout (25s). "                "Проверьте доступность PostgreSQL."
            )
        except Exception:
            pass
    except Exception as e:
        logger.error(f"repricer_job: необработанная ошибка: {e}", exc_info=True)
        try:
            await telegram_notifier.notify_scheduler_error(str(e))
        except Exception:
            pass


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
    'последний прогон'. TTL = 185 мин (чуть больше Celery time_limit=10800s).
    Меньший TTL помечает failed ещё живые медленные прогоны и снимает их
    run-лок (инцидент 2026-06-10).

    Дополнительно: снимает осиротевшие Redis run-локи (repricer:run_lock:{user_id}) —
    если у пользователя нет ни одной записи status='running', а лок взят не
    меньше минуты назад (защита от гонки со свежим прогоном, который ещё не
    успел создать свою запись в БД). Такой лок остаётся висеть на полный TTL
    (~3ч), если прогон был помечен failed/completed вручную в обход
    release_run_lock — планировщик при этом молча пропускает все тики
    пользователя (инцидент 2026-06-11, user=12 простаивал ~50 мин).
    """
    STALE_MINUTES = 185
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
                  AND started_at < NOW() - MAKE_INTERVAL(mins => :minutes)
                RETURNING id, user_id
            """),
            {"minutes": STALE_MINUTES}
        )
        rows = result.fetchall()
        db.commit()
        if rows:
            for run_id, user_id in rows:
                logger.warning(f"  Зависший прогон id={run_id} user={user_id} → failed")
                # Освобождаем Redis-лок если он ещё жив
                try:
                    _get_redis().delete(f"repricer:run_lock:{user_id}")
                except Exception as e:
                    logger.warning(f"Failed to delete Redis lock for user {user_id}: {e}")
            logger.info(f"Очищено зависших прогонов: {len(rows)}")
        else:
            logger.info("Зависших прогонов нет")

        # --- Осиротевшие Redis run-локи ---
        # Лок мог остаться висеть, если прогон был помечен failed/completed
        # напрямую в БД (в обход release_run_lock в repricer_task.py): блок
        # выше его не находит, т.к. status уже не 'running'. Без этой чистки
        # планировщик молча пропускает все тики пользователя до истечения
        # TTL лока (~3ч).
        try:
            redis_client = _get_redis()
            running_users = {
                uid for (uid,) in db.execute(
                    text("SELECT DISTINCT user_id FROM repricer_runs WHERE status = 'running'")
                ).fetchall()
            }
            for key in redis_client.scan_iter(match="repricer:run_lock:*"):
                try:
                    uid = int(key.rsplit(":", 1)[-1])
                except ValueError:
                    continue
                if uid in running_users:
                    continue
                ttl = redis_client.ttl(key)
                # ttl <= 0: уже истёк/без TTL — нечего снимать.
                # ttl > 10800: лок взят меньше минуты назад (TTL=10860s) —
                # запись в repricer_runs ещё может не успеть появиться, не трогаем.
                if ttl <= 0 or ttl > 10800:
                    continue
                redis_client.delete(key)
                logger.warning(
                    f"  Осиротевший Redis-лок user={uid} снят "
                    f"(running-записи нет, оставался TTL={ttl}s)"
                )
        except Exception as e:
            logger.warning(f"Ошибка при очистке осиротевших Redis-локов: {e}")
    except Exception as e:
        logger.error(f"Ошибка при очистке зависших прогонов: {e}", exc_info=True)
    finally:
        db.close()


async def cleanup_old_price_changes():
    """Удаляет price_changes старше 60 дней — таблица растёт ~500k строк/день
    и без чистки разнесёт диск (видели 7GB / 17M строк за месяц).

    После правки движка OUT_OF_STOCK / NO_CHANGE больше не пишутся, поэтому
    новые данные растут в 20× медленнее. Но старые залежи надо вычистить.
    """
    RETENTION_DAYS = 60
    BATCH_SIZE = 50_000
    logger.info(f"Очистка price_changes старше {RETENTION_DAYS} дней...")
    db = SessionLocal()
    total_deleted = 0
    try:
        while True:
            result = db.execute(
                text(f"""
                    DELETE FROM price_changes
                    WHERE id IN (
                        SELECT id FROM price_changes
                        WHERE changed_at < NOW() - INTERVAL '{RETENTION_DAYS} days'
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
            logger.info(f"Удалено старых price_changes: {total_deleted}")
            try:
                raw_conn = engine.raw_connection()
                raw_conn.set_isolation_level(0)  # AUTOCOMMIT для VACUUM
                raw_conn.cursor().execute("VACUUM ANALYZE price_changes")
                raw_conn.close()
                logger.info("VACUUM ANALYZE price_changes выполнен")
            except Exception as e:
                logger.warning(f"VACUUM ANALYZE price_changes не удался: {e}")
        else:
            logger.info("Старых price_changes нет")
    except Exception as e:
        logger.error(f"Ошибка очистки price_changes: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()


# cleanup_old_snapshots убрана (дубль bash cron cleanup_prices.sh)


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



def setup_scheduler() -> AsyncIOScheduler:
    """Настройка и запуск планировщика"""
    scheduler = AsyncIOScheduler(timezone="UTC")

    # Тик каждые 5 минут (xx:00, xx:05, ..., xx:55).
    # _user_should_run() проверяет per-user interval_minutes — пропускает
    # пользователя если с последнего прогона прошло меньше его интервала.
    scheduler.add_job(
        repricer_job,
        trigger=CronTrigger(minute="*/5", timezone="UTC"),
        id="repricer_job",
        name="Repricer Tick (every 5 min)",
        replace_existing=True,
        max_instances=1
    )
    logger.info("Задача 'repricer_job' зарегистрирована (каждые 5 минут)")

    # Proxy health-check отключён — прокси не используются для каталога
    # (вызывали массовый бан WB). При необходимости раскомментировать.
    # scheduler.add_job(health_check_proxies, trigger=IntervalTrigger(minutes=30),
    #     id="proxy_health_check", name="Proxy Health Check", replace_existing=True)
    logger.info("Proxy health-check ОТКЛЮЧЁН (прокси не используются)")

    scheduler.add_job(
        catalog_sync_job,
        trigger=CronTrigger(hour=2, minute=0),
        id="catalog_sync_job",
        name="WB Catalog Sync (daily 02:00 UTC)",
        replace_existing=True,
        max_instances=1,
    )
    logger.info("Задача 'catalog_sync_job' зарегистрирована (ежедневно в 02:00 UTC)")


    # Competitor monitor ОТКЛЮЧЁН — catalog.sync_account забивает все воркеры
    # и блокирует репрайсер. Когда нужен — раскомментировать.
    # scheduler.add_job(competitor_monitor_job, trigger=IntervalTrigger(minutes=30),
    #     id="competitor_monitor_job", replace_existing=True, max_instances=1)
    logger.info("competitor_monitor_job ОТКЛЮЧЁН (конфликт с репрайсером)")

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
        cleanup_old_price_changes,
        trigger=IntervalTrigger(hours=24),
        id="cleanup_old_price_changes",
        name="Cleanup Old Price Changes (daily, 60d retention)",
        replace_existing=True,
        max_instances=1,
    )
    logger.info("Задача 'cleanup_old_price_changes' зарегистрирована (раз в сутки)")

    # cleanup_old_snapshots убрана — bash cron (cleanup_prices.sh, 02:00 UTC)
    # чистит snapshots за 30 дней + дедуплицирует, это лучше и полнее.

    return scheduler


async def startup():
    """Startup процедуры"""
    logger.info("=" * 70)
    logger.info("WB Repricer Scheduler запускается...")
    logger.info("=" * 70)

    logger.info("Proxy pool не инициализируется: прокси отключены для каталога")

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
