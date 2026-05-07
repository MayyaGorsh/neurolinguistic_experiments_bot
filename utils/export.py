"""
экспорт результатов эксперимента.
- export_experiment_csv: только CSV (используется для preflight-проверки
  перед удалением).
- export_experiment_bundle: zip с CSV + всеми голосовыми ответами, если
  они есть; иначе одиночный CSV. preview-сессии исключаются.
template-specific колонки добавляются с исходными именами.
"""

import csv
import io
import logging
import os
import zipfile
from typing import Optional

from db import repositories as repo
from templates import registry as tmpl_registry

logger = logging.getLogger("bot")

VOICE_PREFIX = "voice:"


async def export_experiment_csv(experiment_id: str) -> str:
    """сформировать CSV-строку со всеми ответами по эксперименту"""
    experiment = await repo.get_experiment(experiment_id)
    all_answers = await repo.get_answers_by_experiment(experiment_id)
    all_sessions = await repo.get_sessions_by_experiment(experiment_id)

    # фильтруем preview-сессии
    real_sessions = [s for s in all_sessions if not s.get("is_preview", False)]
    real_session_ids = {str(s["_id"]) for s in real_sessions}
    answers = [a for a in all_answers if a.get("session_id") in real_session_ids]

    # словарь сессий для быстрого доступа
    sess_map = {}
    for s in real_sessions:
        sess_map[str(s["_id"])] = s

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")

    # базовый заголовок
    header = [
        "participant_id",
        "session_id",
        "experiment_id",
        "template_type",
        "assigned_list",
        "phase_index",
        "trial_index",
        "stimulus_id",
        "raw_response",
        "normalized_response",
        "is_correct",
        "reaction_time_ms",
        "timed_out",
        "timestamp",
    ]

    # template-specific колонки
    template_type = experiment.get("template_type", "") if experiment else ""
    tmpl_info = tmpl_registry.get_template(template_type)
    export_columns = []
    if tmpl_info:
        export_columns = tmpl_info.get("export_columns", [])
    header.extend(export_columns)

    # колонки демографии
    demo_keys = set()
    for s in real_sessions:
        demo_keys.update(s.get("demographics", {}).keys())
    demo_keys = sorted(demo_keys)
    header.extend(demo_keys)

    writer.writerow(header)

    # индекс проб строится **по сессии** — у каждой свой prepared_phases
    # (рандомизация, фильтр по листу). если строить общий индекс по
    # experiment.phases, при включённой рандомизации
    # (phase_index, trial_index) ответа покажет на чужую пробу, и
    # template-колонки/имена аудио будут разъезжаться с тем, что слышал
    # участник.
    index_cache: dict[str, dict] = {}

    # строки
    for ans in answers:
        sess_id = ans.get("session_id", "")
        sess = sess_map.get(sess_id, {})
        trial_index = _get_trial_index(sess, experiment, index_cache)
        row = [
            sess.get("telegram_id", ""),
            sess_id,
            ans.get("experiment_id", ""),
            template_type,
            sess.get("assigned_list", ""),
            ans.get("phase_index", ""),
            ans.get("trial_index", ""),
            ans.get("stimulus_id", ""),
            ans.get("raw_response", ""),
            ans.get("normalized_response", ""),
            ans.get("is_correct", ""),
            ans.get("reaction_time_ms", ""),
            ans.get("timed_out", ""),
            ans.get("timestamp", ""),
        ]

        # template-specific значения
        for col in export_columns:
            val = extract_template_value(ans, trial_index, col)
            row.append(val)

        # демография
        demo = sess.get("demographics", {})
        for key in demo_keys:
            row.append(demo.get(key, ""))

        writer.writerow(row)

    return output.getvalue()


def _get_trial_index(
    session: dict, experiment: Optional[dict], cache: dict[str, dict],
) -> dict:
    """вернуть (с кэшем по session_id) индекс проб именно той сессии,
    в которой ответил участник: prepared_phases, если есть, иначе
    общий experiment.phases.
    """
    sess_id = str(session.get("_id", "")) if session else ""
    if sess_id and sess_id in cache:
        return cache[sess_id]

    phases = (session or {}).get("prepared_phases")
    if not phases and experiment:
        phases = experiment.get("phases", [])
    index = build_trial_index_from_phases(phases or [])

    if sess_id:
        cache[sess_id] = index
    return index


def build_trial_index_from_phases(phases: list) -> dict:
    """построить (phase_index, trial_index) -> trial из готового списка
    фаз. Индексы берём строго как позиции — runner сохраняет в ответ
    session.current_phase/current_trial, которые тоже позиционные."""
    index = {}
    for pi, phase in enumerate(phases):
        for ti, trial in enumerate(phase.get("trials", [])):
            index[(pi, ti)] = trial
    return index


def build_trial_index(experiment: dict) -> dict:
    """legacy-обёртка: общий индекс по experiment.phases. Сохранена на
    случай, если из неё что-то импортируют со стороны."""
    if not experiment:
        return {}
    return build_trial_index_from_phases(experiment.get("phases", []))


