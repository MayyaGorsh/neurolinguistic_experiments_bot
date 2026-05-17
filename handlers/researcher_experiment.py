"""жизненный цикл эксперимента после сохранения:
- карточка эксперимента (`show_experiment_detail`) — основная точка
  возврата для большинства действий;
- список «Мои эксперименты» и «Результаты»;
- редактирование черновика (загружает экземпляр обратно в state.data);
- активация / деактивация / удаление с подтверждением;
- превью в роли участника;
- экспорт CSV;
- глобальный «← В главное меню».

`show_experiment_detail` экспортируется наружу — её зовёт
researcher_save после сохранения и handlers.media_upload после
завершения загрузки медиа."""

from aiogram import Bot, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from db import repositories as repo
from utils import export as export_util

from handlers.researcher_common import (
    router,
    CreateExperiment,
    logger,
    _render_screen,
    tmpl_registry,
)


async def show_experiment_detail(
    target, experiment_id: str, banner: str = "",
    state: FSMContext | None = None,
):
    """показать карточку эксперимента с действиями.

    target — CallbackQuery или Message.
    banner — необязательная строка-уведомление в начале экрана
    (например, «✅ Изменения сохранены»), сворачивает короткую
    подтверждающую реплику в этот же экран.
    state — FSMContext для обновления active_menu_msg_id (StaleMenuGuard).
    """
    exp = await repo.get_experiment(experiment_id)
    if not exp:
        await _render_screen(target, "Эксперимент не найден.", state=state)
        return

    status_text = {"draft": "Черновик", "active": "Активен"}
    phases_count = len(exp.get("phases", []))
    trials_count = sum(len(p.get("trials", [])) for p in exp.get("phases", []))
    lists_count = max(int(exp.get("lists_count", 1) or 1), 1)
    # при распределении по листам каждый респондент видит только свой лист —
    # покажем и общий объём, и сколько достанется одному участнику
    per_participant = trials_count // lists_count if lists_count > 1 else trials_count

    head = f"{banner}\n\n" if banner else ""
    summary_parts = [f"Фаз: {phases_count}"]
    if lists_count > 1:
        summary_parts.append(f"листов: {lists_count}")
        summary_parts.append(f"всего проб: {trials_count}")
        summary_parts.append(f"на участника: {per_participant}")
    else:
        summary_parts.append(f"проб: {trials_count}")

    text = (
        f"{head}"
        f"<b>{exp['title']}</b>\n\n"
        f"Статус: {status_text.get(exp['status'], exp['status'])}\n"
        f"Шаблон: {exp['template_type']}\n"
        f"{', '.join(summary_parts)}\n"
    )

    # сводка настроек — тот же набор тогглов, что и в подменю настроек
    from handlers.researcher_settings import settings_summary_block
    settings_block = settings_summary_block(exp)
    if settings_block:
        text += f"\n{settings_block}"

    if exp["status"] == "active":
        # имя бота берём из target.bot — оба CallbackQuery и Message
        # имеют атрибут .bot, который инжектится aiogram-ом.
        try:
            bot_me = await target.bot.get_me()
            link = f"https://t.me/{bot_me.username}?start={exp['deep_link_id']}"
        except Exception:
            link = f"(deep_link_id: {exp['deep_link_id']})"
        # оборачиваем в <code> — нажатие в Telegram копирует текст,
        # и URL не превращается в кликабельную ссылку, поэтому сам
        # экспериментатор не уйдёт по ней в участники
        text += (
            f"\nСсылка для участников (нажмите, чтобы скопировать):\n"
            f"<code>{link}</code>"
        )

    buttons = []
    if exp["status"] == "draft":
        buttons.append([InlineKeyboardButton(
            text="🟢 Активировать",
            callback_data=f"act_ask_{experiment_id}",
        )])
        buttons.append([InlineKeyboardButton(
            text="✏️ Редактировать",
            callback_data=f"edit_draft_{experiment_id}",
        )])
    elif exp["status"] == "active":
        buttons.append([InlineKeyboardButton(
            text="⏸ Деактивировать",
            callback_data=f"deactivate_{experiment_id}",
        )])
        buttons.append([InlineKeyboardButton(
            text="👁 Превью",
            callback_data=f"preview_{experiment_id}",
        )])

    # загрузка медиа — для шаблонов с аудио/видео/картинками
    has_media_phases = any(
        p.get("stimulus_type") in ("audio", "image", "video")
        for p in exp.get("phases", [])
    )
    if has_media_phases and exp["status"] == "draft":
        buttons.append([InlineKeyboardButton(
            text="🖼 Загрузить медиафайлы",
            callback_data=f"upload_media_{experiment_id}",
        )])

    buttons.append([InlineKeyboardButton(
        text="📊 Результаты",
        callback_data=f"results_{experiment_id}",
    )])
    buttons.append([InlineKeyboardButton(
        text="📥 Экспорт CSV",
        callback_data=f"export_{experiment_id}",
    )])
    buttons.append([InlineKeyboardButton(
        text="🗑 Удалить",
        callback_data=f"del_ask_{experiment_id}",
    )])
    buttons.append([InlineKeyboardButton(
        text="← Назад",
        callback_data="my_experiments",
    )])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await _render_screen(target, text, kb, state=state)


