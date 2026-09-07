"""Where a run spends its time and tokens (#13).

One `TurnMetric` per model call (a builder step, a critic round, the intake), persisted as a
`turn` job event as it happens; a `RunSummary` per job, computed from the turns at the end
and stored on the job (`metrics_json`). Cost uses the per-model price table in the settings.
"""

from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, Field

from housegen.core.config import Settings

Role = Literal["builder", "critic", "intake"]
EDIT_TOOLS = {"write_file", "edit_file", "apply_patch", "delete_file"}
RENDER_TOOLS = {"render_views", "check_scene"}
# a builder turn whose prompt is this big and mostly uncached lost the provider's prefix cache
CACHE_MISS_MIN_INPUT = 20_000
CACHE_MISS_HIT_RATIO = 0.5


class ToolCallMetric(BaseModel):
    name: str
    args: str = ""
    duration_ms: int = 0
    ok: bool = True


class TurnMetric(BaseModel):
    step: int
    role: Role
    model: str = ""
    duration_ms: int = 0  # the model call, wall time
    thinking_ms: int = 0  # part of duration_ms before the first visible output
    tools_ms: int = 0  # tool execution, renders included
    render_ms: int = 0  # the renders' share of tools_ms
    input_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    tool_calls: list[ToolCallMetric] = Field(default_factory=list)

    @property
    def cache_hit(self) -> float:
        return self.cached_tokens / self.input_tokens if self.input_tokens else 0.0

    @property
    def cache_miss(self) -> bool:
        """A big prompt that should have been cached and was not (never the first step)."""
        return (
            self.role == "builder"
            and self.step > 1
            and self.input_tokens >= CACHE_MISS_MIN_INPUT
            and self.cache_hit < CACHE_MISS_HIT_RATIO
        )

    @property
    def edits(self) -> int:
        return sum(1 for c in self.tool_calls if c.name in EDIT_TOOLS)

    @property
    def renders(self) -> int:
        return sum(1 for c in self.tool_calls if c.name in RENDER_TOOLS)

    def cost_usd(self, settings: Settings) -> float | None:
        price = settings.price_for(self.model) if self.model else None
        if price is None:
            return None
        uncached = max(0, self.input_tokens - self.cached_tokens - self.cache_write_tokens)
        write_price = price.get("cache_write", price["input"])
        return (
            uncached * price["input"]
            + self.cached_tokens * price["cached"]
            + self.cache_write_tokens * write_price
            + self.output_tokens * price["output"]
        ) / 1_000_000

    def event_payload(self) -> dict[str, object]:
        """What the persisted `turn` event carries (the UI groups the timeline by it)."""
        return {
            **self.model_dump(),
            "cache_hit": round(self.cache_hit, 3),
            "cache_miss": self.cache_miss,
            "edits": self.edits,
        }


class PhaseSummary(BaseModel):
    turns: int = 0
    llm_ms: int = 0
    thinking_ms: int = 0
    writing_ms: int = 0
    tools_ms: int = 0
    render_ms: int = 0
    renders: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_miss_turns: int = 0
    cost_usd: float | None = None


class RunSummary(BaseModel):
    """Totals of a job, by phase and by activity."""

    wall_ms: int = 0
    turns: int = 0
    builder: PhaseSummary = Field(default_factory=PhaseSummary)
    critic: PhaseSummary = Field(default_factory=PhaseSummary)  # critic + intake calls
    edits: int = 0
    edits_per_turn_avg: float = 0.0
    edits_per_turn_max: int = 0
    single_edit_turns: int = 0  # builder turns that made exactly one edit and nothing else
    cost_usd: float | None = None  # None when a model has no price
    models: list[str] = Field(default_factory=list)


class RunMetrics:
    """Accumulates the turns of one job and summarises them."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.started = time.monotonic()
        self.turns: list[TurnMetric] = []

    def add(self, turn: TurnMetric) -> TurnMetric:
        self.turns.append(turn)
        return turn

    def summary(self) -> RunSummary:
        out = RunSummary(wall_ms=int((time.monotonic() - self.started) * 1000))
        priced = True
        total_cost = 0.0
        models: list[str] = []
        edit_counts: list[int] = []
        for t in self.turns:
            phase = out.builder if t.role == "builder" else out.critic
            phase.turns += 1
            phase.llm_ms += t.duration_ms
            phase.thinking_ms += t.thinking_ms
            phase.writing_ms += max(0, t.duration_ms - t.thinking_ms)
            phase.tools_ms += t.tools_ms
            phase.render_ms += t.render_ms
            phase.renders += t.renders
            phase.input_tokens += t.input_tokens
            phase.cached_tokens += t.cached_tokens
            phase.output_tokens += t.output_tokens
            phase.reasoning_tokens += t.reasoning_tokens
            if t.cache_miss:
                phase.cache_miss_turns += 1
            cost = t.cost_usd(self.settings)
            if cost is None:
                priced = False
            else:
                total_cost += cost
                phase.cost_usd = (phase.cost_usd or 0.0) + cost
            if t.model and t.model not in models:
                models.append(t.model)
            if t.role == "builder":
                edit_counts.append(t.edits)
                if t.edits == 1 and len(t.tool_calls) == 1:
                    out.single_edit_turns += 1
        out.turns = len(self.turns)
        out.edits = sum(edit_counts)
        out.edits_per_turn_avg = (
            round(sum(edit_counts) / len(edit_counts), 2) if edit_counts else 0.0
        )
        out.edits_per_turn_max = max(edit_counts, default=0)
        out.cost_usd = round(total_cost, 4) if priced and self.turns else None
        if not priced:
            out.builder.cost_usd = None
            out.critic.cost_usd = None
        out.models = models
        return out
