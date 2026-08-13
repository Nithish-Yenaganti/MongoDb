"""Minimal Fireworks streaming client for the gateway."""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Iterator
from typing import Any


FIREWORKS_URL = "https://api.fireworks.ai/inference/v1/chat/completions"
DEFAULT_MODEL = "accounts/fireworks/models/gpt-oss-20b"


class FireworksClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def stream(
        self, *, prompt: str, cache_key: str, model: str
    ) -> Iterator[tuple[str, Any]]:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "max_tokens": 160,
            "temperature": 0,
            "prompt_cache_key": cache_key,
            "perf_metrics_in_response": True,
        }
        request = urllib.request.Request(
            FIREWORKS_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            method="POST",
        )

        with urllib.request.urlopen(request, timeout=120) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue

                event = json.loads(data)
                choices = event.get("choices") or []
                if choices:
                    delta = choices[0].get("delta") or {}
                    text = delta.get("content") or delta.get("reasoning_content")
                    if text:
                        yield "token", text
                if event.get("usage"):
                    yield "usage", event["usage"]
                if event.get("perf_metrics"):
                    yield "perf", event["perf_metrics"]