@router.callback_query(F.data.startswith("exp_detail_"))
async def on_experiment_detail(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    exp_id = callback.data.replace("exp_detail_", "")
    await show_experiment_detail(callback, exp_id, state=state)


# ── редактирование черновика ──

@router.callback_query(F.data.startswith("edit_draft_"))
async def on_edit_draft(callback: types.CallbackQuery, state: FSMContext):
    """загрузить черновик в FSM state и открыть меню настроек"""
    exp_id = callback.data.replace("edit_draft_", "")
    exp = await repo.get_experiment(exp_id)
    if not exp:
        await callback.answer("Эксперимент не найден.", show_alert=True)
        return
    if exp.get("status") != "draft":
        await callback.answer(
            "Редактировать можно только черновики. Активный эксперимент "
            "изменять нельзя — это сломает консистентность данных.",
            show_alert=True,
        )
        return
    await callback.answer()

    # восстанавливаем режим демографии
    if not exp.get("collect_demographics"):
        demo_mode = "off"
    elif exp.get("demographics_type") == "custom":
        demo_mode = "custom"
    else:
        demo_mode = "standard"

    # сначала пытаемся достать «сырой» csv_data, который сохранили рядом
    # с phases на on_save_draft. он содержит исходные парсенные ряды CSV
    # (до build_phase) — ровно то, что нужно для повторной сборки. без
    # него для не-идемпотентных шаблонов (maze) пересборка из phase.trials
    # даёт рекурсивно-склеенные стимулы.
    raw = exp.get("csv_data_raw")
    if isinstance(raw, dict) and raw:
        csv_data: dict[str, list] = {k: list(v) for k, v in raw.items()}
    else:
        # fallback для старых экспериментов без csv_data_raw — собираем
        # по phase.trials. для шаблонов с идемпотентным build (большинство)
        # этого достаточно.
        csv_data = {}
        for phase_idx, phase in enumerate(exp.get("phases", [])):
            phase_num = phase_idx + 1
            for trial in phase.get("trials", []):
                list_id = str(trial.get("list_id") or "1")
                key = f"{phase_num}_{list_id}"
                csv_data.setdefault(key, []).append(trial)

    # список фаз для шаблона (нужен в меню «Что загрузить дальше»)
    tmpl_info = tmpl_registry.get_template(exp.get("template_type", ""))
    phases_info = ["Основная фаза"]
    if tmpl_info:
        phases_info = tmpl_info.get("phases_info", ["Основная фаза"])
    # для free_form phases_info возьмём из самих фаз (они уже сформированы)
    elif exp.get("phases"):
        phases_info = [p.get("title", f"Фаза {i+1}")
                       for i, p in enumerate(exp["phases"])]

    await state.clear()
    await state.update_data(
        editing_id=exp_id,  # маркер: on_save_draft сделает update, а не create
        title=exp.get("title", ""),
        description=exp.get("description", ""),
        template_type=exp.get("template_type", ""),
        randomize=exp.get("randomize_trials", False),
        randomize_button_positions=exp.get("randomize_button_positions", False),
        randomize_image_positions=exp.get("randomize_image_positions", False),
        delete_previous_trials=exp.get("delete_previous_trials", True),
        demographics_mode=demo_mode,
        demographics_custom=exp.get("demographics_custom", []),
        time_limit=exp.get("time_limit"),
        idle_timeout_seconds=int(exp.get("idle_timeout_seconds", 300) or 0),
        audio_silence_seconds=int(exp.get("audio_silence_seconds", 0) or 0),
        allow_repeat=exp.get("allow_repeat", False),
        phases_info=phases_info,
        current_phase_num=None,
        current_list=None,
        csv_data=csv_data,
        # нормализация: при противоречивых старых данных доверяем lists_count
        lists_count=max(
            int(exp.get("lists_count", 1) or 1),
            2 if exp.get("use_lists") else 1,
        ),
        custom_buttons=exp.get("custom_buttons") or {},
        custom_likert=exp.get("custom_likert") or {},
        custom_instructions=exp.get("custom_instructions") or {},
        presentation_mode=exp.get("presentation_mode", "single"),
        # для free_form: сохраняем фазы как есть, on_save_draft их подхватит
        free_form_phases=exp.get("phases", []) if exp.get("template_type") == "free_form" else [],
    )
    await state.set_state(CreateExperiment.configuring)
    from handlers.researcher_settings import show_config_menu
    await show_config_menu(callback, state)


# ── загрузка медиа ──

@router.callback_query(F.data.startswith("upload_media_"))
async def on_upload_media(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    exp_id = callback.data.replace("upload_media_", "")
    # глушим карточку эксперимента: дальше идёт загрузка медиа в своём
    # роутере; кнопки на карточке после старта аплода больше не должны
    # ничего делать (можно случайно нажать, например, «деактивировать»).
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception as e:
        logger.warning("не удалось снять кнопки с карточки media-upload: %s", e)
    await state.update_data(active_menu_msg_id=None)
    from handlers.media_upload import start_media_upload
    await start_media_upload(callback.message, exp_id, state)


# ── активация / деактивация ──

@router.callback_query(F.data.startswith("act_ask_"))
async def on_activate_ask(callback: types.CallbackQuery, state: FSMContext):
    """шаг 1: спросить подтверждение и заранее прогнать валидацию"""
    await callback.answer()
    exp_id = callback.data.replace("act_ask_", "")

    from utils.validators import validate_experiment
    from handlers.media_upload import attach_media_file_ids

    # подтянуть file_id из media-коллекции в experiment.phases. на_save_draft
    # после редактирования или незавершённый «Готово» в загрузчике могут
    # оставить пробы без file_id — но в media-коллекции файлы есть, и мы
    # тут их подцепляем. без этого валидация рапортует «не загружен
    # медиафайл», хотя файл загружен.
    await attach_media_file_ids(exp_id)

    exp = await repo.get_experiment(exp_id)
    if not exp:
        await _render_screen(callback, "Эксперимент не найден.", state=state)
        return

    errors = validate_experiment(exp)
    if errors:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="✏️ Вернуться к редактированию",
                callback_data=f"edit_draft_{exp_id}",
            )],
            [InlineKeyboardButton(
                text="← К эксперименту",
                callback_data=f"exp_detail_{exp_id}",
            )],
        ])
        await _render_screen(
            callback,
            "❌ <b>Эксперимент нельзя активировать.</b>\n\n"
            "Найдены проблемы:\n"
            + "\n".join(f"• {e}" for e in errors)
            + "\n\nИсправьте их и попробуйте снова.",
            kb,
            state=state,
        )
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🟢 Да, активировать",
            callback_data=f"act_do_{exp_id}",
        )],
        [InlineKeyboardButton(
            text="← Отмена",
            callback_data=f"exp_detail_{exp_id}",
        )],
    ])
    await _render_screen(
        callback,
        "⚠️ <b>После активации нельзя изменить черновик.</b>\n\n"
        "Как только эксперимент станет активным, структура фаз, стимулы, "
        "настройки и анкета будут зафиксированы. Это нужно для чистоты "
        "данных: все респонденты должны пройти одинаковый протокол.\n\n"
        "Если понадобится что-то поменять — эксперимент можно будет "
        "деактивировать обратно в черновик, но это сбросит часть данных "
        "сбора. Лучше проверить всё сейчас.\n\n"
        "Вы уверены?",
        kb,
        state=state,
    )


