"""
движок прохождения эксперимента.
управляет последовательностью фаз и проб, измеряет RT,
обрабатывает тайм-ауты и сохраняет ответы.
"""

import asyncio
import logging
import random
import time
from datetime import datetime
from typing import Optional

from aiogram import Bot, types
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaAudio,
)
from bson import ObjectId

from db import repositories as repo

logger = logging.getLogger("bot")

# хранилище активных таймеров тайм-аутов {session_id: asyncio.Task}
_timeout_tasks: dict[str, asyncio.Task] = {}

# хранилище времени показа стимула {session_id: timestamp в секундах}
_stimulus_shown_at: dict[str, float] = {}


# ── показ стимула ──

async def present_trial(bot: Bot, chat_id: int, session: dict, experiment: dict):
    """показать текущую пробу респонденту"""
    phase_idx = session["current_phase"]
    trial_idx = session["current_trial"]
    phases = experiment["phases"]

    if phase_idx >= len(phases):
        await finish_experiment(bot, chat_id, session)
        return

    phase = phases[phase_idx]
    trials = phase.get("trials", [])

    # если фаза пустая или все пробы пройдены — переходим к следующей фазе
    if trial_idx >= len(trials):
        await advance_phase(bot, chat_id, session, experiment)
        return

    # если это начало фазы — показать инструкцию
    if trial_idx == 0 and phase.get("instruction"):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="Далее",
                callback_data=f"instr_ok_{session['_id']}",
            )]
        ])
        await bot.send_message(chat_id, phase["instruction"], reply_markup=kb)
        return

    trial = trials[trial_idx]
    session_id = str(session["_id"])

    # отправляем стимул в зависимости от типа
    stimulus_type = phase.get("stimulus_type", trial.get("stimulus_type", "text"))
    response_type = phase.get("response_type", "buttons")

    # собираем клавиатуру ответов
    keyboard = build_response_keyboard(trial, phase, session_id, trial_idx)

    if stimulus_type == "text":
        msg = await bot.send_message(
            chat_id,
            trial.get("stimulus_content", ""),
            reply_markup=keyboard,
        )
    elif stimulus_type == "image":
        file_id = trial.get("stimulus_metadata", {}).get("file_id", "")
        msg = await bot.send_photo(
            chat_id, file_id,
            caption=trial.get("stimulus_content", ""),
            reply_markup=keyboard,
        )
    elif stimulus_type == "audio":
        file_id = trial.get("stimulus_metadata", {}).get("file_id", "")
        msg = await bot.send_audio(
            chat_id, file_id,
            caption=trial.get("stimulus_content", ""),
            reply_markup=keyboard,
        )
    elif stimulus_type == "video":
        file_id = trial.get("stimulus_metadata", {}).get("file_id", "")
        msg = await bot.send_video(
            chat_id, file_id,
            caption=trial.get("stimulus_content", ""),
            reply_markup=keyboard,
        )
    else:
        msg = await bot.send_message(
            chat_id,
            trial.get("stimulus_content", ""),
            reply_markup=keyboard,
        )

    # фиксируем момент показа стимула
    _stimulus_shown_at[session_id] = time.time()

    # обновляем статус сессии
    await repo.update_session(session_id, {
        "status": "in_progress",
        "current_phase": phase_idx,
        "current_trial": trial_idx,
    })

    # запускаем тайм-аут, если задан
    time_limit = phase.get("time_limit") or experiment.get("time_limit")
    if time_limit and response_type == "buttons":
        cancel_timeout(session_id)
        task = asyncio.create_task(
            handle_timeout(bot, chat_id, session, experiment, time_limit, msg.message_id)
        )
        _timeout_tasks[session_id] = task


def build_response_keyboard(
    trial: dict, phase: dict, session_id: str, trial_idx: int
) -> Optional[InlineKeyboardMarkup]:
    """собрать клавиатуру в зависимости от типа ответа"""
    response_type = phase.get("response_type", "buttons")

    if response_type in ("open_text", "voice"):
        # для текстового и голосового ввода кнопок нет
        return None

    options = trial.get("response_options", [])

    if response_type == "buttons" and options:
        buttons = []
        for i, opt in enumerate(options):
            buttons.append([InlineKeyboardButton(
                text=opt,
                callback_data=f"ans_{session_id}_{trial_idx}_{i}",
            )])
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    if response_type == "likert":
        # шкала ликерта — кнопки в один ряд
        scale = phase.get("settings", {}).get("likert_scale", 5)
        labels = phase.get("settings", {}).get("likert_labels", {})
        buttons = []
        for i in range(1, scale + 1):
            label = labels.get(str(i), str(i))
            buttons.append(InlineKeyboardButton(
                text=label,
                callback_data=f"ans_{session_id}_{trial_idx}_{i}",
            ))
        return InlineKeyboardMarkup(inline_keyboard=[buttons])

    if response_type == "multiple_choice" and options:
        buttons = []
        for i, opt in enumerate(options):
            buttons.append([InlineKeyboardButton(
                text=opt,
                callback_data=f"ans_{session_id}_{trial_idx}_{i}",
            )])
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    # если ничего не подходит — кнопка «Далее»
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Далее",
            callback_data=f"ans_{session_id}_{trial_idx}_next",
        )]
    ])


# ── обработка ответа ──

