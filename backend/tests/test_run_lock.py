"""
Tests for the per-user distributed run lock (Redis SET NX).

The lock prevents two concurrent repricer prog runs for the same user from
stomping on each other (which would cause WB API throttling / 429 storms).

We patch `_get_client` to return a MagicMock so tests don't require a live
Redis instance and can simulate failure modes deterministically.
"""

from unittest.mock import MagicMock, patch

import pytest
import redis as redis_lib

from app import redis_client


class TestAcquireRunLock:
    def test_returns_true_on_first_acquire(self):
        """Свежий ключ → SET NX возвращает True (lock получен)."""
        mock_client = MagicMock()
        mock_client.set.return_value = True
        with patch.object(redis_client, "_get_client", return_value=mock_client):
            assert redis_client.acquire_run_lock(user_id=1) is True

        # Проверяем правильность SET-команды: ключ, NX=True, EX=ttl
        args, kwargs = mock_client.set.call_args
        assert args[0] == "repricer:run_lock:1"
        assert args[1] == "1"
        assert kwargs.get("nx") is True
        assert kwargs.get("ex") == 10860  # default TTL — чуть больше time_limit=10800s

    def test_returns_false_when_already_locked(self):
        """Если ключ уже существует, SET NX вернёт None → acquire_run_lock=False."""
        mock_client = MagicMock()
        mock_client.set.return_value = None  # SET NX без перезаписи
        with patch.object(redis_client, "_get_client", return_value=mock_client):
            assert redis_client.acquire_run_lock(user_id=1) is False

    def test_custom_ttl_passed_to_redis(self):
        mock_client = MagicMock()
        mock_client.set.return_value = True
        with patch.object(redis_client, "_get_client", return_value=mock_client):
            redis_client.acquire_run_lock(user_id=42, ttl=600)

        _, kwargs = mock_client.set.call_args
        assert kwargs.get("ex") == 600

    def test_returns_false_on_redis_failure(self):
        """Если Redis недоступен — safer default: блокируем запуск.

        Это критично: если acquire_run_lock вернёт True при ошибке Redis,
        возможны параллельные запуски и шторм 429 от WB.
        """
        mock_client = MagicMock()
        mock_client.set.side_effect = redis_lib.ConnectionError("boom")
        with patch.object(redis_client, "_get_client", return_value=mock_client):
            assert redis_client.acquire_run_lock(user_id=1) is False

    def test_unique_key_per_user(self):
        """Разные пользователи → разные ключи (изоляция блокировок)."""
        mock_client = MagicMock()
        mock_client.set.return_value = True
        with patch.object(redis_client, "_get_client", return_value=mock_client):
            redis_client.acquire_run_lock(user_id=1)
            redis_client.acquire_run_lock(user_id=2)

        keys = [c.args[0] for c in mock_client.set.call_args_list]
        assert keys == ["repricer:run_lock:1", "repricer:run_lock:2"]


class TestReleaseRunLock:
    def test_deletes_correct_key(self):
        mock_client = MagicMock()
        with patch.object(redis_client, "_get_client", return_value=mock_client):
            redis_client.release_run_lock(user_id=7)

        mock_client.delete.assert_called_once_with("repricer:run_lock:7")

    def test_does_not_raise_on_redis_error(self):
        """release не должен прокидывать исключения (это часть finally)."""
        mock_client = MagicMock()
        mock_client.delete.side_effect = redis_lib.ConnectionError("boom")
        with patch.object(redis_client, "_get_client", return_value=mock_client):
            # Главное: не падает
            redis_client.release_run_lock(user_id=1)


