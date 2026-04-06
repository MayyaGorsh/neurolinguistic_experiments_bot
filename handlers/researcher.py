"""
кабинет исследователя: создание, настройка, публикация экспериментов,
просмотр результатов и экспорт в CSV.
"""

import logging
import secrets

from aiogram import Router, Bot, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BufferedInputFile,
)

from db import repositories as repo
from utils import export as export_util
from utils import csv_parser
from templates import registry as tmpl_registry

router = Router()
logger = logging.getLogger("bot")


# ── состояния FSM для создания эксперимента ──

class CreateExperiment(StatesGroup):
    choosing_template = State()
    entering_title = State()
    entering_description = State()
    configuring = State()
    uploading_csv = State()
    uploading_media = State()


# ── список шаблонов ──

TEMPLATE_LIST = [
    ("lexical_decision", "Lexical decision"),
    ("predictability_rating", "Predictability rating"),
    ("cloze_mc", "Cloze (multiple choice)"),
    ("cloze_open", "Cloze (open ended)"),
    ("word_translation_mc", "Word translation (closed)"),
    ("word_translation_open", "Word translation (open)"),
    ("sensicality_judgment", "Sensicality judgment"),
    ("acceptability_judgment", "Acceptability judgment"),
    ("tvjt", "Truth Value Judgment Task"),
    ("self_paced_reading", "Self-Paced Reading"),
    ("maze", "Maze task"),
    ("text_change_detection", "Text change detection"),
    ("probe_recognition", "Probe recognition"),
    ("interpretation_generation", "Interpretation generation"),
    ("forced_choice", "Forced choice identification"),
    ("sentence_repetition", "Sentence repetition"),
    ("picture_selection", "Picture selection"),
    ("covered_box", "Covered box"),
    ("picture_naming", "Picture naming"),
    ("video_task", "Video task"),
    ("free_form", "Свободный формат"),
]


# ── создание эксперимента ──

