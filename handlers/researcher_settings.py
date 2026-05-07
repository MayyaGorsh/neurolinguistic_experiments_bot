"""экраны настроек эксперимента:
- top-level меню (`show_config_menu`) — сводка настроек + кнопки действий;
- подменю «Настроить эксперимент» (`show_settings_submenu`) — все
  тогглы со значениями;
- хелперы _collect_settings_state / _settings_rows — единый источник
  правды о том, какие настройки применимы к текущему шаблону;
- хендлеры тогглов и «спросить значение» (тайм-аут, листы, тишина и т. д.);
  собственно ввод значений обрабатывает researcher_text_input.

`show_config_menu` экспортируется наружу — её зовут многие подмодули
(researcher_create, researcher_csv, researcher_demographics, researcher_save,
researcher_experiment) и handlers.free_form."""

from aiogram import types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.researcher_common import (
    router,
    CreateExperiment,
    _AJT_PRESENTATION_LABELS,
    _AJT_PRESENTATION_CYCLE,
    _ajt_has_stimulus2,
    _ajt_csv_has_response_options,
    _template_has_buttons,
    _reset_input_flags,
    _render_screen,
    tmpl_registry,
)


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
    idle_timeout = int(data.get("idle_timeout_seconds", 300) or 0)
    idle_label = (
        "продолжать с того же места" if idle_timeout <= 0
        else f"{idle_timeout} сек"
    )
    audio_silence = int(data.get("audio_silence_seconds", 0) or 0)
    audio_silence_label = "нет" if audio_silence <= 0 else f"{audio_silence} сек"
    is_audio_template = tmpl in ("forced_choice", "sentence_repetition")
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
        "idle_timeout": idle_timeout,
        "idle_label": idle_label,
        "audio_silence": audio_silence,
        "audio_silence_label": audio_silence_label,
        "is_audio_template": is_audio_template,
        "allow_repeat": allow_repeat,
        "presentation_mode": presentation_mode,
        "presentation_label": presentation_label,
        "has_buttons": _template_has_buttons(tmpl),
        "ajt_show_presentation": (
            tmpl == "acceptability_judgment" and _ajt_has_stimulus2(data)
        ),
    }


def _settings_rows(s: dict) -> list[tuple[str, str, str]]:
    """единый источник настроек: список (label, value, callback_data),
    отфильтрованный по применимости к текущему шаблону.

    используется и в сводке show_config_menu, и в кнопках
    show_settings_submenu — чтобы оба экрана показывали один и тот же
    набор и не разъезжались при добавлении новых настроек."""
    rows: list[tuple[str, str, str]] = [
        ("Рандомизация", "да" if s["randomize"] else "нет", "cfg_randomize"),
    ]
    if s["has_buttons"]:
        rows.append((
            "Рандомизация позиций кнопок",
            "да" if s["randomize_buttons"] else "нет",
            "cfg_randomize_buttons",
        ))
    if s["ajt_show_presentation"]:
        rows.append((
            "Режим подачи", s["presentation_label"], "cfg_presentation_mode",
        ))
    rows += [
        (
            "Чистить предыдущие пробы",
            "да" if s["delete_previous"] else "нет",
            "cfg_delete_previous",
        ),
        ("Распределение по листам", s["lists_label"], "cfg_lists"),
        ("Демография", s["demo_label"], "cfg_demographics"),
        ("Тайм-аут", s["timeout_value"], "cfg_timeout"),
        ("Перерыв до сброса", s["idle_label"], "cfg_idle_timeout"),
    ]
    if s["is_audio_template"]:
        rows.append((
            "Тишина в аудио", s["audio_silence_label"], "cfg_audio_silence",
        ))
    rows.append((
        "Повторное прохождение",
        "да" if s["allow_repeat"] else "нет",
        "cfg_repeat",
    ))
    return rows


async def show_config_menu(message_or_cb, state: FSMContext):
    """top-level меню: краткая сводка настроек + действия.
    тогглы — в show_settings_submenu. сводка моноширинная (<pre>) ради
    ровных колонок — кнопки Telegram рендерятся пропорционально, в
    <pre> можно выровнять по символам.

    список строк в сводке = список тогглов в подменю (один источник —
    _settings_rows). если настройка не применима к шаблону, она не
    отображается ни в сводке, ни в подменю."""
    data = await state.get_data()
    s = _collect_settings_state(data)
    tmpl = s["tmpl"]

    rows = _settings_rows(s)
    summary_rows = [(label, value) for label, value, _ in rows]
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
    они применимы.

    набор тогглов берётся из _settings_rows — того же источника, что и
    сводка в верхнем меню; экраны не могут разъехаться."""
    data = await state.get_data()
    s = _collect_settings_state(data)
    tmpl = s["tmpl"]

    text = "<b>Настройки эксперимента</b>"

    buttons: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(
            text=f"{label} — {value}", callback_data=cb,
        )]
        for label, value, cb in _settings_rows(s)
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
    "<b>⏸ Перерыв до сброса</b>\n"
    "Сколько секунд бездействия можно допустить, прежде чем сессия "
    "участника закроется. Если участник отошёл, а потом вернулся "
    "(перешёл по ссылке снова или просто нажал кнопку в чате) и с "
    "момента последней активности прошло больше этого значения — "
    "сессия помечается «прервана» и эксперимент придётся начать заново. "
    "По умолчанию 300 сек (5 мин). 0 — таймаут отключён, эксперимент "
    "всегда продолжается с того же места.\n\n"
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


@router.callback_query(CreateExperiment.configuring, F.data == "cfg_repeat")
async def toggle_repeat(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    await state.update_data(allow_repeat=not data.get("allow_repeat", False))
    await show_settings_submenu(callback, state)


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


@router.callback_query(CreateExperiment.configuring, F.data == "cfg_audio_silence")
async def ask_audio_silence(callback: types.CallbackQuery, state: FSMContext):
    """тишина в конце аудио-стимула. падится в файл при загрузке;
    при изменении настройки уже загруженные файлы перепакуются заново
    из оригиналов на сохранении эксперимента."""
    await callback.answer()
    await _render_screen(
        callback,
        "Сколько секунд тишины добавить в конец каждого аудио?\n"
        "0 — без тишины. Тишина добавляется в сам файл, поэтому "
        "длительность плеера будет включать её.\n\n"
        "/cancel — отмена.",
        state=state,
    )
    await state.set_state(CreateExperiment.configuring)
    await state.update_data(waiting_audio_silence=True)


@router.callback_query(CreateExperiment.configuring, F.data == "cfg_idle_timeout")
async def ask_idle_timeout(callback: types.CallbackQuery, state: FSMContext):
    """перерыв до сброса: через сколько секунд бездействия abandon-ить
    сессию участника. 0 — не закрывать, всегда продолжать."""
    await callback.answer()
    await _render_screen(
        callback,
        "Через сколько секунд бездействия закрывать сессию участника?\n"
        "0 — не закрывать, эксперимент всегда продолжается с того же места.\n\n"
        "/cancel — отмена.",
        state=state,
    )
    await state.set_state(CreateExperiment.configuring)
    await state.update_data(waiting_idle_timeout=True)
