"""ручной диспетчер текстового ввода в режиме настроек.

В режиме `configuring` любой текст летит в `on_config_text` — он смотрит
на флаги `waiting_*` в state.data (их выставляют ask_*-хендлеры в
researcher_settings, on_button_edit / on_likert_label_edit в
researcher_customization, on_instruction_edit / on_cfg_description в
researcher_instructions) и решает, к какому полю отнести введённое
значение."""

from aiogram import types, F
from aiogram.fsm.context import FSMContext

from handlers.researcher_common import (
    router,
    CreateExperiment,
)
from handlers.researcher_customization import (
    _get_current_buttons,
    _send_buttons_submenu,
    _send_likert_submenu,
)
from handlers.researcher_instructions import _send_instructions_submenu
from handlers.researcher_settings import show_config_menu, show_settings_submenu


@router.message(CreateExperiment.configuring, F.text)
async def on_config_text(message: types.Message, state: FSMContext):
    """обработка текстового ввода в режиме настроек (тайм-аут, подписи)"""
    data = await state.get_data()
    text = message.text.strip()

    # тайм-аут
    if data.get("waiting_timeout"):
        try:
            val = int(text)
            await state.update_data(
                time_limit=val if val > 0 else None,
                waiting_timeout=False,
            )
        except ValueError:
            await message.answer("Введите целое число.")
            return
        await show_settings_submenu(message, state)
        return

    # тишина в аудио (audio_silence_seconds)
    if data.get("waiting_audio_silence"):
        try:
            val = int(text)
            if val < 0:
                raise ValueError
        except ValueError:
            await message.answer("Введите неотрицательное целое число.")
            return
        await state.update_data(
            audio_silence_seconds=val,
            waiting_audio_silence=False,
        )
        await show_settings_submenu(message, state)
        return

    # перерыв до сброса (idle timeout)
    if data.get("waiting_idle_timeout"):
        try:
            val = int(text)
            if val < 0:
                raise ValueError
        except ValueError:
            await message.answer("Введите неотрицательное целое число.")
            return
        await state.update_data(
            idle_timeout_seconds=val,
            waiting_idle_timeout=False,
        )
        await show_settings_submenu(message, state)
        return

    # количество листов
    if data.get("waiting_lists_count"):
        try:
            val = int(text)
        except ValueError:
            await message.answer("Введите целое число.")
            return
        if val < 1 or val > 20:
            await message.answer("Число листов должно быть от 1 до 20.")
            return
        # удаляем CSV-слоты, которые перестали существовать при новом lists_count
        csv_data = dict(data.get("csv_data") or {})
        pruned = {
            k: v for k, v in csv_data.items()
            if (k.split("_") + ["", ""])[1].isdigit()
            and 1 <= int(k.split("_")[1]) <= val
        }
        await state.update_data(
            lists_count=val,
            use_lists=val >= 2,
            csv_data=pruned,
            waiting_lists_count=False,
        )
        await show_settings_submenu(message, state)
        return

    # редактирование метки кнопки
    btn_req = data.get("waiting_button_edit")
    if btn_req:
        key = btn_req.get("key", "main")
        idx = btn_req.get("index", -1)
        tmpl_code = data.get("template_type", "")
        current = _get_current_buttons(data, tmpl_code, key)
        if 0 <= idx < len(current):
            if len(text) > 64:
                await message.answer(
                    "Слишком длинная метка (Telegram ограничивает ~64 символа)."
                )
                return
            current[idx] = text
            custom = dict(data.get("custom_buttons") or {})
            custom[key] = current
            await state.update_data(
                custom_buttons=custom, waiting_button_edit=None,
            )
            await message.answer(f"✅ Кнопка №{idx + 1} обновлена: «{text}»")
        else:
            await state.update_data(waiting_button_edit=None)
        # возвращаем подменю кнопок
        await _send_buttons_submenu(message, state)
        return

    # редактирование инструкции фазы
    instr_req = data.get("waiting_instruction_edit")
    if instr_req:
        phase_idx = instr_req.get("phase_index")
        custom = dict(data.get("custom_instructions") or {})
        if text == "-":
            custom.pop(phase_idx, None)
            custom.pop(str(phase_idx), None)
            await message.answer(
                f"✅ Инструкция фазы {phase_idx + 1} сброшена к дефолту."
            )
        else:
            if len(text) > 3000:
                await message.answer("Слишком длинная инструкция (> 3000 символов).")
                return
            custom[str(phase_idx)] = text
            await message.answer(f"✅ Инструкция фазы {phase_idx + 1} обновлена.")
        await state.update_data(
            custom_instructions=custom, waiting_instruction_edit=None,
        )
        await _send_instructions_submenu(message, state)
        return

    # редактирование приветствия
    if data.get("waiting_description_edit"):
        if len(text) > 3000:
            await message.answer("Слишком длинное сообщение (> 3000 символов).")
            return
        await state.update_data(
            description=text, waiting_description_edit=False,
        )
        await message.answer("✅ Приветственное сообщение обновлено.")
        await show_config_menu(message, state)
        return

    # редактирование подписи Likert
    lkt_req = data.get("waiting_likert_edit")
    if lkt_req:
        key = lkt_req.get("key", "main")
        pos = lkt_req.get("pos")
        custom = dict(data.get("custom_likert") or {})
        main = dict(custom.get(key) or {})
        labels = dict(main.get("labels") or {})
        if text == "-":
            labels.pop(str(pos), None)
        else:
            if len(text) > 64:
                await message.answer("Слишком длинная подпись.")
                return
            labels[str(pos)] = text
        main["labels"] = labels
        custom[key] = main
        await state.update_data(
            custom_likert=custom, waiting_likert_edit=None,
        )
        await message.answer(f"✅ Позиция {pos} обновлена.")
        await _send_likert_submenu(message, state)
        return
