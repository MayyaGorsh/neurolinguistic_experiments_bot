"""
движок прохождения эксперимента.
управляет последовательностью фаз и проб, измеряет RT,
обрабатывает тайм-ауты и сохраняет ответы.
"""

import asyncio
import logging
import random
import time
from datetime import datetime
from typing import Optional

from aiogram import Bot, types
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaAudio,
)
from bson import ObjectId

from db import repositories as repo
from engine import audio as audio_util
from engine import images as image_util
from utils.idle_guard import touch_session

logger = logging.getLogger("bot")

# хранилище активных таймеров тайм-аутов {session_id: asyncio.Task}
_timeout_tasks: dict[str, asyncio.Task] = {}

# хранилище времени показа стимула {session_id: timestamp в секундах}
_stimulus_shown_at: dict[str, float] = {}


# ── показ стимула ──

async def _delete_transient(bot: Bot, chat_id: int, message_ids: list):
    """удалить ранее отправленные ботом сообщения (стимул/инструкция/тайм-аут).

    ошибки игнорируем — сообщение может быть уже удалено вручную или старше
    48 часов (Telegram запрещает удалять такие). это не должно прерывать
    показ следующей пробы.
    """
    for mid in message_ids:
        try:
            await bot.delete_message(chat_id, mid)
        except Exception:
            pass


