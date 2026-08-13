
import os 
from dotenv import load_dotenv 
from pymongo import MongoClient, ASCENDING
from scenario import SCENARIO 

load_dotenv()
db = MongoClient(os.environ["MONGODB_URI"])["radixmind"]

turns = {t["turn"]: t for t in SCENARIO["turns"]}

def parse_log_lines(dump: str):
    docs = []
    for i, line in enumerate(dump.split("\n")):
        parts = line.split(" ", 2)
        docs.append({"line_no": i,
                     "level": parts[1] if len(parts) > 1 else "INFO",
                     "text": line})
    return docs


def fetch_turn_text(db, tool_name: str) -> str:
    """THE TOOL CONTRACT — C's driver imports this. Each tool is a real query."""
    if tool_name == "get_service_status":
        return db.status_events.find_one({"event_id": "uptime-1306"})["text"]
    if tool_name == "fetch_recent_logs":
        return "\n".join(d["text"] for d in db.logs.find().sort("line_no", 1))
    if tool_name == "fetch_runbook":
        return db.runbooks.find_one({"runbook_id": "RB-114"})["text"]
    if tool_name == "fetch_service_config":
        return db.service_configs.find_one({"service": "checkout-service"})["text"]
    if tool_name == "query_error_logs":  # the star: a genuine filtered query
        return "\n".join(d["text"] for d in
                         db.logs.find({"level": "ERROR"}).sort("line_no", 1))
    if tool_name == "probe_dependency":
        return db.status_events.find_one({"event_id": "pg-direct-1311"})["text"]
    if tool_name == "fetch_deploy_history":
        return db.deploys.find_one({"service": "checkout-service"})["text"]
    raise KeyError(f"unknown tool: {tool_name}")

if __name__ == "__main__":
    for c in ["logs", "runbooks", "service_configs", "deploys",
              "status_events", "incident_meta"]:
        db[c].delete_many({})  # idempotent re-seed

    db.logs.insert_many(parse_log_lines(turns[2]["text"]))
    db.logs.create_index([("line_no", ASCENDING)])
    db.logs.create_index([("level", ASCENDING), ("line_no", ASCENDING)])
    db.runbooks.insert_one({"runbook_id": "RB-114",
                            "service": "checkout-service",
                            "text": turns[3]["text"]})
    db.service_configs.insert_one({"service": "checkout-service",
                                   "text": turns[4]["text"]})
    db.deploys.insert_one({"service": "checkout-service",
                           "text": turns[7]["text"]})
    db.status_events.insert_one({"event_id": "uptime-1306",
                                 "text": turns[1]["text"]})
    db.status_events.insert_one({"event_id": "pg-direct-1311",
                                 "text": turns[6]["text"]})
    db.incident_meta.insert_one({
        "episode_name": SCENARIO["episode_name"],
        "ground_truth_diagnosis": SCENARIO["ground_truth_diagnosis"],
        "expected_naive_failure": SCENARIO["expected_naive_failure"]})

    # Round-trip integrity: Mongo must hand back EXACTLY what C authored.
    for t in SCENARIO["turns"]:
        if t["tool_name"] is None:
            continue
        rebuilt = fetch_turn_text(db, t["tool_name"])
        assert rebuilt == t["text"], (
            f"ROUND-TRIP MISMATCH on turn {t['turn']} ({t['tool_name']}) — "
            "do not proceed; text from Mongo differs from scenario.py")
    n_logs = db.logs.count_documents({})
    n_err = db.logs.count_documents({"level": "ERROR"})
    print(f"SEED: PASS — {n_logs} log lines ({n_err} ERROR), "
          "all turns round-trip byte-identical")
