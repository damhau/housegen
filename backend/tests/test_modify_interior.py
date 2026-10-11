"""Chat modifications of a house with interiors (#45): the interior kit, its renderer and tools,
the rooms the request names seen before and after; the verifier gives no score when its
pictures cannot show the change, and the "before" pictures come from the saved version (#35)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from housegen.agent import pipeline
from housegen.agent.pipeline import has_interior, thread_text, touched_rooms
from housegen.agent.prompts import BUILDER_SYSTEM, INTERIOR_KIT_REFERENCE
from housegen.core import db
from housegen.core.config import get_settings
from housegen.core.db import session_factory
from housegen.jobs.manager import JobManager
from housegen.llm.types import Completion, ImagePart, Message, ToolCallPart, ToolSpec
from housegen.projects import crud
from housegen.projects.schemas import standard_views
from housegen.projects.storage import ProjectStorage
from housegen.render.kits import kit_with, pinned_kit
from housegen.render.renderer import RenderResult

ROOMS: list[dict[str, Any]] = [
    {"name": "Séjour", "use": "kitchen-living", "area": 38.0, "y": 0.0,
     "polygon": [[0, 0], [8, 0], [8, 5], [0, 5]]},
    {"name": "Chambre 1", "use": "bedroom", "area": 14.0, "y": 2.8,
     "polygon": [[0, 0], [4, 0], [4, 4], [0, 4]]},
    {"name": "SdB parents", "use": "bath", "area": 6.0, "y": 2.8,
     "polygon": [[4, 0], [6, 0], [6, 3], [4, 3]]},
    {"name": "SdB ouest", "use": "bath", "area": 5.0, "y": 5.6,
     "polygon": [[-3, 0], [-1, 0], [-1, 3], [-3, 3]]},
    {"name": "SdB est", "use": "bath", "area": 5.0, "y": 5.6,
     "polygon": [[5, 0], [7, 0], [7, 3], [5, 3]]},
    {"name": "Chambre 2", "use": "bedroom", "area": 12.0, "y": 5.6,
     "polygon": [[1, 0], [4, 0], [4, 4], [1, 4]]},
]  # fmt: skip


def test_the_rooms_a_request_is_about() -> None:
    assert touched_rooms("tile the attic west bathroom, glass shower, towel rail", ROOMS) == [3]
    assert touched_rooms("une plante dans le séjour", ROOMS) == [0]
    assert touched_rooms("Put a lamp in Chambre 2", ROOMS) == [5]
    assert touched_rooms("use bed-oak-linen for every bed", ROOMS) == [1, 5]
    assert touched_rooms("the bathrooms on the first floor: darker tiles", ROOMS) == [2]
    assert touched_rooms("make the roof darker", ROOMS) == []
    assert touched_rooms("anything", []) == []


def test_a_scene_has_interiors_when_its_modules_import_the_interior_kit(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "scene.js").write_text('import * as house from "housekit";\n')
    assert not has_interior(tmp_path)
    (tmp_path / "src" / "furniture-ground.js").write_text(
        'import fx from "housekit/furnish";\nexport const x = 1;\n'
    )
    assert has_interior(tmp_path)


def test_the_builder_gets_the_conversation_with_what_was_done() -> None:
    class M:
        def __init__(self, role: str, content: str, version: int | None = None) -> None:
            self.role, self.content, self.version_number = role, content, version

    text = thread_text(
        [M("user", "add a table in the garden"), M("assistant", "Added an oak table " * 80, 4)]
    )
    assert text is not None
    assert "- Owner: add a table in the garden" in text
    assert "- You (version 4): Added an oak table" in text
    assert len(text) < 900  # a long answer is cut to its gist
    assert thread_text([]) is None


class FakeProvider:
    """Builder turns in order; the verifier (a response schema) answers `verdict`."""

    def __init__(self, turns: list[Any], verdict: dict[str, Any]) -> None:
        self.turns = list(turns)
        self.verdict = verdict
        self.builder: list[tuple[str, list[ToolSpec], list[Message]]] = []
        self.verifier: list[list[Message]] = []

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
        if response_schema is not None:
            self.verifier.append(list(messages))
            return Completion(
                message=Message.assistant(json.dumps(self.verdict)), stop_reason="end_turn"
            )
        self.builder.append((system, list(tools or []), list(messages)))
        turn = self.turns.pop(0)
        return Completion(message=Message(role="assistant", content=turn), stop_reason="tool_use")


class FakeRenderer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    async def render(
        self,
        scene_url: str,
        views: list[str],
        out_dir: Path,
        quality: str = "high",
        camera: dict[str, float] | None = None,
    ) -> RenderResult:
        self.calls.append((scene_url, list(views)))
        out_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 — tiny local mkdir
        images: dict[str, Path] = {}
        for v in views:
            p = out_dir / f"{v}.jpg"
            Image.new("RGB", (8, 8), (200, 200, 200)).save(p, "JPEG")
            images[v] = p
        report = {
            "rooms": ROOMS,
            "planSections": [{"view": "plan-section-1", "y": 0, "bbox": [0, 0, 8, 5]}],
            "layout": None,
        }
        return RenderResult(images=images, errors=[], report=report)


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


async def _house(interior: bool, kit: str, request: str) -> tuple[str, str]:
    """A project at version 1 (saved with `kit`, its pictures on disk) and a modify job."""
    async with session_factory()() as s, s.begin():
        project = await crud.create_project(s, "t")
        pid = project.id
    st = ProjectStorage(pid)
    st.ensure()
    st.init_scene_from_template()
    if interior:
        (st.scene_dir / "src" / "furniture-attic.js").write_text(
            'import fx from "housekit/furnish";\nexport async function furnish() {}\n'
        )
    rooms = ["plan-section-1"] + [f"photo-{i}" for i in range(1, 6)]  # photo-6 is missing
    views = standard_views(False) + (rooms if interior else [])
    dst = st.snapshot(1, kit=kit)
    for v in views:
        Image.new("RGB", (8, 8), (90, 90, 90)).save(dst / "renders" / f"{v}.jpg", "JPEG")
    async with session_factory()() as s, s.begin():
        await crud.add_version(s, pid, 1, "interior", "Interior")
        await crud.add_chat_message(s, pid, "user", "furnish the house")
        await crud.add_chat_message(s, pid, "assistant", "Furnished six rooms.", None, 1)
        job = await crud.create_job(s, pid, "modify", request)
        await crud.add_chat_message(s, pid, "user", request, job.id)
    return pid, job.id


async def _run(
    monkeypatch: pytest.MonkeyPatch, pid: str, job_id: str, verdict: dict[str, Any]
) -> tuple[FakeProvider, FakeRenderer]:
    renderer = FakeRenderer()
    monkeypatch.setattr(pipeline, "renderer", renderer)
    provider = FakeProvider(
        [
            [ToolCallPart(id="1", name="render_views", input={"views": ["room-4"]})],
            [ToolCallPart(id="2", name="check_scene", input={})],
            [ToolCallPart(id="3", name="finish", input={"summary": "Tiled the bathroom."})],
        ],
        verdict,
    )
    monkeypatch.setattr(pipeline, "get_provider", lambda *_: provider)
    manager = JobManager()
    manager.submit(job_id, pid, pipeline.modify)
    await manager._tasks[job_id]
    async with session_factory()() as s:
        job = await crud.get_job(s, job_id)
        assert job.status == "done", job.error
    return provider, renderer


async def test_a_modification_of_a_house_with_interiors_knows_the_interior_kit(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    kit = kit_with("interior.js")
    request = "tile the attic west bathroom, glass shower with its side panel, towel rail"
    pid, job_id = await _house(True, kit, request)
    verdict = {"overall_score": 40, "summary": "no room view shows it", "done": False,
               "issues": [], "visible": False}  # fmt: skip
    provider, renderer = await _run(monkeypatch, pid, job_id, verdict)

    # the builder: the interior kit, its tools, the rooms, the room the request names
    system, tools, messages = provider.builder[0]
    assert system.startswith(BUILDER_SYSTEM)
    assert INTERIOR_KIT_REFERENCE in system
    assert {"check_plan", "measure"} <= {t.name for t in tools}
    first = messages[0]
    assert "## The rooms" in first.text
    assert "4. SdB ouest (bath, 5 m², floor at y=5.60)" in first.text
    assert "- Owner: furnish the house" in first.text
    assert "- You (version 1): Furnished six rooms." in first.text
    assert request not in first.text.split("## Request")[0]  # the request is not in the thread
    labels = [p.label for p in first.content if isinstance(p, ImagePart)]
    assert "Current render — room-4 (SdB ouest)" in labels

    # every page drawn with the renderer that has the interior modules; the version's pictures
    # were reused as "before" (no render of the standard views at the start)
    assert all(url.endswith(f"?kit={kit}") for url, _ in renderer.calls)
    standard = set(standard_views(False))
    first_full = next(i for i, (_, v) in enumerate(renderer.calls) if standard <= set(v))
    builder_render = next(i for i, (_, v) in enumerate(renderer.calls) if v == ["room-4"])
    assert first_full > builder_render  # the only full render is the new version's
    assert ["photo-6"] in [v for _, v in renderer.calls[:builder_render]]  # what it lacked

    # the verifier saw the room before and after, named; it could not see the change: no score
    shown = [p.label for p in provider.verifier[0][0].content if isinstance(p, ImagePart)]
    assert "BEFORE — view room-4 (SdB ouest)" in shown
    assert "AFTER — view room-4 (SdB ouest)" in shown
    async with session_factory()() as s:
        project = await crud.get_project(s, pid)
        v2 = next(v for v in project.versions if v.number == 2)
        assert v2.critic_score is None
        assert json.loads(v2.critique_json or "{}")["visible"] is False
    st = ProjectStorage(pid)
    assert (st.versions_dir / "2" / "kit.txt").read_text().strip() == kit
    saved = {p.stem for p in (st.versions_dir / "2" / "renders").glob("*.jpg")}
    assert {"plan-section-1", "photo-1"} <= saved  # the interior's pictures are kept


async def test_an_exterior_modification_keeps_the_exterior_path(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid, job_id = await _house(False, "an-older-kit", "make the shutters dark grey")
    verdict = {"overall_score": 90, "summary": "done", "done": True, "issues": []}
    provider, renderer = await _run(monkeypatch, pid, job_id, verdict)
    system, tools, messages = provider.builder[0]
    assert system == BUILDER_SYSTEM
    assert "check_plan" not in {t.name for t in tools}
    assert "## The rooms" not in messages[0].text
    pinned = pinned_kit()
    assert all(url.endswith(f"?kit={pinned}") for url, _ in renderer.calls)
    # the version was drawn with another renderer: "before" is rendered again
    assert set(standard_views(False)) <= set(renderer.calls[0][1])
    async with session_factory()() as s:
        project = await crud.get_project(s, pid)
        assert next(v for v in project.versions if v.number == 2).critic_score == 90
