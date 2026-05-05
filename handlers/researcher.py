"""
кабинет исследователя: создание, настройка, публикация экспериментов,
просмотр результатов и экспорт в CSV.
"""

import logging
import os
import secrets

from aiogram import Router, Bot, types, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BufferedInputFile,
    FSInputFile,
    InputMediaDocument,
)

from db import repositories as repo
from utils import export as export_util
from utils import csv_parser
from utils.ui import render_screen as _render_screen
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
    uploading_demographics = State()


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
    ("statement_verification", "Statement verification"),
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


# ── AJT-специфичные хелперы ──

_AJT_PRESENTATION_LABELS = {
    "single": "одиночная",
    "joint_one_rating": "совместная (одна оценка)",
    "joint_two_ratings": "совместная (две оценки)",
}
_AJT_PRESENTATION_CYCLE = ["single", "joint_one_rating", "joint_two_ratings"]


def _ajt_has_stimulus2(data: dict) -> bool:
    """в загруженном CSV (state.csv_data) есть непустая колонка stimulus2.

    тоггл «режим подачи» имеет смысл только когда в данных реально есть
    второе предложение, иначе joint-режимы вырождаются в single."""
    csv_data = data.get("csv_data") or {}
    for trials in csv_data.values():
        for t in (trials or []):
            aux = t.get("auxiliary") or {}
            if aux.get("stimulus2"):
                return True
    return False


def _ajt_csv_has_response_options(data: dict) -> bool:
    """в загруженном CSV у проб есть непустые response_options (opt1..optN).

    нужно для управления кнопкой «📊 Шкала ответа»: для AJT эта настройка
    влияет только в режиме «опций нет — числовая Likert». если CSV уже
    загружен с явными подписями кнопок, Likert-конфиг просто игнорируется,
    и кнопку лучше скрыть, чтобы не сбивать с толку."""
    csv_data = data.get("csv_data") or {}
    for trials in csv_data.values():
        for t in (trials or []):
            if t.get("response_options"):
                return True
    return False


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
    buttons.append([InlineKeyboardButton(
        text="← В главное меню", callback_data="back_to_menu",
    )])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await _render_screen(callback, "Выберите шаблон эксперимента:", kb, state=state)
    await state.set_state(CreateExperiment.choosing_template)


@router.callback_query(
    CreateExperiment.choosing_template,
    F.data.startswith("tmpl_"),
)
async def on_template_chosen(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    template_code = callback.data.replace("tmpl_", "")
    await state.update_data(template_type=template_code)
    await _render_screen(
        callback,
        f"Шаблон: <b>{template_code}</b>\n\n"
        "Введите название эксперимента сообщением.\n"
        "/cancel — отменить.",
        state=state,
    )
    await state.set_state(CreateExperiment.entering_title)


@router.message(CreateExperiment.entering_title, F.text)
async def on_title_entered(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await message.answer("Введите приветственное сообщение для респондентов:")
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


def _collect_settings_state(data: dict) -> dict:
    """собрать словарь текущих настроек + готовые человекочитаемые лейблы.
    Используется обоими экранами (top-level summary и settings submenu),
    чтобы не дублировать парсинг state.data."""
    tmpl = data.get("template_type", "free_form")
    randomize = data.get("randomize", False)
    randomize_buttons = data.get("randomize_button_positions", False)
    delete_previous = data.get("delete_previous_trials", True)
    lists_count = int(data.get("lists_count", 1) or 1)
    use_lists = lists_count >= 2
    lists_label = "нет" if not use_lists else f"{lists_count} шт."
    demo_mode = data.get("demographics_mode", "off")
    demo_custom = data.get("demographics_custom", [])
    time_limit = data.get("time_limit", None)
    allow_repeat = data.get("allow_repeat", False)
    demo_label = {
        "off": "нет",
        "standard": "стандартная",
        "custom": f"своя ({len(demo_custom)} вопр.)",
    }.get(demo_mode, "нет")
    presentation_mode = data.get("presentation_mode", "single")
    presentation_label = _AJT_PRESENTATION_LABELS.get(
        presentation_mode, presentation_mode,
    )
    return {
        "tmpl": tmpl,
        "randomize": randomize,
        "randomize_buttons": randomize_buttons,
        "delete_previous": delete_previous,
        "lists_count": lists_count,
        "lists_label": lists_label,
        "demo_label": demo_label,
        "time_limit": time_limit,
        "timeout_value": f"{time_limit} сек" if time_limit else "нет",
        "allow_repeat": allow_repeat,
        "presentation_mode": presentation_mode,
        "presentation_label": presentation_label,
        "has_buttons": _template_has_buttons(tmpl),
        "ajt_show_presentation": (
            tmpl == "acceptability_judgment" and _ajt_has_stimulus2(data)
        ),
    }


async def show_config_menu(message_or_cb, state: FSMContext):
    """top-level меню: краткая сводка настроек + 6 действий.
    подменю с самими тогглами — show_settings_submenu (из «Настроить
    эксперимент»). сводка моноширинная (<pre>) ради ровных колонок —
    кнопки в Telegram рендерятся пропорциональным шрифтом, в тексте
    сообщения с <pre> можно выровнять по символам."""
    data = await state.get_data()
    s = _collect_settings_state(data)
    tmpl = s["tmpl"]

    summary_rows: list[tuple[str, str]] = [
        ("Рандомизация", "да" if s["randomize"] else "нет"),
    ]
    if s["has_buttons"]:
        summary_rows.append((
            "Рандомизация позиций кнопок",
            "да" if s["randomize_buttons"] else "нет",
        ))
    if s["ajt_show_presentation"]:
        summary_rows.append(("Режим подачи", s["presentation_label"]))
    summary_rows += [
        ("Чистить предыдущие пробы", "да" if s["delete_previous"] else "нет"),
        ("Распределение по листам", s["lists_label"]),
        ("Демография", s["demo_label"]),
        ("Тайм-аут", s["timeout_value"]),
        ("Повторное прохождение", "да" if s["allow_repeat"] else "нет"),
    ]
    label_w = max(len(label) for label, _ in summary_rows) + 2
    summary_lines = [f"{label.ljust(label_w)}{value}" for label, value in summary_rows]
    summary_block = "<pre>" + "\n".join(summary_lines) + "</pre>"

    text = (
        "<b>Настройки эксперимента</b>\n\n"
        f"Шаблон: {tmpl}\n\n"
        f"{summary_block}"
    )

    buttons: list[list[InlineKeyboardButton]] = []
    if tmpl != "free_form":
        buttons.append([InlineKeyboardButton(
            text="📎 Загрузить CSV", callback_data="cfg_upload_csv",
        )])
    buttons.append([InlineKeyboardButton(
        text="⚙️ Настроить эксперимент", callback_data="cfg_settings_submenu",
    )])
    # инструкции фаз доступны для любого шаблона со своим build_phase;
    # для free_form они хранятся в самих фазах, поэтому пропускаем.
    if tmpl_registry.get_template(tmpl):
        buttons.append([InlineKeyboardButton(
            text="📝 Настроить инструкции фаз", callback_data="cfg_instructions",
        )])
    buttons += [
        [InlineKeyboardButton(
            text="💬 Настроить приветственное сообщение",
            callback_data="cfg_description",
        )],
        [InlineKeyboardButton(text="✅ Сохранить как черновик", callback_data="cfg_save")],
    ]

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await _render_screen(message_or_cb, text, kb, state=state)
    await state.set_state(CreateExperiment.configuring)


async def show_settings_submenu(message_or_cb, state: FSMContext):
    """подменю «Настроить эксперимент»: все тогглы со значениями + кнопки
    кастомизации (Кнопки ответа / Шкала ответа) — для тех шаблонов, где
    они применимы."""
    data = await state.get_data()
    s = _collect_settings_state(data)
    tmpl = s["tmpl"]

    text = "<b>Настройки эксперимента</b>"

    buttons: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(
            text=f"Рандомизация — {'да' if s['randomize'] else 'нет'}",
            callback_data="cfg_randomize",
        )],
    ]
    if s["has_buttons"]:
        buttons.append([InlineKeyboardButton(
            text=f"Рандомизация позиций кнопок — {'да' if s['randomize_buttons'] else 'нет'}",
            callback_data="cfg_randomize_buttons",
        )])
    if s["ajt_show_presentation"]:
        buttons.append([InlineKeyboardButton(
            text=f"Режим подачи — {s['presentation_label']}",
            callback_data="cfg_presentation_mode",
        )])
    buttons += [
        [InlineKeyboardButton(
            text=f"Чистить предыдущие пробы — {'да' if s['delete_previous'] else 'нет'}",
            callback_data="cfg_delete_previous",
        )],
        [InlineKeyboardButton(
            text=f"Распределение по листам — {s['lists_label']}",
            callback_data="cfg_lists",
        )],
        [InlineKeyboardButton(
            text=f"Демография — {s['demo_label']}",
            callback_data="cfg_demographics",
        )],
        [InlineKeyboardButton(
            text=f"Тайм-аут — {s['timeout_value']}",
            callback_data="cfg_timeout",
        )],
        [InlineKeyboardButton(
            text=f"Повторное прохождение — {'да' if s['allow_repeat'] else 'нет'}",
            callback_data="cfg_repeat",
        )],
    ]

    # template-specific: «🔤 Кнопки ответа» / «📊 Шкала ответа»
    tmpl_info_for_btn = tmpl_registry.get_template(tmpl)
    if tmpl_info_for_btn:
        has_likert = bool(tmpl_info_for_btn.get("default_likert"))
        if tmpl_info_for_btn.get("default_response_options") and not has_likert:
            buttons.append([InlineKeyboardButton(
                text="🔤 Кнопки ответа", callback_data="cfg_buttons",
            )])
        hide_likert_btn = (
            tmpl == "acceptability_judgment"
            and _ajt_csv_has_response_options(data)
        )
        if has_likert and not hide_likert_btn:
            buttons.append([InlineKeyboardButton(
                text="📊 Шкала ответа", callback_data="cfg_likert",
            )])

    buttons.append([InlineKeyboardButton(
        text="ℹ️ Что это всё значит?", callback_data="cfg_help",
    )])
    buttons.append([InlineKeyboardButton(
        text="← Назад", callback_data="cfg_back_to_main",
    )])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await _render_screen(message_or_cb, text, kb, state=state)
    await state.set_state(CreateExperiment.configuring)


