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


def get_response_options(config: dict, defaults: list, key: str = "main") -> list:
    """вернуть пользовательские метки кнопок, если их длина совпадает с дефолтом.
    сохраняет позиционное соответствие: options[0] = та же «роль», что defaults[0]."""
    overrides = (config or {}).get("custom_buttons", {}) or {}
    o = overrides.get(key)
    if isinstance(o, list) and len(o) == len(defaults) and all(
        isinstance(x, str) and x.strip() for x in o
    ):
        return [x.strip() for x in o]
    return list(defaults)


def get_likert_config(config: dict, defaults: dict, key: str = "main") -> dict:
    """вернуть настройки Likert-шкалы (scale + labels) с учётом пользовательских override'ов"""
    defaults = dict(defaults or {})
    overrides = (config or {}).get("custom_likert", {}) or {}
    o = overrides.get(key)
    if not isinstance(o, dict):
        return defaults
    scale = o.get("scale") if isinstance(o.get("scale"), int) else defaults.get("scale", 5)
    # labels: словарь {"1": "...", "N": "..."}
    labels = dict(defaults.get("labels") or {})
    override_labels = o.get("labels") or {}
    if isinstance(override_labels, dict):
        for k, v in override_labels.items():
            if isinstance(v, str) and v.strip():
                labels[str(k)] = v.strip()
    return {"scale": scale, "labels": labels}


# ── импорт всех шаблонов, чтобы они зарегистрировались ──

from templates import (
    word_level,
    sentence_level,
    auditory,
    visual,
)