async def process_answer(
    bot: Bot,
    chat_id: int,
    session: dict,
    experiment: dict,
    raw_response: str,
    option_index: Optional[int] = None,
):
    """обработать ответ респондента на текущую пробу"""
    session_id = str(session["_id"])
    phase_idx = session["current_phase"]
    trial_idx = session["current_trial"]
    phase = experiment["phases"][phase_idx]
    trial = phase["trials"][trial_idx]

    # отменяем тайм-аут
    cancel_timeout(session_id)

    # считаем RT для кнопок
    rt_ms = None
    response_type = phase.get("response_type", "buttons")
    if response_type in ("buttons", "likert", "multiple_choice"):
        shown_at = _stimulus_shown_at.pop(session_id, None)
        if shown_at:
            rt_ms = int((time.time() - shown_at) * 1000)

    # нормализуем ответ
    normalized = raw_response.strip().lower()

    # проверяем корректность
    correct_answer = trial.get("correct_answer")
    is_correct = None
    if correct_answer is not None:
        if isinstance(correct_answer, list):
            is_correct = normalized in [a.strip().lower() for a in correct_answer]
        else:
            is_correct = normalized == str(correct_answer).strip().lower()

    # сохраняем ответ
    answer_data = {
        "session_id": session_id,
        "experiment_id": session["experiment_id"],
        "phase_index": phase_idx,
        "trial_index": trial_idx,
        "stimulus_id": trial.get("stimulus_content", ""),
        "raw_response": raw_response,
        "normalized_response": normalized,
        "is_correct": is_correct,
        "reaction_time_ms": rt_ms,
        "timed_out": False,
        "timestamp": datetime.utcnow(),
        "metadata": {
            "list_id": session.get("assigned_list"),
            "option_index": option_index,
        },
    }
    await repo.save_answer(answer_data)

    # переходим к следующей пробе
    await advance_trial(bot, chat_id, session, experiment)


# ── тайм-аут ──

async def handle_timeout(
    bot: Bot, chat_id: int, session: dict, experiment: dict,
    time_limit: int, message_id: int,
):
    """обработка тайм-аута: ждем time_limit секунд, потом записываем пропуск"""
    session_id = str(session["_id"])
    try:
        await asyncio.sleep(time_limit)
    except asyncio.CancelledError:
        return

    # тайм-аут сработал
    phase_idx = session["current_phase"]
    trial_idx = session["current_trial"]
    phase = experiment["phases"][phase_idx]
    trial = phase["trials"][trial_idx]

    _stimulus_shown_at.pop(session_id, None)

    # сохраняем ответ с пометкой тайм-аута
    answer_data = {
        "session_id": session_id,
        "experiment_id": session["experiment_id"],
        "phase_index": phase_idx,
        "trial_index": trial_idx,
        "stimulus_id": trial.get("stimulus_content", ""),
        "raw_response": "",
        "normalized_response": "",
        "is_correct": None,
        "reaction_time_ms": time_limit * 1000,
        "timed_out": True,
        "timestamp": datetime.utcnow(),
        "metadata": {"list_id": session.get("assigned_list")},
    }
    await repo.save_answer(answer_data)

    await bot.send_message(chat_id, "Время вышло.")

    # обновляем сессию и идем дальше
    fresh_session = await repo.get_session(session_id)
    if fresh_session:
        await advance_trial(bot, chat_id, fresh_session, experiment)


def cancel_timeout(session_id: str):
    """отменить активный тайм-аут для сессии"""
    task = _timeout_tasks.pop(session_id, None)
    if task and not task.done():
        task.cancel()


# ── навигация по пробам и фазам ──

async def advance_trial(bot: Bot, chat_id: int, session: dict, experiment: dict):
    """перейти к следующей пробе"""
    session_id = str(session["_id"])
    phase_idx = session["current_phase"]
    trial_idx = session["current_trial"] + 1
    phase = experiment["phases"][phase_idx]

    if trial_idx >= len(phase.get("trials", [])):
        # фаза закончена
        await advance_phase(bot, chat_id, session, experiment)
    else:
        await repo.update_session(session_id, {"current_trial": trial_idx})
        updated = await repo.get_session(session_id)
        await present_trial(bot, chat_id, updated, experiment)


async def advance_phase(bot: Bot, chat_id: int, session: dict, experiment: dict):
    """перейти к следующей фазе"""
    session_id = str(session["_id"])
    next_phase = session["current_phase"] + 1

    if next_phase >= len(experiment["phases"]):
        await finish_experiment(bot, chat_id, session)
    else:
        await repo.update_session(session_id, {
            "current_phase": next_phase,
            "current_trial": 0,
        })
        updated = await repo.get_session(session_id)
        await present_trial(bot, chat_id, updated, experiment)


async def finish_experiment(bot: Bot, chat_id: int, session: dict):
    """завершить эксперимент"""
    session_id = str(session["_id"])
    cancel_timeout(session_id)
    _stimulus_shown_at.pop(session_id, None)

    await repo.update_session(session_id, {
        "status": "completed",
        "finished_at": datetime.utcnow(),
    })
    logger.info("сессия %s завершена", session_id)
    await bot.send_message(
        chat_id,
        "Эксперимент завершен. Спасибо за участие!"
    )


# ── рандомизация ──

def randomize_trials(trials: list, seed: Optional[int] = None) -> list:
    """перемешать пробы, сохраняя оригинальные индексы"""
    shuffled = list(trials)
    if seed is not None:
        random.Random(seed).shuffle(shuffled)
    else:
        random.shuffle(shuffled)
    return shuffled


def filter_trials_by_list(trials: list, list_id: str) -> list:
    """оставить только пробы, принадлежащие заданному листу (или без листа)"""
    return [t for t in trials if t.get("list_id") in (list_id, None)]


def prepare_trials_for_session(phase: dict, assigned_list: Optional[str]) -> list:
    """подготовить список проб для конкретной сессии: фильтр по листу + рандомизация"""
    trials = list(phase.get("trials", []))

    # фильтрация по листу
    if assigned_list:
        trials = filter_trials_by_list(trials, assigned_list)

    # рандомизация
    if phase.get("randomize_order", False):
        trials = randomize_trials(trials)

    return trials
