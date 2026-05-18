import logging
from datetime import datetime

from aiogram import F, Router, Bot, types
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db import repositories as repo
from engine import runner
from models.user import is_premium_active
from utils.idle_guard import check_and_abandon_if_idle

router = Router()
logger = logging.getLogger("bot")


CONSENT_TEXT = (
    "Здравствуйте!\n\n"
    "Этот бот используется для проведения научных исследований "
    "по психо- и нейролингвистике. Ваши ответы передаются "
    "исследователю в анонимизированном виде: в выгрузку не попадает "
    "ни ваше имя, ни ваш телеграм-идентификатор, только содержательные "
    "данные эксперимента.\n\n"
    "Бот может также присылать вам приглашения участвовать в новых "
    "исследованиях. От рассылки можно отказаться в любой момент, "
    "введя команду /unsubscribe.\n\n"
    "Подтверждаете согласие на участие и обработку данных в научных целях?"
)


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

    # создаем или находим пользователя как участника. валидность ссылки
    # и активность эксперимента проверяем уже после согласия — иначе
    # пришлось бы дублировать проверки в двух точках входа.
    # имя/фамилию/username не сохраняем (см. models/user.py): согласие
    # обещает анонимизированную обработку, идентификация по telegram_id.
    user_data = {"role": "participant"}
    user = await repo.get_or_create_user(message.from_user.id, user_data)

    # экран согласия: показывается один раз, при первом переходе по
    # любому deep-link до начала любого эксперимента. сохраняем deep_link
    # в FSM, чтобы после «Согласен» вернуться к тому же эксперименту.
    if not user.get("consent_given"):
        await state.update_data(pending_deep_link=deep_link_id)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Да, согласен(на)", callback_data="consent_yes")],
            [InlineKeyboardButton(text="Отказаться", callback_data="consent_no")],
        ])
        await message.answer(CONSENT_TEXT, reply_markup=kb)
        return

    await _enter_experiment_by_link(
        bot, message, message.from_user.id, deep_link_id,
    )


