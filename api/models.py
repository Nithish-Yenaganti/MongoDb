"""Validated request models for the gateway."""

from enum import Enum

from pydantic import BaseModel, Field


class Mode(str, Enum):
    baseline = "baseline"
    radixmind = "radixmind"


class ContextChunk(BaseModel):
    source_type: str = Field(min_length=1)
    text: str = Field(min_length=1)


class TurnRequest(BaseModel):
    episode_id: str = Field(min_length=1)
    turn: int = Field(ge=0)
    mode: Mode
    chunk: ContextChunk
    question: str = Field(min_length=1)
