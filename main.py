import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import BOT_TOKEN
from logger import setup_logger
from handlers import start, researcher, participant, free_form, media_upload, promo, common

logger = setup_logger()


async def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не задан. Проверьте файл .env")
        return

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # подключаем роутеры
    dp.include_router(start.router)
    dp.include_router(researcher.router)
    dp.include_router(free_form.router)
    dp.include_router(media_upload.router)
    dp.include_router(promo.router)
    dp.include_router(participant.router)
    dp.include_router(common.router)  # fallback — последним

    logger.info("бот запущен")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        logger.info("бот остановлен")


if __name__ == "__main__":
    asyncio.run(main())
