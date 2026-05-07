"""кастомизация ответных элементов:
- лейблов кнопок ответа (для шаблонов с default_response_options —
  сейчас это Picture Selection и Covered Box);
- размера и подписей шкалы Ликерта (для шаблонов с default_likert).

Хелперы _get_current_buttons / _get_current_likert и _send_*_submenu
дополнительно используются в researcher_text_input — после обработки
ввода с клавиатуры. Поэтому экспортируются."""

from aiogram import types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.researcher_common import (
    router,
    CreateExperiment,
    _render_screen,
    tmpl_registry,
)


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


# ── handlers: кнопки ──

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


# ── handlers: Likert ──

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
