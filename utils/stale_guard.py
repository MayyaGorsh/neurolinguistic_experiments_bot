"""middleware: блокирует клики по устаревшим меню исследователя.

как работает:
- в FSM-state каждого пользователя хранится active_menu_msg_id —
  id последнего отрендеренного экрана (через utils.ui.render_screen).
- этот middleware ловит callback_query, сверяет message_id с active.
- если они не совпадают, callback не доходит до хендлера, а пользователь
  получает alert «меню устарело».

если active_menu_msg_id ещё не установлен (после рестарта бота, после
state.clear() или до первого render_screen) — пропускаем клик как есть,
чтобы не блокировать легитимные сценарии.
"""

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, TelegramObject

logger = logging.getLogger("bot")


class StaleMenuGuard(BaseMiddleware):
    """отсекает callback'и со старых меню исследователя."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery):
            state: FSMContext | None = data.get("state")
            if state is not None and event.message is not None:
                stored = await state.get_data()
                active_id = stored.get("active_menu_msg_id")
                if active_id is not None and event.message.message_id != active_id:
                    logger.debug(
                        "stale callback от %s: msg=%s, active=%s, data=%s",
                        event.from_user.id, event.message.message_id,
                        active_id, event.data,
                    )
                    await event.answer(
                        "Это меню устарело — нажмите /start, "
                        "чтобы открыть актуальное.",
                        show_alert=True,
                    )
                    return
        return await handler(event, data)
