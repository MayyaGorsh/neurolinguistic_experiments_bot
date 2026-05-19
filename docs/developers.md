---
title: Руководство для разработчика
---

# Руководство для разработчика

* Содержание
{:toc}

Документ — карта репозитория и справочник по функциям. Основная
цель — позволить новому разработчику быстро понять, где что лежит,
какие функции отвечают за какие сценарии и куда вносить правки при
добавлении новых шаблонов или новой логики.

## Стек

- Python 3 (асинхронный).
- [aiogram](https://docs.aiogram.dev/) — Telegram-фреймворк
  (FSM, роутеры, middlewares).
- [Motor](https://motor.readthedocs.io/) — асинхронный драйвер
  MongoDB; GridFS для бинарных данных (голос, картинки-стимулы).
- [pandas](https://pandas.pydata.org/) — парсинг CSV.
- [pydub](https://github.com/jiaaro/pydub) — добавление тишины в
  аудио, измерение длительности.
- [Pillow](https://pillow.readthedocs.io/) — оптимизация картинок
  под лимит Telegram (≤ 10 МБ, целевой ≈ 1 МБ).

## Запуск локально

1. Установить Python ≥ 3.11.
2. `python -m venv venv && venv\Scripts\activate` (Windows) или
   `source venv/bin/activate` (Linux/Mac).
3. `pip install -r bot_claude/requirements.txt`.
4. Положить в корень `.env`:

   ```
   BOT_TOKEN=123456:...
   MONGO_URI=mongodb://localhost:27017
   DB_NAME=linguistic_bot
   ```

5. Запустить локальный MongoDB или указать строку подключения к
   Atlas.
6. `python -m bot_claude.main`.

Логи пишутся в `bot.log` (DEBUG) и в консоль (INFO).

## Структура репозитория

```
bot_claude/
├── main.py                 # точка входа
├── config.py               # .env, лимиты
├── logger.py               # логирование
├── db/
│   ├── connection.py       # Motor + GridFS-бакеты
│   └── repositories.py     # CRUD по всем коллекциям
├── models/
│   ├── experiment.py       # dataclass: Trial / Phase / Experiment
│   ├── session.py          # dataclass: Answer / ParticipantSession
│   └── user.py             # dataclass: User + is_premium_active
├── handlers/
│   ├── start.py            # /start, выбор роли, deep-link
│   ├── common.py           # /help, /cancel, fallback
│   ├── participant.py      # прохождение эксперимента
│   ├── researcher.py       # фасад кабинета исследователя
│   ├── researcher_common.py        # общая инфраструктура researcher_*
│   ├── researcher_create.py        # создание эксперимента
│   ├── researcher_csv.py           # загрузка CSV
│   ├── researcher_settings.py      # настройки эксперимента
│   ├── researcher_demographics.py  # демография
│   ├── researcher_customization.py # лейблы кнопок, шкала
│   ├── researcher_instructions.py  # инструкции фаз
│   ├── researcher_experiment.py    # жизненный цикл, превью, экспорт
│   ├── researcher_save.py          # сохранение/обновление эксперимента
│   ├── researcher_text_input.py    # диспетчер текстового ввода
│   ├── media_upload.py     # загрузка медиафайлов
│   ├── free_form.py        # свободный формат
│   ├── promo.py            # промо-рассылка
│   └── premium.py          # премиум-статус
├── engine/
│   ├── runner.py           # движок прохождения
│   ├── audio.py            # тишина, длительность, переупаковка
│   ├── images.py           # сжатие картинок
│   └── demographics.py     # сбор демографии
├── templates/
│   ├── registry.py         # реестр шаблонов
│   ├── word_level.py       # шаблоны на уровне слова
│   ├── sentence_level.py   # шаблоны на уровне предложения
│   ├── auditory.py         # шаблоны с аудио
│   ├── visual.py           # шаблоны с визуальными стимулами
│   ├── free_form_examples.py  # генератор CSV-примеров для free_form
│   └── examples/           # эталонные CSV (и media/)
└── utils/
    ├── csv_parser.py       # парсинг CSV → trials
    ├── export.py           # экспорт CSV + zip с голосом
    ├── fsm_bridge.py       # мост между runner и FSM
    ├── idle_guard.py       # idle-таймаут участника
    ├── idle_middleware.py  # middleware idle для participant.router
    ├── media.py            # извлечение имён медиа из проб
    ├── stale_guard.py      # middleware stale-menu для исследователя
    ├── ui.py               # render_screen
    └── validators.py       # валидация эксперимента перед публикацией
```

## Схема MongoDB

База называется по `DB_NAME` (по умолчанию `linguistic_bot`).
Коллекции:

- `users` — `telegram_id`, `role`, `created_at`, `consent_given`,
  `consent_at`, `premium_until`.
- `experiments` — `owner_id`, `title`, `description`,
  `template_type`, `status`, список `phases` с пробами,
  `randomize_trials`, `use_lists`, `lists_count`, `time_limit`,
  `idle_timeout_seconds`, `audio_silence_seconds`,
  `collect_demographics`, `demographics_type`, `allow_repeat`,
  `deep_link_id`, кастомные лейблы, режим подачи (для AJT),
  `created_at`, `updated_at`.
- `sessions` — `telegram_id`, ссылка на эксперимент, `status`
  (`started` / `in_progress` / `completed` / `abandoned`),
  `assigned_list`, `current_phase`, `current_trial`, `is_preview`,
  `demographics`, `started_at`, `finished_at`, `last_activity_at`.
- `answers` — по одной записи на пробу: ссылки на сессию и
  эксперимент, индексы фазы и пробы, `stimulus_id`, `raw_response`,
  `normalized_response`, `is_correct`, `reaction_time_ms`,
  `timed_out`, `timestamp`, `metadata`.
- `media` — связь имени файла из CSV с `file_id` Telegram (для
  быстрой переотправки).
- `mailings` — журнал промо-рассылок.

GridFS-бакеты:

- `voice_answers` — байты голосовых ответов респондентов;
- `stimulus_media` — байты картинок-стимулов (тяжелее лимита
  `getFile`).

Имена коллекций и поля документов трогать нельзя: любое
переименование сломает уже собранные данные.

## Точки входа и поток сообщений

Поток сообщения в боте идёт через:

1. `main.py` поднимает `Dispatcher`, регистрирует FSM-storage,
   middleware'ы (`StaleMenuGuard`, `ParticipantIdleGuard`) и все
   роутеры.
2. Сообщение / callback падает в нужный роутер (`start.router`,
   `participant.router`, `researcher.router` и т. д.).
3. Хендлер исследователя при необходимости меняет FSM-state
   (`CreateExperiment.*`), хендлер участника — состояние сессии
   в БД (`sessions.current_phase`, `current_trial`).
4. Для шага прохождения вызывается `engine.runner.present_trial`
   или `process_answer`, которые читают шаблон через
   `templates.registry.get_template` и отрисовывают пробу.

## Как добавить новый шаблон эксперимента

1. В соответствующем модуле `templates/{group}.py` написать
   функцию `build_phase(trials, config, phase_index)`, которая
   собирает `Phase` для нового шаблона (по образцу существующих
   `build_lexical_decision`, `build_picture_selection` и т. д.).
2. Зарегистрировать шаблон через `templates.registry.register(code,
   info)`. Поля `info`:
   - `required_columns` — обязательные колонки CSV;
   - `csv_mapping` — как колонки CSV ложатся в поля trial
     (`stimulus`, `response_options`, `correct_answer`,
     `auxiliary` и т. п.);
   - `build_phase` — функция-сборщик из шага 1;
   - `export_columns` — какие колонки появятся в выгрузке сверх
     базовых;
   - `phases_info` — описание фаз шаблона (название, инструкция
     по умолчанию);
   - `example_caption` — комментарий к примеру CSV;
   - дефолтные лейблы кнопок и шкалы (если применимо).
3. Положить пример CSV в `templates/examples/{code}.csv`.
4. Дополнить `TEMPLATE_LIST`, `TEMPLATE_LABELS` и
   `TEMPLATE_DESCRIPTIONS` в `handlers/researcher_common.py`.
5. Если шаблон требует особой логики прохождения (несколько шагов
   на пробу, отдельная обработка ответа) — добавить ветку в
   `engine/runner.py`.
6. Если шаблон требует медиа — убедиться, что `utils/media.py`
   корректно извлекает имена файлов из новых полей.
7. Добавить раздел в `docs/researchers.md` с парадигмой, форматом
   CSV и примером.

# Карта модулей

## main.py

Точка входа приложения. Инициализирует bot и dispatcher,
регистрирует middleware'ы и роутеры, запускает polling.

- `main()` — инициализирует бота с токеном из конфига, регистрирует
  FSM-storage для runner'а через `fsm_bridge.register_storage`,
  навешивает middleware'ы (`StaleMenuGuard` на исследователя,
  `ParticipantIdleGuard` на участника), подключает все роутеры в
  правильном порядке, запускает цикл polling.

## config.py

Конфигурация приложения: переменные окружения, пути, лимиты.

- `BOT_TOKEN` — строка, токен Telegram-бота из `.env`;
- `MONGO_URI` — строка подключения к MongoDB (Atlas или локальный
  инстанс);
- `DB_NAME` — имя базы данных;
- `FREE_EXPERIMENT_LIMIT` — максимум экспериментов для
  фримиум-пользователя (5).

## logger.py

Логирование в консоль и файл.

- `setup_logger()` — возвращает логгер с форматированием дата +
  время, уровень DEBUG в файл `bot.log`, INFO в консоль.

## db/connection.py

Подключение к MongoDB и инициализация коллекций.

- `client` — `AsyncIOMotorClient`, подключается с TLS для Atlas
  при необходимости;
- `db`, `users_col`, `experiments_col`, `sessions_col`,
  `answers_col`, `media_col`, `mailings_col` — ссылки на
  коллекции;
- `get_voice_answers_bucket()` — лениво создаёт GridFS-бакет для
  голосовых ответов (из-за event loop);
- `get_stimulus_media_bucket()` — лениво создаёт GridFS-бакет для
  медиа-стимулов (картинки до 10 МБ).

## db/repositories.py

CRUD по всем коллекциям. Группы функций — по сущностям.

**Пользователи:**

- `get_user(telegram_id)` — найти пользователя по `telegram_id`.
- `create_user(data)` — создать нового, вернуть его `_id`.
- `update_user(telegram_id, update)` — обновить поля.
- `get_or_create_user(telegram_id, defaults)` — найти или создать с
  дефолтами.

**Эксперименты:**

- `create_experiment(data)` — создать, вернуть `_id`.
- `get_experiment(experiment_id)` — получить по `_id`.
- `get_experiment_by_link(deep_link_id)` — получить по коду
  приглашения.
- `get_experiments_by_owner(owner_id)` — все эксперименты
  исследователя, отсортированные по дате.
- `count_experiments_by_owner(owner_id)` — для фримиум-лимита.
- `update_experiment(experiment_id, update)` — обновить и
  поставить timestamp.
- `delete_experiment_cascade(experiment_id)` — удалить эксперимент
  и все связанные сессии, ответы, медиа, блобы; вернуть счётчики
  удалённых записей.

**Голосовые блобы (GridFS):**

- `save_voice_blob(data, filename, metadata)` — сохранить байты
  голоса, вернуть `blob_id`.
- `read_voice_blob(blob_id)` — прочитать байты или `None`.

**Стимульные медиа-блобы (GridFS):**

- `save_stimulus_blob(data, filename, metadata)` — сохранить
  байты картинки.
- `read_stimulus_blob(blob_id)` — прочитать байты.

**Сессии:**

- `create_session(data)` — создать сессию.
- `get_session(session_id)` — получить по `_id`.
- `get_active_session(telegram_id, experiment_id)` — найти
  незавершённую сессию пользователя для конкретного эксперимента.
- `get_latest_active_session(telegram_id)` — самая свежая
  `in_progress`-сессия (нужна для текстовых ответов, когда неясно,
  в какой эксперимент они адресованы).
- `abandon_other_active_sessions(telegram_id, keep_session_id)` —
  закрыть все `in_progress`-сессии пользователя, кроме указанной
  (вызывается при старте нового эксперимента).
- `get_sessions_by_experiment(experiment_id)` — все сессии
  эксперимента.
- `update_session(session_id, update)` — обновить поля.
- `push_phase_message_ids(session_id, message_id)` — добавить
  `message_id` в список для удаления при смене фазы.
- `mark_session_completed(session_id)` — атомарно перевести
  сессию в `completed`; вернуть `True`, если это был первый
  успешный перевод (защита от двойного клика).
- `count_sessions_by_list(experiment_id)` — для balanced
  distribution участников по листам.

**Ответы:**

- `save_answer(data)` — сохранить ответ.
- `get_answers_by_session(session_id)` — все ответы сессии.
- `get_answers_by_experiment(experiment_id)` — все ответы
  эксперимента.

**Медиа-метаданные:**

- `save_media(data)` — создать запись о файле (для кэширования
  `file_id`).
- `get_media(media_id)` — получить по `_id`.
- `get_media_by_experiment(experiment_id)` — все медиа-записи
  эксперимента.
- `get_media_by_filename(experiment_id, filename)` — поиск по
  имени.
- `update_media(media_id, update)` — обновить.
- `set_media_photo_id(experiment_id, filename, photo_id)` —
  закэшировать `photo file_id` из Telegram.

**Рассылки:**

- `save_mailing(data)` — записать лог рассылки.
- `get_past_participants()` — список `telegram_id` пользователей,
  завершивших хотя бы один эксперимент и давших согласие.

## models/experiment.py

Dataclass'ы для структурирования данных эксперимента.

- `Trial` — одна проба: `trial_index`, `stimulus_content`,
  `stimulus_type`, `response_options`, `correct_answer`,
  `auxiliary`, `list_id`. Методы `to_dict()` / `from_dict()`.
- `Phase` — фаза: `phase_index`, `title`, `instruction`,
  `stimulus_type`, `response_type` (`buttons` / `likert` /
  `multiple_choice` / `open_text` / `voice`), список `trials`,
  `randomize_order`, `time_limit`, `settings`. Методы `to_dict()`
  / `from_dict()`.
- `Experiment` — главный объект: `owner_id`, `title`,
  `description`, `template_type`, `status`, `phases`,
  `randomize_trials`, `use_lists`, `lists_count`, `time_limit`,
  `idle_timeout_seconds`, `audio_silence_seconds`,
  `collect_demographics`, `demographics_type`, `allow_repeat`,
  `export_settings`, `deep_link_id`, `created_at`, `updated_at`.
  Методы `to_dict()` / `from_dict()`.

## models/session.py

Dataclass'ы для сессии и ответов.

- `Answer` — один ответ на пробу: `session_id`, `experiment_id`,
  `phase_index`, `trial_index`, `stimulus_id`, `raw_response`,
  `normalized_response`, `is_correct`, `reaction_time_ms`,
  `timed_out`, `timestamp`, `metadata`.
- `ParticipantSession` — сессия: `telegram_id`, `experiment_id`,
  `status`, `assigned_list`, `current_phase`, `current_trial`,
  `is_preview`, `demographics`, `started_at`, `finished_at`,
  `last_activity_at`.

## models/user.py

Dataclass для пользователя и утилита для проверки премиума.

- `User` — `telegram_id`, `role`, `created_at`, `consent_given`,
  `consent_at`, `premium_until`.
- `is_premium_active(user)` — `True`, если `premium_until > now`.

## handlers/start.py

Сценарий входа: выбор роли и начало эксперимента по deep-link.

- `cmd_start_deep_link(message, command, bot, state)` — обработка
  `/start exp_<code>`: проверка согласия, вход в эксперимент или
  показ экрана согласия.
- `_enter_experiment_by_link(bot, reply_target, telegram_id,
  deep_link_id)` — основная логика входа: поиск эксперимента по
  link, проверка статуса, возобновление незавершённой сессии (с
  idle-проверкой) или создание новой.
- `on_consent_yes` — сохранение согласия и вход в эксперимент.
- `on_consent_no` — отказ от участия.
- `cmd_start()` — обычный `/start`: приветствие с выбором роли.
- `on_welcome_researcher` — переход в режим исследователя.
- `on_welcome_participant` — инструкция для участника.
- `build_researcher_menu_kb(is_premium)` — клавиатура главного
  меню исследователя.
- `show_researcher_menu(message, state)` — показ главного меню
  исследователя.
- `cmd_unsubscribe()` — отписка от рассылки (`/unsubscribe`).

## handlers/common.py

Fallback-хендлеры и обработка неподдерживаемых типов сообщений.

- `cmd_cancel` — отмена текущего действия и сброс FSM.
- `cmd_help` — справка по командам.
- `on_sticker`, `on_animation`, `on_contact`, `on_location` —
  отклоняют соответствующие типы сообщений.
- `fallback` — неизвестное сообщение → подсказка о `/start`.

## handlers/participant.py

Прохождение эксперимента участником: начало, ответы кнопками,
текст, голос, демография.

- `on_begin_experiment(callback)` — нажата «Начать»: создание
  сессии, распределение по листам, подготовка проб (фильтр по
  листу + рандомизация), старт демографии или первого стимула.
- `start_experiment_flow(bot, chat_id, session, experiment)` —
  показ первого стимула после демографии.
- `on_instruction_ok(callback)` — респондент прочитал инструкцию
  фазы → показ первой пробы.
- `on_answer_button(callback)` — нажата кнопка ответа: проверка
  корректности, обработка `multiple_choice` (toggle / submit),
  обработка других типов (`buttons` / `likert`) → вызов
  `runner.process_answer`.
- `on_demo_button` — ответ на вопрос демографии кнопкой.
- `on_text_answer(message)` — текстовое сообщение: поиск активной
  сессии, обработка демографии или `open_text` /
  `buttons_then_text`.
- `on_voice_answer(message)` — голосовое сообщение: скачивание в
  GridFS, обработка как ответ на voice-фазу.
- `find_active_session(telegram_id)` — поиск самой свежей
  незавершённой сессии.

## handlers/researcher.py

Тонкий орхестратор. Реэкспортирует `router` из
`researcher_common`, импортирует все `researcher_*`-подмодули,
чтобы их декораторы зарегистрировались. Также реэкспортирует
`auto_detect_mapping` и `show_config_menu` для использования из
`free_form` и `media_upload`.

## handlers/researcher_common.py

Общая инфраструктура для всех `researcher_*`-подмодулей.

- `router` — общий `Router` для всех подмодулей.
- `CreateExperiment` — FSM-состояния: `choosing_template`,
  `entering_title`, `entering_description`, `configuring`,
  `uploading_csv`, `uploading_media`, `uploading_demographics`.
- `TEMPLATE_LIST`, `TEMPLATE_LABELS` — список доступных шаблонов.
- `TEMPLATE_DESCRIPTIONS` — описание каждого шаблона (методология,
  как работает).
- `_csv_template_phases` — названия фаз по шаблону и числу листов.
- `_template_has_buttons`, `_ajt_has_stimulus2` — служебные
  проверки.
- `_reset_input_flags` — очистка `waiting_*`-флагов state.
- `_render_screen` — реэкспорт `utils.ui.render_screen` для
  подмодулей.

## handlers/researcher_create.py

Флоу создания нового эксперимента: выбор шаблона → ввод названия и
описания.

- `on_create_experiment` — проверка фримиум-лимита, показ списка
  шаблонов.
- `on_template_chosen` — выбран шаблон → запрос названия.
- `on_title_entered` — ввод названия → запрос приветственного
  сообщения → переход в `show_config_menu`.

## handlers/researcher_csv.py

Загрузка CSV со стимулами по фазам и листам. Manifest-UI с
галочками для каждой комбинации (фаза × лист).

- `_build_csv_manifest(data)` — собирает текст и клавиатуру
  manifest: лист кнопок со статусом загрузки.
- Хендлеры: выбор слота → запрос файла → парсинг CSV → валидация
  по шаблону → сохранение в `state.csv_data`.

## handlers/researcher_settings.py

Экраны настроек эксперимента: top-level сводка с кнопками,
подменю всех тоггл-настроек.

- `show_config_menu(experiment_id, state)` — главное меню
  настроек: сводка параметров + кнопки действий (CSV, медиа,
  демография, инструкции, тест, сохранить).
- `show_settings_submenu(callback, state)` — развёрнутый список
  всех настроек: рандомизация, листы, демография, тайм-ауты,
  тишина в аудио, повтор, кастомные лейблы кнопок.
- `_collect_settings_state(data)` — собирает текущие значения и
  человекочитаемые лейблы (используется обоими экранами).
- `_settings_rows(state_dict)` — формирует список параметров для
  отображения.
- Хендлеры тоггл-настроек и запрос значений (через флаги
  `waiting_*`).

## handlers/researcher_demographics.py

Режимы демографической анкеты: `off` / `standard` / `custom`. Для
custom — загрузка CSV с вопросами.

- `show_demographics_menu` — выбор режима.
- `demo_set_off`, `demo_set_standard`, `demo_set_custom` —
  применение режима.
- `demo_ask_upload` — запрос файла CSV с вопросами.
- CSV-парсинг и валидация вопросов.

## handlers/researcher_customization.py

Кастомизация элементов ответа: лейблы кнопок (для Picture
Selection, Covered Box и др.), параметры Likert-шкалы.

- `_get_current_buttons(data, tmpl_code, key)` — текущие лейблы
  (кастомные или дефолт из шаблона).
- `_get_current_likert(data, tmpl_code)` — текущие параметры
  шкалы.
- `_show_buttons_submenu` — редактирование лейблов кнопок.
- `_show_likert_submenu` — редактирование шкалы Ликерта.
- Хендлеры редактирования (запрос ввода текста).

## handlers/researcher_instructions.py

Редактирование инструкций фаз.

- `_get_default_instruction(tmpl_code, phase_index)` — дефолтная
  инструкция из шаблона.
- `_get_current_instruction(data, tmpl_code, phase_index)` —
  кастомная или дефолт.
- `_build_instructions_text_and_kb(data)` — собирает текст и
  клавиатуру подменю инструкций.
- `_show_instructions_submenu` — показ подменю редактирования.

## handlers/researcher_experiment.py

Жизненный цикл эксперимента после сохранения: карточка, список
«Мои эксперименты», редактирование, активация / деактивация /
удаление, превью, экспорт.

- `show_experiment_detail(target, experiment_id, banner, state)` —
  главная карточка эксперимента: сводка (фазы, пробы, листы),
  настройки, кнопки действий (редактировать, активировать,
  превью, экспорт, удалить).
- `on_my_experiments` — список всех экспериментов исследователя.
- `on_edit_experiment` — загрузить черновик обратно в state для
  редактирования.
- `on_activate_experiment`, `on_deactivate_experiment` — смена
  статуса.
- `on_delete_experiment` — запрос подтверждения и cascade-удаление.
- `on_preview_experiment` — запуск превью (эксперимент для
  исследователя как участника с флагом `is_preview`).
- `on_export` — экспорт в CSV + голосовые ответы (zip).

## handlers/researcher_save.py

Сохранение эксперимента: создание нового или обновление черновика
из `state.data`. Пересборка фаз через `build_phase` шаблона,
применение override-инструкций, привязка медиа `file_id` к пробам,
переупаковка аудио при изменении тишины.

- `_reapply_audio_silence(bot, owner_chat_id, experiment_id,
  silence_seconds, phases)` — перезалить все аудио с новой
  длительностью тишины.
- `_attach_media_ids_to_phases(experiment_id, phases)` — найти
  медиа-записи по имени файла и заполнить `blob_id` / `file_id`
  во всех пробах.
- `on_save_experiment` — основная функция: сбор данных из state,
  построение фаз, применение инструкций и медиа, вызов
  repository для сохранения / обновления, переход на карточку.

## handlers/researcher_text_input.py

Диспетчер текстового ввода в состоянии `configuring`. Направляет
текст в нужный обработчик по флагам `waiting_*`.

- `on_config_text(message, state)` — проверяет флаги
  (`waiting_timeout`, `waiting_audio_silence`,
  `waiting_idle_timeout`, `waiting_lists_count`,
  `waiting_button_edit`, `waiting_likert_label`) и обрабатывает
  текст соответственно.

## handlers/media_upload.py

Загрузка медиафайлов (аудио, изображения, видео) и привязка к
пробам.

- `MediaUpload` — FSM-состояния (`waiting_files`,
  `setting_silence`).
- `start_media_upload(message, experiment_id, state)` —
  инициирует процесс: показывает список ожидаемых файлов,
  инструкцию по форматам.
- Хендлеры приёма файлов: детектирование медиа-типа, скачивание /
  обработка (сжатие картинок, добавление тишины в аудио),
  сохранение в GridFS / MongoDB.
- `on_media_done` — завершение загрузки, переход на карточку
  эксперимента.

## handlers/free_form.py

Свободный формат: исследователь конструирует эксперимент без
предопределённого шаблона. Каждая фаза: тип стимула → тип ответа
→ инструкция → CSV → настройки.

- `STIMULUS_TYPES`, `RESPONSE_TYPES` — доступные типы.
- `start_free_form(experiment_id, state)` — начало
  конструирования.
- Хендлеры: выбор типов стимула / ответа, загрузка CSV,
  применение per-phase-настроек.
- `build_freeform_csv_example(stim_type, resp_type)` — генерация
  CSV-примера на лету (см. `templates/free_form_examples.py`).

## handlers/promo.py

Рассылка промо-текстов прошлым участникам (премиум-фича). FSM:
`entering_text` → `confirming` → отправка.

- `PromoStates` — состояния FSM.
- `on_promo_menu` — проверка премиума, показ числа участников,
  запрос текста.
- `on_promo_text` — ввод текста → подтверждение.
- `on_promo_send` — отправка сообщений всем участникам из
  `get_past_participants`.

## handlers/premium.py

Премиум-статус: экран описания и приём заявок на оплату. Перевод
денег вне бота (ручной банковский перевод). Пользователь присылает
скриншот, админ проверяет и обновляет `premium_until` в БД.

- `PremiumStates` — состояния (`waiting_screenshot`).
- `on_premium_info` — показ описания, условий, реквизитов.
- `on_premium_send_proof` — запрос скриншота платежа.
- `on_premium_proof_received` — сохранение скриншота в лог.

## engine/runner.py

Движок прохождения эксперимента. Управляет последовательностью
фаз и проб, измеряет RT, обрабатывает тайм-ауты, сохраняет
ответы.

- `_stimulus_shown_at` — внутреннее хранилище времени показа
  стимула (для RT).
- `_timeout_tasks` — активные таймер-задачи тайм-аутов.
- `present_trial(bot, chat_id, session, experiment)` — показать
  текущую пробу: удалить старые сообщения при смене фазы или
  если `delete_previous_trials=True`, показать инструкцию если
  ещё не показывалась, показать стимул, запустить таймер.
- `process_answer(bot, chat_id, session, experiment,
  raw_response, option_index, ...)` — обработать ответ: записать
  в БД, зафиксировать RT, перейти к следующей пробе или к
  следующему шагу (для `buttons_then_text`) или выполнить
  специальную логику (`interpretation_generation`).
- `advance_phase(bot, chat_id, session, experiment)` — переход к
  следующей фазе.
- `finish_experiment(bot, chat_id, session)` — завершение:
  благодарность, сохранение timestamp, атомарный перевод в
  `completed`.
- `prepare_trials_for_session(phase, assigned_list,
  randomize_button_positions)` — фильтр проб по листу и
  рандомизация порядка (на уровне сессии, не меняя сам
  эксперимент).
- `build_response_keyboard(trial, phase, session_id, phase_idx,
  trial_idx, selected)` — формирование клавиатуры ответов.

## engine/audio.py

Работа с аудио: тишина, длительность, удаление после
проигрывания, переупаковка пачки файлов.

- `append_silence(audio_bytes, silence_ms, fmt)` — добавить
  тишину в конец аудиофайла.
- `get_duration_ms(audio_bytes, fmt)` — получить длительность в
  мс.
- `delete_after_playback(bot, chat_id, message_id, duration_ms)`
  — асинхронно удалить сообщение через `duration_ms`.
- `reupload_padded_audios_batch(bot, chat_id, items,
  silence_seconds)` — переупаковать пачку аудио одной media-group:
  скачать, добавить тишину, отправить, получить новые `file_id`,
  вернуть `{filename: (file_id, duration_ms)}`.

## engine/images.py

Работа с картинками: сжатие под лимит `send_photo` (≤ 10 МБ,
целевой ≈ 1 МБ).

- `shrink_image_for_photo(img_bytes, hint_filename)` — вернуть
  байты, оптимизированные для `send_photo`: уменьшить по
  максимальной стороне (1280 / 960 / 720 / 540 px), пересжать в
  JPEG с постепенным снижением качества.

## engine/demographics.py

Сбор демографических данных перед началом эксперимента.

- `STANDARD_QUESTIONS` — встроенные вопросы (возраст, пол, город,
  родной язык).
- `get_questions(experiment)` — получить список вопросов (`off`
  / `standard` / `custom`).
- `ask_demographic_question(bot, chat_id, session_id, questions,
  q_index)` — задать один вопрос (кнопками или текстом).
- `save_demographic_answer(session_id, questions, q_index,
  answer)` — сохранить ответ в `session.demographics`.

## templates/registry.py

Реестр шаблонов. Каждый шаблон описывает формат CSV
(`required_columns`, `csv_mapping`), функцию `build_phase`,
экспортные колонки, список фаз.

- `register(code, info)` — зарегистрировать шаблон.
- `get_template(code)` — получить описание шаблона.
- `all_templates()` — все зарегистрированные шаблоны.
- `get_example_csv_path(code, phase)` — путь к файлу-примеру CSV.
- `get_example_csv_paths(code, phase)` — все примеры (основной +
  дополнительные).
- `get_example_caption(code, phase)` — комментарий к примеру.
- `get_response_options(config, defaults, key)` — вернуть
  кастомные лейблы кнопок, если их длина совпадает с дефолтной.

## templates/word_level.py

Шаблоны на уровне слова: Lexical Decision, Predictability Rating,
Cloze (MC / open), Word Translation (MC / open).

- `build_lexical_decision(trials, config, phase_index)` — собирает
  фазу.
- `build_predictability_rating` — формирует стимул с контекстом и
  целевым словом, Likert-шкала.
- `build_cloze_mc`, `build_cloze_open` — закрытый и открытый
  cloze.
- `build_word_translation_mc`, `build_word_translation_open` —
  перевод слов.

Для каждого вызывается `register(code, info)` с описанным выше
набором полей.

## templates/sentence_level.py

Шаблоны на уровне предложения: Sensicality / Acceptability
Judgment, TVJT, Statement Verification, Self-Paced Reading, Maze,
Text Change Detection, Probe Recognition, Interpretation
Generation.

- `build_sensicality`, `build_acceptability`, `build_tvjt`,
  `build_statement_verification`, `build_self_paced_reading`,
  `build_maze`, `build_text_change_detection`,
  `build_probe_recognition`, `build_interpretation_generation` —
  построение фаз.
- Acceptability Judgment поддерживает одиночную подачу,
  совместную (joint) с одной или двумя оценками.
- Self-Paced Reading показывает по сегментам, измеряет RT на
  каждом.
- Text Change Detection: два прохода (оригинал vs изменённый),
  поиск различий.
- Interpretation Generation: после ответа запрос текстовой
  интерпретации.

## templates/auditory.py

Шаблоны с аудиальными стимулами: Forced Choice Identification,
Sentence Repetition.

- `build_forced_choice(trials, config, phase_index)` — аудио +
  кнопки для категоризации; поддерживает поле `repeats`
  (развёртывание повторов в несколько проб).
- `build_sentence_repetition` — аудио + голосовой ответ.

## templates/visual.py

Шаблоны с визуальными стимулами: Picture Selection, Covered Box,
Picture Naming, Video Task.

- `build_picture_selection` — предложение + выбор из двух
  картинок; позиции из CSV, рандомизация на уровне сессии.
- `build_covered_box` — предложение + выбор из 2 + 1 (третья —
  закрытая коробка).
- `build_picture_naming` — картинка + голосовой или текстовый
  ответ.
- `build_video_task` — видео + ответ (кнопки, текст или голос).

## templates/free_form_examples.py

Генератор CSV-примеров для свободного формата (без предопределённого
шаблона).

- `build_freeform_csv_example(stim_type, resp_type, n)` — собрать
  на лету CSV-пример для пары (тип стимула × тип ответа):
  возвращает байты UTF-8 с разделителем `;`, содержит примеры
  стимулов и вариантов ответа.

## utils/csv_parser.py

Парсинг CSV-файлов со стимулами, валидация, преобразование в
trial-объекты.

- `_detect_delimiter(first_line)` — возвращает `;` (единственный
  поддерживаемый разделитель).
- `parse_csv_text(text)` — читает CSV-текст, возвращает список
  словарей.
- `parse_csv_bytes(data, encoding)` — читает CSV из байтов
  (UTF-8 или CP1251).
- `validate_columns(rows, required)` — проверяет наличие
  обязательных колонок, возвращает список ошибок.
- `rows_to_trials(rows, mapping)` — преобразует строки CSV в
  trial-объекты по заданному маппингу; распознаёт правильные
  ответы (маркер `*`), `list_id` для распределения.

## utils/export.py

Экспорт результатов эксперимента в CSV (+ голосовые ответы в zip
при наличии).

- `export_experiment_csv(experiment_id)` — формирует CSV со
  всеми ответами: базовые колонки (`participant_id`, `session_id`,
  `phase`, `trial`, `raw_response`, `RT`, `is_correct`) +
  template-specific колонки + демография; фильтрует preview-сессии.
- `export_experiment_bundle(experiment_id)` — возвращает zip с
  CSV + папка `voice/` (если есть голосовые ответы), иначе
  одиночный CSV.

## utils/fsm_bridge.py

Мост между runner (engine/) и FSM dispatcher. Runner не получает
`FSMContext`, но иногда ему нужно обновить state пользователя
(например, `active_menu_msg_id` после превью).

- `register_storage(storage)` — глобально сохранить FSM-storage из
  dispatcher.
- `update_active_menu(bot_id, chat_id, user_id, msg_id)` —
  обновить `active_menu_msg_id` в FSM-state пользователя
  (используется runner после показа меню).

## utils/idle_guard.py

Проверка тайм-аута бездействия участника.

- `check_and_abandon_if_idle(session, experiment, bot,
  telegram_id)` — `True`, если сессия истекла
  (`idle_timeout_seconds > 0` и прошло больше времени); помечает
  `abandoned`, отправляет сообщение.
- `touch_session(session_id)` — обновить `last_activity_at`
  (вызывается на каждом действии участника и при показе стимула).

## utils/idle_middleware.py

Middleware для `participant.router`: проверяет idle-таймаут на
каждом действии (callback / message), поглощает событие, если
истекло.

- `ParticipantIdleGuard` — middleware-класс с методом
  `__call__`: достаёт активную сессию, проверяет idle, поглощает
  событие если истекло, иначе обновляет `last_activity_at` и
  пускает дальше.

## utils/media.py

Утилиты для извлечения имён медиа-файлов из проб (рекурсивный
поиск по всем полям).

- `MEDIA_EXTS` — расширения медиа-файлов (`.wav`, `.mp3`, `.jpg`,
  `.mp4` и т. д.).
- `looks_like_media(s)` — `True`, если строка похожа на имя
  медиа-файла (по расширению).
- `scan_for_media(value, out)` — рекурсивно ищет в `dict` /
  `list` / `str` все строки, похожие на медиа.
- `collect_trial_media(trial)` — все медиа-файлы для одной пробы.
- `collect_experiment_media(experiment)` — объединение по всем
  фазам и листам (что должно быть загружено).

## utils/stale_guard.py

Middleware: блокирует клики по устаревшим меню исследователя.

- `StaleMenuGuard` — middleware-класс: проверяет совпадение
  `message_id` с `active_menu_msg_id` из FSM, блокирует callback,
  если не совпадает; пускает дальше, если совпадает или
  `active_menu_msg_id` не установлен.

## utils/ui.py

Общие UI-хелперы для интерфейса исследователя.

- `render_screen(target, text, kb, state)` — показать экран в
  едином стиле SPA: если `target` — `CallbackQuery`, редактирует
  то же сообщение (или fallback на `.answer`); если `Message` —
  отправляет новое; после успеха обновляет `active_menu_msg_id` в
  FSM (для `StaleMenuGuard`).

## utils/validators.py

Валидация эксперимента перед публикацией.

- `validate_experiment(experiment)` — проверяет: название
  непусто, фазы и пробы не пусты, для всех проб с медиа загружены
  файлы, листы консистентны. Возвращает список ошибок или пустой
  список.

# Тестирование

Автотестов пока нет. Перед коммитом изменений проверять вручную:

1. `python -m bot_claude.main` запускается без ошибок.
2. Создать тестовый эксперимент в каждом затронутом шаблоне.
3. Загрузить эталонный CSV из `templates/examples/` —
   парсинг и валидация должны пройти.
4. Активировать → перейти по deep-link с другого аккаунта →
   ответить на все пробы → выгрузить CSV → проверить колонки.

Для шаблонов с медиа отдельно проверить: загрузка файлов,
кэширование `file_id` (вторая сессия должна стартовать быстрее
первой), экспорт zip с голосовыми ответами.

# Деплой

Бот работает как один процесс `python -m bot_claude.main`.
Зависимости — `bot_claude/requirements.txt`.

На удалённом сервере:

- держать процесс под supervisor / systemd с автоперезапуском;
- логи в `bot.log` ротировать средствами ОС;
- MongoDB — отдельный инстанс или Atlas (для Atlas нужен TLS,
  поддерживается автоматически по `MONGO_URI`);
- при долгих простоях GridFS-бакеты не используют, держать
  свободное место под голосовые ответы.

Скрипты в `bot_claude/scripts/` — служебные одноразовые миграции
и проверки подключений (`connection_testing.py`).
