import os
from pymongo import MongoClient, ASCENDING
from dotenv import load_dotenv

load_dotenv()
client = MongoClient(os.environ["MONGODB_URI"], serverSelectionTimeoutMS=8000)
client.admin.command("ping")
db = client["radixmind"]

db.context_ledger.create_index(
    [("content_hash", ASCENDING)], unique=True, name="uniq_content_hash"
)
# One row per agent that is configured and run 
db.metrics.create_index(
    [("episode_id", ASCENDING), ("turn", ASCENDING)], name="episode_turn"
)

db.metrics.insert_one({"episode_id": "gate1", "turn": 0, "mode": "test"})
doc = db.metrics.find_one({"episode_id": "gate1"})
assert doc is not None
db.metrics.delete_one({"episode_id": "gate1"})
print("Gate 1 Mongo: Pass on this laptop")
