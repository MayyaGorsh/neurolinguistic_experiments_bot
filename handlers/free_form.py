"""free-form mode: исследователь собирает эксперимент без шаблона.

каждая фаза проходит маленький конструктор:
    тип стимула → тип ответа → инструкция → CSV → настройки фазы.

формат CSV единый и определяется парой (stimulus_type × response_type):
    * первая колонка — `stimulus` (текст или имя медиа-файла);
    * если стимул медиа — колонка `caption` (подпись, идёт перед кнопками
      или после аудио);
    * для buttons / multiple_choice / likert / buttons_then_text — колонки
      вариантов ответа; правильный отмечается `*` в начале ячейки;
    * для buttons_then_text — дополнительно колонка `follow_up_prompt`
      (текст приглашения ко второму шагу);
    * для open_text / voice — никаких опций.

CSV-пример отдаётся по кнопке «📄 Прислать пример» — генерируется на лету
под выбранную пару в templates.free_form_examples.

часть настроек, которые в шаблонах общие на эксперимент (рандомизация
порядка, тайм-аут, рандомизация позиций кнопок/картинок), здесь —
per-phase: каждая фаза держит их у себя; глобальные настройки эти
поля для free_form скрывают (см. researcher_settings)."""

import logging

from aiogram import Router, Bot, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from utils import csv_parser
from utils.ui import render_screen
from templates.free_form_examples import build_freeform_csv_example

router = Router()
logger = logging.getLogger("bot")


STIMULUS_TYPES: list[tuple[str, str]] = [
    ("text", "Текст"),
    ("audio", "Аудио"),
    ("image", "Изображение"),
    ("video", "Видео"),
]

RESPONSE_TYPES: list[tuple[str, str]] = [
    ("buttons", "Кнопки"),
    ("likert", "Шкала Ликерта"),
    ("multiple_choice", "Множественный выбор"),
    ("buttons_then_text", "Кнопки + текст"),
    ("open_text", "Открытый текст"),
    ("voice", "Голосовое сообщение"),
]

_STIM_LABEL = dict(STIMULUS_TYPES)
_RESP_LABEL = dict(RESPONSE_TYPES)

# response_type-ы, у которых есть кнопочный выбор (релевантно для
# рандомизации позиций кнопок и для парсинга колонок-вариантов)
_BUTTON_RESP = {"buttons", "likert", "multiple_choice", "buttons_then_text"}


def _ui_target(target):
    """нормализовать target для render_screen.

    в free_form мы зовём меню-функции с разными типами:
      * настоящий CallbackQuery → render_screen его съест и сделает SPA-edit;
      * Message (после ввода текста / загрузки файла) → render_screen
        вызовет .answer() и пришлёт новое сообщение;
      * FakeCallback (см. researcher_create.on_description_entered) — не
        CallbackQuery и не Message; render_screen на нём упадёт. в этом
        случае распакуем .message и работаем как с Message.
    """
    if isinstance(target, types.CallbackQuery):
        return target
    if isinstance(target, types.Message):
        return target
    # FakeCallback и т.п. — берём из него реальное Message
    msg = getattr(target, "message", None)
    return msg if isinstance(msg, types.Message) else target


class FreeFormSetup(StatesGroup):
    phases_summary = State()
    choosing_stim = State()
    choosing_resp = State()
    entering_instruction = State()
    uploading_csv = State()
    phase_settings = State()
    waiting_timeout = State()
    phase_help = State()
    entering_phase_title = State()


# ── helpers ──────────────────────────────────────────────────────────


def _make_csv_mapping(
    stim_type: str, resp_type: str, columns: list[str],
) -> dict:
    """собрать mapping для csv_parser.rows_to_trials под пару (stim, resp).

    первая колонка — стимул. служебные колонки (`caption` для медиа,
    `follow_up_prompt` для buttons_then_text) уходят в auxiliary. всё
    остальное — варианты ответа, если resp_type кнопочный."""
    mapping: dict = {}
    if not columns:
        return mapping

    stim_col = columns[0]
    mapping["stimulus_content"] = stim_col

    reserved = {stim_col}
    aux_cols: list[str] = []
    if stim_type in ("audio", "image", "video") and "caption" in columns:
        aux_cols.append("caption")
        reserved.add("caption")
    if resp_type == "buttons_then_text" and "follow_up_prompt" in columns:
        aux_cols.append("follow_up_prompt")
        reserved.add("follow_up_prompt")
    if aux_cols:
        mapping["auxiliary"] = aux_cols

    if resp_type in _BUTTON_RESP:
        opt_cols = [c for c in columns if c not in reserved]
        if opt_cols:
            mapping["response_options"] = opt_cols

    return mapping


def _build_phase_dict(
    *,
    phase_index: int,
    instruction: str,
    stim_type: str,
    resp_type: str,
    trials: list[dict],
    columns: list[str],
) -> dict:
    """собрать новую фазу с дефолтными per-phase настройками.

    для likert проставляем likert_scale/labels в settings, чтобы
    runner.build_response_keyboard рисовал шкалу с подписями из
    заголовков CSV (как в word-level шаблонах). для остальных
    кнопочных типов варианты лежат прямо в trial.response_options."""
    settings: dict = {}
    if resp_type == "likert":
        # лейблы делений = headers колонок-вариантов в том же порядке,
        # в каком они стоят в mapping["response_options"].
        # повторяем логику _make_csv_mapping (без сохранения mapping),
        # чтобы не зависеть от порядка вызова.
        reserved = {columns[0] if columns else None}
        if stim_type in ("audio", "image", "video") and "caption" in columns:
            reserved.add("caption")
        opt_cols = [c for c in columns if c not in reserved]
        if opt_cols:
            settings["likert_scale"] = len(opt_cols)
            settings["likert_labels"] = {
                str(i + 1): col for i, col in enumerate(opt_cols)
            }

    return {
        "phase_index": phase_index,
        "title": f"Фаза {phase_index + 1}",
        # title_auto=True значит «синхронизировать с позицией»: при
        # перестановке/удалении фаз _reindex_phases перепишет title на
        # актуальное «Фаза N». как только researcher переименует фазу
        # вручную, флаг становится False и имя замораживается.
        "title_auto": True,
        "instruction": instruction,
        "stimulus_type": stim_type,
        "response_type": resp_type,
        "trials": trials,
        # per-phase настройки (см. модуль-комментарий)
        "randomize_order": False,
        "time_limit": None,
        "randomize_button_positions": False,
        "randomize_image_positions": False,
        "settings": settings,
    }


