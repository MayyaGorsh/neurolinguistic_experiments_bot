"""
шаблоны на уровне предложения:
- Sensicality judgment
- Acceptability judgment
- Truth Value Judgment Task (TVJT)
- Statement verification
- Self-Paced Reading
- Maze task
- Text change detection
- Probe recognition
- Interpretation generation
"""

from templates.registry import register, get_likert_config


# ── Sensicality judgment ──
# CSV: stimulus, opt1..opt6 (без *, правильного ответа в этом шаблоне нет)

def build_sensicality(trials, config, phase_index=0):
    return {
        "phase_index": phase_index,
        "title": "Sensicality Judgment",
        "instruction": "Определите, является ли предложение осмысленным.",
        "stimulus_type": "text",
        "response_type": "buttons",
        "trials": trials,
        "randomize_order": config.get("randomize", False),
        "time_limit": config.get("time_limit", 4),
        "settings": {},
    }


register("sensicality_judgment", {
    "required_columns": ["stimulus", "opt1", "opt2"],
    "csv_mapping": {
        "stimulus_content": "stimulus",
        "response_options": ["opt1", "opt2", "opt3", "opt4", "opt5", "opt6"],
    },
    "build_phase": build_sensicality,
    "export_columns": [],
    "phases_info": ["Sensicality Judgment"],
})


# ── Acceptability judgment ──
# CSV-driven: число опций определяет формат ответа.
# - 0 опций → числовая Likert-шкала из настроек эксперимента (scale + labels);
# - 2 опции → yes-no;
# - N опций (до 10) → N-кнопочная шкала с подписями из CSV.
# stimulus2 (опц.) — второй стимул для совместной подачи (joint_*).
# режим подачи (single / joint_one_rating / joint_two_ratings) выбирается
# исследователем; joint_* активны только если в CSV есть stimulus2.

def build_acceptability(trials, config, phase_index=0):
    presentation = config.get("presentation_mode", "single")

    new_trials = []
    for t in trials:
        aux = dict(t.get("auxiliary") or {})
        # сохраняем оригинал stimulus один раз — на edit-reload csv_data
        # содержит уже-собранные пробы со stimulus_content вида
        # "1) X\n\n2) Y", и повторный build_phase без этого якоря
        # обернул бы их вторым слоем «1) (1) X\n\n2) Y) 2) Y».
        if "_stimulus_raw" not in aux:
            aux["_stimulus_raw"] = t.get("stimulus_content", "")
        stim = aux["_stimulus_raw"]
        stim2 = aux.get("stimulus2", "")
        new_t = dict(t)
        new_t["auxiliary"] = aux
        if stim2 and presentation in ("joint_one_rating", "joint_two_ratings"):
            # левый стимул в CSV (stimulus) идёт первым в сообщении.
            new_t["stimulus_content"] = f"1) {stim}\n\n2) {stim2}"
        else:
            new_t["stimulus_content"] = stim
        new_trials.append(new_t)

    # формат ответа определяется CSV: если есть опции — кнопки, иначе Likert.
    has_options = any(t.get("response_options") for t in new_trials)
    settings: dict = {}
    if has_options:
        response_type = "buttons"
    else:
        response_type = "likert"
        likert = get_likert_config(config, {
            "scale": config.get("likert_scale", 5),
            "labels": {
                "1": "Совсем неприемлемо",
                str(config.get("likert_scale", 5)): "Полностью приемлемо",
            },
        })
        settings["likert_scale"] = likert["scale"]
        settings["likert_labels"] = likert["labels"]

    # двойная оценка: одно сообщение, два последовательных клика.
    # рантайм по этому флагу включает edit-message-флоу
    # (см. process_answer / present_trial).
    if presentation == "joint_two_ratings":
        settings["joint_two_ratings"] = True

    return {
        "phase_index": phase_index,
        "title": "Acceptability Judgment",
        "instruction": "Оцените приемлемость предложения.",
        "stimulus_type": "text",
        "response_type": response_type,
        "trials": new_trials,
        "randomize_order": config.get("randomize", False),
        "time_limit": config.get("time_limit"),
        "settings": settings,
    }


