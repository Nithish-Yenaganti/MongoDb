"""

"""
# mongodb/scenario.py
import random

# ── The one dial C is allowed to turn ────────────────────────────────────────
LOG_LINES = 350            # double it if the demo contrast is weak
_SEED = 42                 # never change: determinism is load-bearing
_GUN_INDEX = LOG_LINES // 2 + 7   # where the smoking gun hides in the dump

SMOKING_GUN = (
    "2026-08-13T13:07:42Z ERROR db-pool: connection pool exhausted "
    "(5/5 connections in use), 214 requests queued >30s — pool ceiling "
    "changed by deploy 8f3c2a1"
)


def generate_log_dump() -> str:
    """The big noisy payload. Mostly routine INFO chatter plus a heavy layer
    of payment-gateway timeout red herrings. Exactly one causal line, buried
    at a fixed position. Latencies drift upward through the dump so the data
    itself tells the degradation story."""
    rng = random.Random(_SEED)
    lines = []
    for i in range(LOG_LINES):
        ts = f"2026-08-13T13:{5 + i // 60:02d}:{i % 60:02d}Z"
        rid = f"rid={rng.getrandbits(32):08x}"
        # latency climbs as the pool starves — realism, and it rewards a
        # model that actually reads the filtered slice
        latency = int(120 + (i / LOG_LINES) * 9000 * rng.uniform(0.5, 1.5))
        roll = rng.random()
        if roll < 0.45:
            lines.append(f"{ts} INFO checkout: request completed {rid} "
                         f"status=200 latency={latency}ms")
        elif roll < 0.70:   # the red-herring flood
            lines.append(f"{ts} WARN payment-gateway: call timed out after "
                         f"2000ms {rid} retrying ({rng.randint(1,3)}/3)")
        elif roll < 0.80:
            lines.append(f"{ts} INFO healthz: probe 200 OK uptime=99.98%")
        elif roll < 0.90:
            lines.append(f"{ts} DEBUG cache: miss key=cart:{rng.getrandbits(24):06x}")
        elif roll < 0.95:
            lines.append(f"{ts} WARN db: slow query {latency + 800}ms on "
                         f"orders.find {rid}")
        else:               # ERROR-level red herrings, so the filtered slice
                            # isn't a one-line giveaway
            lines.append(f"{ts} ERROR payment-gateway: upstream timeout after "
                         f"3 retries {rid} — giving up")
    lines[_GUN_INDEX] = SMOKING_GUN
    return "\n".join(lines)


def generate_error_slice() -> str:
    """What a *targeted* query returns: ERROR lines only (~15-20 lines).
    Small, high-signal, still contains red herrings — the model must reason,
    but with the pinned config + runbook it can. In the seeded-Mongo version,
    this is literally db.logs.find({"level": "ERROR"})."""
    return "\n".join(
        line for line in generate_log_dump().split("\n") if " ERROR " in line
    )


RUNBOOK = """RUNBOOK RB-114: Elevated 5xx on checkout-service
Owner: payments-platform | Last reviewed: 2026-07-30

1. Confirm blast radius: check /healthz and the uptime bot for checkout-service.
2. Rule out downstream first-glance blame: probe payment-gateway DIRECTLY
   (bypassing checkout). If the direct probe is healthy, the gateway timeouts
   you see in checkout logs are a symptom, not a cause.
3. Check DB connection pool utilization. Healthy is <70% of db_pool_max as set
   in config/checkout-service.yaml. Exhausted pools queue requests, which
   surfaces as timeouts EVERYWHERE downstream.
4. Cross-reference any anomaly with the deploy history for the last 2 hours.
   Config-only deploys are the most common silent breaker.
5. Mitigation: revert the offending deploy or restore the previous config
   value, then confirm queue depth returns to zero.
"""

SERVICE_CONFIG = """# config/checkout-service.yaml  (live values)
service: checkout-service
replicas: 6
timeouts:
  upstream_ms: 2000
  client_ms: 30000
db:
  host: orders-primary.internal
  db_pool_max: 5        # changed in deploy 8f3c2a1 (was: 50)
  pool_wait_queue: unbounded
payment_gateway:
  endpoint: https://pg.internal/v2
  retries: 3
"""