def _reindex_phases(phases: list[dict]) -> None:
    """после удаления/перестановки пересобираем phase_index;
    title переписываем только для авто-имён (title_auto=True).

    обратная совместимость: у старых фаз поля title_auto нет — считаем
    «авто» тогда, когда title пуст или ровно «Фаза N» для какого-то N."""
    for i, p in enumerate(phases):
        p["phase_index"] = i
        if "title_auto" in p:
            is_auto = bool(p["title_auto"])
        else:
            cur = (p.get("title") or "").strip()
            is_auto = (
                not cur
                or (cur.startswith("Фаза ") and cur[5:].strip().isdigit())
            )
            p["title_auto"] = is_auto
        if is_auto:
            p["title"] = f"Фаза {i + 1}"


def _phase_summary_line(phase: dict) -> str:
    return (
        f"{phase.get('title', 'Фаза')}: "
        f"{_STIM_LABEL.get(phase['stimulus_type'], phase['stimulus_type'])} / "
        f"{_RESP_LABEL.get(phase['response_type'], phase['response_type'])} — "
        f"{len(phase.get('trials', []))} проб"
    )


# ── экраны ────────────────────────────────────────────────────────────


_PHASES_INTRO = (
    "<i>Фаза — это набор проб с одним типом стимула и одним типом ответа. "
    "Например: показываем слова и просим выбрать «слово/не слово» — одна фаза. "
    "Если потом те же слова надо оценить по Ликерту — это уже вторая фаза.\n\n"
    "↑/↓ слева от фазы — переставить местами; ✏️ — открыть настройки и "
    "переименовать.</i>"
)


async def show_phases_summary(target, state: FSMContext):
    """показать список фаз + ➕/✅. target = Message или CallbackQuery.

    каждая фаза — отдельный ряд с кнопками ↑/↓ для перестановки и
    основной кнопкой ✏️ для входа в её настройки. ↑ не показывается
    у первой фазы, ↓ — у последней (Telegram не имеет «неактивных»
    кнопок, поэтому просто опускаем нерабочие).
    """
    data = await state.get_data()
    phases = data.get("free_form_phases", [])

    lines = ["<b>Фазы эксперимента</b>", "", _PHASES_INTRO]
    if phases:
        lines.append("")
        for i, p in enumerate(phases):
            lines.append(f"{i + 1}. {_phase_summary_line(p)}")
    else:
        lines.append("")
        lines.append("Фаз пока нет. Нажмите «➕ Добавить фазу».")
    text = "\n".join(lines)

    buttons: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="➕ Добавить фазу", callback_data="ff_add_phase")],
    ]
    n = len(phases)
    for i, p in enumerate(phases):
        row: list[InlineKeyboardButton] = []
        if i > 0:
            row.append(InlineKeyboardButton(
                text="↑", callback_data=f"ff_move_up_{i}",
            ))
        if i < n - 1:
            row.append(InlineKeyboardButton(
                text="↓", callback_data=f"ff_move_down_{i}",
            ))
        row.append(InlineKeyboardButton(
            text=f"✏️ {i + 1}. {_phase_summary_line(p)}",
            callback_data=f"ff_edit_phase_{i}",
        ))
        buttons.append(row)
    if phases:
        buttons.append([InlineKeyboardButton(
            text="✅ Готово — к настройкам эксперимента",
            callback_data="ff_done",
        )])

    await render_screen(
        _ui_target(target),
        text,
        InlineKeyboardMarkup(inline_keyboard=buttons),
        state=state,
    )
    await state.set_state(FreeFormSetup.phases_summary)


