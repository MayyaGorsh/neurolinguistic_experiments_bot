"""проверка таймаута бездействия участника.

вызывается из двух мест:
- handlers/start.py: при возврате участника по диплинку.
- utils/idle_middleware.py: на каждом действии участника, пока бот открыт.

если сессия истекла — помечаем abandoned и шлём участнику сообщение,
чтобы он понимал, что нужно начать заново."""

from datetime import datetime

from db import repositories as repo


async def check_and_abandon_if_idle(
    session: dict, experiment: dict, bot, telegram_id: int,
) -> bool:
    """True — сессия истекла и помечена abandoned (участнику отправлено
    сообщение). False — сессия активна, можно работать дальше.

    `idle_timeout_seconds == 0` означает «таймаут отключён»: всегда False."""
    idle_limit = int(experiment.get("idle_timeout_seconds", 300) or 0)
    if idle_limit <= 0:
        return False
    last_act = session.get("last_activity_at") or session.get("started_at")
    if not last_act:
        return False
    if (datetime.utcnow() - last_act).total_seconds() <= idle_limit:
        return False
    await repo.update_session(
        str(session["_id"]),
        {"status": "abandoned", "finished_at": datetime.utcnow()},
    )
    try:
        await bot.send_message(
            telegram_id,
            "Прошлая сессия истекла из-за длительного перерыва. "
            "Откройте ссылку на эксперимент ещё раз, чтобы начать заново.",
        )
    except Exception:
        pass
    return True


async def touch_session(session_id: str) -> None:
    """обновить last_activity_at сессии. дёргается на каждом действии
    участника (middleware) и при показе нового стимула ботом (runner),
    чтобы долгое раздумье над пробой не считалось бездействием."""
    await repo.update_session(
        session_id, {"last_activity_at": datetime.utcnow()},
    )
