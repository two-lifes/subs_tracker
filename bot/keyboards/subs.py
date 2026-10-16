from datetime import date

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.models import Subscription
from bot.services.billing import PeriodUnit
from bot.services.notifications import can_mark_paid
from bot.views import sub_button_text


class SubCb(CallbackData, prefix="sub"):
    action: str  # open / edit / renew / archive / restore / delete / delete_yes / history / list
    id: int = 0


class EditCb(CallbackData, prefix="sed"):
    field: str
    id: int


class PeriodCb(CallbackData, prefix="per"):
    unit: str
    count: int = 1  # 0 means "ask for a custom count"


EDIT_FIELDS = {
    "name": "Название",
    "amount": "Цена",
    "currency": "Валюта",
    "period": "Период",
    "paid_on": "Дата оплаты",
    "offsets": "Напоминания",
    "category": "Категория",
    "note": "Заметка",
}


def list_keyboard(
    subs: list[Subscription], today: date, archived: bool = False
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for sub in subs:
        text = sub.name if archived else sub_button_text(sub, today)
        builder.button(text=text, callback_data=SubCb(action="open", id=sub.id))
    builder.adjust(1)
    return builder.as_markup()


def card_keyboard(sub: Subscription, today: date) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    rows = []
    if can_mark_paid(sub, today):
        builder.button(
            text=f"✅ Оплатил ({sub.next_charge_date:%d.%m})",
            callback_data=SubCb(action="paid", id=sub.id),
        )
        builder.button(text="🗄 Не продлеваю", callback_data=SubCb(action="archive", id=sub.id))
        rows.append(2)
    if sub.status == "archived":
        builder.button(text="♻️ Восстановить", callback_data=SubCb(action="restore", id=sub.id))
        rows.append(1)
    else:
        builder.button(text="✏️ Изменить", callback_data=SubCb(action="edit", id=sub.id))
        autopay = "💳 Автоплатёж: вкл" if sub.auto_renew else "✋ Автоплатёж: выкл"
        builder.button(text=autopay, callback_data=SubCb(action="renew", id=sub.id))
        builder.button(text="🗄 В архив", callback_data=SubCb(action="archive", id=sub.id))
        rows += [1, 2]
    builder.button(text="🧾 История", callback_data=SubCb(action="history", id=sub.id))
    builder.button(text="🗑 Удалить", callback_data=SubCb(action="delete", id=sub.id))
    builder.button(text="« К списку", callback_data=SubCb(action="list"))
    builder.adjust(*rows, 2, 1)
    return builder.as_markup()


def offsets_keyboard(sub_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Как по умолчанию", callback_data=f"offs:default:{sub_id}")
    return builder.as_markup()


def delete_confirm_keyboard(sub: Subscription) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Да, удалить", callback_data=SubCb(action="delete_yes", id=sub.id))
    builder.button(text="Отмена", callback_data=SubCb(action="open", id=sub.id))
    return builder.as_markup()


def edit_fields_keyboard(sub: Subscription) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for field, label in EDIT_FIELDS.items():
        builder.button(text=label, callback_data=EditCb(field=field, id=sub.id))
    builder.button(text="« Назад", callback_data=SubCb(action="open", id=sub.id))
    builder.adjust(2)
    return builder.as_markup()


def period_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Месяц", callback_data=PeriodCb(unit=PeriodUnit.MONTH))
    builder.button(text="Год", callback_data=PeriodCb(unit=PeriodUnit.YEAR))
    builder.button(text="Неделя", callback_data=PeriodCb(unit=PeriodUnit.WEEK))
    builder.button(text="Каждые N месяцев", callback_data=PeriodCb(unit=PeriodUnit.MONTH, count=0))
    builder.button(text="Каждые N дней", callback_data=PeriodCb(unit=PeriodUnit.DAY, count=0))
    builder.adjust(3, 1, 1)
    return builder.as_markup()


def currency_choice_keyboard(codes: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for code in codes:
        builder.button(text=code, callback_data=f"acur:{code}")
    builder.adjust(3)
    return builder.as_markup()


def paymode_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Автоматически (с карты)", callback_data="pay:auto")
    builder.button(text="✋ Вручную — спрашивать", callback_data="pay:manual")
    builder.adjust(1)
    return builder.as_markup()


def paid_on_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Сегодня", callback_data="paid:0")
    builder.button(text="Вчера", callback_data="paid:1")
    return builder.as_markup()


def confirm_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Сохранить", callback_data="add:save")
    builder.button(text="Отмена", callback_data="add:cancel")
    return builder.as_markup()
