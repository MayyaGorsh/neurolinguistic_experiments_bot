"""общие UI-хелперы для интерфейса исследователя."""

from aiogram import types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext


async def render_screen(target, text: str, kb=None, state: FSMContext | None = None):
    """показать экран в едином стиле SPA.

    - если target — CallbackQuery, редактируем то же сообщение, в котором
      была нажата кнопка. главное меню «превращается» в подэкран и обратно
      без спама.
    - если target — Message (вход через /start, после ввода текста или
      загрузки файла), отправляем новое сообщение.
    - на любую ошибку редактирования (контент не изменился, сообщение
      слишком старое, это медиа-сообщение) делаем фолбэк через .answer.

    если передан state, после успешного рендера записываем
    active_menu_msg_id — id того сообщения, что сейчас на экране.
    StaleMenuGuard middleware использует это, чтобы блокировать клики
    по более старым меню.
    """
    active_id: int | None = None
    if isinstance(target, types.CallbackQuery):
        msg = target.message
        try:
            await msg.edit_text(text, reply_markup=kb)
            active_id = msg.message_id
        except TelegramBadRequest:
            sent = await msg.answer(text, reply_markup=kb)
            active_id = sent.message_id
    else:
        sent = await target.answer(text, reply_markup=kb)
        active_id = sent.message_id

    if state is not None and active_id is not None:
        await state.update_data(active_menu_msg_id=active_id)
