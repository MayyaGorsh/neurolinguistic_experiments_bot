"""загрузка CSV со стимулами по фазам и листам.

UI устроен как «manifest»: каждая комбинация (фаза × лист) — отдельный
слот-кнопка с галочкой, если файл уже загружен. Клик по слоту выводит
запрос файла; пришедший CSV парсится, валидируется по шаблону и
складывается в state.csv_data под ключом f"{phase}_{list}".
"""

import os

from aiogram import Bot, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaDocument,
)

from utils import csv_parser

from handlers.researcher_common import (
    router,
    CreateExperiment,
    _csv_template_phases,
    _render_screen,
    auto_detect_mapping,
    tmpl_registry,
)


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
    from handlers.researcher_settings import show_config_menu
    await show_config_menu(callback, state)
