"""
парсинг CSV-файлов со стимулами.
общий фреймворк для загрузки, валидации и преобразования в trials.
"""

import csv
import io
import logging
from typing import Optional

logger = logging.getLogger("bot")


def _detect_delimiter(first_line: str) -> str:
    """разделитель CSV — всегда «;».

    раньше детектили между «;» и табуляцией, но это путало лингвистов
    (Excel мог сохранить с табом, а пример был с «;») и иногда тихо
    парсило «не то». теперь единый формат: только точка с запятой.
    если файл сохранён с другим разделителем — DictReader увидит одну
    «жирную» колонку и валидация поймает это как «не найдены колонки».
    """
    return ";"


def parse_csv_text(text: str) -> list[dict]:
    """прочитать CSV-текст и вернуть список словарей (строк).
    разделитель определяется автоматически: ; , или \\t."""
    if not text:
        return []
    # срезаем BOM (Excel добавляет)
    if text.startswith("\ufeff"):
        text = text[1:]
    first_line = text.split("\n", 1)[0]
    delim = _detect_delimiter(first_line)
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    rows = []
    for row in reader:
        # None-ключ появляется, если строк больше колонок (трэш справа)
        row.pop(None, None)
        rows.append(dict(row))
    return rows


def parse_csv_bytes(data: bytes, encoding: str = "utf-8") -> list[dict]:
    """прочитать CSV из байтов"""
    try:
        text = data.decode(encoding)
    except UnicodeDecodeError:
        text = data.decode("cp1251")
    return parse_csv_text(text)


def validate_columns(rows: list[dict], required: list[str]) -> list[str]:
    """проверить наличие обязательных колонок, вернуть список ошибок"""
    if not rows:
        return ["CSV-файл пуст."]
    errors = []
    columns = set(rows[0].keys())
    for col in required:
        if col not in columns:
            errors.append(f"Отсутствует обязательная колонка: {col}")
    return errors


def rows_to_trials(rows: list[dict], mapping: dict) -> list[dict]:
    """
    преобразовать строки CSV в список trial-объектов.
    mapping задает соответствие: ключ trial -> имя колонки CSV.

    пример mapping:
    {
        "stimulus_content": "stimulus",
        "correct_answer": "correct",
        "response_options": ["opt1", "opt2", "opt3"],
    }
    """
    trials = []
    for i, row in enumerate(rows):
        trial = {
            "trial_index": i,
            "stimulus_content": "",
            "stimulus_type": "text",
            "stimulus_metadata": {},
            "response_options": [],
            "correct_answer": None,
            "auxiliary": {},
            "list_id": row.get("list_id"),
        }

        # стимул. csv.DictReader ставит None для ячеек, которых в строке
        # вообще нет (когда у строки меньше полей, чем в шапке — допустимый
        # сценарий для шаблонов с переменным числом опций, например phase 2
        # statement_verification). нормализуем None к пустой строке заранее.
        stim_col = mapping.get("stimulus_content", "stimulus")
        if stim_col in row:
            trial["stimulus_content"] = row[stim_col] or ""

        # правильный ответ
        correct_col = mapping.get("correct_answer")
        if correct_col and correct_col in row:
            val = (row[correct_col] or "").strip()
            if val:
                trial["correct_answer"] = val

        # варианты ответа
        opt_cols = mapping.get("response_options", [])
        if isinstance(opt_cols, list):
            options = []
            for col in opt_cols:
                cell = row.get(col)
                if cell is None:
                    continue
                val = cell.strip()
                if not val:
                    continue
                # помечен * — правильный ответ. для multiple_choice в
                # одной пробе таких меток может быть несколько —
                # накапливаем их в list; для одиночного выбора остаётся
                # строка (runner умеет принимать оба формата —
                # см. ветки isinstance(correct_answer, list) в
                # process_answer).
                if val.startswith("*"):
                    val = val[1:].strip()
                    existing = trial.get("correct_answer")
                    if existing is None:
                        trial["correct_answer"] = val
                    elif isinstance(existing, list):
                        existing.append(val)
                    else:
                        trial["correct_answer"] = [existing, val]
                options.append(val)
            trial["response_options"] = options

        # дополнительные поля
        aux_cols = mapping.get("auxiliary", [])
        for col in aux_cols:
            if col in row:
                trial["auxiliary"][col] = row[col] or ""

        trials.append(trial)

    return trials
