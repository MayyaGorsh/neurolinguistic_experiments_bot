"""
шаблоны с визуальными стимулами:
- Picture selection
- Covered box
- Picture naming
- Video task
"""

from templates.registry import register


# ── Picture selection ──
# CSV: pair_id, img_1_filename, img_2_filename, correct_img (опц.), repeats (опц.)
# кнопки с номерами картинок, рандомизация порядка в паре, RT

def build_picture_selection(trials, config):
    import random

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
        t["response_options"] = ["Картинка 1", "Картинка 2"]

        if correct:
            if correct.strip() == images[0]:
                t["correct_answer"] = "Картинка 1"
            elif correct.strip() == images[1]:
                t["correct_answer"] = "Картинка 2"

    return [{
        "phase_index": 0,
        "title": "Picture Selection",
        "instruction": "Выберите картинку, которая лучше соответствует предложению.",
        "stimulus_type": "image",
        "response_type": "buttons",
        "trials": trials,
        "randomize_order": config.get("randomize", False),
        "time_limit": config.get("time_limit"),
        "settings": {"is_picture_selection": True},
    }]


register("picture_selection", {
    "required_columns": ["pair_id", "img_1_filename", "img_2_filename"],
    "csv_mapping": {
        "stimulus_content": "pair_id",
        "auxiliary": ["pair_id", "img_1_filename", "img_2_filename",
                      "correct_img", "repeats"],
    },
    "build_phases": build_picture_selection,
    "export_columns": ["pair_id", "img_1", "img_2"],
    "phases_info": ["Picture Selection"],
})


# ── Covered box ──
# CSV: pair_id, img_1_filename, img_2_filename, correct_img (опц.)
# три кнопки: картинка 1, картинка 2, закрытая коробка

def build_covered_box(trials, config):
    for t in trials:
        aux = t.get("auxiliary", {})
        img1 = aux.get("img_1_filename", "")
        img2 = aux.get("img_2_filename", "")
        correct = aux.get("correct_img", "")

        t["stimulus_metadata"] = {
            "pair_id": aux.get("pair_id", ""),
            "img_1": img1,
            "img_2": img2,
        }
        t["response_options"] = ["Картинка 1", "Картинка 2", "Закрытая коробка"]

        if correct:
            correct = correct.strip()
            if correct == img1:
                t["correct_answer"] = "Картинка 1"
            elif correct == img2:
                t["correct_answer"] = "Картинка 2"
            elif correct.lower() in ("box", "covered", "закрытая"):
                t["correct_answer"] = "Закрытая коробка"

    return [{
        "phase_index": 0,
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
    }]


register("covered_box", {
    "required_columns": ["pair_id", "img_1_filename", "img_2_filename"],
    "csv_mapping": {
        "stimulus_content": "pair_id",
        "auxiliary": ["pair_id", "img_1_filename", "img_2_filename", "correct_img"],
    },
    "build_phases": build_covered_box,
    "export_columns": ["pair_id", "img_1", "img_2"],
    "phases_info": ["Covered Box"],
})


# ── Picture naming ──
# CSV: img_filename, correct (опционально — список допустимых ответов)
# ответ текстом или голосовым сообщением, без RT

def build_picture_naming(trials, config):
    return [{
        "phase_index": 0,
        "title": "Picture Naming",
        "instruction": "Назовите изображенный объект одним словом или короткой фразой.",
        "stimulus_type": "image",
        "response_type": "open_text",
        "trials": trials,
        "randomize_order": config.get("randomize", False),
        "time_limit": config.get("time_limit"),
        "settings": {},
    }]


register("picture_naming", {
    "required_columns": ["img_filename"],
    "csv_mapping": {
        "stimulus_content": "img_filename",
        "correct_answer": "correct",
    },
    "build_phases": build_picture_naming,
    "export_columns": ["img_filename"],
    "phases_info": ["Picture Naming"],
})


# ── Video task ──
# CSV: video_filename, opt1, opt2, ... (кнопки, количество произвольное)
# для ликерта: кнопки = градации шкалы

def build_video_task(trials, config):
    response_type = "buttons"
    settings = {}

    # если все пробы имеют одинаковое число кнопок >= 5 — считаем ликертом
    option_counts = [len(t.get("response_options", [])) for t in trials]
    if option_counts and min(option_counts) >= 5 and len(set(option_counts)) == 1:
        response_type = "likert"
        scale = option_counts[0]
        labels = {}
        for t in trials:
            for i, opt in enumerate(t["response_options"]):
                labels[str(i + 1)] = opt
            break
        settings["likert_scale"] = scale
        settings["likert_labels"] = labels

    return [{
        "phase_index": 0,
        "title": "Video Task",
        "instruction": "Просмотрите видео и выберите ответ.",
        "stimulus_type": "video",
        "response_type": response_type,
        "trials": trials,
        "randomize_order": config.get("randomize", False),
        "time_limit": config.get("time_limit"),
        "settings": settings,
    }]


register("video_task", {
    "required_columns": ["video_filename"],
    "csv_mapping": {
        "stimulus_content": "video_filename",
        "response_options": ["opt1", "opt2", "opt3", "opt4", "opt5", "opt6", "opt7"],
    },
    "build_phases": build_video_task,
    "export_columns": ["video_filename"],
    "phases_info": ["Video Task"],
})
