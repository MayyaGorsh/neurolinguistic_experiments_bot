"""
шаблоны на уровне слова:
- Lexical decision
- Predictability rating
- Cloze (multiple choice)
- Cloze (open ended)
- Word translation (closed)
- Word translation (open)
"""

from templates.registry import register, get_likert_config


# ── Lexical decision ──
# CSV: stimulus, opt1..opt6 (правильный помечен *)

def build_lexical_decision(trials, config, phase_index=0):
    return {
        "phase_index": phase_index,
        "title": "Lexical Decision",
        "instruction": "Определите, является ли предъявленная последовательность букв словом.",
        "stimulus_type": "text",
        "response_type": "buttons",
        "trials": trials,
        "randomize_order": config.get("randomize", False),
        "time_limit": config.get("time_limit"),
        "settings": {},
    }


register("lexical_decision", {
    "required_columns": ["stimulus", "opt1", "opt2"],
    "csv_mapping": {
        "stimulus_content": "stimulus",
        "response_options": ["opt1", "opt2", "opt3", "opt4", "opt5", "opt6"],
    },
    "build_phase": build_lexical_decision,
    "export_columns": [],
    "phases_info": ["Lexical Decision"],
    "example_caption": (
        "<b>Пример CSV для Lexical Decision</b>\n\n"
        "Каждая строка — одна проба.\n\n"
        "Колонки:\n"
        "• <code>stimulus</code> — слово или несловарная строка "
        "(например, «кошка», «бртан»).\n"
        "• <code>opt1</code>, <code>opt2</code> — лейблы кнопок ответа "
        "(по умолчанию «Слово» / «Не слово»; можно поменять).\n\n"
        "Правильный вариант помечается <code>*</code>: для реальных слов "
        "— <code>*Слово</code>, для несловарных строк — "
        "<code>*Не слово</code>."
    ),
})


# ── Predictability rating ──
# CSV: left_context, target, right_context
# шкала ликерта, RT фиксируется

def build_predictability_rating(trials, config, phase_index=0):
    for t in trials:
        aux = t.get("auxiliary", {})
        left = aux.get("left_context", "")
        target = aux.get("target", "")
        right = aux.get("right_context", "")
        sentence = f"{left} ___ {right}" if right else f"{left} ___"
        t["stimulus_content"] = f"{sentence}\n\nСлово: <b>{target}</b>"

    likert = get_likert_config(config, {
        "scale": 5,
        "labels": {
            "1": "Совсем не ожидаемо",
            "2": "2",
            "3": "Нейтрально",
            "4": "4",
            "5": "Очень ожидаемо",
        },
    })
    return {
        "phase_index": phase_index,
        "title": "Predictability Rating",
        "instruction": "Оцените, насколько ожидаемо данное слово в пропуске.",
        "stimulus_type": "text",
        "response_type": "likert",
        "trials": trials,
        "randomize_order": config.get("randomize", False),
        "time_limit": config.get("time_limit"),
        "settings": {
            "likert_scale": likert["scale"],
            "likert_labels": likert["labels"],
        },
    }


register("predictability_rating", {
    "required_columns": ["left_context", "target"],
    "csv_mapping": {
        "stimulus_content": "left_context",
        "auxiliary": ["left_context", "target", "right_context"],
    },
    "build_phase": build_predictability_rating,
    "export_columns": ["left_context", "target", "right_context"],
    "phases_info": ["Predictability Rating"],
    "default_likert": {"main": {
        "scale": 5,
        "labels": {
            "1": "Совсем не ожидаемо",
            "2": "2",
            "3": "Нейтрально",
            "4": "4",
            "5": "Очень ожидаемо",
        },
    }},
    "example_caption": (
        "<b>Пример CSV для Predictability Rating</b>\n\n"
        "Каждая строка — одно предложение с одним целевым словом в "
        "пропуске.\n\n"
        "Колонки:\n"
        "• <code>left_context</code> — часть предложения до пропуска.\n"
        "• <code>target</code> — слово, ожидаемость которого нужно "
        "оценить.\n"
        "• <code>right_context</code> — часть предложения после пропуска "
        "(можно оставить пустым, если пропуск стоит в конце).\n\n"
        "Бот соберёт стимул вида «<i>left_context</i> ___ "
        "<i>right_context</i>» и отдельно покажет целевое слово, после "
        "чего попросит оценку по шкале Ликерта."
    ),
})


# ── Cloze (multiple choice) ──
# CSV: stimulus, opt1..opt6 (правильный помечен *)

def build_cloze_mc(trials, config, phase_index=0):
    processed = []
    for t in trials:
        content = t.get("stimulus_content", "")
        has_gap = "___" in content
        has_correct = t.get("correct_answer") is not None

        if not has_gap and not has_correct:
            t["response_options"] = []
        processed.append(t)

    return {
        "phase_index": phase_index,
        "title": "Cloze (multiple choice)",
        "instruction": "Выберите слово, которое лучше всего подходит на место пропуска.",
        "stimulus_type": "text",
        "response_type": "buttons",
        "trials": processed,
        "randomize_order": config.get("randomize", False),
        "time_limit": config.get("time_limit"),
        "settings": {},
    }


