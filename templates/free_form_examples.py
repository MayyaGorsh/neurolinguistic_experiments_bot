"""генератор CSV-примеров для free-form фаз.

в отличие от шаблонов, где примеры лежат в templates/examples/ как
готовые файлы, free_form-комбинации (stim_type × resp_type) собираются
на лету. формат един для всех:

- колонка `stimulus` — текст стимула или имя медиа-файла;
- `caption` — подпись (только для медиа-стимула);
- `opt1`/`opt2`/... — варианты ответа (для buttons/multiple_choice/
  buttons_then_text); `*` в начале значения отмечает правильный;
- `1`..`N` — деления Likert-шкалы (вместо opt*); `*` отмечает правильное;
- `follow_up_prompt` — текст приглашения ко второму шагу (только
  для buttons_then_text);
- для open_text/voice опций нет.
"""

import io

import pandas as pd


_STIM_PLACEHOLDER = {
    "text": "стимул_{i}",
    "audio": "audio_{i}.wav",
    "image": "image_{i}.png",
    "video": "video_{i}.mp4",
}


def build_freeform_csv_example(
    stim_type: str, resp_type: str, n: int = 4,
) -> bytes:
    """собрать CSV-пример заполнения под пару (stim_type, resp_type).

    Возвращает байты utf-8 с разделителем `;` — тем же, что детектит
    наш csv_parser._detect_delimiter."""
    stim_tmpl = _STIM_PLACEHOLDER.get(stim_type, "стимул_{i}")
    is_media = stim_type in ("audio", "image", "video")
    rows = []
    for i in range(1, n + 1):
        row: dict = {"stimulus": stim_tmpl.format(i=i)}
        if is_media:
            row["caption"] = f"подпись к стимулу {i}"

        if resp_type in ("buttons", "multiple_choice"):
            # 2 варианта, в чётных строках правильный — opt2, в нечётных — opt1
            row["opt1"] = ("*вариант 1" if i % 2 == 1 else "вариант 1")
            row["opt2"] = ("вариант 2" if i % 2 == 1 else "*вариант 2")
        elif resp_type == "likert":
            # 5-балльная шкала; правильное помечаем для примера в первой строке
            for k in range(1, 6):
                row[str(k)] = f"*{k}" if (i == 1 and k == 3) else str(k)
        elif resp_type == "buttons_then_text":
            row["opt1"] = "*да"
            row["opt2"] = "нет"
            row["follow_up_prompt"] = "Поясните свой ответ"
        # open_text / voice — без вариантов

        rows.append(row)

    df = pd.DataFrame(rows)
    # порядок колонок: stimulus, caption, options, follow_up_prompt
    ordered = ["stimulus"]
    if is_media:
        ordered.append("caption")
    ordered += [c for c in df.columns if c not in ordered and c != "follow_up_prompt"]
    if "follow_up_prompt" in df.columns:
        ordered.append("follow_up_prompt")
    df = df[ordered]

    buf = io.StringIO()
    df.to_csv(buf, index=False, sep=";")
    return buf.getvalue().encode("utf-8")