async def show_phase_settings(target, state: FSMContext, phase_index: int):
    """экран per-phase настроек."""
    data = await state.get_data()
    phases = data.get("free_form_phases", [])
    if not (0 <= phase_index < len(phases)):
        await show_phases_summary(target, state)
        return

    p = phases[phase_index]
    resp_type = p.get("response_type", "buttons")
    stim_type = p.get("stimulus_type", "text")

    lines = [
        f"<b>{p.get('title', f'Фаза {phase_index + 1}')}</b>",
        "",
        f"Тип стимула: {_STIM_LABEL.get(stim_type, stim_type)}",
        f"Тип ответа: {_RESP_LABEL.get(resp_type, resp_type)}",
        f"Проб: {len(p.get('trials', []))}",
    ]
    instr = (p.get("instruction") or "").strip()
    if instr:
        # короткий превью инструкции
        preview = instr if len(instr) <= 120 else instr[:117] + "…"
        lines.append(f"\nИнструкция: {preview}")

    t_lim = p.get("time_limit")
    timeout_label = f"{t_lim} сек" if t_lim else "нет"

    # подписи — как в шаблонах (см. researcher_settings._settings_rows):
    # «Метка — значение». чтобы юзер видел знакомый формат и понимал
    # семантику без дополнительных пояснений (рандомизация = порядок
    # проб; тайм-аут — на пробу; и т.п.).
    buttons: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(
            text=f"Рандомизация — {'да' if p.get('randomize_order') else 'нет'}",
            callback_data="ff_ps_randomize",
        )],
        [InlineKeyboardButton(
            text=f"Тайм-аут — {timeout_label}",
            callback_data="ff_ps_timeout",
        )],
    ]
    if resp_type in _BUTTON_RESP:
        buttons.append([InlineKeyboardButton(
            text=(
                "Рандомизация позиций кнопок — "
                f"{'да' if p.get('randomize_button_positions') else 'нет'}"
            ),
            callback_data="ff_ps_rand_btn",
        )])
    if stim_type == "image":
        buttons.append([InlineKeyboardButton(
            text=(
                "Перемешивать позиции картинок — "
                f"{'да' if p.get('randomize_image_positions') else 'нет'}"
            ),
            callback_data="ff_ps_rand_img",
        )])

    buttons += [
        [InlineKeyboardButton(
            text="📝 Переименовать фазу", callback_data="ff_ps_rename",
        )],
        [InlineKeyboardButton(
            text="✏️ Изменить инструкцию", callback_data="ff_ps_edit_instr",
        )],
        [InlineKeyboardButton(
            text="📎 Заменить CSV", callback_data="ff_ps_replace_csv",
        )],
        [InlineKeyboardButton(
            text="ℹ️ Что это всё значит?", callback_data="ff_ps_help",
        )],
        [InlineKeyboardButton(
            text="🗑 Удалить фазу", callback_data="ff_ps_delete",
        )],
        [InlineKeyboardButton(
            text="← К списку фаз", callback_data="ff_ps_back",
        )],
    ]

    await state.update_data(ff_editing_phase_index=phase_index)
    await render_screen(
        _ui_target(target),
        "\n".join(lines),
        InlineKeyboardMarkup(inline_keyboard=buttons),
        state=state,
    )
    await state.set_state(FreeFormSetup.phase_settings)


def _resolve_phase_kinds(data: dict) -> tuple[str, str]:
    """вернуть (stim_type, resp_type) для текущего CSV-flow.

    при редактировании существующей фазы — берём из неё, иначе — из
    драфта (новая фаза)."""
    editing = data.get("ff_csv_editing_index")
    if editing is not None:
        phases = data.get("free_form_phases", [])
        if 0 <= editing < len(phases):
            return (
                phases[editing].get("stimulus_type", "text"),
                phases[editing].get("response_type", "buttons"),
            )
    return (
        data.get("ff_draft_stim_type", "text"),
        data.get("ff_draft_resp_type", "buttons"),
    )


def _lists_count(data: dict) -> int:
    """глобальная настройка lists_count (≥1)."""
    return max(int(data.get("lists_count", 1) or 1), 1)


def _format_format_hint(stim_type: str, resp_type: str) -> str:
    """человеко-читаемый блок «Формат CSV» для подсказки в manifest."""
    lines = ["<b>Формат CSV:</b>"]
    lines.append(
        "• <code>stimulus</code> — "
        + ("имя медиа-файла" if stim_type in ("audio", "image", "video") else "текст стимула")
    )
    if stim_type in ("audio", "image", "video"):
        lines.append(
            "• <code>caption</code> — подпись, показывается "
            "после медиа-файла"
        )
    if resp_type in _BUTTON_RESP:
        lines.append(
            "• от 1 до 10 колонок-вариантов ответа; "
            "правильный отмечается <code>*</code> в начале значения"
        )
    if resp_type == "buttons_then_text":
        lines.append(
            "• <code>follow_up_prompt</code> — текст приглашения "
            "ко второму шагу"
        )
    if resp_type in ("open_text", "voice"):
        lines.append("• колонок вариантов нет"
                     + (", только стимул и подпись" if stim_type in ("audio", "image", "video")
                        else ", только стимул"))
    lines.append("Разделитель — только <code>;</code> (точка с запятой).")
    return "\n".join(lines)


async def _show_csv_manifest(target, state: FSMContext):
    """показать manifest со слотами по листам для текущего CSV-flow.

    если lists_count == 1, slot всего один — но manifest всё равно
    выводится единообразно, чтобы UX не зависел от настройки листов."""
    data = await state.get_data()
    stim_type, resp_type = _resolve_phase_kinds(data)
    n_lists = _lists_count(data)
    by_list: dict = data.get("ff_csv_trials_by_list") or {}

    lines = ["<b>Загрузка CSV для фазы</b>", ""]
    lines.append(_format_format_hint(stim_type, resp_type))

    buttons: list[list[InlineKeyboardButton]] = []
    done = 0
    for k in range(1, n_lists + 1):
        key = str(k)
        loaded = key in by_list and by_list[key]
        if loaded:
            done += 1
        mark = "✅" if loaded else "⬜"
        label = (
            f"{mark} Лист {k}" if n_lists > 1 else f"{mark} CSV-файл"
        )
        if loaded:
            label += f" — {len(by_list[key])}"
        buttons.append([InlineKeyboardButton(
            text=label, callback_data=f"ff_csv_slot_{k}",
        )])

    if n_lists > 1:
        lines.append("")
        lines.append(f"Загружено: {done}/{n_lists}")

    # «Готово» — собрать фазу из загруженных листов и вернуться в её
    # настройки. при создании новой фазы требуем минимум 1 загруженный
    # лист; полноту покрытия всех листов валидатор проверит при активации.
    # Кнопка «Прислать пример» в manifest-меню не нужна: пример сильно
    # зависит от пары stim×resp, и до клика по слоту юзер всё равно
    # ничего не загружает; кнопку показываем уже на экране слота.
    if done > 0:
        buttons.append([InlineKeyboardButton(
            text="✅ Готово", callback_data="ff_csv_done",
        )])
    buttons.append([InlineKeyboardButton(
        text="❌ Отмена", callback_data="ff_csv_cancel",
    )])

    await render_screen(
        _ui_target(target),
        "\n".join(lines),
        InlineKeyboardMarkup(inline_keyboard=buttons),
        state=state,
    )
    await state.set_state(FreeFormSetup.uploading_csv)


