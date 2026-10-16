from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest

from bot.services.billing import (
    PeriodUnit,
    charge_date,
    first_charge_after,
    local_today,
    min_period_days,
    monthly_amount,
    next_charge_from_today,
    reminders_for,
)

M, Y, W, D = PeriodUnit.MONTH, PeriodUnit.YEAR, PeriodUnit.WEEK, PeriodUnit.DAY


def test_monthly_end_of_month_does_not_drift():
    anchor = date(2026, 1, 31)
    assert [charge_date(anchor, M, 1, k) for k in range(1, 5)] == [
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
        date(2026, 5, 31),
    ]


def test_leap_day_yearly():
    anchor = date(2024, 2, 29)
    assert charge_date(anchor, Y, 1, 1) == date(2025, 2, 28)
    assert charge_date(anchor, Y, 1, 4) == date(2028, 2, 29)


def test_every_n_periods_and_year_rollover():
    assert charge_date(date(2026, 11, 15), M, 3, 1) == date(2027, 2, 15)
    assert charge_date(date(2026, 10, 16), W, 2, 1) == date(2026, 10, 30)
    assert charge_date(date(2026, 10, 16), D, 10, 3) == date(2026, 11, 15)


def test_invalid_count():
    with pytest.raises(ValueError):
        charge_date(date(2026, 1, 1), M, 0, 1)


def test_claude_code_example():
    # Paid today -> next charge in exactly one month
    assert next_charge_from_today(date(2026, 10, 16), M, 1, date(2026, 10, 16)) == date(
        2026, 11, 16
    )


def test_next_charge_for_old_anchor():
    anchor = date(2020, 1, 31)
    assert next_charge_from_today(anchor, M, 1, date(2026, 10, 16)) == date(2026, 10, 31)
    # A charge falling on today is still upcoming
    assert next_charge_from_today(anchor, M, 1, date(2026, 10, 31)) == date(2026, 10, 31)
    assert next_charge_from_today(date(2025, 10, 16), Y, 1, date(2026, 10, 16)) == date(
        2026, 10, 16
    )


def test_first_charge_after_is_strict():
    anchor = date(2026, 1, 31)
    assert first_charge_after(anchor, M, 1, date(2026, 2, 28)) == date(2026, 3, 31)
    assert first_charge_after(anchor, M, 1, date(2026, 1, 31)) == date(2026, 2, 28)
    assert first_charge_after(anchor, M, 1, date(2025, 1, 1)) == date(2026, 2, 28)


@pytest.mark.parametrize("unit", list(PeriodUnit))
@pytest.mark.parametrize("count", [1, 2, 3])
def test_first_charge_after_matches_brute_force(unit, count):
    anchor = date(2023, 3, 31)
    # Enough daily charges to reach past the last `after` date
    dates = [charge_date(anchor, unit, count, k) for k in range(1, 2000)]
    for after in (date(2023, 3, 31), date(2024, 2, 29), date(2025, 12, 31), date(2026, 7, 1)):
        expected = next(d for d in dates if d > after)
        assert first_charge_after(anchor, unit, count, after) == expected


def test_min_period_days():
    assert min_period_days(D, 1) == 1
    assert min_period_days(W, 2) == 14
    assert min_period_days(M, 1) == 28
    assert min_period_days(Y, 1) == 365


def test_monthly_amount():
    assert monthly_amount(2224, M, 1) == Decimal(2224)
    assert monthly_amount(12000, Y, 1) == Decimal(1000)
    assert monthly_amount(3000, M, 3) == Decimal(1000)
    assert monthly_amount(700, W, 1).quantize(Decimal("0.01")) == Decimal("3043.75")


def test_reminders_for_in_user_timezone():
    reminders = reminders_for(date(2026, 11, 16), [0, 3], time(10, 0), "Europe/Moscow")
    assert [r.kind for r in reminders] == ["before_3", "day_of"]
    assert reminders[0].fire_at == datetime(2026, 11, 13, 7, 0, tzinfo=UTC)
    assert reminders[1].fire_at == datetime(2026, 11, 16, 7, 0, tzinfo=UTC)


def test_reminders_across_dst_change():
    # Berlin switches from CEST to CET on 2026-10-25
    reminders = reminders_for(date(2026, 10, 27), [3, 0], time(10, 0), "Europe/Berlin")
    assert reminders[0].fire_at == datetime(2026, 10, 24, 8, 0, tzinfo=UTC)
    assert reminders[1].fire_at == datetime(2026, 10, 27, 9, 0, tzinfo=UTC)


def test_local_today():
    now = datetime(2026, 10, 16, 22, 30, tzinfo=UTC)
    assert local_today(now, "Europe/Moscow") == date(2026, 10, 17)
    assert local_today(now, "America/New_York") == date(2026, 10, 16)
