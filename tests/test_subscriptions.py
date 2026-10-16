from datetime import date

from bot.db.models import Payment, User
from bot.services.billing import PeriodUnit
from bot.services.subscriptions import (
    Draft,
    archive,
    change_paid_on,
    change_period,
    create_subscription,
    get_subscription,
    list_payments,
    list_subscriptions,
    recent_currencies,
    restore,
)

TODAY = date(2026, 10, 16)


def claude_draft(**overrides) -> Draft:
    data = dict(
        name="Claude Code",
        amount_minor=2224,
        currency="USD",
        period_unit=PeriodUnit.MONTH,
        period_count=1,
        paid_on=TODAY,
    )
    data.update(overrides)
    return Draft(**data)


async def test_create_sets_next_charge_and_records_payment(session, user):
    sub = await create_subscription(session, user, claude_draft(), TODAY)
    assert sub.next_charge_date == date(2026, 11, 16)
    payments = await list_payments(session, sub)
    assert [(p.charge_date, p.amount_minor) for p in payments] == [(TODAY, 2224)]


async def test_list_sorted_and_archive_restore(session, user):
    late = await create_subscription(session, user, claude_draft(name="B"), TODAY)
    early = await create_subscription(
        session, user, claude_draft(name="A", paid_on=date(2026, 9, 20)), TODAY
    )
    assert [s.name for s in await list_subscriptions(session, user)] == ["A", "B"]

    archive(early)
    await session.flush()
    assert [s.id for s in await list_subscriptions(session, user)] == [late.id]
    assert [s.id for s in await list_subscriptions(session, user, archived=True)] == [early.id]

    restore(early, date(2026, 12, 1))
    assert early.status == "active"
    assert early.next_charge_date == date(2026, 12, 20)


async def test_change_period_reanchors_on_last_payment(session, user):
    sub = await create_subscription(
        session, user, claude_draft(paid_on=date(2026, 1, 31)), TODAY
    )
    await change_period(session, sub, PeriodUnit.YEAR, 1, TODAY)
    assert sub.anchor_date == date(2026, 1, 31)
    assert sub.next_charge_date == date(2027, 1, 31)


async def test_change_paid_on_moves_latest_payment(session, user):
    sub = await create_subscription(session, user, claude_draft(), TODAY)
    await change_paid_on(session, sub, date(2026, 10, 1), TODAY)
    assert sub.next_charge_date == date(2026, 11, 1)
    # The mistaken 16.10 entry is moved, not duplicated
    assert [p.charge_date for p in await list_payments(session, sub)] == [date(2026, 10, 1)]


async def test_change_paid_on_to_already_recorded_date(session, user):
    sub = await create_subscription(
        session, user, claude_draft(paid_on=date(2026, 9, 16)), TODAY
    )
    session.add(Payment(subscription_id=sub.id, charge_date=date(2026, 10, 16),
                        amount_minor=2224, currency="USD"))
    await session.flush()
    await change_paid_on(session, sub, date(2026, 9, 16), TODAY)
    assert [p.charge_date for p in await list_payments(session, sub)] == [date(2026, 9, 16)]


async def test_get_subscription_checks_owner(session, user):
    sub = await create_subscription(session, user, claude_draft(), TODAY)
    assert await get_subscription(session, user, sub.id) is sub

    other = User(tg_id=2)
    session.add(other)
    await session.flush()
    assert await get_subscription(session, other, sub.id) is None


async def test_recent_currencies(session, user):
    await create_subscription(session, user, claude_draft(currency="USD"), TODAY)
    await create_subscription(session, user, claude_draft(currency="RUB"), TODAY)
    await create_subscription(session, user, claude_draft(currency="USD"), TODAY)
    assert await recent_currencies(session, user) == ["USD", "RUB"]
