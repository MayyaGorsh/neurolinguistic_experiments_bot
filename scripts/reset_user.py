"""удалить пользователя @mayyagorsh из бота для повторного теста онбординга.

запуск: из папки bot_claude/ → python -m scripts.reset_user
"""

import asyncio

from db.connection import users_col, sessions_col

USERNAME = "mayyagorsh"


async def main():
    user = await users_col.find_one({"username": USERNAME})
    if not user:
        print(f"пользователь @{USERNAME} не найден")
        return

    tg_id = user.get("telegram_id")
    print(f"найден: telegram_id={tg_id}, role={user.get('role')}")

    res = await users_col.delete_one({"_id": user["_id"]})
    print(f"удалено из users: {res.deleted_count}")

    if tg_id is not None:
        sres = await sessions_col.delete_many({"telegram_id": tg_id})
        print(f"удалено сессий: {sres.deleted_count}")


if __name__ == "__main__":
    asyncio.run(main())
