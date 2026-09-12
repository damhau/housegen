"""Stopping a running job from the UI: it ends `cancelled` with a persisted event, the project
does not stay `generating`, and a job that is not running cannot be stopped."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from housegen.agent import pipeline
from housegen.core import db
from housegen.core.config import get_settings
from housegen.core.db import session_factory
from housegen.jobs.manager import JobContext, JobManager, job_manager
from housegen.projects import crud
from housegen.projects.storage import ProjectStorage


@pytest.fixture
async def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    await db.dispose_db()
    await db.init_db()
    yield
    await db.dispose_db()
    get_settings.cache_clear()


async def _project_and_job(kind: str = "generate") -> tuple[str, str]:
    async with session_factory()() as s, s.begin():
        project = await crud.create_project(s, "t")
        ProjectStorage(project.id).ensure()
        project.status = "generating"
        job = await crud.create_job(s, project.id, kind)
        return project.id, job.id


async def _wait_status(job_id: str, *statuses: str) -> str:
    deadline = asyncio.get_running_loop().time() + 5
    while asyncio.get_running_loop().time() < deadline:
        async with session_factory()() as s:
            j = await crud.get_job(s, job_id)
            if j.status in statuses:
                return j.status
        await asyncio.sleep(0.02)
    raise AssertionError(f"job {job_id} never reached {statuses}")


async def test_cancel_marks_the_job_cancelled_and_settles_the_project(env: None) -> None:
    pid, job_id = await _project_and_job()
    started = asyncio.Event()

    async def body(ctx: JobContext) -> None:
        await ctx.emit("phase", name="builder", message="working")
        started.set()
        await asyncio.sleep(60)

    manager = JobManager()
    manager.submit(job_id, pid, body)
    await asyncio.wait_for(started.wait(), 5)
    assert manager.cancel(job_id)
    assert await _wait_status(job_id, "cancelled", "failed") == "cancelled"
    async with session_factory()() as s:
        job = await crud.get_job(s, job_id)
        assert job.error is None
        assert job.finished_at is not None
        project = await crud.get_project(s, pid)
        assert project.status == "failed"  # no version yet: not `generating` forever
        rows = await crud.list_job_events(s, job_id)
    events = [(r.type, json.loads(r.payload_json)) for r in rows]
    assert events[-1][0] == "cancelled"
    assert not manager.is_running(job_id)
    assert not manager.cancel(job_id)  # gone


async def test_cancel_endpoint(env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from housegen.main import create_app

    app = create_app()
    started = asyncio.Event()

    async def slow_generate(ctx: JobContext) -> None:
        started.set()
        await asyncio.sleep(60)

    monkeypatch.setattr(pipeline, "generate", slow_generate)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        pid, job_id = await _project_and_job()
        job_manager.submit(job_id, pid, slow_generate)
        await asyncio.wait_for(started.wait(), 5)
        r = await c.post(f"/api/v1/projects/{pid}/jobs/{job_id}/cancel")
        assert r.status_code == 202, r.text
        assert await _wait_status(job_id, "cancelled") == "cancelled"
        r = await c.post(f"/api/v1/projects/{pid}/jobs/{job_id}/cancel")
        assert r.status_code == 409  # not running any more
        r = await c.post(f"/api/v1/projects/other/jobs/{job_id}/cancel")
        assert r.status_code == 404
