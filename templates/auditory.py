"""
шаблоны с аудиальными стимулами:
- Forced choice identification
- Sentence repetition
"""

from templates.registry import register


# ── Forced choice identification ──
# CSV: audio_filename, opt1..opt6 (возможные классы), repeats (опц.)
# аудиофайлы загружаются отдельно
# ответ кнопками, фиксируется RT

def build_forced_choice(trials, config, phase_index=0):
    # разворачиваем повторы
    expanded = []
    idx = 0
    for t in trials:
        aux = t.get("auxiliary", {})
        repeats = int(aux.get("repeats", 1))
        for _ in range(repeats):
            copy = dict(t)
            copy["trial_index"] = idx
            copy["stimulus_metadata"] = dict(t.get("stimulus_metadata", {}))
            copy["stimulus_metadata"]["repeats"] = repeats
            copy["stimulus_metadata"]["audio_filename"] = t.get("stimulus_content", "")
            expanded.append(copy)
            idx += 1

    return {
        "phase_index": phase_index,
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
    }


register("forced_choice", {
    "required_columns": ["audio_filename"],
    "csv_mapping": {
        "stimulus_content": "audio_filename",
        "response_options": ["opt1", "opt2", "opt3", "opt4", "opt5", "opt6"],
        "auxiliary": ["repeats"],
    },
    "build_phase": build_forced_choice,
    "export_columns": ["audio_filename", "repeats"],
    "phases_info": ["Forced Choice Identification"],
})


# ── Sentence repetition ──
# CSV: audio_filename
# аудио-стимул + голосовой ответ

def build_sentence_repetition(trials, config, phase_index=0):
    for t in trials:
        t["stimulus_metadata"] = {
            "audio_filename": t.get("stimulus_content", ""),
        }
    return {
        "phase_index": phase_index,
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
    }


register("sentence_repetition", {
    "required_columns": ["audio_filename"],
    "csv_mapping": {
        "stimulus_content": "audio_filename",
    },
    "build_phase": build_sentence_repetition,
    "export_columns": ["audio_filename"],
    "phases_info": ["Sentence Repetition"],
})
