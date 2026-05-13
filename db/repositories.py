import logging
from datetime import datetime
from typing import Optional

from bson import ObjectId

from db.connection import (
    db,
    users_col,
    experiments_col,
    sessions_col,
    answers_col,
    media_col,
    mailings_col,
    get_voice_answers_bucket,
    get_stimulus_media_bucket,
)

logger = logging.getLogger("bot")


# ── пользователи ──

async def get_user(telegram_id: int) -> Optional[dict]:
    return await users_col.find_one({"telegram_id": telegram_id})


async def create_user(data: dict) -> str:
    result = await users_col.insert_one(data)
    logger.info("создан пользователь telegram_id=%s", data.get("telegram_id"))
    return str(result.inserted_id)


async def update_user(telegram_id: int, update: dict):
    await users_col.update_one(
        {"telegram_id": telegram_id},
        {"$set": update},
    )


async def get_or_create_user(telegram_id: int, defaults: dict) -> dict:
    """вернуть пользователя или создать нового с defaults"""
    user = await get_user(telegram_id)
    if user:
        return user
    defaults["telegram_id"] = telegram_id
    await create_user(defaults)
    return await get_user(telegram_id)


# ── эксперименты ──

async def create_experiment(data: dict) -> str:
    result = await experiments_col.insert_one(data)
    logger.info("создан эксперимент %s", result.inserted_id)
    return str(result.inserted_id)


async def get_experiment(experiment_id: str) -> Optional[dict]:
    return await experiments_col.find_one({"_id": ObjectId(experiment_id)})


async def get_experiment_by_link(deep_link_id: str) -> Optional[dict]:
    return await experiments_col.find_one({"deep_link_id": deep_link_id})


async def get_experiments_by_owner(owner_id: int) -> list:
    cursor = experiments_col.find({"owner_id": owner_id}).sort("created_at", -1)
    return await cursor.to_list(length=100)


async def update_experiment(experiment_id: str, update: dict):
    update["updated_at"] = datetime.utcnow()
    await experiments_col.update_one(
        {"_id": ObjectId(experiment_id)},
        {"$set": update},
    )


async def delete_experiment_cascade(experiment_id: str) -> dict:
    """удалить эксперимент и все связанные данные: сессии, ответы, медиа,
    голосовые блобы, стимульные блобы. Возвращает количество удалённых
    записей по коллекциям."""
    voice_res = await delete_voice_blobs_by_experiment(experiment_id)
    stim_res = await delete_stimulus_blobs_by_experiment(experiment_id)
    answers_res = await answers_col.delete_many({"experiment_id": experiment_id})
    sessions_res = await sessions_col.delete_many({"experiment_id": experiment_id})
    media_res = await media_col.delete_many({"experiment_id": experiment_id})
    exp_res = await experiments_col.delete_one(
        {"_id": ObjectId(experiment_id)}
    )
    logger.info(
        "удалён эксперимент %s: answers=%d, sessions=%d, media=%d, "
        "voice_blobs=%d, stim_blobs=%d",
        experiment_id,
        answers_res.deleted_count, sessions_res.deleted_count,
        media_res.deleted_count, voice_res, stim_res,
    )
    return {
        "experiment": exp_res.deleted_count,
        "sessions": sessions_res.deleted_count,
        "answers": answers_res.deleted_count,
        "media": media_res.deleted_count,
        "voice_blobs": voice_res,
        "stim_blobs": stim_res,
    }


# ── голосовые ответы (GridFS) ──

async def save_voice_blob(
    data: bytes,
    filename: str,
    metadata: dict,
) -> str:
    """сохранить байты голосового ответа в GridFS, вернуть строковый id."""
    bucket = get_voice_answers_bucket()
    oid = await bucket.upload_from_stream(
        filename, data, metadata=metadata,
    )
    return str(oid)


async def read_voice_blob(blob_id: str) -> Optional[bytes]:
    """прочитать байты голосового ответа по id, вернуть None если нет."""
    bucket = get_voice_answers_bucket()
    try:
        stream = await bucket.open_download_stream(ObjectId(blob_id))
    except Exception as e:
        logger.warning("voice blob %s не найден: %s", blob_id, e)
        return None
    try:
        return await stream.read()
    finally:
        try:
            await stream.close()
        except Exception:
            pass