@router.callback_query(F.data.startswith("act_do_"))
async def on_activate_do(callback: types.CallbackQuery, bot: Bot, state: FSMContext):
    """шаг 2: реально активируем"""
    await callback.answer()
    exp_id = callback.data.replace("act_do_", "")

    # повторная валидация — черновик мог измениться между шагами
    from utils.validators import validate_experiment
    exp = await repo.get_experiment(exp_id)
    if not exp:
        await _render_screen(callback, "Эксперимент не найден.", state=state)
        return
    errors = validate_experiment(exp)
    if errors:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="✏️ К редактированию",
                callback_data=f"edit_draft_{exp_id}",
            )],
            [InlineKeyboardButton(
                text="← К эксперименту",
                callback_data=f"exp_detail_{exp_id}",
            )],
        ])
        await _render_screen(
            callback,
            "❌ Не удалось активировать. Проблемы:\n"
            + "\n".join(f"• {e}" for e in errors),
            kb,
            state=state,
        )
        return

    await repo.update_experiment(exp_id, {"status": "active"})
    # ссылка отрисуется внутри show_experiment_detail (статус active)
    await show_experiment_detail(
        callback, exp_id,
        banner="🟢 Эксперимент активирован. Ссылка для участников — ниже.",
        state=state,
    )


