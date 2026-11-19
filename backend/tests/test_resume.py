"""Auto-resume of interrupted jobs after a backend restart (#7), with a fake provider and renderer."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from housegen.agent import pipeline
from housegen.agent.pipeline import _Progress, resume_interrupted_jobs
from housegen.agent.prompts import FIRST_RUN_ADDENDUM, MODIFY_ADDENDUM, RESUME_ADDENDUM
from housegen.core import db
from housegen.core.config import get_settings
from housegen.core.db import session_factory
from housegen.jobs.manager import JobManager
from housegen.llm.types import Completion, Message, ToolCallPart, ToolSpec
from housegen.projects import crud
from housegen.projects.storage import ProjectStorage
from housegen.render.renderer import RenderResult


class FakeProvider:
    """Scripted turns; a turn that is the string "hang" blocks until cancelled."""

    def __init__(self, turns: list[Any]) -> None:
        self.turns = list(turns)
        self.calls: list[list[Message]] = []

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
        self.calls.append(list(messages))
        if response_schema is not None:  # the critic
            verdict = {"overall_score": 92, "summary": "fine", "done": True, "issues": []}
            return Completion(
                message=Message.assistant(json.dumps(verdict)), stop_reason="end_turn"
            )
        turn = self.turns.pop(0)
        if turn == "hang":
            await asyncio.Event().wait()
        if isinstance(turn, str):
            return Completion(message=Message.assistant(turn), stop_reason="end_turn")
        return Completion(
            message=Message(role="assistant", content=list(turn)), stop_reason="tool_use"
        )


class FakeRenderer:
    def __init__(self) -> None:
        self.calls = 0

    async def render(
        self,
        scene_url: str,
        views: list[str],
        out_dir: Path,
        quality: str = "high",
        camera: dict[str, float] | None = None,
    ) -> RenderResult:
        self.calls += 1
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
    monkeypatch.setenv("CRITIC_MAX_ITERATIONS", "1")
    get_settings.cache_clear()
    await db.dispose_db()
    await db.init_db()
    yield
    await db.dispose_db()
    get_settings.cache_clear()


async def _project_with_job(
    kind: str = "generate", request: str = "", version: bool = False
) -> tuple[str, str]:
    async with session_factory()() as s, s.begin():
        project = await crud.create_project(s, "t")
        st = ProjectStorage(project.id)
        st.ensure()
        st.init_scene_from_template()
        if version:
            st.snapshot(1)
            await crud.add_version(s, project.id, 1, "generation", "Initial build", "A house.")
        job = await crud.create_job(s, project.id, kind, request)
        return project.id, job.id


async def _interrupt(job_id: str, pid: str, body: Any, provider: FakeProvider, turns: int) -> None:
    """Run `body` until the provider has been called `turns` times, then stop the server."""
    manager = JobManager()
    manager.submit(job_id, pid, body)
    await _wait_for_job(job_id, "running")
    for _ in range(200):
        if len(provider.calls) == turns:
            break
        await asyncio.sleep(0.02)
    assert len(provider.calls) == turns
    await manager.shutdown()
    async with session_factory()() as s:
        assert (await crud.get_job(s, job_id)).status == "interrupted"


async def _resume_to_done(job_id: str) -> None:
    assert await resume_interrupted_jobs(JobManager()) == 1
    await _wait_for_job(job_id, "done")


async def _wait_for_job(job_id: str, status: str, limit_s: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + limit_s
    while asyncio.get_running_loop().time() < deadline:
        async with session_factory()() as s:
            job = await crud.get_job(s, job_id)
            if job.status == status:
                return
        await asyncio.sleep(0.02)
    raise AssertionError(f"job {job_id} did not reach {status}")


async def _events(job_id: str) -> list[tuple[int, str, dict[str, Any]]]:
    async with session_factory()() as s:
        rows = await crud.list_job_events(s, job_id)
    return [(r.seq, r.type, json.loads(r.payload_json)) for r in rows]


async def test_shutdown_marks_running_jobs_interrupted(env: None) -> None:
    _, job_id = await _project_with_job()
    manager = JobManager()

    async def body(ctx: Any) -> None:
        await ctx.emit("phase", name="builder", message="x")
        await asyncio.Event().wait()

    manager.submit(job_id, "p", body)
    await _wait_for_job(job_id, "running")
    await asyncio.sleep(0.05)
    await manager.shutdown()
    async with session_factory()() as s:
        job = await crud.get_job(s, job_id)
        assert job.status == "interrupted"
        assert job.finished_at is None
        assert await crud.active_job(s, job.project_id) is not None  # still blocks the project


async def test_startup_resumes_with_the_same_id_and_gives_up_after_three(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid, job_id = await _project_with_job()
    async with session_factory()() as s, s.begin():
        await crud.update_job(s, job_id, status="running")  # a crash orphan
        await crud.add_job_event(s, job_id, 1, "phase", {"name": "builder", "message": "x"})
        await crud.set_status(s, pid, "generating")
    submitted: list[tuple[str, str]] = []

    class Manager:
        def submit(self, jid: str, project_id: str, body: Any) -> None:
            submitted.append((jid, project_id))

    manager: Any = Manager()
    assert await resume_interrupted_jobs(manager) == 1
    assert submitted == [(job_id, pid)]

    # three resumes already recorded → failed with a clear error, project status settled
    async with session_factory()() as s, s.begin():
        for seq in (2, 3, 4):
            await crud.add_job_event(s, job_id, seq, "resumed", {"reason": "server restart"})
    submitted.clear()
    assert await resume_interrupted_jobs(manager) == 0
    assert submitted == []
    async with session_factory()() as s:
        job = await crud.get_job(s, job_id)
        assert job.status == "failed"
        assert "3 times" in (job.error or "")
        assert (await crud.get_project(s, pid)).status == "failed"
    events = await _events(job_id)
    assert events[-1][1] == "error"
    assert events[-1][0] == 5  # the sequence continues after the persisted events


async def test_interrupted_build_resumes_from_its_files(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid, job_id = await _project_with_job()
    renderer = FakeRenderer()
    monkeypatch.setattr(pipeline, "renderer", renderer)
    # first process: the builder writes a module, then hangs in its second turn
    first = FakeProvider(
        [
            [
                ToolCallPart(
                    id="1",
                    name="write_file",
                    input={"path": "src/shell.js", "content": "export const written = 1;\n"},
                )
            ],
            "hang",
        ]
    )
    monkeypatch.setattr(pipeline, "get_provider", lambda *_: first)
    manager = JobManager()
    manager.submit(job_id, pid, pipeline.generate)
    await _wait_for_job(job_id, "running")
    for _ in range(200):
        if len(first.calls) == 2:
            break
        await asyncio.sleep(0.02)
    assert len(first.calls) == 2
    await manager.shutdown()
    async with session_factory()() as s:
        assert (await crud.get_job(s, job_id)).status == "interrupted"
    seq_before = max(seq for seq, _, _ in await _events(job_id))

    # second process: a fresh conversation continues from the files
    second = FakeProvider(
        [
            [ToolCallPart(id="a", name="check_scene", input={})],
            [ToolCallPart(id="b", name="finish", input={"summary": "resumed and finished"})],
        ]
    )
    monkeypatch.setattr(pipeline, "get_provider", lambda *_: second)
    manager2 = JobManager()
    assert await resume_interrupted_jobs(manager2) == 1
    await _wait_for_job(job_id, "done")

    events = await _events(job_id)
    seqs = [seq for seq, _, _ in events]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)
    resumed = [e for e in events if e[1] == "resumed"]
    assert len(resumed) == 1
    assert resumed[0][0] == seq_before + 1
    assert resumed[0][2] == {"reason": "server restart", "attempt": 1}
    assert events[-1][1] == "done"
    # the workspace was not reset: the module written before the restart is still there,
    # and the resumed builder was told so (files + current renders, no template line)
    st = ProjectStorage(pid)
    assert (st.scene_dir / "src" / "shell.js").read_text() == "export const written = 1;\n"
    first_msg = second.calls[0][0]
    text = first_msg.text
    assert "interrupted by a server restart" in text
    assert "placeholder scene" not in text
    assert any(p.type == "image" for p in first_msg.content)
    async with session_factory()() as s:
        project = await crud.get_project(s, pid)
        assert project.status == "ready"
        assert project.current_version == 1
        assert [v.number for v in project.versions] == [1]
        assert (await crud.get_job(s, job_id)).result_version == 1


async def test_progress_reads_the_stage_from_events() -> None:
    class Ev:
        def __init__(self, type_: str, payload: dict[str, Any]) -> None:
            self.type = type_
            self.payload_json = json.dumps(payload)

    events = [
        Ev("phase", {"name": "builder", "message": "Reading the plans"}),
        Ev("builder_step", {"tool": "list_files", "result": "src/scene.js"}),
        Ev("builder_step", {"tool": "read_file", "args": "src/scene.js"}),
    ]
    assert not _Progress.from_events(events).wrote
    events += [
        Ev("builder_step", {"tool": "edit_file", "result": "not found", "is_error": True}),
        Ev("builder_done", {"summary": "built"}),
        Ev("version", {"number": 1}),
        Ev("phase", {"name": "critic", "message": "Independent review (round 1)"}),
        Ev("critic", {"iteration": 1, "score": 60, "done": False, "summary": "s", "issues": []}),
        Ev("phase", {"name": "builder", "message": "Fixing the critic's findings (round 1)"}),
        Ev("resumed", {"reason": "server restart"}),
    ]
    p = _Progress.from_events(events)
    assert p.last_phase == "builder"
    assert p.fixing
    assert p.round_number() == 1
    assert p.versions == [1]
    assert p.builder_summary == "built"
    assert p.attempts == 1
    assert p.wrote
    assert not p.done
    verdict = p.last_verdict()
    assert verdict is not None
    assert verdict.overall_score == 60
    assert not verdict.done


LEFT_AS_IS = "Rendering the scene as the restart left it"


async def test_a_build_interrupted_before_its_first_write_starts_over(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#27: the template is not the builder's work to continue from."""
    pid, job_id = await _project_with_job()
    monkeypatch.setattr(pipeline, "renderer", FakeRenderer())
    first = FakeProvider([[ToolCallPart(id="1", name="list_files", input={})], "hang"])
    monkeypatch.setattr(pipeline, "get_provider", lambda *_: first)
    await _interrupt(job_id, pid, pipeline.generate, first, turns=2)
    st = ProjectStorage(pid)
    # a module that is not the build's own (the template reset had not run, say): it must go
    (st.scene_dir / "src" / "stray.js").write_text("export const stray = 1;\n")

    second = FakeProvider(
        [
            [ToolCallPart(id="a", name="check_scene", input={})],
            [ToolCallPart(id="b", name="finish", input={"summary": "built from the start"})],
        ]
    )
    monkeypatch.setattr(pipeline, "get_provider", lambda *_: second)
    await _resume_to_done(job_id)

    text = second.calls[0][0].text
    assert FIRST_RUN_ADDENDUM in text
    assert "placeholder scene from a template" in text
    assert RESUME_ADDENDUM not in text
    assert "interrupted session" not in text
    assert not (st.scene_dir / "src" / "stray.js").exists()
    events = await _events(job_id)
    types = [t for _, t, _ in events]
    assert types.count("resumed") == 1  # the timeline still shows the pause
    after = events[types.index("resumed") + 1 :]
    assert all(p.get("message") != LEFT_AS_IS for _, t, p in after if t == "phase")
    assert after[0][1] == "phase"
    assert after[0][2]["name"] == "builder"
    async with session_factory()() as s:
        project = await crud.get_project(s, pid)
        assert project.status == "ready"
        assert [v.number for v in project.versions] == [1]


