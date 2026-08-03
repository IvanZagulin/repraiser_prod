import asyncio
import logging
import signal
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import redis as redis_lib

from app.celery_app import celery_app
from app.database import SessionLocal
from app.redis_client import (
    acquire_product_locks,
    acquire_run_lock,
    release_product_locks,
    release_run_lock,
)

logger = logging.getLogger(__name__)

# Ошибки соединения/сети — допускают retry; логические ошибки — нет
_NETWORK_ERRORS = (ConnectionError, TimeoutError, OSError, redis_lib.ConnectionError, redis_lib.TimeoutError)
_MANUAL_LOCK_RETRY_DELAY_SEC = 60
_MANUAL_LOCK_MAX_RETRIES = 90
_PRIORITY_LOCK_RETRY_DELAY_SEC = 2
_PRIORITY_LOCK_MAX_RETRIES = 60
_PRIORITY_PRODUCT_LOCK_TTL_SEC = 900


# ─── Graceful shutdown ────────────────────────────────────────────────────────
#
# Когда celery-воркер рестартуется (`docker-compose up --build -d celery_worker`),
# контейнер получает SIGTERM. Без обработки задача обрывается на полуслове:
#  - запись RepricerRun остаётся со status='running' (зомби)
#  - Redis-lock держится свои 65 минут (никто не может перезапустить прогон)
#  - acks_late=True + reject_on_worker_lost=True означают, что задача не вернётся
#    в очередь — просто потеряется
#
# Решение: на время выполнения задачи ставим свой SIGTERM-обработчик, который
# помечает текущий run как failed, отпускает lock и инициирует чистый выход
# (SystemExit). После этого Python разворачивает стек, finally-блоки отрабатывают
# нормально. Прежний обработчик восстанавливается на выходе из контекста.
#
# stop_grace_period для celery_worker в docker-compose.yml поднят до 180s,
# чтобы у Python было время на эту чистку до жёсткого SIGKILL.

@dataclass
class _RunState:
    """Состояние текущего in-flight прогона для сигнального обработчика."""
    user_id: Optional[int] = None
    run_id: Optional[int] = None
    owns_user_lock: bool = True


_run_state = _RunState()


def _abort_current_run(reason: str) -> None:
    """Best-effort финализация in-flight прогона. Безопасно вызывать из сигнал-хендлера.

    Открывает *свежую* DB-сессию (не трогаем ту, которую держит таск) и
    помечает самый поздний running-прогон пользователя как failed. Затем
    отпускает Redis-lock. Все ошибки гасим — это уже path к завершению.
    """
    uid = _run_state.user_id
    if uid is None:
        return

    try:
        db = SessionLocal()
        try:
            from app.models.repricer_run import RepricerRun
            query = db.query(RepricerRun).filter(
                RepricerRun.user_id == uid,
                RepricerRun.status == "running",
            )
            if _run_state.run_id is not None:
                query = query.filter(RepricerRun.id == _run_state.run_id)
            run = query.order_by(RepricerRun.started_at.desc()).first()
            if run is not None:
                run.status = "failed"
                run.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                run.error_message = reason
                db.commit()
        finally:
            try:
                db.close()
            except Exception:
                pass
    except Exception as e:
        logger.error(f"[shutdown] mark run failed (user={uid}): {e}")

    if _run_state.owns_user_lock:
        try:
            release_run_lock(uid)
        except Exception as e:
            logger.error(f"[shutdown] release_run_lock (user={uid}): {e}")


@contextmanager
def _graceful_shutdown_guard(
    user_id: int,
    run_id: Optional[int] = None,
    owns_user_lock: bool = True,
):
    """Контекст: ставит SIGTERM/SIGINT хендлеры, при выходе восстанавливает прежние.

    SIGTERM приходит при graceful-рестарте контейнера; SIGINT — при Ctrl+C
    в локальной разработке.
    """
    _run_state.user_id = user_id
    _run_state.run_id = run_id
    _run_state.owns_user_lock = owns_user_lock
    prev_term = signal.getsignal(signal.SIGTERM)
    prev_int = signal.getsignal(signal.SIGINT)

    def _on_signal(signum, _frame):
        sig_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
        logger.warning(
            f"[Celery] {sig_name} received during run (user={user_id}); "
            "помечаем прогон failed и отпускаем lock"
        )
        _abort_current_run(
            f"Прогон прерван сигналом {sig_name} "
            "(рестарт celery воркера, graceful shutdown)"
        )
        # SystemExit раскручивает стек штатно — finally-блоки задачи отработают
        raise SystemExit(143 if signum == signal.SIGTERM else 130)

    handlers_installed = False
    try:
        signal.signal(signal.SIGTERM, _on_signal)
        signal.signal(signal.SIGINT, _on_signal)
        handlers_installed = True
    except (ValueError, OSError):
        # signal.signal работает только в главном потоке главного интерпретатора.
        # В celery prefork каждый child = главный поток — это норма. В тестах
        # под worker'ами pytest-xdist может выкинуть ValueError; молча skip.
        pass

    try:
        yield
    finally:
        _run_state.user_id = None
        _run_state.run_id = None
        _run_state.owns_user_lock = True
        if handlers_installed:
            try:
                signal.signal(signal.SIGTERM, prev_term)
                signal.signal(signal.SIGINT, prev_int)
            except Exception:
                pass


