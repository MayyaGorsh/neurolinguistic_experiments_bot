"""
кабинет исследователя: создание, настройка, публикация экспериментов,
просмотр результатов и экспорт в CSV.

Этот модуль — тонкий оркестратор. Реализация разнесена по соседним
researcher_*-файлам, каждый из которых регистрирует свои хендлеры на
общий aiogram-router из handlers.researcher_common.

Импорт подмодулей здесь нужен ради побочного эффекта: декораторы
@router.callback_query / @router.message запускаются только при
загрузке модуля, поэтому без этого блока хендлеры не подцепятся.

main.py продолжает работать через `handlers.researcher.router` — он
реэкспортируется ниже. Также реэкспортируем три функции, которые
импортируют другие хендлеры (`free_form`, `media_upload`) — пути их
вызовов остались прежними.
"""

# router — общий для всех researcher_*-модулей;
# main.py забирает его как handlers.researcher.router.
from handlers.researcher_common import router  # noqa: F401

# подгружаем подмодули, чтобы их декораторы зарегистрировали хендлеры.
# порядок импортов не критичен (фильтры aiogram не пересекаются), но
# держим логичный — от создания эксперимента до его жизненного цикла.
from handlers import (  # noqa: F401
    researcher_create,
    researcher_settings,
    researcher_demographics,
    researcher_customization,
    researcher_instructions,
    researcher_text_input,
    researcher_csv,
    researcher_save,
    researcher_experiment,
)

# back-compat реэкспорт: эти имена импортируют другие хендлеры через
# `from handlers.researcher import …`. Не трогаем их пути — иначе
# free_form.py и media_upload.py перестанут импортироваться.
from handlers.researcher_common import auto_detect_mapping  # noqa: F401
from handlers.researcher_settings import show_config_menu  # noqa: F401
from handlers.researcher_experiment import show_experiment_detail  # noqa: F401
