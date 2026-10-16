from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.clock import user_today, utcnow
from bot.db.models import Notification, Payment, Subscription, User
from bot.handlers.common import show
from bot.keyboards.notify import NotifCb
from bot.keyboards.subs import card_keyboard
from bot.services.money import amount_example, format_amount, parse_amount, split_price
from bot.services.notifications import can_snooze, mark_paid, snooze_target
from bot.services.subscriptions import archive
from bot.views import sub_card

router = Router(name="notify")


class PriceChange(StatesGroup):
    amount = State()


async def _load(
    session: AsyncSession, user: User, notification_id: int
) -> Notification | None:
    return await session.scalar(
        select(Notification)
        .join(Subscription)
        .where(Notification.id == notification_id, Subscription.user_id == user.id)
        .options(selectinload(Notification.subscription))
    )


async def _close(callback: CallbackQuery, result: str) -> None:
    """Keeps the reminder text, replaces its buttons with the outcome."""
    original = callback.message
    # An InaccessibleMessage has no text to keep, so only the outcome is shown
    text = original.html_text + "\n\n" if isinstance(original, Message) and original.text else ""
    await show(callback, f"{text}<i>{result}</i>")
    await callback.answer()


@router.callback_query(NotifCb.filter())
async def on_action(
    callback: CallbackQuery, callback_data: NotifCb, session: AsyncSession, user: User,
    state: FSMContext,
) -> None:
    n = await _load(session, user, callback_data.id)
    if n is None:
        await callback.answer("Уведомление не найдено", show_alert=True)
        return
    sub = n.subscription
    today = user_today(user.timezone)

    match callback_data.action:
        case "open":
            await callback.message.answer(
                sub_card(sub, user, today), reply_markup=card_keyboard(sub, today)
            )
            await callback.answer()
        case "cancel" | "stop":
            archive(sub)
            await _close(callback, "Подписка перенесена в архив.")
        case "snooze":
            if not can_snooze(n.charge_date, today):
                await callback.answer("Списание уже совсем скоро — откладывать поздно.")
                return
            n.snooze_until = snooze_target(user, utcnow())
            await callback.answer("Напомню завтра")
        case "nocharge":
            await session.execute(
                delete(Payment).where(
                    Payment.subscription_id == sub.id, Payment.charge_date == n.charge_date
                )
            )
            archive(sub)
            await _close(callback, "Платёж не учтён, подписка перенесена в архив.")
        case "paid":
            if not await mark_paid(session, sub, n.charge_date):
                await _close(callback, "Уже отмечено.")
                return
            await _close(
                callback, f"Оплата записана. Следующий срок — {sub.next_charge_date:%d.%m.%Y}."
            )
        case "price":
            await state.set_state(PriceChange.amount)
            await state.update_data(notification_id=n.id)
            await callback.message.answer(
                f"Сколько списали за «{escape(sub.name)}»? Сумма в {sub.currency}, например "
                f"<code>{amount_example(sub.currency)}</code>. "
                "Новая цена будет и в следующих списаниях.\n/cancel — отмена"
            )
            await callback.answer()
        case _:
            await callback.answer()


@router.message(PriceChange.amount, F.text)
async def got_new_price(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    data = await state.get_data()
    n = await _load(session, user, data.get("notification_id", 0))
    if n is None:
        await state.clear()
        await message.answer("Уведомление не найдено.")
        return
    sub = n.subscription
    split = split_price(message.text)
    amount = parse_amount(split[0], sub.currency) if split and not split[1] else None
    if amount is None:
        await message.answer(
            f"Не понял сумму. Пример: <code>{amount_example(sub.currency)}</code> "
            f"({sub.currency})."
        )
        return
    sub.amount_minor = amount
    payment = await session.scalar(
        select(Payment).where(
            Payment.subscription_id == sub.id, Payment.charge_date == n.charge_date
        )
    )
    if payment is not None:
        payment.amount_minor = amount
        payment.currency = sub.currency
    await state.clear()
    await message.answer(
        f"Цена «{escape(sub.name)}» обновлена: {format_amount(amount, sub.currency)}."
    )
