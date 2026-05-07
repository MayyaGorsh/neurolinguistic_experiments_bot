"""
валидация CSV-файлов и настроек для каждого шаблона.
"""

import logging
from templates import registry as tmpl_registry
from utils.media import collect_trial_media

logger = logging.getLogger("bot")


def validate_experiment(experiment: dict) -> list[str]:
    """проверить корректность эксперимента перед публикацией"""
    errors = []

    if not experiment.get("title", "").strip():
        errors.append("Не задано название эксперимента.")

    phases = experiment.get("phases", [])
    if not phases:
        errors.append("Эксперимент не содержит ни одной фазы.")

    use_lists = experiment.get("use_lists", False)
    # набор листов для каждой фазы (для проверки консистентности)
    phase_list_sets: list[set[str]] = []

    for i, phase in enumerate(phases):
        trials = phase.get("trials", [])
        if not trials:
            errors.append(f"Фаза {i + 1} не содержит проб.")
            phase_list_sets.append(set())
            continue

        # собираем list_id для этой фазы
        list_ids = set()
        for trial in trials:
            lid = str(trial.get("list_id") or "1")
            list_ids.add(lid)
        phase_list_sets.append(list_ids)

        # проверка медиа: смотрим все имена файлов, которые трайл
        # реально использует (для multi-image шаблонов имена лежат в
        # stimulus_metadata, а не в stimulus_content), и сверяемся с
        # blob_ids (картинки в GridFS) либо file_ids (аудио/видео по
        # Telegram file_id), которые заполняет attach_media_ids_to_phases.
        stim_type = phase.get("stimulus_type", "text")
        if stim_type in ("audio", "image", "video"):
            for j, trial in enumerate(trials):
                needed = collect_trial_media(trial)
                if not needed:
                    continue
                meta = trial.get("stimulus_metadata", {}) or {}
                attached = set((meta.get("blob_ids") or {}).keys())
                attached |= set((meta.get("file_ids") or {}).keys())
                stim = trial.get("stimulus_content", "")
                if isinstance(stim, str) and (
                    meta.get("blob_id") or meta.get("file_id")
                ):
                    attached.add(stim)
                missing = sorted(needed - attached)
                if missing:
                    files_part = ", ".join(f"«{m}»" for m in missing)
                    errors.append(
                        f"Фаза {i + 1}, проба {j + 1}: "
                        f"не загружен(ы) медиафайл(ы) {files_part}."
                    )

        # проверка, что у проб есть содержимое
        empty_stim = sum(
            1 for t in trials if not str(t.get("stimulus_content", "")).strip()
        )
        if empty_stim == len(trials):
            errors.append(
                f"Фаза {i + 1}: все пробы с пустым содержанием. "
                "Проверьте CSV — вероятно, неправильные колонки."
            )

    # проверка листов: при use_lists=True у всех фаз должен быть одинаковый набор
    if use_lists and phase_list_sets:
        non_empty = [s for s in phase_list_sets if s]
        if non_empty:
            reference = non_empty[0]
            for i, s in enumerate(phase_list_sets):
                if not s:
                    continue
                if s != reference:
                    missing = reference - s
                    extra = s - reference
                    msg = f"Фаза {i + 1}: несовпадение листов с фазой 1."
                    if missing:
                        msg += f" Не хватает: {', '.join(sorted(missing))}."
                    if extra:
                        msg += f" Лишние: {', '.join(sorted(extra))}."
                    errors.append(msg)
            if len(reference) < 2:
                errors.append(
                    "Включено «распределение по листам», но загружен только "
                    "один лист. Загрузите минимум два или отключите листы."
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

    # определяем required_columns и маппинг с учетом фазы
    phase_mappings = tmpl.get("phase_csv_mappings", {})
    if phase_num in phase_mappings:
        pm = phase_mappings[phase_num]
        required = pm.get("required_columns", [])
        mapping = {k: v for k, v in pm.items() if k != "required_columns"}
    else:
        required = tmpl.get("required_columns", [])
        mapping = tmpl.get("csv_mapping", {})

    columns = set(rows[0].keys())

    # 1. обязательные колонки
    missing_cols = [c for c in required if c not in columns]
    for c in missing_cols:
        errors.append(f"Отсутствует обязательная колонка: «{c}».")
    # если колонок нет — дальше проверять бессмысленно; даём подсказку
    if missing_cols:
        if len(columns) == 1:
            only_col = next(iter(columns))
            if any(d in only_col for d in (";", ",", "\t")):
                errors.append(
                    "Похоже, в файле неверный разделитель — все колонки "
                    "слиплись в одну. Поддерживаются «;» и табуляция "
                    "(запятая не поддерживается, так как она часто "
                    "встречается внутри стимулов). Сохраните файл как "
                    "«CSV UTF-8 (разделитель — точка с запятой)»."
                )
        return errors

    # 2. в колонке стимула должно быть содержимое хотя бы в части строк
    stim_col = mapping.get("stimulus_content")
    if stim_col and stim_col in columns:
        empty = sum(1 for r in rows if not str(r.get(stim_col, "")).strip())
        if empty == len(rows):
            errors.append(
                f"Колонка «{stim_col}» во всех строках пустая. "
                "Нечего показывать участникам."
            )
        elif empty > 0:
            errors.append(
                f"В колонке «{stim_col}» есть {empty} пустых строк из "
                f"{len(rows)}. Пустые строки будут пропущены."
            )

    # 3. video_task: все стимулы должны иметь одинаковое кол-во опций
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
        if phase.get("stimulus_type") not in ("audio", "image", "video"):
            continue
        for trial in phase.get("trials", []):
            for fn in collect_trial_media(trial):
                if fn not in uploaded_files:
                    errors.append(f"Не загружен файл: «{fn}».")
    return errors