register("acceptability_judgment", {
    "required_columns": ["stimulus"],
    "csv_mapping": {
        "stimulus_content": "stimulus",
        "response_options": [
            "opt1", "opt2", "opt3", "opt4", "opt5",
            "opt6", "opt7", "opt8", "opt9", "opt10",
        ],
        "auxiliary": ["stimulus2"],
    },
    "build_phase": build_acceptability,
    "export_columns": ["stimulus2", "rating_target"],
    "phases_info": ["Acceptability Judgment"],
    "default_likert": {"main": {
        "scale": 5,
        "labels": {"1": "Совсем неприемлемо", "5": "Полностью приемлемо"},
    }},
    "extra_examples": ["acceptability_judgment_joint.csv"],
    "example_caption": (
        "<b>Примеры CSV для Acceptability Judgment</b>\n\n"
        "• <code>acceptability_judgment.csv</code> — одиночная подача, "
        "Likert-шкала с подписями.\n"
        "• <code>acceptability_judgment_joint.csv</code> — совместная "
        "подача (две колонки <code>stimulus</code> и "
        "<code>stimulus2</code>), yes-no с правильным ответом.\n\n"
        "Стимул в самом левом столбце (<code>stimulus</code>) выводится "
        "участнику первым.\n\n"
        "Можно указать любое количество вариантов ответа от 2 до 10 "
        "(колонки <code>opt1</code>…<code>opt10</code>). Если ни одной "
        "опции не задано — используется числовая Likert-шкала из "
        "настроек эксперимента (без правильного ответа).\n\n"
        "Чтобы пометить правильный ответ — поставьте <code>*</code> "
        "перед опцией: <code>*Приемлемо</code>."
    ),
})


# ── Truth Value Judgment Task ──
# CSV: stimulus (критическое утверждение), opt1..opt6 (правильный помечен *),
# context (опц.) — короткая история, выводится перед утверждением.
# одна фаза: респондент сначала жмёт кнопку (правильно/неправильно), затем
# пишет обоснование текстом — это поведение типа ответа buttons_then_text.

def build_tvjt(trials, config, phase_index=0):
    # копируем trial-объекты, чтобы повторные вызовы build_phase
    # (при предпросмотре, повторном пересборе и т.п.) не приписывали
    # контекст к stimulus_content раз за разом.
    new_trials = []
    for t in trials:
        aux = dict(t.get("auxiliary") or {})
        # на edit-reload csv_data содержит уже-обработанные пробы со
        # stimulus_content = "context\n\nstim". без якоря _stimulus_raw
        # повторная сборка приклеит контекст ещё раз.
        if "_stimulus_raw" not in aux:
            aux["_stimulus_raw"] = t.get("stimulus_content", "")
        context = aux.get("context", "")
        stim = aux["_stimulus_raw"]
        new_t = dict(t)
        new_t["auxiliary"] = aux
        if context:
            new_t["stimulus_content"] = f"{context}\n\n{stim}"
        else:
            new_t["stimulus_content"] = stim
        new_trials.append(new_t)

    return {
        "phase_index": phase_index,
        "title": "Truth Value Judgment",
        "instruction": (
            "Прочитайте короткую историю и оценку. Решите, правильно или "
            "неправильно описана история, и обоснуйте свой выбор."
        ),
        "stimulus_type": "text",
        "response_type": "buttons_then_text",
        "trials": new_trials,
        "randomize_order": config.get("randomize", False),
        "time_limit": config.get("time_limit"),
        "settings": {},
    }


register("tvjt", {
    "required_columns": ["stimulus", "opt1", "opt2"],
    "csv_mapping": {
        "stimulus_content": "stimulus",
        "response_options": ["opt1", "opt2", "opt3", "opt4", "opt5", "opt6"],
        "auxiliary": ["context", "ask_justification"],
    },
    "build_phase": build_tvjt,
    "export_columns": ["context", "justification"],
    "phases_info": ["Truth Value Judgment"],
    "example_caption": (
        "<b>Пример CSV для Truth Value Judgment Task</b>\n\n"
        "Колонки:\n"
        "• <code>context</code> — короткая история (опц.), выводится "
        "перед утверждением.\n"
        "• <code>stimulus</code> — критическое утверждение, которое "
        "респондент оценивает как «правильно» / «неправильно».\n"
        "• <code>opt1</code>…<code>opt6</code> — варианты ответа на "
        "кнопках. Поставьте <code>*</code> перед правильным "
        "(<code>*Правильно</code>).\n"
        "• <code>ask_justification</code> — запросить ли у респондента "
        "текстовое обоснование после клика. Значения: <code>да</code> "
        "или <code>нет</code>. По умолчанию (если столбца нет или поле "
        "пустое) обоснование <b>не</b> запрашивается. Это позволяет "
        "включить обоснование только для критических проб, а для "
        "филлеров — оставить простой клик."
    ),
})


