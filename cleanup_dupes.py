# cleanup_dupes.py — keep FIRST doc per (episode_id, turn); delete later duplicates.
import os
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()
db = MongoClient(os.environ["MONGODB_URI"])["radixmind"]
removed = 0
for grp in db.metrics.aggregate([
        {"$sort": {"created_at": 1}},
        {"$group": {"_id": {"e": "$episode_id", "t": "$turn"},
                    "ids": {"$push": "$_id"}}}]):
    for dup_id in grp["ids"][1:]:
        db.metrics.delete_one({"_id": dup_id})
        removed += 1
print("removed", removed, "duplicate metric docs")