"""флоу создания эксперимента: выбор шаблона → ввод названия → ввод
приветственного сообщения → переход в меню настроек.

Дальше управление передаётся в handlers/researcher_settings.show_config_menu
(или, для free_form, в handlers/free_form.start_free_form).
"""

from aiogram import types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import FREE_EXPERIMENT_LIMIT
from db import repositories as repo
from models.user import is_premium_active
from handlers.researcher_common import (
    router,
    CreateExperiment,
    TEMPLATE_LIST,
    TEMPLATE_LABELS,
    TEMPLATE_DESCRIPTIONS,
    _render_screen,
)


@router.callback_query(F.data == "create_experiment")
async def on_create_experiment(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    # фримиум-лимит: считаем все эксперименты пользователя (черновики +
    # активные). у премиум-пользователей лимита нет.
    user = await repo.get_user(callback.from_user.id)
    if not is_premium_active(user):
        existing = await repo.count_experiments_by_owner(callback.from_user.id)
        if existing >= FREE_EXPERIMENT_LIMIT:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="⭐ Перейти на премиум", callback_data="premium_info",
                )],
                [InlineKeyboardButton(
                    text="Мои эксперименты", callback_data="my_experiments",
                )],
                [InlineKeyboardButton(
                    text="← В главное меню", callback_data="back_to_menu",
                )],
            ])
            await _render_screen(
                callback,
                f"В бесплатном тарифе доступно до {FREE_EXPERIMENT_LIMIT} экспериментов. "
                f"Сейчас у вас {existing}.\n\n"
                "Удалите ненужные эксперименты или перейдите на премиум, "
                "чтобы снять лимит.",
                kb,
                state=state,
            )
            return
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
    label = TEMPLATE_LABELS.get(template_code, template_code)
    description = TEMPLATE_DESCRIPTIONS.get(template_code, "")
    description_block = f"{description}\n\n" if description else ""
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="← Назад", callback_data="create_experiment"),
    ]])
    await _render_screen(
        callback,
        f"Шаблон: <b>{label}</b>\n\n"
        f"{description_block}"
        "Введите название эксперимента сообщением.",
        cancel_kb,
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
    # для всех шаблонов (включая free_form) — сразу в общее меню настроек.
    # Раньше free_form вёл в конструктор фаз напрямую, но это неудобно:
    # перед тем как грузить CSV по фазам, юзер хочет иметь возможность
    # включить листы (lists_count) — иначе пришлось бы перезаливать каждую
    # фазу. Конструктор фаз во free_form доступен из show_config_menu
    # кнопкой «🧱 Редактировать фазы».
    from handlers.researcher_settings import show_config_menu
    await show_config_menu(message, state)
