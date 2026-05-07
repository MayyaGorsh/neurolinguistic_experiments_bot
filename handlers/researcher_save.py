"""сохранение эксперимента: создание нового или обновление черновика
из state.data — пересборка фаз через build_phase шаблона, применение
override-инструкций, attach медиа-file_id, при необходимости
переупаковка аудио с новой длительностью тишины.

Дальше управление передаётся в `show_experiment_detail`, которая
показывает карточку эксперимента."""

import secrets

from aiogram import types, F
from aiogram.fsm.context import FSMContext

from db import repositories as repo
from engine import audio as audio_util

from handlers.researcher_common import (
    router,
    CreateExperiment,
    logger,
    tmpl_registry,
)


async def _reapply_audio_silence(
    bot, owner_chat_id: int, experiment_id: str, silence_seconds: int,
    phases: list,
):
    """перепаковать все аудио-медиа эксперимента: взять оригинал
    (`original_file_id`), добавить тишину `silence_seconds`, получить
    новый file_id, обновить media-запись и подменить file_id во всех
    trial.stimulus_metadata, ссылающихся на тот же filename.

    вызывается, когда исследователь меняет настройку «тишина в аудио»
    на сохранении эксперимента. mutates `phases` in-place."""
    media_list = await repo.get_media_by_experiment(experiment_id)
    audio_media = [m for m in media_list if m.get("media_type") == "audio"]
    if not audio_media:
        return

    if silence_seconds > 0:
        # одной пачкой через media_group: исследователь увидит один
        # короткий «прилёт» альбома, а не N отдельных вспышек.
        items = [
            (m.get("original_file_id") or m.get("file_id"),
             m.get("filename", "audio.mp3"))
            for m in audio_media
        ]
        # batch возвращает {filename: (file_id, duration_ms)}
        new_data = await audio_util.reupload_padded_audios_batch(
            bot, owner_chat_id, items, silence_seconds,
        )
    else:
        # silence=0 — возвращаемся к оригиналам, ничего не отправляем.
        # длительность считаем по уже сохранённой минус прежняя тишина:
        # если её нет, считаем 0 (timer не сработает, но это лучше
        # пустого file_id).
        new_data = {}
        for m in audio_media:
            filename = m.get("filename", "")
            orig_id = m.get("original_file_id") or m.get("file_id")
            prev_total = int(m.get("duration_ms", 0) or 0)
            prev_silence = int(m.get("silence_ms", 0) or 0)
            base_dur = max(prev_total - prev_silence, 0)
            new_data[filename] = (orig_id, base_dur)

    for m in audio_media:
        filename = m.get("filename", "")
        entry = new_data.get(filename)
        if not entry:
            continue
        new_id, dur_ms = entry
        await repo.update_media(str(m["_id"]), {
            "file_id": new_id,
            "silence_ms": silence_seconds * 1000,
            "duration_ms": dur_ms,
        })

    # обновляем file_id в фазах
    for phase in phases:
        if phase.get("stimulus_type") != "audio":
            continue
        for trial in phase.get("trials", []):
            stim = trial.get("stimulus_content", "")
            if stim in new_data:
                trial.setdefault("stimulus_metadata", {})
                trial["stimulus_metadata"]["file_id"] = new_data[stim][0]