@router.callback_query(F.data.startswith("deactivate_"))
async def on_deactivate(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    exp_id = callback.data.replace("deactivate_", "")
    await repo.update_experiment(exp_id, {"status": "draft"})
    await show_experiment_detail(
        callback, exp_id, banner="⏸ Эксперимент деактивирован.",
        state=state,
    )


# ── удаление эксперимента ──

@router.callback_query(F.data.startswith("del_ask_"))
async def on_delete_ask(callback: types.CallbackQuery, state: FSMContext):
    """шаг 1: подтверждение удаления"""
    await callback.answer()
    exp_id = callback.data.replace("del_ask_", "")
    exp = await repo.get_experiment(exp_id)
    if not exp:
        await _render_screen(callback, "Эксперимент не найден.", state=state)
        return

    sessions = await repo.get_sessions_by_experiment(exp_id)
    real_sessions = [s for s in sessions if not s.get("is_preview", False)]
    n_sessions = len(real_sessions)
    answers = await repo.get_answers_by_experiment(exp_id)
    n_answers = len(answers)

    status_text = {"draft": "Черновик", "active": "Активен"}
    info = (
        f"⚠️ <b>Удалить эксперимент?</b>\n\n"
        f"«{exp['title']}»\n"
        f"Статус: {status_text.get(exp['status'], exp['status'])}\n"
        f"Сессий участников: {n_sessions}\n"
        f"Записей ответов: {n_answers}\n\n"
        f"Перед удалением бот пришлёт CSV с результатами "
        f"(если есть данные). После удаления ссылка перестанет работать "
        f"и все ответы будут стёрты безвозвратно.\n\n"
        f"Точно удалить?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🗑 Да, удалить",
            callback_data=f"del_do_{exp_id}",
        )],
        [InlineKeyboardButton(
            text="← Отмена",
            callback_data=f"exp_detail_{exp_id}",
        )],
    ])
    await _render_screen(callback, info, kb, state=state)


@router.callback_query(F.data.startswith("del_do_"))
async def on_delete_do(
    callback: types.CallbackQuery, state: FSMContext, bot: Bot,
):
    """шаг 2: выгрузить результаты (если есть данные) и удалить эксперимент."""
    await callback.answer()
    exp_id = callback.data.replace("del_do_", "")
    exp = await repo.get_experiment(exp_id)
    if not exp:
        await _render_screen(callback, "Эксперимент не найден.", state=state)
        return

    title = exp.get("title", "experiment")

    # сначала пробуем собрать выгрузку — если упадёт, не удаляем данные
    try:
        data, kind = await export_util.export_experiment_bundle(bot, exp_id)
    except Exception:
        logger.exception("ошибка экспорта перед удалением %s", exp_id)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="← К эксперименту",
                callback_data=f"exp_detail_{exp_id}",
            )],
        ])
        await _render_screen(
            callback,
            "❌ Не удалось сгенерировать выгрузку. Удаление отменено, "
            "чтобы не потерять данные. Попробуйте ещё раз позже.",
            kb,
            state=state,
        )
        return

    # удаляем сообщение-подтверждение, чтобы итоговое меню оказалось
    # ниже файла, а не над ним. (отредактировать его «на месте»
    # нельзя — оно осталось бы выше документа в ленте чата.)
    try:
        await callback.message.delete()
    except Exception:
        pass

    csv_note = ""
    if data:
        if kind == "zip":
            filename = f"results_{exp_id}.zip"
            csv_note = (
                "📥 Архив с CSV и голосовыми ответами — выше отдельным "
                "файлом.\n\n"
            )
        else:
            filename = f"results_{exp_id}.csv"
            csv_note = "📥 CSV с результатами — выше отдельным файлом.\n\n"
        file = BufferedInputFile(data, filename=filename)
        await callback.message.answer_document(
            file,
            caption=f"Результаты «{title}» перед удалением.",
        )
    else:
        csv_note = "ℹ️ Данных для экспорта не было — файл не прислан.\n\n"

    # каскадное удаление
    counts = await repo.delete_experiment_cascade(exp_id)

    logger.info(
        "пользователь %s удалил эксперимент %s (%s)",
        callback.from_user.id, exp_id, title,
    )

    # итоговый экран — новым сообщением, чтобы он оказался под CSV.
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← К списку", callback_data="my_experiments")],
        [InlineKeyboardButton(text="← В главное меню", callback_data="back_to_menu")],
    ])
    sent = await callback.message.answer(
        f"🗑 Эксперимент «{title}» удалён.\n"
        f"Стёрто: сессий {counts['sessions']}, "
        f"ответов {counts['answers']}, "
        f"медиа {counts['media']}.\n\n"
        + csv_note,
        reply_markup=kb,
    )
    # фиксируем как активный экран, чтобы StaleMenuGuard знал, что
    # старая (удалённая) карточка эксперимента более неактивна.
    await state.update_data(active_menu_msg_id=sent.message_id)