def _trials_by_list_from_phase(phase: dict) -> dict[str, list[dict]]:
    """сгруппировать trials существующей фазы по list_id (строки).

    используется при «📎 Заменить CSV» в phase_settings: чтобы юзер
    видел, какие листы уже заполнены."""
    out: dict[str, list[dict]] = {}
    for t in phase.get("trials", []) or []:
        key = str(t.get("list_id") or "1")
        out.setdefault(key, []).append(t)
    return out


def _columns_from_phase(phase: dict) -> list[str]:
    """реконструировать список заголовков CSV из существующих trials.

    нужен при редактировании, когда юзер ещё не загрузил ни одного
    нового файла, но мы должны знать «эталонные» колонки. формат CSV
    в free_form един, поэтому колонки выводятся из формы первого
    непустого trial-а."""
    trials = phase.get("trials", []) or []
    if not trials:
        return []
    first = trials[0]
    stim_type = phase.get("stimulus_type", "text")
    resp_type = phase.get("response_type", "buttons")
    cols: list[str] = ["stimulus"]
    aux = first.get("auxiliary") or {}
    if stim_type in ("audio", "image", "video") and "caption" in aux:
        cols.append("caption")
    # для likert эталонные headers лежат в settings.likert_labels, а в
    # response_options могли остаться значения ячеек (могут отличаться)
    if resp_type == "likert":
        labels = (phase.get("settings") or {}).get("likert_labels") or {}
        scale = (phase.get("settings") or {}).get("likert_scale") or len(labels)
        for i in range(1, int(scale) + 1):
            cols.append(str(labels.get(str(i), str(i))))
    else:
        cols.extend(list(first.get("response_options") or []))
    if "follow_up_prompt" in aux:
        cols.append("follow_up_prompt")
    return cols


async def _enter_csv_flow(target, state: FSMContext, *, editing_index=None):
    """инициализировать CSV-manifest и показать его.

    editing_index=None → создание новой фазы (драфт);
    editing_index=int  → замена CSV в существующей фазе."""
    data = await state.get_data()
    if editing_index is not None:
        phases = data.get("free_form_phases", [])
        if not (0 <= editing_index < len(phases)):
            return
        by_list = _trials_by_list_from_phase(phases[editing_index])
        cols = _columns_from_phase(phases[editing_index])
    else:
        by_list = {}
        cols = []
    # reconstructed columns не «эталонные» — для не-likert восстановить
    # оригинальные headers из trials нельзя (там значения после
    # вычистки `*`). первый реально загруженный CSV в этой сессии
    # переопределит эталон; до этого момента проверка совпадения
    # колонок пропускается (флаг ff_csv_columns_locked=False).
    await state.update_data(
        ff_csv_editing_index=editing_index,
        ff_csv_trials_by_list=by_list,
        ff_csv_columns=cols,
        ff_csv_columns_locked=False,
        ff_csv_current_list=None,
    )
    await _show_csv_manifest(target, state)


# ── вход ──────────────────────────────────────────────────────────────


async def start_free_form(callback, state: FSMContext):
    """вход в редактирование фаз. зовут researcher_create (новый эксп)
    и researcher_settings (кнопка «Редактировать фазы» в edit_draft)."""
    data = await state.get_data()
    phases = list(data.get("free_form_phases", []))
    await state.update_data(
        free_form_phases=phases,
        ff_editing_phase_index=None,
        ff_draft_stim_type=None,
        ff_draft_resp_type=None,
        ff_draft_instruction=None,
        ff_csv_editing_index=None,
        ff_csv_trials_by_list={},
        ff_csv_columns=[],
        ff_csv_columns_locked=False,
        ff_csv_current_list=None,
    )
    await show_phases_summary(callback, state)


# ── handlers: summary ─────────────────────────────────────────────────


@router.callback_query(FreeFormSetup.phases_summary, F.data == "ff_add_phase")
async def on_add_phase(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(
        ff_editing_phase_index=None,
        ff_draft_stim_type=None,
        ff_draft_resp_type=None,
        ff_draft_instruction=None,
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"ff_stim_{code}")]
        for code, label in STIMULUS_TYPES
    ])
    await render_screen(
        callback, "Выберите тип стимула для новой фазы:", kb, state=state,
    )
    await state.set_state(FreeFormSetup.choosing_stim)


