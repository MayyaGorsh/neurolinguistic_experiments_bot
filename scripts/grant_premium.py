"""активировать или продлить премиум-статус пользователя на N дней.

если у пользователя уже активен премиум, продление прибавляет дни к
существующей дате (а не от текущего момента). если премиум истёк или
не выдавался - отсчёт идёт от now.

запуск: из папки bot_claude/
    python -m scripts.grant_premium <telegram_id> [days]
по умолчанию days=30 (один месяц).

обратное действие - сброс премиума:
    python -m scripts.grant_premium <telegram_id> 0
выставит premium_until в прошлое (now - 1 секунда), что эквивалентно
отсутствию премиума и закрывает доступ к гейтам.
"""

import asyncio
import sys
from datetime import datetime, timedelta

from db.connection import users_col


async def grant(telegram_id: int, days: int = 30):
    user = await users_col.find_one({"telegram_id": telegram_id})
    if not user:
        print(f"пользователь telegram_id={telegram_id} не найден")
        return

    now = datetime.utcnow()
    if days <= 0:
        new_until = now - timedelta(seconds=1)
    else:
        current = user.get("premium_until")
        base = current if current and current > now else now
        new_until = base + timedelta(days=days)

    await users_col.update_one(
        {"telegram_id": telegram_id},
        {"$set": {"premium_until": new_until}},
    )
    label = "сброшен" if days <= 0 else f"продлён до {new_until.isoformat()}"
    print(f"премиум для telegram_id={telegram_id}: {label}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m scripts.grant_premium <telegram_id> [days]")
        sys.exit(1)
    tid = int(sys.argv[1])
    d = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    asyncio.run(grant(tid, d))
