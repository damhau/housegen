"""Per-project run settings and the estimate (#18)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from housegen.agent.estimate import estimate
from housegen.agent.run_settings import RunSettings, nearest_effort, resolve
from housegen.core import db
from housegen.core.config import Settings, get_settings
from housegen.core.db import session_factory
from housegen.jobs.manager import job_manager
from housegen.projects import crud


def test_effort_is_mapped_to_the_nearest_level_never_rejected() -> None:
    assert nearest_effort("max", "openai") == "xhigh"
    assert nearest_effort("none", "anthropic") == "low"
    assert nearest_effort("minimal", "anthropic") == "low"
    assert nearest_effort("high", "openai") == "high"
    env = Settings(LLM_PROVIDER="openai", BUILDER_EFFORT="max", CRITIC_EFFORT="medium")
    rs = resolve(env)
    assert rs.builder_effort == "xhigh"
    assert rs.notes == ["builder effort max is not available on openai: using xhigh"]
    assert rs.model == "gpt-6-astra"
    assert rs.preset == "full"  # xhigh, 2 rounds, 60 steps


def test_overrides_and_presets() -> None:
    env = Settings(
        LLM_PROVIDER="anthropic", BUILDER_MODEL="claude-opus-5", CRITIC_MODEL="claude-sonnet-5"
    )
    quick = resolve(env, RunSettings(builder_effort="medium", critic_rounds=1, max_steps=30))
    assert quick.preset == "quick"
    assert quick.critic_model == "claude-sonnet-5"  # .env critic model of the same provider
    other = resolve(env, RunSettings(provider="openai", builder_effort="minimal"))
    assert other.model == "gpt-6-astra"  # the .env model belongs to the other provider
    assert other.critic_model == "gpt-6-astra"
    assert other.builder_effort == "minimal"
    named = resolve(env, RunSettings(model="claude-sonnet-5", render_quality="low"))
    assert named.model == "claude-sonnet-5"
    assert named.critic_model == "claude-sonnet-5"
    assert named.render_quality == "low"
    assert named.preset == "full"


class _Job:
    def __init__(
        self, kind: str, wall_ms: int, tokens: tuple[int, int, int], steps: int = 60
    ) -> None:
        self.kind = kind
        self.status = "done"
        self.metrics = {
            "wall_ms": wall_ms,
            "builder": {
                "input_tokens": tokens[0],
                "cached_tokens": tokens[1],
                "output_tokens": tokens[2],
            },
            "critic": {},
        }
        self.settings = {"max_steps": steps}


def test_estimate_from_history_and_defaults() -> None:
    env = Settings(MODEL_PRICES={"m": {"input": 10.0, "cached": 1.0, "output": 100.0}})
    rs = resolve(env, RunSettings(model="m", max_steps=60))
    jobs = [
        _Job("generate", 20 * 60_000, (1_000_000, 900_000, 10_000)),
        _Job("generate", 10 * 60_000, (500_000, 400_000, 5_000)),
        _Job("modify", 5 * 60_000, (100_000, 0, 1_000)),
    ]
    e = estimate(env, rs, "generate", jobs)  # type: ignore[arg-type]
    assert e.basis == "history"
    assert e.samples == 2
    assert e.minutes == 15
    # avg 750k in (650k cached) + 7.5k out: 100k*10 + 650k*1 + 7.5k*100 = 1.0 + 0.65 + 0.75
    assert e.cost_usd == pytest.approx(2.4)
    half = estimate(env, resolve(env, RunSettings(model="m", max_steps=30)), "generate", jobs)  # type: ignore[arg-type]
    assert half.minutes == 8
    assert half.cost_usd == pytest.approx(1.2)
    first = estimate(env, rs, "intake", jobs)  # type: ignore[arg-type]
    assert first.basis == "default"
    assert first.samples == 0
    assert estimate(env, resolve(env, RunSettings(model="unpriced")), "modify", []).cost_usd is None


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    await db.dispose_db()
    from housegen.main import create_app

    app = create_app()
    await db.init_db()
    monkeypatch.setattr(job_manager, "submit", lambda *a, **k: None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    await db.dispose_db()
    get_settings.cache_clear()


def _pdf() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    doc.new_page()
    return doc.tobytes()


async def test_settings_persist_and_the_job_takes_a_snapshot(client: AsyncClient) -> None:
    r = await client.post(
        "/api/v1/projects",
        data={"name": "t"},
        files=[("plans", ("p.pdf", _pdf(), "application/pdf"))],
    )
    pid = r.json()["id"]
    assert r.json()["settings"] == {
        "provider": None,
        "model": None,
        "builder_effort": None,
        "critic_effort": None,
        "critic_rounds": None,
        "max_steps": None,
        "render_quality": None,
    }
    r = await client.patch(
        f"/api/v1/projects/{pid}/settings",
        json={"builder_effort": "medium", "critic_rounds": 1, "max_steps": 30},
    )
    assert r.status_code == 200, r.text
    assert r.json()["settings"]["max_steps"] == 30
    assert r.json()["effective_settings"]["preset"] == "quick"
    r = await client.get(f"/api/v1/projects/{pid}/estimate?kind=generate")
    assert r.status_code == 200, r.text
    assert r.json()["basis"] == "default"
    assert r.json()["minutes"] >= 1
    r = await client.post(f"/api/v1/projects/{pid}/intake")
    job_id = r.json()["id"]
    assert r.json()["settings"]["max_steps"] == 30
    # changing the settings afterwards does not touch the job's snapshot
    await client.patch(f"/api/v1/projects/{pid}/settings", json={"max_steps": 90})
    async with session_factory()() as s:
        job = await crud.get_job(s, job_id)
        assert json.loads(job.settings_json or "{}")["max_steps"] == 30
    r = await client.get(f"/api/v1/projects/{pid}/settings/effective")
    assert r.json()["max_steps"] == 90
    assert r.json()["preset"] == "custom"
