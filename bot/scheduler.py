"""Background loop: plans reminders/renewals and delivers them to Telegram."""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.clock import utcnow
from bot.keyboards.notify import notification_keyboard
from bot.services.billing import local_today
from bot.services.notifications import (
    KIND_DUE,
    can_snooze,
    due_for_delivery,
    is_still_relevant,
    plan,
    snooze_target,
)
from bot.views import notification_text

log = logging.getLogger(__name__)

TICK_SECONDS = 60


async def deliver(session_factory: async_sessionmaker, bot: Bot, now: datetime) -> None:
    async with session_factory() as session:
        notifications = await due_for_delivery(session, now)
        for n in notifications:
            sub = n.subscription
            snoozed = n.status == "sent"
            n.snooze_until = None
            try:
                relevant = is_still_relevant(n, now)
                if relevant:
                    today = local_today(now, sub.user.timezone or "UTC")
                    text = notification_text(n.kind, n.charge_date, sub, today)
                    markup = notification_keyboard(
                        n.kind, n.id, sub.auto_renew, can_snooze(n.charge_date, today)
                    )
            except Exception:
                # A bug in one notification must not block the rest of the queue forever
                log.exception("Cannot render notification %s; dropping it", n.id)
                n.status = "skipped"
                await session.commit()
                continue
            if not relevant:
                if not snoozed:
                    n.status = "skipped"
                await session.commit()
                continue
            try:
                await bot.send_message(sub.user.tg_id, text, reply_markup=markup)
            except (TelegramForbiddenError, TelegramBadRequest):
                # Blocked bot or a malformed message: retrying would fail forever
                log.exception("Dropping notification %s", n.id)
                n.status = "skipped"
            except (TelegramNetworkError, TelegramRetryAfter, TelegramServerError) as exc:
                # Leave it pending (or re-arm the snooze) and try again next tick
                log.warning("Telegram unavailable (%s); will retry", exc)
                await session.rollback()
                return
            else:
                n.status = "sent"
                n.sent_at = now
                if n.kind == KIND_DUE:
                    # Keep asking daily until the user answers; is_still_relevant stops it
                    n.snooze_until = snooze_target(sub.user, now)
            await session.commit()


async def tick(
    session_factory: async_sessionmaker, bot: Bot, now: datetime, db_lock: asyncio.Lock
) -> None:
    # Same lock as update handlers: no lost updates or "database is locked" between them
    async with db_lock:
        async with session_factory() as session, session.begin():
            await plan(session, now)
        await deliver(session_factory, bot, now)


async def run_scheduler(
    session_factory: async_sessionmaker,
    bot: Bot,
    db_lock: asyncio.Lock,
    clock: Callable[[], datetime] = utcnow,
    interval: float = TICK_SECONDS,
) -> None:
    while True:
        try:
            await tick(session_factory, bot, clock(), db_lock)
        except asyncio.CancelledError:
            raise
        except Exception:
            # One bad tick must not stop reminders for good
            log.exception("Scheduler tick failed")
        await asyncio.sleep(interval)
