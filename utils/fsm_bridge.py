"""мост между runner-ом и FSM dispatcher-а.

runner живёт в engine/, не получает FSMContext по аргументам, но иногда
ему надо обновить state пользователя (например, поставить
active_menu_msg_id на сообщение, которое он отправил после завершения
превью). чтобы не пробрасывать FSMContext через цепочку
present_trial → advance_phase → finish_experiment, регистрируем
storage диспатчера один раз на старте и берём его отсюда.
"""

import logging

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import BaseStorage, StorageKey

logger = logging.getLogger("bot")

_storage: BaseStorage | None = None


def register_storage(storage: BaseStorage) -> None:
    global _storage
    _storage = storage


async def update_active_menu(
    bot_id: int, chat_id: int, user_id: int, msg_id: int | None,
) -> None:
    """обновить active_menu_msg_id у пользователя.

    в личных чатах с ботом user_id == chat_id, так что при вызове из
    runner-а можно передавать одно и то же значение в оба поля.
    """
    if _storage is None:
        logger.warning("FSM storage не зарегистрирован — обновление пропущено")
        return
    key = StorageKey(bot_id=bot_id, chat_id=chat_id, user_id=user_id)
    ctx = FSMContext(storage=_storage, key=key)
    await ctx.update_data(active_menu_msg_id=msg_id)