_CONFIG_HELP_PAGE_1 = (
    "<b>Параметры эксперимента — 1/2</b>\n\n"
    "<b>🎲 Рандомизация</b>\n"
    "Если включена — стимулы (слова, предложения и т.п.) будут "
    "показываться каждому участнику в случайном порядке. "
    "Если выключена — все увидят стимулы в том же порядке, "
    "в каком они идут в CSV-файле.\n\n"
    "<b>🔀 Рандомизация позиций кнопок</b>\n"
    "Если включена — на каждой пробе с вариантами ответа "
    "(«Слово»/«Не слово», «Да»/«Нет» и т.п.) порядок кнопок будет "
    "случайным. Это важно при измерении времени реакции (RT): "
    "иначе курсор/палец оказывается ближе к одному из вариантов и "
    "ответы «не слово» подряд получаются систематически быстрее. "
    "Не влияет на шкалы Likert и текстовый ввод.\n\n"
    "<b>🧹 Чистить предыдущие пробы</b>\n"
    "Если включено (по умолчанию — да) — перед показом следующей "
    "пробы бот удаляет своё предыдущее сообщение со стимулом и "
    "инструкции этой пробы. Участник видит только текущий стимул, "
    "не может сравнить с предыдущими и не получает контекст от уже "
    "пройденных проб. Если выключено — все стимулы накапливаются "
    "в чате.\n"
    "Технические ограничения: ответы участника текстом или "
    "голосом удалить нельзя (Telegram запрещает боту удалять "
    "сообщения пользователя в личке), а сообщения старше 48 часов "
    "тоже не удаляются.\n\n"
    "<b>📋 Распределение по листам</b>\n"
    "«Лист» — это отдельный набор стимулов. Если у вас несколько "
    "вариантов эксперимента (например, лист A и лист B с разными "
    "наборами стимулов), бот автоматически распределит участников по "
    "листам поровну. Каждый участник пройдёт только один лист.\n\n"
    "<b>👤 Демография</b>\n"
    "Анкета, которую участник заполнит перед экспериментом. "
    "Ответы сохраняются вместе с результатами. Три варианта:\n"
    "• <b>Нет</b> — анкета не показывается.\n"
    "• <b>Стандартная</b> — заранее готовый набор: "
    "возраст (открытый ответ), пол (М/Ж/Другое), "
    "город (открытый ответ), родной язык (открытый ответ).\n"
    "• <b>Своя</b> — вы загружаете CSV-файл со своими вопросами. "
    "Формат (разделитель — точка с запятой):\n"
    "<code>key;text;type;options</code>\n"
    "где <i>key</i> — короткий идентификатор (напр. <code>age</code>), "
    "<i>text</i> — сам вопрос, "
    "<i>type</i> — <code>open_text</code> (любой ответ текстом) или "
    "<code>buttons</code> (выбор из вариантов), "
    "<i>options</i> — варианты для <code>buttons</code>, "
    "разделённые <code>|</code> (для <code>open_text</code> оставьте пустым)."
)

_CONFIG_HELP_PAGE_2 = (
    "<b>Параметры эксперимента — 2/2</b>\n\n"
    "<b>⏱ Тайм-аут и время реакции (RT)</b>\n"
    "Ограничение времени (в секундах) на ответ по каждому стимулу. "
    "Если участник не успел — ответ засчитывается как пропуск, "
    "эксперимент идёт дальше. «Нет» — времени неограниченно.\n"
    "<b>Важно про RT в открытых ответах.</b> В пробах с кнопками "
    "(<i>buttons</i>, <i>likert</i>, <i>multiple_choice</i>) RT "
    "измеряется от показа стимула до нажатия кнопки. "
    "В <i>open_text</i> и <i>voice</i> RT — это время от показа стимула "
    "до <b>отправки</b> сообщения, то есть включает и набор/запись, "
    "а не только задержку до начала ответа. Telegram не уведомляет бота "
    "о том, что пользователь начал печатать или удерживать запись, "
    "поэтому «чистое» время до начала ответа измерить нельзя. "
    "Тайм-аут в этих пробах тоже считается до отправки сообщения: "
    "если за N секунд участник не <i>отправил</i> ответ — ставится "
    "пропуск, даже если он печатал или говорил.\n\n"
    "<b>🔁 Повторное прохождение</b>\n"
    "Если включено — один и тот же участник может пройти эксперимент "
    "несколько раз. Если выключено — бот не даст пройти второй раз.\n\n"
    "<b>📎 Загрузить CSV</b>\n"
    "Файл со стимулами. Формат зависит от выбранного шаблона "
    "(колонки <i>stimulus</i>, <i>class</i>, и т.п.). Если фаз или листов "
    "несколько — CSV загружается отдельно для каждой фазы и листа.\n\n"
    "<b>📊 Шкала ответа (Likert) — как располагаются кнопки</b>\n"
    "• Если у всех позиций шкалы подписи — это просто цифры "
    "(<code>1</code>, <code>2</code>, …, <code>N</code>), кнопки "
    "встанут в один горизонтальный ряд.\n"
    "• Если хотя бы у одной позиции есть текстовая подпись "
    "(например, «Совсем не ожидаемо» на 1), все кнопки автоматически "
    "выкладываются в вертикальный список. Иначе Telegram обрезает "
    "длинные подписи на мобильных экранах.\n"
    "Поэтому если хочется компактную горизонтальную шкалу — "
    "не задавайте подписи, оставьте только цифры. Если важно "
    "обозначить полюса словами — будьте готовы к вертикальному виду.\n\n"
    "<b>🔤 Кнопки ответа</b>\n"
    "Кастомизация лейблов кнопок доступна только для шаблонов с "
    "картинками (Picture Selection, Covered Box). По умолчанию лейблы — "
    "«1», «2» (и «3» для трёх картинок). Их можно переименовать "
    "(например, «1» → «Левая»). Корректность считается по позиции: "
    "колонка <i>correct_img</i> в CSV указывает, какая картинка "
    "правильная, бот подставляет лейбл соответствующей позиции.\n"
    "Не меняйте порядок лейблов местами — это сместит соответствие "
    "позиций и сломает проверку корректности.\n\n"
    "<b>🌟 Правильный ответ в кнопочных шаблонах</b>\n"
    "Для остальных кнопочных шаблонов варианты ответа задаются "
    "колонками <code>opt1..opt6</code> в CSV. Поставьте звёздочку "
    "<code>*</code> перед текстом правильной опции — например, "
    "<code>*Слово</code>. Если правильного ответа нет (например, в "
    "Sensicality Judgment), просто не ставьте звёздочки — поле "
    "<i>is_correct</i> в результатах останется пустым."
)


@router.callback_query(CreateExperiment.configuring, F.data == "cfg_help")
async def show_config_help(callback: types.CallbackQuery, state: FSMContext):
    """объяснение параметров эксперимента — страница 1"""
    await callback.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Дальше →", callback_data="cfg_help_2")],
        [InlineKeyboardButton(text="← Назад", callback_data="cfg_back_to_settings")],
    ])
    await _render_screen(callback, _CONFIG_HELP_PAGE_1, kb, state=state)


