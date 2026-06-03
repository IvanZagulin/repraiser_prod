"""
Tests for the Celery task `run_repricer_for_user`.

The task itself is thin glue around RepricerEngine; what it owns is:
  - acquiring/releasing the per-user distributed lock
  - validating user exists and is active
  - calling the engine and returning a status dict

We mock the engine and the lock helpers so tests are fast and deterministic.
We use `.apply()` (Celery's in-process executor) so no worker is needed.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.tasks import repricer_task


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
        # Никакой работы с БД и release не должно быть
        mock_session.assert_not_called()
        mock_release.assert_not_called()


class TestUserValidation:
    """Внутри lock'а проверяем, что user существует и активен."""

    def test_skipped_when_user_not_found(self):
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

    def test_skipped_when_user_inactive(self, test_user, db_session):
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

    def test_release_called_on_success(self, test_user):
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

    def test_release_called_on_exception(self, test_user):
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
            # EagerResult сохраняет исключение, .get() его перевыбрасывает
            with pytest.raises(ValueError, match="engine exploded"):
                result.get()

        mock_release.assert_called_once_with(test_user.id)