# ── Statement verification ──
# 2 фазы: верификация утверждений + опциональная проверка знания (открытый ответ)
# фаза 1 CSV: stimulus, opt1..opt6 (правильный помечен * — опционально)
# фаза 2 CSV: question (вопрос для проверки знания, открытый ответ)

def build_statement_verification(trials, config, phase_index=0):
    """единая build_phase: phase_index=0 — верификация, phase_index=1 — контроль"""
    if phase_index == 0:
        return {
            "phase_index": 0,
            "title": "Statement Verification",
            "instruction": "Определите, верно ли утверждение.",
            "stimulus_type": "text",
            "response_type": "buttons",
            "trials": trials,
            "randomize_order": config.get("randomize", False),
            "time_limit": config.get("time_limit"),
            "settings": {},
        }
    else:
        return {
            "phase_index": phase_index,
            "title": "Контроль знания",
            "instruction": "Ответьте на вопросы.",
            "stimulus_type": "text",
            "response_type": "open_text",
            "trials": trials,
            "randomize_order": False,
            "time_limit": None,
            "settings": {},
        }


register("statement_verification", {
    "required_columns": ["stimulus", "opt1", "opt2"],
    "csv_mapping": {
        "stimulus_content": "stimulus",
        "response_options": ["opt1", "opt2", "opt3", "opt4", "opt5", "opt6"],
    },
    "build_phase": build_statement_verification,
    "phase_csv_mappings": {
        2: {
            "stimulus_content": "question",
            "required_columns": ["question"],
        },
    },
    "export_columns": [],
    "phases_info": ["Statement Verification", "Контроль знания"],
})


# ── Self-Paced Reading ──
# CSV: sentence_id, segment, region (опц.)

def build_spr(trials, config, phase_index=0):
    sentences = {}
    for t in trials:
        aux = t.get("auxiliary", {})
        sent_id = aux.get("sentence_id", "0")
        if sent_id not in sentences:
            sentences[sent_id] = []
        sentences[sent_id].append(t)

    spr_trials = []
    idx = 0
    for sent_id in sorted(sentences.keys()):
        segments = sentences[sent_id]
        accumulated = ""
        for seg in segments:
            word = seg.get("stimulus_content", "").strip()
            accumulated = (accumulated + " " + word).strip()
            spr_trials.append({
                "trial_index": idx,
                "stimulus_content": accumulated,
                "stimulus_type": "text",
                "stimulus_metadata": {
                    "sentence_id": sent_id,
                    "segment": word,
                    "region": seg.get("auxiliary", {}).get("region", ""),
                },
                "response_options": [],
                "correct_answer": None,
                "auxiliary": {"sentence_id": sent_id},
                "list_id": seg.get("list_id"),
            })
            idx += 1

    return {
        "phase_index": phase_index,
        "title": "Self-Paced Reading",
        "instruction": (
            "Вам будет предъявляться предложение по частям. "
            "Нажимайте «Далее», чтобы увидеть следующий фрагмент."
        ),
        "stimulus_type": "text",
        "response_type": "buttons",
        "trials": spr_trials,
        "randomize_order": False,
        "time_limit": config.get("time_limit"),
        "settings": {"is_spr": True},
    }


register("self_paced_reading", {
    "required_columns": ["sentence_id", "segment"],
    "csv_mapping": {
        "stimulus_content": "segment",
        "auxiliary": ["sentence_id", "region"],
    },
    "build_phase": build_spr,
    "export_columns": ["sentence_id", "segment", "region"],
    "phases_info": ["Self-Paced Reading"],
})


# ── Maze task ──
# CSV: target, distractor ($$$ — разделитель предложений)

def build_maze(trials, config, phase_index=0):
    import random
    maze_trials = []
    idx = 0

    for t in trials:
        target = t.get("stimulus_content", "").strip()
        aux = t.get("auxiliary", {})
        distractor = aux.get("distractor", "").strip()

        if target == "$$$":
            continue

        options = [target, distractor]
        correct = target
        if random.random() > 0.5:
            options = [distractor, target]

        maze_trials.append({
            "trial_index": idx,
            "stimulus_content": "",
            "stimulus_type": "text",
            "stimulus_metadata": {
                "target": target,
                "distractor": distractor,
            },
            "response_options": options,
            "correct_answer": correct,
            "auxiliary": {},
            "list_id": t.get("list_id"),
        })
        idx += 1

    return {
        "phase_index": phase_index,
        "title": "Maze Task",
        "instruction": (
            "На каждом шаге выбирайте слово, которое грамматически "
            "продолжает предложение."
        ),
        "stimulus_type": "text",
        "response_type": "buttons",
        "trials": maze_trials,
        "randomize_order": False,
        "time_limit": config.get("time_limit"),
        "settings": {"is_maze": True},
    }