@router.callback_query(CreateExperiment.configuring, F.data == "cfg_help_2")
async def show_config_help_page_2(callback: types.CallbackQuery, state: FSMContext):
    """объяснение параметров эксперимента — страница 2"""
    await callback.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Назад к странице 1", callback_data="cfg_help")],
        [InlineKeyboardButton(text="← В настройки", callback_data="cfg_back_to_settings")],
    ])
    await _render_screen(callback, _CONFIG_HELP_PAGE_2, kb, state=state)


def _reset_input_flags() -> dict:
    """очистить waiting_*-флаги ввода, чтобы следующее текстовое сообщение
    не было интерпретировано как недозавершённый ввод тайм-аута, метки
    кнопки и т.п. — используем при любом «выходе наверх» из суб-экрана."""
    return {
        "waiting_button_edit": None,
        "waiting_likert_edit": None,
        "waiting_instruction_edit": None,
        "waiting_description_edit": False,
        "waiting_timeout": False,
        "waiting_lists_count": False,
    }


@router.callback_query(CreateExperiment.configuring, F.data == "cfg_back")
async def cfg_back_to_menu(callback: types.CallbackQuery, state: FSMContext):
    """legacy «назад» — используется help-страницами и редакторами
    инструкций/приветствия; ведёт в top-level меню."""
    await callback.answer()
    await state.update_data(**_reset_input_flags())
    await show_config_menu(callback, state)


@router.callback_query(CreateExperiment.configuring, F.data == "cfg_back_to_main")
async def cfg_back_to_main(callback: types.CallbackQuery, state: FSMContext):
    """«Назад» из подменю «Настроить эксперимент» в top-level."""
    await callback.answer()
    await state.update_data(**_reset_input_flags())
    await show_config_menu(callback, state)


@router.callback_query(CreateExperiment.configuring, F.data == "cfg_back_to_settings")
async def cfg_back_to_settings(callback: types.CallbackQuery, state: FSMContext):
    """«Назад» из суб-экранов конкретных настроек (Демография, Тайм-аут,
    Листы, Кнопки ответа, Шкала ответа) — обратно в подменю настроек."""
    await callback.answer()
    await state.update_data(**_reset_input_flags())
    await show_settings_submenu(callback, state)


@router.callback_query(CreateExperiment.configuring, F.data == "cfg_settings_submenu")
async def on_open_settings_submenu(callback: types.CallbackQuery, state: FSMContext):
    """вход в подменю «Настроить эксперимент» из top-level."""
    await callback.answer()
    await show_settings_submenu(callback, state)


@router.callback_query(CreateExperiment.configuring, F.data == "cfg_randomize")
async def toggle_randomize(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    await state.update_data(randomize=not data.get("randomize", False))
    await show_settings_submenu(callback, state)


@router.callback_query(CreateExperiment.configuring, F.data == "cfg_randomize_buttons")
async def toggle_randomize_buttons(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    await state.update_data(
        randomize_button_positions=not data.get("randomize_button_positions", False)
    )
    await show_settings_submenu(callback, state)


@router.callback_query(CreateExperiment.configuring, F.data == "cfg_presentation_mode")
async def toggle_presentation_mode(callback: types.CallbackQuery, state: FSMContext):
    """переключение AJT режима подачи: single → joint_one → joint_two → ..."""
    await callback.answer()
    data = await state.get_data()
    current = data.get("presentation_mode", "single")
    try:
        idx = _AJT_PRESENTATION_CYCLE.index(current)
    except ValueError:
        idx = 0
    nxt = _AJT_PRESENTATION_CYCLE[(idx + 1) % len(_AJT_PRESENTATION_CYCLE)]
    await state.update_data(presentation_mode=nxt)
    await show_settings_submenu(callback, state)


@router.callback_query(CreateExperiment.configuring, F.data == "cfg_delete_previous")
async def toggle_delete_previous(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    # дефолт — True; первое нажатие выключает
    current = data.get("delete_previous_trials", True)
    await state.update_data(delete_previous_trials=not current)
    await show_settings_submenu(callback, state)


@router.callback_query(CreateExperiment.configuring, F.data == "cfg_lists")
async def ask_lists_count(callback: types.CallbackQuery, state: FSMContext):
    """запросить число листов; 1 = без распределения, ≥2 = делим респондентов"""
    await callback.answer()
    data = await state.get_data()
    current = int(data.get("lists_count", 1) or 1)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cfg_back_to_settings")],
    ])
    await _render_screen(
        callback,
        "Сколько <b>листов</b> в эксперименте?\n\n"
        "<b>1</b> — без распределения по листам, все участники видят один и тот же набор стимулов.\n"
        "<b>≥ 2</b> — респонденты делятся между листами поровну, каждый видит только свой лист.\n\n"
        f"<b>Сейчас:</b> {current}\n\n"
        "Введите целое число от 1 до 20.",
        kb,
        state=state,
    )
    await state.update_data(waiting_lists_count=True)


@router.callback_query(CreateExperiment.configuring, F.data == "cfg_demographics")
async def show_demographics_menu(callback: types.CallbackQuery, state: FSMContext):
    """подменю выбора режима демографии"""
    await callback.answer()
    data = await state.get_data()
    mode = data.get("demographics_mode", "off")
    custom = data.get("demographics_custom", [])

    text = (
        "<b>Демографическая анкета</b>\n\n"
        "• <b>Нет</b> — анкета не показывается.\n"
        "• <b>Стандартная</b> — возраст, пол, город, родной язык.\n"
        "• <b>Своя</b> — загрузите CSV со своими вопросами "
        "(<code>key;text;type;options</code>, "
        "<code>type</code>: <code>open_text</code>/<code>buttons</code>, "
        "варианты для кнопок через <code>|</code>).\n\n"
        f"Сейчас: <b>{ {'off': 'нет', 'standard': 'стандартная', 'custom': f'своя ({len(custom)} вопр.)'}.get(mode, 'нет') }</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Нет", callback_data="demo_off")],
        [InlineKeyboardButton(text="📋 Стандартная", callback_data="demo_standard")],
        [InlineKeyboardButton(text="📎 Загрузить свою (CSV)", callback_data="demo_upload")],
        [InlineKeyboardButton(text="← Назад", callback_data="cfg_back_to_settings")],
    ])
    await _render_screen(callback, text, kb, state=state)


