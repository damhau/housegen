"""The project list shows a project's running job (a Furnish run or a modification leaves its
status at "ready") and when its current version was saved, not only when it was created."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from housegen.core import db
from housegen.core.config import get_settings
from housegen.projects.models import Job, Project, SceneVersion


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    await db.dispose_db()
    from housegen.main import create_app

    app = create_app()
    await db.init_db()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    await db.dispose_db()
    get_settings.cache_clear()


async def test_list_shows_the_running_job_and_the_last_version_time(client: AsyncClient) -> None:
    created = datetime.now(UTC) - timedelta(days=2)
    saved = datetime.now(UTC) - timedelta(minutes=5)
    async with db.session_factory()() as s:
        busy = Project(name="busy", status="ready", current_version=1, created_at=created)
        idle = Project(name="idle", status="ready", current_version=0, created_at=created)
        s.add_all([busy, idle])
        await s.flush()
        s.add(
            SceneVersion(
                project_id=busy.id, number=1, kind="generation", label="v1", created_at=saved
            )
        )
        s.add(Job(project_id=busy.id, kind="generate", status="done"))
        s.add(Job(project_id=busy.id, kind="interior", status="running"))
        s.add(Job(project_id=idle.id, kind="modify", status="failed"))
        await s.commit()
        ids = {"busy": busy.id, "idle": idle.id}
    r = await client.get("/api/v1/projects")
    assert r.status_code == 200
    rows = {p["id"]: p for p in r.json()}
    b, i = rows[ids["busy"]], rows[ids["idle"]]
    assert b["status"] == "ready"
    assert b["job"] == "interior"
    assert i["job"] is None
    assert abs(datetime.fromisoformat(b["updated_at"]) - saved).total_seconds() < 2
    assert abs(datetime.fromisoformat(i["updated_at"]) - created).total_seconds() < 2
