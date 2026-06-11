from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.utils.schedule_slots import (
    interval_slot_start,
    is_interval_slot,
    next_interval_slot,
    run_started_in_slot,
)


MSK = ZoneInfo("Europe/Moscow")


def dt(hour: int, minute: int, second: int = 0):
    return datetime(2026, 6, 11, hour, minute, second, tzinfo=MSK)


def test_30_minute_slots_align_to_hour_and_half_hour():
    assert is_interval_slot(dt(10, 0, 17), 30)
    assert is_interval_slot(dt(10, 30), 30)
    assert not is_interval_slot(dt(10, 15), 30)
    assert next_interval_slot(dt(10, 15), 30) == dt(10, 30)


def test_15_minute_slots_align_to_quarters():
    assert is_interval_slot(dt(10, 0), 15)
    assert is_interval_slot(dt(10, 15), 15)
    assert is_interval_slot(dt(10, 30), 15)
    assert is_interval_slot(dt(10, 45), 15)
    assert not is_interval_slot(dt(10, 50), 15)


def test_hourly_slots_align_to_top_of_hour():
    assert is_interval_slot(dt(10, 0), 60)
    assert not is_interval_slot(dt(10, 30), 60)
    assert next_interval_slot(dt(10, 30), 60) == dt(11, 0)


def test_two_hour_slots_align_from_local_midnight():
    assert is_interval_slot(dt(10, 0), 120)
    assert not is_interval_slot(dt(11, 0), 120)
    assert next_interval_slot(dt(11, 30), 120) == dt(12, 0)


def test_interval_slot_start_supports_non_hour_divisors():
    assert is_interval_slot(dt(10, 30), 45)
    assert not is_interval_slot(dt(10, 45), 45)
    assert interval_slot_start(dt(10, 50), 45) == dt(10, 30)
    assert next_interval_slot(dt(10, 50), 45) == dt(11, 15)


def test_run_started_in_current_local_slot():
    now = dt(10, 30, 20)
    started_same_slot = datetime(2026, 6, 11, 7, 30, 5, tzinfo=timezone.utc)
    started_prev_slot = datetime(2026, 6, 11, 7, 0, 5, tzinfo=timezone.utc)

    assert run_started_in_slot(started_same_slot, now, 30)
    assert not run_started_in_slot(started_prev_slot, now, 30)


def test_invalid_interval_rejected():
    with pytest.raises(ValueError):
        is_interval_slot(dt(10, 0), 0)
