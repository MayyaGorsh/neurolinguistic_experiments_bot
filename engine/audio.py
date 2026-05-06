"""
работа с аудио: добавление тишины в конце файла,
удаление сообщения после проигрывания.
"""

import asyncio
import io
import logging

from pydub import AudioSegment

logger = logging.getLogger("bot")


def append_silence(audio_bytes: bytes, silence_ms: int, fmt: str = "ogg") -> bytes:
    """добавить тишину в конце аудиофайла"""
    try:
        audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format=fmt)
        silence = AudioSegment.silent(duration=silence_ms)
        result = audio + silence
        out = io.BytesIO()
        result.export(out, format=fmt)
        return out.getvalue()
    except Exception as e:
        logger.error("ошибка добавления тишины: %s", e)
        return audio_bytes


def get_duration_ms(audio_bytes: bytes, fmt: str = "ogg") -> int:
    """получить длительность аудио в миллисекундах"""
    try:
        audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format=fmt)
        return len(audio)
    except Exception:
        return 0


async def delete_after_playback(bot, chat_id: int, message_id: int, duration_ms: int):
    """удалить сообщение после предполагаемого времени проигрывания"""
    try:
        await asyncio.sleep(duration_ms / 1000)
        await bot.delete_message(chat_id, message_id)
    except Exception as e:
        logger.debug("не удалось удалить сообщение: %s", e)


async def reupload_padded_audios_batch(
    bot, chat_id: int, items: list[tuple[str, str]], silence_seconds: int,
) -> dict[str, tuple[str, int]]:
    """перезалить пачку аудио одним media-group и сразу удалить альбом.

    items — список (source_file_id, filename). возвращает
    {filename: (new_audio_file_id, duration_ms)}. duration_ms нужна
    в runtime для отложенного удаления стимула после проигрывания.

    media-group в Telegram — максимум 10 элементов, поэтому большие
    наборы режем на пачки по 10."""
    from aiogram.types import BufferedInputFile, InputMediaAudio

    result: dict[str, tuple[str, int]] = {}
    if not items:
        return result

    BATCH = 10
    for i in range(0, len(items), BATCH):
        chunk = items[i:i + BATCH]
        media_inputs: list[InputMediaAudio] = []
        chunk_meta: list[tuple[str, int]] = []  # (filename, duration_ms)
        for src_id, filename in chunk:
            try:
                file = await bot.download(src_id)
                audio_bytes = file.read()
            except Exception as e:
                logger.warning("download %s не удался: %s", filename, e)
                continue
            fmt = (filename.rsplit(".", 1)[-1] or "mp3").lower()
            if fmt not in ("mp3", "ogg", "wav", "m4a", "opus"):
                fmt = "mp3"
            if silence_seconds > 0:
                audio_bytes = append_silence(
                    audio_bytes, silence_seconds * 1000, fmt=fmt,
                )
            duration_ms = get_duration_ms(audio_bytes, fmt=fmt)
            media_inputs.append(InputMediaAudio(
                media=BufferedInputFile(audio_bytes, filename=filename),
                title=" ", performer=" ",
            ))
            chunk_meta.append((filename, duration_ms))

        if not media_inputs:
            continue

        try:
            messages = await bot.send_media_group(
                chat_id, media_inputs, disable_notification=True,
            )
        except Exception as e:
            logger.warning("send_media_group не удался: %s", e)
            continue

        for (filename, duration_ms), msg in zip(chunk_meta, messages):
            new_id = msg.audio.file_id if msg.audio else (
                msg.document.file_id if msg.document else None
            )
            if new_id:
                result[filename] = (new_id, duration_ms)

        try:
            await bot.delete_messages(
                chat_id, [m.message_id for m in messages],
            )
        except Exception:
            for m in messages:
                try:
                    await bot.delete_message(chat_id, m.message_id)
                except Exception:
                    pass

    return result


async def reupload_padded_audio(
    bot, chat_id: int, source_file_id: str, silence_seconds: int,
    filename: str = "audio.mp3",
) -> tuple[str, int]:
    """скачать аудио по file_id, опционально добавить тишину, отправить
    обратно через send_audio (с пустыми title/performer, чтобы участник
    не видел имени файла) и вернуть новый file_id audio-типа.
    сообщение сразу удаляется — нам нужен только file_id.

    нормализует тип file_id: если оригинал был загружен как document
    (drag-drop .wav), новый file_id будет именно audio — иначе бот
    при отправке участнику показывает .wav как документ-карточку
    с названием файла, а не как плеер.

    chat_id должен быть chat-ом исследователя — telegram-у нужно
    куда-то отправить файл, чтобы выдать новый file_id."""
    from aiogram.types import BufferedInputFile

    file = await bot.download(source_file_id)
    audio_bytes = file.read()
    fmt = (filename.rsplit(".", 1)[-1] or "mp3").lower()
    if fmt not in ("mp3", "ogg", "wav", "m4a", "opus"):
        fmt = "mp3"
    if silence_seconds > 0:
        audio_bytes = append_silence(
            audio_bytes, silence_seconds * 1000, fmt=fmt,
        )
    duration_ms = get_duration_ms(audio_bytes, fmt=fmt)

    msg = await bot.send_audio(
        chat_id,
        BufferedInputFile(audio_bytes, filename=filename),
        title=" ", performer=" ",
        disable_notification=True,
    )
    new_file_id = msg.audio.file_id if msg.audio else (
        msg.document.file_id if msg.document else None
    )
    try:
        await bot.delete_message(chat_id, msg.message_id)
    except Exception:
        pass
    return (new_file_id or source_file_id, duration_ms)
