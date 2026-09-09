"""Clock protocol (T0). Deterministic clocks are injected in tests; production
uses SystemClock. All timestamps are RFC3339 UTC (models.format_timestamp).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now_utc(self) -> datetime:
        """Current UTC time; naive datetimes are forbidden."""
        ...


class SystemClock:
    def now_utc(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Test clock; advances only when moved explicitly."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("FixedClock requires an aware datetime")
        self._now = start

    def now_utc(self) -> datetime:
        return self._now

    def advance_seconds(self, seconds: float) -> None:
        from datetime import timedelta

        self._now = self._now + timedelta(seconds=seconds)


def is_expired(expires_at: str, now: datetime) -> bool:
    """Lease/expiry helper: True when expires_at < now (UTC strings)."""
    from agy_cloud.models import parse_timestamp

    return parse_timestamp(expires_at) <= now
