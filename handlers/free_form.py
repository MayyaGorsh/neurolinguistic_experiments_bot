"""
free-form mode: исследователь создает эксперимент без шаблона,
добавляя произвольное число фаз с выбором stimulus type и response type.
"""

import logging

from aiogram import Router, Bot, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

router = Router()
logger = logging.getLogger("bot")


class FreeFormSetup(StatesGroup):
    adding_phase = State()
    choosing_stimulus_type = State()
    choosing_response_type = State()
    entering_instruction = State()
    uploading_phase_csv = State()
    configuring_buttons = State()


STIMULUS_TYPES = [
    ("text", "Текст"),
    ("audio", "Аудио"),
    ("image", "Изображение"),
    ("video", "Видео"),
]

RESPONSE_TYPES = [
    ("buttons", "Кнопки"),
    ("likert", "Шкала Ликерта"),
    ("multiple_choice", "Множественный выбор"),
    ("open_text", "Открытый текст"),
    ("voice", "Голосовое сообщение"),
]


async def start_free_form(callback: types.CallbackQuery, state: FSMContext):
    """начать создание free-form эксперимента"""
    data = await state.get_data()
    phases = data.get("free_form_phases", [])
    await state.update_data(free_form_phases=phases)
    await show_phases_summary(callback.message, phases)
    await state.set_state(FreeFormSetup.adding_phase)


async def show_phases_summary(message, phases: list):
    """показать текущий список фаз и кнопки управления"""
    if phases:
        text = "<b>Фазы эксперимента:</b>\n\n"
        for i, p in enumerate(phases):
            text += (
                f"{i + 1}. {p.get('title', 'Фаза')}\n"
                f"   Стимул: {p['stimulus_type']}, Ответ: {p['response_type']}\n"
                f"   Проб: {len(p.get('trials', []))}\n\n"
            )
    else:
        text = "Фаз пока нет."

    buttons = [
        [InlineKeyboardButton(text="➕ Добавить фазу", callback_data="ff_add_phase")],
    ]
    if phases:
        buttons.append([InlineKeyboardButton(
            text="✅ Готово — сохранить",
            callback_data="ff_done",
        )])
    buttons.append([InlineKeyboardButton(text="← Отмена", callback_data="back_to_menu")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(text, reply_markup=kb)


@router.callback_query(FreeFormSetup.adding_phase, F.data == "ff_add_phase")
async def on_add_phase(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    buttons = []
    for code, label in STIMULUS_TYPES:
        buttons.append([InlineKeyboardButton(
            text=label, callback_data=f"ff_stim_{code}"
        )])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.answer("Выберите тип стимула для новой фазы:", reply_markup=kb)
    await state.set_state(FreeFormSetup.choosing_stimulus_type)


@router.callback_query(FreeFormSetup.choosing_stimulus_type, F.data.startswith("ff_stim_"))
async def on_stimulus_chosen(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    stim_type = callback.data.replace("ff_stim_", "")
    await state.update_data(current_phase_stim=stim_type)

    buttons = []
    for code, label in RESPONSE_TYPES:
        buttons.append([InlineKeyboardButton(
            text=label, callback_data=f"ff_resp_{code}"
        )])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.answer("Выберите тип ответа:", reply_markup=kb)
    await state.set_state(FreeFormSetup.choosing_response_type)


@router.callback_query(FreeFormSetup.choosing_response_type, F.data.startswith("ff_resp_"))
async def on_response_chosen(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    resp_type = callback.data.replace("ff_resp_", "")
    await state.update_data(current_phase_resp=resp_type)
    await callback.message.answer("Введите инструкцию для этой фазы:")
    await state.set_state(FreeFormSetup.entering_instruction)


@router.message(FreeFormSetup.entering_instruction, F.text)
async def on_instruction_entered(message: types.Message, state: FSMContext):
    await state.update_data(current_phase_instruction=message.text.strip())
    await message.answer(
        "Отправьте CSV-файл со стимулами для этой фазы.\n"
        "Первая колонка — стимул, остальные — варианты ответа "
        "(правильный помечается * в начале)."
    )
    await state.set_state(FreeFormSetup.uploading_phase_csv)


@router.message(FreeFormSetup.uploading_phase_csv, F.document)
async def on_phase_csv(message: types.Message, state: FSMContext, bot: Bot):
    doc = message.document
    if not doc.file_name.lower().endswith(".csv"):
        await message.answer("Отправьте файл в формате CSV.")
        return

    from utils import csv_parser

    file = await bot.download(doc)
    content = file.read()

    try:
        rows = csv_parser.parse_csv_bytes(content)
    except Exception as e:
        await message.answer(f"Ошибка чтения CSV: {e}")
        return

    if not rows:
        await message.answer("Файл пуст.")
        return

    # автоматический маппинг
    from handlers.researcher import auto_detect_mapping
    mapping = auto_detect_mapping(rows)
    trials = csv_parser.rows_to_trials(rows, mapping)

    data = await state.get_data()
    phases = data.get("free_form_phases", [])
    phase_index = len(phases)

    new_phase = {
        "phase_index": phase_index,
        "title": f"Фаза {phase_index + 1}",
        "instruction": data.get("current_phase_instruction", ""),
        "stimulus_type": data.get("current_phase_stim", "text"),
        "response_type": data.get("current_phase_resp", "buttons"),
        "trials": trials,
        "randomize_order": False,
        "time_limit": None,
        "settings": {},
    }
    phases.append(new_phase)
    await state.update_data(free_form_phases=phases)

    await message.answer(f"Фаза добавлена: {len(trials)} проб.")
    await show_phases_summary(message, phases)
    await state.set_state(FreeFormSetup.adding_phase)


@router.callback_query(FreeFormSetup.adding_phase, F.data == "ff_done")
async def on_free_form_done(callback: types.CallbackQuery, state: FSMContext):
    """сохранить free-form эксперимент — передаем управление в researcher.on_save_draft"""
    await callback.answer()

    # фазы уже собраны в free_form_phases — переходим к настройкам
    from handlers.researcher import show_config_menu
    await show_config_menu(callback, state)