async def _enter_experiment_by_link(
    bot: Bot, reply_target: types.Message, telegram_id: int,
    deep_link_id: str,
):
    """показать вход в эксперимент по deep_link_id.
    вызывается из cmd_start_deep_link (когда согласие уже дано) и из
    обработчика согласия (после клика «Согласен»)."""
    experiment = await repo.get_experiment_by_link(deep_link_id)
    if not experiment:
        await reply_target.answer("Эксперимент не найден или ссылка устарела.")
        return

    if experiment["status"] != "active":
        await reply_target.answer("Этот эксперимент сейчас неактивен.")
        return

    exp_id = str(experiment["_id"])
    existing = await repo.get_active_session(telegram_id, exp_id)
    if existing:
        # idle-таймаут: если участник давно не возвращался, abandon-им
        # сессию и идём в обычный путь «начать заново».
        if await check_and_abandon_if_idle(
            existing, experiment, bot, telegram_id,
        ):
            existing = None
    if existing:
        # закрываем чужие in_progress сессии (от других экспериментов),
        # чтобы текст/голос не уходил «не туда» (см. find_active_session).
        await repo.abandon_other_active_sessions(
            telegram_id, keep_session_id=str(existing["_id"]),
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
        if existing.get("pending_text_change"):
            clear_pending["pending_text_change"] = None
        if existing.get("pending_interpretation"):
            clear_pending["pending_interpretation"] = None
        if clear_pending:
            await repo.update_session(str(existing["_id"]), clear_pending)
            existing = await repo.get_session(str(existing["_id"])) or existing
        await reply_target.answer(
            "У вас есть незавершенная сессия. "
            "Продолжаем с того места, где вы остановились."
        )
        # возобновляем сессию: берем подготовленные фазы и показываем текущую пробу
        prepared = existing.get("prepared_phases") or experiment["phases"]
        exp_copy = dict(experiment)
        exp_copy["phases"] = prepared
        await runner.present_trial(bot, telegram_id, existing, exp_copy)
        return

    if not experiment.get("allow_repeat", False):
        # проверяем завершенные сессии
        sessions = await repo.get_sessions_by_experiment(exp_id)
        finished = [s for s in sessions
                    if s["telegram_id"] == telegram_id
                    and s["status"] == "completed"]
        if finished:
            await reply_target.answer("Вы уже проходили этот эксперимент. Повторное прохождение не предусмотрено.")
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
    await reply_target.answer(text, reply_markup=kb)


@router.callback_query(F.data == "consent_yes")
async def on_consent_yes(
    callback: types.CallbackQuery, state: FSMContext, bot: Bot,
):
    """респондент подтвердил согласие — фиксируем и продолжаем вход
    в эксперимент по сохранённому deep_link."""
    await callback.answer()
    await repo.update_user(callback.from_user.id, {
        "consent_given": True,
        "consent_at": datetime.utcnow(),
    })
    data = await state.get_data()
    deep_link_id = data.get("pending_deep_link")
    await state.update_data(pending_deep_link=None)
    if not deep_link_id:
        # экран согласия в норме показывается только в потоке deep-link,
        # но на всякий случай страхуемся: если ссылка потерялась — даём
        # понятную инструкцию вместо немого выхода.
        await callback.message.answer(
            "Спасибо! Перейдите по ссылке на исследование заново, "
            "чтобы начать."
        )
        return
    await _enter_experiment_by_link(
        bot, callback.message, callback.from_user.id, deep_link_id,
    )


@router.callback_query(F.data == "consent_no")
async def on_consent_no(callback: types.CallbackQuery, state: FSMContext):
    """респондент отказался — без согласия пройти исследование нельзя."""
    await callback.answer()
    await state.update_data(pending_deep_link=None)
    await callback.message.answer(
        "Без вашего согласия пройти исследование невозможно. "
        "Если измените решение, перейдите по ссылке заново."
    )


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
    user_data = {"role": "researcher"}
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


def build_researcher_menu_kb(is_premium: bool) -> InlineKeyboardMarkup:
    """клавиатура главного меню исследователя.

    кнопка премиума есть всегда: для не-премиум - «Перейти на премиум» с
    переходом на экран оплаты; для премиума - «Премиум» с переходом на тот
    же экран, где показывается дата окончания подписки и кнопка продления.
    """
    premium_label = (
        "⭐ Премиум" if is_premium else "⭐ Перейти на премиум"
    )
    buttons = [
        [InlineKeyboardButton(text="Создать эксперимент", callback_data="create_experiment")],
        [InlineKeyboardButton(text="Мои эксперименты", callback_data="my_experiments")],
        [InlineKeyboardButton(text="Результаты", callback_data="results_menu")],
        [InlineKeyboardButton(text="Рассылка участникам", callback_data="promo_menu")],
        [InlineKeyboardButton(text=premium_label, callback_data="premium_info")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def show_researcher_menu(message: types.Message, state: FSMContext | None = None):
    """главное меню исследователя"""
    user = await repo.get_user(message.from_user.id)
    kb = build_researcher_menu_kb(is_premium_active(user))
    sent = await message.answer("Главное меню:", reply_markup=kb)
    # фиксируем id главного меню как «текущий активный экран», чтобы
    # StaleMenuGuard блокировал клики по предыдущим меню в чате.
    if state is not None:
        await state.update_data(active_menu_msg_id=sent.message_id)


@router.message(Command("unsubscribe"))
async def cmd_unsubscribe(message: types.Message, state: FSMContext):
    """отписка от рассылки. команда упомянута только в тексте согласия,
    в меню и в подсказках не отображается. снимает consent_given —
    после этого пользователь выпадает из выборки get_past_participants.
    повторно подписаться можно, перейдя по любому deep-link и снова
    подтвердив согласие."""
    user = await repo.get_user(message.from_user.id)
    if not user or user.get("role") != "participant":
        # для исследователя или для незарегистрированного — молча
        # отвечаем нейтральным текстом, чтобы не светить лишнее.
        await message.answer("Готово.")
        return
    if not user.get("consent_given"):
        await message.answer(
            "Вы и так не получаете сообщений от бота."
        )
        return
    await repo.update_user(message.from_user.id, {
        "consent_given": False,
        "consent_at": None,
    })
    logger.info("отписка участника %s", message.from_user.id)
    await message.answer(
        "Вы отписались от рассылки. Бот больше не будет присылать "
        "вам приглашения. Если передумаете — перейдите по ссылке "
        "на любое исследование и подтвердите согласие заново."
    )
