# per-mode summary. Run any time after metrics exist.
import os
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()
db = MongoClient(os.environ["MONGODB_URI"])["radixmind"]

def summarize():
    pipeline = [
        {"$group": {
            "_id": "$mode",
            "avg_ttft_ms": {"$avg": "$ttft_ms"},
            "avg_cached_tokens": {"$avg": "$cached_tokens"},
            "avg_prompt_tokens": {"$avg": "$prompt_tokens"},
            "turns": {"$sum": 1},
        }},
        {"$addFields": {
            "hit_rate": {"$cond": [
                {"$eq": ["$avg_prompt_tokens", 0]}, 0,
                {"$divide": ["$avg_cached_tokens", "$avg_prompt_tokens"]}]}
        }},
        {"$sort": {"_id": 1}},
    ]
    for row in db.metrics.aggregate(pipeline):
        print(f"{row['_id']:<12} turns={row['turns']:<4} "
              f"avg_ttft_ms={row['avg_ttft_ms']:.0f}  "
              f"hit_rate={row['hit_rate']:.1%}")

if __name__ == "__main__":
    # quick self-test with throwaway docs, delete after
    db.metrics.insert_many([
        {"episode_id": "smoke", "turn": 1, "mode": "naive", "cached_tokens": 0, "prompt_tokens": 5000, "ttft_ms": 900},
        {"episode_id": "smoke", "turn": 1, "mode": "radixmind", "cached_tokens": 4500, "prompt_tokens": 5000, "ttft_ms": 200},
    ])
    summarize()
    db.metrics.delete_many({"episode_id": "smoke"})