import os
from dotenv import load_dotenv

load_dotenv()

# токен телеграм-бота
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# строка подключения к MongoDB
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")

# имя базы данных
DB_NAME = os.getenv("DB_NAME", "lingvo_bot")

# фримиум: максимум экспериментов у пользователя без премиум-статуса.
# считаются все эксперименты (черновики и активные), независимо от статуса.
FREE_EXPERIMENT_LIMIT = 5