async def present_trial(bot: Bot, chat_id: int, session: dict, experiment: dict):
    """показать текущую пробу респонденту"""
    phase_idx = session["current_phase"]
    trial_idx = session["current_trial"]
    phases = experiment["phases"]
    session_id = str(session["_id"])

    # сбрасываем idle-таймер: показ нового стимула считается активностью,
    # иначе долгое раздумье участника превращалось бы в «бездействие».
    await touch_session(session_id)

    # граница фазы: при переходе к следующей фазе (но не к «фантомной»
    # фазе за концом эксперимента) удаляем все сообщения предыдущей фазы
    # — стимулы, инструкции, промпты, уведомления тайм-аута. это работает
    # независимо от настройки delete_previous_trials: даже если внутри
    # фазы все стимулы накапливаются, на новой фазе чат «очищается».
    if phase_idx < len(phases):
        stored_phase_idx = session.get("phase_msg_phase_idx")
        if stored_phase_idx is None:
            await repo.update_session(session_id, {
                "phase_msg_phase_idx": phase_idx,
            })
            session["phase_msg_phase_idx"] = phase_idx
        elif stored_phase_idx != phase_idx:
            old_ids = list(session.get("phase_message_ids") or [])
            if old_ids:
                await _delete_transient(bot, chat_id, old_ids)
            await repo.update_session(session_id, {
                "phase_message_ids": [],
                "phase_msg_phase_idx": phase_idx,
                # после очистки всей фазы транзиентный список тоже
                # становится бессмысленным — обнуляем, чтобы следующее
                # delete_previous-удаление не пыталось ещё раз дёргать
                # уже удалённые сообщения.
                "transient_message_ids": [],
            })
            session["phase_message_ids"] = []
            session["phase_msg_phase_idx"] = phase_idx
            session["transient_message_ids"] = []

    # если включён режим «чистить предыдущие пробы» — стираем сообщения
    # бота от предыдущей пробы/инструкции/тайм-аута перед показом новой.
    # дефолт True: эксперимент по умолчанию ведёт себя как «чистый»,
    # участник видит только текущий стимул.
    delete_previous = experiment.get("delete_previous_trials", True)
    if delete_previous:
        prev_ids = session.get("transient_message_ids", []) or []
        if prev_ids:
            await _delete_transient(bot, chat_id, prev_ids)
            await repo.update_session(session_id, {"transient_message_ids": []})

    if phase_idx >= len(phases):
        await finish_experiment(bot, chat_id, session)
        return

    phase = phases[phase_idx]
    trials = phase.get("trials", [])

    # если фаза пустая или все пробы пройдены — переходим к следующей фазе
    if trial_idx >= len(trials):
        await advance_phase(bot, chat_id, session, experiment)
        return

    # если это начало фазы и инструкция ещё не показывалась — показать её.
    # shown_instructions хранит phase_idx, для которых инструкция уже показана,
    # чтобы не зацикливаться при повторных вызовах present_trial.
    shown_instructions = session.get("shown_instructions", [])
    if (
        trial_idx == 0
        and phase.get("instruction")
        and phase_idx not in shown_instructions
    ):
        # callback_data включает phase_idx — чтобы клик по инструкции
        # старой фазы (например, скролл вверх) можно было отличить от
        # текущей и проигнорировать как stale.
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="Далее",
                callback_data=f"instr_ok_{session['_id']}_{phase_idx}",
            )]
        ])
        instr_msg = await bot.send_message(
            chat_id, phase["instruction"], reply_markup=kb
        )
        # инструкция фазы — всегда в phase_message_ids, чтобы её удалили
        # на границе со следующей фазой (даже если delete_previous=False).
        await repo.push_phase_message_id(session_id, instr_msg.message_id)
        # запоминаем id, чтобы удалить инструкцию вместе с следующим
        # переходом, если включён режим очистки.
        if delete_previous:
            await repo.update_session(session_id, {
                "transient_message_ids": [instr_msg.message_id],
            })
        # для превью эксперимента: каждое сообщение с кнопками,
        # которое мы шлём, становится «активным меню» для researcher-FSM.
        # это блокирует клики по любым более ранним меню исследователя
        # (карточки, списки и т.п.), которые остались в чате.
        if session.get("is_preview"):
            from utils.fsm_bridge import update_active_menu
            await update_active_menu(
                bot.id, chat_id, chat_id, instr_msg.message_id,
            )
        return

    trial = trials[trial_idx]

    # отправляем стимул в зависимости от типа
    stimulus_type = phase.get("stimulus_type", trial.get("stimulus_type", "text"))
    response_type = phase.get("response_type", "buttons")

    # сообщения, которые надо удалять при переходе к следующей пробе
    # (сверх основного msg). multi-image трайлы шлют ещё текст-стимул и
    # альбом картинок — без явного добавления в transient они остаются
    # в чате, и «чистить предыдущие пробы» работает наполовину.
    extra_transient_ids: list[int] = []

    # собираем клавиатуру ответов
    keyboard = build_response_keyboard(trial, phase, session_id, phase_idx, trial_idx)

    # Text Change Detection: первая стадия — text_original с единственной
    # кнопкой «Далее». Кнопки ответа из CSV приберегаются на стадию 2,
    # которую запустит process_answer (см. ветку is_text_change).
    if phase.get("settings", {}).get("is_text_change"):
        prefix = f"ans_{session_id}_{phase_idx}_{trial_idx}"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="Далее", callback_data=f"{prefix}_next",
            )]
        ])

    # Interpretation Generation: первая стадия — стимул с одной кнопкой
    # «Далее». response_type=open_text сам по себе не даёт клавиатуры —
    # без этой кнопки участник не понимает, что делать. После клика
    # process_answer (ветка is_interpretation) попросит написать
    # интерпретацию текстом.
    if phase.get("settings", {}).get("is_interpretation"):
        prefix = f"ans_{session_id}_{phase_idx}_{trial_idx}"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="Далее", callback_data=f"{prefix}_next",
            )]
        ])

    if stimulus_type == "text":
        body = trial.get("stimulus_content", "") or ""
        # AJT, режим joint_two_ratings: к стимулу приклеиваем подсказку
        # «оцените 1-е предложение». после первого клика edit_message_text
        # заменит её на «оцените 2-е предложение» (см. process_answer).
        if phase.get("settings", {}).get("joint_two_ratings"):
            body = _ajt_two_ratings_text(body, 1)
        # Telegram отвергает send_message с пустым text (Bad Request:
        # message text is empty), даже если есть reply_markup. дефолт-
        # фолбэк для случаев, когда build_phase оставил stimulus_content
        # пустым (старые maze-trials, edge-case AJT и т.п.) — это лучше,
        # чем падение всей сессии.
        if not body.strip():
            body = "…"
        msg = await bot.send_message(
            chat_id,
            body,
            reply_markup=keyboard,
        )
    elif stimulus_type == "image":
        from aiogram.types import BufferedInputFile

        meta = trial.get("stimulus_metadata", {}) or {}
        blob_ids_by_name = meta.get("blob_ids") or {}
        file_ids_by_name = meta.get("file_ids") or {}
        ordered_names = meta.get("images") or []

        # фастпас: если для имени уже есть photo file_id (Telegram
        # выдал его на предыдущем сеансе или предыдущем трайле этой
        # же сессии — мы кэшируем результат после первой отправки),
        # шлём этот id напрямую — никаких байтов через сеть. Иначе
        # подтаскиваем байты из GridFS.
        ordered_args: list[tuple[str, object, bool]] = []
        for name in ordered_names:
            cached_id = file_ids_by_name.get(name)
            if cached_id:
                ordered_args.append((name, cached_id, False))
                continue
            blob_id = blob_ids_by_name.get(name)
            if not blob_id:
                continue
            data = await repo.read_stimulus_blob(blob_id)
            if data is None:
                continue
            ordered_args.append(
                (name, BufferedInputFile(data, filename=name), True)
            )

        if len(ordered_args) >= 2:
            # multi-image трайл (picture_selection / covered_box):
            # 1) предложение-стимул отдельным сообщением (caption у
            #    media_group рисуется поверх первой картинки и легко
            #    режется по длине — выносим в текст).
            # 2) альбом картинок с подписями «1», «2» (или «3») —
            #    одна цифра соответствует одной кнопке ниже.
            # 3) клавиатура с кнопками выбора. inline-keyboard на сам
            #    media_group повесить нельзя — отдельным сообщением.
            stim_text = (trial.get("stimulus_content") or "").strip()
            if stim_text:
                text_msg = await bot.send_message(chat_id, stim_text)
                await repo.push_phase_message_id(
                    session_id, text_msg.message_id,
                )
                extra_transient_ids.append(text_msg.message_id)

            album = [
                InputMediaPhoto(media=arg, caption=str(i + 1))
                for i, (_name, arg, _was_bytes) in enumerate(ordered_args)
            ]
            try:
                album_msgs = await bot.send_media_group(chat_id, album)
            except Exception as e:
                # как правило сюда попадаем, если в meta.file_ids
                # лежит stale id (например, document id, оставшийся
                # от предыдущего билда логики). Пересобираем альбом
                # из GridFS-байт и шлём повторно — после удачи кэш
                # обновится корректным photo file_id'ом.
                logger.warning(
                    "send_media_group упал на кэшированных id (%s) — "
                    "пересобираю из GridFS-байт", e,
                )
                rebuilt = []
                for name, _arg, _was_bytes in ordered_args:
                    blob_id = blob_ids_by_name.get(name)
                    if not blob_id:
                        continue
                    data = await repo.read_stimulus_blob(blob_id)
                    if data is None:
                        continue
                    rebuilt.append(
                        (name, BufferedInputFile(data, filename=name), True)
                    )
                ordered_args = rebuilt
                album = [
                    InputMediaPhoto(media=arg, caption=str(i + 1))
                    for i, (_n, arg, _w) in enumerate(ordered_args)
                ]
                album_msgs = await bot.send_media_group(chat_id, album)

            for am in album_msgs:
                await repo.push_phase_message_id(
                    session_id, am.message_id,
                )
                extra_transient_ids.append(am.message_id)

            # кэшируем photo file_id'ы для тех картинок, которые на
            # этом трайле летели байтами — следующий показ пойдёт
            # быстрым путём.
            new_ids: dict[str, str] = {}
            for (name, _arg, was_bytes), am in zip(ordered_args, album_msgs):
                if not was_bytes:
                    continue
                pid = am.photo[-1].file_id if am.photo else None
                if pid:
                    new_ids[name] = pid
            if new_ids:
                await _cache_photo_file_ids(
                    session_id, experiment, new_ids,
                )

            msg = await bot.send_message(
                chat_id, "Выберите номер картинки:",
                reply_markup=keyboard,
            )
        else:
            # одиночная картинка (picture_naming). Тот же кэш-фастпас:
            # если есть file_id, шлём id; иначе достаём байты из GridFS
            # и кэшируем результат.
            single_name = (meta.get("img_filename") or "").strip() or None
            cached_id = (
                file_ids_by_name.get(single_name) if single_name else None
            ) or meta.get("file_id")
            msg = None
            if cached_id:
                try:
                    msg = await bot.send_photo(
                        chat_id, cached_id, reply_markup=keyboard,
                    )
                except Exception as e:
                    logger.warning(
                        "send_photo по кэшированному id упал (%s) — "
                        "пробую байтами", e,
                    )
            if msg is None:
                blob_id = meta.get("blob_id")
                data = (
                    await repo.read_stimulus_blob(blob_id) if blob_id else None
                )
                if data is None:
                    logger.error(
                        "image-стимул без blob: phase=%s trial=%s — пропускаю",
                        phase_idx, trial_idx,
                    )
                    msg = await bot.send_message(
                        chat_id, "(стимул недоступен)", reply_markup=keyboard,
                    )
                else:
                    msg = await bot.send_photo(
                        chat_id,
                        BufferedInputFile(
                            data, filename=single_name or "image",
                        ),
                        reply_markup=keyboard,
                    )
                    pid = msg.photo[-1].file_id if msg.photo else None
                    if pid and single_name:
                        await _cache_photo_file_ids(
                            session_id, experiment, {single_name: pid},
                        )
    elif stimulus_type == "audio":
        file_id = trial.get("stimulus_metadata", {}).get("file_id", "")
        # title/performer = " " — иначе Telegram выводит имя файла
        # как заголовок аудио-плеера.
        msg = await bot.send_audio(
            chat_id, file_id,
            title=" ", performer=" ",
            reply_markup=keyboard,
        )
    elif stimulus_type == "video":
        file_id = trial.get("stimulus_metadata", {}).get("file_id", "")
        msg = await bot.send_video(
            chat_id, file_id,
            reply_markup=keyboard,
        )
    else:
        msg = await bot.send_message(
            chat_id,
            trial.get("stimulus_content", ""),
            reply_markup=keyboard,
        )

    # фиксируем момент показа стимула
    _stimulus_shown_at[session_id] = time.time()

    # стимул — в phase_message_ids: при переходе к следующей фазе он
    # будет удалён независимо от delete_previous_trials.
    await repo.push_phase_message_id(session_id, msg.message_id)

    # обновляем статус сессии
    update_fields = {
        "status": "in_progress",
        "current_phase": phase_idx,
        "current_trial": trial_idx,
    }
    # для аудио-стимулов в audio-шаблонах не кладём msg в transient:
    # удаление advance-ом обрывает звук на полуслове, если участник
    # ответил быстро. вместо этого ставим отложенное удаление по
    # длительности файла (включая запеканную тишину). длительность
    # читаем из media-записи (она запоминается на upload) — это
    # надёжнее, чем msg.audio.duration: для file_id document-типа
    # Telegram возвращает msg.document, и .audio будет None.
    is_audio_template = experiment.get("template_type") in (
        "forced_choice", "sentence_repetition",
    )
    audio_auto_advance = False
    if delete_previous and is_audio_template and stimulus_type == "audio":
        exp_id_str = str(experiment.get("_id", ""))
        stim_filename = trial.get("stimulus_content", "")
        media_rec = await repo.get_media_by_filename(exp_id_str, stim_filename)
        duration_ms = int((media_rec or {}).get("duration_ms", 0) or 0)

        # 1-й fallback: ответ Telegram.
        if duration_ms <= 0 and getattr(msg, "audio", None) is not None:
            duration_ms = (msg.audio.duration or 0) * 1000

        # 2-й fallback: считаем pydub-ом из самого файла и кэшируем
        # обратно в media (для старых записей без duration_ms или для
        # document file_id, у которого msg.audio=None).
        if duration_ms <= 0 and media_rec:
            src_file_id = (
                media_rec.get("file_id") or media_rec.get("original_file_id")
            )
            if src_file_id:
                try:
                    file = await bot.download(src_file_id)
                    audio_bytes = file.read()
                    fmt = (stim_filename.rsplit(".", 1)[-1] or "mp3").lower()
                    if fmt not in ("mp3", "ogg", "wav", "m4a", "opus"):
                        fmt = "mp3"
                    duration_ms = audio_util.get_duration_ms(audio_bytes, fmt=fmt)
                    if duration_ms > 0:
                        await repo.update_media(
                            str(media_rec["_id"]),
                            {"duration_ms": duration_ms},
                        )
                except Exception as e:
                    logger.warning(
                        "не удалось вычислить длительность %s: %s",
                        stim_filename, e,
                    )

        if duration_ms > 0:
            cancel_timeout(session_id)
            task = asyncio.create_task(handle_audio_finished(
                bot, chat_id, session, experiment, duration_ms, msg.message_id,
            ))
            _timeout_tasks[session_id] = task
            audio_auto_advance = True
            # logger.info(
            #     "audio auto-advance armed: msg=%s duration_ms=%s",
            #     msg.message_id, duration_ms,
            # )
    if delete_previous:
        # сохраняем msg + дополнительные сообщения трайла в transient.
        # На next-trial обычная чистка удалит их перед показом нового
        # стимула. Если сработает таймер, он удалит главный msg сам,
        # а повторный delete_message — no-op, ошибки гасятся.
        update_fields["transient_message_ids"] = (
            extra_transient_ids + [msg.message_id]
        )
    await repo.update_session(session_id, update_fields)

    # для превью: помечаем стимул как «активное меню исследователя».
    # любые клики по более старым researcher-меню (на которые исследователь
    # мог бы случайно попасть, прокрутив чат вверх во время превью)
    # будут заблокированы StaleMenuGuard.
    # для реальных участников делать это не нужно: StaleMenuGuard на
    # participant-роутере не висит, а свои клики (ans_*) валидируются
    # отдельно по phase/trial из callback_data.
    if session.get("is_preview") and keyboard is not None:
        from utils.fsm_bridge import update_active_menu
        await update_active_menu(
            bot.id, chat_id, chat_id, msg.message_id,
        )

    # запускаем тайм-аут, если задан — для любого интерактивного типа ответа.
    # handle_timeout одинаково корректно сохраняет «пропуск» и для buttons,
    # и для open_text/voice/likert/multiple_choice; process_answer отменяет
    # таймер, если респондент успел ответить.
    # если audio_auto_advance уже занял слот таймера длительностью аудио,
    # дополнительный time_limit-таймер не запускаем — иначе они будут
    # бороться за один и тот же _timeout_tasks[session_id].
    time_limit = phase.get("time_limit") or experiment.get("time_limit")
    if (
        not audio_auto_advance
        and time_limit
        and response_type in (
            "buttons", "buttons_then_text", "open_text", "voice", "likert",
            "multiple_choice",
        )
    ):
        cancel_timeout(session_id)
        task = asyncio.create_task(
            handle_timeout(bot, chat_id, session, experiment, time_limit, msg.message_id)
        )
        _timeout_tasks[session_id] = task


