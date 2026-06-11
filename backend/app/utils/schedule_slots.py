from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _validate_interval(interval_minutes: int) -> None:
    if interval_minutes <= 0 or interval_minutes > 24 * 60:
        raise ValueError("interval_minutes must be between 1 and 1440")


def interval_slot_start(dt: datetime, interval_minutes: int) -> datetime:
    """Return the wall-clock slot start for dt in dt's own timezone."""
    _validate_interval(interval_minutes)
    minutes_since_midnight = dt.hour * 60 + dt.minute
    slot_minutes = (minutes_since_midnight // interval_minutes) * interval_minutes
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight + timedelta(minutes=slot_minutes)


def next_interval_slot(dt: datetime, interval_minutes: int) -> datetime:
    """Return the next wall-clock slot after dt in dt's own timezone."""
    _validate_interval(interval_minutes)
    minutes_since_midnight = dt.hour * 60 + dt.minute
    next_slot_minutes = ((minutes_since_midnight // interval_minutes) + 1) * interval_minutes
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight + timedelta(minutes=next_slot_minutes)


def is_interval_slot(dt: datetime, interval_minutes: int) -> bool:
    """True at exact wall-clock interval minutes, ignoring seconds."""
    _validate_interval(interval_minutes)
    minutes_since_midnight = dt.hour * 60 + dt.minute
    return minutes_since_midnight % interval_minutes == 0


def run_started_in_slot(
    started_at_utc: datetime | None,
    now_local: datetime,
    interval_minutes: int,
) -> bool:
    """Return True if a UTC run timestamp belongs to now_local's current slot."""
    if started_at_utc is None:
        return False
    if started_at_utc.tzinfo is None:
        started_at_utc = started_at_utc.replace(tzinfo=timezone.utc)
    else:
        started_at_utc = started_at_utc.astimezone(timezone.utc)
    started_local = started_at_utc.astimezone(now_local.tzinfo)
    return (
        interval_slot_start(started_local, interval_minutes)
        == interval_slot_start(now_local, interval_minutes)
    )
