"""Read-only public share link (#24)."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from housegen.core import db
from housegen.core.config import get_settings
from housegen.core.db import session_factory
from housegen.jobs.manager import job_manager
from housegen.projects import crud


def _pdf() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    doc.new_page()
    return doc.tobytes()


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


async def _project(client: AsyncClient) -> str:
    r = await client.post(
        "/api/v1/projects",
        data={"name": "Villa", "notes": "the roof is dark grey (private)"},
        files=[("plans", ("p.pdf", _pdf(), "application/pdf"))],
    )
    pid: str = r.json()["id"]
    async with session_factory()() as s, s.begin():
        await crud.add_version(s, pid, 1, "generation", "Initial build", "built")
        await crud.add_version(s, pid, 2, "modification", "Darker roof", "changed")
        await crud.add_chat_message(s, pid, "user", "make it darker (private)")
    return pid


async def test_share_create_is_idempotent_pins_and_revokes(client: AsyncClient) -> None:
    pid = await _project(client)
    assert (await client.get(f"/api/v1/projects/{pid}")).json()["share"] is None
    r = await client.post(f"/api/v1/projects/{pid}/share")
    assert r.status_code == 200, r.text
    share = r.json()
    token = share["token"]
    assert len(token) >= 30
    assert pid not in token
    assert share["url"] == f"/s/{token}"
    assert share["version"] is None
    # sharing again keeps the token; pinning a version re-pins it
    r = await client.post(f"/api/v1/projects/{pid}/share", json={"version": 1})
    assert r.json()["token"] == token
    assert r.json()["version"] == 1
    assert (await client.get(f"/api/v1/projects/{pid}")).json()["share"] == {
        "token": token,
        "url": f"/s/{token}",
        "version": 1,
    }
    # an unknown version cannot be pinned
    assert (
        await client.post(f"/api/v1/projects/{pid}/share", json={"version": 9})
    ).status_code == 404

    # the public payload: the pinned scene and its pictures, none of the owner's words
    r = await client.get(f"/api/v1/shared/{token}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Villa"
    assert body["version"] == 1
    assert body["pinned"] is True
    assert body["scene_url"] == f"/scenes/{pid}/versions/1/index.html"
    assert set(body) == {
        "name",
        "version",
        "pinned",
        "scene_url",
        "render_urls",
        "photo_urls",
        "created_at",
    }
    assert "private" not in r.text
    # unpinned → the current version
    await client.post(f"/api/v1/projects/{pid}/share", json={})
    body = (await client.get(f"/api/v1/shared/{token}")).json()
    assert body["version"] == 2
    assert body["pinned"] is False

    # revoke: the link dies, the project says it is not shared, a new share is a new token
    assert (await client.delete(f"/api/v1/projects/{pid}/share")).status_code == 204
    assert (await client.get(f"/api/v1/shared/{token}")).status_code == 404
    assert (await client.get(f"/api/v1/projects/{pid}")).json()["share"] is None
    assert (await client.post(f"/api/v1/projects/{pid}/share")).json()["token"] != token
    assert (await client.get("/api/v1/shared/nope")).status_code == 404