async def _text_change_edit_or_resend(
    bot: Bot,
    chat_id: int,
    session_id: str,
    message_id: Optional[int],
    text: str,
    reply_markup,
    experiment: dict,
) -> Optional[int]:
    """для text_change_detection: попытаться отредактировать сообщение
    с указанным message_id, иначе отправить новое и взять его в transient.
    Возвращает message_id, на котором висит актуальный текст — он же будет
    использован для следующего edit'а.

    Все промпты (text_repeated, «напишите слово в оригинале», «напишите
    слово в повторном тексте») должны жить в ОДНОМ сообщении, чтобы
    участник не мог проскроллить вверх и подсмотреть скрытые тексты."""
    if message_id is not None:
        try:
            await bot.edit_message_text(
                text or "…",
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=reply_markup,
            )
            return message_id
        except Exception as e:
            logger.warning("text_change edit_message не удался: %s", e)
    fallback = await bot.send_message(
        chat_id, text or "…", reply_markup=reply_markup,
    )
    await repo.push_phase_message_id(session_id, fallback.message_id)
    if experiment.get("delete_previous_trials", True):
        fresh = await repo.get_session(session_id)
        ids = list((fresh or {}).get("transient_message_ids") or [])
        ids.append(fallback.message_id)
        await repo.update_session(session_id, {
            "transient_message_ids": ids,
        })
    return fallback.message_id


def _ajt_two_ratings_text(stim_content: str, rating_idx: int) -> str:
    """текст сообщения для AJT joint_two_ratings.

    к телу стимула («1) … 2) …») приклеиваем подсказку, какое
    из двух предложений сейчас оценивает участник."""
    label = "1-е предложение" if rating_idx == 1 else "2-е предложение"
    return f"{stim_content}\n\n<b>Оцените {label}</b>"


# «бюджет» одного ряда инлайн-клавиатуры в символах при равной ширине
# кнопок. на мобиле Telegram даёт каждому ряду примерно одинаковую
# ширину (≈ ширина бабла со стимулом); если max_label × n_in_row
# не влезает в бюджет — подпись режется на «...».
# подбираем количество кнопок в ряду от длины самой длинной подписи,
# а не хардкодим порог: yes-no «Да/Нет» помещается по 3, а Likert-метки
# вроде «Совсем неприемлемо» (18 симв.) автоматически уходят по одной.
_ROW_CHAR_BUDGET = 24


def _layout_button_rows(items: list) -> list:
    """разложить кнопки по рядам единообразно.

    цель — чтобы в коротких пробах (например, lexical decision со словом
    из 4 букв) кнопки тоже располагались рядом, а не друг под другом.
    инлайн-клавиатура задаёт минимальную ширину сообщения, поэтому
    «бабл» получается одинаково широким независимо от длины стимула.

    схема:
      1–3 кнопки → один ряд;
      4 → 2+2;
      5 → 3+2;
      6 → 3+3;
      7+ → ряды по 3 (последний может быть короче).
    """
    n = len(items)
    if n == 0:
        return []
    if n <= 3:
        return [items]
    if n == 4:
        return [items[:2], items[2:]]
    if n == 5:
        return [items[:3], items[3:]]
    if n == 6:
        return [items[:3], items[3:]]
    return [items[i:i + 3] for i in range(0, n, 3)]