# ── превью ──

@router.callback_query(F.data.startswith("preview_"))
async def on_preview(callback: types.CallbackQuery, bot: Bot, state: FSMContext):
    """исследователь проходит эксперимент как участник (preview mode)"""
    await callback.answer()
    exp_id = callback.data.replace("preview_", "")
    experiment = await repo.get_experiment(exp_id)
    if not experiment:
        await _render_screen(callback, "Эксперимент не найден.", state=state)
        return

    # глушим карточку эксперимента: после старта превью её кнопки
    # не должны срабатывать — пользователь работает в новом контексте
    # (превью), и клик по «деактивировать» из старой карточки сбил бы
    # эксперимент в неожиданный момент.
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception as e:
        logger.warning("не удалось снять кнопки с карточки превью: %s", e)
    # сбрасываем active_menu_msg_id: превью идёт через participant-флоу,
    # и StaleMenuGuard будет пропускать всё, пока не появится новое
    # researcher-меню (главное меню после завершения превью).
    await state.update_data(active_menu_msg_id=None)

    session_data = {
        "telegram_id": callback.from_user.id,
        "experiment_id": exp_id,
        "status": "started",
        "assigned_list": "1",
        "current_phase": 0,
        "current_trial": 0,
        "is_preview": True,
        "demographics": {},
        "demographics_index": 999,  # пропускаем демографию в превью
    }

    from engine import runner

    session_id = await repo.create_session(session_data)
    session = await repo.get_session(session_id)

    # подготавливаем пробы
    phases = experiment.get("phases", [])
    randomize_buttons = experiment.get("randomize_button_positions", False)
    for i, phase in enumerate(phases):
        prepared = runner.prepare_trials_for_session(
            phase, "1",
            randomize_button_positions=randomize_buttons,
        )
        phases[i]["trials"] = prepared
    await repo.update_session(session_id, {"prepared_phases": phases})
    session = await repo.get_session(session_id)

    # показываем приветственный экран — тот же, что увидит респондент
    welcome = (
        f"<b>{experiment.get('title', '')}</b>\n\n"
        f"{experiment.get('description', '') or '<i>(приветствие не задано)</i>'}"
        f"\n\n<i>— превью в роли участника —</i>"
    )
    await callback.message.answer(welcome)
    await runner.present_trial(bot, callback.from_user.id, session, experiment)


# ── результаты ──

