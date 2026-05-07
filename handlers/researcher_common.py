"""общая инфраструктура кабинета исследователя:
- aiogram Router (его все researcher_*-модули используют для регистрации
  своих хендлеров — см. researcher.py, который импортирует все подмодули
  и тем самым запускает декораторы);
- класс FSM-состояний CreateExperiment;
- список шаблонов (код → лейбл) и их пользовательские описания;
- мелкие хелперы, которые нужны нескольким подмодулям.

Все researcher_*-файлы делают `from handlers.researcher_common import ...`,
а сам researcher.py — это тонкий оркестратор: импортирует router отсюда,
подгружает подмодули и реэкспортирует пару функций для backwards-совместимости
с handlers.free_form / handlers.media_upload."""

import logging

from aiogram import Router
from aiogram.fsm.state import StatesGroup, State

from utils.ui import render_screen as _render_screen  # noqa: F401 — реэкспорт для подмодулей
from templates import registry as tmpl_registry


router = Router()
logger = logging.getLogger("bot")


# ── состояния FSM для создания эксперимента ──

class CreateExperiment(StatesGroup):
    choosing_template = State()
    entering_title = State()
    entering_description = State()
    configuring = State()
    uploading_csv = State()
    uploading_media = State()
    uploading_demographics = State()


# ── список шаблонов ──

TEMPLATE_LIST = [
    ("lexical_decision", "Lexical decision"),
    ("predictability_rating", "Predictability rating"),
    ("cloze_mc", "Cloze (multiple choice)"),
    ("cloze_open", "Cloze (open ended)"),
    ("word_translation_mc", "Word translation (closed)"),
    ("word_translation_open", "Word translation (open)"),
    ("sensicality_judgment", "Sensicality judgment"),
    ("acceptability_judgment", "Acceptability judgment"),
    ("tvjt", "Truth Value Judgment Task"),
    ("statement_verification", "Statement verification"),
    ("self_paced_reading", "Self-Paced Reading"),
    ("maze", "Maze task"),
    ("text_change_detection", "Text change detection"),
    ("probe_recognition", "Probe recognition"),
    ("interpretation_generation", "Interpretation generation"),
    ("forced_choice", "Forced choice identification"),
    ("sentence_repetition", "Sentence repetition"),
    ("picture_selection", "Picture selection"),
    ("covered_box", "Covered box"),
    ("picture_naming", "Picture naming"),
    ("video_task", "Video task"),
    ("free_form", "Свободный формат"),
]

TEMPLATE_LABELS = dict(TEMPLATE_LIST)

