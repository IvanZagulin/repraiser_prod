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
    new_run_lock_owner,
    refresh_run_lock,
    release_product_locks,
    release_run_lock,
    release_stale_run_lock,
)

logger = logging.getLogger(__name__)

# Ошибки соединения/сети — допускают retry; логические ошибки — нет
_NETWORK_ERRORS = (ConnectionError, TimeoutError, OSError, redis_lib.ConnectionError, redis_lib.TimeoutError)
_MANUAL_LOCK_RETRY_DELAY_SEC = 60
_MANUAL_LOCK_MAX_RETRIES = 90
_PRIORITY_LOCK_RETRY_DELAY_SEC = 2
_PRIORITY_LOCK_MAX_RETRIES = 60
_PRIORITY_PRODUCT_LOCK_TTL_SEC = 900
# Блокировка прогона живёт 10 минут и продлевается каждую минуту, пока воркер
# жив. Раньше она ставилась на 3 часа: после падения воркера (OOM, SIGKILL при
# деплое) переоценка стояла до 3 часов, в том числе после ночной выгрузки 1С.
_RUN_LOCK_HEARTBEAT_TTL_SEC = 600
_RUN_LOCK_HEARTBEAT_SEC = 60
# Лок без записи running в БД снимаем, только если он старше этого: свежий
# прогон ещё может не успеть создать запись.
_STALE_RUN_LOCK_MIN_AGE_SEC = 120


class _RunLockHeartbeat:
    def __init__(self, user_id: int, owner: Optional[str] = None):
        import threading
        self._user_id = user_id
        self._owner = owner
        self._lost_logged = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name=f"run-lock-hb-{user_id}", daemon=True)

    def _loop(self):
        while True:
            ok = refresh_run_lock(self._user_id, _RUN_LOCK_HEARTBEAT_TTL_SEC, owner=self._owner)
            if not ok and self._owner and not self._lost_logged:
                # Лок истёк или его снял/перехватил кто-то другой: второй прогон
                # может стартовать параллельно — это надо видеть в логах.
                self._lost_logged = True
                logger.error(
                    "[Celery] user=%s: run-лок потерян (владелец %s) — heartbeat не продлил его",
                    self._user_id, self._owner,
                )
            if self._stop.wait(_RUN_LOCK_HEARTBEAT_SEC):
                return

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()


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
    lock_owner: Optional[str] = None


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
            release_run_lock(uid, owner=_run_state.lock_owner)
        except Exception as e:
            logger.error(f"[shutdown] release_run_lock (user={uid}): {e}")


@contextmanager
def _graceful_shutdown_guard(
    user_id: int,
    run_id: Optional[int] = None,
    owns_user_lock: bool = True,
    lock_owner: Optional[str] = None,
):
    """Контекст: ставит SIGTERM/SIGINT хендлеры, при выходе восстанавливает прежние.

    SIGTERM приходит при graceful-рестарте контейнера; SIGINT — при Ctrl+C
    в локальной разработке.
    """
    _run_state.user_id = user_id
    _run_state.run_id = run_id
    _run_state.owns_user_lock = owns_user_lock
    _run_state.lock_owner = lock_owner
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

    heartbeat = _RunLockHeartbeat(user_id, lock_owner) if owns_user_lock else None
    if heartbeat:
        heartbeat.start()
    try:
        yield
    finally:
        if heartbeat:
            heartbeat.stop()
        _run_state.user_id = None
        _run_state.run_id = None
        _run_state.owns_user_lock = True
        _run_state.lock_owner = None
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
    time_limit=3600,  # 1 час — потолок прогона (было 3 часа); зависший прогон
    # Celery принудительно снимет максимум через час, а не через три.
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
    run_lock_owner = new_run_lock_owner(f"task:{self.request.id or 'local'}")

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
        lock_acquired = acquire_run_lock(user_id, owner=run_lock_owner)

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
                # Свежий лок не трогаем: прогон мог стартовать секунду назад и
                # ещё не создать запись в БД. Снимаем только старый лок и только
                # если он не сменил владельца между чтением и удалением.
                if release_stale_run_lock(user_id, _STALE_RUN_LOCK_MIN_AGE_SEC):
                    logger.warning(
                        "[Celery] user=%s: run lock existed but DB has no running runs; "
                        "stale lock released, retrying once",
                        user_id,
                    )
                    lock_acquired = acquire_run_lock(user_id, owner=run_lock_owner)
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
        lock_owner=None if priority else run_lock_owner,
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
                bypass_competitor_cooldown=priority,
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
                    release_run_lock(user_id, owner=run_lock_owner)
                except Exception as e:
                    logger.error(f"[Celery] user={user_id}: release_run_lock failed: {e}")
