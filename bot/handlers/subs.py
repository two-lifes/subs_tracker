from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.clock import user_today
from bot.db.models import Subscription, User
from bot.handlers.common import show
from bot.handlers.menu import menu_router
from bot.keyboards.menu import BTN_ARCHIVE, BTN_LIST
from bot.keyboards.subs import (
    EDIT_FIELDS,
    EditCb,
    PeriodCb,
    SubCb,
    card_keyboard,
    currency_choice_keyboard,
    delete_confirm_keyboard,
    edit_fields_keyboard,
    list_keyboard,
    offsets_keyboard,
    paid_on_keyboard,
    period_keyboard,
)
from bot.services.billing import PeriodUnit
from bot.services.dates import parse_offsets, parse_past_date
from bot.services.money import (
    COMMON_CURRENCIES,
    amount_example,
    exponent,
    parse_amount,
    parse_currency,
    split_price,
)
from bot.services.notifications import can_mark_paid, is_overdue, mark_paid
from bot.services.subscriptions import (
    archive,
    change_paid_on,
    change_period,
    get_subscription,
    last_payment_date,
    list_payments,
    list_subscriptions,
    restore,
)
from bot.views import current_value, list_text, payments_text, sub_card

router = Router(name="subs")

MAX_TEXT = {"name": 100, "category": 50, "note": 500}
MAX_PERIOD_COUNT = 120


class EditSub(StatesGroup):
    value = State()
    period_count = State()


# ---- list & archive ----


async def _send_list(message: Message, session: AsyncSession, user: User) -> None:
    subs = await list_subscriptions(session, user)
    today = user_today(user.timezone)
    await message.answer(list_text(subs, today), reply_markup=list_keyboard(subs, today))


@menu_router.message(Command("list"))
@menu_router.message(F.text == BTN_LIST)
async def cmd_list(
    message: Message, session: AsyncSession, user: User, state: FSMContext
) -> None:
    await state.clear()
    await _send_list(message, session, user)


@menu_router.message(Command("archive"))
@menu_router.message(F.text == BTN_ARCHIVE)
async def cmd_archive(
    message: Message, session: AsyncSession, user: User, state: FSMContext
) -> None:
    await state.clear()
    subs = await list_subscriptions(session, user, archived=True)
    if not subs:
        await message.answer("Архив пуст.")
        return
    await message.answer(
        "<b>Архив</b> — отменённые подписки:",
        reply_markup=list_keyboard(subs, user_today(user.timezone), archived=True),
    )


# ---- card ----


async def _load(
    callback: CallbackQuery, session: AsyncSession, user: User, sub_id: int
) -> Subscription | None:
    sub = await get_subscription(session, user, sub_id)
    if sub is None:
        await callback.answer("Подписка не найдена", show_alert=True)
    return sub


async def _show_card(callback: CallbackQuery, sub: Subscription, user: User) -> None:
    today = user_today(user.timezone)
    await show(callback, sub_card(sub, user, today), card_keyboard(sub, today))


async def _send_card(message: Message, sub: Subscription, user: User, prefix: str = "") -> None:
    today = user_today(user.timezone)
    await message.answer(
        prefix + sub_card(sub, user, today), reply_markup=card_keyboard(sub, today)
    )


