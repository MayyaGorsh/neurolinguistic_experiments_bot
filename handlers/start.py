import logging

from aiogram import Router, Bot, types
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db import repositories as repo
from engine import runner

router = Router()
logger = logging.getLogger("bot")


@router.message(CommandStart(deep_link=True))
async def cmd_start_deep_link(message: types.Message, command: CommandObject, bot: Bot):
    """обработка перехода по ссылке на эксперимент: /start exp_<id>"""
    args = command.args or ""
    logger.info("deep link от %s: %s", message.from_user.id, args)

    if not args.startswith("exp_"):
        await cmd_start(message)
        return

    deep_link_id = args  # например exp_abc123

    # ищем эксперимент по deep link
    experiment = await repo.get_experiment_by_link(deep_link_id)
    if not experiment:
        await message.answer("Эксперимент не найден или ссылка устарела.")
        return

    if experiment["status"] != "active":
        await message.answer("Этот эксперимент сейчас неактивен.")
        return

    # создаем или находим пользователя как участника
    user_data = {
        "username": message.from_user.username,
        "first_name": message.from_user.first_name,
        "last_name": message.from_user.last_name,
        "role": "participant",
    }
    await repo.get_or_create_user(message.from_user.id, user_data)

    # проверяем, не проходил ли уже
    exp_id = str(experiment["_id"])
    existing = await repo.get_active_session(message.from_user.id, exp_id)
    if existing:
        await message.answer(
            "У вас есть незавершенная сессия. "
            "Продолжаем с того места, где вы остановились."
        )
        # возобновляем сессию: берем подготовленные фазы и показываем текущую пробу
        prepared = existing.get("prepared_phases") or experiment["phases"]
        exp_copy = dict(experiment)
        exp_copy["phases"] = prepared
        await runner.present_trial(bot, message.from_user.id, existing, exp_copy)
        return

    if not experiment.get("allow_repeat", False):
        # проверяем завершенные сессии
        sessions = await repo.get_sessions_by_experiment(exp_id)
        finished = [s for s in sessions
                    if s["telegram_id"] == message.from_user.id
                    and s["status"] == "completed"]
        if finished:
            await message.answer("Вы уже проходили этот эксперимент. Повторное прохождение не предусмотрено.")
            return

    # показываем приветствие
    text = (
        f"<b>{experiment['title']}</b>\n\n"
        f"{experiment.get('description', '')}\n\n"
        "Нажмите «Начать», чтобы приступить."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Начать", callback_data=f"begin_{exp_id}")]
    ])
    await message.answer(text, reply_markup=kb)


@router.message(CommandStart())
async def cmd_start(message: types.Message):
    """обычный /start — первый контакт с ботом, регистрация исследователя"""
    user = await repo.get_user(message.from_user.id)

    if user and user["role"] == "researcher":
        await show_researcher_menu(message)
        return

    # первое обращение — регистрируем как исследователя
    user_data = {
        "username": message.from_user.username,
        "first_name": message.from_user.first_name,
        "last_name": message.from_user.last_name,
        "role": "researcher",
    }
    await repo.get_or_create_user(message.from_user.id, user_data)

    # если уже был participant — обновляем роль
    if user and user["role"] == "participant":
        await repo.update_user(message.from_user.id, {"role": "researcher"})

    await message.answer(
        "Добро пожаловать! Вы зарегистрированы как исследователь.\n"
        "Используйте меню ниже для работы с экспериментами."
    )
    await show_researcher_menu(message)


async def show_researcher_menu(message: types.Message):
    """главное меню исследователя"""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Создать эксперимент", callback_data="create_experiment")],
        [InlineKeyboardButton(text="Мои эксперименты", callback_data="my_experiments")],
        [InlineKeyboardButton(text="Результаты", callback_data="results_menu")],
        [InlineKeyboardButton(text="Рассылка участникам", callback_data="promo_menu")],
    ])
    await message.answer("Главное меню:", reply_markup=kb)
