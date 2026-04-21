"""
общие хендлеры: fallback, отмена, обработка неподдерживаемых сообщений.
"""

import logging

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

router = Router()
logger = logging.getLogger("bot")


@router.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    """отмена текущего действия и сброс FSM"""
    current = await state.get_state()
    if current:
        await state.clear()
        await message.answer("Действие отменено.")
    else:
        await message.answer("Нечего отменять.")
    await message.answer("Отправьте /start, чтобы вернуться в меню.")


@router.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "<b>Команды бота:</b>\n\n"
        "/start — главное меню (для исследователя)\n"
        "/cancel — отменить текущее действие\n"
        "/help — эта справка\n\n"
        "Респонденты переходят по ссылке на эксперимент."
    )


# обработка стикеров, анимаций и прочего
@router.message(F.sticker)
async def on_sticker(message: types.Message):
    await message.answer("Стикеры не поддерживаются. Используйте текст или кнопки.")


@router.message(F.animation)
async def on_animation(message: types.Message):
    await message.answer("GIF не поддерживаются.")


@router.message(F.contact)
async def on_contact(message: types.Message):
    await message.answer("Контакты не поддерживаются.")


@router.message(F.location)
async def on_location(message: types.Message):
    await message.answer("Геолокация не поддерживается.")


@router.message()
async def fallback(message: types.Message):
    """обработка любых неизвестных сообщений"""
    logger.debug("неизвестное сообщение от %s: %s", message.from_user.id, message.text)
    await message.answer(
        "Я не понимаю эту команду.\n"
        "Отправьте /start, чтобы открыть меню, "
        "или /cancel, чтобы отменить текущее действие."
    )