@router.callback_query(F.data == "create_experiment")
async def on_create_experiment(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    # показываем список шаблонов
    buttons = []
    for code, label in TEMPLATE_LIST:
        buttons.append([InlineKeyboardButton(
            text=label, callback_data=f"tmpl_{code}"
        )])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.answer("Выберите шаблон эксперимента:", reply_markup=kb)
    await state.set_state(CreateExperiment.choosing_template)


@router.callback_query(
    CreateExperiment.choosing_template,
    F.data.startswith("tmpl_"),
)
async def on_template_chosen(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    template_code = callback.data.replace("tmpl_", "")
    await state.update_data(template_type=template_code)
    await callback.message.answer("Введите название эксперимента:")
    await state.set_state(CreateExperiment.entering_title)


@router.message(CreateExperiment.entering_title, F.text)
async def on_title_entered(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await message.answer("Введите описание эксперимента (его увидят респонденты):")
    await state.set_state(CreateExperiment.entering_description)


@router.message(CreateExperiment.entering_description, F.text)
async def on_description_entered(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text.strip())
    data = await state.get_data()

    # для free-form переходим в отдельный flow
    if data.get("template_type") == "free_form":
        from handlers.free_form import start_free_form

        class FakeCallback:
            """обертка, чтобы передать message в start_free_form"""
            def __init__(self, msg):
                self.message = msg
                self.from_user = msg.from_user
                self.data = ""
            async def answer(self): pass

        await start_free_form(FakeCallback(message), state)
        return

    await show_config_menu(message, state)


async def show_config_menu(message_or_cb, state: FSMContext):
    """показать меню настроек эксперимента"""
    data = await state.get_data()
    tmpl = data.get("template_type", "free_form")

    # текущие настройки
    randomize = data.get("randomize", False)
    use_lists = data.get("use_lists", False)
    demographics = data.get("demographics", False)
    time_limit = data.get("time_limit", None)
    allow_repeat = data.get("allow_repeat", False)

    text = (
        f"<b>Настройки эксперимента</b>\n\n"
        f"Шаблон: {tmpl}\n"
        f"Рандомизация: {'да' if randomize else 'нет'}\n"
        f"Листы: {'да' if use_lists else 'нет'}\n"
        f"Демография: {'да' if demographics else 'нет'}\n"
        f"Тайм-аут: {str(time_limit) + ' сек' if time_limit else 'нет'}\n"
        f"Повторное прохождение: {'да' if allow_repeat else 'нет'}\n"
    )

    buttons = [
        [InlineKeyboardButton(
            text=f"{'✅' if randomize else '❌'} Рандомизация",
            callback_data="cfg_randomize",
        )],
        [InlineKeyboardButton(
            text=f"{'✅' if use_lists else '❌'} Распределение по листам",
            callback_data="cfg_lists",
        )],
        [InlineKeyboardButton(
            text=f"{'✅' if demographics else '❌'} Демография",
            callback_data="cfg_demographics",
        )],
        [InlineKeyboardButton(
            text=f"Тайм-аут: {str(time_limit) + ' сек' if time_limit else 'нет'}",
            callback_data="cfg_timeout",
        )],
        [InlineKeyboardButton(
            text=f"{'✅' if allow_repeat else '❌'} Повторное прохождение",
            callback_data="cfg_repeat",
        )],
        [InlineKeyboardButton(text="📎 Загрузить CSV", callback_data="cfg_upload_csv")],
        [InlineKeyboardButton(text="✅ Сохранить как черновик", callback_data="cfg_save")],
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    target = message_or_cb
    if hasattr(target, "answer"):
        await target.answer(text, reply_markup=kb)
    elif hasattr(target, "message"):
        await target.message.answer(text, reply_markup=kb)

    await state.set_state(CreateExperiment.configuring)


@router.callback_query(CreateExperiment.configuring, F.data == "cfg_randomize")
async def toggle_randomize(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    await state.update_data(randomize=not data.get("randomize", False))
    await show_config_menu(callback, state)


@router.callback_query(CreateExperiment.configuring, F.data == "cfg_lists")
async def toggle_lists(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    await state.update_data(use_lists=not data.get("use_lists", False))
    await show_config_menu(callback, state)


@router.callback_query(CreateExperiment.configuring, F.data == "cfg_demographics")
async def toggle_demographics(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    await state.update_data(demographics=not data.get("demographics", False))
    await show_config_menu(callback, state)


@router.callback_query(CreateExperiment.configuring, F.data == "cfg_repeat")
async def toggle_repeat(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    await state.update_data(allow_repeat=not data.get("allow_repeat", False))
    await show_config_menu(callback, state)


@router.callback_query(CreateExperiment.configuring, F.data == "cfg_timeout")
async def ask_timeout(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer(
        "Введите тайм-аут в секундах (0 — отключить):"
    )
    await state.set_state(CreateExperiment.configuring)
    await state.update_data(waiting_timeout=True)


@router.message(CreateExperiment.configuring, F.text)
async def on_config_text(message: types.Message, state: FSMContext):
    """обработка текстового ввода в режиме настроек (тайм-аут)"""
    data = await state.get_data()
    if data.get("waiting_timeout"):
        try:
            val = int(message.text.strip())
            await state.update_data(
                time_limit=val if val > 0 else None,
                waiting_timeout=False,
            )
        except ValueError:
            await message.answer("Введите целое число.")
            return
        await show_config_menu(message, state)
        return


# ── загрузка CSV (по фазам и листам) ──

@router.callback_query(CreateExperiment.configuring, F.data == "cfg_upload_csv")
async def ask_csv(callback: types.CallbackQuery, state: FSMContext):
    """начало загрузки CSV: определяем сколько фаз и листов нужно"""
    await callback.answer()
    data = await state.get_data()
    template_type = data.get("template_type", "free_form")
    use_lists = data.get("use_lists", False)

    # определяем список фаз из шаблона
    tmpl_info = tmpl_registry.get_template(template_type)
    phases_info = ["Основная фаза"]
    if tmpl_info:
        phases_info = tmpl_info.get("phases_info", ["Основная фаза"])

    await state.update_data(
        phases_info=phases_info,
        current_phase_num=1,
        current_list="1",
        # хранилище: {(phase_num, list_id): [trials]}
        csv_data={},
    )

    phase_name = phases_info[0]
    if use_lists:
        prompt = f"Отправьте CSV для фазы 1 ({phase_name}), лист 1."
    else:
        if len(phases_info) > 1:
            prompt = f"Отправьте CSV для фазы 1 ({phase_name})."
        else:
            prompt = "Отправьте CSV-файл со стимулами."

    await callback.message.answer(prompt)
    await state.set_state(CreateExperiment.uploading_csv)


@router.message(CreateExperiment.uploading_csv, F.document)
async def on_csv_uploaded(message: types.Message, state: FSMContext, bot: Bot):
    """обработка загруженного CSV-файла"""
    doc = message.document
    if not doc.file_name.lower().endswith(".csv"):
        await message.answer("Пожалуйста, отправьте файл в формате CSV.")
        return

    file = await bot.download(doc)
    content = file.read()

    try:
        rows = csv_parser.parse_csv_bytes(content)
    except Exception as e:
        await message.answer(f"Ошибка чтения CSV: {e}")
        return

    if not rows:
        await message.answer("CSV-файл пуст.")
        return

    data = await state.get_data()
    template_type = data.get("template_type", "free_form")
    current_phase_num = data.get("current_phase_num", 1)
    current_list = data.get("current_list", "1")

    # валидация и маппинг
    tmpl_info = tmpl_registry.get_template(template_type)
    if tmpl_info:
        required = tmpl_info.get("required_columns", [])
        errors = csv_parser.validate_columns(rows, required)
        if errors:
            await message.answer("Ошибки в CSV:\n" + "\n".join(errors))
            return
        mapping = tmpl_info.get("csv_mapping", {})
    else:
        mapping = auto_detect_mapping(rows)

    trials = csv_parser.rows_to_trials(rows, mapping)

    # помечаем list_id и phase_num
    for t in trials:
        t["list_id"] = current_list
        t["phase_num"] = current_phase_num

    # сохраняем
    csv_data = data.get("csv_data", {})
    key = f"{current_phase_num}_{current_list}"
    csv_data[key] = trials
    await state.update_data(csv_data=csv_data)

    count = len(trials)
    columns = list(rows[0].keys()) if rows else []
    phases_info = data.get("phases_info", ["Основная фаза"])
    phase_name = phases_info[current_phase_num - 1] if current_phase_num <= len(phases_info) else f"Фаза {current_phase_num}"

    await message.answer(
        f"Загружено {count} строк для фазы {current_phase_num} ({phase_name}), лист {current_list}.\n"
        f"Колонки: {', '.join(columns)}"
    )

    # предлагаем следующий шаг
    await ask_next_csv_step(message, state)


async def ask_next_csv_step(message, state: FSMContext):
    """определить, что загружать дальше: следующий лист или следующую фазу"""
    data = await state.get_data()
    use_lists = data.get("use_lists", False)
    current_phase_num = data.get("current_phase_num", 1)
    current_list = data.get("current_list", "1")
    phases_info = data.get("phases_info", ["Основная фаза"])

    buttons = []

    # предложить следующий лист (внутри текущей фазы)
    if use_lists:
        next_list = str(int(current_list) + 1)
        buttons.append([InlineKeyboardButton(
            text=f"Загрузить лист {next_list} (фаза {current_phase_num})",
            callback_data=f"csv_next_list_{next_list}",
        )])

    # предложить следующую фазу
    if current_phase_num < len(phases_info):
        next_phase = current_phase_num + 1
        next_name = phases_info[next_phase - 1]
        buttons.append([InlineKeyboardButton(
            text=f"Перейти к фазе {next_phase} ({next_name})",
            callback_data=f"csv_next_phase_{next_phase}",
        )])

    buttons.append([InlineKeyboardButton(text="✅ Готово", callback_data="csv_done")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("Что загрузить дальше?", reply_markup=kb)


@router.callback_query(CreateExperiment.uploading_csv, F.data.startswith("csv_next_list_"))
async def on_next_list(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    next_list = callback.data.replace("csv_next_list_", "")
    data = await state.get_data()
    current_phase_num = data.get("current_phase_num", 1)
    phases_info = data.get("phases_info", ["Основная фаза"])
    phase_name = phases_info[current_phase_num - 1] if current_phase_num <= len(phases_info) else f"Фаза {current_phase_num}"

    await state.update_data(current_list=next_list)
    await callback.message.answer(
        f"Отправьте CSV для фазы {current_phase_num} ({phase_name}), лист {next_list}."
    )


@router.callback_query(CreateExperiment.uploading_csv, F.data.startswith("csv_next_phase_"))
async def on_next_phase(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    next_phase = int(callback.data.replace("csv_next_phase_", ""))
    data = await state.get_data()
    use_lists = data.get("use_lists", False)
    phases_info = data.get("phases_info", ["Основная фаза"])
    phase_name = phases_info[next_phase - 1] if next_phase <= len(phases_info) else f"Фаза {next_phase}"

    await state.update_data(current_phase_num=next_phase, current_list="1")
    if use_lists:
        await callback.message.answer(
            f"Отправьте CSV для фазы {next_phase} ({phase_name}), лист 1."
        )
    else:
        await callback.message.answer(
            f"Отправьте CSV для фазы {next_phase} ({phase_name})."
        )


@router.callback_query(CreateExperiment.uploading_csv, F.data == "csv_done")
async def on_csv_done(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    csv_data = data.get("csv_data", {})

    # подсчитываем количество уникальных листов
    list_ids = set()
    for key in csv_data:
        parts = key.split("_")
        if len(parts) == 2:
            list_ids.add(parts[1])
    lists_count = len(list_ids) if list_ids else 1

    await state.update_data(lists_count=lists_count)
    await show_config_menu(callback, state)


# ── сохранение черновика ──

@router.callback_query(CreateExperiment.configuring, F.data == "cfg_save")
async def on_save_draft(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()

    template_type = data.get("template_type", "free_form")
    csv_data = data.get("csv_data", {})
    phases_info = data.get("phases_info", ["Основная фаза"])

    # группируем trials по номеру фазы
    trials_by_phase = {}
    for key, trials in csv_data.items():
        parts = key.split("_")
        phase_num = int(parts[0]) if len(parts) == 2 else 1
        if phase_num not in trials_by_phase:
            trials_by_phase[phase_num] = []
        trials_by_phase[phase_num].extend(trials)

    # формируем фазы из шаблона
    tmpl_info = tmpl_registry.get_template(template_type)
    if tmpl_info:
        if len(phases_info) > 1 and len(trials_by_phase) > 1:
            # многофазный шаблон — собираем фазы по отдельности
            phases = []
            for phase_num in sorted(trials_by_phase.keys()):
                phase_trials = trials_by_phase[phase_num]
                built = tmpl_info["build_phases"](phase_trials, data)
                # берем фазу с нужным индексом
                for p in built:
                    if p["phase_index"] == phase_num - 1:
                        phases.append(p)
                        break
                else:
                    # если build_phases не вернул фазу с нужным индексом,
                    # берем первую и корректируем индекс
                    if built:
                        built[0]["phase_index"] = phase_num - 1
                        phases.append(built[0])
        else:
            # однофазный шаблон или все данные в одной фазе
            all_trials = []
            for phase_num in sorted(trials_by_phase.keys()):
                all_trials.extend(trials_by_phase[phase_num])
            phases = tmpl_info["build_phases"](all_trials, data)
    else:
        # free_form или неизвестный — одна фаза со всеми пробами
        all_trials = []
        for phase_num in sorted(trials_by_phase.keys()):
            all_trials.extend(trials_by_phase[phase_num])
        phases = [{
            "phase_index": 0,
            "title": "Основная фаза",
            "instruction": "",
            "stimulus_type": "text",
            "response_type": "buttons",
            "trials": all_trials,
            "randomize_order": data.get("randomize", False),
            "time_limit": data.get("time_limit"),
            "settings": {},
        }]

    deep_link_id = "exp_" + secrets.token_urlsafe(8)

    experiment_data = {
        "owner_id": callback.from_user.id,
        "title": data.get("title", "Без названия"),
        "description": data.get("description", ""),
        "template_type": template_type,
        "status": "draft",
        "phases": phases,
        "randomize_trials": data.get("randomize", False),
        "use_lists": data.get("use_lists", False),
        "lists_count": data.get("lists_count", 1),
        "time_limit": data.get("time_limit"),
        "collect_demographics": data.get("demographics", False),
        "demographics_type": "standard",
        "demographics_custom": [],
        "allow_repeat": data.get("allow_repeat", False),
        "export_settings": {},
        "deep_link_id": deep_link_id,
    }

    exp_id = await repo.create_experiment(experiment_data)
    await state.clear()

    logger.info("эксперимент %s создан пользователем %s", exp_id, callback.from_user.id)

    await callback.message.answer(
        f"Эксперимент «{data.get('title')}» сохранен как черновик.\n"
        f"ID: {exp_id}"
    )
    await show_experiment_detail(callback.message, exp_id)


# ── детали эксперимента ──

async def show_experiment_detail(message, experiment_id: str):
    """показать карточку эксперимента с действиями"""
    exp = await repo.get_experiment(experiment_id)
    if not exp:
        await message.answer("Эксперимент не найден.")
        return

    status_text = {"draft": "Черновик", "active": "Активен", "archived": "Архив"}
    phases_count = len(exp.get("phases", []))
    trials_count = sum(len(p.get("trials", [])) for p in exp.get("phases", []))

    text = (
        f"<b>{exp['title']}</b>\n\n"
        f"Статус: {status_text.get(exp['status'], exp['status'])}\n"
        f"Шаблон: {exp['template_type']}\n"
        f"Фаз: {phases_count}, проб: {trials_count}\n"
    )

    if exp["status"] == "active":
        bot_info_text = f"\nСсылка: https://t.me/YOUR_BOT?start={exp['deep_link_id']}"
        text += bot_info_text

    buttons = []
    if exp["status"] == "draft":
        buttons.append([InlineKeyboardButton(
            text="🟢 Активировать",
            callback_data=f"activate_{experiment_id}",
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
        text="← Назад",
        callback_data="my_experiments",
    )])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("exp_detail_"))
async def on_experiment_detail(callback: types.CallbackQuery):
    await callback.answer()
    exp_id = callback.data.replace("exp_detail_", "")
    await show_experiment_detail(callback.message, exp_id)


# ── загрузка медиа ──

@router.callback_query(F.data.startswith("upload_media_"))
async def on_upload_media(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    exp_id = callback.data.replace("upload_media_", "")
    from handlers.media_upload import start_media_upload
    await start_media_upload(callback.message, exp_id, state)


# ── активация / деактивация ──

@router.callback_query(F.data.startswith("activate_"))
async def on_activate(callback: types.CallbackQuery, bot: Bot):
    await callback.answer()
    exp_id = callback.data.replace("activate_", "")

    # валидация перед активацией
    from utils.validators import validate_experiment
    exp = await repo.get_experiment(exp_id)
    if not exp:
        await callback.message.answer("Эксперимент не найден.")
        return
    errors = validate_experiment(exp)
    if errors:
        await callback.message.answer(
            "Не удалось активировать. Ошибки:\n" + "\n".join(f"• {e}" for e in errors)
        )
        return

    await repo.update_experiment(exp_id, {"status": "active"})
    exp = await repo.get_experiment(exp_id)

    bot_me = await bot.get_me()
    link = f"https://t.me/{bot_me.username}?start={exp['deep_link_id']}"

    await callback.message.answer(
        f"Эксперимент активирован!\n\nСсылка для респондентов:\n{link}"
    )
    await show_experiment_detail(callback.message, exp_id)


@router.callback_query(F.data.startswith("deactivate_"))
async def on_deactivate(callback: types.CallbackQuery):
    await callback.answer()
    exp_id = callback.data.replace("deactivate_", "")
    await repo.update_experiment(exp_id, {"status": "draft"})
    await callback.message.answer("Эксперимент деактивирован.")
    await show_experiment_detail(callback.message, exp_id)


# ── превью ──

@router.callback_query(F.data.startswith("preview_"))
async def on_preview(callback: types.CallbackQuery, bot: Bot):
    """исследователь проходит эксперимент как участник (preview mode)"""
    await callback.answer()
    exp_id = callback.data.replace("preview_", "")
    experiment = await repo.get_experiment(exp_id)
    if not experiment:
        await callback.message.answer("Эксперимент не найден.")
        return

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
    for i, phase in enumerate(phases):
        prepared = runner.prepare_trials_for_session(phase, "1")
        phases[i]["trials"] = prepared
    await repo.update_session(session_id, {"prepared_phases": phases})
    session = await repo.get_session(session_id)

    await callback.message.answer("Запускаю превью эксперимента...")
    await runner.present_trial(bot, callback.from_user.id, session, experiment)


# ── результаты ──

@router.callback_query(F.data.startswith("results_"))
async def on_results(callback: types.CallbackQuery):
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

    # распределение по листам
    list_counts = {}
    for s in real_sessions:
        lst = s.get("assigned_list", "—")
        list_counts[lst] = list_counts.get(lst, 0) + 1
    if list_counts:
        text += "\nПо листам:\n"
        for lst, cnt in sorted(list_counts.items()):
            text += f"  Лист {lst}: {cnt}\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Экспорт CSV", callback_data=f"export_{exp_id}")],
        [InlineKeyboardButton(text="← Назад", callback_data=f"exp_detail_{exp_id}")],
    ])
    await callback.message.answer(text, reply_markup=kb)


# ── экспорт CSV ──

@router.callback_query(F.data.startswith("export_"))
async def on_export(callback: types.CallbackQuery):
    await callback.answer()
    exp_id = callback.data.replace("export_", "")

    csv_text = await export_util.export_experiment_csv(exp_id)
    if not csv_text.strip():
        await callback.message.answer("Нет данных для экспорта.")
        return

    file = BufferedInputFile(
        csv_text.encode("utf-8-sig"),
        filename=f"results_{exp_id}.csv",
    )
    await callback.message.answer_document(file, caption="Результаты эксперимента")


# ── список экспериментов ──

@router.callback_query(F.data == "my_experiments")
async def on_my_experiments(callback: types.CallbackQuery):
    await callback.answer()
    experiments = await repo.get_experiments_by_owner(callback.from_user.id)

    if not experiments:
        await callback.message.answer("У вас пока нет экспериментов.")
        return

    buttons = []
    for exp in experiments:
        icon = {"draft": "📝", "active": "🟢", "archived": "📦"}.get(exp["status"], "")
        buttons.append([InlineKeyboardButton(
            text=f"{icon} {exp['title']}",
            callback_data=f"exp_detail_{exp['_id']}",
        )])
    buttons.append([InlineKeyboardButton(text="← Назад", callback_data="back_to_menu")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.answer("Ваши эксперименты:", reply_markup=kb)


@router.callback_query(F.data == "results_menu")
async def on_results_menu(callback: types.CallbackQuery):
    """показать список экспериментов для просмотра результатов"""
    await callback.answer()
    experiments = await repo.get_experiments_by_owner(callback.from_user.id)

    if not experiments:
        await callback.message.answer("У вас пока нет экспериментов.")
        return

    buttons = []
    for exp in experiments:
        buttons.append([InlineKeyboardButton(
            text=exp["title"],
            callback_data=f"results_{exp['_id']}",
        )])
    buttons.append([InlineKeyboardButton(text="← Назад", callback_data="back_to_menu")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.answer("Выберите эксперимент:", reply_markup=kb)


@router.callback_query(F.data == "back_to_menu")
async def on_back_to_menu(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Создать эксперимент", callback_data="create_experiment")],
        [InlineKeyboardButton(text="Мои эксперименты", callback_data="my_experiments")],
        [InlineKeyboardButton(text="Результаты", callback_data="results_menu")],
    ])
    await callback.message.answer("Главное меню:", reply_markup=kb)


# ── вспомогательные ──

def auto_detect_mapping(rows: list[dict]) -> dict:
    """попытка автоматически определить маппинг колонок CSV"""
    if not rows:
        return {}
    cols = list(rows[0].keys())
    mapping = {}

    # первая колонка — стимул
    if cols:
        mapping["stimulus_content"] = cols[0]

    # ищем колонку correct
    for c in cols:
        if "correct" in c.lower():
            mapping["correct_answer"] = c
            break

    # остальные — варианты ответа (кроме стимула, correct и list_id)
    skip = {mapping.get("stimulus_content"), mapping.get("correct_answer"), "list_id"}
    opt_cols = [c for c in cols if c not in skip]
    if opt_cols:
        mapping["response_options"] = opt_cols

    return mapping
