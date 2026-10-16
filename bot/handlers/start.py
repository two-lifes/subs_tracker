from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.models import User
from bot.handlers.menu import menu_router
from bot.keyboards.menu import BTN_SETTINGS, currency_keyboard, main_menu, timezone_keyboard
from bot.services.dates import parse_offsets, parse_time
from bot.services.money import parse_currency
from bot.services.tz import parse_timezone
from bot.views import format_offsets

router = Router(name="start")


class Setup(StatesGroup):
    timezone = State()
    currency = State()
    offsets = State()
    remind_time = State()


TZ_PROMPT = (
    "Выберите часовой пояс или пришлите его текстом: "
    "<code>Europe/Berlin</code> или смещение <code>UTC+3</code>."
)
CURRENCY_PROMPT = (
    "Выберите основную валюту — в ней будут итоги расходов. "
    "Или пришлите трёхбуквенный код (например, <code>PLN</code>)."
)


def settings_text(user: User) -> str:
    return (
        "<b>Настройки</b>\n"
        f"Часовой пояс: {user.timezone}\n"
        f"Основная валюта: {user.base_currency}\n"
        f"Напоминания по умолчанию: {format_offsets(user.default_offsets)}, "
        f"в {user.remind_time:%H:%M}"
    )


def _settings_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="Часовой пояс", callback_data="settings:tz")
    builder.button(text="Валюта", callback_data="settings:currency")
    builder.button(text="Дни напоминаний", callback_data="settings:offsets")
    builder.button(text="Время напоминаний", callback_data="settings:time")
    builder.adjust(2)
    return builder.as_markup()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, user: User) -> None:
    await state.clear()
    if user.timezone is None:
        await message.answer(
            "Привет! Я буду следить за вашими подписками и напоминать о списаниях."
        )
        await state.set_state(Setup.timezone)
        await message.answer(TZ_PROMPT, reply_markup=timezone_keyboard())
        return
    if user.base_currency is None:
        await state.set_state(Setup.currency)
        await message.answer(CURRENCY_PROMPT, reply_markup=currency_keyboard("cur"))
        return
    await message.answer("Главное меню.", reply_markup=main_menu())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.", reply_markup=main_menu())


@menu_router.message(Command("settings"))
@menu_router.message(F.text == BTN_SETTINGS)
async def cmd_settings(message: Message, user: User, state: FSMContext) -> None:
    await state.clear()
    await message.answer(settings_text(user), reply_markup=_settings_keyboard())


@router.callback_query(F.data == "settings:tz")
async def settings_tz(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Setup.timezone)
    await callback.message.answer(TZ_PROMPT, reply_markup=timezone_keyboard())
    await callback.answer()


@router.callback_query(F.data == "settings:currency")
async def settings_currency(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Setup.currency)
    await callback.message.answer(CURRENCY_PROMPT, reply_markup=currency_keyboard("cur"))
    await callback.answer()


async def _save_timezone(message: Message, state: FSMContext, user: User, tz: str) -> None:
    user.timezone = tz
    if user.base_currency is None:
        await state.set_state(Setup.currency)
        await message.answer(f"Часовой пояс: {tz}.\n\n{CURRENCY_PROMPT}",
                             reply_markup=currency_keyboard("cur"))
        return
    await state.clear()
    await message.answer(f"Часовой пояс: {tz}.", reply_markup=main_menu())


async def _save_currency(message: Message, state: FSMContext, user: User, code: str) -> None:
    user.base_currency = code
    await state.clear()
    await message.answer(f"Основная валюта: {code}.\n\n{settings_text(user)}",
                         reply_markup=main_menu())


@router.callback_query(Setup.timezone, F.data.startswith("tz:"))
async def pick_timezone(callback: CallbackQuery, state: FSMContext, user: User) -> None:
    await callback.answer()
    await _save_timezone(callback.message, state, user, callback.data.removeprefix("tz:"))


@router.message(Setup.timezone, F.text)
async def type_timezone(message: Message, state: FSMContext, user: User) -> None:
    tz = parse_timezone(message.text)
    if tz is None:
        await message.answer("Не узнал часовой пояс. " + TZ_PROMPT)
        return
    await _save_timezone(message, state, user, tz)


@router.callback_query(Setup.currency, F.data.startswith("cur:"))
async def pick_currency(callback: CallbackQuery, state: FSMContext, user: User) -> None:
    await callback.answer()
    await _save_currency(callback.message, state, user, callback.data.removeprefix("cur:"))


@router.message(Setup.currency, F.text)
async def type_currency(message: Message, state: FSMContext, user: User) -> None:
    code = parse_currency(message.text)
    if code is None:
        await message.answer("Нужен трёхбуквенный код валюты, например USD.")
        return
    await _save_currency(message, state, user, code)


@router.callback_query(F.data == "settings:offsets")
async def settings_offsets(callback: CallbackQuery, state: FSMContext, user: User) -> None:
    await state.set_state(Setup.offsets)
    current = ", ".join(str(d) for d in user.default_offsets) or "-"
    await callback.message.answer(
        f"Сейчас: <code>{current}</code> — {format_offsets(user.default_offsets)}.\n\n"
        "За сколько дней до списания напоминать? Например <code>3, 0</code> "
        "(0 — в день списания), <code>-</code> — не напоминать.\n"
        "Касается подписок без своих настроек напоминаний.\n/cancel — отмена"
    )
    await callback.answer()


@router.message(Setup.offsets, F.text)
async def type_offsets(message: Message, state: FSMContext, user: User) -> None:
    offsets = parse_offsets(message.text)
    if offsets is None:
        await message.answer("Пример: <code>3, 0</code>. Дни от 0 до 60, не больше пяти.")
        return
    user.default_offsets = offsets
    await state.clear()
    await message.answer(settings_text(user), reply_markup=_settings_keyboard())


@router.callback_query(F.data == "settings:time")
async def settings_time(callback: CallbackQuery, state: FSMContext, user: User) -> None:
    await state.set_state(Setup.remind_time)
    await callback.message.answer(
        f"Сейчас: <code>{user.remind_time:%H:%M}</code>.\n\n"
        "Во сколько присылать напоминания? Например <code>10:00</code>.\n/cancel — отмена"
    )
    await callback.answer()


@router.message(Setup.remind_time, F.text)
async def type_time(message: Message, state: FSMContext, user: User) -> None:
    value = parse_time(message.text)
    if value is None:
        await message.answer("Нужно время в формате ЧЧ:ММ, например 09:30.")
        return
    user.remind_time = value
    await state.clear()
    await message.answer(settings_text(user), reply_markup=_settings_keyboard())