@router.callback_query(
    FreeFormSetup.phases_summary, F.data.startswith("ff_edit_phase_"),
)
async def on_edit_phase(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    try:
        idx = int(callback.data.replace("ff_edit_phase_", ""))
    except ValueError:
        return
    await show_phase_settings(callback, state, idx)


async def _swap_phases(state: FSMContext, i: int, j: int) -> None:
    """перестановка двух фаз в state с переиндексацией."""
    data = await state.get_data()
    phases = list(data.get("free_form_phases", []))
    if not (0 <= i < len(phases)) or not (0 <= j < len(phases)):
        return
    phases[i], phases[j] = phases[j], phases[i]
    _reindex_phases(phases)
    await state.update_data(free_form_phases=phases)


@router.callback_query(
    FreeFormSetup.phases_summary, F.data.startswith("ff_move_up_"),
)
async def on_move_up(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    try:
        idx = int(callback.data.replace("ff_move_up_", ""))
    except ValueError:
        return
    if idx <= 0:
        return
    await _swap_phases(state, idx, idx - 1)
    await show_phases_summary(callback, state)


@router.callback_query(
    FreeFormSetup.phases_summary, F.data.startswith("ff_move_down_"),
)
async def on_move_down(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    try:
        idx = int(callback.data.replace("ff_move_down_", ""))
    except ValueError:
        return
    data = await state.get_data()
    phases = data.get("free_form_phases", [])
    if idx >= len(phases) - 1:
        return
    await _swap_phases(state, idx, idx + 1)
    await show_phases_summary(callback, state)


@router.callback_query(FreeFormSetup.phases_summary, F.data == "ff_done")
async def on_phases_done(callback: types.CallbackQuery, state: FSMContext):
    """фазы собраны — передаем управление в общий config menu."""
    await callback.answer()
    data = await state.get_data()
    if not data.get("free_form_phases"):
        await callback.message.answer(
            "Сначала добавьте хотя бы одну фазу."
        )
        return
    from handlers.researcher_settings import show_config_menu
    await show_config_menu(callback, state)


# ── handlers: choosing types ──────────────────────────────────────────


@router.callback_query(FreeFormSetup.choosing_stim, F.data.startswith("ff_stim_"))
async def on_stim_chosen(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    code = callback.data.replace("ff_stim_", "")
    if code not in _STIM_LABEL:
        return
    await state.update_data(ff_draft_stim_type=code)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"ff_resp_{c}")]
        for c, label in RESPONSE_TYPES
    ])
    await render_screen(
        callback,
        f"Стимул: <b>{_STIM_LABEL[code]}</b>.\nТеперь выберите тип ответа:",
        kb,
        state=state,
    )
    await state.set_state(FreeFormSetup.choosing_resp)


@router.callback_query(FreeFormSetup.choosing_resp, F.data.startswith("ff_resp_"))
async def on_resp_chosen(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    code = callback.data.replace("ff_resp_", "")
    if code not in _RESP_LABEL:
        return
    await state.update_data(ff_draft_resp_type=code)
    # промпт без кнопок — следующий шаг это ввод текста; render_screen
    # тоже подходит, он перерисует текущий экран и не оставит кнопки,
    # которые мы уже не ждём.
    await render_screen(
        callback,
        f"Ответ: <b>{_RESP_LABEL[code]}</b>.\n\nВведите инструкцию для этой фазы:",
        None,
        state=state,
    )
    await state.set_state(FreeFormSetup.entering_instruction)


# ── handlers: instruction (create OR edit) ────────────────────────────


@router.message(FreeFormSetup.entering_instruction, F.text)
async def on_instruction(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        await message.answer("Инструкция не может быть пустой. Введите текст:")
        return

    data = await state.get_data()
    editing = data.get("ff_editing_phase_index")
    if editing is not None:
        # редактирование существующей фазы — только инструкция
        phases = list(data.get("free_form_phases", []))
        if 0 <= editing < len(phases):
            phases[editing]["instruction"] = text
            await state.update_data(free_form_phases=phases)
        await message.answer("✅ Инструкция обновлена.")
        await show_phase_settings(message, state, editing)
        return

    # создание новой фазы — сохраняем в драфте и идём в manifest CSV
    await state.update_data(ff_draft_instruction=text)
    await _enter_csv_flow(message, state, editing_index=None)


# ── handlers: CSV manifest ────────────────────────────────────────────


@router.callback_query(FreeFormSetup.uploading_csv, F.data == "ff_csv_example")
async def on_csv_example(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    data = await state.get_data()
    stim_type, resp_type = _resolve_phase_kinds(data)
    csv_bytes = build_freeform_csv_example(stim_type, resp_type)
    fname = f"example_{stim_type}_{resp_type}.csv"
    await callback.message.answer_document(
        BufferedInputFile(csv_bytes, filename=fname),
        caption=(
            "Пример заполнения. Скачайте, адаптируйте под свой материал "
            "и пришлите обратно тем же файлом."
        ),
    )


@router.callback_query(FreeFormSetup.uploading_csv, F.data == "ff_csv_cancel")
async def on_csv_cancel(callback: types.CallbackQuery, state: FSMContext):
    """отмена manifest-а: при редактировании возвращаемся в настройки
    фазы (накопленные изменения отбрасываем); при создании — в список
    фаз без сохранения драфта."""
    await callback.answer()
    data = await state.get_data()
    editing = data.get("ff_csv_editing_index")
    await state.update_data(
        ff_csv_trials_by_list={},
        ff_csv_columns=[],
        ff_csv_columns_locked=False,
        ff_csv_current_list=None,
        ff_csv_editing_index=None,
    )
    if editing is not None:
        await show_phase_settings(callback, state, editing)
    else:
        await show_phases_summary(callback, state)


@router.callback_query(
    FreeFormSetup.uploading_csv, F.data.startswith("ff_csv_slot_"),
)
async def on_csv_slot(callback: types.CallbackQuery, state: FSMContext):
    """клик по слоту: выставляем current_list и просим файл."""
    await callback.answer()
    try:
        k = int(callback.data.replace("ff_csv_slot_", ""))
    except ValueError:
        return
    data = await state.get_data()
    n = _lists_count(data)
    if not (1 <= k <= n):
        return

    by_list = data.get("ff_csv_trials_by_list") or {}
    already = by_list.get(str(k))
    note = (
        f"\n<i>В этом слоте уже загружено {len(already)} проб. "
        "Новый файл заменит текущий.</i>"
        if already else ""
    )
    label = f"Лист {k}" if n > 1 else "файл"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📄 Прислать пример заполнения",
            callback_data="ff_csv_example",
        )],
        [InlineKeyboardButton(
            text="← Назад", callback_data="ff_csv_slot_back",
        )],
    ])
    await render_screen(
        callback,
        f"Отправьте CSV для слота: <b>{label}</b>.{note}",
        kb,
        state=state,
    )
    await state.update_data(ff_csv_current_list=str(k))