async def test_a_modification_interrupted_before_its_first_write_starts_over(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid, job_id = await _project_with_job("modify", "make the roof red", version=True)
    monkeypatch.setattr(pipeline, "renderer", FakeRenderer())
    first = FakeProvider([[ToolCallPart(id="1", name="list_files", input={})], "hang"])
    monkeypatch.setattr(pipeline, "get_provider", lambda *_: first)
    await _interrupt(job_id, pid, pipeline.modify, first, turns=2)

    second = FakeProvider(
        [
            [ToolCallPart(id="a", name="check_scene", input={})],
            [ToolCallPart(id="b", name="finish", input={"summary": "the roof is red"})],
        ]
    )
    monkeypatch.setattr(pipeline, "get_provider", lambda *_: second)
    await _resume_to_done(job_id)

    text = second.calls[0][0].text
    assert MODIFY_ADDENDUM in text
    assert "## Request\nmake the roof red" in text
    assert RESUME_ADDENDUM not in text
    events = await _events(job_id)
    assert [t for _, t, _ in events].count("resumed") == 1
    assert all(p.get("message") != LEFT_AS_IS for _, t, p in events if t == "phase")
    async with session_factory()() as s:
        project = await crud.get_project(s, pid)
        assert [v.number for v in project.versions] == [1, 2]


async def test_a_modification_interrupted_after_a_write_continues_from_it(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid, job_id = await _project_with_job("modify", "make the roof red", version=True)
    monkeypatch.setattr(pipeline, "renderer", FakeRenderer())
    written = {"path": "src/roof.js", "content": "export const red = 1;\n"}
    first = FakeProvider([[ToolCallPart(id="1", name="write_file", input=written)], "hang"])
    monkeypatch.setattr(pipeline, "get_provider", lambda *_: first)
    await _interrupt(job_id, pid, pipeline.modify, first, turns=2)

    second = FakeProvider(
        [
            [ToolCallPart(id="a", name="check_scene", input={})],
            [ToolCallPart(id="b", name="finish", input={"summary": "the roof is red"})],
        ]
    )
    monkeypatch.setattr(pipeline, "get_provider", lambda *_: second)
    await _resume_to_done(job_id)

    assert RESUME_ADDENDUM in second.calls[0][0].text
    assert (ProjectStorage(pid).scene_dir / "src" / "roof.js").exists()
    events = await _events(job_id)
    assert any(p.get("message") == LEFT_AS_IS for _, t, p in events if t == "phase")


async def test_a_chat_turn_interrupted_after_a_write_starts_again_from_the_version(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid, job_id = await _project_with_job("chat", "what is the roof made of?", version=True)
    monkeypatch.setattr(pipeline, "renderer", FakeRenderer())
    written = {"path": "src/half.js", "content": "export const half = 1;\n"}
    first = FakeProvider([[ToolCallPart(id="1", name="write_file", input=written)], "hang"])
    monkeypatch.setattr(pipeline, "get_provider", lambda *_: first)
    await _interrupt(job_id, pid, pipeline.chat, first, turns=2)
    assert (ProjectStorage(pid).scene_dir / "src" / "half.js").exists()

    second = FakeProvider(["Tiles."])
    monkeypatch.setattr(pipeline, "get_provider", lambda *_: second)
    await _resume_to_done(job_id)

    assert not (ProjectStorage(pid).scene_dir / "src" / "half.js").exists()
    events = await _events(job_id)
    assert events[-1][1] == "done"
    assert events[-1][2]["summary"] == "Tiles."
