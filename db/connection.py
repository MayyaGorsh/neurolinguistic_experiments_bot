import certifi
from motor.motor_asyncio import AsyncIOMotorClient
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
