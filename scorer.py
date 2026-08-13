# scorer.py — pure viability scorer. score(text, source_type) -> [0.0, 1.0].
import re

SOURCE_PRIORS = {
    "runbook": 0.95, "service_config": 0.90, "deploy_history": 0.85,
    "filtered_logs": 0.80,   # noisy text but high operational value
    "retrieved_doc": 0.55,
    "status_ping": 0.15, "log_dump": 0.08, "scratchpad": 0.05,
}
PIN_THRESHOLD = 0.60
# hex runs (rids), long digit runs, ISO timestamps — machine noise markers
_NOISE = re.compile(r"[0-9a-f]{8,}|\b\d{6,}\b|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", re.I)

def _text_quality(text: str) -> float:
    """Alphabetic density minus a machine-noise penalty. 0 = pure noise."""
    compact = text.replace(" ", "").replace("\n", "")
    if not compact:
        return 0.0
    alpha = sum(ch.isalpha() for ch in compact) / len(compact)
    hits_per_k = len(_NOISE.findall(text)) / max(1.0, len(text) / 1000)
    noise = min(1.0, hits_per_k / 8.0)   # ~8 hits per 1k chars = saturated
    return max(0.0, alpha - 0.5 * noise)

def score(text: str, source_type: str) -> float:
    prior = SOURCE_PRIORS.get(source_type, 0.50)
    return round(min(1.0, max(0.0, 0.75 * prior + 0.25 * _text_quality(text))), 3)

if __name__ == "__main__":
    from scenario import SCENARIO
    ok = True
    print(f"{'turn':<5}{'source_type':<17}{'score':>7}  decision   expected")
    for t in SCENARIO["turns"]:
        if t["expected_admission"] == "none":
            continue
        s = score(t["text"], t["source_type"])
        decision = "pin" if s >= PIN_THRESHOLD else "archive"
        mark = "" if decision == t["expected_admission"] else "   <-- MISMATCH"
        ok &= (decision == t["expected_admission"])
        print(f"{t['turn']:<5}{t['source_type']:<17}{s:>7.3f}  {decision:<9}  "
              f"{t['expected_admission']}{mark}")
    print("SCORER TEST: PASS — hand to A" if ok else
          "SCORER TEST: FAIL — nudge SOURCE_PRIORS on mismatched rows only")