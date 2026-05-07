"""
Утилиты для извлечения имён медиа-файлов из проб.

Имя файла в trial может лежать в разных местах в зависимости от
шаблона: stimulus_content (picture_naming, sentence_repetition,
forced_choice, video_task), stimulus_metadata.images (picture_selection),
stimulus_metadata.img_1/2/3 (covered_box), auxiliary.correct_img и т.п.
Чтобы не плодить логику на каждый шаблон, ищем все строки, похожие на
имя медиа-файла, рекурсивно по этим полям.
"""

import os


MEDIA_EXTS: frozenset[str] = frozenset({
    ".wav", ".mp3", ".ogg", ".opus", ".m4a",
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp",
    ".mp4", ".mov", ".webm", ".mkv", ".avi",
})


def looks_like_media(s) -> bool:
    if not isinstance(s, str) or not s:
        return False
    return os.path.splitext(s.lower())[1] in MEDIA_EXTS


def scan_for_media(value, out: set[str]) -> None:
    """рекурсивно обойти dict/list/str и положить в out все строки,
    которые выглядят как имя медиа-файла."""
    if isinstance(value, str):
        if looks_like_media(value):
            out.add(value)
    elif isinstance(value, dict):
        for v in value.values():
            scan_for_media(v, out)
    elif isinstance(value, list):
        for v in value:
            scan_for_media(v, out)


def collect_trial_media(trial: dict) -> set[str]:
    """имена всех медиа-файлов, которые нужны конкретной пробе."""
    out: set[str] = set()
    for src in (
        trial.get("stimulus_content"),
        trial.get("stimulus_metadata"),
        trial.get("auxiliary"),
    ):
        scan_for_media(src, out)
    return out


def collect_experiment_media(experiment: dict) -> set[str]:
    """union по всем фазам и всем list_id (без фильтра): что должно
    быть загружено, чтобы эксперимент мог работать для всех участников."""
    out: set[str] = set()
    for phase in (experiment or {}).get("phases", []):
        if phase.get("stimulus_type") not in ("audio", "image", "video"):
            continue
        for trial in phase.get("trials", []):
            out |= collect_trial_media(trial)
    return out
