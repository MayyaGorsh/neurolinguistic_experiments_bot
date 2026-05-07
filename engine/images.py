"""
утилиты для работы с картинками-стимулами.

shrink_image_for_photo — ужать байты до Telegram-лимита send_photo
(≤10 МБ): уменьшаем по убывающей лестнице max-стороны, пересжимаем
в JPEG со ступенчатым снижением качества.
"""

import io
import logging


logger = logging.getLogger("bot")

# Telegram отвергает send_photo, если файл больше 10 МБ. Это абсолютный
# потолок; реальная цель — сильно меньше: на телефоне стимул всё равно
# отображается на экране ≤ 800 px шириной, поэтому 1280 px / ~1 МБ
# достаточно с запасом. Маленький файл = быстрый upload в Telegram на
# первом показе и быстрый GridFS-read.
_TELEGRAM_PHOTO_BYTES_LIMIT = 10 * 1024 * 1024
_PHOTO_TARGET_BYTES = 1 * 1024 * 1024
_PHOTO_MAX_DIM = 1280


def shrink_image_for_photo(img_bytes: bytes, hint_filename: str = "") -> bytes:
    """вернуть байты, которые точно пройдут через Telegram send_photo
    и будут как можно компактнее (~1 МБ): уменьшаем размер и
    пересжимаем в JPEG. Если файл уже меньше целевого размера, не
    трогаем — нет смысла пере-энкодить уже небольшую картинку."""
    if len(img_bytes) <= _PHOTO_TARGET_BYTES:
        return img_bytes
    try:
        from PIL import Image
    except Exception:
        return img_bytes

    # PIL по умолчанию защищается от «бомб» и роняет load() при ~178M
    # пикселей. У стимулов попадаются легитимно огромные исходники,
    # отключаем эту защиту здесь явно.
    Image.MAX_IMAGE_PIXELS = None

    # пробуем по убыванию максимального ребра: сначала целимся в
    # _PHOTO_MAX_DIM, дальше срезаем агрессивнее, если всё ещё перевес.
    last = b""
    for max_dim in (_PHOTO_MAX_DIM, 960, 720, 540):
        try:
            with Image.open(io.BytesIO(img_bytes)) as im:
                im.load()
                if im.mode not in ("RGB", "L"):
                    im = im.convert("RGB")
                im.thumbnail((max_dim, max_dim), Image.LANCZOS)
                for quality in (85, 75, 65, 55, 45):
                    buf = io.BytesIO()
                    im.save(buf, format="JPEG", quality=quality, optimize=True)
                    last = buf.getvalue()
                    if len(last) <= _PHOTO_TARGET_BYTES:
                        return last
                if len(last) <= _TELEGRAM_PHOTO_BYTES_LIMIT:
                    return last
        except Exception:
            logger.exception(
                "shrink_image_for_photo упал на %s (max_dim=%s)",
                hint_filename, max_dim,
            )
            return img_bytes
    # даже 720 px дал слишком тяжёлый файл — отдадим как есть, а
    # решение «фото или документ» останется выше.
    return last or img_bytes
