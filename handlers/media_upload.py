"""
загрузка медиафайлов (аудио, изображения, видео)
и привязка к пробам эксперимента.
"""

import logging

from aiogram import Router, Bot, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db import repositories as repo
from engine import audio as audio_util

router = Router()
logger = logging.getLogger("bot")


class MediaUpload(StatesGroup):
    waiting_files = State()
    setting_silence = State()


async def start_media_upload(
    message, experiment_id: str, state: FSMContext
):
    """начать процесс загрузки медиафайлов для эксперимента"""
    exp = await repo.get_experiment(experiment_id)
    if not exp:
        await message.answer("Эксперимент не найден.")
        return

    # собираем список ожидаемых файлов из проб
    expected = set()
    for phase in exp.get("phases", []):
        for trial in phase.get("trials", []):
            stim = trial.get("stimulus_content", "")
            if stim and phase.get("stimulus_type") in ("audio", "image", "video"):
                expected.add(stim)

    if not expected:
        await message.answer("В этом эксперименте нет проб с медиафайлами.")
        return

    await state.update_data(
        media_experiment_id=experiment_id,
        expected_files=list(expected),
        uploaded_files={},
    )

    text = (
        f"Ожидаемые файлы ({len(expected)}):\n"
        + "\n".join(f"  • {f}" for f in sorted(expected))
        + "\n\nИмя файла должно совпадать с тем, что указано в CSV.\n\n"
        "💡 <b>Замена файла:</b> если файл с таким именем уже был "
        "загружен раньше — просто пришлите новый с тем же именем, "
        "и в эксперименте будет использоваться новая версия."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Готово", callback_data="media_done")],
    ])
    await message.answer(text, reply_markup=kb)
    await state.set_state(MediaUpload.waiting_files)


def _expected_set(data: dict) -> set[str]:
    """набор ожидаемых имён файлов из state."""
    return set(data.get("expected_files") or [])


async def _reject_unexpected(message: types.Message, filename: str) -> None:
    """единая реплика для файла, не указанного в CSV. имя файла — то,
    что бот извлёк из сообщения (Telegram-овское file_name или caption)."""
    await message.answer(
        f"Файл «{filename}» пропущен — его нет в списке ожидаемых.\n"
        "Проверьте имя или название в CSV."
    )


@router.message(MediaUpload.waiting_files, F.photo)
async def on_photo(message: types.Message, state: FSMContext, bot: Bot):
    """получили фото — сохраняем file_id"""
    photo = message.photo[-1]  # лучшее качество
    caption = message.caption or ""
    data = await state.get_data()
    exp_id = data["media_experiment_id"]

    # имя файла — только из caption: у фото в Telegram нет file_name,
    # без явной подписи мы не сможем сопоставить с CSV-стимулом.
    filename = caption.strip()
    if not filename or filename not in _expected_set(data):
        await _reject_unexpected(message, filename or "(без имени)")
        return

    await save_media_file(exp_id, filename, photo.file_id, "image")
    uploaded = data.get("uploaded_files", {})
    uploaded[filename] = photo.file_id
    await state.update_data(uploaded_files=uploaded)
    await message.answer(f"Файл «{filename}» загружен.")


@router.message(MediaUpload.waiting_files, F.document)
async def on_document(message: types.Message, state: FSMContext, bot: Bot):
    """получили документ (аудио, видео, изображение)"""
    doc = message.document
    filename = doc.file_name or ""
    data = await state.get_data()
    exp_id = data["media_experiment_id"]

    if not filename or filename not in _expected_set(data):
        await _reject_unexpected(message, filename or "(без имени)")
        return

    # определяем тип
    mime = doc.mime_type or ""
    if mime.startswith("audio"):
        media_type = "audio"
    elif mime.startswith("video"):
        media_type = "video"
    elif mime.startswith("image"):
        media_type = "image"
    else:
        media_type = "other"

    original_file_id = doc.file_id
    file_id = original_file_id
    duration_ms = 0
    silence = await _silence_seconds_for(exp_id)
    # для аудио всегда перезаливаем через send_audio: drag-drop .wav
    # приходит как document — у такого file_id плеер участнику не
    # развернётся, бот покажет карточку файла с именем. перезалив
    # даёт чистый audio file_id (заодно добавляет тишину, если
    # настроена).
    if media_type == "audio":
        try:
            file_id, duration_ms = await audio_util.reupload_padded_audio(
                bot, message.chat.id, original_file_id, silence,
                filename=filename,
            )
        except Exception as e:
            logger.warning("нормализация аудио %s не удалась: %s", filename, e)

    await save_media_file(
        exp_id, filename, file_id, media_type,
        original_file_id=original_file_id, silence_ms=silence * 1000,
        duration_ms=duration_ms,
    )
    uploaded = data.get("uploaded_files", {})
    uploaded[filename] = file_id
    await state.update_data(uploaded_files=uploaded)
    await message.answer(f"Файл «{filename}» загружен.")


