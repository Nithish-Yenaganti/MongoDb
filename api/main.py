"""FastAPI gateway exposing POST /turn for Phase 1A."""
# mongodb/api/main.py
from __future__ import annotations

import json
import os
import time
import urllib.error
from hashlib import sha256
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from api.fireworks_client import DEFAULT_MODEL, FireworksClient
from api.metrics import MongoMetricsWriter
from api.models import Mode, TurnRequest
from api.prompt_compiler import EpisodeMemory, MemorySegment, compile_prompt
from api.scoring import PIN_THRESHOLD, score


class FireworksStream(Protocol):
    def stream(
        self, *, prompt: str, cache_key: str, model: str
    ) -> Iterator[tuple[str, Any]]: ...


class MetricsWriter(Protocol):
    def write(self, document: dict[str, Any]) -> None: ...

    def write_context(self, document: dict[str, Any]) -> None: ...


@dataclass
class TurnMetrics:
    prompt_tokens: int = 0
    cached_tokens: int = 0
    ttft_ms: float | None = None
    server_ttft_ms: float | None = None


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class TurnService:
    def __init__(
        self,
        fireworks: FireworksStream,
        metrics_writer: MetricsWriter,
        memory: EpisodeMemory | None = None,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self.fireworks = fireworks
        self.metrics_writer = metrics_writer
        self.memory = memory or EpisodeMemory()
        self.model = model

    def run(self, request: TurnRequest) -> Iterator[str]:
        item_score = score(request.chunk.text, request.chunk.source_type)
        segment = MemorySegment(
            turn=request.turn,
            source_type=request.chunk.source_type,
            text=request.chunk.text,
            score=item_score,
        )

        if request.mode is Mode.radixmind:
            try:
                if item_score >= PIN_THRESHOLD:
                    admitted, segments = self.memory.append_if_fits(
                        request.episode_id, request.mode, segment
                    )
                else:
                    admitted = False
                    segments = self.memory.snapshot(request.episode_id, request.mode)
            except ValueError as error:
                yield self._sse("error", {"detail": str(error)})
                return

            self.metrics_writer.write_context(
                {
                    "content_hash": sha256(
                        request.chunk.text.encode("utf-8")
                    ).hexdigest(),
                    "episode_id": request.episode_id,
                    "turn": request.turn,
                    "source_type": request.chunk.source_type,
                    "score": item_score,
                    "status": "pinned" if admitted else "archived",
                    "updated_at": datetime.now(timezone.utc),
                }
            )
        else:
            admitted = True
            try:
                segments = self.memory.append(request.episode_id, request.mode, segment)
            except ValueError as error:
                yield self._sse("error", {"detail": str(error)})
                return

        prompt = compile_prompt(segments, request.question, request.mode)
        cache_key = f"{request.episode_id}:{request.mode.value}"
        metrics = TurnMetrics()
        started = time.perf_counter()

        try:
            for kind, value in self.fireworks.stream(
                prompt=prompt,
                cache_key=cache_key,
                model=self.model,
            ):
                if kind == "token":
                    if metrics.ttft_ms is None:
                        metrics.ttft_ms = (time.perf_counter() - started) * 1_000
                    yield self._sse("token", {"text": value})
                elif kind == "usage":
                    metrics.prompt_tokens = int(value.get("prompt_tokens") or 0)
                    details = value.get("prompt_tokens_details") or {}
                    metrics.cached_tokens = int(details.get("cached_tokens") or 0)
                elif kind == "perf":
                    metrics.cached_tokens = int(
                        value.get("cached-prompt-tokens", metrics.cached_tokens) or 0
                    )
                    server_ttft = _number(
                        value.get("server-time-to-first-token")
                        or value.get("server_time_to_first_token")
                    )
                    if server_ttft is not None:
                        metrics.server_ttft_ms = server_ttft * 1_000
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as error:
            yield self._sse("error", {"detail": f"Fireworks request failed: {error}"})
            return

        document = {
            "episode_id": request.episode_id,
            "turn": request.turn,
            "mode": request.mode.value,
            "source_type": request.chunk.source_type,
            "score": item_score,
            "admitted": admitted,
            "model": self.model,
            "prompt_cache_key": cache_key,
            **asdict(metrics),
            "created_at": datetime.now(timezone.utc),
        }
        self.metrics_writer.write(document)
        public_metrics = {
            key: value for key, value in document.items() if key != "created_at"
        }
        yield self._sse("metrics", public_metrics)

    @staticmethod
    def _sse(event: str, data: dict[str, Any]) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def configure_service() -> TurnService:
    api_key = os.getenv("FIREWORKS_API_KEY")
    mongo_uri = os.getenv("MONGODB_URI")
    if not api_key or not mongo_uri:
        raise HTTPException(
            status_code=503,
            detail="FIREWORKS_API_KEY and MONGODB_URI must be configured",
        )
    return TurnService(
        FireworksClient(api_key),
        MongoMetricsWriter(mongo_uri),
        model=os.getenv("FIREWORKS_MODEL", DEFAULT_MODEL),
    )


def create_app(service: TurnService | None = None) -> FastAPI:
    application = FastAPI(title="RadixMind Gateway", version="0.1.0")
    if service is not None:
        application.state.turn_service = service

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/turn")
    def post_turn(request: TurnRequest) -> StreamingResponse:
        active_service = getattr(application.state, "turn_service", None)
        if active_service is None:
            active_service = configure_service()
            application.state.turn_service = active_service
        return StreamingResponse(
            active_service.run(request),
            media_type="text/event-stream",
        )

    return application


load_dotenv()
app = create_app()
