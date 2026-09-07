"""Cost and duration estimate before a run (#18), from the project's own history.

The last finished jobs of the same kind give the average wall time and token profile; the
first run of a project (or a kind never run) falls back to a typical profile. Tokens are
priced with the model the project is set to run with, scaled by the step budget.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from housegen.agent.metrics import RunSummary
from housegen.agent.run_settings import ResolvedRunSettings
from housegen.core.config import Settings
from housegen.projects.models import Job

Kind = Literal["intake", "generate", "modify"]
SAMPLES = 5
# typical profiles at the default 60-step budget, from the runs of 2026-09 (mostly cached input)
DEFAULT_PROFILE: dict[str, dict[str, int]] = {
    "generate": {"wall_ms": 35 * 60_000, "input": 6_000_000, "cached": 5_400_000, "output": 90_000},
    "modify": {"wall_ms": 10 * 60_000, "input": 1_500_000, "cached": 1_300_000, "output": 25_000},
    "intake": {"wall_ms": 2 * 60_000, "input": 25_000, "cached": 0, "output": 4_000},
}
DEFAULT_STEPS = 60


class Estimate(BaseModel):
    kind: Kind
    minutes: int
    cost_usd: float | None  # None when the model has no price
    basis: Literal["history", "default"]
    samples: int  # finished runs of this kind the estimate averages
    model: str


def _profile(jobs: list[Job], kind: str) -> tuple[dict[str, float], int, int]:
    """(average wall/tokens, samples, steps the samples ran with)."""
    picked = [j for j in jobs if j.kind == kind and j.status == "done" and j.metrics][:SAMPLES]
    if not picked:
        return dict(DEFAULT_PROFILE[kind]), 0, DEFAULT_STEPS
    totals = {"wall_ms": 0.0, "input": 0.0, "cached": 0.0, "output": 0.0}
    steps = 0
    for j in picked:
        m = RunSummary.model_validate(j.metrics)
        totals["wall_ms"] += m.wall_ms
        totals["input"] += m.builder.input_tokens + m.critic.input_tokens
        totals["cached"] += m.builder.cached_tokens + m.critic.cached_tokens
        totals["output"] += m.builder.output_tokens + m.critic.output_tokens
        settings = j.settings or {}
        steps += int(settings.get("max_steps") or DEFAULT_STEPS)
    n = len(picked)
    return {k: v / n for k, v in totals.items()}, n, round(steps / n)


def estimate(env: Settings, rs: ResolvedRunSettings, kind: Kind, jobs: list[Job]) -> Estimate:
    profile, samples, steps = _profile(jobs, kind)
    # a bigger step budget makes the builder work (and pay) longer, within reason
    scale = min(2.0, max(0.5, rs.max_steps / steps)) if kind != "intake" else 1.0
    price = env.price_for(rs.model)
    cost: float | None = None
    if price is not None:
        uncached = max(0.0, profile["input"] - profile["cached"])
        cost = (
            (
                uncached * price["input"]
                + profile["cached"] * price["cached"]
                + profile["output"] * price["output"]
            )
            / 1_000_000
            * scale
        )
        cost = round(cost, 2)
    return Estimate(
        kind=kind,
        minutes=max(1, round(profile["wall_ms"] * scale / 60_000)),
        cost_usd=cost,
        basis="history" if samples else "default",
        samples=samples,
        model=rs.model,
    )
