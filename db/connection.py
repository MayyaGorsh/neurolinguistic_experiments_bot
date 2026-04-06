from motor.motor_asyncio import AsyncIOMotorClient
from config import MONGO_URI, DB_NAME

# клиент и база данных — инициализируются при импорте
client = AsyncIOMotorClient(MONGO_URI)
db = client[DB_NAME]

# коллекции
users_col = db["users"]
experiments_col = db["experiments"]
sessions_col = db["sessions"]
answers_col = db["answers"]
media_col = db["media"]
mailings_col = db["mailings"]