register("cloze_mc", {
    "required_columns": ["stimulus"],
    "csv_mapping": {
        "stimulus_content": "stimulus",
        "response_options": ["opt1", "opt2", "opt3", "opt4", "opt5", "opt6"],
    },
    "build_phase": build_cloze_mc,
    "export_columns": [],
    "phases_info": ["Cloze (multiple choice)"],
    "example_caption": (
        "<b>Пример CSV для Cloze (multiple choice)</b>\n\n"
        "Каждая строка — одна проба.\n\n"
        "Колонки:\n"
        "• <code>stimulus</code> — предложение с пропуском "
        "<code>___</code> на месте искомого слова.\n"
        "• <code>opt1</code>…<code>opt6</code> — варианты ответа на "
        "кнопках (от 2 до 6).\n\n"
        "Правильный вариант помечается <code>*</code>: "
        "<code>*читает</code>.\n\n"
        "<b>Контекстные предложения.</b> Если в строке нет "
        "<code>___</code> и нет правильного варианта, бот считает её "
        "контекстной и выводит просто с кнопкой «Далее» — это удобно для "
        "подводящих предложений к критическому стимулу."
    ),
})


# ── Cloze (open ended) ──
# CSV: stimulus, correct1..correct10 (все допустимые ответы)

def build_cloze_open(trials, config, phase_index=0):
    for t in trials:
        correct_list = []
        aux = t.get("auxiliary", {})
        for key in sorted(aux.keys()):
            if key.startswith("correct") and aux[key].strip():
                correct_list.append(aux[key].strip())
        if correct_list:
            t["correct_answer"] = correct_list
    return {
        "phase_index": phase_index,
        "title": "Cloze (open ended)",
        "instruction": "Введите слово, которое лучше всего подходит на место пропуска.",
        "stimulus_type": "text",
        "response_type": "open_text",
        "trials": trials,
        "randomize_order": config.get("randomize", False),
        "time_limit": config.get("time_limit"),
        "settings": {},
    }


register("cloze_open", {
    "required_columns": ["stimulus"],
    "csv_mapping": {
        "stimulus_content": "stimulus",
        "auxiliary": ["correct1", "correct2", "correct3", "correct4", "correct5",
                      "correct6", "correct7", "correct8", "correct9", "correct10"],
    },
    "build_phase": build_cloze_open,
    "export_columns": [],
    "phases_info": ["Cloze (open ended)"],
    "example_caption": (
        "<b>Пример CSV для Cloze (open ended)</b>\n\n"
        "Каждая строка — одна проба.\n\n"
        "Колонки:\n"
        "• <code>stimulus</code> — предложение с пропуском "
        "<code>___</code>.\n"
        "• <code>correct1</code>…<code>correct10</code> — допустимые "
        "варианты заполнения пропуска (до 10). Достаточно перечислить "
        "только те, что считаются корректными при оценивании; остальные "
        "колонки оставьте пустыми.\n\n"
        "Респондент вводит ответ текстом; в выгрузке сохраняется сам "
        "ответ и пометка, попадает ли он в список допустимых."
    ),
})


# ── Word translation (closed — кнопки) ──
# CSV: stimulus, opt1..opt6 (правильный помечен *)

def build_word_translation_mc(trials, config, phase_index=0):
    return {
        "phase_index": phase_index,
        "title": "Word Translation (closed)",
        "instruction": "Выберите правильный перевод слова.",
        "stimulus_type": "text",
        "response_type": "buttons",
        "trials": trials,
        "randomize_order": config.get("randomize", False),
        "time_limit": config.get("time_limit"),
        "settings": {},
    }


register("word_translation_mc", {
    "required_columns": ["stimulus"],
    "csv_mapping": {
        "stimulus_content": "stimulus",
        "response_options": ["opt1", "opt2", "opt3", "opt4", "opt5", "opt6"],
    },
    "build_phase": build_word_translation_mc,
    "export_columns": [],
    "phases_info": ["Word Translation (closed)"],
    "example_caption": (
        "<b>Пример CSV для Word Translation (closed)</b>\n\n"
        "Каждая строка — одно слово на исходном языке.\n\n"
        "Колонки:\n"
        "• <code>stimulus</code> — слово на исходном языке (например, "
        "«будинок»).\n"
        "• <code>opt1</code>…<code>opt6</code> — варианты перевода на "
        "кнопках (от 2 до 6).\n\n"
        "Правильный перевод помечается <code>*</code>: <code>*дом</code>."
    ),
})


# ── Word translation (open — текстовый ввод) ──
# CSV: stimulus, correct1..correct6 (допустимые переводы)

def build_word_translation_open(trials, config, phase_index=0):
    for t in trials:
        correct_list = []
        aux = t.get("auxiliary", {})
        for key in sorted(aux.keys()):
            if key.startswith("correct") and aux[key].strip():
                correct_list.append(aux[key].strip())
        if correct_list:
            t["correct_answer"] = correct_list
    return {
        "phase_index": phase_index,
        "title": "Word Translation (open)",
        "instruction": "Введите перевод предъявленного слова.",
        "stimulus_type": "text",
        "response_type": "open_text",
        "trials": trials,
        "randomize_order": config.get("randomize", False),
        "time_limit": config.get("time_limit"),
        "settings": {},
    }


register("word_translation_open", {
    "required_columns": ["stimulus"],
    "csv_mapping": {
        "stimulus_content": "stimulus",
        "auxiliary": ["correct1", "correct2", "correct3", "correct4",
                      "correct5", "correct6"],
    },
    "build_phase": build_word_translation_open,
    "export_columns": [],
    "phases_info": ["Word Translation (open)"],
    "example_caption": (
        "<b>Пример CSV для Word Translation (open)</b>\n\n"
        "Каждая строка — одно слово на исходном языке.\n\n"
        "Колонки:\n"
        "• <code>stimulus</code> — слово на исходном языке.\n"
        "• <code>correct1</code>…<code>correct6</code> — допустимые "
        "переводы (до 6). Достаточно перечислить только корректные "
        "варианты, остальные колонки оставьте пустыми.\n\n"
        "Респондент вводит перевод текстом; в выгрузке сохраняется "
        "ответ и пометка, попадает ли он в список допустимых."
    ),
})
