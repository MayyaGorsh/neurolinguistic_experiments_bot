"""
хендлеры прохождения эксперимента респондентом.
обрабатывают: начало сессии, ответы кнопками, ответы текстом,
голосовые сообщения, демографию, инструкции.
"""

import logging

from aiogram import Router, Bot, types, F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db import repositories as repo
from engine import runner, demographics

router = Router()
logger = logging.getLogger("bot")


# ── начало эксперимента ──

@router.callback_query(F.data.startswith("begin_"))
async def on_begin_experiment(callback: types.CallbackQuery, bot: Bot):
    """респондент нажал «Начать» — создаем сессию"""
    await callback.answer()
    experiment_id = callback.data.replace("begin_", "")

    experiment = await repo.get_experiment(experiment_id)
    if not experiment:
        await callback.message.answer("Эксперимент не найден.")
        return

    # создаем сессию
    session_data = {
        "telegram_id": callback.from_user.id,
        "experiment_id": experiment_id,
        "status": "started",
        "assigned_list": None,
        "current_phase": 0,
        "current_trial": 0,
        "is_preview": False,
        "demographics": {},
        "demographics_index": 0,
    }

    # распределение по листам
    if experiment.get("use_lists") and experiment.get("lists_count", 1) > 1:
        counts = await repo.count_sessions_by_list(experiment_id)
        min_count = float("inf")
        best_list = "1"
        for i in range(1, experiment["lists_count"] + 1):
            list_name = str(i)
            c = counts.get(list_name, 0)
            if c < min_count:
                min_count = c
                best_list = list_name
        session_data["assigned_list"] = best_list

    session_id = await repo.create_session(session_data)
    logger.info(
        "сессия %s: user=%s, exp=%s, list=%s",
        session_id, callback.from_user.id, experiment_id,
        session_data["assigned_list"],
    )

    session = await repo.get_session(session_id)

    # подготавливаем пробы для каждой фазы (фильтр по листу + рандомизация)
    phases = experiment.get("phases", [])
    for i, phase in enumerate(phases):
        prepared = runner.prepare_trials_for_session(
            phase, session_data["assigned_list"]
        )
        phases[i]["trials"] = prepared

    # обновляем эксперимент с подготовленными пробами для этой сессии
    # (сохраняем в сессию, чтобы не менять общий эксперимент)
    await repo.update_session(session_id, {"prepared_phases": phases})

    # начинаем с демографии, если включена
    questions = demographics.get_questions(experiment)
    if questions:
        await repo.update_session(session_id, {"demographics_index": 0})
        await demographics.ask_demographic_question(
            bot, callback.from_user.id, session_id, questions, 0
        )
        return

    # если демографии нет — сразу к эксперименту
    await start_experiment_flow(bot, callback.from_user.id, session, experiment)


async def start_experiment_flow(
    bot: Bot, chat_id: int, session: dict, experiment: dict
):
    """запуск первой фазы эксперимента после демографии"""
    session_id = str(session["_id"])
    # берем подготовленные фазы из сессии
    prepared = session.get("prepared_phases") or experiment["phases"]
    # создаем рабочую копию эксперимента с подготовленными пробами
    exp_copy = dict(experiment)
    exp_copy["phases"] = prepared
    await runner.present_trial(bot, chat_id, session, exp_copy)


# ── обработка нажатий на инструкцию ──

@router.callback_query(F.data.startswith("instr_ok_"))
async def on_instruction_ok(callback: types.CallbackQuery, bot: Bot):
    """респондент прочел инструкцию — показываем первую пробу фазы"""
    await callback.answer()
    session_id = callback.data.replace("instr_ok_", "")
    session = await repo.get_session(session_id)
    if not session:
        return

    experiment = await repo.get_experiment(session["experiment_id"])
    if not experiment:
        return

    # помечаем инструкцию текущей фазы как показанную — чтобы
    # present_trial не нарисовал её снова (именно из-за этого раньше
    # получалась бесконечная петля «Далее»)
    phase_idx = session.get("current_phase", 0)
    shown = list(session.get("shown_instructions", []))
    if phase_idx not in shown:
        shown.append(phase_idx)
        await repo.update_session(session_id, {"shown_instructions": shown})
        session = await repo.get_session(session_id)

    prepared = session.get("prepared_phases") or experiment["phases"]
    exp_copy = dict(experiment)
    exp_copy["phases"] = prepared
    await runner.present_trial(bot, callback.from_user.id, session, exp_copy)


# ── обработка ответов кнопками ──

