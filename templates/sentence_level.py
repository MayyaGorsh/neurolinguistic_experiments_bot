"""
шаблоны на уровне предложения:
- Sensicality judgment
- Acceptability judgment
- Truth Value Judgment Task (TVJT)
- Self-Paced Reading
- Maze task
- Text change detection
- Probe recognition
- Interpretation generation
"""

from templates.registry import register


# ── Sensicality judgment ──

def build_sensicality(trials, config):
    for t in trials:
        t["response_options"] = ["Осмысленно", "Неосмысленно"]
        correct = t.get("correct_answer", "")
        if correct:
            correct_lower = correct.strip().lower()
            if correct_lower in ("yes", "да", "1", "осмысленно"):
                t["correct_answer"] = "Осмысленно"
            else:
                t["correct_answer"] = "Неосмысленно"
    return [{
        "phase_index": 0,
        "title": "Sensicality Judgment",
        "instruction": "Определите, является ли предложение осмысленным.",
        "stimulus_type": "text",
        "response_type": "buttons",
        "trials": trials,
        "randomize_order": config.get("randomize", False),
        "time_limit": config.get("time_limit", 4),
        "settings": {},
    }]


register("sensicality_judgment", {
    "required_columns": ["stimulus"],
    "csv_mapping": {
        "stimulus_content": "stimulus",
        "correct_answer": "correct",
    },
    "build_phases": build_sensicality,
    "export_columns": [],
    "phases_info": ["Sensicality Judgment"],
})


# ── Acceptability judgment ──

def build_acceptability(trials, config):
    settings = {}
    resp_format = config.get("response_format", "likert")
    presentation = config.get("presentation_mode", "single")

    if resp_format == "yes_no":
        for t in trials:
            t["response_options"] = ["Приемлемо", "Неприемлемо"]
        response_type = "buttons"
    else:
        scale = config.get("likert_scale", 5)
        label_mode = config.get("label_mode", "endpoints")
        labels = {}
        if label_mode == "full":
            labels = {str(i): f"{i}" for i in range(1, scale + 1)}
            labels["1"] = "Совсем неприемлемо"
            labels[str(scale)] = "Полностью приемлемо"
        elif label_mode == "endpoints":
            labels["1"] = "Совсем неприемлемо"
            labels[str(scale)] = "Полностью приемлемо"
        settings["likert_scale"] = scale
        settings["likert_labels"] = labels
        response_type = "likert"

    if presentation in ("joint_one_rating", "joint_two_ratings"):
        for t in trials:
            aux = t.get("auxiliary", {})
            stim2 = aux.get("stimulus2", "")
            if stim2:
                t["stimulus_content"] = (
                    f"1) {t['stimulus_content']}\n\n2) {stim2}"
                )

    return [{
        "phase_index": 0,
        "title": "Acceptability Judgment",
        "instruction": "Оцените приемлемость предложения.",
        "stimulus_type": "text",
        "response_type": response_type,
        "trials": trials,
        "randomize_order": config.get("randomize", False),
        "time_limit": config.get("time_limit"),
        "settings": settings,
    }]


register("acceptability_judgment", {
    "required_columns": ["stimulus"],
    "csv_mapping": {
        "stimulus_content": "stimulus",
        "auxiliary": ["stimulus2"],
    },
    "build_phases": build_acceptability,
    "export_columns": ["stimulus2"],
    "phases_info": ["Acceptability Judgment"],
})


# ── Truth Value Judgment Task ──

def build_tvjt(trials, config):
    main_trials = []
    control_trials = []

    for t in trials:
        aux = t.get("auxiliary", {})
        context = aux.get("context", "")
        if context:
            t["stimulus_content"] = f"{context}\n\n{t['stimulus_content']}"

        if not t.get("response_options"):
            t["response_options"] = ["Да", "Нет", "Не знаю"]
        main_trials.append(t)

        control_q = aux.get("control_question", "")
        if control_q:
            control_opts = []
            for k in sorted(aux.keys()):
                if k.startswith("control_opt") and aux[k].strip():
                    control_opts.append(aux[k].strip())
            control_trial = {
                "trial_index": t["trial_index"],
                "stimulus_content": control_q,
                "stimulus_type": "text",
                "stimulus_metadata": {},
                "response_options": control_opts if control_opts else ["Да", "Нет"],
                "correct_answer": aux.get("control_correct"),
                "auxiliary": {"is_control": True},
                "list_id": t.get("list_id"),
            }
            control_trials.append(control_trial)

    phases = [{
        "phase_index": 0,
        "title": "Truth Value Judgment",
        "instruction": "Оцените истинность утверждения.",
        "stimulus_type": "text",
        "response_type": "buttons",
        "trials": main_trials,
        "randomize_order": config.get("randomize", False),
        "time_limit": config.get("time_limit"),
        "settings": {},
    }]

    if control_trials:
        phases.append({
            "phase_index": 1,
            "title": "Контроль знания",
            "instruction": "Ответьте на контрольные вопросы.",
            "stimulus_type": "text",
            "response_type": "buttons",
            "trials": control_trials,
            "randomize_order": False,
            "time_limit": None,
            "settings": {},
        })

    return phases