# ── стимульные блобы (GridFS, картинки) ──

async def save_stimulus_blob(
    data: bytes, filename: str, metadata: dict,
) -> str:
    bucket = get_stimulus_media_bucket()
    oid = await bucket.upload_from_stream(filename, data, metadata=metadata)
    return str(oid)


async def read_stimulus_blob(blob_id: str) -> Optional[bytes]:
    bucket = get_stimulus_media_bucket()
    try:
        stream = await bucket.open_download_stream(ObjectId(blob_id))
    except Exception as e:
        logger.warning("stimulus blob %s не найден: %s", blob_id, e)
        return None
    try:
        return await stream.read()
    finally:
        try:
            await stream.close()
        except Exception:
            pass


async def _delete_blobs_by_experiment(
    bucket_name: str, bucket, experiment_id: str,
) -> int:
    """общая логика удаления GridFS-блобов по metadata.experiment_id.
    GridFS-бакет с именем X хранит файлы в коллекциях <X>.files /
    <X>.chunks; в .files лежит наш metadata, по нему и ищем."""
    files_col = db[f"{bucket_name}.files"]
    cursor = files_col.find(
        {"metadata.experiment_id": experiment_id}, {"_id": 1},
    )
    count = 0
    async for doc in cursor:
        try:
            await bucket.delete(doc["_id"])
            count += 1
        except Exception as e:
            logger.warning(
                "не удалось удалить %s blob %s: %s",
                bucket_name, doc["_id"], e,
            )
    return count


async def delete_stimulus_blobs_by_experiment(experiment_id: str) -> int:
    return await _delete_blobs_by_experiment(
        "stimulus_media", get_stimulus_media_bucket(), experiment_id,
    )


async def delete_voice_blobs_by_experiment(experiment_id: str) -> int:
    return await _delete_blobs_by_experiment(
        "voice_answers", get_voice_answers_bucket(), experiment_id,
    )


# ── сессии ──

async def create_session(data: dict) -> str:
    result = await sessions_col.insert_one(data)
    return str(result.inserted_id)


async def get_session(session_id: str) -> Optional[dict]:
    return await sessions_col.find_one({"_id": ObjectId(session_id)})


async def get_active_session(telegram_id: int, experiment_id: str) -> Optional[dict]:
    """найти незавершенную сессию пользователя для эксперимента"""
    return await sessions_col.find_one({
        "telegram_id": telegram_id,
        "experiment_id": experiment_id,
        "status": {"$in": ["started", "in_progress"]},
    })


async def get_latest_active_session(telegram_id: int) -> Optional[dict]:
    """последняя по времени незавершённая сессия пользователя.

    нужна, когда мы ловим не клик по конкретной клавиатуре, а текстовое или
    голосовое сообщение: у респондента может остаться несколько брошенных
    in_progress-сессий по разным экспериментам, и find_one возвращает
    случайную. сортируем по _id desc — берём самую свежую."""
    cursor = sessions_col.find({
        "telegram_id": telegram_id,
        "status": {"$in": ["started", "in_progress"]},
    }).sort("_id", -1).limit(1)
    docs = await cursor.to_list(length=1)
    return docs[0] if docs else None


async def abandon_other_active_sessions(
    telegram_id: int, keep_session_id: Optional[str] = None,
) -> int:
    """закрыть все in_progress сессии пользователя кроме keep_session_id.

    вызываем при старте/резюме нового прохождения, чтобы старые брошенные
    сессии не цеплялись за текстовые ответы (см. get_latest_active_session)
    и не конфликтовали по pending_judgment."""
    query: dict = {
        "telegram_id": telegram_id,
        "status": {"$in": ["started", "in_progress"]},
    }
    if keep_session_id:
        query["_id"] = {"$ne": ObjectId(keep_session_id)}
    result = await sessions_col.update_many(
        query,
        {"$set": {"status": "abandoned", "finished_at": datetime.utcnow()}},
    )
    return result.modified_count


async def get_sessions_by_experiment(experiment_id: str) -> list:
    cursor = sessions_col.find({"experiment_id": experiment_id})
    return await cursor.to_list(length=10000)