def _layout_buttons(options: list, callback_prefix: str) -> list:
    """собрать ряды InlineKeyboardButton по эвристике layout-а.

    выбираем количество кнопок в ряду из «бюджета» символов на ряд:
    fit = budget // max_label. при fit ≥ 3 уходим в стандартный
    _layout_button_rows (3+2, 3+3 и т.п.); при fit == 2 — пары;
    при fit == 1 — каждая на своей строке. так короткие подписи не
    раскидываются по столбику без нужды, а длинные не режутся."""
    keys = [
        InlineKeyboardButton(
            text=opt, callback_data=f"{callback_prefix}_{i}",
        )
        for i, opt in enumerate(options)
    ]
    n = len(keys)
    max_len = max((len(opt) for opt in options), default=0)
    if max_len == 0:
        return _layout_button_rows(keys)
    fit_per_row = max(1, _ROW_CHAR_BUDGET // max_len)
    if fit_per_row >= 3:
        return _layout_button_rows(keys)
    if fit_per_row == 2:
        return [keys[i:i + 2] for i in range(0, n, 2)]
    return [[k] for k in keys]


def build_response_keyboard(
    trial: dict, phase: dict, session_id: str,
    phase_idx: int, trial_idx: int,
) -> Optional[InlineKeyboardMarkup]:
    """собрать клавиатуру в зависимости от типа ответа.

    callback_data формата ans_{session_id}_{phase_idx}_{trial_idx}_{option}
    позволяет хендлеру отличить клик по актуальной пробе от клика по
    стимулу прошлой фазы или прошлой пробы (после прокрутки чата вверх).
    """
    response_type = phase.get("response_type", "buttons")

    if response_type in ("open_text", "voice"):
        # для текстового и голосового ввода кнопок нет
        return None

    options = trial.get("response_options", [])
    prefix = f"ans_{session_id}_{phase_idx}_{trial_idx}"

    if response_type in ("buttons", "buttons_then_text") and options:
        rows = _layout_buttons(options, prefix)
        return InlineKeyboardMarkup(inline_keyboard=rows)

    if response_type == "likert":
        # шкала ликерта.
        # если у всех позиций подписи — просто цифры → горизонтальная шкала.
        # если хоть у одной позиции есть текстовая подпись (например,
        # «Совсем не ожидаемо») → каждая позиция в своём ряду, иначе
        # длинные подписи режутся (Telegram укорачивает текст в кнопке).
        scale = phase.get("settings", {}).get("likert_scale", 5)
        labels = phase.get("settings", {}).get("likert_labels", {})
        items = []
        any_text_label = False
        for i in range(1, scale + 1):
            label = labels.get(str(i), str(i))
            if label != str(i):
                any_text_label = True
            items.append(InlineKeyboardButton(
                text=label,
                callback_data=f"{prefix}_{i}",
            ))
        if any_text_label:
            rows = [[btn] for btn in items]
        else:
            rows = [items]
        return InlineKeyboardMarkup(inline_keyboard=rows)

    if response_type == "multiple_choice" and options:
        rows = _layout_buttons(options, prefix)
        return InlineKeyboardMarkup(inline_keyboard=rows)

    # если ничего не подходит — кнопка «Далее»
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Далее",
            callback_data=f"{prefix}_next",
        )]
    ])


# ── обработка ответа ──

