"""LiveProgress: ticks, phase changes and coalesced text deltas as transient events."""

from __future__ import annotations

import asyncio
from typing import Any

from housegen.agent.progress import LiveProgress
from housegen.llm.types import ProgressEvent


class StubCtx:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit_transient(self, type_: str, **payload: Any) -> None:
        self.events.append((type_, payload))


async def test_progress_emits_ticks_phases_and_text() -> None:
    ctx = StubCtx()
    async with LiveProgress(ctx, "builder", step=3, tick_s=0.05, text_flush_s=0.0) as live:  # type: ignore[arg-type]
        await live.on_event(ProgressEvent(kind="phase", phase="thinking"))
        await live.on_event(ProgressEvent(kind="thought", text="Reading the "))
        await live.on_event(ProgressEvent(kind="thought", text="south elevation"))
        await asyncio.sleep(0.12)  # a couple of ticks while "thinking"
        await live.on_event(ProgressEvent(kind="phase", phase="writing"))
        await live.on_event(ProgressEvent(kind="text", text="hello ", output_chars=6))
        await live.on_event(ProgressEvent(kind="text", text="world", output_chars=11))
        await live.on_event(ProgressEvent(kind="tokens", output_tokens=42, output_chars=11))
        await live.on_event(ProgressEvent(kind="phase", phase="tool_call", tool_name="write_file"))

    types = [t for t, _ in ctx.events]
    assert types.count("llm_progress") >= 3
    deltas = [p["text"] for t, p in ctx.events if t == "builder_delta"]
    assert "".join(deltas) == "hello world"
    thoughts = [p for t, p in ctx.events if t == "llm_thought"]
    assert thoughts[0]["reset"] is True  # first event of the call clears the previous one
    assert "".join(p["text"] for p in thoughts) == "Reading the south elevation"
    last = [p for t, p in ctx.events if t == "llm_progress"][-1]
    assert last["done"] is True
    assert last["phase"] == "tool_call"
    assert last["tool_name"] == "write_file"
    assert last["output_tokens"] == 42
    assert last["estimated"] is False
    assert last["role"] == "builder"
    assert last["step"] == 3
    assert all(p["elapsed_s"] >= 0 for t, p in ctx.events if t == "llm_progress")


async def test_progress_estimates_tokens_from_chars_when_unknown() -> None:
    ctx = StubCtx()
    async with LiveProgress(ctx, "analyst", show_text=False, tick_s=10) as live:  # type: ignore[arg-type]
        await live.on_event(ProgressEvent(kind="text", text="x" * 400, output_chars=400))
    assert not [t for t, _ in ctx.events if t == "builder_delta"]  # show_text=False
    last = [p for t, p in ctx.events if t == "llm_progress"][-1]
    assert last["estimated"] is True
    assert last["output_tokens"] == 100