# короткое описание шаблона + ссылка на исследование, на которое опирается дизайн.
# показывается после выбора шаблона, перед вводом названия эксперимента.
TEMPLATE_DESCRIPTIONS: dict[str, str] = {
    "lexical_decision": (
        "Респондент как можно быстрее решает, является ли предъявленная "
        "строка реальным словом языка или нет; ответ — кнопками "
        "«Слово»/«Не слово». Бот фиксирует точность и время реакции от "
        "появления стимула до нажатия.\n"
        "Методология: Diependaele et al. (2012)."
    ),
    "predictability_rating": (
        "Респондент видит предложение с пропуском и предполагаемое слово "
        "в этом пропуске, после чего по шкале Ликерта оценивает, насколько "
        "ожидаемо это слово в данном контексте. Бот сохраняет ответ и "
        "время реакции.\n"
        "Методология: Gregor de Varda et al. (2024)."
    ),
    "cloze_mc": (
        "Респонденту предъявляется предложение с пропуском «___» и "
        "вариантами ответа на кнопках; нужно выбрать слово, наиболее "
        "подходящее в пропуск. Бот сохраняет выбранный вариант, "
        "корректность и время реакции.\n"
        "Методология: Keshavarz & Salimi (2007); Kleijn (2018)."
    ),
    "cloze_open": (
        "Респонденту предъявляется предложение с пропуском «___», ответ "
        "вводится текстом. Введённая строка сверяется со списком "
        "допустимых вариантов, заданным исследователем (до 10 на пункт).\n"
        "Методология: Keshavarz & Salimi (2007); Kleijn (2018); Trace (2020)."
    ),
    "word_translation_mc": (
        "Респондент видит слово на исходном языке и выбирает перевод на "
        "целевой язык из вариантов на кнопках. Бот фиксирует выбор и "
        "время реакции от предъявления до первого нажатия.\n"
        "Методология: Golubović & Gooskens (2015)."
    ),
    "word_translation_open": (
        "Респондент видит слово на исходном языке и вводит перевод "
        "текстом. Время реакции не фиксируется, так как Telegram не "
        "отслеживает момент начала ввода сообщения.\n"
        "Методология: Golubović & Gooskens (2015)."
    ),
    "sensicality_judgment": (
        "Респондент как можно быстрее решает, является ли предложение "
        "осмысленным; бинарный ответ кнопками «Осмысленно»/«Неосмысленно». "
        "Бот фиксирует точность и время реакции.\n"
        "Методология: Bambini et al. (2013)."
    ),
    "acceptability_judgment": (
        "Респондент оценивает, насколько предложение звучит естественно "
        "для носителя языка. Настраиваемый формат ответа (yes-no или "
        "шкала Ликерта), подписи кнопок и режим предъявления (одиночная "
        "подача или совместная подача двух предложений с одной или двумя "
        "оценками).\n"
        "Методология: Хомский (1957); Marty et al. (2020)."
    ),
    "tvjt": (
        "Респонденту предъявляется (опционально) краткий контекст и "
        "утверждение о нём; задача — оценить истинность реплики кнопками. "
        "Опционально можно попросить респондента обосновать свой ответ "
        "текстом.\n"
        "Методология: Lidz & Musolino (2002)."
    ),
    "statement_verification": (
        "Респондент оценивает истинность утверждений на основе общего "
        "знания кнопками. После основной части проводится контроль "
        "знания фактов отдельными вопросами.\n"
        "Методология: Bredart & Modolo (1988)."
    ),
    "self_paced_reading": (
        "Предложение предъявляется по словам или сегментам: каждое "
        "нажатие кнопки «Далее» добавляет следующий фрагмент. Бот "
        "фиксирует время чтения каждого сегмента как интервал между "
        "нажатиями.\n"
        "Методология: Swets et al. (2007); Marsden et al. (2018)."
    ),
    "maze": (
        "Респондент читает предложение по словам, на каждой позиции "
        "выбирая корректное продолжение из двух кнопок (целевое слово и "
        "дистрактор; стороны рандомизируются). При ошибке проба "
        "прекращается. Бот фиксирует время между нажатиями.\n"
        "Методология: Boyce et al. (2020)."
    ),
    "text_change_detection": (
        "Один и тот же короткий текст предъявляется дважды; респондент "
        "сообщает, было ли отличие, а если было — называет исходное слово "
        "и слово замены. Корректность засчитывается только при точной "
        "идентификации подстановки.\n"
        "Методология: Sanford et al. (2005)."
    ),
    "probe_recognition": (
        "Двухфазный эксперимент: на фазе обучения респондент "
        "последовательно запоминает фразы, на фазе теста решает, "
        "встречалось ли выделенное заглавными буквами слово на первой "
        "фазе. Бот фиксирует корректность и время реакции.\n"
        "Методология: Light & Carter-Sobell (1970); Klein & Murphy (2001)."
    ),
    "interpretation_generation": (
        "Респонденту предъявляется предложение и кнопка «Далее»; после "
        "нажатия стимул удаляется, и бот просит ввести парафраз текстом.\n"
        "Методология: Patson et al. (2009)."
    ),
    "forced_choice": (
        "На каждом шаге респондент слушает аудио-стимул и выбирает один "
        "из заданных классов кнопками (2 и более альтернатив). Подходит "
        "для задач фонемной категоризации; бот рандомизирует порядок "
        "предъявления и фиксирует выбор и время реакции.\n"
        "Методология: Kapnoula et al. (2017)."
    ),
    "sentence_repetition": (
        "Респондент слушает аудио-стимул, затем записывает голосовое "
        "сообщение с устным воспроизведением предложения. Бот сохраняет "
        "аудиофайлы и метаданные; разметка корректности — на стороне "
        "исследователя. Можно задать лимит времени отдельно на "
        "прослушивание и на запись ответа.\n"
        "Методология: Potter & Lombardi (1998)."
    ),
    "picture_selection": (
        "Респонденту предъявляется предложение и пара изображений; нужно "
        "выбрать картинку, лучше соответствующую описанию. Бот фиксирует "
        "выбор, корректность (если задана) и время реакции; порядок "
        "картинок в паре можно рандомизировать автоматически.\n"
        "Методология: Qiu et al. (2025)."
    ),
    "covered_box": (
        "Расширение Picture selection: к двум видимым картинкам "
        "добавляется третья «закрытая» опция, которую респондент может "
        "выбрать, если ни один видимый вариант не соответствует "
        "описанию точно. Используется для разведения семантики и "
        "прагматических выводов.\n"
        "Методология: Huang et al. (2013)."
    ),
    "picture_naming": (
        "Респонденту предъявляется изображение, ответ — название одним "
        "словом или короткой именной группой текстом или голосовым "
        "сообщением. Время реакции не фиксируется, так как Telegram не "
        "отслеживает момент начала ввода.\n"
        "Методология: Krautz & Keuleers (2022)."
    ),
    "video_task": (
        "На каждой пробе предъявляется видео и набор кнопок, заданных "
        "исследователем (например, градации шкалы Ликерта или варианты "
        "act-out). Бот сохраняет выбранную кнопку и время от "
        "предъявления видео до первого нажатия.\n"
        "Методология: Deliens et al. (2018)."
    ),
    "free_form": (
        "Свободный конструктор: вы сами задаёте последовательность шагов "
        "эксперимента (текст, кнопки, ввод текста, аудио, видео и т. д.) "
        "без предустановленного дизайна. Подходит, если ни один из "
        "стандартных шаблонов не описывает вашу процедуру."
    ),
}


