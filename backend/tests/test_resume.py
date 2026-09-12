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


async def _project_with_job(kind: str = "generate") -> tuple[str, str]:
    async with session_factory()() as s, s.begin():
        project = await crud.create_project(s, "t")
        st = ProjectStorage(project.id)
        st.ensure()
        st.init_scene_from_template()
        job = await crud.create_job(s, project.id, kind)
        return project.id, job.id


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
    assert not p.done
    verdict = p.last_verdict()
    assert verdict is not None
    assert verdict.overall_score == 60
    assert not verdict.done
