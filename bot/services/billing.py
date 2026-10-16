"""Charge-date arithmetic. Pure functions, no I/O.

Every charge date is computed as ``anchor + k * period`` rather than by shifting the
previous date, so a subscription paid on Jan 31 renews on Feb 28 and then on Mar 31
(not Mar 28).
"""

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo


class PeriodUnit(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


# Average lengths used only for normalising prices to "per month"
_DAYS_PER_YEAR = Decimal("365.25")
_MONTHS_PER_YEAR = Decimal(12)


def _add_months(start: date, months: int) -> date:
    total = start.year * 12 + (start.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def charge_date(anchor: date, unit: PeriodUnit, count: int, k: int) -> date:
    """Date of the k-th charge (k=0 is the anchor itself)."""
    if count < 1:
        raise ValueError("period count must be >= 1")
    match unit:
        case PeriodUnit.DAY:
            return anchor + timedelta(days=count * k)
        case PeriodUnit.WEEK:
            return anchor + timedelta(weeks=count * k)
        case PeriodUnit.MONTH:
            return _add_months(anchor, count * k)
        case PeriodUnit.YEAR:
            return _add_months(anchor, 12 * count * k)
    raise ValueError(f"unknown period unit: {unit}")


def _estimate_k(anchor: date, unit: PeriodUnit, count: int, target: date) -> int:
    # Rough lower bound so the search below takes only a few steps even for old anchors
    days = (target - anchor).days
    match unit:
        case PeriodUnit.DAY:
            approx = days // count
        case PeriodUnit.WEEK:
            approx = days // (7 * count)
        case PeriodUnit.MONTH:
            approx = ((target.year - anchor.year) * 12 + target.month - anchor.month) // count
        case PeriodUnit.YEAR:
            approx = (target.year - anchor.year) // count
    return max(approx - 1, 0)


def first_charge_after(anchor: date, unit: PeriodUnit, count: int, after: date) -> date:
    """First charge date strictly later than ``after`` (never the anchor itself)."""
    k = max(_estimate_k(anchor, unit, count, after), 1)
    while (candidate := charge_date(anchor, unit, count, k)) <= after:
        k += 1
    return candidate


def next_charge_from_today(anchor: date, unit: PeriodUnit, count: int, today: date) -> date:
    """Next upcoming charge for a subscription paid on ``anchor``.

    A charge falling on ``today`` counts as upcoming unless it is the anchor payment itself.
    """
    if today <= anchor:
        return charge_date(anchor, unit, count, 1)
    return first_charge_after(anchor, unit, count, today - timedelta(days=1))


def min_period_days(unit: PeriodUnit, count: int) -> int:
    """Shortest possible gap between two charges (February for months)."""
    match unit:
        case PeriodUnit.DAY:
            return count
        case PeriodUnit.WEEK:
            return 7 * count
        case PeriodUnit.MONTH:
            return 28 * count
        case PeriodUnit.YEAR:
            return 365 * count
    raise ValueError(f"unknown period unit: {unit}")


def monthly_amount(amount_minor: int, unit: PeriodUnit, count: int) -> Decimal:
    """Price normalised to one average month, in minor units."""
    amount = Decimal(amount_minor)
    match unit:
        case PeriodUnit.DAY:
            per_month = amount * _DAYS_PER_YEAR / _MONTHS_PER_YEAR
        case PeriodUnit.WEEK:
            per_month = amount * _DAYS_PER_YEAR / 7 / _MONTHS_PER_YEAR
        case PeriodUnit.MONTH:
            per_month = amount
        case PeriodUnit.YEAR:
            per_month = amount / _MONTHS_PER_YEAR
    return per_month / count


@dataclass(frozen=True)
class Reminder:
    kind: str
    fire_at: datetime  # aware, UTC


def reminder_kind(offset_days: int) -> str:
    return "day_of" if offset_days == 0 else f"before_{offset_days}"


def reminders_for(
    charge: date, offsets: list[int], remind_time: time, tz: str
) -> list[Reminder]:
    """Reminder moments for one charge, in UTC, earliest first."""
    zone = ZoneInfo(tz)
    result = []
    for offset in sorted(set(offsets), reverse=True):
        local = datetime.combine(charge - timedelta(days=offset), remind_time, tzinfo=zone)
        result.append(Reminder(reminder_kind(offset), local.astimezone(ZoneInfo("UTC"))))
    return result


def local_today(now_utc: datetime, tz: str) -> date:
    return now_utc.astimezone(ZoneInfo(tz)).date()