async def process_answer(
    bot: Bot,
    chat_id: int,
    session: dict,
    experiment: dict,
    raw_response: str,
    option_index: Optional[int] = None,
    message_id: Optional[int] = None,
    extra_metadata: Optional[dict] = None,
):
    """обработать ответ респондента на текущую пробу.

    message_id — id сообщения, на кнопку которого был клик (нужен для
    AJT joint_two_ratings, чтобы отредактировать текст и клавиатуру под
    второе оценивание без отправки нового сообщения).

    extra_metadata — дополнительные поля, которые нужно сохранить в
    answer.metadata. Например, voice_blob_id для голосовых ответов.
    """
    session_id = str(session["_id"])
    phase_idx = session["current_phase"]
    trial_idx = session["current_trial"]
    phase = experiment["phases"][phase_idx]
    trial = phase["trials"][trial_idx]

    # отменяем тайм-аут
    cancel_timeout(session_id)

    response_type = phase.get("response_type", "buttons")
    settings = phase.get("settings", {}) or {}

    # Interpretation Generation: 2-стадийная проба.
    # Стадия 1 (pending=None): пришёл клик «Далее» по стимулу — фиксируем
    #   reading_rt_ms, шлём промпт «Запишите интерпретацию» и ставим
    #   pending_interpretation. Текст ждём от участника отдельным сообщением.
    # Стадия "awaiting_text": пришёл текст — сохраняем answer (raw_response =
    #   набранная интерпретация, RT = время ввода до отправки), чистим
    #   pending, advance_trial.
    if settings.get("is_interpretation"):
        pending_raw = session.get("pending_interpretation")
        pending = pending_raw if isinstance(pending_raw, dict) else None

        if not pending:
            # стадия 1 — клик «Далее»
            if raw_response != "_next_":
                # для интерпретации до клика «Далее» текст не принимаем —
                # участник мог опередить и ответить голосом/текстом раньше.
                return
            shown_at = _stimulus_shown_at.pop(session_id, None)
            reading_rt = (
                int((time.time() - shown_at) * 1000) if shown_at else None
            )
            # для interpretation_generation стимул удаляем сразу после
            # клика «Далее» — независимо от delete_previous_trials. так
            # участник сосредоточен на промпте и не подсматривает
            # предложение, формулируя интерпретацию.
            if message_id is not None:
                try:
                    await bot.delete_message(chat_id, message_id)
                except Exception:
                    pass
            prompt_msg = await bot.send_message(
                chat_id,
                "Запишите, как вы понимаете смысл этого предложения:",
            )
            await repo.push_phase_message_id(session_id, prompt_msg.message_id)
            # transient очищаем (стимула там больше нет — мы его удалили)
            # и кладём туда промпт, чтобы present_trial следующей пробы
            # его прибрал (если включена очистка). прямой delete_message
            # промпта на стадии 2 — ниже, безусловно.
            await repo.update_session(session_id, {
                "transient_message_ids": [prompt_msg.message_id],
                "pending_interpretation": {
                    "stage": "awaiting_text",
                    "reading_rt_ms": reading_rt,
                    "prompt_msg_id": prompt_msg.message_id,
                },
            })
            # старт отсчёта RT для текстового ввода
            _stimulus_shown_at[session_id] = time.time()
            return

        # стадия 2 — пришёл текст с интерпретацией
        shown_at = _stimulus_shown_at.pop(session_id, None)
        write_rt = int((time.time() - shown_at) * 1000) if shown_at else None
        # удаляем промпт «Запишите…» — пробу полностью «закрываем» в чате,
        # независимо от delete_previous_trials. ответ участника удалить
        # нельзя (Telegram не даёт боту удалять чужие сообщения в личке),
        # он останется в чате как запись его интерпретации.
        prompt_msg_id = pending.get("prompt_msg_id")
        if prompt_msg_id is not None:
            try:
                await bot.delete_message(chat_id, prompt_msg_id)
            except Exception:
                pass
        answer_data = {
            "session_id": session_id,
            "experiment_id": session["experiment_id"],
            "phase_index": phase_idx,
            "trial_index": trial_idx,
            "stimulus_id": trial.get("stimulus_content", ""),
            "raw_response": raw_response,
            "normalized_response": raw_response.strip(),
            "is_correct": None,
            "reaction_time_ms": write_rt,
            "timed_out": False,
            "timestamp": datetime.utcnow(),
            "metadata": {
                "list_id": session.get("assigned_list"),
                "reading_rt_ms": pending.get("reading_rt_ms"),
            },
        }
        await repo.save_answer(answer_data)
        await repo.update_session(session_id, {
            "pending_interpretation": None,
            "transient_message_ids": [],
        })
        fresh = await repo.get_session(session_id) or session
        await advance_trial(bot, chat_id, fresh, experiment)
        return

    # Text Change Detection: проба до 4-х стадий.
    # Стадия 1 (стартовая, pending=None): показан text_original с одной
    #   кнопкой «Далее». Клик → reading_rt_ms, edit_message → text_repeated
    #   с answer-кнопками.
    # Стадия "decide": клик по answer-кнопке (decision_rt_ms). Если
    #   участник выбрал «было изменение» (opt1 по конвенции CSV), бот
    #   спрашивает оригинальное слово. Иначе — финализируем answer.
    # Стадия "ask_original": текстовый ответ с оригинальным словом →
    #   запрашиваем слово в повторном тексте.
    # Стадия "ask_new": текстовый ответ с новым словом → финализируем
    #   answer со всеми RT и пользовательскими словами.
    if settings.get("is_text_change"):
        pending_raw = session.get("pending_text_change")
        pending = pending_raw if isinstance(pending_raw, dict) else {}
        stage = pending.get("stage")
        meta = trial.get("stimulus_metadata") or {}

        # стадия 1 — клик «Далее» по text_original
        if not stage:
            shown_at = _stimulus_shown_at.pop(session_id, None)
            reading_rt = (
                int((time.time() - shown_at) * 1000) if shown_at else None
            )
            text_repeated = (
                meta.get("text_repeated") or trial.get("stimulus_content", "")
            ) or "…"
            kb = build_response_keyboard(
                trial, phase, session_id, phase_idx, trial_idx,
            )
            edit_msg_id = await _text_change_edit_or_resend(
                bot, chat_id, session_id, message_id, text_repeated, kb,
                experiment,
            )
            await repo.update_session(session_id, {
                "pending_text_change": {
                    "reading_rt_ms": reading_rt,
                    "stage": "decide",
                    "message_id": edit_msg_id,
                },
            })
            _stimulus_shown_at[session_id] = time.time()
            return

        # стадия 2 — клик по answer-кнопке
        if stage == "decide":
            shown_at = _stimulus_shown_at.pop(session_id, None)
            decision_rt = (
                int((time.time() - shown_at) * 1000) if shown_at else None
            )
            normalized = raw_response.strip().lower()
            correct_answer = trial.get("correct_answer")
            is_correct = None
            if correct_answer is not None:
                ca = str(correct_answer).strip().lower()
                is_correct = normalized == ca
            change_label = (meta.get("is_change_label") or "").strip().lower()
            user_says_change = bool(change_label) and normalized == change_label

            if not user_says_change:
                # «изменения не было» — финализируем сразу, без вопросов
                answer_data = {
                    "session_id": session_id,
                    "experiment_id": session["experiment_id"],
                    "phase_index": phase_idx,
                    "trial_index": trial_idx,
                    "stimulus_id": trial.get("stimulus_content", ""),
                    "raw_response": raw_response,
                    "normalized_response": normalized,
                    "is_correct": is_correct,
                    "reaction_time_ms": decision_rt,
                    "timed_out": False,
                    "timestamp": datetime.utcnow(),
                    "metadata": {
                        "list_id": session.get("assigned_list"),
                        "reading_rt_ms": pending.get("reading_rt_ms"),
                        "user_change_original": "",
                        "user_change_new": "",
                    },
                }
                await repo.save_answer(answer_data)
                await repo.update_session(session_id, {
                    "pending_text_change": None,
                })
                fresh = await repo.get_session(session_id) or session
                await advance_trial(bot, chat_id, fresh, experiment)
                return

            # «изменение было» — спрашиваем оригинальное слово.
            # Редактируем тот же мессадж: убираем кнопки и заменяем
            # текст на промпт. Так участник не сможет проскроллить и
            # «подсмотреть» оригинальный/повторный текст.
            edit_target = pending.get("message_id") or message_id
            edit_msg_id = await _text_change_edit_or_resend(
                bot, chat_id, session_id, edit_target,
                "Напишите слово, которое было в оригинале:", None,
                experiment,
            )
            await repo.update_session(session_id, {
                "pending_text_change": {
                    "stage": "ask_original",
                    "reading_rt_ms": pending.get("reading_rt_ms"),
                    "decision_rt_ms": decision_rt,
                    "raw_response": raw_response,
                    "normalized_response": normalized,
                    "is_correct": is_correct,
                    "message_id": edit_msg_id,
                },
            })
            return

        # стадия 3 — пришёл текст с оригинальным словом
        if stage == "ask_original":
            edit_msg_id = await _text_change_edit_or_resend(
                bot, chat_id, session_id, pending.get("message_id"),
                "Напишите слово в повторном тексте:", None,
                experiment,
            )
            new_pending = dict(pending)
            new_pending["user_change_original"] = raw_response
            new_pending["stage"] = "ask_new"
            new_pending["message_id"] = edit_msg_id
            await repo.update_session(session_id, {
                "pending_text_change": new_pending,
            })
            return

        # стадия 4 — пришёл текст с новым словом, финализируем
        if stage == "ask_new":
            answer_data = {
                "session_id": session_id,
                "experiment_id": session["experiment_id"],
                "phase_index": phase_idx,
                "trial_index": trial_idx,
                "stimulus_id": trial.get("stimulus_content", ""),
                "raw_response": pending.get("raw_response", ""),
                "normalized_response": pending.get("normalized_response", ""),
                "is_correct": pending.get("is_correct"),
                "reaction_time_ms": pending.get("decision_rt_ms"),
                "timed_out": False,
                "timestamp": datetime.utcnow(),
                "metadata": {
                    "list_id": session.get("assigned_list"),
                    "reading_rt_ms": pending.get("reading_rt_ms"),
                    "user_change_original": pending.get(
                        "user_change_original", ""
                    ),
                    "user_change_new": raw_response,
                },
            }
            await repo.save_answer(answer_data)
            await repo.update_session(session_id, {
                "pending_text_change": None,
            })
            fresh = await repo.get_session(session_id) or session
            await advance_trial(bot, chat_id, fresh, experiment)
            return

    # AJT joint_two_ratings: один CSV-row → одна проба → ДВА клика по одному
    # сообщению. при первом клике сохраняем оценку первого предложения в
    # pending_first_rating и редактируем сообщение под второе оценивание.
    # при втором клике сохраняем оба ответа двумя записями (rating_target
    # = stimulus / stimulus2) и переходим к следующей пробе.
    if settings.get("joint_two_ratings"):
        pending = session.get("pending_first_rating")
        normalized = raw_response.strip().lower()
        correct_answer = trial.get("correct_answer")
        is_correct = None
        if correct_answer is not None:
            ca = str(correct_answer).strip().lower()
            is_correct = normalized == ca

        if not pending:
            shown_at = _stimulus_shown_at.pop(session_id, None)
            rt_ms = int((time.time() - shown_at) * 1000) if shown_at else None
            await repo.update_session(session_id, {
                "pending_first_rating": {
                    "raw_response": raw_response,
                    "normalized_response": normalized,
                    "is_correct": is_correct,
                    "reaction_time_ms": rt_ms,
                    "option_index": option_index,
                },
            })
            # редактируем сообщение: тот же текст стимула, новая подсказка
            new_text = _ajt_two_ratings_text(trial.get("stimulus_content", ""), 2)
            new_kb = build_response_keyboard(
                trial, phase, session_id, phase_idx, trial_idx,
            )
            if message_id is not None:
                try:
                    await bot.edit_message_text(
                        new_text,
                        chat_id=chat_id,
                        message_id=message_id,
                        reply_markup=new_kb,
                    )
                except Exception as e:
                    # сообщение могло быть удалено / слишком старое и т.п. —
                    # фолбэк: шлём новое сообщение со 2-й оценкой
                    logger.warning("edit_message не удался: %s", e)
                    fallback = await bot.send_message(
                        chat_id, new_text, reply_markup=new_kb,
                    )
                    await repo.push_phase_message_id(
                        session_id, fallback.message_id,
                    )
                    if experiment.get("delete_previous_trials", True):
                        fresh = await repo.get_session(session_id)
                        ids = list((fresh or {}).get("transient_message_ids") or [])
                        ids.append(fallback.message_id)
                        await repo.update_session(session_id, {
                            "transient_message_ids": ids,
                        })
            # начинаем отсчёт RT для второй оценки
            _stimulus_shown_at[session_id] = time.time()
            return

        # второй клик — собираем 2 answer-записи на одну пробу
        shown_at = _stimulus_shown_at.pop(session_id, None)
        rt2 = int((time.time() - shown_at) * 1000) if shown_at else None
        list_id = session.get("assigned_list")
        first_answer = {
            "session_id": session_id,
            "experiment_id": session["experiment_id"],
            "phase_index": phase_idx,
            "trial_index": trial_idx,
            "stimulus_id": trial.get("auxiliary", {}).get(
                "_stimulus_raw"
            ) or trial.get("stimulus_content", ""),
            "raw_response": pending.get("raw_response", ""),
            "normalized_response": pending.get("normalized_response", ""),
            "is_correct": pending.get("is_correct"),
            "reaction_time_ms": pending.get("reaction_time_ms"),
            "timed_out": False,
            "timestamp": datetime.utcnow(),
            "metadata": {"list_id": list_id, "rating_target": "stimulus"},
        }
        second_answer = {
            "session_id": session_id,
            "experiment_id": session["experiment_id"],
            "phase_index": phase_idx,
            "trial_index": trial_idx,
            "stimulus_id": trial.get("auxiliary", {}).get(
                "stimulus2"
            ) or trial.get("stimulus_content", ""),
            "raw_response": raw_response,
            "normalized_response": normalized,
            "is_correct": is_correct,
            "reaction_time_ms": rt2,
            "timed_out": False,
            "timestamp": datetime.utcnow(),
            "metadata": {"list_id": list_id, "rating_target": "stimulus2"},
        }
        await repo.save_answer(first_answer)
        await repo.save_answer(second_answer)
        await repo.update_session(session_id, {"pending_first_rating": None})
        fresh = await repo.get_session(session_id) or session
        await advance_trial(bot, chat_id, fresh, experiment)
        return

    # двухшаговый ответ: сначала кнопка (правильно/неправильно),
    # затем текстовое обоснование. первый шаг — клик: фиксируем выбор
    # в pending_judgment и просим обосновать. второй шаг — текст:
    # собираем итоговый answer и переходим к следующей пробе.
    # запрос обоснования включается ПОПРОБНО через CSV-колонку
    # ask_justification ("да"/"нет"). если значение не "да" — клик
    # сразу завершает пробу, без второго шага.
    if response_type == "buttons_then_text":
        pending = session.get("pending_judgment")
        if not pending:
            # первый шаг — пришла кнопка
            shown_at = _stimulus_shown_at.pop(session_id, None)
            rt_ms = int((time.time() - shown_at) * 1000) if shown_at else None
            normalized = raw_response.strip().lower()
            correct_answer = trial.get("correct_answer")
            is_correct = None
            if correct_answer is not None:
                if isinstance(correct_answer, list):
                    is_correct = normalized in [
                        a.strip().lower() for a in correct_answer
                    ]
                else:
                    is_correct = normalized == str(correct_answer).strip().lower()

            ask = str(
                (trial.get("auxiliary") or {}).get("ask_justification") or ""
            ).strip().lower()
            wants_justification = ask in ("да", "yes", "y", "true", "1")

            if not wants_justification:
                # обоснование не нужно — пишем ответ и идём дальше.
                answer_data = {
                    "session_id": session_id,
                    "experiment_id": session["experiment_id"],
                    "phase_index": phase_idx,
                    "trial_index": trial_idx,
                    "stimulus_id": trial.get("stimulus_content", ""),
                    "raw_response": raw_response,
                    "normalized_response": normalized,
                    "is_correct": is_correct,
                    "reaction_time_ms": rt_ms,
                    "timed_out": False,
                    "timestamp": datetime.utcnow(),
                    "metadata": {"list_id": session.get("assigned_list")},
                }
                await repo.save_answer(answer_data)
                await advance_trial(bot, chat_id, session, experiment)
                return

            await repo.update_session(session_id, {
                "pending_judgment": {
                    "raw_response": raw_response,
                    "normalized_response": normalized,
                    "is_correct": is_correct,
                    "reaction_time_ms": rt_ms,
                    "option_index": option_index,
                },
            })
            prompt_msg = await bot.send_message(
                chat_id,
                "Опишите коротко, почему вы так решили:",
            )
            await repo.push_phase_message_id(session_id, prompt_msg.message_id)
            # держим приглашение в transient, чтобы оно удалилось вместе
            # с подтверждением выбора при переходе к следующей пробе
            if experiment.get("delete_previous_trials", True):
                fresh = await repo.get_session(session_id)
                ids = list((fresh or {}).get("transient_message_ids") or [])
                ids.append(prompt_msg.message_id)
                await repo.update_session(session_id, {
                    "transient_message_ids": ids,
                })
            return

        # второй шаг — пришёл текст с обоснованием
        metadata = {
            "list_id": session.get("assigned_list"),
            "justification": raw_response,
        }
        answer_data = {
            "session_id": session_id,
            "experiment_id": session["experiment_id"],
            "phase_index": phase_idx,
            "trial_index": trial_idx,
            "stimulus_id": trial.get("stimulus_content", ""),
            "raw_response": pending.get("raw_response", ""),
            "normalized_response": pending.get("normalized_response", ""),
            "is_correct": pending.get("is_correct"),
            "reaction_time_ms": pending.get("reaction_time_ms"),
            "timed_out": False,
            "timestamp": datetime.utcnow(),
            "metadata": metadata,
        }
        await repo.save_answer(answer_data)
        await repo.update_session(session_id, {"pending_judgment": None})
        fresh = await repo.get_session(session_id) or session
        await advance_trial(bot, chat_id, fresh, experiment)
        return

    # считаем RT для всех интерактивных типов — для open_text/voice это
    # тоже полезно (когда задан тайм-аут или просто для анализа задержки)
    rt_ms = None
    if response_type in ("buttons", "likert", "multiple_choice", "open_text", "voice"):
        shown_at = _stimulus_shown_at.pop(session_id, None)
        if shown_at:
            rt_ms = int((time.time() - shown_at) * 1000)

    # нормализуем ответ
    normalized = raw_response.strip().lower()

    # проверяем корректность
    correct_answer = trial.get("correct_answer")
    is_correct = None
    if correct_answer is not None:
        if isinstance(correct_answer, list):
            is_correct = normalized in [a.strip().lower() for a in correct_answer]
        else:
            is_correct = normalized == str(correct_answer).strip().lower()

    # сохраняем ответ
    # для buttons option_index — это позиция нажатой кнопки, которая
    # после рандомизации ничего не значит и может ввести в заблуждение
    # при анализе. сохраняем только текст нажатой кнопки (raw_response).
    metadata = {"list_id": session.get("assigned_list")}
    if response_type == "likert":
        # для likert числовое значение = сам ответ, raw_response уже строкa
        metadata["option_index"] = option_index
    if extra_metadata:
        metadata.update(extra_metadata)
    answer_data = {
        "session_id": session_id,
        "experiment_id": session["experiment_id"],
        "phase_index": phase_idx,
        "trial_index": trial_idx,
        "stimulus_id": trial.get("stimulus_content", ""),
        "raw_response": raw_response,
        "normalized_response": normalized,
        "is_correct": is_correct,
        "reaction_time_ms": rt_ms,
        "timed_out": False,
        "timestamp": datetime.utcnow(),
        "metadata": metadata,
    }
    await repo.save_answer(answer_data)

    # maze: при неправильном выборе пропускаем оставшиеся слова текущего
    # предложения и сразу прыгаем к началу следующего. предложения
    # помечены trial.stimulus_metadata.sentence_idx (см. build_maze).
    if (
        phase.get("settings", {}).get("is_maze")
        and is_correct is False
    ):
        await _maze_jump_to_next_sentence(
            bot, chat_id, session, experiment, trial_idx,
        )
        return

    # переходим к следующей пробе
    await advance_trial(bot, chat_id, session, experiment)