register("maze", {
    "required_columns": ["target", "distractor"],
    "csv_mapping": {
        "stimulus_content": "target",
        "auxiliary": ["distractor"],
    },
    "build_phase": build_maze,
    "export_columns": ["target", "distractor"],
    "phases_info": ["Maze Task"],
})


# ── Text change detection ──
# CSV: text_original, text_repeated, changed_word_original, changed_word_new

def build_text_change(trials, config, phase_index=0):
    for t in trials:
        aux = t.get("auxiliary", {})
        t["stimulus_metadata"] = {
            "text_repeated": aux.get("text_repeated", t["stimulus_content"]),
            "changed_word_original": aux.get("changed_word_original", ""),
            "changed_word_new": aux.get("changed_word_new", ""),
        }

    return {
        "phase_index": phase_index,
        "title": "Text Change Detection",
        "instruction": (
            "Вам будет показан текст, затем он исчезнет, и появится "
            "повторное предъявление. Определите, было ли изменение."
        ),
        "stimulus_type": "text",
        "response_type": "buttons",
        "trials": trials,
        "randomize_order": config.get("randomize", False),
        "time_limit": config.get("time_limit"),
        "settings": {"is_text_change": True},
    }


register("text_change_detection", {
    "required_columns": ["text_original", "text_repeated", "opt1", "opt2"],
    "csv_mapping": {
        "stimulus_content": "text_original",
        "response_options": ["opt1", "opt2", "opt3", "opt4", "opt5", "opt6"],
        "auxiliary": ["text_repeated", "changed_word_original", "changed_word_new"],
    },
    "build_phase": build_text_change,
    "export_columns": ["changed_word_original", "changed_word_new"],
    "phases_info": ["Text Change Detection"],
})


# ── Probe recognition ──
# 2 фазы, исследователь загружает 2 отдельных CSV
# фаза 1 CSV: stimulus (фразы для запоминания, без правильного ответа)
# фаза 2 CSV: stimulus, opt1..opt6 (правильный помечен *)

def build_probe_recognition(trials, config, phase_index=0):
    """единая build_phase: phase_index=0 — запоминание, phase_index=1 — тестирование"""
    if phase_index == 0:
        for t in trials:
            t["response_options"] = []
            t["correct_answer"] = None
        return {
            "phase_index": 0,
            "title": "Фаза запоминания",
            "instruction": "Прочитайте и запомните каждую фразу.",
            "stimulus_type": "text",
            "response_type": "buttons",
            "trials": trials,
            "randomize_order": config.get("randomize", False),
            "time_limit": None,
            "settings": {},
        }
    else:
        return {
            "phase_index": phase_index,
            "title": "Фаза тестирования",
            "instruction": (
                "Определите, встречалось ли выделенное слово на предыдущем этапе."
            ),
            "stimulus_type": "text",
            "response_type": "buttons",
            "trials": trials,
            "randomize_order": config.get("randomize", False),
            "time_limit": config.get("time_limit"),
            "settings": {},
        }


register("probe_recognition", {
    "required_columns": ["stimulus"],
    "csv_mapping": {
        "stimulus_content": "stimulus",
    },
    "build_phase": build_probe_recognition,
    "phase_csv_mappings": {
        2: {
            "stimulus_content": "stimulus",
            "response_options": ["opt1", "opt2", "opt3", "opt4", "opt5", "opt6"],
            "required_columns": ["stimulus", "opt1", "opt2"],
        },
    },
    "export_columns": [],
    "phases_info": ["Фаза запоминания", "Фаза тестирования"],
})


# ── Interpretation generation ──
# CSV: stimulus

def build_interpretation(trials, config, phase_index=0):
    return {
        "phase_index": phase_index,
        "title": "Interpretation Generation",
        "instruction": (
            "Прочитайте предложение, нажмите «Далее», затем запишите, "
            "как вы понимаете смысл предложения."
        ),
        "stimulus_type": "text",
        "response_type": "open_text",
        "trials": trials,
        "randomize_order": config.get("randomize", False),
        "time_limit": config.get("time_limit"),
        "settings": {"is_interpretation": True},
    }


register("interpretation_generation", {
    "required_columns": ["stimulus"],
    "csv_mapping": {
        "stimulus_content": "stimulus",
    },
    "build_phase": build_interpretation,
    "export_columns": [],
    "phases_info": ["Interpretation Generation"],
})
