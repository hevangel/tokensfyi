"""Deterministic and system clock adapters."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    """A repeatable clock for local runs and tests."""

    def __init__(self, current: datetime | None = None) -> None:
        self._current = (current or datetime.now(UTC)).astimezone(UTC)

    def now(self) -> datetime:
        return self._current

    def set(self, current: datetime) -> None:
        self._current = current.astimezone(UTC)

    def advance(self, **kwargs: int) -> datetime:
        self._current += timedelta(**kwargs)
        return self._current
