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
    "example_caption": (
        "<b>Пример CSV для Sensicality Judgment</b>\n\n"
        "Каждая строка — одно предложение для оценки.\n\n"
        "Колонки:\n"
        "• <code>stimulus</code> — предложение, осмысленность которого "
        "респондент будет оценивать.\n"
        "• <code>opt1</code>, <code>opt2</code> — лейблы кнопок ответа "
        "(по умолчанию «Осмысленно» / «Неосмысленно»; можно поменять).\n\n"
        "В этом шаблоне правильного ответа нет — респондент даёт "
        "субъективную оценку, поэтому <code>*</code> ставить не нужно."
    ),
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
        "по одному предложению на пробу.\n"
        "• <code>acceptability_judgment_joint.csv</code> — совместная "
        "подача (колонки <code>stimulus</code> и <code>stimulus2</code>), "
        "одна сравнительная оценка на пару.\n\n"
        "Стимул в самом левом столбце (<code>stimulus</code>) выводится "
        "участнику первым.\n\n"
        "<b>Варианты ответа</b> (<code>opt1</code>…<code>opt10</code>, "
        "от 2 до 10 кнопок). Что в них писать — зависит от режима подачи:\n"
        "• <b>Одиночная</b> или <b>совместная с двумя оценками</b> "
        "(каждое предложение оценивается отдельно): подойдёт yes-no "
        "(<code>Приемлемо</code> / <code>Неприемлемо</code>) или Likert "
        "(<code>Совсем неприемлемо</code> … <code>Полностью приемлемо</code>).\n"
        "• <b>Совместная с одной оценкой</b> (одна общая оценка на пару): "
        "в опции пишите варианты сравнения, например "
        "<code>1 лучше</code> / <code>2 лучше</code>.\n\n"
        "Если ни одной опции не задано — используется числовая Likert-шкала "
        "из настроек эксперимента.\n\n"
        "Чтобы пометить правильный ответ — поставьте <code>*</code> "
        "перед опцией: <code>*1 лучше</code>."
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
# 2 фазы: верификация утверждений + контроль знания (кнопочные ответы).
# фаза 1 CSV: stimulus, opt1..opt6 (правильный помечен * — опционально)
# фаза 2 CSV: question, opt1..opt6 (правильный помечен *), число опций
# у разных вопросов может отличаться (лишние колонки оставить пустыми)

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
            "response_type": "buttons",
            "trials": trials,
            "randomize_order": config.get("randomize", False),
            "time_limit": config.get("time_limit"),
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
            "response_options": ["opt1", "opt2", "opt3", "opt4", "opt5", "opt6"],
            "required_columns": ["question", "opt1", "opt2"],
        },
    },
    "export_columns": [],
    "phases_info": ["Statement Verification", "Контроль знания"],
    "example_caption_phase1": (
        "<b>Statement Verification — основная фаза</b>\n\n"
        "Участник читает утверждение и решает, верно оно или нет.\n\n"
        "Колонки CSV:\n"
        "• <code>stimulus</code> — само утверждение.\n"
        "• <code>opt1</code>…<code>opt6</code> — варианты ответа на "
        "кнопках (обычно «Верно» / «Неверно», но можно задать любой "
        "набор от 2 до 6).\n\n"
        "Правильный вариант помечается <code>*</code> (опционально, если "
        "у утверждения есть объективно верный ответ): "
        "<code>*Верно</code>."
    ),
    "example_caption_phase2": (
        "<b>Statement Verification — контроль знания</b>\n\n"
        "После верификации участнику задают контрольные вопросы с "
        "кнопочными вариантами ответа — это позволяет отделить эффект "
        "иллюзии (когда участник назвал ложное утверждение верным) от "
        "обычного незнания.\n\n"
        "Колонки CSV:\n"
        "• <code>question</code> — текст вопроса.\n"
        "• <code>opt1</code>…<code>opt6</code> — варианты ответа. "
        "У разных вопросов количество вариантов может отличаться "
        "(лишние колонки можно оставить пустыми).\n\n"
        "Правильный вариант помечается <code>*</code>: "
        "<code>*Ной</code>."
    ),
})


# ── Self-Paced Reading ──
# CSV: sentence_id, segment

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
        "auxiliary": ["sentence_id"],
    },
    "build_phase": build_spr,
    "export_columns": ["sentence_id", "segment"],
    "phases_info": ["Self-Paced Reading"],
    "example_caption": (
        "<b>Пример CSV для Self-Paced Reading</b>\n\n"
        "Каждая строка — один сегмент (слово или фрагмент) одного "
        "предложения.\n\n"
        "Колонки:\n"
        "• <code>sentence_id</code> — идентификатор предложения; все "
        "сегменты одного предложения должны иметь один и тот же id.\n"
        "• <code>segment</code> — текст сегмента в порядке предъявления.\n\n"
        "Респондент видит сегменты по очереди: каждое нажатие «Далее» "
        "добавляет следующий, время до нажатия фиксируется как время "
        "чтения сегмента."
    ),
})


