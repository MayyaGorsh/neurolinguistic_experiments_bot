"""
сбор демографических данных перед началом эксперимента.
два режима: стандартная анкета и кастомная из CSV.
"""

import logging
from aiogram import Bot, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db import repositories as repo

logger = logging.getLogger("bot")

# стандартные вопросы
STANDARD_QUESTIONS = [
    {"key": "age", "text": "Укажите ваш возраст:", "type": "open_text"},
    {
        "key": "gender",
        "text": "Укажите ваш пол:",
        "type": "buttons",
        "options": ["Мужской", "Женский", "Другое"],
    },
    {"key": "residence", "text": "Укажите город проживания:", "type": "open_text"},
    {"key": "native_language", "text": "Укажите ваш родной язык:", "type": "open_text"},
]


def get_questions(experiment: dict) -> list:
    """получить список вопросов демографии для эксперимента"""
    if not experiment.get("collect_demographics", False):
        return []

    if experiment.get("demographics_type") == "custom":
        return experiment.get("demographics_custom", [])

    return STANDARD_QUESTIONS


async def ask_demographic_question(
    bot: Bot, chat_id: int, session_id: str, questions: list, q_index: int
):
    """задать один вопрос демографии"""
    if q_index >= len(questions):
        return False  # все вопросы заданы

    q = questions[q_index]
    text = q["text"]

    if q.get("type") == "buttons" and q.get("options"):
        buttons = []
        for i, opt in enumerate(q["options"]):
            buttons.append([InlineKeyboardButton(
                text=opt,
                callback_data=f"demo_{session_id}_{q_index}_{i}",
            )])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        await bot.send_message(chat_id, text, reply_markup=kb)
    else:
        # открытый текстовый ответ
        await bot.send_message(chat_id, text)

    return True


async def save_demographic_answer(
    session_id: str, questions: list, q_index: int, answer: str
):
    """сохранить ответ на вопрос демографии в сессию"""
    q = questions[q_index]
    key = q.get("key", f"q_{q_index}")
    session = await repo.get_session(session_id)
    if not session:
        return
    demographics = session.get("demographics", {})
    demographics[key] = answer
    await repo.update_session(session_id, {"demographics": demographics})