# ── AJT-специфичные хелперы ──

_AJT_PRESENTATION_LABELS = {
    "single": "одиночная",
    "joint_one_rating": "совместная (одна оценка)",
    "joint_two_ratings": "совместная (две оценки)",
}
_AJT_PRESENTATION_CYCLE = ["single", "joint_one_rating", "joint_two_ratings"]


def _ajt_has_stimulus2(data: dict) -> bool:
    """в загруженном CSV (state.csv_data) есть непустая колонка stimulus2.

    тоггл «режим подачи» имеет смысл только когда в данных реально есть
    второе предложение, иначе joint-режимы вырождаются в single."""
    csv_data = data.get("csv_data") or {}
    for trials in csv_data.values():
        for t in (trials or []):
            aux = t.get("auxiliary") or {}
            if aux.get("stimulus2"):
                return True
    return False


def _ajt_csv_has_response_options(data: dict) -> bool:
    """в загруженном CSV у проб есть непустые response_options (opt1..optN).

    нужно для управления кнопкой «📊 Шкала ответа»: для AJT эта настройка
    влияет только в режиме «опций нет — числовая Likert». если CSV уже
    загружен с явными подписями кнопок, Likert-конфиг просто игнорируется,
    и кнопку лучше скрыть, чтобы не сбивать с толку."""
    csv_data = data.get("csv_data") or {}
    for trials in csv_data.values():
        for t in (trials or []):
            if t.get("response_options"):
                return True
    return False


# ── шаблонные хелперы ──

def _template_has_buttons(tmpl_code: str) -> bool:
    """есть ли в шаблоне хоть одна фаза с response_type="buttons".

    Используем для скрытия кнопочно-специфичных опций в конфиге
    (например, рандомизации позиций кнопок) для шаблонов с
    open_text/voice/likert.
    """
    tmpl = tmpl_registry.get_template(tmpl_code) or {}
    build_fn = tmpl.get("build_phase")
    phases_info = tmpl.get("phases_info") or ["Основная фаза"]
    if not build_fn:
        # free_form и т.п. — на всякий случай показываем опцию
        return True
    for i in range(len(phases_info)):
        try:
            phase = build_fn([], {}, i) or {}
            settings = phase.get("settings", {}) or {}
            # SPR — единственная кнопка «Далее», шаффлить нечего;
            # Maze сам мешает target/distractor попробно (см. build_maze),
            # глобальная рандомизация для него тоже бессмысленна.
            if settings.get("is_spr") or settings.get("is_maze"):
                continue
            if phase.get("response_type") in ("buttons", "buttons_then_text"):
                return True
        except Exception:
            # если шаблон не может построить фазу без trials — допускаем,
            # что кнопки могут быть, не прячем опцию
            return True
    return False


def _csv_template_phases(data: dict) -> list[str]:
    """вернуть список фаз шаблона для текущего эксперимента."""
    template_type = data.get("template_type", "free_form")
    tmpl_info = tmpl_registry.get_template(template_type)
    phases_info = ["Основная фаза"]
    if tmpl_info:
        phases_info = tmpl_info.get("phases_info", ["Основная фаза"]) or ["Основная фаза"]
    return phases_info


def _reset_input_flags() -> dict:
    """очистить waiting_*-флаги ввода, чтобы следующее текстовое сообщение
    не было интерпретировано как недозавершённый ввод тайм-аута, метки
    кнопки и т.п. — используем при любом «выходе наверх» из суб-экрана."""
    return {
        "waiting_button_edit": None,
        "waiting_likert_edit": None,
        "waiting_instruction_edit": None,
        "waiting_description_edit": False,
        "waiting_timeout": False,
        "waiting_lists_count": False,
    }


def auto_detect_mapping(rows: list[dict]) -> dict:
    """попытка автоматически определить маппинг колонок CSV.

    Используется как fallback, когда шаблон не зарегистрирован в реестре
    (например, в free_form). handlers.free_form тоже импортирует эту
    функцию (через handlers.researcher для backwards-совместимости —
    см. реэкспорт в handlers/researcher.py)."""
    if not rows:
        return {}
    cols = list(rows[0].keys())
    mapping = {}

    # первая колонка — стимул
    if cols:
        mapping["stimulus_content"] = cols[0]

    # ищем колонку correct
    for c in cols:
        if "correct" in c.lower():
            mapping["correct_answer"] = c
            break

    # остальные — варианты ответа (кроме стимула, correct и list_id)
    skip = {mapping.get("stimulus_content"), mapping.get("correct_answer"), "list_id"}
    opt_cols = [c for c in cols if c not in skip]
    if opt_cols:
        mapping["response_options"] = opt_cols

    return mapping