# ── Maze task ──
# CSV: target, distractor.
# Для первого слова предложения, которое участник не выбирает,
# в distractor ставится $$$ — на этом шаге слово просто показывается
# с кнопкой «Далее», а не предлагается на выбор.

def build_maze(trials, config, phase_index=0):
    import random
    maze_trials = []
    idx = 0
    # накопленный текст текущего предложения. служит стимулом для
    # очередной пробы выбора: участник видит «то, что собрано», а под
    # ним — две кнопки с правильным и неправильным следующим словом.
    acc = ""
    # индекс текущего предложения: каждая строка с distractor=$$$ его
    # увеличивает. рантайм по этому индексу прыгает к следующему
    # предложению, если участник выбрал неправильное слово.
    sentence_idx = -1

    for t in trials:
        target = t.get("stimulus_content", "").strip()
        aux = t.get("auxiliary", {})
        distractor = aux.get("distractor", "").strip()

        if not target:
            continue
        # legacy-формат: $$$ стоял в target. сейчас он в distractor,
        # старые ряды просто пропускаем (с инкрементом счётчика
        # предложений, чтобы пересборка из старого CSV не ломалась).
        if target == "$$$":
            sentence_idx += 1
            acc = ""
            continue

        if distractor == "$$$":
            # начало нового предложения — первое слово ставится в acc,
            # пробу не создаём: оно станет стимулом для следующей строки.
            sentence_idx += 1
            acc = target
            continue

        # обычная maze-проба: стимул — то, что собрано в acc;
        # выбор — между target (правильное) и distractor (неправильное).
        options = [target, distractor]
        correct = target
        if random.random() > 0.5:
            options = [distractor, target]

        maze_trials.append({
            "trial_index": idx,
            "stimulus_content": acc,
            "stimulus_type": "text",
            "stimulus_metadata": {
                "target": target,
                "distractor": distractor,
                "sentence_idx": max(sentence_idx, 0),
            },
            "response_options": options,
            "correct_answer": correct,
            "auxiliary": {},
            "list_id": t.get("list_id"),
        })
        idx += 1
        acc = (acc + " " + target).strip()

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
    "example_caption": (
        "<b>Пример CSV для Maze Task</b>\n\n"
        "Каждая строка — одно слово предложения и его дистрактор "
        "(невозможное продолжение).\n\n"
        "Колонки:\n"
        "• <code>target</code> — правильное слово.\n"
        "• <code>distractor</code> — неправильное слово на второй "
        "кнопке. Бот сам перемешает их позиции попробно.\n\n"
        "<b>Начало предложения.</b> Слово, с которого начинается "
        "предложение, участник не выбирает — оно выводится как стимул, "
        "а под ним появляются варианты выбора <i>следующего</i> "
        "слова. Чтобы пометить такую строку, поставьте <code>$$$</code> "
        "в столбце <code>distractor</code>.\n\n"
        "Несколько предложений идут в файле подряд: каждое начинается "
        "со своей строки с <code>$$$</code>. Между предложениями "
        "перехода с «Далее» нет — после клика по последнему слову бот "
        "сразу показывает первое слово следующего предложения. Если "
        "участник выбрал неправильную опцию — оставшаяся часть "
        "предложения пропускается, бот переходит к следующему."
    ),
})


# ── Text change detection ──
# CSV: text_original, text_repeated, changed_word_original, changed_word_new
# Поток на пробу — двухстадийный (см. runner.process_answer):
# 1) Показывается text_original с единственной кнопкой «Далее».
#    Фиксируем reading_rt_ms — сколько участник читал оригинал.
# 2) По клику «Далее» сообщение редактируется в text_repeated с
#    кнопками «было / не было». Фиксируем reaction_time_ms — время
#    собственно решения.