@router.callback_query(FreeFormSetup.uploading_csv, F.data == "ff_csv_slot_back")
async def on_csv_slot_back(callback: types.CallbackQuery, state: FSMContext):
    """вернуться из экрана конкретного слота обратно в manifest без загрузки."""
    await callback.answer()
    await state.update_data(ff_csv_current_list=None)
    await _show_csv_manifest(callback, state)


@router.message(FreeFormSetup.uploading_csv, F.document)
async def on_phase_csv(message: types.Message, state: FSMContext, bot: Bot):
    doc = message.document
    data = await state.get_data()
    current_list = data.get("ff_csv_current_list")
    if not current_list:
        await message.answer(
            "Сначала выберите слот в меню (нажмите «Лист N» / «CSV-файл»), "
            "а затем отправьте файл."
        )
        await _show_csv_manifest(message, state)
        return
    if not (doc.file_name or "").lower().endswith(".csv"):
        await message.answer("Отправьте файл в формате CSV.")
        return

    file = await bot.download(doc)
    content = file.read()
    try:
        rows = csv_parser.parse_csv_bytes(content)
    except Exception as e:
        await message.answer(f"❌ Ошибка чтения CSV: {e}")
        return
    if not rows:
        await message.answer("❌ Файл пуст.")
        return

    stim_type, resp_type = _resolve_phase_kinds(data)

    columns = list(rows[0].keys())
    if not columns:
        await message.answer("❌ В CSV не найдены колонки заголовка.")
        return
    if resp_type in _BUTTON_RESP:
        reserved = {columns[0]}
        if stim_type in ("audio", "image", "video"):
            reserved.add("caption")
        if resp_type == "buttons_then_text":
            reserved.add("follow_up_prompt")
        opt_cols = [c for c in columns if c not in reserved]
        if not opt_cols:
            await message.answer(
                "❌ Для выбранного типа ответа нужны колонки вариантов "
                "(хотя бы одна) помимо <code>stimulus</code>"
                + (" / <code>caption</code>" if stim_type in ("audio", "image", "video") else "")
                + "."
            )
            return
        if len(opt_cols) > 10:
            await message.answer(
                f"❌ Слишком много колонок-вариантов ({len(opt_cols)}). "
                "Максимум — 10."
            )
            return

    # колонки должны совпадать между листами одной фазы (формат CSV
    # один на пару stim×resp). первый реально загруженный слот в этой
    # сессии фиксирует эталон (ff_csv_columns_locked=True); проверяем
    # только после фиксации.
    prev_cols = data.get("ff_csv_columns")
    if (
        data.get("ff_csv_columns_locked")
        and prev_cols
        and list(prev_cols) != columns
    ):
        await message.answer(
            "❌ Колонки этого CSV не совпадают с уже загруженным листом.\n"
            f"Ожидаются: <code>{', '.join(prev_cols)}</code>.\n"
            f"В этом файле: <code>{', '.join(columns)}</code>."
        )
        return

    mapping = _make_csv_mapping(stim_type, resp_type, columns)
    trials = csv_parser.rows_to_trials(rows, mapping)
    trials = [t for t in trials if str(t.get("stimulus_content", "")).strip()]
    if not trials:
        await message.answer(
            "❌ После парсинга не осталось ни одного стимула с содержимым."
        )
        return

    # для image-стимулов положим img_filename — это даёт runner-у точку
    # стабильного кэширования photo file_id (см. runner: image branch).
    if stim_type == "image":
        for t in trials:
            t.setdefault("stimulus_metadata", {})
            t["stimulus_metadata"]["img_filename"] = (
                str(t.get("stimulus_content", "")).strip()
            )

    # размечаем list_id для всех проб этого слота
    for t in trials:
        t["list_id"] = current_list

    by_list = dict(data.get("ff_csv_trials_by_list") or {})
    by_list[current_list] = trials
    await state.update_data(
        ff_csv_trials_by_list=by_list,
        ff_csv_columns=columns,
        ff_csv_columns_locked=True,
        ff_csv_current_list=None,  # снимаем активный слот
    )
    n = _lists_count(data)
    label = f"лист {current_list}" if n > 1 else "файл"
    await message.answer(f"✅ Загружено {len(trials)} проб ({label}).")
    await _show_csv_manifest(message, state)


