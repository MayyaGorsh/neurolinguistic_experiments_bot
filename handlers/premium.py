"""премиум-статус: страница описания и приёма заявок на оплату.

Сам перевод денег происходит вне бота (ручной банковский перевод).
Пользователь присылает скриншот, бот логирует заявку (telegram_id, file_id,
timestamp) - администратор просматривает логи, проверяет перевод и вручную
обновляет users.is_premium = True в MongoDB.

is_premium хранится на документе пользователя; проверки стоят в:
- handlers/researcher_create.py - лимит на число экспериментов
- handlers/promo.py - доступ к рассылке
- handlers/start.py - наличие кнопки «Перейти на премиум» в главном меню
"""

import logging

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db import repositories as repo
from models.user import is_premium_active
from utils.ui import render_screen

router = Router()
logger = logging.getLogger("bot")


# текст с условиями. реквизиты заглушечные - заменить на реальные перед
# публикацией бота.
PREMIUM_INFO_TEXT = (
    "<b>Премиум-статус</b>\n\n"
    "Что даёт премиум:\n"
    "• снимает лимит на число экспериментов (в бесплатном тарифе - до 5)\n"
    "• доступ к рассылке приглашений по базе участников бота\n\n"
    "<b>Как оформить</b>\n"
    "Стоимость: 299 руб. за месяц.\n"
    "Перевод на карту 0000 0000 0000 0000 (получатель: «Имя Фамилия»).\n\n"
    "После перевода нажмите кнопку ниже и пришлите скриншот в чат. "
    "Мы вручную проверим оплату и активируем премиум на 30 дней. "
    "По истечении срока премиум автоматически отменяется; для продления "
    "достаточно повторить перевод."
)


class PremiumStates(StatesGroup):
    waiting_screenshot = State()


@router.callback_query(F.data == "premium_info")
async def on_premium_info(callback: types.CallbackQuery, state: FSMContext):
    """экран с описанием премиум-статуса и инструкцией по оплате."""
    await callback.answer()
    user = await repo.get_user(callback.from_user.id)
    if is_premium_active(user):
        until = user.get("premium_until")
        until_str = until.strftime("%d.%m.%Y") if until else "—"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="Продлить - отправить скриншот",
                callback_data="premium_send_proof",
            )],
            [InlineKeyboardButton(
                text="← В главное меню", callback_data="back_to_menu",
            )],
        ])
        await render_screen(
            callback,
            f"У вас активен премиум-статус до <b>{until_str}</b>. "
            "По истечении срока премиум автоматически отменится; "
            "для продления переведите 299 руб. и пришлите скриншот.",
            kb,
            state=state,
        )
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Я перевёл(а) - отправить скриншот",
            callback_data="premium_send_proof",
        )],
        [InlineKeyboardButton(
            text="← В главное меню", callback_data="back_to_menu",
        )],
    ])
    await render_screen(callback, PREMIUM_INFO_TEXT, kb, state=state)


@router.callback_query(F.data == "premium_send_proof")
async def on_premium_send_proof(
    callback: types.CallbackQuery, state: FSMContext,
):
    """приглашение прислать скриншот перевода."""
    await callback.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Отмена", callback_data="back_to_menu")],
    ])
    await render_screen(
        callback,
        "Пришлите скриншот перевода сюда в чат отдельным сообщением. "
        "После проверки мы активируем вам премиум.",
        kb,
        state=state,
    )
    await state.set_state(PremiumStates.waiting_screenshot)


@router.message(PremiumStates.waiting_screenshot, F.photo)
async def on_premium_screenshot(message: types.Message, state: FSMContext):
    """скриншот пришёл - логируем заявку для ручной обработки админом.

    из логов админ берёт telegram_id и (при необходимости) file_id скриншота,
    проверяет перевод по выписке и руками обновляет users.is_premium в Mongo.
    """
    file_id = message.photo[-1].file_id
    logger.warning(
        "PREMIUM REQUEST: telegram_id=%s photo_file_id=%s",
        message.from_user.id, file_id,
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="← В главное меню", callback_data="back_to_menu",
        )],
    ])
    await message.answer(
        "Спасибо, заявка принята. Мы вручную проверим перевод и активируем "
        "вам премиум-статус. После активации откроются рассылка и неограниченное "
        "число экспериментов.",
        reply_markup=kb,
    )
    await state.clear()


@router.message(PremiumStates.waiting_screenshot)
async def on_premium_wrong_input(message: types.Message):
    """любое не-фото в режиме ожидания скриншота - подсказка."""
    await message.answer(
        "Ожидается скриншот (фото). Пришлите изображение перевода или "
        "вернитесь в меню командой /start."
    )
