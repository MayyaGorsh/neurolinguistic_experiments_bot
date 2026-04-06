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
