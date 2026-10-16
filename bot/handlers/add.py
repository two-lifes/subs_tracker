from datetime import date, timedelta
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.clock import user_today
from bot.db.models import User
from bot.handlers.common import drop_buttons
from bot.handlers.menu import menu_router
from bot.keyboards.menu import BTN_ADD, main_menu
from bot.keyboards.subs import (
    PeriodCb,
    card_keyboard,
    confirm_keyboard,
    currency_choice_keyboard,
    paid_on_keyboard,
    paymode_keyboard,
    period_keyboard,
)
from bot.services.billing import PeriodUnit, next_charge_from_today
from bot.services.dates import parse_past_date, relative_days
from bot.services.money import (
    COMMON_CURRENCIES,
    amount_example,
    exponent,
    format_amount,
    parse_amount,
    parse_currency,
    split_price,
)
from bot.services.subscriptions import Draft, create_subscription, recent_currencies
from bot.views import format_period, sub_card

router = Router(name="add")

MAX_NAME = 100
MAX_PERIOD_COUNT = 120


class AddSub(StatesGroup):
    name = State()
    amount = State()  # amount and currency, in either order
    period = State()
    period_count = State()
    paymode = State()
    paid_on = State()
    confirm = State()


AMOUNT_PROMPT = (
    "Сколько стоит? Пришлите сумму, например <code>{example}</code>, "
    "и выберите валюту кнопкой — в любом порядке.\n"
    "<i>Другую валюту можно прислать кодом, например PLN.</i>"
)
CURRENCY_PROMPT = "В какой валюте?"
PAID_ON_PROMPT = (
    "Когда была последняя оплата? Нажмите кнопку или пришлите дату "
    "<code>ДД.ММ</code> / <code>ДД.ММ.ГГГГ</code>."
)


@menu_router.message(Command("add"))
@menu_router.message(F.text == BTN_ADD)
async def start_add(message: Message, state: FSMContext, user: User) -> None:
    if user.timezone is None or user.base_currency is None:
        await message.answer("Сначала завершите настройку: /start")
        return
    await state.clear()
    await state.set_state(AddSub.name)
    await message.answer("Как называется подписка? (/cancel — отмена)")


