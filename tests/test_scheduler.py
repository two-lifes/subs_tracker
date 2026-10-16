"""Reminder planning, auto-renewal and delivery, with simulated time.

Owner is in Europe/Moscow (UTC+3), reminders at 10:00 local = 07:00 UTC.
Claude Code is paid on 16.10.2026, so the next charge is 16.11.2026.
"""

import asyncio
from datetime import UTC, date, datetime

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetMe

from bot import clock
from bot.db.models import Notification, Subscription, User
from bot.keyboards.menu import BTN_ADD, BTN_LIST
from bot.keyboards.notify import NotifCb
from bot.keyboards.subs import PeriodCb, SubCb
from bot.services.billing import PeriodUnit
from bot.services.notifications import plan
from tests.harness import onboard


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def fake_clock():
    fake = FakeClock()
    clock.set_clock(fake)
    yield fake
    clock.set_clock(lambda: datetime.now(UTC))


def utc(month: int, day: int, hour: int, minute: int = 0, year: int = 2026) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


async def add_claude(h, paymode: str) -> None:
    await onboard(h)
    await h.text(BTN_ADD)
    await h.text("Claude Code")
    await h.tap("acur:USD")
    await h.text("22.24")
    await h.tap(PeriodCb(unit=PeriodUnit.MONTH).pack())
    await h.tap(paymode)
    await h.tap("paid:0")
    await h.tap("add:save")
    [sub] = await h.subs()
    assert sub.next_charge_date == date(2026, 11, 16)
    assert sub.auto_renew is (paymode == "pay:auto")
    h.net.requests.clear()


@pytest.fixture
async def setup(h, fake_clock):
    await add_claude(h, "pay:auto")
    return h, fake_clock


@pytest.fixture
async def manual(h, fake_clock):
    await add_claude(h, "pay:manual")
    return h, fake_clock


async def run_at(h, fake_clock, now: datetime) -> None:
    fake_clock.now = now
    await h.tick(now)


async def test_reminder_before_charge_is_sent_once(setup):
    h, fc = setup
    await run_at(h, fc, utc(11, 13, 6, 59))
    assert h.net.sent() == []

    await run_at(h, fc, utc(11, 13, 7, 0))
    await run_at(h, fc, utc(11, 13, 7, 1))  # a later tick must not repeat it
    [msg] = h.net.sent()
    assert "Через 3 дня (16.11)" in msg.text
    assert "22.24 USD" in msg.text


async def test_auto_renew_records_payment_and_moves_date(setup):
    h, fc = setup
    await run_at(h, fc, utc(11, 13, 7))
    await run_at(h, fc, utc(11, 16, 7))
    await run_at(h, fc, utc(11, 16, 8))

    texts = [m.text for m in h.net.sent()]
    assert len(texts) == 2
    assert "Сегодня списание" in texts[1] and "16.12.2026" in texts[1]
    [sub] = await h.subs()
    assert sub.next_charge_date == date(2026, 12, 16)
    assert [p.charge_date for p in await h.payments()] == [date(2026, 10, 16), date(2026, 11, 16)]


async def test_after_downtime_only_latest_reminder_is_sent(setup):
    h, fc = setup
    # Bot was off from before the 3-day reminder until after the charge moment
    await run_at(h, fc, utc(11, 16, 9))
    [msg] = h.net.sent()
    assert "Сегодня списание" in msg.text
    statuses = {n.kind: n.status for n in await h.notifications()}
    assert statuses == {"before_3": "skipped", "charged": "sent"}


async def test_long_downtime_catches_up_silently(setup):
    h, fc = setup
    await run_at(h, fc, utc(1, 20, 9, year=2027))
    assert h.net.sent() == []
    [sub] = await h.subs()
    assert sub.next_charge_date == date(2027, 2, 16)
    assert len(await h.payments()) == 4  # the initial one + Nov, Dec, Jan


async def test_network_error_is_retried(setup):
    h, fc = setup
    h.net.fail_next = TelegramNetworkError(method=GetMe(), message="offline")
    await run_at(h, fc, utc(11, 13, 7))
    assert h.net.sent() == []
    await run_at(h, fc, utc(11, 13, 7, 1))
    assert len(h.net.sent()) == 1


async def test_snooze_resends_next_day(setup):
    h, fc = setup
    await run_at(h, fc, utc(11, 13, 7))
    [n] = await h.notifications()
    await h.tap(NotifCb(action="snooze", id=n.id).pack())

    await run_at(h, fc, utc(11, 14, 6, 59))
    assert len(h.net.sent()) == 1
    await run_at(h, fc, utc(11, 14, 7))
    await run_at(h, fc, utc(11, 14, 7, 1))
    sent = h.net.sent()
    assert len(sent) == 2
    assert "Через 2 дня" in sent[1].text


def button_texts(msg) -> list[str]:
    return [b.text for row in msg.reply_markup.inline_keyboard for b in row]