DEPLOY_HISTORY = """Recent deploys — checkout-service (last 2h)
[13:02] 8f3c2a1  "checkout-service config: tune db pool settings"   author: pcheng
[12:20] b71e9d0  "bump logging verbosity for cart module"           author: aroy
[11:45] 4c00fa2  "copy change on receipt email template"            author: mdiaz
"""

STATUS_PING_1 = ("2026-08-13T13:06Z uptime-bot: checkout-service DEGRADED — "
                 "502 rate 34% over last 5m, p95 latency 28.4s (baseline 340ms)")

STATUS_PING_2 = ("2026-08-13T13:11Z dependency-probe: payment-gateway DIRECT "
                 "probe: p50 45ms, p99 210ms, error rate 0.0% — HEALTHY from "
                 "outside checkout-service")

FINAL_QUESTION = (
    "Based on everything gathered during this incident: what is the root "
    "cause of the checkout-service 502s, and what is the exact fix? Cite the "
    "specific evidence."
)

# ── The scenario the driver replays, in order ────────────────────────────────
# expected_admission is B's free unit test for the scorer: the scorer should
# roughly reproduce this column, or its weights need tuning.
SCENARIO = {
    "episode_name": "checkout-502-incident",
    "ground_truth_diagnosis": (
        "Deploy 8f3c2a1 (13:02) reduced db_pool_max from 50 to 5 in "
        "checkout-service.yaml. The connection pool exhausts under normal "
        "load, requests queue >30s, causing timeouts and 502s. The "
        "payment-gateway timeouts are a downstream symptom (direct probe is "
        "healthy). Fix: revert 8f3c2a1 / restore db_pool_max to 50, redeploy, "
        "confirm the queue drains."
    ),
    "expected_naive_failure": (
        "Blames the payment gateway (the red-herring flood), or gives a vague "
        "'multiple timeout issues' answer without identifying the pool change."
    ),
    "turns": [
        {"turn": 1, "tool_name": "get_service_status",
         "source_type": "status_ping", "expected_admission": "archive",
         "text": STATUS_PING_1},
        {"turn": 2, "tool_name": "fetch_recent_logs",
         "source_type": "log_dump", "expected_admission": "archive",
         "text": generate_log_dump()},
        {"turn": 3, "tool_name": "fetch_runbook",
         "source_type": "runbook", "expected_admission": "pin",
         "text": RUNBOOK},
        {"turn": 4, "tool_name": "fetch_service_config",
         "source_type": "service_config", "expected_admission": "pin",
         "text": SERVICE_CONFIG},
        {"turn": 5, "tool_name": "query_error_logs",
         "source_type": "filtered_logs", "expected_admission": "pin",
         "text": generate_error_slice()},
        {"turn": 6, "tool_name": "probe_dependency",
         "source_type": "status_ping", "expected_admission": "archive",
         "text": STATUS_PING_2},
        {"turn": 7, "tool_name": "fetch_deploy_history",
         "source_type": "deploy_history", "expected_admission": "pin",
         "text": DEPLOY_HISTORY},
        {"turn": 8, "tool_name": None,
         "source_type": "final_question", "expected_admission": "none",
         "text": FINAL_QUESTION},
    ],
}


if __name__ == "__main__":
    # Sanity check: sizes per turn, so C can eyeball the noise-to-signal shape
    print(f"{'turn':<5} {'source_type':<16} {'chars':>8} {'~tokens':>8}")
    for t in SCENARIO["turns"]:
        n = len(t["text"])
        print(f"{t['turn']:<5} {t['source_type']:<16} {n:>8} {n // 4:>8}")
    dump = generate_log_dump()
    assert SMOKING_GUN in dump, "smoking gun missing from dump!"
    assert dump == generate_log_dump(), "dump is not deterministic!"
    print(f"\nsmoking gun buried at line {_GUN_INDEX} of {LOG_LINES}")
    print("scenario OK — hand to B for seeding")