import certifi
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket
from config import MONGO_URI, DB_NAME

# для Atlas нужен tlsCAFile, для локального mongodb:// — нет
_kwargs = {"tlsCAFile": certifi.where()} if "mongodb+srv" in MONGO_URI else {}
client = AsyncIOMotorClient(MONGO_URI, **_kwargs)
db = client[DB_NAME]

# коллекции
users_col = db["users"]
experiments_col = db["experiments"]
sessions_col = db["sessions"]
answers_col = db["answers"]
media_col = db["media"]
mailings_col = db["mailings"]


# GridFS-бакет для бинарного содержимого голосовых ответов. file_id'ы
# Telegram обещаны "вечными", но мы всё равно тащим байты к себе сразу
# при получении — чтобы выгрузка через несколько месяцев не зависела от
# доступности файла на стороне Telegram.
#
# Инстанцируется лениво из async-контекста: AsyncIOMotorGridFSBucket в
# конструкторе хватает текущий event loop (или создаёт новый через
# asyncio.get_event_loop), и если это происходит на импорте модуля до
# запуска aiogram'овского loop'а, то bucket «приклеивается» не к тому
# loop'у, и весь motor-клиент после этого начинает падать с
# "got Future attached to a different loop". Поэтому создаём только когда
# уже есть running loop.
_voice_answers_bucket: AsyncIOMotorGridFSBucket | None = None


def get_voice_answers_bucket() -> AsyncIOMotorGridFSBucket:
    global _voice_answers_bucket
    if _voice_answers_bucket is None:
        _voice_answers_bucket = AsyncIOMotorGridFSBucket(
            db, bucket_name="voice_answers",
        )
    return _voice_answers_bucket


# GridFS-бакет для байт стимулов-картинок: храним уже ужатые под
# Telegram-лимит фото (≤10 МБ). Bot API не даёт скачивать через getFile
# файлы >20 МБ — поэтому хранить байты у себя единственно надёжный
# способ. Аудио/видео пока не трогаем (там мы просто переотправляем по
# file_id, лимиты крупнее).
_stimulus_media_bucket: AsyncIOMotorGridFSBucket | None = None


def get_stimulus_media_bucket() -> AsyncIOMotorGridFSBucket:
    global _stimulus_media_bucket
    if _stimulus_media_bucket is None:
        _stimulus_media_bucket = AsyncIOMotorGridFSBucket(
            db, bucket_name="stimulus_media",
        )
    return _stimulus_media_bucket
