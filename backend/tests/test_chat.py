"""The conversation's turn (#35): it answers, asks back, makes a small change itself, hands a
bigger one to the builder (now, or once the running job ends), restores a version; it may be
sent while the builder works, one answer at a time."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from housegen.agent import pipeline
from housegen.agent.pipeline import file_map, point_text, start_waiting
from housegen.core import db
from housegen.core.config import get_settings
from housegen.core.db import session_factory
from housegen.jobs.manager import JobContext, JobManager, job_manager
from housegen.llm.types import Completion, ImagePart, Message, ToolCallPart, ToolSpec
from housegen.projects import crud
from housegen.projects.storage import ProjectStorage
from housegen.render.renderer import RenderResult


def _pdf() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    doc.new_page()
    return doc.tobytes()


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (90, 120, 160)).save(buf, "JPEG")
    return buf.getvalue()


class FakeProvider:
    def __init__(self, turns: list[Any]) -> None:
        self.turns = list(turns)
        self.calls: list[tuple[str, list[str], list[Message], str | None, str | None]] = []

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
        names = [t.name for t in tools or []]
        self.calls.append((system, names, list(messages), effort, cache_key))
        turn = self.turns.pop(0)
        if isinstance(turn, str):
            return Completion(message=Message.assistant(turn), stop_reason="end_turn")
        return Completion(message=Message(role="assistant", content=turn), stop_reason="tool_use")


class FakeRenderer:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def render(
        self,
        scene_url: str,
        views: list[str],
        out_dir: Path,
        quality: str = "high",
        camera: dict[str, float] | None = None,
    ) -> RenderResult:
        self.calls.append(list(views))
        out_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 — tiny local mkdir
        images: dict[str, Path] = {}
        for v in views:
            p = out_dir / f"{v}.jpg"
            Image.new("RGB", (8, 8), (200, 200, 200)).save(p, "JPEG")
            images[v] = p
        return RenderResult(images=images, errors=[], report={"rooms": [], "planSections": []})


class Manager(JobManager):
    """Records what a turn starts instead of running it."""

    def __init__(self) -> None:
        super().__init__()
        self.started: list[tuple[str, str, Any]] = []

    def submit(self, job_id: str, project_id: str, body: Any) -> None:
        self.started.append((job_id, project_id, body))


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    await db.dispose_db()
    from housegen.main import create_app

    app = create_app()
    await db.init_db()
    monkeypatch.setattr(job_manager, "submit", lambda *a, **k: None)  # no real run
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    await db.dispose_db()
    get_settings.cache_clear()


async def _project(client: AsyncClient, version: bool = True) -> str:
    files = [("plans", ("plan.pdf", _pdf(), "application/pdf"))]
    files.append(("photos", ("n.jpg", _jpeg(), "image/jpeg")))
    r = await client.post("/api/v1/projects", data={"name": "t", "sides": ["north"]}, files=files)
    pid: str = r.json()["id"]
    if version:
        st = ProjectStorage(pid)
        (st.scene_dir / "src" / "garden.js").write_text(
            "// the garden: lawn, hedges and the table\nexport function garden(ctx) {}\n"
        )
        dst = st.snapshot(1, kit="k1")
        for v in ("aerial", "south-photo", "north"):
            Image.new("RGB", (8, 8)).save(dst / "renders" / f"{v}.jpg", "JPEG")
        async with session_factory()() as s, s.begin():
            await crud.add_version(
                s, pid, 1, "generation", "Initial build", "A house with a garden.",
                suggestions=["the blue car"], questions=["Is the roof tiled?"],
            )  # fmt: skip
    return pid


async def test_a_message_starts_a_turn_even_while_the_builder_works(client: AsyncClient) -> None:
    pid = await _project(client, version=False)
    r = await client.post(f"/api/v1/projects/{pid}/chat", data={"message": "hello"})
    assert r.status_code == 409  # nothing to talk about yet
    pid = await _project(client)
    point = {"position": [1.2, 0.8, -3.4], "object": "table", "kind": "furniture",
             "room": "Salon", "room_index": 3}  # fmt: skip
    r = await client.post(
        f"/api/v1/projects/{pid}/chat",
        data={"message": "put a vase on this", "point": json.dumps(point)},
    )
    assert r.status_code == 202, r.text
    assert r.json()["kind"] == "chat"
    first = r.json()["id"]
    chat = (await client.get(f"/api/v1/projects/{pid}/chat")).json()
    assert chat[-1]["content"] == "put a vase on this"
    assert chat[-1]["point"]["room"] == "Salon"
    assert chat[-1]["point"]["position"] == [1.2, 0.8, -3.4]
    # one answer at a time
    r = await client.post(f"/api/v1/projects/{pid}/chat", data={"message": "and a lamp"})
    assert r.status_code == 409
    # while a build runs, the conversation goes on; a build does not start while a turn runs
    async with session_factory()() as s, s.begin():
        await crud.update_job(s, first, status="done")
        await crud.create_job(s, pid, "modify", "a pergola")
    r = await client.post(f"/api/v1/projects/{pid}/chat", data={"message": "how long?"})
    assert r.status_code == 202, r.text
    r = await client.post(f"/api/v1/projects/{pid}/modify", data={"message": "x"})
    assert r.status_code == 409
    async with session_factory()() as s, s.begin():
        for j in await crud.list_jobs(s, pid):
            await crud.update_job(s, j.id, status="done")
    r = await client.post(
        f"/api/v1/projects/{pid}/chat", data={"message": "x", "point": "not json"}
    )
    assert r.status_code == 422


async def _turn(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, pid: str, message: str, turns: list[Any],
    point: dict[str, Any] | None = None,
) -> tuple[FakeProvider, FakeRenderer, Manager, str]:  # fmt: skip
    async with session_factory()() as s, s.begin():
        job = await crud.create_job(s, pid, "chat", message)
        await crud.add_chat_message(s, pid, "user", message, job.id, point=point)
    provider, renderer, manager = FakeProvider(turns), FakeRenderer(), Manager()
    monkeypatch.setattr(pipeline, "get_provider", lambda *_: provider)
    monkeypatch.setattr(pipeline, "renderer", renderer)
    await pipeline.chat(JobContext(manager, job.id, pid))
    return provider, renderer, manager, job.id


async def _chat(client: AsyncClient, pid: str) -> list[dict[str, Any]]:
    return list((await client.get(f"/api/v1/projects/{pid}/chat")).json())


async def test_a_question_gets_an_answer_and_nothing_changes(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = await _project(client)
    point = {"position": [1.0, 0.75, 2.0], "object": "table", "room": "Salon", "room_index": 3}
    provider, renderer, manager, job_id = await _turn(
        client, monkeypatch, pid, "why is this table so low?", ["It is 75 cm high, like most."],
        point=point,
    )  # fmt: skip
    system, tools, messages, effort, cache_key = provider.calls[0]
    assert "For each message, do one of these" in system
    assert {"apply_patch", "finish", "hand_over", "restore_version"} <= set(tools)
    assert effort == "medium"
    assert cache_key == f"chat-{pid}"
    text = messages[0].text
    assert "## The current version: 1 (generation), Initial build\nA house with a garden." in text
    assert "## The builder's open questions\n- Is the roof tiled?" in text
    assert "- the blue car" in text
    assert "- src/garden.js (2 lines): the garden: lawn, hedges and the table" in text
    assert "- object: table" in text
    assert "(view room-3)" in text
    assert text.rstrip().endswith("## The owner's message\nwhy is this table so low?")
    labels = [p.label for p in messages[0].content if isinstance(p, ImagePart)]
    assert labels == ["The current version — aerial", "The current version — south-photo"]
    chat = await _chat(client, pid)
    assert chat[-1] == {**chat[-1], "role": "assistant", "content": "It is 75 cm high, like most.",
                        "job_id": job_id, "version_number": None}  # fmt: skip
    assert renderer.calls == []
    assert manager.started == []
    async with session_factory()() as s:
        assert (await crud.get_project(s, pid)).current_version == 1


async def test_a_small_change_is_made_in_the_turn_and_saved_as_a_version(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = await _project(client)
    patch = ("*** Begin Patch\n*** Update File: src/garden.js\n@@\n-export function garden(ctx) {}"
             "\n+export function garden(ctx) { /* a flower */ }\n*** End Patch")  # fmt: skip
    _, renderer, manager, job_id = await _turn(
        client, monkeypatch, pid, "add a flower on the garden table",
        [
            [ToolCallPart(id="1", name="apply_patch", input={"patch": patch})],
            [ToolCallPart(id="2", name="render_views", input={"views": ["south-photo"]})],
            [ToolCallPart(id="3", name="check_scene", input={})],
            [ToolCallPart(id="4", name="finish", input={"summary": "Put a flower on the table."})],
        ],
    )  # fmt: skip
    await asyncio.gather(*pipeline._BACKGROUND)  # the version's pictures, drawn after the turn
    async with session_factory()() as s:
        project = await crud.get_project(s, pid)
        assert project.current_version == 2
        v2 = project.versions[-1]
        assert (v2.kind, v2.label, v2.summary) == (
            "edit",
            "add a flower on the garden table",
            "Put a flower on the table.",
        )
    chat = await _chat(client, pid)
    assert chat[-1]["version_number"] == 2
    assert chat[-1]["pictures"] == [f"/scenes/{pid}/versions/2/renders/south-photo.jpg"]
    st = ProjectStorage(pid)
    assert "a flower" in (st.versions_dir / "2" / "src" / "garden.js").read_text()
    # the answer came first, then the pictures of every view (no "before" render, no verifier)
    saved = {p.stem for p in (st.versions_dir / "2" / "renders").glob("*.jpg")}
    assert {"aerial", "south-photo", "north-photo"} <= saved
    assert renderer.calls[0] == ["south-photo"]
    assert renderer.calls[1] == []  # the check
    assert len(renderer.calls) == 3
    async with session_factory()() as s:
        events = [e.type for e in await crud.list_job_events(s, job_id)]
    assert events.index("answer") < events.index("done")
    assert manager.started == []


async def test_a_big_change_is_handed_to_the_builder(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = await _project(client)
    request = "Add a pergola over the south terrace, 4 x 3 m, dark grey metal"
    _, _, manager, job_id = await _turn(
        client, monkeypatch, pid, "a pergola on the terrace please, dark grey",
        [[ToolCallPart(id="1", name="hand_over", input={"request": request})]],
    )  # fmt: skip
    async with session_factory()() as s:
        jobs = await crud.list_jobs(s, pid)
    new = next(j for j in jobs if j.kind == "modify")
    assert new.request_text == request
    assert new.status == "queued"
    assert manager.started == [(new.id, pid, pipeline.modify)]
    chat = await _chat(client, pid)
    assert chat[-1]["job_id"] == job_id
    assert "builder" in chat[-1]["content"]


async def test_while_the_builder_works_a_change_waits_for_it(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = await _project(client)
    async with session_factory()() as s, s.begin():
        running = await crud.create_job(s, pid, "modify", "a pergola")
        await crud.update_job(s, running.id, status="running")
    provider, _, manager, _ = await _turn(
        client, monkeypatch, pid, "also paint the shutters green",
        [[ToolCallPart(id="1", name="hand_over",
                       input={"request": "Paint the shutters green", "reply": "Après la pergola."})]],
    )  # fmt: skip
    _, tools, messages, _, _ = provider.calls[0]
    assert set(tools) == {"list_files", "read_file", "inspect_image", "hand_over"}
    assert "A job is running on this scene right now" in messages[0].text
    async with session_factory()() as s:
        waiting = await crud.waiting_jobs(s, pid)
    assert [j.request_text for j in waiting] == ["Paint the shutters green"]
    assert manager.started == []
    assert (await _chat(client, pid))[-1]["content"] == "Après la pergola."
    # the running job ends: the waiting change starts
    assert await start_waiting(manager, pid) is None  # still running
    async with session_factory()() as s, s.begin():
        await crud.update_job(s, running.id, status="done")
    assert await start_waiting(manager, pid) == waiting[0].id
    assert manager.started == [(waiting[0].id, pid, pipeline.modify)]
    async with session_factory()() as s:
        assert (await crud.get_job(s, waiting[0].id)).status == "queued"


async def test_a_waiting_change_can_be_cancelled(client: AsyncClient) -> None:
    pid = await _project(client)
    async with session_factory()() as s, s.begin():
        job = await crud.create_job(s, pid, "modify", "x", status="waiting")
    r = await client.post(f"/api/v1/projects/{pid}/jobs/{job.id}/cancel")
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "cancelled"


async def test_undo_restores_the_version_before(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = await _project(client)
    st = ProjectStorage(pid)
    (st.scene_dir / "src" / "garden.js").write_text("export function garden() { /* v2 */ }\n")
    st.snapshot(2, kit="k1")
    async with session_factory()() as s, s.begin():
        await crud.add_version(s, pid, 2, "edit", "a flower")
    await _turn(
        client, monkeypatch, pid, "undo that",
        [[ToolCallPart(id="1", name="restore_version", input={"number": 1})]],
    )  # fmt: skip
    async with session_factory()() as s:
        project = await crud.get_project(s, pid)
        assert project.current_version == 3
        assert project.versions[-1].kind == "restore"
    assert "v2" not in (st.scene_dir / "src" / "garden.js").read_text()
    assert (await _chat(client, pid))[-1]["version_number"] == 3


async def test_a_change_that_overruns_its_steps_goes_to_the_builder_from_the_saved_version(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = await _project(client)
    monkeypatch.setenv("CHAT_MAX_STEPS", "2")
    get_settings.cache_clear()
    write = {"path": "src/garden.js", "content": "export function garden() { /* half */ }\n"}
    _, _, manager, _ = await _turn(
        client, monkeypatch, pid, "redo the whole garden",
        [
            [ToolCallPart(id="1", name="write_file", input=write)],
            [ToolCallPart(id="2", name="read_file", input={"path": "src/garden.js"})],
        ],
    )  # fmt: skip
    st = ProjectStorage(pid)
    assert "half" not in (st.scene_dir / "src" / "garden.js").read_text()  # reverted
    async with session_factory()() as s:
        jobs = await crud.list_jobs(s, pid)
    assert next(j for j in jobs if j.kind == "modify").request_text == "redo the whole garden"
    assert len(manager.started) == 1


def test_what_the_turn_is_told_about_the_scene_and_the_point(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "scene.js").write_text(
        "import x from './a.js';\nexport async function buildScene(ctx) {}\n"
    )
    (tmp_path / "src" / "roof.js").write_text(
        "/** The roof: a hipped roof of brown tiles */\nexport const a = 1;\n"
    )
    text = file_map(tmp_path)
    assert "- src/roof.js (2 lines): The roof: a hipped roof of brown tiles" in text
    assert "- src/scene.js (2 lines): exports buildScene" in text
    assert point_text(None) is None
    p = point_text({"position": [1, 2, 3], "kind": "wall"})
    assert p is not None
    assert "- object: wall" in p
    assert "x=1.00, y=2.00, z=3.00" in p


def test_the_module_that_builds_what_was_pointed_at_is_shown(tmp_path: Path) -> None:
    from housegen.agent.pipeline import pointed_module

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "furniture-first.js").write_text(
        'const table = await fx.model("dining-table-white", { width: 1.9 });\n'
    )
    (tmp_path / "src" / "roof.js").write_text("export const roof = 1;\n")
    text = pointed_module(tmp_path, {"position": [0, 0, 0], "object": "dining-table-white"})
    assert text is not None
    assert text.startswith("## Where it is built: src/furniture-first.js\n```js\n")
    assert 'fx.model("dining-table-white"' in text
    assert pointed_module(tmp_path, {"position": [0, 0, 0], "object": "sofa"}) is None
    assert pointed_module(tmp_path, None) is None


async def test_a_clean_render_checks_the_scene_like_check_scene(tmp_path: Path) -> None:
    from housegen.agent.tools import BuilderTools
    from housegen.agent.workspace import Workspace

    ws = Workspace(tmp_path)
    ws.write("src/scene.js", "export async function buildScene() {}\n")
    tools = BuilderTools(ws, FakeRenderer(), "http://x/index.html", tmp_path / "renders")  # type: ignore[arg-type]
    await tools.call("write_file", {"path": "src/a.js", "content": "export const a = 1;\n"})
    assert tools.finish_blockers()
    await tools.call("render_views", {"views": ["south-photo"]})
    assert not tools.finish_blockers()
    assert tools.edits == 1


async def test_the_rooms_come_from_the_report_kept_with_the_version(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = await _project(client)
    st = ProjectStorage(pid)
    rooms = [
        {"name": "Salon", "use": "living", "area": 20, "y": 0, "polygon": [[0, 0], [4, 0], [4, 5]]}
    ]
    (st.versions_dir / "1" / "report.json").write_text(
        json.dumps({"rooms": rooms, "planSections": []})
    )
    async with session_factory()() as s, s.begin():
        job = await crud.create_job(s, pid, "chat", "hello")
    renderer = FakeRenderer()
    monkeypatch.setattr(pipeline, "renderer", renderer)
    run = pipeline._Run(JobContext(Manager(), job.id, pid))
    inp = await run.inputs()
    await run.prepare_interior(inp, 1)
    assert [r["name"] for r in run.rooms] == ["Salon"]
    assert renderer.calls == []  # no load of the scene for its rooms
    # the working copy changed since: the scene is loaded, and nothing is kept
    (st.scene_dir / "src" / "garden.js").write_text("export function garden() { /* changed */ }\n")
    await run.prepare_interior(inp, 1)
    assert renderer.calls == [[]]