async def _maze_jump_to_next_sentence(
    bot: Bot, chat_id: int, session: dict, experiment: dict,
    current_trial_idx: int,
):
    """найти первую пробу следующего предложения и встать на неё.
    если предложений больше нет — переходим к следующей фазе/завершаем."""
    session_id = str(session["_id"])
    phase_idx = session["current_phase"]
    phase = experiment["phases"][phase_idx]
    trials = phase.get("trials", []) or []
    if current_trial_idx >= len(trials):
        await advance_phase(bot, chat_id, session, experiment)
        return
    current_sid = (
        trials[current_trial_idx].get("stimulus_metadata", {}) or {}
    ).get("sentence_idx")
    target_idx: Optional[int] = None
    for i in range(current_trial_idx + 1, len(trials)):
        sid = (trials[i].get("stimulus_metadata", {}) or {}).get("sentence_idx")
        if sid is not None and sid != current_sid:
            target_idx = i
            break
    if target_idx is None:
        await advance_phase(bot, chat_id, session, experiment)
        return
    await repo.update_session(session_id, {"current_trial": target_idx})
    fresh = await repo.get_session(session_id) or session
    await present_trial(bot, chat_id, fresh, experiment)


# ── тайм-аут ──

async def handle_audio_finished(
    bot: Bot, chat_id: int, session: dict, experiment: dict,
    duration_ms: int, message_id: int,
):
    """для audio-шаблонов с delete_previous=True: подождать, пока файл
    отыграет, удалить сообщение со стимулом и автоматически перейти
    к следующей пробе. если участник успел нажать кнопку раньше, его
    process_answer вызывает cancel_timeout — наш await sleep ловит
    CancelledError и выходит, не вмешиваясь."""
    session_id = str(session["_id"])
    try:
        await asyncio.sleep(duration_ms / 1000 + 0.5)
    except asyncio.CancelledError:
        return

    _timeout_tasks.pop(session_id, None)

    # удаляем сообщение со стимулом (best effort).
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass

    # проверяем, что сессия всё ещё на той пробе, для которой был запланирован
    # таймер — иначе advance был сделан кем-то ещё (например параллельным
    # хендлером ответа), и второй advance сломает прогресс.
    fresh = await repo.get_session(session_id)
    if not fresh:
        return
    if (
        fresh.get("current_phase") != session["current_phase"]
        or fresh.get("current_trial") != session["current_trial"]
    ):
        return
    if fresh.get("status") == "completed":
        return

    phase_idx = session["current_phase"]
    trial_idx = session["current_trial"]
    phase = experiment["phases"][phase_idx]
    trial = phase["trials"][trial_idx]

    _stimulus_shown_at.pop(session_id, None)

    # участник не успел ответить — сохраняем «пустой» ответ как timed_out
    answer_data = {
        "session_id": session_id,
        "experiment_id": session["experiment_id"],
        "phase_index": phase_idx,
        "trial_index": trial_idx,
        "stimulus_id": trial.get("stimulus_content", ""),
        "raw_response": "",
        "normalized_response": "",
        "is_correct": None,
        "reaction_time_ms": duration_ms,
        "timed_out": True,
        "timestamp": datetime.utcnow(),
        "metadata": {"list_id": fresh.get("assigned_list")},
    }
    await repo.save_answer(answer_data)
    await advance_trial(bot, chat_id, fresh, experiment)


