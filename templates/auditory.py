"""
шаблоны с аудиальными стимулами:
- Forced choice identification
- Sentence repetition
"""

from templates.registry import register


# ── Forced choice identification ──
# CSV: stimulus_id, class1, class2, ... (варианты категорий), repeats (опционально)
# аудиофайлы загружаются отдельно и привязываются к stimulus_id
# ответ кнопками, фиксируется RT

def build_forced_choice(trials, config):
    for t in trials:
        aux = t.get("auxiliary", {})
        # если варианты ответа не заданы через response_options,
        # берем из class1/class2
        if not t.get("response_options"):
            classes = []
            for key in sorted(aux.keys()):
                if key.startswith("class") and aux[key].strip():
                    classes.append(aux[key].strip())
            t["response_options"] = classes if classes else ["A", "B"]

        # число повторов
        repeats = int(aux.get("repeats", 1))
        if repeats > 1:
            t["stimulus_metadata"]["repeats"] = repeats

    # разворачиваем повторы
    expanded = []
    idx = 0
    for t in trials:
        reps = t.get("stimulus_metadata", {}).get("repeats", 1)
        for _ in range(reps):
            copy = dict(t)
            copy["trial_index"] = idx
            expanded.append(copy)
            idx += 1

    return [{
        "phase_index": 0,
        "title": "Forced Choice Identification",
        "instruction": (
            "Прослушайте аудио и выберите категорию, "
            "к которой оно относится."
        ),
        "stimulus_type": "audio",
        "response_type": "buttons",
        "trials": expanded,
        "randomize_order": config.get("randomize", True),
        "time_limit": config.get("time_limit"),
        "settings": {},
    }]


register("forced_choice", {
    "required_columns": ["stimulus_id"],
    "csv_mapping": {
        "stimulus_content": "stimulus_id",
        "auxiliary": ["class1", "class2", "class3", "class4", "repeats"],
    },
    "build_phases": build_forced_choice,
    "export_columns": ["stimulus_id", "repeats"],
    "phases_info": ["Forced Choice Identification"],
})


# ── Sentence repetition ──
# CSV: stimulus_id (имя аудиофайла)
# аудио-стимул + голосовой ответ

def build_sentence_repetition(trials, config):
    return [{
        "phase_index": 0,
        "title": "Sentence Repetition",
        "instruction": (
            "Прослушайте предложение и повторите его вслух, "
            "отправив голосовое сообщение."
        ),
        "stimulus_type": "audio",
        "response_type": "voice",
        "trials": trials,
        "randomize_order": config.get("randomize", False),
        "time_limit": config.get("time_limit"),
        "settings": {},
    }]


register("sentence_repetition", {
    "required_columns": ["stimulus_id"],
    "csv_mapping": {
        "stimulus_content": "stimulus_id",
    },
    "build_phases": build_sentence_repetition,
    "export_columns": ["stimulus_id"],
    "phases_info": ["Sentence Repetition"],
})
