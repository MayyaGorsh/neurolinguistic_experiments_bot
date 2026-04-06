"""
парсинг CSV-файлов со стимулами.
общий фреймворк для загрузки, валидации и преобразования в trials.
"""

import csv
import io
import logging
from typing import Optional

logger = logging.getLogger("bot")


def parse_csv_text(text: str) -> list[dict]:
    """прочитать CSV-текст и вернуть список словарей (строк)"""
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    rows = []
    for row in reader:
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

        # стимул
        stim_col = mapping.get("stimulus_content", "stimulus")
        if stim_col in row:
            trial["stimulus_content"] = row[stim_col]

        # правильный ответ
        correct_col = mapping.get("correct_answer")
        if correct_col and correct_col in row:
            val = row[correct_col].strip()
            if val:
                trial["correct_answer"] = val

        # варианты ответа
        opt_cols = mapping.get("response_options", [])
        if isinstance(opt_cols, list):
            options = []
            for col in opt_cols:
                if col in row and row[col].strip():
                    val = row[col].strip()
                    # помечен * — правильный ответ
                    if val.startswith("*"):
                        val = val[1:].strip()
                        trial["correct_answer"] = val
                    options.append(val)
            trial["response_options"] = options

        # дополнительные поля
        aux_cols = mapping.get("auxiliary", [])
        for col in aux_cols:
            if col in row:
                trial["auxiliary"][col] = row[col]

        trials.append(trial)

    return trials