@router.callback_query(SubCb.filter(F.action == "list"))
async def back_to_list(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
    subs = await list_subscriptions(session, user)
    today = user_today(user.timezone)
    await show(callback, list_text(subs, today), list_keyboard(subs, today))
    await callback.answer()


@router.callback_query(SubCb.filter(F.action == "open"))
async def open_card(
    callback: CallbackQuery, callback_data: SubCb, session: AsyncSession, user: User,
    state: FSMContext,
) -> None:
    await state.clear()
    if sub := await _load(callback, session, user, callback_data.id):
        await _show_card(callback, sub, user)
        await callback.answer()


@router.callback_query(SubCb.filter(F.action == "renew"))
async def toggle_renew(
    callback: CallbackQuery, callback_data: SubCb, session: AsyncSession, user: User
) -> None:
    if sub := await _load(callback, session, user, callback_data.id):
        if not sub.auto_renew and is_overdue(sub, user_today(user.timezone)):
            # Otherwise the scheduler would book the unpaid overdue charge as auto-paid
            await callback.answer(
                f"Сначала отметьте срок {sub.next_charge_date:%d.%m}: "
                "«✅ Оплатил» или «🗄 Не продлеваю».",
                show_alert=True,
            )
            return
        sub.auto_renew = not sub.auto_renew
        await _show_card(callback, sub, user)
        await callback.answer(
            "Автоплатёж включён" if sub.auto_renew else "Оплата вручную: буду спрашивать"
        )


@router.callback_query(SubCb.filter(F.action == "paid"))
async def card_paid(
    callback: CallbackQuery, callback_data: SubCb, session: AsyncSession, user: User
) -> None:
    sub = await _load(callback, session, user, callback_data.id)
    if sub is None:
        return
    if not can_mark_paid(sub, user_today(user.timezone)):
        await callback.answer("Сейчас отмечать оплату не нужно.")
        return
    paid_for = sub.next_charge_date
    await mark_paid(session, sub, paid_for)
    await _show_card(callback, sub, user)
    await callback.answer(f"Оплата за {paid_for:%d.%m} записана")


@router.callback_query(SubCb.filter(F.action == "archive"))
async def to_archive(
    callback: CallbackQuery, callback_data: SubCb, session: AsyncSession, user: User
) -> None:
    if sub := await _load(callback, session, user, callback_data.id):
        archive(sub)
        await _show_card(callback, sub, user)
        await callback.answer("Перенесено в архив")


@router.callback_query(SubCb.filter(F.action == "restore"))
async def from_archive(
    callback: CallbackQuery, callback_data: SubCb, session: AsyncSession, user: User
) -> None:
    if sub := await _load(callback, session, user, callback_data.id):
        restore(sub, user_today(user.timezone))
        await _show_card(callback, sub, user)
        await callback.answer("Восстановлено")


@router.callback_query(SubCb.filter(F.action == "delete"))
async def ask_delete(
    callback: CallbackQuery, callback_data: SubCb, session: AsyncSession, user: User
) -> None:
    if sub := await _load(callback, session, user, callback_data.id):
        await show(
            callback,
            f"Удалить «{escape(sub.name)}» вместе с историей платежей? Это необратимо.\n"
            "Если подписку просто отменили — лучше перенести в архив.",
            delete_confirm_keyboard(sub),
        )
        await callback.answer()


@router.callback_query(SubCb.filter(F.action == "delete_yes"))
async def do_delete(
    callback: CallbackQuery, callback_data: SubCb, session: AsyncSession, user: User
) -> None:
    if sub := await _load(callback, session, user, callback_data.id):
        await session.delete(sub)
        await show(callback, f"«{escape(sub.name)}» удалена.")
        await callback.answer()


@router.callback_query(SubCb.filter(F.action == "history"))
async def history(
    callback: CallbackQuery, callback_data: SubCb, session: AsyncSession, user: User
) -> None:
    if sub := await _load(callback, session, user, callback_data.id):
        payments = await list_payments(session, sub)
        await callback.message.answer(payments_text(sub, payments))
        await callback.answer()


# ---- edit ----


@router.callback_query(SubCb.filter(F.action == "edit"))
async def choose_field(
    callback: CallbackQuery, callback_data: SubCb, session: AsyncSession, user: User
) -> None:
    if sub := await _load(callback, session, user, callback_data.id):
        await show(callback, f"Что изменить в «{escape(sub.name)}»?", edit_fields_keyboard(sub))
        await callback.answer()


_PROMPTS = {
    "name": "Новое название:",
    "amount": "Новая цена в {currency}, например <code>{example}</code>:",
    "currency": "Новая валюта — кнопка или код. Сумма в числах останется прежней.",
    "period": "Новый период:",
    "paid_on": "Дата последней оплаты — кнопка или <code>ДД.ММ[.ГГГГ]</code>. "
    "Следующие списания будут считаться от неё.",
    "offsets": "За сколько дней напоминать? Например <code>3, 0</code> "
    "(0 — в день списания), <code>-</code> — не напоминать.",
    "category": "Категория (например «Работа»). <code>-</code> — убрать.",
    "note": "Заметка. <code>-</code> — убрать.",
}


@router.callback_query(EditCb.filter())
async def ask_value(
    callback: CallbackQuery, callback_data: EditCb, session: AsyncSession, user: User,
    state: FSMContext,
) -> None:
    sub = await _load(callback, session, user, callback_data.id)
    if sub is None:
        return
    field = callback_data.field
    if field not in EDIT_FIELDS:
        await callback.answer()
        return
    await state.set_state(EditSub.value)
    await state.update_data(field=field, sub_id=sub.id)
    markup = None
    match field:
        case "currency":
            markup = currency_choice_keyboard(
                list(dict.fromkeys([user.base_currency, *COMMON_CURRENCIES]))
            )
        case "period":
            markup = period_keyboard()
        case "paid_on":
            markup = paid_on_keyboard()
        case "offsets":
            markup = offsets_keyboard(sub.id)
    current = current_value(field, sub, user, await last_payment_date(session, sub))
    prompt = f"Сейчас: {current}.\n\n" + _PROMPTS[field].format(
        currency=sub.currency, example=amount_example(sub.currency)
    )
    await callback.message.answer(prompt + "\n/cancel — отмена", reply_markup=markup)
    await callback.answer()


async def _editing(
    state: FSMContext, session: AsyncSession, user: User
) -> tuple[str, Subscription | None]:
    data = await state.get_data()
    return data.get("field", ""), await get_subscription(session, user, data.get("sub_id", 0))


async def _finish(message: Message, state: FSMContext, sub: Subscription, user: User) -> None:
    await state.clear()
    await _send_card(message, sub, user, prefix="✅ Сохранено.\n\n")


def _convert_amount(amount_minor: int, old: str, new: str) -> int:
    # Keep the same number in major units when the currency's precision differs
    value = Decimal(amount_minor).scaleb(exponent(new) - exponent(old))
    return max(int(value.quantize(Decimal(1), ROUND_HALF_UP)), 1)


@router.message(EditSub.value, F.text)
async def got_value(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    field, sub = await _editing(state, session, user)
    if sub is None:
        await state.clear()
        await message.answer("Подписка не найдена.")
        return
    text = message.text.strip()
    today = user_today(user.timezone)

    match field:
        case "name" | "category" | "note":
            if field != "name" and text == "-":
                setattr(sub, field, None)
            elif not text or len(text) > MAX_TEXT[field]:
                await message.answer(f"Нужно от 1 до {MAX_TEXT[field]} символов.")
                return
            else:
                setattr(sub, field, text)
        case "amount":
            split = split_price(text)
            amount = parse_amount(split[0], sub.currency) if split and not split[1] else None
            if amount is None:
                await message.answer(
                    f"Не понял сумму. Пример: <code>{amount_example(sub.currency)}</code> "
                    f"({sub.currency})."
                )
                return
            sub.amount_minor = amount
        case "currency":
            code = parse_currency(text)
            if code is None:
                await message.answer("Нужен трёхбуквенный код валюты, например USD.")
                return
            sub.amount_minor = _convert_amount(sub.amount_minor, sub.currency, code)
            sub.currency = code
        case "paid_on":
            paid_on = parse_past_date(text, today)
            if paid_on is None:
                await message.answer("Не понял дату (и она не должна быть в будущем).")
                return
            await change_paid_on(session, sub, paid_on, today)
        case "offsets":
            offsets = parse_offsets(text)
            if offsets is None:
                await message.answer("Пример: <code>3, 0</code>. Дни от 0 до 60, не больше пяти.")
                return
            sub.remind_offsets = offsets
        case "period":
            await message.answer("Выберите период кнопкой.", reply_markup=period_keyboard())
            return
        case _:
            await state.clear()
            return
    await _finish(message, state, sub, user)


@router.callback_query(EditSub.value, F.data.startswith("acur:"))
async def edit_currency_button(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    field, sub = await _editing(state, session, user)
    await callback.answer()
    if sub is None or field != "currency":
        return
    code = callback.data.removeprefix("acur:")
    sub.amount_minor = _convert_amount(sub.amount_minor, sub.currency, code)
    sub.currency = code
    await _finish(callback.message, state, sub, user)


@router.callback_query(EditSub.value, F.data.startswith("paid:"))
async def edit_paid_on_button(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    field, sub = await _editing(state, session, user)
    await callback.answer()
    if sub is None or field != "paid_on":
        return
    today = user_today(user.timezone)
    paid_on = today - timedelta(days=int(callback.data.removeprefix("paid:")))
    await change_paid_on(session, sub, paid_on, today)
    await _finish(callback.message, state, sub, user)


@router.callback_query(EditSub.value, F.data.startswith("offs:default:"))
async def edit_offsets_default(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    field, sub = await _editing(state, session, user)
    await callback.answer()
    if sub is None or field != "offsets":
        return
    sub.remind_offsets = None
    await _finish(callback.message, state, sub, user)


@router.callback_query(EditSub.value, PeriodCb.filter())
async def edit_period_button(
    callback: CallbackQuery, callback_data: PeriodCb, state: FSMContext,
    session: AsyncSession, user: User,
) -> None:
    field, sub = await _editing(state, session, user)
    await callback.answer()
    if sub is None or field != "period":
        return
    if callback_data.count == 0:
        await state.update_data(period_unit=callback_data.unit)
        await state.set_state(EditSub.period_count)
        word = "месяцев" if callback_data.unit == PeriodUnit.MONTH else "дней"
        await callback.message.answer(f"Раз в сколько {word}? Пришлите число.")
        return
    await change_period(
        session, sub, PeriodUnit(callback_data.unit), 1, user_today(user.timezone)
    )
    await _finish(callback.message, state, sub, user)


@router.message(EditSub.period_count, F.text)
async def edit_period_count(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    text = message.text.strip()
    if not text.isdigit() or not 1 <= int(text) <= MAX_PERIOD_COUNT:
        await message.answer(f"Нужно целое число от 1 до {MAX_PERIOD_COUNT}.")
        return
    data = await state.get_data()
    _, sub = await _editing(state, session, user)
    if sub is None:
        await state.clear()
        return
    await change_period(
        session, sub, PeriodUnit(data["period_unit"]), int(text), user_today(user.timezone)
    )
    await _finish(message, state, sub, user)