async def test_snooze_offered_only_when_charge_is_far_enough(setup):
    h, fc = setup
    await run_at(h, fc, utc(11, 13, 7))
    assert "⏰ Напомнить завтра" in button_texts(h.net.sent()[-1])


async def test_no_snooze_when_charge_is_today(setup):
    h, fc = setup
    # Bot was down on the 13th; on the 16th the reminder arrives before the 10:00 charge
    await run_at(h, fc, utc(11, 16, 6))
    [msg] = h.net.sent()
    assert "Сегодня (16.11)" in msg.text
    assert "⏰ Напомнить завтра" not in button_texts(msg)

    # A snooze tap on such a message (e.g. an old client) must not schedule anything
    [n] = await h.notifications()
    await h.tap(NotifCb(action="snooze", id=n.id).pack())
    [n] = await h.notifications()
    assert n.snooze_until is None


async def test_cancel_from_reminder_archives(setup):
    h, fc = setup
    await run_at(h, fc, utc(11, 13, 7))
    [n] = await h.notifications()
    await h.tap(NotifCb(action="cancel", id=n.id).pack())
    await run_at(h, fc, utc(11, 16, 7))

    [sub] = await h.subs()
    assert sub.status == "archived"
    assert len(h.net.sent()) == 1  # no charge message for an archived subscription
    assert len(await h.payments()) == 1


async def test_price_change_after_charge(setup):
    h, fc = setup
    await run_at(h, fc, utc(11, 16, 7))
    charged = next(n for n in await h.notifications() if n.kind == "charged")
    await h.tap(NotifCb(action="price", id=charged.id).pack())
    await h.text("24.99")

    [sub] = await h.subs()
    assert sub.amount_minor == 2499
    payments = {p.charge_date: p.amount_minor for p in await h.payments()}
    assert payments == {date(2026, 10, 16): 2224, date(2026, 11, 16): 2499}


async def test_no_charge_removes_payment(setup):
    h, fc = setup
    await run_at(h, fc, utc(11, 16, 7))
    charged = next(n for n in await h.notifications() if n.kind == "charged")
    await h.tap(NotifCb(action="nocharge", id=charged.id).pack())
    [sub] = await h.subs()
    assert sub.status == "archived"
    assert [p.charge_date for p in await h.payments()] == [date(2026, 10, 16)]


def asks(h) -> list[str]:
    return [m.text for m in h.net.sent() if "Оплатили?" in m.text]


async def test_toggle_switches_to_manual(setup):
    h, fc = setup
    [sub] = await h.subs()
    await h.tap(SubCb(action="renew", id=sub.id).pack())
    [sub] = await h.subs()
    assert sub.auto_renew is False


async def test_manual_payment_asks_daily_until_paid(manual):
    h, fc = manual
    await run_at(h, fc, utc(11, 16, 7))
    await run_at(h, fc, utc(11, 16, 8))  # same day: no repeat
    assert len(asks(h)) == 1
    assert "Сегодня срок оплаты" in asks(h)[0]

    await run_at(h, fc, utc(11, 17, 7))
    await run_at(h, fc, utc(11, 18, 7))
    assert len(asks(h)) == 3
    assert "ждёт оплаты" in asks(h)[-1] and "2 дня назад" in asks(h)[-1]
    [sub] = await h.subs()
    assert sub.next_charge_date == date(2026, 11, 16)  # not moved until answered
    assert len(await h.payments()) == 1

    n = next(n for n in await h.notifications() if n.kind == "due")
    await h.tap(NotifCb(action="paid", id=n.id).pack())
    await h.tap(NotifCb(action="paid", id=n.id).pack())  # double tap is harmless
    [sub] = await h.subs()
    assert sub.next_charge_date == date(2026, 12, 16)
    assert [p.charge_date for p in await h.payments()] == [date(2026, 10, 16), date(2026, 11, 16)]

    await run_at(h, fc, utc(11, 19, 7))
    assert len(asks(h)) == 3  # answered: no more asking


async def test_stop_from_due_archives_and_stops_asking(manual):
    h, fc = manual
    await run_at(h, fc, utc(11, 16, 7))
    n = next(n for n in await h.notifications() if n.kind == "due")
    await h.tap(NotifCb(action="stop", id=n.id).pack())
    await run_at(h, fc, utc(11, 17, 7))
    [sub] = await h.subs()
    assert sub.status == "archived"
    assert len(asks(h)) == 1


async def test_overdue_marked_in_list_and_paid_from_card(manual):
    h, fc = manual
    await run_at(h, fc, utc(11, 17, 9))
    await h.text(BTN_LIST)
    assert "ждёт оплаты" in h.net.last_text()

    [sub] = await h.subs()
    await h.tap(SubCb(action="paid", id=sub.id).pack())
    [sub] = await h.subs()
    assert sub.next_charge_date == date(2026, 12, 16)
    assert len(await h.payments()) == 2

    await h.text(BTN_LIST)
    assert "ждёт оплаты" not in h.net.last_text()
    await run_at(h, fc, utc(11, 18, 7))
    assert len(asks(h)) == 1  # the daily ask stops once paid from the card


