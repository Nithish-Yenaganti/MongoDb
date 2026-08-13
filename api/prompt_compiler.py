"""A/B/C prompt compilation and bounded episode memory."""
# mongodb/api/prompt_compiler.py
import random
import time
from dataclasses import dataclass
from math import ceil
from threading import Lock

from api.models import Mode


SEGMENT_A = """SEGMENT A — STABLE INSTRUCTIONS
You are an incident-response assistant. Use only the supplied episode memory.
Separate symptoms from causes, preserve diagnostic evidence, and answer concisely.
"""

SEGMENT_B_TOKEN_BUDGET = 10_000


def estimate_tokens(text: str) -> int:
    """Conservative tokenizer-free estimate used for admission control."""
    return ceil(len(text.encode("utf-8")) / 4)


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

    def append_if_fits(
        self,
        episode_id: str,
        mode: Mode,
        segment: MemorySegment,
        token_budget: int = SEGMENT_B_TOKEN_BUDGET,
    ) -> tuple[bool, list[MemorySegment]]:
        """Append a segment only when Segment B remains within its budget."""
        key = (episode_id, mode)
        with self._lock:
            current = self._segments.setdefault(key, [])
            if current and segment.turn <= current[-1].turn:
                raise ValueError("turn must be greater than the previous turn")
            candidate = [*current, segment]
            if segment_b_tokens(candidate) > token_budget:
                return False, list(current)
            current.append(segment)
            return True, list(current)

    def snapshot(self, episode_id: str, mode: Mode) -> list[MemorySegment]:
        with self._lock:
            return list(self._segments.get((episode_id, mode), []))


def segment_b_text(segments: list[MemorySegment]) -> str:
    segment_b = ["SEGMENT B — APPEND-ONLY EPISODE MEMORY"]
    for item in segments:
        segment_b.append(
            f"TURN {item.turn} | SOURCE {item.source_type} | SCORE {item.score:.3f}\n"
            f"{item.text}"
        )
    return "\n\n".join(segment_b)


def segment_b_tokens(segments: list[MemorySegment]) -> int:
    return estimate_tokens(segment_b_text(segments))


def compile_prompt(
    segments: list[MemorySegment], question: str, mode: Mode = Mode.radixmind
) -> str:
    """Build stable RadixMind prompts and deliberately unstable naive prompts."""
    prompt_segments = list(segments)
    if mode is not Mode.radixmind:
        random.SystemRandom().shuffle(prompt_segments)
        compiled_at = time.time_ns()
        segment_b = [f"SEGMENT B — NAIVE MEMORY | COMPILED_AT {compiled_at}"]
        for item in prompt_segments:
            segment_b.append(
                f"OBSERVED_AT {time.time_ns()} | TURN {item.turn} | "
                f"SOURCE {item.source_type} | SCORE {item.score:.3f}\n{item.text}"
            )
        segment_b_text_value = "\n\n".join(segment_b)
    else:
        segment_b_text_value = segment_b_text(prompt_segments)
    segment_c = f"SEGMENT C — CURRENT QUESTION\n{question}"
    return "\n\n".join([SEGMENT_A.rstrip(), segment_b_text_value, segment_c])
