# reads ONLY real gateway-written metrics from Atlas.
# mongodb/aggregate.py
import os, sys
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()
db = MongoClient(os.environ["MONGODB_URI"])["radixmind"]

def per_turn(episode_id):
    print(f"\n--- {episode_id} (per turn, live from Atlas) ---")
    print(f"{'turn':<5}{'cached':>9}{'prompt':>9}{'hit%':>8}{'ttft_ms':>9}")
    for d in db.metrics.find({"episode_id": episode_id}).sort("turn", 1):
        pt, ct = d.get("prompt_tokens", 0), d.get("cached_tokens", 0)
        print(f"{d['turn']:<5}{ct:>9}{pt:>9}{(ct/pt*100 if pt else 0):>7.1f}%"
              f"{round(d.get('ttft_ms') or 0):>9}")

def summarize():
    pipeline = [
        {"$group": {"_id": "$mode",
                    "avg_ttft_ms": {"$avg": "$ttft_ms"},
                    "total_cached": {"$sum": "$cached_tokens"},
                    "total_prompt": {"$sum": "$prompt_tokens"},
                    "turns": {"$sum": 1}}},
        {"$addFields": {"hit_rate": {"$cond": [
            {"$eq": ["$total_prompt", 0]}, 0,
            {"$divide": ["$total_cached", "$total_prompt"]}]}}},
        {"$sort": {"_id": 1}},
    ]
    rows = list(db.metrics.aggregate(pipeline))
    if not rows:
        sys.exit("No metrics found — run driver.py first.")
    print("\n=== SUMMARY BY MODE ===")
    for r in rows:
        print(f"{r['_id']:<12} turns={r['turns']:<4} "
              f"avg_ttft_ms={r['avg_ttft_ms']:.0f}  "
              f"hit_rate={r['hit_rate']:.1%}  "
              f"tokens_saved={r['total_cached']:,}")

if __name__ == "__main__":
    per_turn("demo-baseline")
    per_turn("demo-radixmind")
    summarize()