@router.message(MediaUpload.waiting_files, F.audio)
async def on_audio(message: types.Message, state: FSMContext, bot: Bot):
    """получили аудиофайл"""
    audio = message.audio
    filename = audio.file_name or ""
    data = await state.get_data()
    exp_id = data["media_experiment_id"]

    if not filename or filename not in _expected_set(data):
        await _reject_unexpected(message, filename or "(без имени)")
        return

    original_file_id = audio.file_id
    file_id = original_file_id
    duration_ms = (audio.duration or 0) * 1000
    silence = await _silence_seconds_for(exp_id)
    # перезаливаем всегда — даже если silence=0: заголовок аудио-плеера
    # у Telegram наследуется из загруженного файла, а нам нужен пустой
    # title/performer, чтобы участник не видел имени файла.
    try:
        file_id, duration_ms = await audio_util.reupload_padded_audio(
            bot, message.chat.id, original_file_id, silence,
            filename=filename,
        )
    except Exception as e:
        logger.warning("нормализация аудио %s не удалась: %s", filename, e)

    await save_media_file(
        exp_id, filename, file_id, "audio",
        original_file_id=original_file_id, silence_ms=silence * 1000,
        duration_ms=duration_ms,
    )
    uploaded = data.get("uploaded_files", {})
    uploaded[filename] = file_id
    await state.update_data(uploaded_files=uploaded)
    await message.answer(f"Аудио «{filename}» загружено.")


@router.message(MediaUpload.waiting_files, F.video)
async def on_video(message: types.Message, state: FSMContext, bot: Bot):
    """получили видео"""
    video = message.video
    filename = video.file_name or ""
    data = await state.get_data()
    exp_id = data["media_experiment_id"]

    if not filename or filename not in _expected_set(data):
        await _reject_unexpected(message, filename or "(без имени)")
        return

    await save_media_file(exp_id, filename, video.file_id, "video")
    uploaded = data.get("uploaded_files", {})
    uploaded[filename] = video.file_id
    await state.update_data(uploaded_files=uploaded)
    await message.answer(f"Видео «{filename}» загружено.")


@router.message(MediaUpload.waiting_files, F.voice)
async def on_voice_file(message: types.Message, state: FSMContext, bot: Bot):
    """голосовое сообщение как аудиофайл. у voice в Telegram нет
    file_name — единственный способ задать имя — caption."""
    data = await state.get_data()
    exp_id = data["media_experiment_id"]
    caption = message.caption or ""
    filename = caption.strip()
    if not filename or filename not in _expected_set(data):
        await _reject_unexpected(message, filename or "(без имени)")
        return

    # voice → audio: send_audio с voice file_id Telegram не примет,
    # перезаливаем через download+upload, попутно подставляя silence.
    original_file_id = message.voice.file_id
    file_id = original_file_id
    duration_ms = (message.voice.duration or 0) * 1000
    silence = await _silence_seconds_for(exp_id)
    try:
        file_id, duration_ms = await audio_util.reupload_padded_audio(
            bot, message.chat.id, original_file_id, silence,
            filename=filename if "." in filename else f"{filename}.ogg",
        )
    except Exception as e:
        logger.warning("конвертация voice %s не удалась: %s", filename, e)

    await save_media_file(
        exp_id, filename, file_id, "audio",
        original_file_id=original_file_id, silence_ms=silence * 1000,
        duration_ms=duration_ms,
    )
    uploaded = data.get("uploaded_files", {})
    uploaded[filename] = file_id
    await state.update_data(uploaded_files=uploaded)
    await message.answer(f"Голосовое «{filename}» загружено.")


