"""
Tests for the Celery task `run_repricer_for_user`.

The task itself is thin glue around RepricerEngine; what it owns is:
  - acquiring/releasing the per-user distributed lock
  - validating user exists and is active
  - calling the engine and returning a status dict

We patch:
  - `acquire_run_lock` / `release_run_lock` to control lock state
  - `SessionLocal` to redirect DB lookups to the in-memory test session
  - `RepricerEngine` to keep the test fast and independent of the engine
"""

from unittest.mock import MagicMock, patch

import pytest

from app.models.repricer_run import RepricerRun
from app.tasks import repricer_task


class _SessionProxy:
    """Прокидывает все вызовы к настоящей тестовой сессии, но close() — no-op,
    чтобы task.finally не закрыла сессию раньше времени (это ломает фикстуру)."""

    def __init__(self, real_session):
        self._real = real_session

    def __getattr__(self, name):
        return getattr(self._real, name)

    def close(self):
        # no-op: фикстура db_session сама всё откатит и закроет
        pass


@pytest.fixture()
def patched_session(db_session):
    """Перенаправить SessionLocal() внутри task на тестовую сессию."""
    proxy = _SessionProxy(db_session)
    with patch("app.tasks.repricer_task.SessionLocal", return_value=proxy):
        yield


class TestLockGate:
    """The task should fail closed if the lock can't be acquired."""

    def test_skipped_when_lock_busy(self, test_user):
        """acquire_run_lock returns False ⇒ task exits without touching DB/engine."""
        with patch("app.tasks.repricer_task.acquire_run_lock", return_value=False), \
             patch("app.tasks.repricer_task.release_run_lock") as mock_release, \
             patch("app.tasks.repricer_task.SessionLocal") as mock_session:
            result = repricer_task.run_repricer_for_user.apply(
                args=(test_user.id, [1]),
                kwargs={"run_type": "scheduled"},
            ).get()

        assert result == {"status": "skipped", "reason": "already running"}
        # До БД и release дойти не должно
        mock_session.assert_not_called()
        mock_release.assert_not_called()

    def test_existing_queued_manual_run_times_out_when_lock_stays_busy(self, patched_session, db_session, test_user):
        """Async API keeps a manual run queued while waiting for the active lock."""
        running = RepricerRun(
            user_id=test_user.id,
            run_type="manual",
            status="running",
            total_products=1,
            updated_count=0,
            skipped_count=0,
            error_count=0,
        )
        run = RepricerRun(
            user_id=test_user.id,
            run_type="manual",
            status="queued",
            total_products=1,
            updated_count=0,
            skipped_count=0,
            error_count=0,
        )
        db_session.add(running)
        db_session.add(run)
        db_session.commit()

        with patch("app.tasks.repricer_task._MANUAL_LOCK_MAX_RETRIES", 1), \
             patch("app.tasks.repricer_task.acquire_run_lock", return_value=False), \
             patch("app.tasks.repricer_task.release_run_lock") as mock_release:
            result = repricer_task.run_repricer_for_user.apply(
                args=(test_user.id, [1]),
                kwargs={"run_type": "manual", "existing_run_id": run.id},
            ).get()

        db_session.refresh(run)
        assert result == {"status": "skipped", "reason": "active run timeout"}
        assert run.status == "failed"
        assert "Timed out waiting" in run.error_message
        assert run.completed_at is not None
        mock_release.assert_not_called()

    def test_stale_lock_without_running_run_is_released_and_retried(
        self, patched_session, db_session, test_user
    ):
        """A stale Redis lock must not block a queued async run forever."""
        run = RepricerRun(
            user_id=test_user.id,
            run_type="manual",
            status="queued",
            total_products=1,
            updated_count=0,
            skipped_count=0,
            error_count=0,
        )
        db_session.add(run)
        db_session.commit()
        fake_run = MagicMock(id=run.id, status="completed", updated_count=3)

        async def _fake_run_repricer(*args, **kwargs):
            return fake_run

        with patch("app.tasks.repricer_task.acquire_run_lock", side_effect=[False, True]) as mock_acquire, \
             patch("app.tasks.repricer_task.release_run_lock") as mock_release, \
             patch("app.services.repricer_engine.RepricerEngine") as mock_engine_cls:
            mock_engine_cls.return_value.run_repricer = _fake_run_repricer
            result = repricer_task.run_repricer_for_user.apply(
                args=(test_user.id, [1]),
                kwargs={"run_type": "manual", "existing_run_id": run.id},
            ).get()

        assert result["run_id"] == run.id
        assert result["status"] == "completed"
        assert mock_acquire.call_count == 2
        assert mock_release.call_count == 2


class TestUserValidation:
    """Внутри lock'а проверяем, что user существует и активен."""

    def test_skipped_when_user_not_found(self, patched_session):
        """user_id, которого нет в БД → задача выходит, лок отпускается."""
        with patch("app.tasks.repricer_task.acquire_run_lock", return_value=True), \
             patch("app.tasks.repricer_task.release_run_lock") as mock_release:
            result = repricer_task.run_repricer_for_user.apply(
                args=(999_999, [1]),
                kwargs={"run_type": "scheduled"},
            ).get()

        assert result["status"] == "skipped"
        assert "not found" in result["reason"] or "inactive" in result["reason"]
        mock_release.assert_called_once_with(999_999)

    def test_skipped_when_user_inactive(self, patched_session, test_user, db_session):
        """Заблокированный пользователь не должен запускать репрайсер."""
        test_user.is_active = False
        db_session.commit()

        with patch("app.tasks.repricer_task.acquire_run_lock", return_value=True), \
             patch("app.tasks.repricer_task.release_run_lock") as mock_release:
            result = repricer_task.run_repricer_for_user.apply(
                args=(test_user.id, [1]),
                kwargs={"run_type": "scheduled"},
            ).get()

        assert result["status"] == "skipped"
        mock_release.assert_called_once_with(test_user.id)


