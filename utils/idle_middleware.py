"""middleware для participant.router: проверяет idle-таймаут на каждом
действии участника (callback или message). если истекло — поглощает
событие и помечает сессию abandoned. иначе обновляет last_activity_at."""

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from db import repositories as repo
from utils.idle_guard import check_and_abandon_if_idle, touch_session


class ParticipantIdleGuard(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, (CallbackQuery, Message)):
            return await handler(event, data)
        user = event.from_user
        if user is None:
            return await handler(event, data)
        session = await repo.get_latest_active_session(user.id)
        if not session:
            return await handler(event, data)
        experiment = await repo.get_experiment(session["experiment_id"])
        if not experiment:
            return await handler(event, data)
        bot = data.get("bot") or getattr(event, "bot", None)
        if await check_and_abandon_if_idle(session, experiment, bot, user.id):
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer()
                except Exception:
                    pass
            return  # событие поглощено
        await touch_session(str(session["_id"]))
        return await handler(event, data)