async def test_card_refuses_payment_too_early(manual):
    h, fc = manual
    fc.now = utc(10, 20, 9)
    [sub] = await h.subs()
    await h.tap(SubCb(action="paid", id=sub.id).pack())
    [sub] = await h.subs()
    assert sub.next_charge_date == date(2026, 11, 16)
    assert len(await h.payments()) == 1


async def test_manual_paid_early_from_reminder(manual):
    h, fc = manual
    await run_at(h, fc, utc(11, 13, 7))
    [msg] = h.net.sent()
    assert "срок оплаты" in msg.text
    [n] = await h.notifications()
    await h.tap(NotifCb(action="paid", id=n.id).pack())

    await run_at(h, fc, utc(11, 16, 7))
    assert asks(h) == []
    [sub] = await h.subs()
    assert sub.next_charge_date == date(2026, 12, 16)


async def test_editing_date_drops_stale_reminder(setup):
    h, fc = setup
    fc.now = utc(11, 13, 7)
    # Plan only, then change the date before delivery happens
    async with h.sessionmaker() as session, session.begin():
        await plan(session, fc.now)
    [sub] = await h.subs()
    await h.tap(f"sed:paid_on:{sub.id}")
    await h.text("10.11")
    await h.tick(fc.now)

    assert [m.text for m in h.net.sent() if m.text.startswith("⏰")] == []
    [n] = [n for n in await h.notifications() if n.charge_date == date(2026, 11, 16)]
    assert n.status == "skipped"


# ---- robustness ----


async def add_broken_subscription(h) -> Subscription:
    """A record whose timezone can't be loaded: every computation on it raises."""
    async with h.sessionmaker() as session, session.begin():
        broken_user = User(tg_id=555, timezone="Mars/Olympus", base_currency="USD")
        session.add(broken_user)
        await session.flush()
        sub = Subscription(
            user_id=broken_user.id, name="Broken", amount_minor=100, currency="USD",
            period_unit="month", period_count=1, anchor_date=date(2026, 10, 1),
            next_charge_date=date(2026, 11, 1),
        )
        session.add(sub)
        await session.flush()
        # Queued before the healthy reminder, so a crash on it would block the queue
        session.add(Notification(
            subscription_id=sub.id, charge_date=date(2026, 11, 1), kind="before_3",
            status="pending", created_at=utc(10, 29, 7),
        ))
    return sub


async def test_broken_record_does_not_block_other_reminders(setup):
    h, fc = setup
    broken = await add_broken_subscription(h)
    await run_at(h, fc, utc(11, 13, 7))

    [msg] = h.net.sent()
    assert "Claude Code" in msg.text
    statuses = {n.subscription_id: n.status for n in await h.notifications()}
    assert statuses[broken.id] == "skipped"


async def test_autopay_cannot_be_enabled_while_overdue(manual):
    h, fc = manual
    await run_at(h, fc, utc(11, 17, 9))  # the 16.11 charge is unanswered
    [sub] = await h.subs()
    await h.tap(SubCb(action="renew", id=sub.id).pack())
    assert "Сначала отметьте срок 16.11" in h.net.callback_answers()[-1]

    await run_at(h, fc, utc(11, 17, 9, 1))
    [sub] = await h.subs()
    assert sub.auto_renew is False
    assert len(await h.payments()) == 1  # the unpaid charge was not booked as paid


async def test_daily_plan_gets_no_pointless_before_reminders(h, fake_clock):
    await onboard(h)
    await h.text(BTN_ADD)
    await h.text("Daily")
    await h.tap("acur:USD")
    await h.text("1")
    await h.tap(PeriodCb(unit=PeriodUnit.DAY, count=0).pack())
    await h.text("1")
    await h.tap("pay:auto")
    await h.tap("paid:0")
    await h.tap("add:save")
    h.net.requests.clear()

    for day in (17, 18, 19):
        await run_at(h, fake_clock, utc(10, day, 6))
        await run_at(h, fake_clock, utc(10, day, 7))
    texts = [m.text for m in h.net.sent()]
    assert not any(t.startswith("⏰") for t in texts)
    assert sum("Сегодня списание" in t for t in texts) == 3


async def test_scheduler_and_user_actions_run_concurrently(manual):
    h, fc = manual
    await run_at(h, fc, utc(11, 16, 7))
    n = next(n for n in await h.notifications() if n.kind == "due")
    fc.now = utc(11, 17, 7)
    # Before the shared lock these could interleave inside one SQLite write window
    await asyncio.gather(
        h.tick(fc.now),
        h.tap(NotifCb(action="paid", id=n.id).pack()),
        h.text(BTN_LIST),
    )
    [sub] = await h.subs()
    assert sub.next_charge_date == date(2026, 12, 16)
    assert len(await h.payments()) == 2
