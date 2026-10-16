from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from bot.services.money import COMMON_CURRENCIES

POPULAR_TIMEZONES = [
    "Europe/Moscow",
    "Europe/Kyiv",
    "Europe/Minsk",
    "Asia/Almaty",
    "Europe/Berlin",
    "UTC",
]


BTN_ADD = "➕ Добавить"
BTN_LIST = "📋 Подписки"
BTN_ARCHIVE = "🗄 Архив"
BTN_SETTINGS = "⚙️ Настройки"


def main_menu() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    for text in (BTN_ADD, BTN_LIST, BTN_ARCHIVE, BTN_SETTINGS):
        builder.button(text=text)
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def timezone_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for tz in POPULAR_TIMEZONES:
        builder.button(text=tz, callback_data=f"tz:{tz}")
    builder.adjust(2)
    return builder.as_markup()


def currency_keyboard(prefix: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for code in COMMON_CURRENCIES:
        builder.button(text=code, callback_data=f"{prefix}:{code}")
    builder.adjust(3)
    return builder.as_markup()