class TestLockLifecycle:
    """release_run_lock должен быть вызван и при успехе, и при ошибке."""

    def test_release_called_on_success(self, patched_session, test_user):
        """Успешный прогон → release_run_lock вызван ровно один раз."""
        fake_run = MagicMock(id=42, status="completed", updated_count=5)

        async def _fake_run_repricer(*args, **kwargs):
            return fake_run

        with patch("app.tasks.repricer_task.acquire_run_lock", return_value=True), \
             patch("app.tasks.repricer_task.release_run_lock") as mock_release, \
             patch("app.services.repricer_engine.RepricerEngine") as mock_engine_cls:
            mock_engine_cls.return_value.run_repricer = _fake_run_repricer

            result = repricer_task.run_repricer_for_user.apply(
                args=(test_user.id, [1]),
                kwargs={"run_type": "manual"},
            ).get()

        assert result["run_id"] == 42
        assert result["status"] == "completed"
        mock_release.assert_called_once_with(test_user.id)

    def test_release_called_on_exception(self, patched_session, test_user):
        """Если движок упал — release всё равно вызывается (finally)."""

        async def _boom(*args, **kwargs):
            raise ValueError("engine exploded")

        with patch("app.tasks.repricer_task.acquire_run_lock", return_value=True), \
             patch("app.tasks.repricer_task.release_run_lock") as mock_release, \
             patch("app.services.repricer_engine.RepricerEngine") as mock_engine_cls:
            mock_engine_cls.return_value.run_repricer = _boom

            result = repricer_task.run_repricer_for_user.apply(
                args=(test_user.id, [1]),
                kwargs={"run_type": "manual"},
            )
            with pytest.raises(ValueError, match="engine exploded"):
                result.get()

        mock_release.assert_called_once_with(test_user.id)


# ─── Graceful SIGTERM handling ────────────────────────────────────────────────


class TestAbortCurrentRun:
    """`_abort_current_run` — финализатор для сигнал-хендлера."""

    def test_noop_when_no_active_run(self):
        """Если _run_state.user_id не установлен — ничего не делаем (no exception)."""
        from app.tasks.repricer_task import _abort_current_run, _run_state
        assert _run_state.user_id is None
        _abort_current_run("test reason")

    def test_marks_run_as_failed_and_releases_lock(
        self, patched_session, test_user, test_account, db_session
    ):
        """При SIGTERM-сценарии: running прогон → failed, lock отпущен."""
        from app.models.repricer_run import RepricerRun
        from app.tasks.repricer_task import _abort_current_run, _run_state

        # Воссоздаём ситуацию зомби-прогона: запись в running без owner-кода
        run = RepricerRun(
            user_id=test_user.id,
            run_type="scheduled",
            status="running",
            total_products=1000,
        )
        db_session.add(run)
        db_session.commit()

        _run_state.user_id = test_user.id
        try:
            with patch("app.tasks.repricer_task.release_run_lock") as mock_release:
                _abort_current_run("test: simulated SIGTERM")
            mock_release.assert_called_once_with(test_user.id)
        finally:
            _run_state.user_id = None

        db_session.refresh(run)
        assert run.status == "failed"
        assert run.completed_at is not None
        assert "SIGTERM" in (run.error_message or "")

    def test_safe_when_no_running_run_for_user(self, patched_session, test_user):
        """Если в БД нет running-прогона — просто отпускаем lock и выходим."""
        from app.tasks.repricer_task import _abort_current_run, _run_state

        _run_state.user_id = test_user.id
        try:
            with patch("app.tasks.repricer_task.release_run_lock") as mock_release:
                _abort_current_run("test: no run")
            # Даже без прогона lock всё равно отпускаем (защита от висящего ключа)
            mock_release.assert_called_once_with(test_user.id)
        finally:
            _run_state.user_id = None


class TestGracefulShutdownGuard:
    """`_graceful_shutdown_guard` — контекстный менеджер сигнал-хендлеров."""

    def test_sets_and_clears_run_state(self):
        from app.tasks.repricer_task import _graceful_shutdown_guard, _run_state

        assert _run_state.user_id is None
        with _graceful_shutdown_guard(user_id=42):
            assert _run_state.user_id == 42
        assert _run_state.user_id is None

    def test_clears_state_on_exception(self):
        """Даже если внутри контекста выброшено исключение — state очищается."""
        from app.tasks.repricer_task import _graceful_shutdown_guard, _run_state

        try:
            with _graceful_shutdown_guard(user_id=42):
                raise RuntimeError("oops")
        except RuntimeError:
            pass
        assert _run_state.user_id is None

    def test_restores_previous_handlers(self):
        """Прежние SIGTERM/SIGINT хендлеры восстанавливаются на выходе."""
        import signal
        from app.tasks.repricer_task import _graceful_shutdown_guard

        prev_term = signal.getsignal(signal.SIGTERM)
        prev_int = signal.getsignal(signal.SIGINT)

        with _graceful_shutdown_guard(user_id=1):
            # Внутри — наш хендлер
            assert signal.getsignal(signal.SIGTERM) is not prev_term
            assert signal.getsignal(signal.SIGINT) is not prev_int

        assert signal.getsignal(signal.SIGTERM) == prev_term
        assert signal.getsignal(signal.SIGINT) == prev_int
