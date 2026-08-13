"""Deterministic A/B/C prompt compilation and episode memory."""

from dataclasses import dataclass
from threading import Lock

from api.models import Mode


SEGMENT_A = """SEGMENT A — STABLE INSTRUCTIONS
You are an incident-response assistant. Use only the supplied episode memory.
Separate symptoms from causes, preserve diagnostic evidence, and answer concisely.
"""


@dataclass(frozen=True)
class MemorySegment:
    turn: int
    source_type: str
    text: str
    score: float


class EpisodeMemory:
    """Process-local, append-only Segment B memory keyed by episode and mode."""

    def __init__(self) -> None:
        self._segments: dict[tuple[str, Mode], list[MemorySegment]] = {}
        self._lock = Lock()

    def append(
        self, episode_id: str, mode: Mode, segment: MemorySegment
    ) -> list[MemorySegment]:
        key = (episode_id, mode)
        with self._lock:
            current = self._segments.setdefault(key, [])
            if current and segment.turn <= current[-1].turn:
                raise ValueError("turn must be greater than the previous turn")
            current.append(segment)
            return list(current)

    def snapshot(self, episode_id: str, mode: Mode) -> list[MemorySegment]:
        with self._lock:
            return list(self._segments.get((episode_id, mode), []))


def compile_prompt(segments: list[MemorySegment], question: str) -> str:
    """Build stable Segment A, append-only Segment B, and dynamic Segment C."""
    segment_b = ["SEGMENT B — APPEND-ONLY EPISODE MEMORY"]
    for item in segments:
        segment_b.append(
            f"TURN {item.turn} | SOURCE {item.source_type} | SCORE {item.score:.3f}\n"
            f"{item.text}"
        )
    segment_c = f"SEGMENT C — CURRENT QUESTION\n{question}"
    return "\n\n".join([SEGMENT_A.rstrip(), *segment_b, segment_c])
