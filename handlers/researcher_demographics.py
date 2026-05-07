"""режимы демографической анкеты (off / standard / custom).

Custom-режим: исследователь загружает CSV с вопросами; здесь же лежит
строка-пример (DEMO_EXAMPLE_CSV), которую мы шлём по нажатию
«Показать пример»."""

from aiogram import Bot, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from utils import csv_parser

from handlers.researcher_common import (
    router,
    CreateExperiment,
    _render_screen,
)


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
    from handlers.researcher_settings import show_settings_submenu
    await show_settings_submenu(callback, state)


@router.callback_query(CreateExperiment.configuring, F.data == "demo_standard")
async def demo_set_standard(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(demographics_mode="standard", demographics_custom=[])
    from handlers.researcher_settings import show_settings_submenu
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
    from handlers.researcher_settings import show_settings_submenu
    await show_settings_submenu(message, state)
