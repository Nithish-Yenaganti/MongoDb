import json, os, httpx
from dotenv import load_dotenv
from pymongo import MongoClient
from scenario import SCENARIO
from seed import fetch_turn_text

load_dotenv()
db = MongoClient(os.environ["MONGODB_URI"])["radixmind"]
GATEWAY = os.getenv("GATEWAY_URL", "http://localhost:8000/turn")
# IDENTICAL every intermediate turn — a stable question keeps Segment C small.
INTERIM_QUESTION = "Note in one sentence what this evidence adds to the investigation."

def run_turn(payload):
    answer_parts, metrics, event = [], None, None
    with httpx.stream("POST", GATEWAY, json=payload, timeout=180) as r:
        r.raise_for_status()
        for raw in r.iter_lines():
            if not raw:
                continue
            if raw.startswith("event:"):
                event = raw.split(":", 1)[1].strip()
            elif raw.startswith("data:"):
                data = json.loads(raw.split(":", 1)[1].strip())
                if event == "token":
                    answer_parts.append(data.get("text", ""))
                elif event == "metrics":
                    metrics = data
                elif event == "error":
                    raise RuntimeError(data.get("detail"))
    return "".join(answer_parts), metrics or {}

def run_mode(mode):
    episode_id = f"demo-{mode}"
    print(f"\n=== MODE: {mode} ===")
    print(f"{'turn':<5}{'source':<16}{'cached':>8}{'prompt':>8}{'ttft_ms':>9}")
    for t in SCENARIO["turns"]:
        final = t["tool_name"] is None
        text = t["text"] if final else fetch_turn_text(db, t["tool_name"])
        answer, m = run_turn({
            "episode_id": episode_id, "turn": t["turn"], "mode": mode,
            "chunk": {"source_type": t["source_type"], "text": text},
            "question": t["text"] if final else INTERIM_QUESTION})
        print(f"{t['turn']:<5}{t['source_type']:<16}{m.get('cached_tokens', 0):>8}"
              f"{m.get('prompt_tokens', 0):>8}{round(m.get('ttft_ms') or 0):>9}")
        if final:
            print(f"\n--- FINAL ANSWER ({mode}) ---\n{answer}\n")

if __name__ == "__main__":
    run_mode("baseline")
    run_mode("radixmind")
