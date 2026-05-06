import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import BOT_TOKEN
from logger import setup_logger
from handlers import start, researcher, participant, free_form, media_upload, promo, common
from utils.stale_guard import StaleMenuGuard
from utils.idle_middleware import ParticipantIdleGuard
from utils.fsm_bridge import register_storage

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

    # делаем FSM-storage доступным runner-у (для обновления
    # active_menu_msg_id после завершения превью эксперимента)
    register_storage(dp.fsm.storage)

    # вешаем StaleMenuGuard на researcher и promo роутеры — он отсекает
    # клики по «протухшим» меню (см. utils/stale_guard.py).
    # participant и media_upload не покрываем: там семантика «активного»
    # сообщения другая — не по message_id, а по полям сессии.
    researcher.router.callback_query.outer_middleware(StaleMenuGuard())
    promo.router.callback_query.outer_middleware(StaleMenuGuard())

    # idle-таймаут участника: если давно ничего не отвечал, abandon-им
    # сессию на любом следующем действии (см. utils/idle_middleware.py).
    idle_guard = ParticipantIdleGuard()
    participant.router.callback_query.outer_middleware(idle_guard)
    participant.router.message.outer_middleware(idle_guard)

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