async def handle_timeout(
    bot: Bot, chat_id: int, session: dict, experiment: dict,
    time_limit: int, message_id: int,
):
    """обработка тайм-аута: ждем time_limit секунд, потом записываем пропуск"""
    session_id = str(session["_id"])
    try:
        await asyncio.sleep(time_limit)
    except asyncio.CancelledError:
        return

    # тайм-аут сработал. убираем себя из реестра до того, как пойдём
    # в advance_trial: иначе на последней пробе цепочка дойдёт до
    # finish_experiment, которая дёрнет cancel_timeout — и отменит
    # нашу же текущую задачу. следующий await поднимет CancelledError,
    # и «Эксперимент завершен» не успеет отправиться.
    _timeout_tasks.pop(session_id, None)

    # тайм-аут сработал
    phase_idx = session["current_phase"]
    trial_idx = session["current_trial"]
    phase = experiment["phases"][phase_idx]
    trial = phase["trials"][trial_idx]

    _stimulus_shown_at.pop(session_id, None)

    # сохраняем ответ с пометкой тайм-аута
    answer_data = {
        "session_id": session_id,
        "experiment_id": session["experiment_id"],
        "phase_index": phase_idx,
        "trial_index": trial_idx,
        "stimulus_id": trial.get("stimulus_content", ""),
        "raw_response": "",
        "normalized_response": "",
        "is_correct": None,
        "reaction_time_ms": time_limit * 1000,
        "timed_out": True,
        "timestamp": datetime.utcnow(),
        "metadata": {"list_id": session.get("assigned_list")},
    }
    await repo.save_answer(answer_data)

    timeout_msg = await bot.send_message(chat_id, "Время вышло")
    await repo.push_phase_message_id(session_id, timeout_msg.message_id)

    # обновляем сессию и идем дальше; если включена очистка — добавляем
    # «Время вышло» к списку transient, чтобы следующий present_trial
    # удалил и стимул, и это уведомление.
    fresh_session = await repo.get_session(session_id)
    if fresh_session:
        if experiment.get("delete_previous_trials", True):
            ids = list(fresh_session.get("transient_message_ids") or [])
            ids.append(timeout_msg.message_id)
            await repo.update_session(session_id, {
                "transient_message_ids": ids,
            })
            fresh_session = await repo.get_session(session_id)
        await advance_trial(bot, chat_id, fresh_session, experiment)


def cancel_timeout(session_id: str):
    """отменить активный тайм-аут для сессии"""
    task = _timeout_tasks.pop(session_id, None)
    if task and not task.done():
        task.cancel()


# ── навигация по пробам и фазам ──

async def advance_trial(bot: Bot, chat_id: int, session: dict, experiment: dict):
    """перейти к следующей пробе"""
    session_id = str(session["_id"])
    phase_idx = session["current_phase"]
    trial_idx = session["current_trial"] + 1
    phase = experiment["phases"][phase_idx]

    if trial_idx >= len(phase.get("trials", [])):
        # фаза закончена
        await advance_phase(bot, chat_id, session, experiment)
    else:
        await repo.update_session(session_id, {"current_trial": trial_idx})
        updated = await repo.get_session(session_id)
        await present_trial(bot, chat_id, updated, experiment)


async def advance_phase(bot: Bot, chat_id: int, session: dict, experiment: dict):
    """перейти к следующей фазе.

    проксируем через present_trial: даже если все фазы кончились,
    present_trial поймает phase_idx >= len(phases) и вызовет
    finish_experiment, успев перед этим удалить предыдущие сообщения
    (если включена опция «чистить предыдущие пробы»).
    """
    session_id = str(session["_id"])
    next_phase = session["current_phase"] + 1
    await repo.update_session(session_id, {
        "current_phase": next_phase,
        "current_trial": 0,
    })
    updated = await repo.get_session(session_id)
    await present_trial(bot, chat_id, updated, experiment)