@router.callback_query(F.data.startswith("results_") & (F.data != "results_menu"))
async def on_results(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    exp_id = callback.data.replace("results_", "")
    sessions = await repo.get_sessions_by_experiment(exp_id)

    # фильтруем preview-сессии
    real_sessions = [s for s in sessions if not s.get("is_preview", False)]

    total = len(real_sessions)
    completed = sum(1 for s in real_sessions if s["status"] == "completed")
    in_progress = sum(1 for s in real_sessions if s["status"] in ("started", "in_progress"))

    text = (
        f"<b>Результаты</b>\n\n"
        f"Всего сессий: {total}\n"
        f"Завершено: {completed}\n"
        f"В процессе: {in_progress}\n"
    )

    # распределение по листам — только если эксперимент реально использует листы
    experiment = await repo.get_experiment(exp_id)
    lists_count = max(int((experiment or {}).get("lists_count", 1) or 1), 1)
    if lists_count > 1:
        list_counts: dict = {}
        for s in real_sessions:
            lst = s.get("assigned_list") or "—"
            list_counts[lst] = list_counts.get(lst, 0) + 1
        if list_counts:
            text += "\nПо листам:\n"
            for lst, cnt in sorted(list_counts.items(), key=lambda kv: str(kv[0])):
                text += f"  Лист {lst}: {cnt}\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Экспорт CSV", callback_data=f"export_{exp_id}")],
        [InlineKeyboardButton(text="← Назад", callback_data=f"exp_detail_{exp_id}")],
    ])
    await _render_screen(callback, text, kb, state=state)


# ── экспорт CSV ──

@router.callback_query(F.data.startswith("export_"))
async def on_export(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    exp_id = callback.data.replace("export_", "")

    data, kind = await export_util.export_experiment_bundle(bot, exp_id)
    if not data:
        # экран не меняем — показываем тоаст-уведомление поверх кнопки
        await callback.answer("Нет данных для экспорта.", show_alert=True)
        return

    await callback.answer()
    if kind == "zip":
        filename = f"results_{exp_id}.zip"
        caption = "Результаты эксперимента (CSV + голосовые ответы)"
    else:
        filename = f"results_{exp_id}.csv"
        caption = "Результаты эксперимента"
    file = BufferedInputFile(data, filename=filename)
    # удаляем текущий экран (карточку или экран результатов), чтобы файл
    # оказался выше итогового меню. иначе документ висит снизу, а
    # активное меню — сверху, неудобно.
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer_document(file, caption=caption)
    # перерисовываем карточку эксперимента новым сообщением — она
    # окажется ниже файла. возвращаемся именно в карточку: это самый
    # частый «домашний» экран после экспорта. show_experiment_detail
    # обновит active_menu_msg_id через переданный state.
    await show_experiment_detail(callback.message, exp_id, state=state)


# ── список экспериментов ──

@router.callback_query(F.data == "my_experiments")
async def on_my_experiments(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    experiments = await repo.get_experiments_by_owner(callback.from_user.id)

    if not experiments:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="← В главное меню", callback_data="back_to_menu")],
        ])
        await _render_screen(callback, "У вас пока нет экспериментов.", kb, state=state)
        return

    buttons = []
    for exp in experiments:
        icon = {"draft": "📝", "active": "🟢"}.get(exp["status"], "")
        buttons.append([InlineKeyboardButton(
            text=f"{icon} {exp['title']}",
            callback_data=f"exp_detail_{exp['_id']}",
        )])
    buttons.append([InlineKeyboardButton(text="← Назад", callback_data="back_to_menu")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await _render_screen(callback, "Ваши эксперименты:", kb, state=state)


@router.callback_query(F.data == "results_menu")
async def on_results_menu(callback: types.CallbackQuery, state: FSMContext):
    """показать список экспериментов для просмотра результатов"""
    await callback.answer()
    experiments = await repo.get_experiments_by_owner(callback.from_user.id)

    if not experiments:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="← В главное меню", callback_data="back_to_menu")],
        ])
        await _render_screen(callback, "У вас пока нет экспериментов.", kb, state=state)
        return

    buttons = []
    for exp in experiments:
        buttons.append([InlineKeyboardButton(
            text=exp["title"],
            callback_data=f"results_{exp['_id']}",
        )])
    buttons.append([InlineKeyboardButton(text="← Назад", callback_data="back_to_menu")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await _render_screen(callback, "Выберите эксперимент:", kb, state=state)


# ── главное меню (общая точка возврата) ──

@router.callback_query(F.data == "back_to_menu")
async def on_back_to_menu(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Создать эксперимент", callback_data="create_experiment")],
        [InlineKeyboardButton(text="Мои эксперименты", callback_data="my_experiments")],
        [InlineKeyboardButton(text="Результаты", callback_data="results_menu")],
        [InlineKeyboardButton(text="Рассылка участникам", callback_data="promo_menu")],
    ])
    await _render_screen(callback, "Главное меню:", kb, state=state)
