import logging

from aiogram import F, Router, Bot, types
from aiogram.filters import CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db import repositories as repo
from engine import runner

router = Router()
logger = logging.getLogger("bot")


@router.message(CommandStart(deep_link=True))
async def cmd_start_deep_link(
    message: types.Message, command: CommandObject, bot: Bot,
    state: FSMContext,
):
    """обработка перехода по ссылке на эксперимент: /start exp_<id>"""
    args = command.args or ""
    logger.info("deep link от %s: %s", message.from_user.id, args)

    # /start по дип-линку — это сценарий участника. чистим FSM, чтобы
    # active_menu_msg_id от прошлого researcher-меню не блокировал клик
    # «Начать» через StaleMenuGuard.
    await state.clear()

    if not args.startswith("exp_"):
        await cmd_start(message, state)
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
        # закрываем чужие in_progress сессии (от других экспериментов),
        # чтобы текст/голос не уходил «не туда» (см. find_active_session).
        await repo.abandon_other_active_sessions(
            message.from_user.id, keep_session_id=str(existing["_id"]),
        )
        # резюмируем эту сессию: чистим всё, что осталось «в подвешенном
        # состоянии» от прошлого захода — иначе следующий клик/текст
        # уйдёт во второй шаг чужого протокола (TVJT обоснование, AJT
        # вторая оценка) и сломает сессию.
        clear_pending = {}
        if existing.get("pending_judgment"):
            clear_pending["pending_judgment"] = None
        if existing.get("pending_first_rating"):
            clear_pending["pending_first_rating"] = None
        if clear_pending:
            await repo.update_session(str(existing["_id"]), clear_pending)
            existing = await repo.get_session(str(existing["_id"])) or existing
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

    # показываем приветствие. Название эксперимента респонденту не
    # показываем — оно служебное (для исследователя в списке экспериментов
    # и в экспорте). Если задано приветственное сообщение (description) —
    # показываем его; иначе нейтральная заглушка.
    description = (experiment.get("description") or "").strip()
    if description:
        text = f"{description}\n\nНажмите «Начать», чтобы приступить."
    else:
        text = "Нажмите «Начать», чтобы приступить."
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Начать", callback_data=f"begin_{exp_id}")]
    ])
    await message.answer(text, reply_markup=kb)


@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    """обычный /start — приветствие с выбором роли"""
    # /start — это «жёсткий ресет» интерфейса. чистим FSM, чтобы старые
    # active_menu_msg_id и waiting_* флаги не мешали новой сессии.
    await state.clear()
    user = await repo.get_user(message.from_user.id)

    # обрубаем все незавершённые сессии этого пользователя. иначе
    # любое следующее текстовое сообщение (даже просто заметка
    # самому себе) уйдёт в find_active_session и будет записано как
    # ответ на пробу старого эксперимента. /start = чистый старт.
    abandoned = await repo.abandon_other_active_sessions(message.from_user.id)
    if abandoned:
        logger.info(
            "/start: закрыто %s старых сессий пользователя %s",
            abandoned, message.from_user.id,
        )

    # уже зарегистрированный исследователь — сразу в его меню,
    # выбор роли не предлагаем (он уже сделал его раньше).
    if user and user["role"] == "researcher":
        await show_researcher_menu(message, state)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Зарегистрироваться как исследователь",
            callback_data="welcome_researcher",
        )],
        [InlineKeyboardButton(
            text="Пройти исследование",
            callback_data="welcome_participant",
        )],
    ])
    sent = await message.answer(
        "Добро пожаловать! Выберите, как вы хотите использовать бота:",
        reply_markup=kb,
    )
    await state.update_data(active_menu_msg_id=sent.message_id)


@router.callback_query(F.data == "welcome_researcher")
async def on_welcome_researcher(
    callback: types.CallbackQuery, state: FSMContext,
):
    """пользователь выбрал роль исследователя — регистрируем и показываем меню"""
    user = await repo.get_user(callback.from_user.id)
    user_data = {
        "username": callback.from_user.username,
        "first_name": callback.from_user.first_name,
        "last_name": callback.from_user.last_name,
        "role": "researcher",
    }
    if not user:
        await repo.get_or_create_user(callback.from_user.id, user_data)
    elif user["role"] != "researcher":
        await repo.update_user(callback.from_user.id, {"role": "researcher"})
    await callback.answer()
    await callback.message.answer(
        "Вы зарегистрированы как исследователь."
    )
    await show_researcher_menu(callback.message, state)


@router.callback_query(F.data == "welcome_participant")
async def on_welcome_participant(
    callback: types.CallbackQuery, state: FSMContext,
):
    """пользователь хочет пройти исследование — показываем инструкцию"""
    await callback.answer()
    # сбрасываем active_menu_msg_id, чтобы дальше клики по «Начать» в
    # эксперименте по дип-линку не блокировались StaleMenuGuard'ом.
    await state.update_data(active_menu_msg_id=None)
    await callback.message.answer(
        "Чтобы пройти исследование, попросите исследователя прислать вам "
        "ссылку и перейдите по ней."
    )


async def show_researcher_menu(message: types.Message, state: FSMContext | None = None):
    """главное меню исследователя"""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Создать эксперимент", callback_data="create_experiment")],
        [InlineKeyboardButton(text="Мои эксперименты", callback_data="my_experiments")],
        [InlineKeyboardButton(text="Результаты", callback_data="results_menu")],
        [InlineKeyboardButton(text="Рассылка участникам", callback_data="promo_menu")],
    ])
    sent = await message.answer("Главное меню:", reply_markup=kb)
    # фиксируем id главного меню как «текущий активный экран», чтобы
    # StaleMenuGuard блокировал клики по предыдущим меню в чате.
    if state is not None:
        await state.update_data(active_menu_msg_id=sent.message_id)