async def finish_experiment(bot: Bot, chat_id: int, session: dict):
    """завершить эксперимент"""
    session_id = str(session["_id"])
    cancel_timeout(session_id)
    _stimulus_shown_at.pop(session_id, None)

    # атомарный гард от двойного завершения: если участник нажал кнопку
    # последней пробы дважды (или тайм-аут совпал с ответом), оба
    # параллельных коллбэка добегают сюда. модифицируем сессию только
    # если она ещё не completed — проигравший коллбэк просто выходит,
    # не дублируя «эксперимент завершён».
    if not await repo.mark_session_completed(session_id):
        return
    logger.info("сессия %s завершена", session_id)

    if session.get("is_preview"):
        await bot.send_message(
            chat_id,
            "✅ Превью завершено. Это был просмотр эксперимента в роли участника — "
            "ответы не сохраняются в результатах."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Создать эксперимент", callback_data="create_experiment")],
            [InlineKeyboardButton(text="Мои эксперименты", callback_data="my_experiments")],
            [InlineKeyboardButton(text="Результаты", callback_data="results_menu")],
            [InlineKeyboardButton(text="Рассылка участникам", callback_data="promo_menu")],
        ])
        menu_msg = await bot.send_message(chat_id, "Главное меню:", reply_markup=kb)
        # фиксируем это меню как «текущий активный экран» в FSM —
        # без этого StaleMenuGuard будет пропускать клики по любым
        # старым меню в чате (т.к. on_preview сбросил active_menu_msg_id),
        # и пользователь сможет случайно «оживить» устаревшее меню.
        from utils.fsm_bridge import update_active_menu
        await update_active_menu(
            bot.id, chat_id, chat_id, menu_msg.message_id,
        )
    else:
        await bot.send_message(
            chat_id,
            "Эксперимент завершен. Спасибо за участие!"
        )


# ── рандомизация ──

def randomize_trials(trials: list, seed: Optional[int] = None) -> list:
    """перемешать пробы, сохраняя оригинальные индексы"""
    shuffled = list(trials)
    if seed is not None:
        random.Random(seed).shuffle(shuffled)
    else:
        random.shuffle(shuffled)
    return shuffled


async def _cache_photo_file_ids(
    session_id: str, experiment: dict, name_to_photo: dict[str, str],
) -> None:
    """запомнить photo file_id'ы, полученные после первого реального
    upload'а байтов. Пишем в три места:

    1) media-коллекция — пер-эксперимент cross-session кэш, видим всем
       будущим сессиям ещё на этапе attach_media_ids_to_phases;
    2) experiment.phases — чтобы новые сессии стартовали уже с
       проставленными photo file_id'ами (а не с теми document id, что
       могли остаться от старого билда);
    3) session.prepared_phases — чтобы следующий трайл уже текущей
       сессии пошёл фастпасом, не дожидаясь нового запуска.
    """
    if not name_to_photo:
        return

    exp_id = str(experiment.get("_id", ""))
    if exp_id:
        for name, pid in name_to_photo.items():
            try:
                await repo.set_media_photo_id(exp_id, name, pid)
            except Exception:
                logger.exception(
                    "set_media_photo_id упал для %s", name,
                )

    phases = experiment.get("phases", [])
    for phase in phases:
        if phase.get("stimulus_type") != "image":
            continue
        for trial in phase.get("trials", []):
            meta = trial.get("stimulus_metadata") or {}
            blobs = meta.get("blob_ids") or {}
            single_name = meta.get("img_filename") or ""
            fids = meta.setdefault("file_ids", {})
            for name, pid in name_to_photo.items():
                if name in blobs or name == single_name:
                    fids[name] = pid
            if single_name in name_to_photo:
                meta["file_id"] = name_to_photo[single_name]
            trial["stimulus_metadata"] = meta

    try:
        await repo.update_session(
            session_id, {"prepared_phases": phases},
        )
    except Exception:
        logger.exception(
            "не удалось сохранить prepared_phases с кэшированным "
            "photo file_id для сессии %s", session_id,
        )

    # пер-эксперимент: дёргаем стандартный attach по media-коллекции,
    # она уже обновлена set_media_photo_id выше. attach перечитает
    # эталонные experiment.phases из БД (со ВСЕМИ листами) и проставит
    # туда свежие photo id — без риска потерять трайлы чужого листа,
    # которые в session.prepared_phases отфильтрованы.
    if exp_id:
        try:
            from handlers.media_upload import attach_media_file_ids
            await attach_media_file_ids(exp_id)
        except Exception:
            logger.exception(
                "не удалось пере-аттачить media id'ы в experiment.phases (%s)",
                exp_id,
            )


def filter_trials_by_list(trials: list, list_id: str) -> list:
    """оставить только пробы, принадлежащие заданному листу (или без листа)"""
    return [t for t in trials if t.get("list_id") in (list_id, None)]


def prepare_trials_for_session(
    phase: dict,
    assigned_list: Optional[str],
    randomize_button_positions: bool = False,
) -> list:
    """подготовить список проб для конкретной сессии: фильтр по листу + рандомизация.

    если randomize_button_positions=True и фаза использует кнопочный ответ,
    в каждой пробе порядок response_options тасуется индивидуально.
    это снимает carry-over эффект курсора при измерении RT.
    """
    trials = list(phase.get("trials", []))

    # фильтрация по листу
    if assigned_list:
        trials = filter_trials_by_list(trials, assigned_list)

    # рандомизация порядка проб
    if phase.get("randomize_order", False):
        trials = randomize_trials(trials)

    # рандомизация позиций кнопок ответа внутри каждой пробы
    response_type = phase.get("response_type", "buttons")
    settings = phase.get("settings", {}) or {}
    # maze сам мешает target/distractor на этапе build_phase — не дублируем
    is_maze = bool(settings.get("is_maze"))
    if (
        randomize_button_positions
        and response_type == "buttons"
        and not is_maze
    ):
        rebuilt = []
        for t in trials:
            options = t.get("response_options") or []
            if len(options) >= 2:
                # копируем пробу и тасуем опции, не трогая исходник
                # (он шарится между сессиями).
                t_copy = dict(t)
                shuffled = list(options)
                random.shuffle(shuffled)
                t_copy["response_options"] = shuffled
                rebuilt.append(t_copy)
            else:
                rebuilt.append(t)
        trials = rebuilt

    # рандомизация позиций картинок в пробе (picture_selection /
    # covered_box). Тасуем именно здесь, на старте сессии, а не в
    # build_phase: тогда у каждого участника свой случайный порядок,
    # а в эталонных experiment.phases всегда лежит «как в CSV».
    # correct_answer пересчитывать не нужно — там лежит имя файла,
    # инвариантное к перестановке позиций.
    if phase.get("randomize_image_positions", False):
        rebuilt = []
        for t in trials:
            meta = t.get("stimulus_metadata") or {}
            images = list(meta.get("images") or [])
            if len(images) >= 2:
                t_copy = dict(t)
                new_meta = dict(meta)
                shuffled = list(images)
                random.shuffle(shuffled)
                new_meta["images"] = shuffled
                for i, name in enumerate(shuffled):
                    new_meta[f"img_{i + 1}"] = name
                t_copy["stimulus_metadata"] = new_meta
                rebuilt.append(t_copy)
            else:
                rebuilt.append(t)
        trials = rebuilt

    return trials
