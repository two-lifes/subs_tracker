from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Payment, Subscription, User
from bot.services.billing import PeriodUnit, first_charge_after, next_charge_from_today


@dataclass
class Draft:
    name: str
    amount_minor: int
    currency: str
    period_unit: PeriodUnit
    period_count: int
    paid_on: date
    auto_renew: bool = True


async def create_subscription(
    session: AsyncSession, user: User, draft: Draft, today: date
) -> Subscription:
    sub = Subscription(
        user_id=user.id,
        name=draft.name,
        amount_minor=draft.amount_minor,
        currency=draft.currency,
        period_unit=draft.period_unit.value,
        period_count=draft.period_count,
        anchor_date=draft.paid_on,
        next_charge_date=next_charge_from_today(
            draft.paid_on, draft.period_unit, draft.period_count, today
        ),
        status="active",
        auto_renew=draft.auto_renew,
    )
    session.add(sub)
    await session.flush()
    # The payment the user just told us about is real spending, so it belongs in history
    session.add(
        Payment(
            subscription_id=sub.id,
            charge_date=draft.paid_on,
            amount_minor=draft.amount_minor,
            currency=draft.currency,
        )
    )
    await session.flush()
    return sub


def recompute_next_charge(sub: Subscription, today: date) -> None:
    sub.next_charge_date = next_charge_from_today(
        sub.anchor_date, PeriodUnit(sub.period_unit), sub.period_count, today
    )


async def last_payment_date(session: AsyncSession, sub: Subscription) -> date | None:
    return await session.scalar(
        select(func.max(Payment.charge_date)).where(Payment.subscription_id == sub.id)
    )


async def change_period(
    session: AsyncSession, sub: Subscription, unit: PeriodUnit, count: int, today: date
) -> None:
    # Re-anchor on the latest known payment: the old anchor may not line up with the new period
    sub.anchor_date = await last_payment_date(session, sub) or sub.anchor_date
    sub.period_unit = unit.value
    sub.period_count = count
    recompute_next_charge(sub, today)


async def change_paid_on(
    session: AsyncSession, sub: Subscription, paid_on: date, today: date
) -> None:
    """Corrects the date of the latest payment; history follows so spending stats stay right."""
    payments = await session.scalars(
        select(Payment).where(Payment.subscription_id == sub.id)
    )
    by_date = {p.charge_date: p for p in payments}
    latest = by_date[max(by_date)] if by_date else None
    if latest is None:
        session.add(
            Payment(
                subscription_id=sub.id,
                charge_date=paid_on,
                amount_minor=sub.amount_minor,
                currency=sub.currency,
            )
        )
    elif latest.charge_date != paid_on:
        if paid_on in by_date:
            # That date is already recorded, so the latest entry was the mistaken one
            await session.delete(latest)
        else:
            latest.charge_date = paid_on
    await session.flush()
    sub.anchor_date = paid_on
    recompute_next_charge(sub, today)


def archive(sub: Subscription, now: datetime | None = None) -> None:
    sub.status = "archived"
    sub.archived_at = now or datetime.now(UTC)


def restore(sub: Subscription, today: date) -> None:
    sub.status = "active"
    sub.archived_at = None
    recompute_next_charge(sub, today)


async def list_subscriptions(
    session: AsyncSession, user: User, archived: bool = False
) -> list[Subscription]:
    query = select(Subscription).where(Subscription.user_id == user.id)
    if archived:
        query = query.where(Subscription.status == "archived").order_by(Subscription.name)
    else:
        query = query.where(Subscription.status != "archived").order_by(
            Subscription.next_charge_date, Subscription.name
        )
    return list(await session.scalars(query))


async def get_subscription(session: AsyncSession, user: User, sub_id: int) -> Subscription | None:
    return await session.scalar(
        select(Subscription).where(Subscription.id == sub_id, Subscription.user_id == user.id)
    )


async def list_payments(session: AsyncSession, sub: Subscription, limit: int = 24) -> list[Payment]:
    return list(
        await session.scalars(
            select(Payment)
            .where(Payment.subscription_id == sub.id)
            .order_by(Payment.charge_date.desc())
            .limit(limit)
        )
    )


async def recent_currencies(session: AsyncSession, user: User, limit: int = 4) -> list[str]:
    rows = await session.execute(
        select(Subscription.currency, func.max(Subscription.id).label("last"))
        .where(Subscription.user_id == user.id)
        .group_by(Subscription.currency)
        .order_by(func.max(Subscription.id).desc())
        .limit(limit)
    )
    return [row.currency for row in rows]


def upcoming_charges(sub: Subscription, today: date, until: date) -> list[date]:
    """Charge dates in ``[today, until]``, stepping the same way the scheduler advances."""
    unit = PeriodUnit(sub.period_unit)
    result = []
    charge = sub.next_charge_date
    while charge <= until:
        if charge >= today:
            result.append(charge)
        charge = first_charge_after(sub.anchor_date, unit, sub.period_count, charge)
    return result


async def subscriptions_by_status(
    session: AsyncSession, user_id: int, status: str
) -> list[Subscription]:
    return list(
        await session.scalars(
            select(Subscription)
            .where(Subscription.user_id == user_id, Subscription.status == status)
            .order_by(Subscription.id)
        )
    )


async def payments_between(
    session: AsyncSession, user_id: int, start: date, end: date
) -> list[Payment]:
    """Payments of all the user's subscriptions (archived too), both bounds inclusive."""
    return list(
        await session.scalars(
            select(Payment)
            .join(Subscription)
            .where(
                Subscription.user_id == user_id,
                Payment.charge_date >= start,
                Payment.charge_date <= end,
            )
            .order_by(Payment.charge_date, Payment.id)
        )
    )
