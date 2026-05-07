"""флоу создания эксперимента: выбор шаблона → ввод названия → ввод
приветственного сообщения → переход в меню настроек.

Дальше управление передаётся в handlers/researcher_settings.show_config_menu
(или, для free_form, в handlers/free_form.start_free_form).
"""

from aiogram import types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

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

    from handlers.researcher_settings import show_config_menu
    await show_config_menu(message, state)