class TestLockLifecycle:
    """acquire → release → acquire снова работает корректно."""

    def test_acquire_release_acquire(self):
        mock_client = MagicMock()
        # Первый SET: True (получили). Второй (после release): True снова.
        mock_client.set.side_effect = [True, True]
        with patch.object(redis_client, "_get_client", return_value=mock_client):
            assert redis_client.acquire_run_lock(user_id=1) is True
            redis_client.release_run_lock(user_id=1)
            assert redis_client.acquire_run_lock(user_id=1) is True

        # SET звался дважды, DELETE один раз
        assert mock_client.set.call_count == 2
        mock_client.delete.assert_called_once_with("repricer:run_lock:1")


class TestOwnedRunLock:
    """Лок с владельцем: продлевается и снимается только свой."""

    def test_owner_is_stored_as_lock_value(self):
        mock_client = MagicMock()
        mock_client.set.return_value = True
        owner = redis_client.new_run_lock_owner("task:abc")
        with patch.object(redis_client, "_get_client", return_value=mock_client):
            assert redis_client.acquire_run_lock(user_id=1, owner=owner) is True
        assert mock_client.set.call_args.args[1] == owner

    def test_release_with_owner_uses_compare_and_delete(self):
        mock_client = MagicMock()
        with patch.object(redis_client, "_get_client", return_value=mock_client):
            redis_client.release_run_lock(user_id=1, owner="task:x:1")
        mock_client.delete.assert_not_called()
        args = mock_client.eval.call_args.args
        assert args[2:] == ("repricer:run_lock:1", "task:x:1")

    def test_refresh_with_foreign_owner_reports_lost_lock(self):
        mock_client = MagicMock()
        mock_client.eval.return_value = 0  # в ключе чужое значение
        with patch.object(redis_client, "_get_client", return_value=mock_client):
            assert redis_client.refresh_run_lock(1, 600, owner="task:x:1") is False
        mock_client.expire.assert_not_called()

    def test_acquired_at_is_parsed_from_owner(self):
        assert redis_client.run_lock_acquired_at("task:abc:uuid:1700000000") == 1700000000.0
        assert redis_client.run_lock_acquired_at("1") is None

    def test_fresh_lock_is_not_released_as_stale(self):
        mock_client = MagicMock()
        mock_client.get.return_value = redis_client.new_run_lock_owner("task:new")
        with patch.object(redis_client, "_get_client", return_value=mock_client):
            assert redis_client.release_stale_run_lock(1, min_age_sec=120) is False
        mock_client.eval.assert_not_called()

    def test_old_lock_is_released_only_if_unchanged(self):
        mock_client = MagicMock()
        mock_client.get.return_value = "task:old:uuid:1000"
        mock_client.eval.return_value = 1
        with patch.object(redis_client, "_get_client", return_value=mock_client):
            assert redis_client.release_stale_run_lock(1, min_age_sec=120) is True
        assert mock_client.eval.call_args.args[2:] == ("repricer:run_lock:1", "task:old:uuid:1000")

    def test_guard_is_active_when_redis_fails(self):
        mock_client = MagicMock()
        mock_client.exists.side_effect = redis_lib.ConnectionError("boom")
        with patch.object(redis_client, "_get_client", return_value=mock_client):
            assert redis_client.guard_active("wb_quarantine_hold:1") is True


