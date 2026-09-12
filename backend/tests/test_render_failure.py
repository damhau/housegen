"""The version render fails (the scene did not become ready, a crash): retry at medium, then tell
the builder; never a critic call on a version without renders."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from housegen.agent import pipeline
from housegen.core import db
from housegen.core.config import get_settings
from housegen.core.db import session_factory
from housegen.jobs.manager import JobManager
from housegen.llm.types import Completion, Message, ToolCallPart, ToolSpec
from housegen.projects import crud
from housegen.projects.storage import ProjectStorage
from housegen.render.renderer import RenderResult


class FakeProvider:
    """Every builder pass checks the scene and finishes; the critic returns a good verdict."""

    def __init__(self) -> None:
        self.turns = 0
        self.builder_calls: list[list[Message]] = []  # the messages at the start of each pass
        self.critic_calls = 0

    async def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        response_schema: dict[str, Any] | None = None,
        max_tokens: int = 16000,
        on_progress: Any = None,
        effort: str | None = None,
    ) -> Completion:
        if response_schema is not None:
            self.critic_calls += 1
            verdict = {"overall_score": 92, "summary": "fine", "done": True, "issues": []}
            return Completion(
                message=Message.assistant(json.dumps(verdict)), stop_reason="end_turn"
            )
        # a pass is two turns: check_scene (finish is refused without it), then finish
        self.turns += 1
        if self.turns % 2:
            self.builder_calls.append(list(messages))
            call = ToolCallPart(id=f"c{self.turns}", name="check_scene", input={})
        else:
            n = len(self.builder_calls)
            call = ToolCallPart(id=f"f{n}", name="finish", input={"summary": f"pass {n}"})
        return Completion(message=Message(role="assistant", content=[call]), stop_reason="tool_use")


class FakeRenderer:
    """`failing` = how many version renders (any quality) in a row produce nothing."""

    def __init__(self, failing: int) -> None:
        self.failing = failing
        self.qualities: list[str] = []

    async def render(
        self,
        scene_url: str,
        views: list[str],
        out_dir: Path,
        quality: str = "high",
        camera: dict[str, float] | None = None,
    ) -> RenderResult:
        if not views:  # check_scene
            return RenderResult()
        self.qualities.append(quality)
        if len(self.qualities) <= self.failing:
            return RenderResult(
                errors=["scene did not become ready (buildScene never resolved or crashed)"]
            )
        out_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 — tiny local mkdir
        images: dict[str, Path] = {}
        for v in views:
            p = out_dir / f"{v}.jpg"
            Image.new("RGB", (8, 8), (200, 200, 200)).save(p, "JPEG")
            images[v] = p
        return RenderResult(images=images)


@pytest.fixture
async def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CRITIC_MAX_ITERATIONS", "1")
    get_settings.cache_clear()
    await db.dispose_db()
    await db.init_db()
    yield
    await db.dispose_db()
    get_settings.cache_clear()


async def _run_generate(
    monkeypatch: pytest.MonkeyPatch, renderer: FakeRenderer
) -> tuple[str, str, FakeProvider, list[tuple[str, dict[str, Any]]]]:
    async with session_factory()() as s, s.begin():
        project = await crud.create_project(s, "t")
        st = ProjectStorage(project.id)
        st.ensure()
        st.init_scene_from_template()
        # one photo, so the critic has a reference to compare the renders with
        Image.new("RGB", (8, 8), (120, 120, 120)).save(st.photos_dir / "north.jpg", "JPEG")
        await crud.add_photo(s, project, "north", "north.jpg", "north.jpg")
        job = await crud.create_job(s, project.id, "generate")
        pid, job_id = project.id, job.id
    provider = FakeProvider()
    monkeypatch.setattr(pipeline, "renderer", renderer)
    monkeypatch.setattr(pipeline, "get_provider", lambda *_: provider)
    manager = JobManager()
    manager.submit(job_id, pid, pipeline.generate)
    deadline = asyncio.get_running_loop().time() + 10
    while asyncio.get_running_loop().time() < deadline:
        async with session_factory()() as s:
            j = await crud.get_job(s, job_id)
            if j.status in ("done", "failed"):
                break
        await asyncio.sleep(0.02)
    assert j.status == "done", j.error
    async with session_factory()() as s:
        rows = await crud.list_job_events(s, job_id)
    events = [(r.type, json.loads(r.payload_json)) for r in rows]
    return pid, job_id, provider, events


async def test_high_fails_medium_stands_in(env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    renderer = FakeRenderer(failing=1)
    pid, _, provider, events = await _run_generate(monkeypatch, renderer)
    assert renderer.qualities == ["high", "medium"]
    assert len(provider.builder_calls) == 1  # the builder was not bothered
    assert provider.critic_calls == 1
    phases = [p["message"] for t, p in events if t == "phase"]
    assert any("retrying at medium" in m for m in phases)
    st = ProjectStorage(pid)
    assert list((st.versions_dir / "1" / "renders").glob("*.jpg"))


async def test_no_render_at_all_goes_back_to_the_builder(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    renderer = FakeRenderer(failing=2)
    pid, _, provider, _events = await _run_generate(monkeypatch, renderer)
    # high + medium failed, the builder got the errors, its second pass rendered at high
    assert renderer.qualities == ["high", "medium", "high"]
    assert len(provider.builder_calls) == 2
    last = provider.builder_calls[1][-1]
    assert last.role == "user"
    assert "final render of your scene failed" in last.text
    assert "did not become ready" in last.text
    assert provider.critic_calls == 1
    st = ProjectStorage(pid)
    assert list((st.versions_dir / "1" / "renders").glob("*.jpg"))


async def test_still_nothing_saves_the_version_and_skips_the_critic(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    renderer = FakeRenderer(failing=10)
    pid, _, provider, events = await _run_generate(monkeypatch, renderer)
    assert renderer.qualities == ["high", "medium", "high", "medium"]
    assert len(provider.builder_calls) == 2
    assert provider.critic_calls == 0
    phases = [p["message"] for t, p in events if t == "phase"]
    assert any("review skipped" in m and "no render" in m for m in phases)
    done = [p for t, p in events if t == "done"]
    assert done
    assert done[0]["version"] == 1
    assert done[0].get("score") is None
    st = ProjectStorage(pid)
    assert (st.versions_dir / "1").is_dir()
    assert not list((st.versions_dir / "1" / "renders").glob("*.jpg"))
