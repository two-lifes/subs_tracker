from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards.menu import main_menu

router = Router(name="fallback")


@router.callback_query()
async def stale_button(callback: CallbackQuery) -> None:
    # Buttons from finished or abandoned dialogs; answer so Telegram stops the spinner
    await callback.answer("Эта кнопка уже неактуальна.")


@router.message()
async def unknown_message(message: Message, state: FSMContext) -> None:
    # Never stay silent: e.g. text where a button is expected, a sticker, or a reply to a
    # dialog step that was forgotten after a restart
    if await state.get_state() is not None:
        await message.answer(
            "Не понял ответ. Нажмите кнопку в сообщении выше или /cancel — отменить."
        )
        return
    await message.answer("Не понял. Выберите действие в меню.", reply_markup=main_menu())
