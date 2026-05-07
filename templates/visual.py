"""
шаблоны с визуальными стимулами:
- Picture selection
- Covered box
- Picture naming
- Video task
"""

from templates.registry import register


# ── Picture selection ──
# CSV: stimulus, pair_id, img_1_filename, img_2_filename, correct_img (опц.)

def build_picture_selection(trials, config, phase_index=0):
    # позиции картинок в паре фиксируем как в CSV (img_1, img_2);
    # перетасовка по сессии — отдельный тоггл randomize_image_positions,
    # обрабатывается в runner.prepare_trials_for_session, чтобы у
    # каждого участника был свой случайный порядок.
    options = ["1", "2"]
    for t in trials:
        aux = t.get("auxiliary", {})
        img1 = aux.get("img_1_filename", "")
        img2 = aux.get("img_2_filename", "")
        correct = (aux.get("correct_img") or "").strip()

        images = [img1, img2]
        t["stimulus_metadata"] = {
            "pair_id": aux.get("pair_id", ""),
            "images": images,
            "img_1": images[0],
            "img_2": images[1],
        }
        t["response_options"] = options

        # Сохраняем имя файла как correct_answer — оно инвариантно
        # к перетасовке позиций. raw_response в on_answer_button тоже
        # резолвится в filename, is_correct = строковое сравнение.
        if correct and correct in (images[0], images[1]):
            t["correct_answer"] = correct

    return {
        "phase_index": phase_index,
        "title": "Picture Selection",
        "instruction": "Выберите картинку, которая лучше соответствует предложению.",
        "stimulus_type": "image",
        "response_type": "buttons",
        "trials": trials,
        "randomize_order": config.get("randomize", False),
        "randomize_image_positions": config.get(
            "randomize_image_positions", False,
        ),
        "time_limit": config.get("time_limit"),
        "settings": {"is_picture_selection": True},
    }


register("picture_selection", {
    "required_columns": ["stimulus", "pair_id", "img_1_filename", "img_2_filename"],
    "csv_mapping": {
        "stimulus_content": "stimulus",
        "auxiliary": ["pair_id", "img_1_filename", "img_2_filename",
                      "correct_img"],
    },
    "build_phase": build_picture_selection,
    # img_1/img_2 — то, что реально показано на экране (после
    # возможной перетасовки позиций); correct_img — имя файла
    # правильной картинки из CSV. raw_response в выгрузке тоже имя
    # файла, поэтому сравнение «raw_response == correct_img» —
    # ровно то, что нужно для анализа.
    "export_columns": ["pair_id", "img_1", "img_2", "correct_img"],
    "phases_info": ["Picture Selection"],
    # лейблы кнопок — только цифры по позиции, кастомизация лейблов не
    # имеет смысла для шаблонов с картинками (нужен порядок «1=левая»
    # / «2=правая», иначе сломается соответствие кнопок и положений).
    "has_image_positions": True,
    "example_caption": (
        "<b>Пример CSV для Picture Selection</b>\n\n"
        "Каждая строка — одна проба: предложение и пара изображений, "
        "из которых нужно выбрать одно.\n\n"
        "Колонки:\n"
        "• <code>stimulus</code> — предложение, по которому респондент "
        "выбирает картинку.\n"
        "• <code>pair_id</code> — идентификатор пары (попадает в "
        "выгрузку, удобно для анализа).\n"
        "• <code>img_1_filename</code>, <code>img_2_filename</code> — "
        "имена файлов с изображениями. Сами файлы вы загрузите отдельно "
        "следующим шагом.\n"
        "• <code>correct_img</code> — опционально, имя файла с "
        "«правильной» картинкой. Корректность считается по имени файла, "
        "так что рандомизация позиций кнопок ничего не ломает."
    ),
})


# ── Covered box ──
# CSV: stimulus, pair_id, img_1_filename, img_2_filename, img_3_filename, correct_img (опц.)

def build_covered_box(trials, config, phase_index=0):
    # позиции картинок берём как в CSV. Перетасовка по сессии —
    # отдельный тоггл randomize_image_positions; обрабатывается в
    # runner.prepare_trials_for_session.
    options = ["1", "2", "3"]
    for t in trials:
        aux = t.get("auxiliary", {})
        img1 = aux.get("img_1_filename", "")
        img2 = aux.get("img_2_filename", "")
        img3 = aux.get("img_3_filename", "")
        correct = (aux.get("correct_img") or "").strip()

        images = [img1, img2, img3]
        t["stimulus_metadata"] = {
            "pair_id": aux.get("pair_id", ""),
            "images": images,
            "img_1": img1,
            "img_2": img2,
            "img_3": img3,
        }
        t["response_options"] = options

        # correct_answer = имя файла, см. комментарий в build_picture_selection.
        if correct and correct in images:
            t["correct_answer"] = correct

    return {
        "phase_index": phase_index,
        "title": "Covered Box",
        "instruction": (
            "Выберите картинку, которая соответствует описанию, "
            "или закрытую коробку, если ни одна не подходит."
        ),
        "stimulus_type": "image",
        "response_type": "buttons",
        "trials": trials,
        "randomize_order": config.get("randomize", False),
        "randomize_image_positions": config.get(
            "randomize_image_positions", False,
        ),
        "time_limit": config.get("time_limit"),
        "settings": {"is_covered_box": True},
    }


