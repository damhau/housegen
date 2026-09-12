from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

MediaType = Literal["image/jpeg", "image/png", "image/webp"]


class TextPart(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImagePart(BaseModel):
    type: Literal["image"] = "image"
    media_type: MediaType
    data: str  # base64
    label: str | None = None  # informative caption, rendered as text next to the image

    @classmethod
    def from_bytes(cls, raw: bytes, media_type: MediaType, label: str | None = None) -> ImagePart:
        return cls(
            media_type=media_type, data=base64.standard_b64encode(raw).decode("ascii"), label=label
        )

    @classmethod
    def from_file(cls, path: Path, label: str | None = None) -> ImagePart:
        suffix = path.suffix.lower()
        media: MediaType = (
            "image/png" if suffix == ".png" else "image/webp" if suffix == ".webp" else "image/jpeg"
        )
        return cls.from_bytes(path.read_bytes(), media, label)


class ToolCallPart(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    input: dict[str, Any]


class ToolResultPart(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str
    content: list[TextPart | ImagePart]
    is_error: bool = False


Part = TextPart | ImagePart | ToolCallPart | ToolResultPart


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: list[Part]
    # Provider-private replay data for assistant turns (e.g. OpenAI Responses output items
    # including reasoning). Only the provider that produced it reads it; others ignore it.
    raw: list[dict[str, Any]] | None = None
    raw_provider: str | None = None

    @classmethod
    def user(cls, *parts: Part | str) -> Message:
        return cls(
            role="user", content=[TextPart(text=p) if isinstance(p, str) else p for p in parts]
        )

    @classmethod
    def assistant(cls, *parts: Part | str) -> Message:
        return cls(
            role="assistant", content=[TextPart(text=p) if isinstance(p, str) else p for p in parts]
        )

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.content if isinstance(p, TextPart))

    @property
    def tool_calls(self) -> list[ToolCallPart]:
        return [p for p in self.content if isinstance(p, ToolCallPart)]


class ModelInfo(BaseModel):
    """One model a provider offers (for the settings sheet's model list)."""

    id: str
    display_name: str | None = None
    created_at: datetime | None = None


class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class Usage(BaseModel):
    """Token counts of one call (or a sum of calls).

    `input_tokens` is the whole prompt, cached part included (OpenAI convention; the
    Anthropic provider adds the cache reads/writes back in). `reasoning_tokens` are the
    share of `output_tokens` spent thinking where the provider reports it (OpenAI;
    Anthropic counts thinking inside output_tokens without a split).
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )


class ProgressEvent(BaseModel):
    """Streaming progress from a provider while a completion is in flight."""

    kind: Literal["phase", "text", "thought", "tokens"]
    phase: Literal["thinking", "writing", "tool_call"] | None = None
    tool_name: str | None = None
    text: str | None = None  # text delta (kind == "text") or reasoning-summary delta ("thought")
    output_tokens: int | None = None  # exact cumulative count when the provider reports it
    output_chars: int | None = None  # cumulative characters received (for estimates)


ProgressCallback = Callable[[ProgressEvent], Awaitable[None]]


class Completion(BaseModel):
    message: Message
    stop_reason: Literal["end_turn", "tool_use", "max_tokens", "refusal", "other"]
    usage: Usage = Field(default_factory=Usage)
    model: str = ""
    raw_stop_reason: str | None = None
    # timing of the call (#13): wall time, and how much of it passed before the first
    # visible output (text or tool call) started streaming, i.e. spent reasoning
    duration_ms: int = 0
    thinking_ms: int = 0

    @property
    def tool_calls(self) -> list[ToolCallPart]:
        return self.message.tool_calls
