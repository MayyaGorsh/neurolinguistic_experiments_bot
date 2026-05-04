"""
шаблоны с визуальными стимулами:
- Picture selection
- Covered box
- Picture naming
- Video task
"""

from templates.registry import register, get_response_options


# ── Picture selection ──
# CSV: stimulus, pair_id, img_1_filename, img_2_filename, correct_img (опц.)

def build_picture_selection(trials, config, phase_index=0):
    import random

    options = get_response_options(config, ["1", "2"])
    for t in trials:
        aux = t.get("auxiliary", {})
        img1 = aux.get("img_1_filename", "")
        img2 = aux.get("img_2_filename", "")
        correct = aux.get("correct_img", "")

        # рандомизация порядка картинок в паре
        images = [img1, img2]
        if config.get("randomize", False):
            random.shuffle(images)

        t["stimulus_metadata"] = {
            "pair_id": aux.get("pair_id", ""),
            "images": images,
            "img_1": images[0],
            "img_2": images[1],
        }
        t["response_options"] = options

        if correct:
            if correct.strip() == images[0]:
                t["correct_answer"] = options[0]
            elif correct.strip() == images[1]:
                t["correct_answer"] = options[1]

    return {
        "phase_index": phase_index,
        "title": "Picture Selection",
        "instruction": "Выберите картинку, которая лучше соответствует предложению.",
        "stimulus_type": "image",
        "response_type": "buttons",
        "trials": trials,
        "randomize_order": config.get("randomize", False),
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
    "export_columns": ["pair_id", "img_1", "img_2"],
    "phases_info": ["Picture Selection"],
    "default_response_options": {"main": ["1", "2"]},
})


# ── Covered box ──
# CSV: stimulus, pair_id, img_1_filename, img_2_filename, img_3_filename, correct_img (опц.)

def build_covered_box(trials, config, phase_index=0):
    options = get_response_options(config, ["1", "2", "3"])
    for t in trials:
        aux = t.get("auxiliary", {})
        img1 = aux.get("img_1_filename", "")
        img2 = aux.get("img_2_filename", "")
        img3 = aux.get("img_3_filename", "")
        correct = aux.get("correct_img", "")

        t["stimulus_metadata"] = {
            "pair_id": aux.get("pair_id", ""),
            "img_1": img1,
            "img_2": img2,
            "img_3": img3,
        }
        t["response_options"] = options

        if correct:
            correct = correct.strip()
            if correct == img1:
                t["correct_answer"] = options[0]
            elif correct == img2:
                t["correct_answer"] = options[1]
            elif correct == img3:
                t["correct_answer"] = options[2]

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
    "export_columns": ["pair_id", "img_1", "img_2", "img_3"],
    "phases_info": ["Covered Box"],
    "default_response_options": {"main": ["1", "2", "3"]},
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
})
