import certifi
from pymongo import MongoClient
from config import MONGO_URI

client = MongoClient(MONGO_URI, tlsCAFile=certifi.where(), serverSelectionTimeoutMS=10000)
print(client.admin.command("ping"))