@router.callback_query(CreateExperiment.configuring, F.data == "demo_off")
async def demo_set_off(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(demographics_mode="off", demographics_custom=[])
    await show_settings_submenu(callback, state)


@router.callback_query(CreateExperiment.configuring, F.data == "demo_standard")
async def demo_set_standard(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(demographics_mode="standard", demographics_custom=[])
    await show_settings_submenu(callback, state)


@router.callback_query(CreateExperiment.configuring, F.data == "demo_upload")
async def demo_ask_upload(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Показать пример", callback_data="demo_example")],
    ])
    await _render_screen(
        callback,
        "Отправьте CSV-файл с вопросами.\n\n"
        "Колонки (разделитель — <code>;</code>):\n"
        "• <b>key</b> — короткий идентификатор (напр. <code>age</code>)\n"
        "• <b>text</b> — сам вопрос\n"
        "• <b>type</b> — <code>open_text</code> или <code>buttons</code>\n"
        "• <b>options</b> — варианты через <code>|</code> (для buttons)",
        kb,
        state=state,
    )
    await state.set_state(CreateExperiment.uploading_demographics)


DEMO_EXAMPLE_CSV = (
    "key;text;type;options\n"
    "age;Укажите ваш возраст:;open_text;\n"
    "gender;Укажите ваш пол:;buttons;Мужской|Женский|Другое\n"
    "city;Укажите город проживания:;open_text;\n"
    "native;Укажите ваш родной язык:;open_text;\n"
    "english_level;Ваш уровень английского:;buttons;A1|A2|B1|B2|C1|C2|Не владею\n"
    "other_languages;Какими ещё языками владеете и на каком уровне?;open_text;\n"
)


@router.callback_query(CreateExperiment.uploading_demographics, F.data == "demo_example")
async def demo_send_example(callback: types.CallbackQuery, state: FSMContext):
    """отправить пример CSV-опросника.

    следующее действие исследователя — отгрузить свой CSV, поэтому
    после файла-примера никакого нового меню не шлём: подпись к
    документу уже содержит инструкцию «скачайте, отредактируйте,
    пришлите обратно». как только исследователь загрузит CSV, обработчик
    demo_on_csv_uploaded покажет следующий экран. экран-промпт удаляем —
    его инструкция продублирована в caption файла.
    """
    await callback.answer()
    file = BufferedInputFile(
        DEMO_EXAMPLE_CSV.encode("utf-8"),
        filename="demographics_example.csv",
    )
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer_document(
        file,
        caption=(
            "Пример CSV-опросника. Скачайте, отредактируйте под свои "
            "вопросы и пришлите CSV-файл обратно (колонки: "
            "<code>key;text;type;options</code>; "
            "<code>type</code> — <code>open_text</code> или "
            "<code>buttons</code>; варианты для <code>buttons</code> "
            "через <code>|</code>).\n\n"
            "/cancel — отмена."
        ),
    )


@router.message(CreateExperiment.uploading_demographics, F.document)
async def demo_on_csv_uploaded(message: types.Message, state: FSMContext, bot: Bot):
    """обработка CSV с кастомной анкетой"""
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

    # валидация и сбор вопросов
    questions = []
    errors = []
    for i, row in enumerate(rows, start=2):  # строка 1 — заголовок
        key = (row.get("key") or "").strip()
        text = (row.get("text") or "").strip()
        qtype = (row.get("type") or "open_text").strip().lower()
        options_raw = (row.get("options") or "").strip()

        if not key:
            errors.append(f"Строка {i}: пустой key")
            continue
        if not text:
            errors.append(f"Строка {i}: пустой text")
            continue
        if qtype not in ("open_text", "buttons"):
            errors.append(f"Строка {i}: неизвестный type '{qtype}' (нужно open_text или buttons)")
            continue

        q = {"key": key, "text": text, "type": qtype}
        if qtype == "buttons":
            opts = [o.strip() for o in options_raw.split("|") if o.strip()]
            if not opts:
                errors.append(f"Строка {i}: для type=buttons нужны options через |")
                continue
            q["options"] = opts
        questions.append(q)

    if errors:
        await message.answer(
            "Найдены ошибки:\n" + "\n".join(errors[:10]) +
            ("\n..." if len(errors) > 10 else "")
        )
        await state.set_state(CreateExperiment.configuring)
        return

    if not questions:
        await message.answer("В файле нет валидных вопросов.")
        await state.set_state(CreateExperiment.configuring)
        return

    await state.update_data(
        demographics_mode="custom",
        demographics_custom=questions,
    )
    await message.answer(f"Анкета загружена: {len(questions)} вопрос(ов).")
    await state.set_state(CreateExperiment.configuring)
    await show_settings_submenu(message, state)


@router.callback_query(CreateExperiment.configuring, F.data == "cfg_repeat")
async def toggle_repeat(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    await state.update_data(allow_repeat=not data.get("allow_repeat", False))
    await show_settings_submenu(callback, state)


# ── кастомизация кнопок ответа ──

def _get_current_buttons(data: dict, tmpl_code: str, key: str = "main") -> list[str]:
    """вернуть актуальные метки кнопок: кастомные или дефолт шаблона"""
    custom = (data.get("custom_buttons") or {}).get(key)
    if isinstance(custom, list) and custom:
        return list(custom)
    tmpl = tmpl_registry.get_template(tmpl_code) or {}
    return list((tmpl.get("default_response_options") or {}).get(key, []))


async def _show_buttons_submenu(callback: types.CallbackQuery, state: FSMContext):
    """подменю редактирования кнопок ответа"""
    data = await state.get_data()
    tmpl_code = data.get("template_type", "")
    labels = _get_current_buttons(data, tmpl_code)

    if not labels:
        # тоаст поверх текущего меню — экран не меняем
        await callback.answer(
            "У этого шаблона нет настраиваемых кнопок.", show_alert=True,
        )
        return

    text = (
        "<b>Кнопки ответа</b>\n\n"
        "Здесь вы задаёте, каким <i>текстом</i> подписана каждая "
        "семантическая категория шаблона. Кнопка №1 — это всегда "
        "первая категория (для lexical decision — «слово», для "
        "judgment-шаблонов — «приемлемо/осмысленно/верно» и т.п.), "
        "кнопка №2 — вторая категория.\n\n"
        "Важно: список ниже — это <b>сопоставление лейбла категории</b>, "
        "а не порядок показа на экране. Если включена «Рандомизация "
        "позиций кнопок», физическое расположение кнопок участнику "
        "тасуется на каждой пробе — но связка «лейбл ↔ категория» "
        "остаётся такой, как задано здесь.\n\n"
        "ℹ️ <b>Что сохраняется в результаты:</b> текст нажатой кнопки. "
        "Если переименуете «Слово» в «Yes» — в CSV-экспорте ответы "
        "будут «Yes». На корректность это не влияет.\n\n"
        "⚠️ <b>Чего делать не надо:</b> менять местами лейблы категорий "
        "(например, поставить «Не слово» на позицию №1). Это исказит "
        "правильные ответы: код шаблона выводит correct_answer как "
        "«лейбл категории такой-то» — и если первой категорией вдруг "
        "окажется «не-слово», то на CSV-стимуле с <code>class=word</code> "
        "правильным ответом будет «Не слово».\n\n"
        "ℹ️ Если кнопки приходят из CSV (<code>opt1..opt6</code> с маркером "
        "<code>*</code>), эта настройка не применяется — варианты "
        "берутся напрямую из файла.\n\n"
        "Текущие значения:\n"
        + "\n".join(f"{i+1}. {lbl}" for i, lbl in enumerate(labels))
    )

    buttons = []
    for i, lbl in enumerate(labels):
        buttons.append([InlineKeyboardButton(
            text=f"✏️ {i+1}: {lbl}",
            callback_data=f"btn_edit_{i}",
        )])
    buttons.append([InlineKeyboardButton(
        text="↩️ Сбросить к дефолту", callback_data="btn_reset",
    )])
    buttons.append([InlineKeyboardButton(
        text="← Назад", callback_data="cfg_back_to_settings",
    )])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await _render_screen(callback, text, kb, state=state)


async def _send_buttons_submenu(message: types.Message, state: FSMContext):
    """вариант для Message-контекста — после текстового ввода"""
    data = await state.get_data()
    tmpl_code = data.get("template_type", "")
    labels = _get_current_buttons(data, tmpl_code)
    if not labels:
        return
    text = (
        "<b>Кнопки ответа</b>\n\n"
        "Текущие значения:\n"
        + "\n".join(f"{i+1}. {lbl}" for i, lbl in enumerate(labels))
    )
    buttons = [[InlineKeyboardButton(
        text=f"✏️ {i+1}: {lbl}", callback_data=f"btn_edit_{i}",
    )] for i, lbl in enumerate(labels)]
    buttons.append([InlineKeyboardButton(text="↩️ Сбросить к дефолту", callback_data="btn_reset")])
    buttons.append([InlineKeyboardButton(text="← Назад", callback_data="cfg_back_to_settings")])
    await _render_screen(
        message, text,
        InlineKeyboardMarkup(inline_keyboard=buttons),
        state=state,
    )


async def _send_likert_submenu(message: types.Message, state: FSMContext):
    """вариант Likert-подменю для Message-контекста"""
    data = await state.get_data()
    tmpl_code = data.get("template_type", "")
    likert = _get_current_likert(data, tmpl_code)
    scale = likert["scale"]
    labels = likert["labels"]
    text = (
        "<b>Шкала ответа (Likert)</b>\n\n"
        f"Размер: <b>{scale}</b>\n"
        "Подписи:\n"
        + "\n".join(
            f"  {i}: {labels.get(str(i), str(i))}"
            for i in range(1, scale + 1)
        )
    )
    buttons = [[InlineKeyboardButton(
        text=f"🔁 Размер шкалы: {scale} (нажмите, чтобы сменить)",
        callback_data="lkt_scale",
    )]]
    for i in range(1, scale + 1):
        buttons.append([InlineKeyboardButton(
            text=f"✏️ {i}: {labels.get(str(i), str(i))}",
            callback_data=f"lkt_edit_{i}",
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Сбросить к дефолту", callback_data="lkt_reset")])
    buttons.append([InlineKeyboardButton(text="← Назад", callback_data="cfg_back_to_settings")])
    await _render_screen(
        message, text,
        InlineKeyboardMarkup(inline_keyboard=buttons),
        state=state,
    )


@router.callback_query(CreateExperiment.configuring, F.data == "cfg_buttons")
async def on_cfg_buttons(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await _show_buttons_submenu(callback, state)


@router.callback_query(CreateExperiment.configuring, F.data.startswith("btn_edit_"))
async def on_button_edit(callback: types.CallbackQuery, state: FSMContext):
    """запрос новой метки для кнопки с индексом N"""
    await callback.answer()
    idx = int(callback.data.replace("btn_edit_", ""))
    data = await state.get_data()
    tmpl_code = data.get("template_type", "")
    labels = _get_current_buttons(data, tmpl_code)
    if idx < 0 or idx >= len(labels):
        return
    await state.update_data(waiting_button_edit={"key": "main", "index": idx})
    await _render_screen(
        callback,
        f"Введите новую метку для кнопки №{idx + 1} "
        f"(сейчас: «{labels[idx]}»).\n\n"
        f"Отправьте /cancel чтобы отменить.",
        state=state,
    )


@router.callback_query(CreateExperiment.configuring, F.data == "btn_reset")
async def on_button_reset(callback: types.CallbackQuery, state: FSMContext):
    """сбросить кастомизацию кнопок к дефолту шаблона"""
    await callback.answer("Сброшено к дефолту.")
    data = await state.get_data()
    custom = dict(data.get("custom_buttons") or {})
    custom.pop("main", None)
    await state.update_data(custom_buttons=custom)
    await _show_buttons_submenu(callback, state)


# ── кастомизация Likert-шкалы ──

def _get_current_likert(data: dict, tmpl_code: str, key: str = "main") -> dict:
    """вернуть актуальные настройки Likert: кастомные поверх дефолта"""
    tmpl = tmpl_registry.get_template(tmpl_code) or {}
    default = dict((tmpl.get("default_likert") or {}).get(key) or {})
    if not default:
        return {"scale": 5, "labels": {}}
    default.setdefault("scale", 5)
    default.setdefault("labels", {})

    custom = (data.get("custom_likert") or {}).get(key) or {}
    scale = custom.get("scale") if isinstance(custom.get("scale"), int) else default["scale"]
    labels = dict(default["labels"])
    for k, v in (custom.get("labels") or {}).items():
        if isinstance(v, str) and v.strip():
            labels[str(k)] = v.strip()
    return {"scale": scale, "labels": labels}


async def _show_likert_submenu(callback: types.CallbackQuery, state: FSMContext):
    """подменю редактирования Likert-шкалы"""
    data = await state.get_data()
    tmpl_code = data.get("template_type", "")
    likert = _get_current_likert(data, tmpl_code)
    scale = likert["scale"]
    labels = likert["labels"]

    text = (
        "<b>Шкала ответа (Likert)</b>\n\n"
        "Respondent увидит набор кнопок с номерами 1..N. "
        "Можно поменять размер шкалы и подписи к любой позиции "
        "(обычно подписывают крайние — «совсем не...» и «очень...»).\n\n"
        "ℹ️ <b>Как это покажется участнику:</b>\n"
        "• Если у всех позиций подписи — просто цифры, кнопки будут "
        "в один горизонтальный ряд.\n"
        "• Как только у любой позиции появляется текстовая подпись, "
        "все кнопки переключаются в вертикальный список — иначе "
        "Telegram режет длинные надписи на узких экранах.\n\n"
        f"Текущий размер: <b>{scale}</b>\n"
        "Подписи:\n"
        + "\n".join(
            f"  {i}: {labels.get(str(i), str(i))}"
            for i in range(1, scale + 1)
        )
    )

    buttons = [[InlineKeyboardButton(
        text=f"🔁 Размер шкалы: {scale} (нажмите, чтобы сменить)",
        callback_data="lkt_scale",
    )]]
    for i in range(1, scale + 1):
        buttons.append([InlineKeyboardButton(
            text=f"✏️ {i}: {labels.get(str(i), str(i))}",
            callback_data=f"lkt_edit_{i}",
        )])
    buttons.append([InlineKeyboardButton(
        text="↩️ Сбросить к дефолту", callback_data="lkt_reset",
    )])
    buttons.append([InlineKeyboardButton(
        text="← Назад", callback_data="cfg_back_to_settings",
    )])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await _render_screen(callback, text, kb, state=state)


@router.callback_query(CreateExperiment.configuring, F.data == "cfg_likert")
async def on_cfg_likert(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await _show_likert_submenu(callback, state)


@router.callback_query(CreateExperiment.configuring, F.data == "lkt_scale")
async def on_likert_toggle_scale(callback: types.CallbackQuery, state: FSMContext):
    """циклическое переключение размера шкалы: 5 → 7 → 9 → 5"""
    await callback.answer()
    data = await state.get_data()
    tmpl_code = data.get("template_type", "")
    current = _get_current_likert(data, tmpl_code)
    next_scale = {5: 7, 7: 9, 9: 5}.get(current["scale"], 5)

    custom = dict(data.get("custom_likert") or {})
    main = dict(custom.get("main") or {})
    main["scale"] = next_scale
    # чистим подписи вне нового диапазона
    existing_labels = dict(main.get("labels") or {})
    for k in list(existing_labels.keys()):
        try:
            if int(k) > next_scale or int(k) < 1:
                existing_labels.pop(k, None)
        except ValueError:
            existing_labels.pop(k, None)
    main["labels"] = existing_labels
    custom["main"] = main
    await state.update_data(custom_likert=custom)
    await _show_likert_submenu(callback, state)


@router.callback_query(CreateExperiment.configuring, F.data.startswith("lkt_edit_"))
async def on_likert_label_edit(callback: types.CallbackQuery, state: FSMContext):
    """запрос новой подписи для позиции N"""
    await callback.answer()
    pos = int(callback.data.replace("lkt_edit_", ""))
    await state.update_data(waiting_likert_edit={"key": "main", "pos": pos})
    data = await state.get_data()
    tmpl_code = data.get("template_type", "")
    current = _get_current_likert(data, tmpl_code)
    cur_label = current["labels"].get(str(pos), str(pos))
    await _render_screen(
        callback,
        f"Введите новую подпись для позиции {pos} "
        f"(сейчас: «{cur_label}»).\n\n"
        f"Отправьте «-» чтобы убрать подпись (останется просто цифра).\n"
        f"/cancel — отмена.",
        state=state,
    )


@router.callback_query(CreateExperiment.configuring, F.data == "lkt_reset")
async def on_likert_reset(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("Сброшено к дефолту.")
    data = await state.get_data()
    custom = dict(data.get("custom_likert") or {})
    custom.pop("main", None)
    await state.update_data(custom_likert=custom)
    await _show_likert_submenu(callback, state)


# ── редактирование инструкций фаз ──

def _template_has_buttons(tmpl_code: str) -> bool:
    """есть ли в шаблоне хоть одна фаза с response_type="buttons".

    Используем для скрытия кнопочно-специфичных опций в конфиге
    (например, рандомизации позиций кнопок) для шаблонов с
    open_text/voice/likert.
    """
    tmpl = tmpl_registry.get_template(tmpl_code) or {}
    build_fn = tmpl.get("build_phase")
    phases_info = tmpl.get("phases_info") or ["Основная фаза"]
    if not build_fn:
        # free_form и т.п. — на всякий случай показываем опцию
        return True
    for i in range(len(phases_info)):
        try:
            phase = build_fn([], {}, i) or {}
            settings = phase.get("settings", {}) or {}
            # SPR — единственная кнопка «Далее», шаффлить нечего;
            # Maze сам мешает target/distractor попробно (см. build_maze),
            # глобальная рандомизация для него тоже бессмысленна.
            if settings.get("is_spr") or settings.get("is_maze"):
                continue
            if phase.get("response_type") in ("buttons", "buttons_then_text"):
                return True
        except Exception:
            # если шаблон не может построить фазу без trials — допускаем,
            # что кнопки могут быть, не прячем опцию
            return True
    return False


def _get_default_instruction(tmpl_code: str, phase_index: int) -> str:
    """получить дефолтную инструкцию для фазы, вызвав build_phase с пустыми trials"""
    tmpl = tmpl_registry.get_template(tmpl_code) or {}
    build_fn = tmpl.get("build_phase")
    if not build_fn:
        return ""
    try:
        phase = build_fn([], {}, phase_index)
        return phase.get("instruction", "") or ""
    except Exception:
        return ""


def _get_current_instruction(data: dict, tmpl_code: str, phase_index: int) -> str:
    """вернуть кастомную инструкцию или дефолт"""
    custom = data.get("custom_instructions") or {}
    val = custom.get(phase_index)
    if val is None:
        val = custom.get(str(phase_index))
    if isinstance(val, str) and val.strip():
        return val
    return _get_default_instruction(tmpl_code, phase_index)


def _build_instructions_text_and_kb(data: dict) -> tuple[str, InlineKeyboardMarkup]:
    tmpl_code = data.get("template_type", "")
    # для новых экспериментов on_template_chosen не кладёт phases_info
    # в state — берём из реестра. _csv_template_phases уже умеет это.
    phases_info = data.get("phases_info") or _csv_template_phases(data)
    text_lines = [
        "<b>Инструкции фаз</b>\n",
        "Текст, который респондент видит перед стимулами. "
        "Можно переопределить для каждой фазы.\n",
    ]
    buttons = []
    for i, name in enumerate(phases_info):
        current = _get_current_instruction(data, tmpl_code, i)
        preview = (current[:60] + "…") if len(current) > 60 else current
        custom_marker = "✏️" if (data.get("custom_instructions") or {}).get(i) or \
                                (data.get("custom_instructions") or {}).get(str(i)) else "  "
        text_lines.append(f"<b>{i+1}. {name}</b>\n{preview or '<i>(пусто)</i>'}\n")
        buttons.append([InlineKeyboardButton(
            text=f"{custom_marker} Изменить фазу {i+1}",
            callback_data=f"instr_edit_{i}",
        )])
    buttons.append([InlineKeyboardButton(
        text="↩️ Сбросить все к дефолту", callback_data="instr_reset",
    )])
    buttons.append([InlineKeyboardButton(
        text="← Назад к настройкам", callback_data="cfg_back",
    )])
    return "\n".join(text_lines), InlineKeyboardMarkup(inline_keyboard=buttons)


async def _show_instructions_submenu(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    text, kb = _build_instructions_text_and_kb(data)
    await _render_screen(callback, text, kb, state=state)


async def _send_instructions_submenu(message: types.Message, state: FSMContext):
    data = await state.get_data()
    text, kb = _build_instructions_text_and_kb(data)
    await _render_screen(message, text, kb, state=state)


@router.callback_query(CreateExperiment.configuring, F.data == "cfg_instructions")
async def on_cfg_instructions(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    # сбрасываем waiting-флаги — в т.ч. на случай возврата по кнопке «Отмена»
    # из режима редактирования инструкции, чтобы следующий ввод не залетел
    # как недозавершённый
    await state.update_data(
        waiting_button_edit=None,
        waiting_likert_edit=None,
        waiting_instruction_edit=None,
        waiting_description_edit=False,
        waiting_timeout=False,
    )
    await _show_instructions_submenu(callback, state)


@router.callback_query(CreateExperiment.configuring, F.data.startswith("instr_edit_"))
async def on_instruction_edit(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    phase_idx = int(callback.data.replace("instr_edit_", ""))
    data = await state.get_data()
    tmpl_code = data.get("template_type", "")
    current = _get_current_instruction(data, tmpl_code, phase_idx)
    await state.update_data(waiting_instruction_edit={"phase_index": phase_idx})
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cfg_instructions")],
    ])
    await _render_screen(
        callback,
        f"Введите новую инструкцию для фазы {phase_idx + 1}.\n\n"
        f"<b>Сейчас:</b>\n{current}\n\n"
        f"Отправьте «-» чтобы сбросить к дефолту шаблона.",
        kb,
        state=state,
    )


@router.callback_query(CreateExperiment.configuring, F.data == "instr_reset")
async def on_instruction_reset_all(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("Все инструкции сброшены.")
    await state.update_data(custom_instructions={})
    await _show_instructions_submenu(callback, state)


# ── редактирование приветствия (description) ──

@router.callback_query(CreateExperiment.configuring, F.data == "cfg_description")
async def on_cfg_description(callback: types.CallbackQuery, state: FSMContext):
    """запросить новое приветственное сообщение"""
    await callback.answer()
    data = await state.get_data()
    current = data.get("description", "") or "<i>(пусто)</i>"
    await state.update_data(waiting_description_edit=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cfg_back")],
    ])
    await _render_screen(
        callback,
        "Введите <b>приветственное сообщение</b>. Его увидит респондент, "
        "когда перейдёт по ссылке на эксперимент — до инструкций и "
        "стимулов.\n\n"
        f"<b>Сейчас:</b>\n{current}",
        kb,
        state=state,
    )


@router.callback_query(CreateExperiment.configuring, F.data == "cfg_timeout")
async def ask_timeout(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await _render_screen(
        callback,
        "Введите тайм-аут в секундах (0 — отключить).\n\n"
        "/cancel — отмена.",
        state=state,
    )
    await state.set_state(CreateExperiment.configuring)
    await state.update_data(waiting_timeout=True)


@router.message(CreateExperiment.configuring, F.text)
async def on_config_text(message: types.Message, state: FSMContext):
    """обработка текстового ввода в режиме настроек (тайм-аут, подписи)"""
    data = await state.get_data()
    text = message.text.strip()

    # тайм-аут
    if data.get("waiting_timeout"):
        try:
            val = int(text)
            await state.update_data(
                time_limit=val if val > 0 else None,
                waiting_timeout=False,
            )
        except ValueError:
            await message.answer("Введите целое число.")
            return
        await show_settings_submenu(message, state)
        return

    # количество листов
    if data.get("waiting_lists_count"):
        try:
            val = int(text)
        except ValueError:
            await message.answer("Введите целое число.")
            return
        if val < 1 or val > 20:
            await message.answer("Число листов должно быть от 1 до 20.")
            return
        # удаляем CSV-слоты, которые перестали существовать при новом lists_count
        csv_data = dict(data.get("csv_data") or {})
        pruned = {
            k: v for k, v in csv_data.items()
            if (k.split("_") + ["", ""])[1].isdigit()
            and 1 <= int(k.split("_")[1]) <= val
        }
        await state.update_data(
            lists_count=val,
            use_lists=val >= 2,
            csv_data=pruned,
            waiting_lists_count=False,
        )
        await show_settings_submenu(message, state)
        return

    # редактирование метки кнопки
    btn_req = data.get("waiting_button_edit")
    if btn_req:
        key = btn_req.get("key", "main")
        idx = btn_req.get("index", -1)
        tmpl_code = data.get("template_type", "")
        current = _get_current_buttons(data, tmpl_code, key)
        if 0 <= idx < len(current):
            if len(text) > 64:
                await message.answer(
                    "Слишком длинная метка (Telegram ограничивает ~64 символа)."
                )
                return
            current[idx] = text
            custom = dict(data.get("custom_buttons") or {})
            custom[key] = current
            await state.update_data(
                custom_buttons=custom, waiting_button_edit=None,
            )
            await message.answer(f"✅ Кнопка №{idx + 1} обновлена: «{text}»")
        else:
            await state.update_data(waiting_button_edit=None)
        # возвращаем подменю кнопок
        await _send_buttons_submenu(message, state)
        return

    # редактирование инструкции фазы
    instr_req = data.get("waiting_instruction_edit")
    if instr_req:
        phase_idx = instr_req.get("phase_index")
        custom = dict(data.get("custom_instructions") or {})
        if text == "-":
            custom.pop(phase_idx, None)
            custom.pop(str(phase_idx), None)
            await message.answer(
                f"✅ Инструкция фазы {phase_idx + 1} сброшена к дефолту."
            )
        else:
            if len(text) > 3000:
                await message.answer("Слишком длинная инструкция (> 3000 символов).")
                return
            custom[str(phase_idx)] = text
            await message.answer(f"✅ Инструкция фазы {phase_idx + 1} обновлена.")
        await state.update_data(
            custom_instructions=custom, waiting_instruction_edit=None,
        )
        await _send_instructions_submenu(message, state)
        return

    # редактирование приветствия
    if data.get("waiting_description_edit"):
        if len(text) > 3000:
            await message.answer("Слишком длинное сообщение (> 3000 символов).")
            return
        await state.update_data(
            description=text, waiting_description_edit=False,
        )
        await message.answer("✅ Приветственное сообщение обновлено.")
        await show_config_menu(message, state)
        return

    # редактирование подписи Likert
    lkt_req = data.get("waiting_likert_edit")
    if lkt_req:
        key = lkt_req.get("key", "main")
        pos = lkt_req.get("pos")
        custom = dict(data.get("custom_likert") or {})
        main = dict(custom.get(key) or {})
        labels = dict(main.get("labels") or {})
        if text == "-":
            labels.pop(str(pos), None)
        else:
            if len(text) > 64:
                await message.answer("Слишком длинная подпись.")
                return
            labels[str(pos)] = text
        main["labels"] = labels
        custom[key] = main
        await state.update_data(
            custom_likert=custom, waiting_likert_edit=None,
        )
        await message.answer(f"✅ Позиция {pos} обновлена.")
        await _send_likert_submenu(message, state)
        return


# ── загрузка CSV (по фазам и листам) ──

def _csv_template_phases(data: dict) -> list[str]:
    """вернуть список фаз шаблона для текущего эксперимента."""
    template_type = data.get("template_type", "free_form")
    tmpl_info = tmpl_registry.get_template(template_type)
    phases_info = ["Основная фаза"]
    if tmpl_info:
        phases_info = tmpl_info.get("phases_info", ["Основная фаза"]) or ["Основная фаза"]
    return phases_info


def _build_csv_manifest(data: dict) -> tuple[str, InlineKeyboardMarkup]:
    """собрать текст и клавиатуру manifest-меню CSV.

    Каждая (фаза × лист) — отдельная кнопка с галочкой, если файл уже
    загружен. Все рендеры через _render_screen, поэтому StaleMenuGuard
    не блокирует клики.
    """
    phases_info = _csv_template_phases(data)
    lists_count = max(int(data.get("lists_count", 1) or 1), 1)
    csv_data = data.get("csv_data") or {}

    lines = ["<b>Загрузка CSV</b>", ""]
    if lists_count > 1 and len(phases_info) > 1:
        lines.append(f"Фаз: {len(phases_info)}, листов: {lists_count}.")
    elif lists_count > 1:
        lines.append(f"Листов: {lists_count}.")
    elif len(phases_info) > 1:
        lines.append(f"Фаз: {len(phases_info)}.")
    lines.append("Нажмите на слот, чтобы загрузить или заменить файл.")

    buttons: list[list[InlineKeyboardButton]] = []
    total = 0
    done = 0
    for ph in range(1, len(phases_info) + 1):
        for lst in range(1, lists_count + 1):
            total += 1
            key = f"{ph}_{lst}"
            uploaded = key in csv_data
            if uploaded:
                done += 1
            mark = "✅" if uploaded else "⬜"
            phase_name = phases_info[ph - 1]
            if lists_count > 1 and len(phases_info) > 1:
                label = f"{mark} Фаза {ph} · лист {lst}"
            elif lists_count > 1:
                label = f"{mark} Лист {lst}"
            elif len(phases_info) > 1:
                label = f"{mark} Фаза {ph} ({phase_name})"
            else:
                label = f"{mark} CSV-файл"
            if uploaded:
                label += f" — {len(csv_data[key])}"
            buttons.append([InlineKeyboardButton(
                text=label, callback_data=f"csv_slot_{ph}_{lst}",
            )])

    lines.append("")
    lines.append(f"Загружено: {done}/{total}")

    buttons.append([InlineKeyboardButton(
        text="✅ Готово", callback_data="csv_done",
    )])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


async def _show_csv_manifest(target, state: FSMContext):
    """показать manifest-меню (target — CallbackQuery или Message)."""
    data = await state.get_data()
    text, kb = _build_csv_manifest(data)
    await _render_screen(target, text, kb, state=state)


@router.callback_query(CreateExperiment.configuring, F.data == "cfg_upload_csv")
async def ask_csv(callback: types.CallbackQuery, state: FSMContext):
    """вход в загрузку CSV: показываем manifest со всеми слотами."""
    await callback.answer()
    data = await state.get_data()
    phases_info = _csv_template_phases(data)
    lists_count = max(int(data.get("lists_count", 1) or 1), 1)

    # подчищаем csv_data от слотов вне текущей размерности
    # (могло остаться после уменьшения lists_count)
    csv_data = dict(data.get("csv_data") or {})
    valid = {f"{ph}_{lst}"
             for ph in range(1, len(phases_info) + 1)
             for lst in range(1, lists_count + 1)}
    csv_data = {k: v for k, v in csv_data.items() if k in valid}

    await state.update_data(
        phases_info=phases_info,
        csv_data=csv_data,
        current_phase_num=None,
        current_list=None,
    )
    await state.set_state(CreateExperiment.uploading_csv)
    await _show_csv_manifest(callback, state)


@router.message(CreateExperiment.uploading_csv, F.document)
async def on_csv_uploaded(message: types.Message, state: FSMContext, bot: Bot):
    """обработка загруженного CSV-файла"""
    data = await state.get_data()
    current_phase_num = data.get("current_phase_num")
    current_list = data.get("current_list")
    if not current_phase_num or not current_list:
        # пользователь прислал файл, не выбрав слот в manifest
        await message.answer(
            "Сначала выберите слот в меню — нажмите на нужную фазу/лист, "
            "и потом отправьте файл."
        )
        await _show_csv_manifest(message, state)
        return

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

    template_type = data.get("template_type", "free_form")

    # валидация и маппинг (с учетом phase_csv_mappings для многофазных шаблонов)
    tmpl_info = tmpl_registry.get_template(template_type)
    if tmpl_info:
        # единая валидация через утилиту — покрывает колонки, разделитель,
        # пустые стимулы, специфику шаблона
        from utils.validators import validate_csv_for_template
        errors = validate_csv_for_template(template_type, rows, current_phase_num)
        # разделим «критичные» ошибки и «предупреждения» (содержат слово «пропущены»)
        critical = [e for e in errors if "пропущены" not in e]
        warnings = [e for e in errors if "пропущены" in e]
        if critical:
            await message.answer(
                "❌ Ошибки в CSV — файл не загружен:\n"
                + "\n".join(f"• {e}" for e in critical)
            )
            return
        if warnings:
            await message.answer(
                "⚠️ Предупреждения:\n" + "\n".join(f"• {w}" for w in warnings)
            )

        phase_mappings = tmpl_info.get("phase_csv_mappings", {})
        if current_phase_num in phase_mappings:
            pm = phase_mappings[current_phase_num]
            mapping = {k: v for k, v in pm.items() if k != "required_columns"}
        else:
            mapping = tmpl_info.get("csv_mapping", {})
    else:
        mapping = auto_detect_mapping(rows)

    trials = csv_parser.rows_to_trials(rows, mapping)

    # пост-проверка: в trials должно быть содержание
    non_empty = [t for t in trials if str(t.get("stimulus_content", "")).strip()]
    if not non_empty:
        await message.answer(
            "❌ После парсинга не осталось ни одного стимула с содержимым. "
            "Похоже, колонки CSV не соответствуют шаблону. "
            f"Ожидается колонка стимула: «{mapping.get('stimulus_content', 'stimulus')}»."
        )
        return
    # отбрасываем пустые строки, чтобы они не попадали в эксперимент
    trials = non_empty

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
        f"✅ Загружено {count} строк для фазы {current_phase_num} ({phase_name})"
        + (f", лист {current_list}" if int(data.get("lists_count", 1) or 1) > 1 else "")
        + f".\nКолонки: {', '.join(columns)}"
    )

    # сбрасываем «активный» слот и возвращаемся в manifest
    await state.update_data(current_phase_num=None, current_list=None)
    await _show_csv_manifest(message, state)


@router.callback_query(CreateExperiment.uploading_csv, F.data.startswith("csv_slot_"))
async def on_csv_slot(callback: types.CallbackQuery, state: FSMContext):
    """клик по слоту в manifest — спрашиваем файл для этой (фаза, лист)."""
    await callback.answer()
    parts = callback.data.replace("csv_slot_", "").split("_")
    try:
        ph, lst = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        await _show_csv_manifest(callback, state)
        return

    data = await state.get_data()
    phases_info = _csv_template_phases(data)
    lists_count = max(int(data.get("lists_count", 1) or 1), 1)
    phase_name = phases_info[ph - 1] if 1 <= ph <= len(phases_info) else f"Фаза {ph}"

    await state.update_data(current_phase_num=ph, current_list=str(lst))

    if lists_count > 1 and len(phases_info) > 1:
        prompt = f"Отправьте CSV для фазы {ph} ({phase_name}), лист {lst}."
    elif lists_count > 1:
        prompt = f"Отправьте CSV для листа {lst}."
    elif len(phases_info) > 1:
        prompt = f"Отправьте CSV для фазы {ph} ({phase_name})."
    else:
        prompt = "Отправьте CSV-файл со стимулами."

    csv_data = data.get("csv_data") or {}
    key = f"{ph}_{lst}"
    if key in csv_data:
        prompt += (
            f"\n\n<i>В этом слоте уже загружено {len(csv_data[key])} строк. "
            "Если отправите новый файл — он заменит текущий.</i>"
        )

    kb_rows: list[list[InlineKeyboardButton]] = []
    template_type = data.get("template_type", "free_form")
    # пример заполнения csv — для конкретной фазы (выбранного слота).
    # фазы шаблона могут иметь разные форматы (например, в probe_recognition
    # фаза 2 содержит дополнительную колонку correct), поэтому пример
    # подбирается per-phase: registry.get_example_csv_path(code, phase).
    if tmpl_registry.get_example_csv_path(template_type, ph):
        kb_rows.append([InlineKeyboardButton(
            text="📄 Прислать пример заполнения файла",
            callback_data="csv_example",
        )])
    kb_rows.append([InlineKeyboardButton(
        text="❌ Отмена", callback_data="csv_back_to_manifest",
    )])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await _render_screen(callback, prompt, kb, state=state)


@router.callback_query(CreateExperiment.uploading_csv, F.data == "csv_example")
async def on_csv_example(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    """прислать csv-пример(ы) для выбранного слота (с учётом фазы).

    шаблоны, у которых разные настройки требуют разных примеров (например,
    Acceptability Judgment с одиночной/совместной подачей), могут
    зарегистрировать несколько файлов через `extra_examples`. caption
    берётся из `example_caption` шаблона, либо общий дефолтный текст."""
    await callback.answer()
    data = await state.get_data()
    template_type = data.get("template_type", "free_form")
    phase = int(data.get("current_phase_num") or 1)
    paths = tmpl_registry.get_example_csv_paths(template_type, phase)
    if not paths:
        await callback.message.answer("Для этого шаблона примера нет.")
        return
    caption = tmpl_registry.get_example_caption(template_type, phase) or (
        "Пример заполнения CSV для этой фазы. Скачайте, "
        "адаптируйте под свой материал и загрузите обратно."
    )
    # сначала отдельным сообщением — пояснение, потом сами файлы.
    # если файлов больше одного — шлём их альбомом (одним «бабблом»),
    # иначе одиночным send_document. так пользователь видит сначала
    # инструкцию, а потом компактную пачку CSV под ней.
    await callback.message.answer(caption)
    if len(paths) == 1:
        await bot.send_document(
            callback.from_user.id,
            FSInputFile(paths[0], filename=os.path.basename(paths[0])),
        )
    else:
        media = [
            InputMediaDocument(
                media=FSInputFile(p, filename=os.path.basename(p)),
            )
            for p in paths
        ]
        await bot.send_media_group(callback.from_user.id, media=media)


@router.callback_query(CreateExperiment.uploading_csv, F.data == "csv_back_to_manifest")
async def on_csv_back_to_manifest(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(current_phase_num=None, current_list=None)
    await _show_csv_manifest(callback, state)


@router.callback_query(CreateExperiment.uploading_csv, F.data == "csv_done")
async def on_csv_done(callback: types.CallbackQuery, state: FSMContext):
    """выйти из manifest CSV в основное меню настроек.

    lists_count теперь хранится явной настройкой; ничего не пересчитываем.
    Полнота загрузки проверяется validate_experiment при активации.
    """
    await callback.answer()
    await state.update_data(current_phase_num=None, current_list=None)
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
        build_fn = tmpl_info.get("build_phase")
        if build_fn:
            phases = []
            custom_instr = data.get("custom_instructions") or {}
            for phase_num in sorted(trials_by_phase.keys()):
                phase_trials = trials_by_phase[phase_num]
                phase_index = phase_num - 1
                phase = build_fn(phase_trials, data, phase_index)
                # применяем пользовательскую инструкцию, если задана
                override = custom_instr.get(phase_index) or custom_instr.get(str(phase_index))
                if isinstance(override, str) and override.strip():
                    phase["instruction"] = override
                phases.append(phase)
        else:
            # fallback — старый формат (не должен использоваться)
            all_trials = []
            for phase_num in sorted(trials_by_phase.keys()):
                all_trials.extend(trials_by_phase[phase_num])
            phases = [tmpl_info["build_phases"](all_trials, data)]
    else:
        # free_form — фазы уже собраны в free_form_phases
        ff_phases = data.get("free_form_phases", [])
        if ff_phases:
            phases = ff_phases
        else:
            # fallback — одна фаза со всеми пробами
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

    # режим редактирования — делаем update, не создаём новый
    editing_id = data.get("editing_id")

    # lists_count теперь хранится явной настройкой; use_lists — производная
    lists_count = max(int(data.get("lists_count", 1) or 1), 1)
    use_lists = lists_count >= 2

    # поля, которые меняются и при create, и при update
    mutable_fields = {
        "title": data.get("title", "Без названия"),
        "description": data.get("description", ""),
        "template_type": template_type,
        "phases": phases,
        "randomize_trials": data.get("randomize", False),
        "randomize_button_positions": data.get("randomize_button_positions", False),
        "delete_previous_trials": data.get("delete_previous_trials", True),
        "use_lists": use_lists,
        "lists_count": lists_count,
        "time_limit": data.get("time_limit"),
        "collect_demographics": data.get("demographics_mode", "off") != "off",
        "demographics_type": "custom" if data.get("demographics_mode") == "custom" else "standard",
        "demographics_custom": data.get("demographics_custom", []),
        "allow_repeat": data.get("allow_repeat", False),
        "custom_buttons": data.get("custom_buttons") or {},
        "custom_likert": data.get("custom_likert") or {},
        "custom_instructions": data.get("custom_instructions") or {},
        "presentation_mode": data.get("presentation_mode", "single"),
        # сохраняем «сырой» csv_data рядом с phases. при правке черновика
        # некоторые шаблоны (maze) сильно меняют структуру в build_phase
        # — их build не идемпотентен, и пересборка из phase.trials дала бы
        # рекурсивно-склеенные стимулы. Cырые ряды позволяют сохранить
        # исходные данные и пересобрать из них корректно.
        "csv_data_raw": data.get("csv_data") or {},
    }

    if editing_id:
        # update: не трогаем owner_id, deep_link_id, status, export_settings
        await repo.update_experiment(editing_id, mutable_fields)
        exp_id = editing_id
        logger.info("эксперимент %s обновлён пользователем %s", exp_id, callback.from_user.id)
        await state.clear()
        await show_experiment_detail(
            callback, exp_id, banner="✅ Изменения сохранены.",
            state=state,
        )
        return

    # create — новый эксперимент
    deep_link_id = "exp_" + secrets.token_urlsafe(8)
    experiment_data = {
        **mutable_fields,
        "owner_id": callback.from_user.id,
        "status": "draft",
        "export_settings": {},
        "deep_link_id": deep_link_id,
    }

    exp_id = await repo.create_experiment(experiment_data)
    await state.clear()

    logger.info("эксперимент %s создан пользователем %s", exp_id, callback.from_user.id)

    await show_experiment_detail(
        callback, exp_id,
        banner=f"✅ «{data.get('title')}» сохранён как черновик.",
        state=state,
    )


# ── детали эксперимента ──

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

    status_text = {"draft": "Черновик", "active": "Активен", "archived": "Архив"}
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
        delete_previous_trials=exp.get("delete_previous_trials", True),
        demographics_mode=demo_mode,
        demographics_custom=exp.get("demographics_custom", []),
        time_limit=exp.get("time_limit"),
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

    status_text = {"draft": "Черновик", "active": "Активен", "archived": "Архив"}
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
async def on_delete_do(callback: types.CallbackQuery, state: FSMContext):
    """шаг 2: выгрузить CSV (если есть данные) и удалить эксперимент."""
    await callback.answer()
    exp_id = callback.data.replace("del_do_", "")
    exp = await repo.get_experiment(exp_id)
    if not exp:
        await _render_screen(callback, "Эксперимент не найден.", state=state)
        return

    title = exp.get("title", "experiment")

    # сначала пробуем выгрузить CSV — если упадёт, не удаляем данные
    try:
        csv_text = await export_util.export_experiment_csv(exp_id)
    except Exception:
        logger.exception("ошибка экспорта CSV перед удалением %s", exp_id)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="← К эксперименту",
                callback_data=f"exp_detail_{exp_id}",
            )],
        ])
        await _render_screen(
            callback,
            "❌ Не удалось сгенерировать CSV. Удаление отменено, "
            "чтобы не потерять данные. Попробуйте ещё раз позже.",
            kb,
            state=state,
        )
        return

    # удаляем сообщение-подтверждение, чтобы итоговое меню оказалось
    # ниже CSV-файла, а не над ним. (отредактировать его «на месте»
    # нельзя — оно осталось бы выше документа в ленте чата.)
    try:
        await callback.message.delete()
    except Exception:
        pass

    csv_note = ""
    if csv_text and csv_text.strip():
        file = BufferedInputFile(
            csv_text.encode("utf-8-sig"),
            filename=f"results_{exp_id}.csv",
        )
        await callback.message.answer_document(
            file,
            caption=f"Результаты «{title}» перед удалением.",
        )
        csv_note = "📥 CSV с результатами — выше отдельным файлом.\n\n"
    else:
        csv_note = "ℹ️ Данных для экспорта не было — CSV не прислан.\n\n"

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
async def on_export(callback: types.CallbackQuery, state: FSMContext):
    exp_id = callback.data.replace("export_", "")

    csv_text = await export_util.export_experiment_csv(exp_id)
    if not csv_text.strip():
        # экран не меняем — показываем тоаст-уведомление поверх кнопки
        await callback.answer("Нет данных для экспорта.", show_alert=True)
        return

    await callback.answer()
    file = BufferedInputFile(
        csv_text.encode("utf-8-sig"),
        filename=f"results_{exp_id}.csv",
    )
    # удаляем текущий экран (карточку или экран результатов), чтобы CSV
    # оказался выше итогового меню. иначе документ висит снизу, а
    # активное меню — сверху, неудобно.
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer_document(file, caption="Результаты эксперимента")
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
        icon = {"draft": "📝", "active": "🟢", "archived": "📦"}.get(exp["status"], "")
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
