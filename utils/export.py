"""
экспорт результатов эксперимента в CSV.
preview-сессии исключаются из выгрузки.
template-specific колонки добавляются с исходными именами.
"""

import csv
import io
import logging
from typing import Optional

from db import repositories as repo
from templates import registry as tmpl_registry

logger = logging.getLogger("bot")


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

    # собираем индекс проб для быстрого доступа к metadata
    trial_index = build_trial_index(experiment)

    # строки
    for ans in answers:
        sess = sess_map.get(ans.get("session_id"), {})
        row = [
            sess.get("telegram_id", ""),
            ans.get("session_id", ""),
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


def build_trial_index(experiment: dict) -> dict:
    """построить индекс (phase_index, trial_index) -> trial для быстрого доступа"""
    index = {}
    if not experiment:
        return index
    for phase in experiment.get("phases", []):
        pi = phase.get("phase_index", 0)
        for trial in phase.get("trials", []):
            ti = trial.get("trial_index", 0)
            index[(pi, ti)] = trial
    return index


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