def build_text_change(trials, config, phase_index=0):
    new_trials = []
    for t in trials:
        aux = dict(t.get("auxiliary") or {})
        new_t = dict(t)
        new_t["auxiliary"] = aux
        # opt1 в CSV — по конвенции «изменение было», opt2 — «изменения не
        # было». is_change_label запоминает строку первого варианта, чтобы
        # рантайм мог понять, выбрал ли участник «было»-ветку, и спросить
        # у него оригинальное и новое слово.
        opts = list(t.get("response_options") or [])
        new_t["stimulus_metadata"] = {
            "text_repeated": aux.get("text_repeated", t.get("stimulus_content", "")),
            "changed_word_original": aux.get("changed_word_original", ""),
            "changed_word_new": aux.get("changed_word_new", ""),
            "is_change_label": opts[0] if opts else "",
        }
        new_trials.append(new_t)

    return {
        "phase_index": phase_index,
        "title": "Text Change Detection",
        "instruction": (
            "Вам будет показан текст. Прочитайте его и нажмите «Далее» — "
            "после этого текст сменится на повторное предъявление, и нужно "
            "будет ответить, было ли в нём изменение."
        ),
        "stimulus_type": "text",
        "response_type": "buttons",
        "trials": new_trials,
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
    "export_columns": [
        "changed_word_original", "changed_word_new", "reading_rt_ms",
        "user_change_original", "user_change_new",
    ],
    "phases_info": ["Text Change Detection"],
    "example_caption": (
        "<b>Пример CSV для Text Change Detection</b>\n\n"
        "Каждая строка — одна проба. Бот покажет <code>text_original</code> "
        "с кнопкой «Далее»; когда участник её нажмёт, сообщение "
        "сменится на <code>text_repeated</code> с кнопками ответа. "
        "Если участник выберет «было изменение» — бот задаст ещё два "
        "вопроса: какое слово было в оригинале и на какое заменили.\n\n"
        "Колонки:\n"
        "• <code>text_original</code> — исходный текст.\n"
        "• <code>text_repeated</code> — повторное предъявление. Если "
        "хотите, чтобы изменения <i>не</i> было — повторите тот же текст.\n"
        "• <code>changed_word_original</code> — слово в оригинале, "
        "которое заменили (для проб без изменения оставьте пустым).\n"
        "• <code>changed_word_new</code> — слово в повторном "
        "предъявлении, на которое его заменили (тоже пустое для "
        "одинаковых текстов).\n"
        "• <code>opt1</code> и <code>opt2</code> — лейблы кнопок ответа.\n\n"
        "<b>⚠️ Важно про порядок opt1/opt2.</b> Бот по позиции "
        "распознаёт, заметил ли участник изменение, чтобы потом задать "
        "уточняющие вопросы. Поэтому строго:\n"
        "• в <code>opt1</code> — вариант «<b>изменение было</b>»,\n"
        "• в <code>opt2</code> — вариант «<b>изменения не было</b>».\n"
        "Сами тексты можно поменять (например, «Заметил» / «Не "
        "заметил»), главное — не путать колонки местами. Иначе бот "
        "будет спрашивать слова при выборе «не было».\n\n"
        "Поставьте <code>*</code> перед правильным вариантом: "
        "<code>*Изменение было</code> для проб с заменой, "
        "<code>*Изменения не было</code> для одинаковых текстов."
    ),
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
    "example_caption_phase1": (
        "<b>Probe Recognition — фаза запоминания</b>\n\n"
        "На этой фазе респондент по одной фразе на сообщение запоминает "
        "материал. Кнопок ответа нет, переход — по «Далее».\n\n"
        "Колонки CSV:\n"
        "• <code>stimulus</code> — фраза для запоминания (одна на "
        "строку)."
    ),
    "example_caption_phase2": (
        "<b>Probe Recognition — фаза тестирования</b>\n\n"
        "Респондент видит тестовую фразу с одним словом, написанным "
        "заглавными буквами, и отвечает кнопками: встречалось ли это "
        "выделенное слово на фазе запоминания.\n\n"
        "Колонки CSV:\n"
        "• <code>stimulus</code> — тестовая фраза. Целевое слово в ней "
        "должно быть написано заглавными буквами.\n"
        "• <code>opt1</code>…<code>opt6</code> — лейблы кнопок (обычно "
        "две: «Да» / «Нет»).\n\n"
        "Правильный вариант помечается <code>*</code>: <code>*Да</code> "
        "для проб с повтором выделенного слова, <code>*Нет</code> — для "
        "филлеров."
    ),
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
    "example_caption": (
        "<b>Пример CSV для Interpretation Generation</b>\n\n"
        "Каждая строка — одно предложение для парафраза.\n\n"
        "Колонки:\n"
        "• <code>stimulus</code> — предложение, смысл которого "
        "респондент сформулирует своими словами.\n\n"
        "Бот покажет стимул с кнопкой «Далее»; после нажатия сообщение "
        "со стимулом удаляется, и респондент вводит парафраз текстом."
    ),
})
