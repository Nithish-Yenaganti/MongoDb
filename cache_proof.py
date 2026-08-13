#!/usr/bin/env python3
"""Phase 0A: prove Fireworks prompt-prefix caching with two streamed calls."""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any


API_URL = "https://api.fireworks.ai/inference/v1/chat/completions"
DEFAULT_MODEL = "accounts/fireworks/models/gpt-oss-20b"


@dataclass
class CallMetrics:
    prompt_tokens: int
    cached_tokens: int
    client_ttft_seconds: float | None
    server_ttft_seconds: float | None


def load_env_file(path: Path) -> None:
    """Load simple KEY=VALUE entries without changing or printing the file."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def make_prefix(run_id: str, word_count: int = 2_000) -> str:
    """Create a stable, exactly word_count-word incident prefix for this run."""
    seed = f"""
    Incident cache proof {run_id}. The checkout API is returning intermittent five
    hundred errors after a configuration rollout. Operators collected application
    logs, deployment history, dependency health, database observations, status
    messages, and a recovery runbook. CPU and memory remain normal while request
    latency rises. Most log lines repeat harmless health checks, but one diagnostic
    line reports that the database connection pool is exhausted. Preserve evidence,
    distinguish symptoms from causes, ignore repeated noise, and answer only from
    this supplied incident context. The rollback procedure requires confirming the
    active release, restoring the previous pool limit, restarting one replica at a
    time, and checking error rate before continuing. No credentials or customer data
    are included in this synthetic scenario.
    """.split()
    return " ".join((seed * ((word_count // len(seed)) + 1))[:word_count])


def nested_number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def metric(perf: dict[str, Any], *names: str) -> float | None:
    for name in names:
        if name in perf:
            parsed = nested_number(perf[name])
            if parsed is not None:
                return parsed
    return None


def stream_call(
    *, api_key: str, model: str, prefix: str, question: str, cache_key: str, number: int
) -> CallMetrics:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": prefix},
            {"role": "user", "content": question},
        ],
        "stream": True,
        "max_tokens": 40,
        "temperature": 0,
        "prompt_cache_key": cache_key,
        "perf_metrics_in_response": True,
    }
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )

    started = time.perf_counter()
    client_ttft: float | None = None
    usage: dict[str, Any] = {}
    perf: dict[str, Any] = {}

    print(f"\n--- CALL {number} ---")
    with urllib.request.urlopen(request, timeout=120) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue

            event = json.loads(payload)
            choices = event.get("choices") or []
            if choices:
                delta = choices[0].get("delta") or {}
                text = delta.get("content") or delta.get("reasoning_content")
                if text:
                    if client_ttft is None:
                        client_ttft = time.perf_counter() - started
                    print(text, end="", flush=True)

            if event.get("usage"):
                usage = event["usage"]
            if event.get("perf_metrics"):
                perf = event["perf_metrics"]

    details = usage.get("prompt_tokens_details") or {}
    cached = details.get("cached_tokens")
    if cached is None:
        cached = metric(perf, "cached-prompt-tokens", "cached_prompt_tokens") or 0

    server_ttft = metric(
        perf,
        "server-time-to-first-token",
        "server_time_to_first_token",
    )
    result = CallMetrics(
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        cached_tokens=int(cached),
        client_ttft_seconds=client_ttft,
        server_ttft_seconds=server_ttft,
    )

    print()
    print(f"prompt_tokens={result.prompt_tokens}")
    print(f"cached_tokens={result.cached_tokens}")
    print(
        "client_ttft_seconds="
        + (f"{client_ttft:.3f}" if client_ttft is not None else "unavailable")
    )
    print(
        "server_ttft_seconds="
        + (f"{server_ttft:.3f}" if server_ttft is not None else "unavailable")
    )
    return result


def main() -> int:
    load_env_file(Path(".env"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        default=os.getenv("FIREWORKS_MODEL", DEFAULT_MODEL),
        help="Fireworks serverless model ID",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the inputs without making API calls",
    )
    args = parser.parse_args()

    run_id = uuid.uuid4().hex
    prefix = make_prefix(run_id)
    cache_key = f"phase0-cache-proof-{run_id}"
    print(f"model={args.model}")
    print(f"shared_prefix_words={len(prefix.split())}")
    print(f"prompt_cache_key={cache_key}")

    if args.dry_run:
        print("DRY RUN PASS: prefix is exactly 2,000 words; no API call was made.")
        return 0

    api_key = os.getenv("FIREWORKS_API_KEY")
    if not api_key:
        if not sys.stdin.isatty():
            print(
                "ERROR: FIREWORKS_API_KEY is absent from the shell and .env, and "
                "secure prompting requires an interactive terminal. The .env file "
                "was not changed.",
                file=sys.stderr,
            )
            return 2
        api_key = getpass.getpass("Paste FIREWORKS_API_KEY (hidden): ").strip()
        if not api_key:
            print("ERROR: No API key supplied; nothing was sent.", file=sys.stderr)
            return 2

    try:
        first = stream_call(
            api_key=api_key,
            model=args.model,
            prefix=prefix,
            question="What is the main incident symptom? Answer in one sentence.",
            cache_key=cache_key,
            number=1,
        )
        second = stream_call(
            api_key=api_key,
            model=args.model,
            prefix=prefix,
            question="What should responders investigate next? Answer in one sentence.",
            cache_key=cache_key,
            number=2,
        )
    except urllib.error.HTTPError as error:
        message = error.read().decode("utf-8", errors="replace")
        print(f"\nHTTP {error.code}: {message}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        print(f"\nREQUEST FAILED: {error}", file=sys.stderr)
        return 1

    cache_ratio = (
        second.cached_tokens / second.prompt_tokens if second.prompt_tokens else 0.0
    )
    client_faster = (
        first.client_ttft_seconds is not None
        and second.client_ttft_seconds is not None
        and second.client_ttft_seconds < first.client_ttft_seconds
    )
    server_faster = (
        first.server_ttft_seconds is not None
        and second.server_ttft_seconds is not None
        and second.server_ttft_seconds < first.server_ttft_seconds
    )

    print("\n=== GATE 1 RESULT ===")
    print(f"call_1_cached_tokens={first.cached_tokens}")
    print(f"call_2_cached_tokens={second.cached_tokens}")
    print(f"call_2_cache_ratio={cache_ratio:.1%}")
    print(f"client_ttft_faster={client_faster}")
    print(f"server_ttft_faster={server_faster}")

    if second.cached_tokens > 0 and cache_ratio >= 0.60:
        print("PASS: call two reused most of the shared prompt prefix.")
        if not (client_faster or server_faster):
            print("NOTE: TTFT was not faster; shared-server latency can vary between calls.")
        return 0

    print("FAIL: cache reuse was not large enough; retry once with another serverless model.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
