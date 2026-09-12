"""Per-project run settings (#18): model, effort, critic rounds, step budget, render quality.

`RunSettings` is what the owner stores on a project (every field optional: unset means the
`.env` default). `resolve()` turns it into the complete `ResolvedRunSettings` a job runs with,
snapshotted on the job at start so a settings change never affects a running job and the
history shows what a version was made with. Effort values the current provider does not have
are mapped to the nearest one, never rejected (`max` → `xhigh` on OpenAI, `none`/`minimal` →
`low` on Anthropic), and the mapping is reported in `notes`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from housegen.core.config import Settings

Provider = Literal["anthropic", "openai"]
Effort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]
Quality = Literal["low", "medium", "high"]
Preset = Literal["quick", "full", "custom"]

EFFORTS: list[Effort] = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
# what each provider accepts; the rest is mapped to the nearest level
PROVIDER_EFFORTS: dict[str, list[Effort]] = {
    "openai": ["none", "minimal", "low", "medium", "high", "xhigh"],
    "anthropic": ["low", "medium", "high", "xhigh", "max"],
}
DEFAULT_MODELS: dict[str, str] = {"anthropic": "claude-opus-5", "openai": "gpt-6-astra"}

# the two presets of the settings sheet
PRESETS: dict[str, dict[str, object]] = {
    "quick": {"builder_effort": "medium", "critic_rounds": 1, "max_steps": 30},
    "full": {"builder_effort": "xhigh", "critic_rounds": 2, "max_steps": 60},
}


class RunSettings(BaseModel):
    """What the owner sets on a project; unset fields fall back to the `.env` defaults."""

    provider: Provider | None = None
    model: str | None = Field(default=None, max_length=100)
    builder_effort: Effort | None = None
    critic_effort: Effort | None = None
    critic_rounds: int | None = Field(default=None, ge=0, le=5)
    max_steps: int | None = Field(default=None, ge=5, le=200)
    # quality of the builder's in-loop renders (the saved version is always rendered at high)
    render_quality: Quality | None = None

    def preset(self) -> Preset:
        for name, values in PRESETS.items():
            if all(getattr(self, k) == v for k, v in values.items()):
                return name  # type: ignore[return-value]
        return "custom"


class ResolvedRunSettings(BaseModel):
    """The complete settings a job runs with."""

    provider: Provider
    model: str
    critic_model: str
    builder_effort: Effort
    critic_effort: Effort
    critic_rounds: int
    max_steps: int
    render_quality: Quality
    max_tokens: int
    preset: Preset = "custom"
    # how values were mapped onto the provider ("max is not available on OpenAI: using xhigh")
    notes: list[str] = Field(default_factory=list)


def nearest_effort(effort: Effort, provider: str) -> Effort:
    """The provider's closest effort level (its floor or ceiling when out of range)."""
    allowed = PROVIDER_EFFORTS.get(provider, EFFORTS)
    if effort in allowed:
        return effort
    i = EFFORTS.index(effort)
    below = [e for e in allowed if EFFORTS.index(e) < i]
    above = [e for e in allowed if EFFORTS.index(e) > i]
    return below[-1] if below else above[0]


def resolve(env: Settings, overrides: RunSettings | None = None) -> ResolvedRunSettings:
    o = overrides or RunSettings()
    provider: Provider = o.provider or env.LLM_PROVIDER
    # the .env model only applies to its own provider; another provider gets its default
    env_model = env.BUILDER_MODEL if provider == env.LLM_PROVIDER else None
    model = o.model or env_model or DEFAULT_MODELS[provider]
    env_critic = env.CRITIC_MODEL if provider == env.LLM_PROVIDER else None
    # a chosen model applies to both roles unless .env names a critic model of this provider
    critic_model = o.model or env_critic or env_model or DEFAULT_MODELS[provider]
    notes: list[str] = []
    efforts: dict[str, Effort] = {}
    for role, wanted in (
        ("builder", o.builder_effort or env.BUILDER_EFFORT),
        ("critic", o.critic_effort or env.CRITIC_EFFORT),
    ):
        got = nearest_effort(wanted, provider)
        if got != wanted:
            notes.append(f"{role} effort {wanted} is not available on {provider}: using {got}")
        efforts[role] = got
    resolved = ResolvedRunSettings(
        provider=provider,
        model=model,
        critic_model=critic_model,
        builder_effort=efforts["builder"],
        critic_effort=efforts["critic"],
        critic_rounds=o.critic_rounds if o.critic_rounds is not None else env.CRITIC_MAX_ITERATIONS,
        max_steps=o.max_steps if o.max_steps is not None else env.BUILDER_MAX_STEPS,
        render_quality=o.render_quality or env.RENDER_QUALITY,
        max_tokens=env.LLM_MAX_TOKENS,
        notes=notes,
    )
    resolved.preset = RunSettings(
        builder_effort=resolved.builder_effort,
        critic_rounds=resolved.critic_rounds,
        max_steps=resolved.max_steps,
    ).preset()
    return resolved
