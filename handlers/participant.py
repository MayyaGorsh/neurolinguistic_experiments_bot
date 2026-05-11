"""
хендлеры прохождения эксперимента респондентом.
обрабатывают: начало сессии, ответы кнопками, ответы текстом,
голосовые сообщения, демографию, инструкции.
"""

import logging

from aiogram import Router, Bot, types, F
from aiogram.exceptions import TelegramBadRequest
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

    # сразу убираем клавиатуру у приветствия, чтобы повторный клик
    # (или скролл к старому сообщению с кнопкой) не запустил эксперимент
    # ещё раз. делаем это до всех дальнейших проверок и до создания
    # сессии, чтобы кнопка точно «погасла» в любом исходе.
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass

    experiment = await repo.get_experiment(experiment_id)
    if not experiment:
        await callback.message.answer("Эксперимент не найден.")
        return

    # эксперимент мог быть деактивирован после того, как участник открыл
    # ссылку — не запускаем сессию, если он сейчас не active.
    if experiment.get("status") != "active":
        await callback.message.answer("Этот эксперимент сейчас неактивен.")
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
    # закрываем все остальные брошенные сессии пользователя — иначе текст/
    # голос может уйти в чужую сессию (см. find_active_session) и
    # pending_judgment из старой сессии заблокирует кликнутую кнопку.
    abandoned = await repo.abandon_other_active_sessions(
        callback.from_user.id, keep_session_id=session_id,
    )
    if abandoned:
        logger.info(
            "закрыто %s старых сессий пользователя %s при старте новой",
            abandoned, callback.from_user.id,
        )
    logger.info(
        "сессия %s: user=%s, exp=%s, list=%s",
        session_id, callback.from_user.id, experiment_id,
        session_data["assigned_list"],
    )

    session = await repo.get_session(session_id)

    # подготавливаем пробы для каждой фазы (фильтр по листу + рандомизация)
    phases = experiment.get("phases", [])
    randomize_buttons = experiment.get("randomize_button_positions", False)
    for i, phase in enumerate(phases):
        prepared = runner.prepare_trials_for_session(
            phase, session_data["assigned_list"],
            randomize_button_positions=randomize_buttons,
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
    # формат: instr_ok_{session_id}_{phase_idx}
    parts = callback.data.split("_")
    if len(parts) < 4:
        return
    session_id = parts[2]
    try:
        cb_phase_idx = int(parts[3])
    except ValueError:
        return

    session = await repo.get_session(session_id)
    if not session:
        return

    # клик по инструкции уже пройденной фазы (прокрутил наверх) — игнор
    current_phase = session.get("current_phase", 0)
    if cb_phase_idx != current_phase:
        await callback.answer(
            "Эта инструкция уже пройдена.", show_alert=True,
        )
        return

    # идемпотентность: если инструкция этой фазы уже была подтверждена
    # (shown_instructions содержит текущий phase_idx), повторные клики
    # ничего не делают — иначе каждый клик заново вызывает present_trial
    # и пересылает первый стимул фазы. возникает, например, когда
    # delete_previous_trials=False и кнопка «Далее» инструкции остаётся
    # видимой в чате после первого клика.
    shown = list(session.get("shown_instructions", []))
    if current_phase in shown:
        await callback.answer(
            "Эта инструкция уже пройдена.", show_alert=True,
        )
        return

    experiment = await repo.get_experiment(session["experiment_id"])
    if not experiment:
        return

    # помечаем инструкцию текущей фазы как показанную — чтобы
    # present_trial не нарисовал её снова (именно из-за этого раньше
    # получалась бесконечная петля «Далее»)
    shown.append(current_phase)
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
    # формат: ans_{session_id}_{phase_idx}_{trial_idx}_{option_or_next}
    parts = callback.data.split("_")
    if len(parts) < 5:
        await callback.answer()
        return

    session_id = parts[1]
    try:
        cb_phase_idx = int(parts[2])
        cb_trial_idx = int(parts[3])
    except ValueError:
        await callback.answer()
        return
    option_str = parts[4]

    session = await repo.get_session(session_id)
    if not session:
        await callback.answer()
        return

    # отсекаем клики по старым пробам/фазам (прокрутка вверх).
    # ответ записываем только по текущей паре phase/trial из сессии.
    current_phase = session.get("current_phase", 0)
    current_trial = session.get("current_trial", 0)
    if cb_phase_idx != current_phase or cb_trial_idx != current_trial:
        await callback.answer(
            "Эта проба уже пройдена.", show_alert=True,
        )
        return

    # для buttons_then_text после первого клика бот ждёт обоснование текстом —
    # повторные клики по той же кнопке игнорируем, чтобы не перезаписать выбор.
    if session.get("pending_judgment"):
        await callback.answer(
            "Напишите обоснование ответа сообщением.", show_alert=True,
        )
        return

    # text_change в стадиях ask_original/ask_new ждёт от участника
    # текстовый ответ — клики по более старым кнопкам игнорируем.
    ptc = session.get("pending_text_change")
    if isinstance(ptc, dict) and ptc.get("stage") in ("ask_original", "ask_new"):
        await callback.answer(
            "Сначала напишите слово сообщением.", show_alert=True,
        )
        return

    # interpretation_generation: после клика «Далее» ждём текст с
    # интерпретацией — повторные клики по той же «Далее» игнорируем,
    # чтобы не сбросить reading_rt и не дублировать промпт.
    pi = session.get("pending_interpretation")
    if isinstance(pi, dict) and pi.get("stage") == "awaiting_text":
        await callback.answer(
            "Напишите интерпретацию сообщением.", show_alert=True,
        )
        return

    experiment = await repo.get_experiment(session["experiment_id"])
    if not experiment:
        await callback.answer()
        return

    prepared = session.get("prepared_phases") or experiment["phases"]
    exp_copy = dict(experiment)
    exp_copy["phases"] = prepared

    phase = prepared[current_phase]
    trial = phase["trials"][current_trial]

    # multiple_choice: callback'и идут двумя видами —
    #   _mc_K  — toggle K-го варианта (не финализирует, RT ещё идёт);
    #   _mcdone — финализация выбора и отправка ответа.
    # parts: ans, sid, pi, ti, "mc" или "mcdone", [K]
    if option_str == "mc" and len(parts) >= 6:
        try:
            idx = int(parts[5])
        except ValueError:
            await callback.answer()
            return
        options = trial.get("response_options", [])
        if not (0 <= idx < len(options)):
            await callback.answer()
            return
        chosen = list(session.get("pending_multi") or [])
        # toggle: если уже выбран — убираем (теряет позицию), иначе
        # добавляем в конец (фиксируем порядок по клику).
        if idx in chosen:
            chosen.remove(idx)
        else:
            chosen.append(idx)
        await repo.update_session(session_id, {"pending_multi": chosen})
        new_kb = runner.build_response_keyboard(
            trial, phase, session_id, current_phase, current_trial,
            selected=chosen,
        )
        try:
            await callback.message.edit_reply_markup(reply_markup=new_kb)
        except Exception:
            pass
        await callback.answer()
        return

    if option_str == "mcdone":
        chosen = list(session.get("pending_multi") or [])
        if not chosen:
            await callback.answer(
                "Выберите хотя бы один вариант.", show_alert=True,
            )
            return
        options = trial.get("response_options", [])
        chosen_texts = [
            options[i] for i in chosen if 0 <= i < len(options)
        ]
        # очищаем pending_multi сразу, чтобы повторный клик «Готово»
        # ничего не пересохранил.
        await repo.update_session(session_id, {"pending_multi": []})
        await callback.answer()
        # raw_response для MC — все выбранные варианты в порядке клика,
        # склеенные «, ». is_correct (общий) для MC оставляем пустым:
        # содержательная корректность раскладывается в пары
        # ans_K / is_correct_K (см. export.py).
        await runner.process_answer(
            bot, callback.from_user.id, session, exp_copy,
            ", ".join(chosen_texts),
            chosen[0] if chosen else None,
            message_id=callback.message.message_id,
            extra_metadata={
                "mc_chosen": chosen_texts,
                "mc_chosen_indices": chosen,
            },
        )
        return

    await callback.answer()

    # определяем текст ответа
    if option_str == "next":
        raw_response = "_next_"
        option_index = None
    else:
        try:
            option_index = int(option_str)
        except ValueError:
            return
        response_type = phase.get("response_type", "buttons")
        if response_type == "likert":
            raw_response = str(option_index)
        else:
            options = trial.get("response_options", [])
            if option_index < len(options):
                raw_response = options[option_index]
                # Шаблоны с картинками: участник кликнул кнопку под
                # позицией N («1», «2», «3»), но в выгрузку важно
                # сохранить имя выбранной картинки — иначе по «1» не
                # поймёшь, что именно было выбрано (тем более при
                # включённом перемешивании позиций). Резолвим в
                # filename из stimulus_metadata.images.
                phase_settings = phase.get("settings") or {}
                if phase_settings.get("is_picture_selection") or \
                        phase_settings.get("is_covered_box"):
                    images = (
                        trial.get("stimulus_metadata") or {}
                    ).get("images") or []
                    if option_index < len(images) and images[option_index]:
                        raw_response = images[option_index]
            else:
                raw_response = str(option_index)

    await runner.process_answer(
        bot, callback.from_user.id, session, exp_copy,
        raw_response, option_index,
        message_id=callback.message.message_id,
    )


# ── обработка ответов демографии кнопками ──

@router.callback_query(F.data.startswith("demo_"))
async def on_demo_button(callback: types.CallbackQuery, bot: Bot):
    """респондент ответил на вопрос демографии кнопкой"""
    parts = callback.data.split("_")
    # формат: demo_{session_id}_{q_index}_{option_index}
    if len(parts) < 4:
        await callback.answer()
        return

    session_id = parts[1]
    try:
        q_index = int(parts[2])
        opt_index = int(parts[3])
    except ValueError:
        await callback.answer()
        return

    session = await repo.get_session(session_id)
    if not session:
        await callback.answer()
        return

    # отсекаем клик по старому вопросу демографии (прокрутил вверх).
    # ответ принимаем только на текущий q_index из сессии.
    current_demo_idx = session.get("demographics_index", 0)
    if q_index != current_demo_idx:
        await callback.answer(
            "Этот вопрос уже пройден.", show_alert=True,
        )
        return

    await callback.answer()

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

    # text_change на стадиях ask_original/ask_new ждёт текстовое сообщение
    # с указанием слова — пропускаем такие тексты сразу в process_answer,
    # минуя обычные проверки response_type.
    if _text_change_expects_text(session):
        pass
    elif response_type not in ("open_text", "voice", "buttons_then_text"):
        await message.answer("Пожалуйста, используйте кнопки для ответа.")
        return
    elif response_type == "buttons_then_text" and not session.get("pending_judgment"):
        # для buttons_then_text текст имеет смысл только после клика по кнопке —
        # до этого ждём выбор «правильно/неправильно».
        await message.answer("Сначала выберите вариант кнопкой.")
        return

    await runner.process_answer(
        bot, message.from_user.id, session, exp_copy, message.text
    )


def _text_change_expects_text(session: dict) -> bool:
    """text_change на стадиях ask_original/ask_new ждёт от участника
    текстовое сообщение с указанием слова из оригинала и из повторного
    текста."""
    ptc = session.get("pending_text_change")
    if not isinstance(ptc, dict):
        return False
    return ptc.get("stage") in ("ask_original", "ask_new")


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

    voice_file_id = message.voice.file_id

    # сразу качаем байты к себе — Telegram file_id формально вечный, но
    # надёжнее не зависеть от него: исследователь может выгрузить
    # результаты через несколько месяцев. блоб лежит в GridFS, raw_response
    # хранит оригинальный telegram-id для отладки.
    voice_blob_id = None
    try:
        file = await bot.download(voice_file_id)
        voice_bytes = file.read()
        voice_blob_id = await repo.save_voice_blob(
            voice_bytes,
            filename=f"voice_{voice_file_id}.ogg",
            metadata={
                "experiment_id": session["experiment_id"],
                "session_id": str(session["_id"]),
                "telegram_file_id": voice_file_id,
                "phase_index": session.get("current_phase"),
                "trial_index": session.get("current_trial"),
            },
        )
    except Exception:
        logger.exception(
            "не удалось скачать voice file_id=%s для сессии %s — "
            "сохраним только telegram-id, выгрузка попробует докачать позже",
            voice_file_id, session.get("_id"),
        )

    extra_metadata = {}
    if voice_blob_id:
        extra_metadata["voice_blob_id"] = voice_blob_id

    await runner.process_answer(
        bot, message.from_user.id, session, exp_copy,
        f"voice:{voice_file_id}",
        extra_metadata=extra_metadata or None,
    )


# ── вспомогательные ──

async def find_active_session(telegram_id: int):
    """найти самую свежую незавершенную сессию пользователя.

    раньше брали find_one без сортировки и ловили баг: если у пользователя
    остались брошенные in_progress сессии от прошлых экспериментов, текст/
    голос мог уйти в любую из них, и process_answer показывал стимул из
    «не того» эксперимента. сейчас явно берём самую свежую."""
    return await repo.get_latest_active_session(telegram_id)