@router.callback_query(CreateExperiment.configuring, F.data == "cfg_save")
async def on_save_draft(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()

    template_type = data.get("template_type", "free_form")
    csv_data = data.get("csv_data", {})
    phases_info = data.get("phases_info", ["Основная фаза"])

    # группируем trials по номеру фазы
    trials_by_phase = {}
    for key, trials in csv_data.items():
        parts = key.split("_")
        phase_num = int(parts[0]) if len(parts) == 2 else 1
        if phase_num not in trials_by_phase:
            trials_by_phase[phase_num] = []
        trials_by_phase[phase_num].extend(trials)

    # формируем фазы из шаблона
    tmpl_info = tmpl_registry.get_template(template_type)
    if tmpl_info:
        build_fn = tmpl_info.get("build_phase")
        if build_fn:
            phases = []
            custom_instr = data.get("custom_instructions") or {}
            for phase_num in sorted(trials_by_phase.keys()):
                phase_trials = trials_by_phase[phase_num]
                phase_index = phase_num - 1
                phase = build_fn(phase_trials, data, phase_index)
                # применяем пользовательскую инструкцию, если задана
                override = custom_instr.get(phase_index) or custom_instr.get(str(phase_index))
                if isinstance(override, str) and override.strip():
                    phase["instruction"] = override
                phases.append(phase)
        else:
            # fallback — старый формат (не должен использоваться)
            all_trials = []
            for phase_num in sorted(trials_by_phase.keys()):
                all_trials.extend(trials_by_phase[phase_num])
            phases = [tmpl_info["build_phases"](all_trials, data)]
    else:
        # free_form — фазы уже собраны в free_form_phases
        ff_phases = data.get("free_form_phases", [])
        if ff_phases:
            phases = ff_phases
        else:
            # fallback — одна фаза со всеми пробами
            all_trials = []
            for phase_num in sorted(trials_by_phase.keys()):
                all_trials.extend(trials_by_phase[phase_num])
            phases = [{
                "phase_index": 0,
                "title": "Основная фаза",
                "instruction": "",
                "stimulus_type": "text",
                "response_type": "buttons",
                "trials": all_trials,
                "randomize_order": data.get("randomize", False),
                "time_limit": data.get("time_limit"),
                "settings": {},
            }]

    # режим редактирования — делаем update, не создаём новый
    editing_id = data.get("editing_id")

    # lists_count теперь хранится явной настройкой; use_lists — производная
    lists_count = max(int(data.get("lists_count", 1) or 1), 1)
    use_lists = lists_count >= 2

    # поля, которые меняются и при create, и при update
    mutable_fields = {
        "title": data.get("title", "Без названия"),
        "description": data.get("description", ""),
        "template_type": template_type,
        "phases": phases,
        "randomize_trials": data.get("randomize", False),
        "randomize_button_positions": data.get("randomize_button_positions", False),
        "delete_previous_trials": data.get("delete_previous_trials", True),
        "use_lists": use_lists,
        "lists_count": lists_count,
        "time_limit": data.get("time_limit"),
        "idle_timeout_seconds": int(data.get("idle_timeout_seconds", 300) or 0),
        "audio_silence_seconds": int(data.get("audio_silence_seconds", 0) or 0),
        "collect_demographics": data.get("demographics_mode", "off") != "off",
        "demographics_type": "custom" if data.get("demographics_mode") == "custom" else "standard",
        "demographics_custom": data.get("demographics_custom", []),
        "allow_repeat": data.get("allow_repeat", False),
        "custom_buttons": data.get("custom_buttons") or {},
        "custom_likert": data.get("custom_likert") or {},
        "custom_instructions": data.get("custom_instructions") or {},
        "presentation_mode": data.get("presentation_mode", "single"),
        # сохраняем «сырой» csv_data рядом с phases. при правке черновика
        # некоторые шаблоны (maze) сильно меняют структуру в build_phase
        # — их build не идемпотентен, и пересборка из phase.trials дала бы
        # рекурсивно-склеенные стимулы. Cырые ряды позволяют сохранить
        # исходные данные и пересобрать из них корректно.
        "csv_data_raw": data.get("csv_data") or {},
    }

    # show_experiment_detail здесь импортируется лениво, чтобы не плодить
    # цикл researcher_save ↔ researcher_experiment на этапе загрузки модуля.
    from handlers.researcher_experiment import show_experiment_detail

    if editing_id:
        # build_phase выше пересобрал пробы из CSV и стёр все file_id
        # на trial.stimulus_metadata. Подтягиваем их обратно из
        # media-коллекции, иначе следующая активация упадёт с «не
        # загружен медиафайл», хотя файлы загружены.
        media_records = await repo.get_media_by_experiment(editing_id)
        if media_records:
            from handlers.media_upload import attach_media_ids_to_phases
            attach_media_ids_to_phases(media_records, mutable_fields["phases"])

        # если изменилась настройка тишины и шаблон аудио — перепакуем
        # все аудио-медиа из оригиналов с новой длительностью тишины,
        # обновим file_id в media-коллекции и в phases (stimulus_metadata).
        old_exp = await repo.get_experiment(editing_id) or {}
        old_silence = int(old_exp.get("audio_silence_seconds", 0) or 0)
        new_silence = int(mutable_fields.get("audio_silence_seconds", 0) or 0)
        if (
            template_type in ("forced_choice", "sentence_repetition")
            and old_silence != new_silence
        ):
            await _reapply_audio_silence(
                callback.bot, callback.from_user.id, editing_id,
                new_silence, mutable_fields["phases"],
            )
        await repo.update_experiment(editing_id, mutable_fields)
        exp_id = editing_id
        logger.info("эксперимент %s обновлён пользователем %s", exp_id, callback.from_user.id)
        await state.clear()
        await show_experiment_detail(
            callback, exp_id, banner="✅ Изменения сохранены.",
            state=state,
        )
        return

    # create — новый эксперимент
    deep_link_id = "exp_" + secrets.token_urlsafe(8)
    experiment_data = {
        **mutable_fields,
        "owner_id": callback.from_user.id,
        "status": "draft",
        "export_settings": {},
        "deep_link_id": deep_link_id,
    }

    exp_id = await repo.create_experiment(experiment_data)
    await state.clear()

    logger.info("эксперимент %s создан пользователем %s", exp_id, callback.from_user.id)

    await show_experiment_detail(
        callback, exp_id,
        banner=f"✅ «{data.get('title')}» сохранён как черновик.",
        state=state,
    )
