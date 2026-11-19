"""The start of an interior job (#98): no exterior set rendered or shown to its builder; a house
already furnished starts from the saved plan of each storey, without a render."""

from __future__ import annotations

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
from housegen.llm.types import Completion, ImagePart, Message, ToolCallPart, ToolSpec
from housegen.projects import crud
from housegen.projects.schemas import standard_views
from housegen.projects.storage import ProjectStorage
from housegen.render.kits import kit_with
from housegen.render.renderer import RenderResult

EXTERIOR = set(standard_views(True)) | set(standard_views(False))
PLANS = ["plan-section-1", "plan-section-2"]


class FakeProvider:
    """The builder checks the scene and finishes; every call is logged in `log`."""

    def __init__(self, log: list[tuple[str, list[str]]]) -> None:
        self.log = log
        self.builder: list[list[Message]] = []
        self.turns = [
            [ToolCallPart(id="1", name="check_scene", input={})],
            [ToolCallPart(id="2", name="finish", input={"summary": "Furnished."})],
        ]

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
        cache_key: str | None = None,
    ) -> Completion:
        self.log.append(("llm", []))
        self.builder.append(list(messages))
        turn = self.turns.pop(0)
        return Completion(message=Message(role="assistant", content=turn), stop_reason="tool_use")


class FakeRenderer:
    def __init__(self, log: list[tuple[str, list[str]]]) -> None:
        self.log = log

    async def render(
        self,
        scene_url: str,
        views: list[str],
        out_dir: Path,
        quality: str = "high",
        camera: dict[str, float] | None = None,
    ) -> RenderResult:
        self.log.append(("render", list(views)))
        out_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 — tiny local mkdir
        images: dict[str, Path] = {}
        for v in views:
            p = out_dir / f"{v}.jpg"
            Image.new("RGB", (8, 8), (200, 200, 200)).save(p, "JPEG")
            images[v] = p
        return RenderResult(images=images, errors=[])


@pytest.fixture
async def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    await db.dispose_db()
    await db.init_db()
    yield
    await db.dispose_db()
    get_settings.cache_clear()


async def _house(furnished: bool, edited: bool = False) -> tuple[str, str]:
    """A project at version 1 with a plan sheet and the version's pictures on disk (the exterior
    set, and when furnished the plan of two storeys and a room), and an interior job."""
    async with session_factory()() as s, s.begin():
        pid = (await crud.create_project(s, "t")).id
    st = ProjectStorage(pid)
    st.ensure()
    st.init_scene_from_template()
    (st.plans_dir / "1").mkdir(parents=True)
    Image.new("RGB", (40, 30), "white").save(st.plans_dir / "1" / "page-1.png")
    if furnished:
        (st.scene_dir / "src" / "interior-ground.js").write_text(
            'import { floorPlan } from "housekit/interior";\nexport const rooms = [];\n'
        )
    dst = st.snapshot(1, kit=kit_with("interior.js"))
    views = standard_views(False) + ([*PLANS, "photo-1"] if furnished else [])
    for i, v in enumerate(views):
        grey = 20 + 10 * i  # each picture its own bytes
        Image.new("RGB", (8, 8), (grey, grey, grey)).save(dst / "renders" / f"{v}.jpg", "JPEG")
    if edited:
        (st.scene_dir / "src" / "interior-ground.js").write_text(
            'import { floorPlan } from "housekit/interior";\nexport const rooms = ["half"];\n'
        )
    async with session_factory()() as s, s.begin():
        await crud.add_version(s, pid, 1, "interior" if furnished else "generation", "v1")
        job = await crud.create_job(s, pid, "interior", "")
    return pid, job.id


async def _run(
    monkeypatch: pytest.MonkeyPatch, pid: str, job_id: str
) -> tuple[FakeProvider, list[tuple[str, list[str]]], list[tuple[str, dict[str, Any]]]]:
    log: list[tuple[str, list[str]]] = []
    provider = FakeProvider(log)
    monkeypatch.setattr(pipeline, "renderer", FakeRenderer(log))
    monkeypatch.setattr(pipeline, "get_provider", lambda *_: provider)
    manager = JobManager()
    manager.submit(job_id, pid, pipeline.interior)
    await manager._tasks[job_id]
    async with session_factory()() as s:
        job = await crud.get_job(s, job_id)
        assert job.status == "done", job.error
        events = [
            (e.type, json.loads(e.payload_json)) for e in await crud.list_job_events(s, job_id)
        ]
    return provider, log, events


def _before_the_builder(
    events: list[tuple[str, dict[str, Any]]],
) -> list[tuple[str, dict[str, Any]]]:
    start = next(i for i, (t, p) in enumerate(events) if t == "phase" and p["name"] == "builder")
    assert events[start][1]["message"] == "Building the interior"
    return events[:start]


@pytest.mark.parametrize("edited", [False, True])
async def test_an_interior_starts_building_without_the_exterior_pictures(
    env: None, monkeypatch: pytest.MonkeyPatch, edited: bool
) -> None:
    """A first interior, and a furnished house whose working copy is no longer its version (its
    saved plans would not be what the builder starts from): no picture before the builder."""
    pid, job_id = await _house(furnished=edited, edited=edited)
    provider, log, events = await _run(monkeypatch, pid, job_id)

    assert log[0] == ("llm", [])  # nothing rendered before the builder's first call
    assert _before_the_builder(events) == []
    first = provider.builder[0][0]
    assert [p.label for p in first.content if isinstance(p, ImagePart)] == ["plan-1"]
    assert "The exterior model" not in first.text
    assert "The interior as it stands" not in first.text
    # the version's pictures are still the whole set, drawn at the end
    saved = {p.stem for p in (ProjectStorage(pid).versions_dir / "2" / "renders").glob("*.jpg")}
    assert set(standard_views(False)) <= saved  # a project without photos


async def test_a_furnished_house_starts_from_its_saved_floor_plans(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid, job_id = await _house(furnished=True)
    provider, log, events = await _run(monkeypatch, pid, job_id)

    assert log[0] == ("llm", [])  # the plans are the version's: no render before the builder
    start = _before_the_builder(events)
    assert [t for t, _ in start] == ["phase", "render"]
    assert start[0][1]["message"] == "The interior as it stands: version 1's floor plans"
    shown = start[1][1]["renders"]
    assert list(shown) == PLANS
    assert not EXTERIOR & set(shown)
    st = ProjectStorage(pid)
    kept = st.job_renders_dir(job_id) / Path(shown["plan-section-1"]).name
    saved = st.versions_dir / "1" / "renders" / "plan-section-1.jpg"
    assert kept.read_bytes() == saved.read_bytes()

    first = provider.builder[0][0]
    assert "## The interior as it stands (version 1)" in first.text
    labels = [p.label for p in first.content if isinstance(p, ImagePart)]
    assert labels == ["plan-1", "plan-section-1 — version 1", "plan-section-2 — version 1"]
    # the working copy's style was written after the plans were read (#101)
    assert (st.scene_dir / "src" / "style.js").exists()
