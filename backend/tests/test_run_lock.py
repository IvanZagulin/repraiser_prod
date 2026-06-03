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
        assert kwargs.get("ex") == 3900  # default TTL

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
