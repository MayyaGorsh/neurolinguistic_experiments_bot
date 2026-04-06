"""
рассылка промо-текстов прошлым участникам экспериментов.
"""

import logging
from datetime import datetime

from aiogram import Router, Bot, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db import repositories as repo

router = Router()
logger = logging.getLogger("bot")


class PromoStates(StatesGroup):
    entering_text = State()
    confirming = State()


@router.callback_query(F.data == "promo_menu")
async def on_promo_menu(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    participants = await repo.get_past_participants()
    await callback.message.answer(
        f"Прошлых участников: {len(participants)}\n\n"
        "Введите текст для рассылки:"
    )
    await state.set_state(PromoStates.entering_text)


@router.message(PromoStates.entering_text, F.text)
async def on_promo_text(message: types.Message, state: FSMContext):
    await state.update_data(promo_text=message.text.strip())
    participants = await repo.get_past_participants()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Отправить", callback_data="promo_send")],
        [InlineKeyboardButton(text="Отмена", callback_data="back_to_menu")],
    ])
    await message.answer(
        f"Текст рассылки:\n\n{message.text.strip()}\n\n"
        f"Получателей: {len(participants)}\n"
        "Подтвердите отправку.",
        reply_markup=kb,
    )
    await state.set_state(PromoStates.confirming)


@router.callback_query(PromoStates.confirming, F.data == "promo_send")
async def on_promo_send(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    data = await state.get_data()
    text = data.get("promo_text", "")
    participants = await repo.get_past_participants()

    sent = 0
    failed = 0
    for tid in participants:
        try:
            await bot.send_message(tid, text)
            sent += 1
        except Exception:
            failed += 1

    # сохраняем отчет
    await repo.save_mailing({
        "sender_id": callback.from_user.id,
        "text": text,
        "recipients_count": len(participants),
        "sent": sent,
        "failed": failed,
        "timestamp": datetime.utcnow(),
    })

    await callback.message.answer(
        f"Рассылка завершена.\nОтправлено: {sent}, не доставлено: {failed}"
    )
    await state.clear()
