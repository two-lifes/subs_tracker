"""Decides which reminders are due and performs auto-renewals. DB only, no Telegram I/O.

Every message goes through the ``notifications`` table: planning inserts ``pending`` rows,
delivery sends them and marks them ``sent``. A crash or network error between the two
just leaves the row pending for the next tick, and the unique key prevents duplicates.
"""

import logging
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import Notification, Payment, Subscription, User
from bot.services.billing import (
    PeriodUnit,
    first_charge_after,
    local_today,
    min_period_days,
    reminders_for,
)

log = logging.getLogger(__name__)

KIND_CHARGED = "charged"  # auto-renewed today
KIND_DUE = "due"  # auto-renew is off: ask whether it was paid
# A long outage must not turn into an endless loop or a flood of messages
MAX_CATCH_UP = 500
# Pending informational messages older than this are no longer worth sending
PENDING_TTL = timedelta(days=1)


def charge_moment(charge: date, remind_time: time, tz: str) -> datetime:
    return datetime.combine(charge, remind_time, tzinfo=ZoneInfo(tz))


def effective_offsets(sub: Subscription, user: User) -> list[int]:
    return sub.remind_offsets if sub.remind_offsets is not None else user.default_offsets


async def _ensure(
    session: AsyncSession, sub: Subscription, charge: date, kind: str, status: str, now: datetime
) -> None:
    exists = await session.scalar(
        select(Notification.id).where(
            Notification.subscription_id == sub.id,
            Notification.charge_date == charge,
            Notification.kind == kind,
        )
    )
    if exists is None:
        session.add(
            Notification(
                subscription_id=sub.id, charge_date=charge, kind=kind, status=status, created_at=now
            )
        )
        await session.flush()


async def record_payment(session: AsyncSession, sub: Subscription, charge: date) -> None:
    exists = await session.scalar(
        select(Payment.id).where(Payment.subscription_id == sub.id, Payment.charge_date == charge)
    )
    if exists is None:
        session.add(
            Payment(
                subscription_id=sub.id,
                charge_date=charge,
                amount_minor=sub.amount_minor,
                currency=sub.currency,
            )
        )
        await session.flush()


def advance(sub: Subscription, charge: date) -> None:
    sub.next_charge_date = first_charge_after(
        sub.anchor_date, PeriodUnit(sub.period_unit), sub.period_count, charge
    )


# Manual payers may pay a bit ahead of the due date; further out a tap is more likely a mistake
PAY_EARLY_DAYS = 7


def can_mark_paid(sub: Subscription, today: date) -> bool:
    return (
        sub.status == "active"
        and not sub.auto_renew
        and (sub.next_charge_date - today).days <= PAY_EARLY_DAYS
    )


def is_overdue(sub: Subscription, today: date) -> bool:
    return sub.status == "active" and not sub.auto_renew and sub.next_charge_date < today


async def mark_paid(session: AsyncSession, sub: Subscription, charge: date) -> bool:
    """Records a manual payment for ``charge``. False if that charge was already settled."""
    if sub.next_charge_date != charge:
        return False
    await record_payment(session, sub, charge)
    advance(sub, charge)
    return True


async def _plan_subscription(
    session: AsyncSession, sub: Subscription, user: User, now: datetime
) -> None:
    tz = user.timezone or "UTC"
    today = local_today(now, tz)
    offsets = effective_offsets(sub, user)

    for _ in range(MAX_CATCH_UP):
        charge = sub.next_charge_date
        if now < charge_moment(charge, user.remind_time, tz):
            break
        # The charge moment has come: earlier "N days before" reminders are pointless now
        for offset in offsets:
            if offset > 0:
                await _ensure(session, sub, charge, f"before_{offset}", "skipped", now)
        if not sub.auto_renew:
            # Needs the user's answer, so it is sent even if the bot was down that day
            await _ensure(session, sub, charge, KIND_DUE, "pending", now)
            return
        await record_payment(session, sub, charge)
        if 0 in offsets:
            # Charges missed during a long outage are recorded silently
            status = "skipped" if charge < today else "pending"
            await _ensure(session, sub, charge, KIND_CHARGED, status, now)
        advance(sub, charge)
    else:
        log.warning("Subscription %s: catch-up limit reached", sub.id)

    charge = sub.next_charge_date
    # A reminder must fall after the previous charge: "3 days before" means nothing for a
    # daily plan and would fire every day
    period = min_period_days(PeriodUnit(sub.period_unit), sub.period_count)
    before = [o for o in offsets if 0 < o < period]
    due = [r for r in reminders_for(charge, before, user.remind_time, tz) if r.fire_at <= now]
    if due:
        # After downtime only the most recent reminder is useful; the rest are skipped
        for reminder in due[:-1]:
            await _ensure(session, sub, charge, reminder.kind, "skipped", now)
        await _ensure(session, sub, charge, due[-1].kind, "pending", now)


async def plan(session: AsyncSession, now: datetime) -> None:
    subs = list(
        await session.scalars(
            select(Subscription)
            .where(Subscription.status == "active")
            .options(selectinload(Subscription.user))
        )
    )
    for sub in subs:
        # A savepoint per subscription: one broken record must not block everyone's reminders
        try:
            async with session.begin_nested():
                await _plan_subscription(session, sub, sub.user, now)
        except Exception:
            log.exception("Planning failed for subscription %s", sub.id)


async def due_for_delivery(session: AsyncSession, now: datetime) -> list[Notification]:
    """Pending messages plus snoozed ones whose time has come."""
    rows = await session.scalars(
        select(Notification)
        .where((Notification.status == "pending") | (Notification.snooze_until <= now))
        .options(selectinload(Notification.subscription).selectinload(Subscription.user))
        .order_by(Notification.id)
    )
    return list(rows)


def is_still_relevant(n: Notification, now: datetime) -> bool:
    sub = n.subscription
    if sub.status == "archived":
        return False
    if n.kind.startswith("before_"):
        # The subscription was edited or already renewed: the reminder refers to a stale date
        return sub.next_charge_date == n.charge_date and now < charge_moment(
            n.charge_date, sub.user.remind_time, sub.user.timezone or "UTC"
        )
    if n.kind == KIND_DUE:
        return sub.next_charge_date == n.charge_date
    return now - as_utc(n.created_at) < PENDING_TTL


def as_utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes; they were written as UTC."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def can_snooze(charge: date, today: date) -> bool:
    # Tomorrow's reminder must still come before the charge, or snoozing is pointless
    return (charge - today).days >= 2


def snooze_target(user: User, now: datetime) -> datetime:
    tz = user.timezone or "UTC"
    tomorrow = local_today(now, tz) + timedelta(days=1)
    # SQLite drops tzinfo on write, so every stored moment must be UTC
    return datetime.combine(tomorrow, user.remind_time, tzinfo=ZoneInfo(tz)).astimezone(UTC)