register("tvjt", {
    "required_columns": ["stimulus"],
    "csv_mapping": {
        "stimulus_content": "stimulus",
        "response_options": ["opt1", "opt2", "opt3"],
        "correct_answer": "correct",
        "auxiliary": ["context", "control_question",
                      "control_opt1", "control_opt2", "control_opt3",
                      "control_correct"],
    },
    "build_phases": build_tvjt,
    "export_columns": ["context", "is_control"],
    "phases_info": ["Truth Value Judgment", "Контроль знания"],
})


# ── Self-Paced Reading ──

def build_spr(trials, config):
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

    return [{
        "phase_index": 0,
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
    }]


register("self_paced_reading", {
    "required_columns": ["sentence_id", "segment"],
    "csv_mapping": {
        "stimulus_content": "segment",
        "auxiliary": ["sentence_id", "region"],
    },
    "build_phases": build_spr,
    "export_columns": ["sentence_id", "segment", "region"],
    "phases_info": ["Self-Paced Reading"],
})


# ── Maze task ──

def build_maze(trials, config):
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

    return [{
        "phase_index": 0,
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
    }]


register("maze", {
    "required_columns": ["target", "distractor"],
    "csv_mapping": {
        "stimulus_content": "target",
        "auxiliary": ["distractor"],
    },
    "build_phases": build_maze,
    "export_columns": ["target", "distractor"],
    "phases_info": ["Maze Task"],
})


# ── Text change detection ──

def build_text_change(trials, config):
    for t in trials:
        aux = t.get("auxiliary", {})
        t["stimulus_metadata"] = {
            "text_repeated": aux.get("text_repeated", t["stimulus_content"]),
            "changed_word_original": aux.get("changed_word_original", ""),
            "changed_word_new": aux.get("changed_word_new", ""),
        }
        t["response_options"] = ["Изменение было", "Изменения не было"]
        if aux.get("changed_word_original", "").strip():
            t["correct_answer"] = "Изменение было"
        else:
            t["correct_answer"] = "Изменения не было"

    return [{
        "phase_index": 0,
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
    }]


register("text_change_detection", {
    "required_columns": ["text_original", "text_repeated"],
    "csv_mapping": {
        "stimulus_content": "text_original",
        "auxiliary": ["text_repeated", "changed_word_original", "changed_word_new"],
    },
    "build_phases": build_text_change,
    "export_columns": ["changed_word_original", "changed_word_new"],
    "phases_info": ["Text Change Detection"],
})


# ── Probe recognition ──

def build_probe_recognition(trials, config):
    study = []
    test = []
    for t in trials:
        pt = t.get("auxiliary", {}).get("phase_type", "test")
        if pt == "study":
            study.append(t)
        else:
            test.append(t)
            if not t.get("response_options"):
                t["response_options"] = ["Да", "Нет"]

    phases = [
        {
            "phase_index": 0,
            "title": "Фаза запоминания",
            "instruction": "Прочитайте и запомните каждую фразу.",
            "stimulus_type": "text",
            "response_type": "buttons",
            "trials": study,
            "randomize_order": config.get("randomize", False),
            "time_limit": None,
            "settings": {},
        },
        {
            "phase_index": 1,
            "title": "Фаза тестирования",
            "instruction": (
                "Определите, встречалось ли выделенное слово на предыдущем этапе."
            ),
            "stimulus_type": "text",
            "response_type": "buttons",
            "trials": test,
            "randomize_order": config.get("randomize", False),
            "time_limit": config.get("time_limit"),
            "settings": {},
        },
    ]
    return phases


register("probe_recognition", {
    "required_columns": ["stimulus"],
    "csv_mapping": {
        "stimulus_content": "stimulus",
        "correct_answer": "correct",
        "auxiliary": ["phase_type"],
    },
    "build_phases": build_probe_recognition,
    "export_columns": ["phase_type"],
    "phases_info": ["Фаза запоминания", "Фаза тестирования"],
})


# ── Interpretation generation ──

def build_interpretation(trials, config):
    return [{
        "phase_index": 0,
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
    }]


register("interpretation_generation", {
    "required_columns": ["stimulus"],
    "csv_mapping": {
        "stimulus_content": "stimulus",
    },
    "build_phases": build_interpretation,
    "export_columns": [],
    "phases_info": ["Interpretation Generation"],
})
