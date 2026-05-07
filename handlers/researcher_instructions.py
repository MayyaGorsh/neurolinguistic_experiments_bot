"""редактирование инструкций фаз и приветственного сообщения.

Инструкции — это текст, который респондент видит перед стимулами фазы.
Дефолт берётся из шаблона (build_phase возвращает поле "instruction"),
overrides хранятся в state как `custom_instructions: {phase_index: text}`.

Само принятие нового текста с клавиатуры — в researcher_text_input;
здесь только UI и хелперы доступа к текущей инструкции."""

from aiogram import types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.researcher_common import (
    router,
    CreateExperiment,
    _csv_template_phases,
    _render_screen,
    tmpl_registry,
)


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