class TestProductLocks:
    def test_batch_acquire_returns_only_created_locks(self):
        mock_client = MagicMock()
        pipe = MagicMock()
        pipe.execute.return_value = [True, None]
        mock_client.pipeline.return_value = pipe
        mock_client.mget.return_value = ["another-owner"]

        with patch.object(redis_client, "_get_client", return_value=mock_client):
            acquired = redis_client.acquire_product_locks(7, [10, 11], "owner-a")

        assert acquired == {10}
        assert pipe.set.call_count == 2
        assert pipe.set.call_args_list[0].args[:2] == (
            "repricer:product_lock:7:10",
            "owner-a",
        )

    def test_same_owner_is_reentrant_and_refreshes_ttl(self):
        mock_client = MagicMock()
        create_pipe = MagicMock()
        create_pipe.execute.return_value = [None]
        renew_pipe = MagicMock()
        renew_pipe.execute.return_value = [True]
        mock_client.pipeline.side_effect = [create_pipe, renew_pipe]
        mock_client.mget.return_value = ["owner-a"]

        with patch.object(redis_client, "_get_client", return_value=mock_client):
            acquired = redis_client.acquire_product_locks(7, [10], "owner-a", ttl=120)

        assert acquired == {10}
        renew_pipe.expire.assert_called_once_with("repricer:product_lock:7:10", 120)

    def test_acquire_fails_closed_on_redis_error(self):
        mock_client = MagicMock()
        mock_client.pipeline.side_effect = redis_lib.ConnectionError("boom")
        with patch.object(redis_client, "_get_client", return_value=mock_client):
            assert redis_client.acquire_product_locks(7, [10], "owner-a") is None

    def test_release_uses_compare_and_delete_script(self):
        mock_client = MagicMock()
        pipe = MagicMock()
        mock_client.pipeline.return_value = pipe
        with patch.object(redis_client, "_get_client", return_value=mock_client):
            redis_client.release_product_locks(7, [10], "owner-a")

        pipe.eval.assert_called_once()
        args = pipe.eval.call_args.args
        assert args[1:] == (1, "repricer:product_lock:7:10", "owner-a")
        pipe.execute.assert_called_once_with()


# ─── Catalog sync lock ────────────────────────────────────────────────────────


class TestAcquireCatalogSyncLock:
    """Per-account дедупликация catalog.sync_account — антишторм после деплоя."""

    def test_returns_true_on_first_acquire(self):
        mock_client = MagicMock()
        mock_client.set.return_value = True
        with patch.object(redis_client, "_get_client", return_value=mock_client):
            assert redis_client.acquire_catalog_sync_lock(account_id=9) is True

        args, kwargs = mock_client.set.call_args
        assert args[0] == "catalog:sync_lock:9"
        assert kwargs.get("nx") is True
        assert kwargs.get("ex") == 3600  # default TTL = 1 час

    def test_returns_false_when_already_locked(self):
        """Главный тест: повторная постановка sync для того же аккаунта → False."""
        mock_client = MagicMock()
        mock_client.set.return_value = None
        with patch.object(redis_client, "_get_client", return_value=mock_client):
            assert redis_client.acquire_catalog_sync_lock(account_id=9) is False

    def test_locks_are_independent_per_account(self):
        """Lock на account=9 не влияет на account=10."""
        mock_client = MagicMock()
        mock_client.set.return_value = True
        with patch.object(redis_client, "_get_client", return_value=mock_client):
            redis_client.acquire_catalog_sync_lock(account_id=9)
            redis_client.acquire_catalog_sync_lock(account_id=10)

        keys = [c.args[0] for c in mock_client.set.call_args_list]
        assert keys == ["catalog:sync_lock:9", "catalog:sync_lock:10"]

    def test_returns_false_on_redis_failure(self):
        """Redis недоступен → fail closed (safer default)."""
        mock_client = MagicMock()
        mock_client.set.side_effect = redis_lib.ConnectionError("boom")
        with patch.object(redis_client, "_get_client", return_value=mock_client):
            assert redis_client.acquire_catalog_sync_lock(account_id=9) is False


class TestReleaseCatalogSyncLock:
    def test_deletes_correct_key(self):
        mock_client = MagicMock()
        with patch.object(redis_client, "_get_client", return_value=mock_client):
            redis_client.release_catalog_sync_lock(account_id=42)

        mock_client.delete.assert_called_once_with("catalog:sync_lock:42")

    def test_does_not_raise_on_redis_error(self):
        mock_client = MagicMock()
        mock_client.delete.side_effect = redis_lib.ConnectionError("boom")
        with patch.object(redis_client, "_get_client", return_value=mock_client):
            redis_client.release_catalog_sync_lock(account_id=1)  # не падает
