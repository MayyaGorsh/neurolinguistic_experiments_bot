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
        + "\n\nОтправляйте файлы по одному. Имя файла должно совпадать "
        "с тем, что указано в CSV.\n\n"
        "💡 <b>Замена файла:</b> если файл с таким именем уже был "
        "загружен раньше — просто пришлите новый с тем же именем, "
        "и в эксперименте будет использоваться новая версия."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Готово", callback_data="media_done")],
    ])
    await message.answer(text, reply_markup=kb)
    await state.set_state(MediaUpload.waiting_files)


@router.message(MediaUpload.waiting_files, F.photo)
async def on_photo(message: types.Message, state: FSMContext, bot: Bot):
    """получили фото — сохраняем file_id"""
    photo = message.photo[-1]  # лучшее качество
    caption = message.caption or ""
    data = await state.get_data()
    exp_id = data["media_experiment_id"]

    # определяем имя файла из подписи или по порядку
    filename = caption.strip() if caption else f"photo_{photo.file_id[:8]}"

    await save_media_file(exp_id, filename, photo.file_id, "image")
    uploaded = data.get("uploaded_files", {})
    uploaded[filename] = photo.file_id
    await state.update_data(uploaded_files=uploaded)
    await message.answer(f"Файл «{filename}» загружен.")


@router.message(MediaUpload.waiting_files, F.document)
async def on_document(message: types.Message, state: FSMContext, bot: Bot):
    """получили документ (аудио, видео, изображение)"""
    doc = message.document
    filename = doc.file_name or f"file_{doc.file_id[:8]}"
    data = await state.get_data()
    exp_id = data["media_experiment_id"]

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

    # для аудио: проверяем, нужна ли тишина
    file_id = doc.file_id
    silence = data.get("silence_ms", 0)
    if media_type == "audio" and silence > 0:
        file = await bot.download(doc)
        audio_bytes = file.read()
        audio_bytes = audio_util.append_silence(audio_bytes, silence)
        # загружаем обратно — нужно отправить боту и получить новый file_id
        # (сохраняем оригинальный file_id, тишина применяется при отправке)

    await save_media_file(exp_id, filename, file_id, media_type)
    uploaded = data.get("uploaded_files", {})
    uploaded[filename] = file_id
    await state.update_data(uploaded_files=uploaded)
    await message.answer(f"Файл «{filename}» загружен.")


@router.message(MediaUpload.waiting_files, F.audio)
async def on_audio(message: types.Message, state: FSMContext, bot: Bot):
    """получили аудиофайл"""
    audio = message.audio
    filename = audio.file_name or f"audio_{audio.file_id[:8]}"
    data = await state.get_data()
    exp_id = data["media_experiment_id"]

    await save_media_file(exp_id, filename, audio.file_id, "audio")
    uploaded = data.get("uploaded_files", {})
    uploaded[filename] = audio.file_id
    await state.update_data(uploaded_files=uploaded)
    await message.answer(f"Аудио «{filename}» загружено.")


@router.message(MediaUpload.waiting_files, F.video)
async def on_video(message: types.Message, state: FSMContext, bot: Bot):
    """получили видео"""
    video = message.video
    filename = video.file_name or f"video_{video.file_id[:8]}"
    data = await state.get_data()
    exp_id = data["media_experiment_id"]

    await save_media_file(exp_id, filename, video.file_id, "video")
    uploaded = data.get("uploaded_files", {})
    uploaded[filename] = video.file_id
    await state.update_data(uploaded_files=uploaded)
    await message.answer(f"Видео «{filename}» загружено.")


@router.message(MediaUpload.waiting_files, F.voice)
async def on_voice_file(message: types.Message, state: FSMContext):
    """голосовое сообщение как аудиофайл"""
    data = await state.get_data()
    exp_id = data["media_experiment_id"]
    filename = f"voice_{message.voice.file_id[:8]}"

    await save_media_file(exp_id, filename, message.voice.file_id, "audio")
    uploaded = data.get("uploaded_files", {})
    uploaded[filename] = message.voice.file_id
    await state.update_data(uploaded_files=uploaded)
    await message.answer(f"Голосовое «{filename}» загружено.")


@router.callback_query(MediaUpload.waiting_files, F.data == "media_done")
async def on_media_done(callback: types.CallbackQuery, state: FSMContext):
    """завершить загрузку медиа и привязать file_id к пробам"""
    await callback.answer()
    data = await state.get_data()
    exp_id = data["media_experiment_id"]
    uploaded = data.get("uploaded_files", {})
    expected = set(data.get("expected_files", []))

    # привязываем file_id к пробам
    exp = await repo.get_experiment(exp_id)
    if exp:
        phases = exp.get("phases", [])
        for phase in phases:
            if phase.get("stimulus_type") in ("audio", "image", "video"):
                for trial in phase.get("trials", []):
                    stim = trial.get("stimulus_content", "")
                    if stim in uploaded:
                        trial.setdefault("stimulus_metadata", {})
                        trial["stimulus_metadata"]["file_id"] = uploaded[stim]
        await repo.update_experiment(exp_id, {"phases": phases})

    missing = expected - set(uploaded.keys())
    if missing:
        await callback.message.answer(
            f"Загрузка завершена. Не загружены: {', '.join(sorted(missing))}"
        )
    else:
        await callback.message.answer("Все медиафайлы загружены и привязаны к пробам.")

    await state.clear()


async def save_media_file(experiment_id: str, filename: str, file_id: str, media_type: str):
    """сохранить запись о медиафайле в коллекцию"""
    await repo.save_media({
        "experiment_id": experiment_id,
        "filename": filename,
        "file_id": file_id,
        "media_type": media_type,
    })