@router.callback_query(FreeFormSetup.uploading_csv, F.data == "ff_csv_done")
async def on_csv_done(callback: types.CallbackQuery, state: FSMContext):
    """собрать фазу из всех загруженных листов и вернуться в её настройки.

    при создании — добавляем новую фазу в free_form_phases.
    при замене CSV — обновляем trials существующей фазы (для likert
    дополнительно пересчитываем likert_scale/labels)."""
    await callback.answer()
    data = await state.get_data()
    by_list: dict = data.get("ff_csv_trials_by_list") or {}
    if not any(by_list.values()):
        await callback.message.answer(
            "Загрузите хотя бы один лист перед тем, как нажать «Готово»."
        )
        return

    # объединяем trials по слотам в порядке list_id (1, 2, …)
    def _key(k: str) -> tuple[int, str]:
        try:
            return (0, str(int(k)).zfill(8))
        except ValueError:
            return (1, k)
    ordered = sorted(by_list.items(), key=lambda kv: _key(kv[0]))
    merged: list[dict] = []
    for _lid, lst in ordered:
        merged.extend(lst)
    if not merged:
        await callback.message.answer(
            "Загрузите хотя бы один лист перед тем, как нажать «Готово»."
        )
        return

    # колонки нужны только для likert/build — берём из первого trial-а:
    # сохранять «исходные columns» в state мы не стали, чтобы не разъезжалось
    # при разных листах. для likert опции уже в trial.response_options
    # (одинаковые для всех листов), поэтому собираем шкалу из них.
    stim_type, resp_type = _resolve_phase_kinds(data)
    phases = list(data.get("free_form_phases", []))
    editing = data.get("ff_csv_editing_index")

    # колонки получены из загрузки (одинаковые во всех слотах фазы);
    # при редактировании может не быть, если юзер не загружал слотов,
    # но в этом случае мы сюда не попадём (см. проверку выше).
    columns = list(data.get("ff_csv_columns") or [])

    if editing is not None and 0 <= editing < len(phases):
        phases[editing]["trials"] = merged
        if resp_type == "likert" and columns:
            reserved = {columns[0]}
            if stim_type in ("audio", "image", "video") and "caption" in columns:
                reserved.add("caption")
            opt_cols = [c for c in columns if c not in reserved]
            settings = dict(phases[editing].get("settings") or {})
            settings["likert_scale"] = len(opt_cols)
            settings["likert_labels"] = {
                str(i + 1): col for i, col in enumerate(opt_cols)
            }
            phases[editing]["settings"] = settings
        await state.update_data(
            free_form_phases=phases,
            ff_csv_trials_by_list={},
            ff_csv_columns=[],
            ff_csv_current_list=None,
            ff_csv_editing_index=None,
        )
        await callback.message.answer(
            f"✅ CSV обновлён: {len(merged)} проб всего."
        )
        await show_phase_settings(callback, state, editing)
        return

    new_phase = _build_phase_dict(
        phase_index=len(phases),
        instruction=data.get("ff_draft_instruction") or "",
        stim_type=stim_type,
        resp_type=resp_type,
        trials=merged,
        columns=columns,
    )
    phases.append(new_phase)
    await state.update_data(
        free_form_phases=phases,
        ff_csv_trials_by_list={},
        ff_csv_columns=[],
        ff_csv_columns_locked=False,
        ff_csv_current_list=None,
        ff_csv_editing_index=None,
    )
    await callback.message.answer(
        f"✅ Фаза добавлена: {len(merged)} проб всего."
    )
    await show_phase_settings(callback, state, len(phases) - 1)


# ── handlers: phase_settings toggles ──────────────────────────────────


def _patch_phase(phases: list[dict], idx: int, **kwargs) -> None:
    if 0 <= idx < len(phases):
        phases[idx].update(kwargs)


