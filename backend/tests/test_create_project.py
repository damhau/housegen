"""Upload validation + photo normalisation for project creation."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from housegen.core import db
from housegen.core.config import get_settings


def _jpeg(w: int = 2400, h: int = 1600) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (120, 140, 90)).save(buf, "JPEG")
    return buf.getvalue()


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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    await db.dispose_db()
    get_settings.cache_clear()


async def test_many_unlabelled_photos_are_accepted_and_downscaled(client: AsyncClient) -> None:
    files = [("plans", ("plan.pdf", _pdf(), "application/pdf"))]
    sides = ["north", "south"] + ["other"] * 5
    for i in range(len(sides)):
        files.append(("photos", (f"p{i}.jpg", _jpeg(), "image/jpeg")))
    r = await client.post("/api/v1/projects", data={"name": "t", "sides": sides}, files=files)
    assert r.status_code == 201, r.text
    body = r.json()
    assert [p["side"] for p in body["photos"]] == sides
    stored = Path(get_settings().projects_dir) / body["id"] / "photos" / "north.jpg"
    assert Image.open(stored).size == (1600, 1067)  # long side capped


async def test_duplicate_facade_label_is_rejected(client: AsyncClient) -> None:
    files = [("plans", ("plan.pdf", _pdf(), "application/pdf"))]
    sides = ["north", "north"]
    for i in range(2):
        files.append(("photos", (f"p{i}.jpg", _jpeg(200, 100), "image/jpeg")))
    r = await client.post("/api/v1/projects", data={"name": "t", "sides": sides}, files=files)
    assert r.status_code == 422
    assert "façade side" in r.json()["error"]["message"]


async def test_project_without_photos_is_accepted_with_notes(client: AsyncClient) -> None:
    files = [("plans", ("plan.pdf", _pdf(), "application/pdf"))]
    r = await client.post(
        "/api/v1/projects", data={"name": "t", "notes": "the roof is dark grey"}, files=files
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["photos"] == []
    assert body["brief"] == "the roof is dark grey"
    assert body["intake"] is None


async def test_generate_with_answers_extends_the_brief(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from housegen.jobs.manager import job_manager

    monkeypatch.setattr(job_manager, "submit", lambda *a, **k: None)  # no real run
    files = [("plans", ("plan.pdf", _pdf(), "application/pdf"))]
    r = await client.post(
        "/api/v1/projects", data={"name": "t", "notes": "built 1972"}, files=files
    )
    pid = r.json()["id"]
    r = await client.post(
        f"/api/v1/projects/{pid}/generate",
        json={"answers": [{"question": "Wall colour?", "answer": "white"}], "notes": ""},
    )
    assert r.status_code == 202, r.text
    assert r.json()["kind"] == "generate"
    job_id = r.json()["id"]
    brief = (await client.get(f"/api/v1/projects/{pid}")).json()["brief"]
    assert brief == "built 1972\n\nQ: Wall colour?\nA: white"
    chat = (await client.get(f"/api/v1/projects/{pid}/chat")).json()
    assert [m["role"] for m in chat] == ["user"]
    # a plain regenerate adds nothing (the first job never ran: mark it done to start another)
    from housegen.core.db import session_factory
    from housegen.projects import crud

    async with session_factory()() as s, s.begin():
        await crud.update_job(s, job_id, status="done")
    r = await client.post(f"/api/v1/projects/{pid}/generate")
    assert r.status_code == 202, r.text
    assert (await client.get(f"/api/v1/projects/{pid}")).json()["brief"] == brief
