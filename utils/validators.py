"""
валидация CSV-файлов и настроек для каждого шаблона.
"""

import logging
from templates import registry as tmpl_registry

logger = logging.getLogger("bot")


def validate_experiment(experiment: dict) -> list[str]:
    """проверить корректность эксперимента перед публикацией"""
    errors = []

    if not experiment.get("title", "").strip():
        errors.append("Не задано название эксперимента.")

    phases = experiment.get("phases", [])
    if not phases:
        errors.append("Эксперимент не содержит ни одной фазы.")

    for i, phase in enumerate(phases):
        trials = phase.get("trials", [])
        if not trials:
            errors.append(f"Фаза {i + 1} не содержит проб.")

        stim_type = phase.get("stimulus_type", "text")
        if stim_type in ("audio", "image", "video"):
            for j, trial in enumerate(trials):
                meta = trial.get("stimulus_metadata", {})
                if not meta.get("file_id"):
                    errors.append(
                        f"Фаза {i + 1}, проба {j + 1}: "
                        f"не загружен медиафайл для «{trial.get('stimulus_content', '')}»."
                    )

    return errors


def validate_csv_for_template(template_code: str, rows: list[dict],
                              phase_num: int = 1) -> list[str]:
    """проверить CSV на соответствие шаблону"""
    errors = []

    if not rows:
        return ["CSV-файл пуст."]

    tmpl = tmpl_registry.get_template(template_code)
    if not tmpl:
        return []

    # определяем required_columns с учетом фазы
    phase_mappings = tmpl.get("phase_csv_mappings", {})
    if phase_num in phase_mappings:
        required = phase_mappings[phase_num].get("required_columns", [])
    else:
        required = tmpl.get("required_columns", [])

    columns = set(rows[0].keys())
    for col in required:
        if col not in columns:
            errors.append(f"Отсутствует обязательная колонка: «{col}».")

    # валидация video_task: все стимулы должны иметь одинаковое кол-во опций
    if template_code == "video_task":
        opt_cols = ["opt1", "opt2", "opt3", "opt4", "opt5", "opt6", "opt7"]
        counts = []
        for i, row in enumerate(rows):
            n = sum(1 for c in opt_cols if c in row and row[c].strip())
            counts.append(n)
        if counts and len(set(counts)) > 1:
            errors.append(
                "Все стимулы должны иметь одинаковое количество опций. "
                f"Найдены строки с разным числом: {sorted(set(counts))}."
            )

    return errors


def check_media_files(experiment: dict, uploaded_files: dict) -> list[str]:
    """проверить, что все нужные медиафайлы загружены"""
    errors = []
    for phase in experiment.get("phases", []):
        if phase.get("stimulus_type") in ("audio", "image", "video"):
            for trial in phase.get("trials", []):
                stim = trial.get("stimulus_content", "")
                if stim and stim not in uploaded_files:
                    errors.append(f"Не загружен файл: «{stim}».")
    return errors