register("covered_box", {
    "required_columns": ["stimulus", "pair_id", "img_1_filename", "img_2_filename", "img_3_filename"],
    "csv_mapping": {
        "stimulus_content": "stimulus",
        "auxiliary": ["pair_id", "img_1_filename", "img_2_filename",
                      "img_3_filename", "correct_img"],
    },
    "build_phase": build_covered_box,
    "export_columns": ["pair_id", "img_1", "img_2", "img_3", "correct_img"],
    "phases_info": ["Covered Box"],
    "has_image_positions": True,
    "example_caption": (
        "<b>Пример CSV для Covered Box</b>\n\n"
        "Каждая строка — одна проба: предложение и три картинки. "
        "Третья по конвенции — «закрытая коробка», которую респондент "
        "выбирает, если ни одна видимая картинка не подходит точно.\n\n"
        "Колонки:\n"
        "• <code>stimulus</code> — предложение-инструкция (например, "
        "«Дай мне коробку с тремя кругами»).\n"
        "• <code>pair_id</code> — идентификатор тройки (для выгрузки).\n"
        "• <code>img_1_filename</code>, <code>img_2_filename</code> — "
        "имена файлов двух «открытых» картинок.\n"
        "• <code>img_3_filename</code> — имя файла «закрытой коробки». "
        "Можно использовать одну и ту же картинку во всех пробах.\n"
        "• <code>correct_img</code> — опционально, имя файла с "
        "правильным ответом для критических проб (включая «закрытую "
        "коробку», если ни одна видимая картинка не подходит).\n\n"
        "Сами файлы изображений вы загрузите отдельно следующим шагом."
    ),
})


# ── Picture naming ──
# CSV: img_filename, correct1..correct4 (опциональные допустимые ответы)

def build_picture_naming(trials, config, phase_index=0):
    for t in trials:
        correct_list = []
        aux = t.get("auxiliary", {})
        for key in sorted(aux.keys()):
            if key.startswith("correct") and aux[key].strip():
                correct_list.append(aux[key].strip())
        if correct_list:
            t["correct_answer"] = correct_list
        t["stimulus_metadata"] = {
            "img_filename": t.get("stimulus_content", ""),
        }
    return {
        "phase_index": phase_index,
        "title": "Picture Naming",
        "instruction": "Назовите изображенный объект одним словом или короткой фразой.",
        "stimulus_type": "image",
        "response_type": "open_text",
        "trials": trials,
        "randomize_order": config.get("randomize", False),
        "time_limit": config.get("time_limit"),
        "settings": {},
    }


register("picture_naming", {
    "required_columns": ["img_filename"],
    "csv_mapping": {
        "stimulus_content": "img_filename",
        "auxiliary": ["correct1", "correct2", "correct3", "correct4"],
    },
    "build_phase": build_picture_naming,
    "export_columns": ["img_filename"],
    "phases_info": ["Picture Naming"],
    "example_caption": (
        "<b>Пример CSV для Picture Naming</b>\n\n"
        "Каждая строка — одно изображение для называния.\n\n"
        "Колонки:\n"
        "• <code>img_filename</code> — имя файла с изображением. Сами "
        "файлы вы загрузите отдельно следующим шагом.\n"
        "• <code>correct1</code>…<code>correct4</code> — опционально, "
        "допустимые варианты названия (например, синонимы). Если задано "
        "хотя бы одно — в выгрузке будет помечено, попал ли ответ "
        "респондента в этот список.\n\n"
        "Респондент вводит название текстом или присылает голосовое "
        "сообщение. Время реакции не фиксируется."
    ),
})


# ── Video task ──
# CSV: video_filename, opt1..opt7 (кнопки)
# все стимулы должны иметь одинаковое количество заполненных опций

def build_video_task(trials, config, phase_index=0):
    settings = {}

    option_counts = [len(t.get("response_options", [])) for t in trials]

    if not option_counts:
        return {
            "phase_index": phase_index,
            "title": "Video Task",
            "instruction": "Просмотрите видео и выберите ответ.",
            "stimulus_type": "video",
            "response_type": "buttons",
            "trials": trials,
            "randomize_order": config.get("randomize", False),
            "time_limit": config.get("time_limit"),
            "settings": {},
        }

    scale = option_counts[0]
    labels = {}
    for t in trials:
        for i, opt in enumerate(t["response_options"]):
            labels[str(i + 1)] = opt
        break
    settings["likert_scale"] = scale
    settings["likert_labels"] = labels

    return {
        "phase_index": phase_index,
        "title": "Video Task",
        "instruction": "Просмотрите видео и выберите ответ.",
        "stimulus_type": "video",
        "response_type": "likert",
        "trials": trials,
        "randomize_order": config.get("randomize", False),
        "time_limit": config.get("time_limit"),
        "settings": settings,
    }


register("video_task", {
    "required_columns": ["video_filename"],
    "csv_mapping": {
        "stimulus_content": "video_filename",
        "response_options": ["opt1", "opt2", "opt3", "opt4", "opt5", "opt6", "opt7"],
    },
    "build_phase": build_video_task,
    "export_columns": ["video_filename"],
    "phases_info": ["Video Task"],
    "example_caption": (
        "<b>Пример CSV для Video Task</b>\n\n"
        "Каждая строка — одно видео и набор вариантов ответа на "
        "кнопках.\n\n"
        "Колонки:\n"
        "• <code>video_filename</code> — имя видеофайла. Сами файлы вы "
        "загрузите отдельно следующим шагом.\n"
        "• <code>opt1</code>…<code>opt7</code> — лейблы кнопок ответа.\n\n"
        "<b>Важно:</b> у всех проб в файле должно быть одинаковое "
        "количество заполненных вариантов — например, везде по 7 для "
        "шкалы Ликерта или везде по 2 для двухальтернативного выбора. "
        "Лишние колонки оставьте пустыми."
    ),
})
