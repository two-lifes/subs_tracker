from collections.abc import Callable
from datetime import UTC, date, datetime

from bot.services.billing import local_today

_clock: Callable[[], datetime] = lambda: datetime.now(UTC)  # noqa: E731


def utcnow() -> datetime:
    return _clock()


def set_clock(clock: Callable[[], datetime]) -> None:
    """Replaces the time source; used by tests to simulate days passing."""
    global _clock
    _clock = clock


def user_today(tz: str | None) -> date:
    return local_today(utcnow(), tz or "UTC")
