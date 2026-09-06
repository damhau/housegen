"""Live progress of one LLM call, pushed to the job's SSE subscribers.

Three transient (not persisted) event types:
  llm_progress   once per second and on every phase change:
                 { role, step, phase, tool_name, output_tokens, estimated, elapsed_s, done }
  llm_thought    coalesced reasoning-summary deltas: { role, step, text, reset }
  builder_delta  coalesced answer-text deltas (builder only): { step, text }
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from types import TracebackType

from housegen.jobs.manager import JobContext
from housegen.llm.types import ProgressEvent

logger = logging.getLogger(__name__)


class LiveProgress:
    def __init__(
        self,
        ctx: JobContext,
        role: str,
        *,
        step: int | None = None,
        show_text: bool = True,
        tick_s: float = 1.0,
        text_flush_s: float = 0.3,
    ) -> None:
        self.ctx = ctx
        self.role = role
        self.step = step
        self.show_text = show_text
        self.tick_s = tick_s
        self.text_flush_s = text_flush_s
        self.phase: str = "thinking"
        self.tool_name: str | None = None
        self.output_tokens: int | None = None
        self.output_chars = 0
        self._text_buf = ""
        self._thought_buf = ""
        self._last_flush = 0.0
        self._started = 0.0
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> LiveProgress:
        self._started = time.monotonic()
        self._last_flush = self._started
        # a new call starts: tell the UI to drop the previous call's thought text
        self.ctx.emit_transient("llm_thought", role=self.role, step=self.step, text="", reset=True)
        self._emit_tick()
        self._task = asyncio.create_task(self._ticker(), name=f"progress-{self.role}")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._flush()
        self._emit_tick(done=True)

    async def on_event(self, ev: ProgressEvent) -> None:
        if ev.kind == "phase" and ev.phase:
            changed = ev.phase != self.phase or ev.tool_name != self.tool_name
            self.phase = ev.phase
            self.tool_name = ev.tool_name
            if changed:
                self._flush()
                self._emit_tick()
        if ev.output_tokens is not None:
            self.output_tokens = ev.output_tokens
        if ev.output_chars is not None:
            self.output_chars = ev.output_chars
        if ev.kind == "text" and ev.text and self.show_text:
            self._text_buf += ev.text
        elif ev.kind == "thought" and ev.text:
            self._thought_buf += ev.text
        if (self._text_buf or self._thought_buf) and (
            time.monotonic() - self._last_flush >= self.text_flush_s
        ):
            self._flush()

    async def _ticker(self) -> None:
        while True:
            await asyncio.sleep(self.tick_s)
            self._emit_tick()

    def _flush(self) -> None:
        if self._thought_buf:
            self.ctx.emit_transient(
                "llm_thought", role=self.role, step=self.step, text=self._thought_buf, reset=False
            )
            self._thought_buf = ""
        if self._text_buf:
            self.ctx.emit_transient("builder_delta", step=self.step, text=self._text_buf)
            self._text_buf = ""
        self._last_flush = time.monotonic()

    def _emit_tick(self, done: bool = False) -> None:
        estimated = self.output_tokens is None
        tokens = self.output_tokens if self.output_tokens is not None else self.output_chars // 4
        self.ctx.emit_transient(
            "llm_progress",
            role=self.role,
            step=self.step,
            phase=self.phase,
            tool_name=self.tool_name,
            output_tokens=tokens,
            estimated=estimated,
            elapsed_s=round(time.monotonic() - self._started, 1),
            done=done,
        )