async def export_experiment_bundle(
    bot, experiment_id: str,
) -> tuple[Optional[bytes], str]:
    """собрать выгрузку: если у эксперимента есть голосовые ответы,
    вернуть zip (results.csv + папка responses/ с .ogg-файлами,
    названными как «<имя стимула>__<session_id>.ogg»). Иначе — голый
    CSV. Возвращает (None, "") если данных нет вовсе.

    Голосовые ответы читаются из GridFS, куда складываются сразу при
    получении сообщения. Для записей из истории, где блоб не сохранили,
    предусмотрен fallback на скачивание по telegram file_id — ровно
    одна попытка на ответ.
    """
    csv_text = await export_experiment_csv(experiment_id)
    if not csv_text or not csv_text.strip():
        return None, ""

    experiment = await repo.get_experiment(experiment_id)
    all_answers = await repo.get_answers_by_experiment(experiment_id)
    all_sessions = await repo.get_sessions_by_experiment(experiment_id)

    real_session_ids = {
        str(s["_id"]) for s in all_sessions if not s.get("is_preview", False)
    }
    voice_answers = [
        a for a in all_answers
        if a.get("session_id") in real_session_ids
        and isinstance(a.get("raw_response"), str)
        and a["raw_response"].startswith(VOICE_PREFIX)
    ]

    csv_bytes = csv_text.encode("utf-8-sig")
    if not voice_answers:
        return csv_bytes, "csv"

    sess_map = {str(s["_id"]): s for s in all_sessions}
    index_cache: dict[str, dict] = {}

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("results.csv", csv_bytes)

        used_names: set[str] = set()
        for ans in voice_answers:
            data = await _load_voice_bytes(ans, bot)
            if data is None:
                continue

            sess = sess_map.get(ans.get("session_id"), {})
            trial_index = _get_trial_index(sess, experiment, index_cache)
            stim_name = _stimulus_filename(ans, trial_index)
            session_id = str(ans.get("session_id", ""))
            stem, ext = _split_stem_ext(stim_name, default_ext=".ogg")

            base = f"{stem}__{session_id}{ext}"
            arc_name = f"responses/{base}"
            # маловероятно, но защитимся от коллизий
            if arc_name in used_names:
                ti = ans.get("trial_index", "")
                base = f"{stem}__{session_id}_t{ti}{ext}"
                arc_name = f"responses/{base}"
            used_names.add(arc_name)

            zf.writestr(arc_name, data)

    return buf.getvalue(), "zip"


async def _load_voice_bytes(answer: dict, bot) -> Optional[bytes]:
    """достать байты голосового ответа: сначала из GridFS, иначе
    fallback на скачивание по telegram file_id (для старых записей,
    созданных до того, как мы стали кэшировать блобы локально)."""
    blob_id = (answer.get("metadata") or {}).get("voice_blob_id")
    if blob_id:
        data = await repo.read_voice_blob(blob_id)
        if data is not None:
            return data

    raw = answer.get("raw_response") or ""
    if not raw.startswith(VOICE_PREFIX):
        return None
    file_id = raw[len(VOICE_PREFIX):].strip()
    if not file_id:
        return None
    try:
        file = await bot.download(file_id)
        return file.read()
    except Exception as e:
        logger.warning(
            "voice ответ %s недоступен (нет блоба, telegram download упал): %s",
            answer.get("_id"), e,
        )
        return None


def _stimulus_filename(answer: dict, trial_index: dict) -> str:
    """достать имя исходного аудио-стимула для пробы (audio_filename)."""
    key = (answer.get("phase_index", 0), answer.get("trial_index", 0))
    trial = trial_index.get(key, {})
    stim_meta = trial.get("stimulus_metadata", {})
    name = stim_meta.get("audio_filename") or stim_meta.get("filename") or ""
    if name:
        return _sanitize(os.path.basename(str(name)))
    sid = answer.get("stimulus_id") or "stimulus"
    return _sanitize(str(sid))


def _split_stem_ext(name: str, default_ext: str = ".ogg") -> tuple[str, str]:
    stem, ext = os.path.splitext(name)
    if not stem:
        stem = "stimulus"
    if not ext:
        ext = default_ext
    return stem, ext


_FORBIDDEN = '<>:"/\\|?*\x00'

def _sanitize(name: str) -> str:
    """убрать символы, недопустимые в путях zip/файловых системах."""
    cleaned = "".join("_" if ch in _FORBIDDEN else ch for ch in name)
    return cleaned.strip(" .") or "file"


def extract_template_value(answer: dict, trial_index: dict, column: str) -> str:
    """извлечь значение template-specific колонки из ответа или пробы"""
    # сначала ищем в metadata ответа
    meta = answer.get("metadata", {})
    if column in meta:
        return str(meta[column])

    # потом ищем в stimulus_metadata и auxiliary пробы
    key = (answer.get("phase_index", 0), answer.get("trial_index", 0))
    trial = trial_index.get(key, {})

    stim_meta = trial.get("stimulus_metadata", {})
    if column in stim_meta:
        return str(stim_meta[column])

    aux = trial.get("auxiliary", {})
    if column in aux:
        return str(aux[column])

    return ""