@router.callback_query(FreeFormSetup.phase_settings, F.data == "ff_ps_randomize")
async def toggle_randomize(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    idx = data.get("ff_editing_phase_index")
    phases = list(data.get("free_form_phases", []))
    if idx is None or not (0 <= idx < len(phases)):
        return
    cur = bool(phases[idx].get("randomize_order"))
    _patch_phase(phases, idx, randomize_order=not cur)
    await state.update_data(free_form_phases=phases)
    await show_phase_settings(callback, state, idx)


@router.callback_query(FreeFormSetup.phase_settings, F.data == "ff_ps_rand_btn")
async def toggle_rand_btn(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    idx = data.get("ff_editing_phase_index")
    phases = list(data.get("free_form_phases", []))
    if idx is None or not (0 <= idx < len(phases)):
        return
    cur = bool(phases[idx].get("randomize_button_positions"))
    _patch_phase(phases, idx, randomize_button_positions=not cur)
    await state.update_data(free_form_phases=phases)
    await show_phase_settings(callback, state, idx)


@router.callback_query(FreeFormSetup.phase_settings, F.data == "ff_ps_rand_img")
async def toggle_rand_img(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    idx = data.get("ff_editing_phase_index")
    phases = list(data.get("free_form_phases", []))
    if idx is None or not (0 <= idx < len(phases)):
        return
    cur = bool(phases[idx].get("randomize_image_positions"))
    _patch_phase(phases, idx, randomize_image_positions=not cur)
    await state.update_data(free_form_phases=phases)
    await show_phase_settings(callback, state, idx)


@router.callback_query(FreeFormSetup.phase_settings, F.data == "ff_ps_timeout")
async def ask_timeout(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Снять тайм-аут", callback_data="ff_ps_timeout_clear")],
        [InlineKeyboardButton(text="← Назад", callback_data="ff_ps_timeout_back")],
    ])
    await render_screen(
        callback,
        "Введите тайм-аут в секундах (число от 1 до 600). "
        "Это лимит на одну пробу.",
        kb,
        state=state,
    )
    await state.set_state(FreeFormSetup.waiting_timeout)


@router.callback_query(FreeFormSetup.waiting_timeout, F.data == "ff_ps_timeout_clear")
async def clear_timeout(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    idx = data.get("ff_editing_phase_index")
    phases = list(data.get("free_form_phases", []))
    if idx is not None and 0 <= idx < len(phases):
        _patch_phase(phases, idx, time_limit=None)
        await state.update_data(free_form_phases=phases)
        await show_phase_settings(callback, state, idx)


@router.callback_query(FreeFormSetup.waiting_timeout, F.data == "ff_ps_timeout_back")
async def back_from_timeout(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    idx = data.get("ff_editing_phase_index")
    if idx is not None:
        await show_phase_settings(callback, state, idx)


@router.message(FreeFormSetup.waiting_timeout, F.text)
async def on_timeout_input(message: types.Message, state: FSMContext):
    txt = (message.text or "").strip()
    try:
        val = int(txt)
    except ValueError:
        await message.answer("Введите целое число секунд (или нажмите «Снять тайм-аут»).")
        return
    if not (1 <= val <= 600):
        await message.answer("Число должно быть от 1 до 600.")
        return
    data = await state.get_data()
    idx = data.get("ff_editing_phase_index")
    phases = list(data.get("free_form_phases", []))
    if idx is None or not (0 <= idx < len(phases)):
        await show_phases_summary(message, state)
        return
    _patch_phase(phases, idx, time_limit=val)
    await state.update_data(free_form_phases=phases)
    await show_phase_settings(message, state, idx)


@router.callback_query(FreeFormSetup.phase_settings, F.data == "ff_ps_rename")
async def on_rename_phase(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    idx = data.get("ff_editing_phase_index")
    phases = data.get("free_form_phases", [])
    if idx is None or not (0 <= idx < len(phases)):
        return
    cur = (phases[idx].get("title") or "").strip()
    cur_note = f"\n\n<i>Текущее: {cur}</i>" if cur else ""
    await render_screen(
        callback,
        "Введите новое имя фазы (до 60 символов). Чтобы вернуть "
        "автоматическое имя «Фаза N», отправьте «-»." + cur_note,
        None,
        state=state,
    )
    await state.set_state(FreeFormSetup.entering_phase_title)


@router.message(FreeFormSetup.entering_phase_title, F.text)
async def on_phase_title_input(message: types.Message, state: FSMContext):
    raw = (message.text or "").strip()
    data = await state.get_data()
    idx = data.get("ff_editing_phase_index")
    phases = list(data.get("free_form_phases", []))
    if idx is None or not (0 <= idx < len(phases)):
        await show_phases_summary(message, state)
        return

    if raw in ("-", "—", ""):
        # сброс на автоматическое имя — фаза снова синхронизируется с
        # позицией в списке.
        phases[idx]["title_auto"] = True
        _reindex_phases(phases)
    else:
        phases[idx]["title"] = raw[:60]
        phases[idx]["title_auto"] = False
    await state.update_data(free_form_phases=phases)
    await show_phase_settings(message, state, idx)


@router.callback_query(FreeFormSetup.phase_settings, F.data == "ff_ps_edit_instr")
async def on_edit_instr(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    idx = data.get("ff_editing_phase_index")
    phases = data.get("free_form_phases", [])
    if idx is None or not (0 <= idx < len(phases)):
        return
    cur = (phases[idx].get("instruction") or "").strip()
    note = f"\n\n<i>Текущая инструкция:</i>\n{cur}" if cur else ""
    await render_screen(
        callback, "Введите новую инструкцию:" + note, None, state=state,
    )
    await state.set_state(FreeFormSetup.entering_instruction)


@router.callback_query(FreeFormSetup.phase_settings, F.data == "ff_ps_replace_csv")
async def on_replace_csv(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    idx = data.get("ff_editing_phase_index")
    if idx is None:
        return
    await _enter_csv_flow(callback, state, editing_index=idx)


@router.callback_query(FreeFormSetup.phase_settings, F.data == "ff_ps_delete")
async def on_delete_phase(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    idx = data.get("ff_editing_phase_index")
    phases = list(data.get("free_form_phases", []))
    if idx is None or not (0 <= idx < len(phases)):
        return
    del phases[idx]
    _reindex_phases(phases)
    await state.update_data(
        free_form_phases=phases,
        ff_editing_phase_index=None,
    )
    await show_phases_summary(callback, state)


@router.callback_query(FreeFormSetup.phase_settings, F.data == "ff_ps_back")
async def on_phase_settings_back(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(ff_editing_phase_index=None)
    await show_phases_summary(callback, state)


# ── help: те же тексты, что в шаблонах. собственные хендлеры нужны,
#   потому что у researcher_settings они зарегистрированы на состояние
#   CreateExperiment.configuring, а во free_form-flow state другой.

@router.callback_query(FreeFormSetup.phase_settings, F.data == "ff_ps_help")
async def show_phase_help(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    from handlers.researcher_settings import _CONFIG_HELP_PAGE_1
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Дальше →", callback_data="ff_ps_help_2")],
        [InlineKeyboardButton(text="← Назад", callback_data="ff_ps_help_back")],
    ])
    await render_screen(callback, _CONFIG_HELP_PAGE_1, kb, state=state)
    await state.set_state(FreeFormSetup.phase_help)


@router.callback_query(FreeFormSetup.phase_help, F.data == "ff_ps_help_2")
async def show_phase_help_2(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    from handlers.researcher_settings import _CONFIG_HELP_PAGE_2
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Назад к странице 1", callback_data="ff_ps_help")],
        [InlineKeyboardButton(text="← К настройкам фазы", callback_data="ff_ps_help_back")],
    ])
    await render_screen(callback, _CONFIG_HELP_PAGE_2, kb, state=state)


@router.callback_query(FreeFormSetup.phase_help, F.data == "ff_ps_help")
async def back_to_help_1(callback: types.CallbackQuery, state: FSMContext):
    # переход «← Назад к странице 1» из второй страницы help
    await show_phase_help(callback, state)


@router.callback_query(FreeFormSetup.phase_help, F.data == "ff_ps_help_back")
async def help_back_to_phase(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    idx = data.get("ff_editing_phase_index")
    if idx is not None:
        await show_phase_settings(callback, state, idx)
    else:
        await show_phases_summary(callback, state)
