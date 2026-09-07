"""Timestamps leave the API as aware UTC, even though SQLite stores them naive (#1)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from housegen.core import db
from housegen.core.config import get_settings
from housegen.projects.models import Project

_UTC_SUFFIX = re.compile(r"(Z|\+00:00)$")


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


async def _insert_project() -> str:
    async with db.session_factory()() as s:
        p = Project(name="t")
        s.add(p)
        await s.commit()
        return p.id


async def test_datetime_reloaded_from_sqlite_is_aware_utc(client: AsyncClient) -> None:
    pid = await _insert_project()
    async with db.session_factory()() as s:  # fresh session: hits the DB, not the identity map
        row = (await s.execute(select(Project).where(Project.id == pid))).scalar_one()
    assert row.created_at.tzinfo is UTC
    assert abs((datetime.now(UTC) - row.created_at).total_seconds()) < 60


async def test_api_timestamps_carry_utc_offset(client: AsyncClient) -> None:
    pid = await _insert_project()
    r = await client.get("/api/v1/projects")
    assert r.status_code == 200, r.text
    summary = next(p for p in r.json() if p["id"] == pid)
    assert _UTC_SUFFIX.search(summary["created_at"]), summary["created_at"]

    r = await client.get(f"/api/v1/projects/{pid}")
    assert r.status_code == 200, r.text
    assert _UTC_SUFFIX.search(r.json()["created_at"]), r.json()["created_at"]