@router.callback_query(F.data.startswith("ans_"))
async def on_answer_button(callback: types.CallbackQuery, bot: Bot):
    """респондент нажал кнопку ответа"""
    await callback.answer()
    parts = callback.data.split("_")
    # формат: ans_{session_id}_{trial_idx}_{option_index_or_next}
    if len(parts) < 4:
        return

    session_id = parts[1]
    option_str = parts[3]

    session = await repo.get_session(session_id)
    if not session:
        return

    experiment = await repo.get_experiment(session["experiment_id"])
    if not experiment:
        return

    prepared = session.get("prepared_phases") or experiment["phases"]
    exp_copy = dict(experiment)
    exp_copy["phases"] = prepared

    phase = prepared[session["current_phase"]]
    trial = phase["trials"][session["current_trial"]]

    # определяем текст ответа
    if option_str == "next":
        raw_response = "_next_"
        option_index = None
    else:
        option_index = int(option_str)
        response_type = phase.get("response_type", "buttons")
        if response_type == "likert":
            raw_response = str(option_index)
        else:
            options = trial.get("response_options", [])
            if option_index < len(options):
                raw_response = options[option_index]
            else:
                raw_response = str(option_index)

    await runner.process_answer(
        bot, callback.from_user.id, session, exp_copy,
        raw_response, option_index,
    )


# ── обработка ответов демографии кнопками ──

@router.callback_query(F.data.startswith("demo_"))
async def on_demo_button(callback: types.CallbackQuery, bot: Bot):
    """респондент ответил на вопрос демографии кнопкой"""
    await callback.answer()
    parts = callback.data.split("_")
    # формат: demo_{session_id}_{q_index}_{option_index}
    if len(parts) < 4:
        return

    session_id = parts[1]
    q_index = int(parts[2])
    opt_index = int(parts[3])

    session = await repo.get_session(session_id)
    if not session:
        return

    experiment = await repo.get_experiment(session["experiment_id"])
    if not experiment:
        return

    questions = demographics.get_questions(experiment)
    if q_index >= len(questions):
        return

    # определяем текст ответа
    q = questions[q_index]
    options = q.get("options", [])
    answer_text = options[opt_index] if opt_index < len(options) else str(opt_index)

    await demographics.save_demographic_answer(session_id, questions, q_index, answer_text)

    # следующий вопрос
    next_q = q_index + 1
    await repo.update_session(session_id, {"demographics_index": next_q})

    if next_q < len(questions):
        await demographics.ask_demographic_question(
            bot, callback.from_user.id, session_id, questions, next_q
        )
    else:
        session = await repo.get_session(session_id)
        await start_experiment_flow(bot, callback.from_user.id, session, experiment)


# ── обработка текстовых ответов ──

@router.message(F.text)
async def on_text_answer(message: types.Message, bot: Bot):
    """обработка текстового ввода от респондента (open_text или демография)"""
    # ищем активную сессию для этого пользователя
    session = await find_active_session(message.from_user.id)
    if not session:
        return  # нет активной сессии — пропускаем, дойдет до fallback

    experiment = await repo.get_experiment(session["experiment_id"])
    if not experiment:
        return

    # проверяем, не идет ли сбор демографии
    questions = demographics.get_questions(experiment)
    demo_idx = session.get("demographics_index", 0)
    if questions and demo_idx < len(questions):
        q = questions[demo_idx]
        if q.get("type") != "buttons":
            await demographics.save_demographic_answer(
                str(session["_id"]), questions, demo_idx, message.text
            )
            next_q = demo_idx + 1
            session_id = str(session["_id"])
            await repo.update_session(session_id, {"demographics_index": next_q})
            if next_q < len(questions):
                await demographics.ask_demographic_question(
                    bot, message.from_user.id, session_id, questions, next_q
                )
            else:
                session = await repo.get_session(session_id)
                await start_experiment_flow(
                    bot, message.from_user.id, session, experiment
                )
            return

    # иначе это ответ на пробу
    prepared = session.get("prepared_phases") or experiment["phases"]
    exp_copy = dict(experiment)
    exp_copy["phases"] = prepared

    phase = prepared[session["current_phase"]]
    response_type = phase.get("response_type", "buttons")

    if response_type not in ("open_text", "voice"):
        await message.answer("Пожалуйста, используйте кнопки для ответа.")
        return

    await runner.process_answer(
        bot, message.from_user.id, session, exp_copy, message.text
    )


# ── обработка голосовых сообщений ──

@router.message(F.voice)
async def on_voice_answer(message: types.Message, bot: Bot):
    """обработка голосового сообщения от респондента"""
    session = await find_active_session(message.from_user.id)
    if not session:
        return

    experiment = await repo.get_experiment(session["experiment_id"])
    if not experiment:
        return

    prepared = session.get("prepared_phases") or experiment["phases"]
    exp_copy = dict(experiment)
    exp_copy["phases"] = prepared

    phase = prepared[session["current_phase"]]
    response_type = phase.get("response_type", "buttons")

    if response_type != "voice":
        await message.answer("В этом задании нужно ответить текстом или кнопками.")
        return

    # сохраняем file_id голосового сообщения
    voice_file_id = message.voice.file_id
    await runner.process_answer(
        bot, message.from_user.id, session, exp_copy,
        f"voice:{voice_file_id}",
    )


# ── вспомогательные ──

async def find_active_session(telegram_id: int):
    """найти любую незавершенную сессию пользователя"""
    from db.connection import sessions_col
    return await sessions_col.find_one({
        "telegram_id": telegram_id,
        "status": {"$in": ["started", "in_progress"]},
    })