@router.message(AddSub.name, F.text)
async def got_name(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    name = message.text.strip()
    if not name or len(name) > MAX_NAME or name.startswith("/"):
        await message.answer(f"Название должно быть от 1 до {MAX_NAME} символов.")
        return
    await state.update_data(name=name)
    await state.set_state(AddSub.amount)
    # Currency isn't chosen yet, so the example follows the user's main currency
    await message.answer(
        AMOUNT_PROMPT.format(example=amount_example(user.base_currency)),
        reply_markup=currency_choice_keyboard(await _currency_options(session, user)),
    )


async def _currency_options(session: AsyncSession, user: User) -> list[str]:
    codes = [user.base_currency, *await recent_currencies(session, user), *COMMON_CURRENCIES]
    return list(dict.fromkeys(c for c in codes if c))[:6]


async def _try_complete_price(message: Message, state: FSMContext) -> None:
    """Moves on once both the amount and the currency are known, in whichever order they came."""
    data = await state.get_data()
    amount_text, currency = data.get("amount_text"), data.get("currency")
    if amount_text is None:
        await message.answer(
            f"Валюта: {currency}. Теперь пришлите сумму, "
            f"например <code>{amount_example(currency)}</code>."
        )
        return
    if currency is None:
        return
    amount = parse_amount(amount_text, currency)
    if amount is None:
        await state.update_data(amount_text=None)
        hint = "целым числом" if exponent(currency) == 0 else "не более чем с 2 знаками после точки"
        await message.answer(f"Для {currency} сумма указывается {hint}. Пришлите сумму ещё раз.")
        return
    await state.update_data(amount_minor=amount)
    await state.set_state(AddSub.period)
    await message.answer(
        f"{format_amount(amount, currency)}. Как часто списывается?",
        reply_markup=period_keyboard(),
    )


async def _choose_currency(message: Message, state: FSMContext, code: str) -> None:
    await state.update_data(currency=code)
    await _try_complete_price(message, state)


@router.message(AddSub.amount, F.text)
async def got_amount(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    text = message.text.strip()
    # A bare currency code typed instead of tapping a button
    if not any(ch.isdigit() for ch in text) and (code := parse_currency(text)):
        await _choose_currency(message, state, code)
        return
    split = split_price(text)
    # Validate with 2 decimals for now; re-checked against the real currency's precision later
    if split is None or parse_amount(split[0], split[1] or "USD") is None:
        currency = (await state.get_data()).get("currency") or user.base_currency
        await message.answer(f"Не понял сумму. Пример: <code>{amount_example(currency)}</code>.")
        return
    number, typed_currency = split
    await state.update_data(amount_text=number)
    if typed_currency:
        await state.update_data(currency=typed_currency)
    if (await state.get_data()).get("currency") is None:
        await message.answer(
            CURRENCY_PROMPT,
            reply_markup=currency_choice_keyboard(await _currency_options(session, user)),
        )
        return
    await _try_complete_price(message, state)


@router.callback_query(AddSub.amount, F.data.startswith("acur:"))
async def picked_currency(callback: CallbackQuery, state: FSMContext) -> None:
    code = callback.data.removeprefix("acur:")
    await callback.answer(f"Валюта: {code}")
    await _choose_currency(callback.message, state, code)


@router.callback_query(AddSub.period, PeriodCb.filter())
async def picked_period(
    callback: CallbackQuery, callback_data: PeriodCb, state: FSMContext
) -> None:
    await callback.answer()
    await state.update_data(period_unit=callback_data.unit)
    if callback_data.count == 0:
        await state.set_state(AddSub.period_count)
        word = "месяцев" if callback_data.unit == PeriodUnit.MONTH else "дней"
        await callback.message.answer(f"Раз в сколько {word}? Пришлите число.")
        return
    await state.update_data(period_count=callback_data.count)
    await _ask_paymode(callback.message, state)


@router.message(AddSub.period_count, F.text)
async def got_period_count(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    if not text.isdigit() or not 1 <= int(text) <= MAX_PERIOD_COUNT:
        await message.answer(f"Нужно целое число от 1 до {MAX_PERIOD_COUNT}.")
        return
    await state.update_data(period_count=int(text))
    await _ask_paymode(message, state)


async def _ask_paymode(message: Message, state: FSMContext) -> None:
    await state.set_state(AddSub.paymode)
    await message.answer(
        "Как оплачивается?\n"
        "💳 <b>Автоматически</b> — списывается с карты сама, я просто отмечу платёж.\n"
        "✋ <b>Вручную</b> — в день оплаты спрошу «Оплатили?» и буду напоминать, "
        "пока не ответите.",
        reply_markup=paymode_keyboard(),
    )


@router.callback_query(AddSub.paymode, F.data.startswith("pay:"))
async def picked_paymode(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.update_data(auto_renew=callback.data == "pay:auto")
    await state.set_state(AddSub.paid_on)
    await callback.message.answer(PAID_ON_PROMPT, reply_markup=paid_on_keyboard())


async def _set_paid_on(message: Message, state: FSMContext, user: User, paid_on: date) -> None:
    data = await state.update_data(paid_on=paid_on.isoformat())
    today = user_today(user.timezone)
    next_charge = next_charge_from_today(
        paid_on, PeriodUnit(data["period_unit"]), data["period_count"], today
    )
    await state.set_state(AddSub.confirm)
    await message.answer(
        f"<b>{escape(data['name'])}</b>\n"
        f"{format_amount(data['amount_minor'], data['currency'])} "
        f"{format_period(data['period_unit'], data['period_count'])}\n"
        f"Оплата: {'автоплатёж' if data['auto_renew'] else 'вручную'}\n"
        f"Оплачено: {paid_on:%d.%m.%Y}\n"
        f"Следующая оплата: {next_charge:%d.%m.%Y}, {relative_days(next_charge, today)}",
        reply_markup=confirm_keyboard(),
    )


@router.callback_query(AddSub.paid_on, F.data.startswith("paid:"))
async def picked_paid_on(callback: CallbackQuery, state: FSMContext, user: User) -> None:
    await callback.answer()
    days_ago = int(callback.data.removeprefix("paid:"))
    paid_on = user_today(user.timezone) - timedelta(days=days_ago)
    await _set_paid_on(callback.message, state, user, paid_on)


@router.message(AddSub.paid_on, F.text)
async def typed_paid_on(message: Message, state: FSMContext, user: User) -> None:
    paid_on = parse_past_date(message.text, user_today(user.timezone))
    if paid_on is None:
        await message.answer("Не понял дату (и она не должна быть в будущем). " + PAID_ON_PROMPT)
        return
    await _set_paid_on(message, state, user, paid_on)


@router.callback_query(AddSub.confirm, F.data == "add:save")
async def save(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    data = await state.get_data()
    await state.clear()
    draft = Draft(
        name=data["name"],
        amount_minor=data["amount_minor"],
        currency=data["currency"],
        period_unit=PeriodUnit(data["period_unit"]),
        period_count=data["period_count"],
        paid_on=date.fromisoformat(data["paid_on"]),
        auto_renew=data["auto_renew"],
    )
    today = user_today(user.timezone)
    sub = await create_subscription(session, user, draft, today)
    await callback.answer("Сохранено")
    await drop_buttons(callback)
    await callback.message.answer(
        "✅ Добавлено.\n\n" + sub_card(sub, user, today), reply_markup=card_keyboard(sub, today)
    )
    await callback.message.answer("Одной неожиданностью меньше ✨",reply_markup=main_menu())


@router.callback_query(AddSub.confirm, F.data == "add:cancel")
async def cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await drop_buttons(callback)
    await callback.message.answer("Отменено.", reply_markup=main_menu())