# ─── Celery task ──────────────────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="repricer.run_for_user",
    max_retries=2,
    default_retry_delay=60,
    time_limit=10800,  # 3 часа — для 30k+ товаров
)
def run_repricer_for_user(
    self,
    user_id: int,
    product_ids: list,
    run_type: str = "scheduled",
    existing_run_id: Optional[int] = None,
    dry_run: bool = False,
    priority: bool = False,
):
    """Запускает репрайсер для одного пользователя в Celery-воркере."""
    # Защита от двойного запуска для одного пользователя
    priority_owner = None
    priority_product_ids: set[int] = set()
    lock_acquired = False

    if priority:
        # Explicit small/manual runs bypass the coarse user lock, but reserve
        # their products so a concurrent full run cannot write the same cards.
        priority_product_ids = {int(product_id) for product_id in product_ids}
        priority_owner = f"priority:{self.request.id or uuid.uuid4().hex}"
        acquired = acquire_product_locks(
            user_id,
            priority_product_ids,
            priority_owner,
            ttl=_PRIORITY_PRODUCT_LOCK_TTL_SEC,
        )
        if acquired != priority_product_ids:
            if acquired:
                release_product_locks(user_id, acquired, priority_owner)
            if existing_run_id:
                db = SessionLocal()
                try:
                    from app.models.repricer_run import RepricerRun
                    run = db.query(RepricerRun).filter(RepricerRun.id == existing_run_id).first()
                    if run and self.request.retries >= _PRIORITY_LOCK_MAX_RETRIES:
                        run.status = "failed"
                        run.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                        run.error_message = "Timed out waiting for selected products to become available"
                        db.commit()
                        return {"status": "skipped", "reason": "priority product lock timeout"}
                    if run and run.status == "queued":
                        run.error_message = "Waiting for selected products to become available"
                        db.commit()
                finally:
                    db.close()
            logger.info(
                "[Celery] user=%s run=%s: priority products busy; retry %s/%s",
                user_id,
                existing_run_id,
                self.request.retries + 1,
                _PRIORITY_LOCK_MAX_RETRIES,
            )
            raise self.retry(
                countdown=_PRIORITY_LOCK_RETRY_DELAY_SEC,
                max_retries=_PRIORITY_LOCK_MAX_RETRIES,
            )
    else:
        lock_acquired = acquire_run_lock(user_id)

    if not priority and not lock_acquired and existing_run_id:
        db = SessionLocal()
        try:
            from app.models.repricer_run import RepricerRun
            running_count = (
                db.query(RepricerRun)
                .filter(
                    RepricerRun.user_id == user_id,
                    RepricerRun.status == "running",
                )
                .count()
            )
            if running_count == 0:
                logger.warning(
                    "[Celery] user=%s: run lock exists but DB has no running runs; "
                    "releasing stale lock and retrying once",
                    user_id,
                )
                release_run_lock(user_id)
                lock_acquired = acquire_run_lock(user_id)
            if not lock_acquired:
                run = db.query(RepricerRun).filter(RepricerRun.id == existing_run_id).first()
                if run and run.status == "queued" and run_type == "manual":
                    if self.request.retries >= _MANUAL_LOCK_MAX_RETRIES:
                        run.status = "failed"
                        run.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                        run.error_message = "Timed out waiting for active repricer run to finish"
                        db.commit()
                        return {"status": "skipped", "reason": "active run timeout"}
                    run.error_message = "Waiting for active repricer run to finish"
                    db.commit()
                    logger.info(
                        "[Celery] user=%s run=%s: active repricer lock, keeping manual run queued "
                        "(retry %s/%s)",
                        user_id,
                        existing_run_id,
                        self.request.retries + 1,
                        _MANUAL_LOCK_MAX_RETRIES,
                    )
                    raise self.retry(
                        countdown=_MANUAL_LOCK_RETRY_DELAY_SEC,
                        max_retries=_MANUAL_LOCK_MAX_RETRIES,
                    )
                if run and run.status == "queued":
                    run.status = "failed"
                    run.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                    run.error_message = "Repricer run already active; wait for it to finish"
                    db.commit()
        finally:
            db.close()

    if not priority and not lock_acquired:
        logger.warning(f"[Celery] user={user_id}: прогон уже идёт, пропускаем дублирующийся запуск")
        return {"status": "skipped", "reason": "already running"}

    with _graceful_shutdown_guard(
        user_id,
        existing_run_id,
        owns_user_lock=not priority,
    ):
        db = None
        try:
            db = SessionLocal()
            # Проверяем что пользователь существует и активен — защита от
            # передачи произвольного user_id через Celery/Redis
            from app.models.user import User
            user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
            if not user:
                logger.warning(f"[Celery] user_id={user_id} не найден или заблокирован — задача пропущена")
                return {"status": "skipped", "reason": "user not found or inactive"}

            from app.services.repricer_engine import RepricerEngine
            engine = RepricerEngine(
                db,
                user_id=user_id,
                product_lock_owner=priority_owner,
                hold_product_locks=priority,
                force_catalog_refresh=priority,
            )
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
            try:
                if db is not None:
                    db.close()
            except Exception as e:
                logger.error(f"[Celery] user={user_id}: db.close() failed: {e}")
            if priority and priority_owner:
                release_product_locks(user_id, priority_product_ids, priority_owner)
            elif lock_acquired:
                try:
                    release_run_lock(user_id)
                except Exception as e:
                    logger.error(f"[Celery] user={user_id}: release_run_lock failed: {e}")