async def update_session(session_id: str, update: dict):
    await sessions_col.update_one(
        {"_id": ObjectId(session_id)},
        {"$set": update},
    )


async def push_phase_message_id(session_id: str, message_id: int):
    """добавить message_id в phase_message_ids сессии (атомарный $push).
    список используется для удаления всех сообщений текущей фазы при
    переходе к следующей — независимо от настройки delete_previous_trials."""
    await sessions_col.update_one(
        {"_id": ObjectId(session_id)},
        {"$push": {"phase_message_ids": message_id}},
    )


async def mark_session_completed(session_id: str) -> bool:
    """атомарно перевести сессию в completed.
    возвращает True, если этот вызов выиграл гонку (статус был не completed),
    и False, если сессия уже была завершена кем-то ещё (двойной клик, гонка
    тайм-аута и ответа). вызывающая сторона использует это, чтобы не слать
    «эксперимент завершён» повторно."""
    result = await sessions_col.update_one(
        {"_id": ObjectId(session_id), "status": {"$ne": "completed"}},
        {"$set": {"status": "completed", "finished_at": datetime.utcnow()}},
    )
    return result.modified_count == 1


async def count_sessions_by_list(experiment_id: str) -> dict:
    """подсчет количества сессий по каждому листу для balanced distribution"""
    pipeline = [
        {"$match": {"experiment_id": experiment_id}},
        {"$group": {"_id": "$assigned_list", "count": {"$sum": 1}}},
    ]
    result = {}
    async for doc in sessions_col.aggregate(pipeline):
        result[doc["_id"]] = doc["count"]
    return result


# ── ответы ──

async def save_answer(data: dict) -> str:
    result = await answers_col.insert_one(data)
    return str(result.inserted_id)


async def get_answers_by_session(session_id: str) -> list:
    cursor = answers_col.find({"session_id": session_id})
    return await cursor.to_list(length=10000)


async def get_answers_by_experiment(experiment_id: str) -> list:
    cursor = answers_col.find({"experiment_id": experiment_id})
    return await cursor.to_list(length=100000)


# ── медиа ──

async def save_media(data: dict) -> str:
    result = await media_col.insert_one(data)
    return str(result.inserted_id)


async def get_media(media_id: str) -> Optional[dict]:
    return await media_col.find_one({"_id": ObjectId(media_id)})


async def get_media_by_experiment(experiment_id: str) -> list:
    cursor = media_col.find({"experiment_id": experiment_id})
    return await cursor.to_list(length=10000)


async def get_media_by_filename(experiment_id: str, filename: str) -> Optional[dict]:
    return await media_col.find_one({
        "experiment_id": experiment_id, "filename": filename,
    })


async def update_media(media_id: str, update: dict):
    await media_col.update_one(
        {"_id": ObjectId(media_id)},
        {"$set": update},
    )


async def set_media_photo_id(
    experiment_id: str, filename: str, photo_id: str,
) -> None:
    """после первого реального send_media_group / send_photo Telegram
    возвращает photo file_id — кэшируем его в media.file_id. Так
    следующие сессии этого же эксперимента сразу пойдут по «быстрой»
    ветке, без upload'а байтов."""
    await media_col.update_one(
        {"experiment_id": experiment_id, "filename": filename},
        {"$set": {"file_id": photo_id}},
    )


# ── рассылки ──

async def save_mailing(data: dict) -> str:
    result = await mailings_col.insert_one(data)
    return str(result.inserted_id)


async def get_past_participants() -> list:
    """список telegram_id пользователей, которые хотя бы раз завершили
    эксперимент и согласились получать сообщения от бота.
    рассылка не идёт тем, кто не дал согласия (consent_given != True)."""
    pipeline = [
        {"$match": {"status": "completed", "is_preview": {"$ne": True}}},
        {"$group": {"_id": "$telegram_id"}},
        {"$lookup": {
            "from": "users",
            "localField": "_id",
            "foreignField": "telegram_id",
            "as": "user",
        }},
        {"$match": {"user.consent_given": True}},
    ]
    result = []
    async for doc in sessions_col.aggregate(pipeline):
        result.append(doc["_id"])
    return result
