from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.services.notifications import KIND_CHARGED, KIND_DUE


class NotifCb(CallbackData, prefix="ntf"):
    # cancel / snooze / open / price / nocharge / paid / stop
    action: str
    id: int  # notification id


def notification_keyboard(
    kind: str, notification_id: int, auto_renew: bool, snooze: bool
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    def add(text: str, action: str) -> None:
        builder.button(text=text, callback_data=NotifCb(action=action, id=notification_id))

    if kind == KIND_CHARGED:
        add("✏️ Цена изменилась", "price")
        add("🚫 Не списалось, отменил", "nocharge")
    elif kind == KIND_DUE:
        add("✅ Оплатил", "paid")
        add("🗄 Не продлеваю", "stop")
    else:
        if not auto_renew:
            add("✅ Уже оплатил", "paid")
        add("🗄 Отменил подписку", "cancel")
        if snooze:
            add("⏰ Напомнить завтра", "snooze")
    add("Открыть карточку", "open")
    builder.adjust(1)
    return builder.as_markup()
