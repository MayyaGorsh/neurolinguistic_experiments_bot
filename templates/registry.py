"""
реестр шаблонов экспериментов.
каждый шаблон описывает:
- required_columns: обязательные колонки CSV
- csv_mapping: маппинг колонок CSV -> полей trial
- build_phases: функция, формирующая фазы из загруженных проб
- export_columns: колонки, которые добавляются в CSV-экспорт (с исходными именами)
- phases_info: список названий фаз (для шаблонов с >1 фазой, чтобы запрашивать CSV по фазам)
"""

_TEMPLATES: dict = {}


def register(code: str, info: dict):
    """зарегистрировать шаблон"""
    _TEMPLATES[code] = info


def get_template(code: str) -> dict | None:
    return _TEMPLATES.get(code)


def all_templates() -> dict:
    return dict(_TEMPLATES)


# ── импорт всех шаблонов, чтобы они зарегистрировались ──

from templates import (
    word_level,
    sentence_level,
    auditory,
    visual,
)