@router.callback_query(MediaUpload.waiting_files, F.data == "media_done")
async def on_media_done(callback: types.CallbackQuery, state: FSMContext):
    """завершить загрузку медиа и привязать file_id к пробам.

    источник истины — media-коллекция, а не state.uploaded_files:
    aiogram обрабатывает несколько файлов из одного multi-select
    параллельно, и read-modify-write поверх state теряет записи
    (последний писатель побеждает). save_media_file делает upsert
    по (experiment_id, filename) — поэтому в БД лежит правильный
    набор."""
    await callback.answer()
    data = await state.get_data()
    exp_id = data["media_experiment_id"]
    expected = set(data.get("expected_files", []))

    media_records = await repo.get_media_by_experiment(exp_id)
    uploaded = {m["filename"]: m["file_id"] for m in media_records}

    # привязываем file_id к пробам через единый хелпер
    await attach_media_file_ids(exp_id)

    missing = expected - set(uploaded.keys())
    banner = (
        f"⚠️ Загрузка завершена. Не загружены: {', '.join(sorted(missing))}"
        if missing else
        "✅ Все медиафайлы загружены и привязаны к пробам."
    )
    await state.clear()
    # возвращаемся в карточку черновика — иначе исследователь зависает на
    # экране загрузки без видимого продолжения. lazy import: researcher
    # сам импортирует media_upload, прямой импорт привёл бы к циклу.
    from handlers.researcher import show_experiment_detail
    await show_experiment_detail(
        callback, exp_id, banner=banner, state=state,
    )


async def save_media_file(
    experiment_id: str, filename: str, file_id: str, media_type: str,
    original_file_id: str | None = None, silence_ms: int = 0,
    duration_ms: int = 0,
):
    """сохранить запись о медиафайле в коллекцию.

    при повторной загрузке файла с тем же filename — обновляем
    запись (а не плодим дубли), чтобы при перезаливке аудио
    подхватился новый file_id, силенс и длительность."""
    existing = await repo.get_media_by_filename(experiment_id, filename)
    payload = {
        "experiment_id": experiment_id,
        "filename": filename,
        "file_id": file_id,
        "media_type": media_type,
        "original_file_id": original_file_id or file_id,
        "silence_ms": silence_ms,
        "duration_ms": duration_ms,
    }
    if existing:
        await repo.update_media(str(existing["_id"]), payload)
    else:
        await repo.save_media(payload)


async def _silence_seconds_for(experiment_id: str) -> int:
    """текущая настройка тишины для эксперимента (секунды)."""
    exp = await repo.get_experiment(experiment_id)
    if not exp:
        return 0
    return int(exp.get("audio_silence_seconds", 0) or 0)


def attach_media_ids_to_phases(media_records: list, phases: list) -> bool:
    """проставить file_id в trial.stimulus_metadata по filename из media.
    мутирует phases in-place. возвращает True, если что-то изменилось.

    нужна не только при «Готово» в загрузчике: build_phase в on_save_draft
    пересобирает пробы из CSV и стирает уже привязанные file_id.
    Логика «media → trial» должна вызываться и после такой пересборки,
    и перед валидацией активации — иначе валидатор вернёт «не загружен
    медиафайл», хотя в media-коллекции все файлы есть."""
    file_id_by_name = {m["filename"]: m["file_id"] for m in media_records}
    changed = False
    for phase in phases:
        if phase.get("stimulus_type") not in ("audio", "image", "video"):
            continue
        for trial in phase.get("trials", []):
            stim = trial.get("stimulus_content", "")
            new_id = file_id_by_name.get(stim)
            if not new_id:
                continue
            trial.setdefault("stimulus_metadata", {})
            if trial["stimulus_metadata"].get("file_id") != new_id:
                trial["stimulus_metadata"]["file_id"] = new_id
                changed = True
    return changed


async def attach_media_file_ids(experiment_id: str) -> None:
    """прочитать media-коллекцию и записать file_id в experiment.phases
    для всех проб, чьё stimulus_content совпадает с filename."""
    media_records = await repo.get_media_by_experiment(experiment_id)
    if not media_records:
        return
    exp = await repo.get_experiment(experiment_id)
    if not exp:
        return
    phases = exp.get("phases", [])
    if attach_media_ids_to_phases(media_records, phases):
        await repo.update_experiment(experiment_id, {"phases": phases})
