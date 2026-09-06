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
    files = [("plan", ("plan.pdf", _pdf(), "application/pdf"))]
    sides = ["north", "south"] + ["other"] * 5
    for i, s in enumerate(sides):
        files.append(("photos", (f"p{i}.jpg", _jpeg(), "image/jpeg")))
    r = await client.post("/api/v1/projects", data={"name": "t", "sides": sides}, files=files)
    assert r.status_code == 201, r.text
    body = r.json()
    assert [p["side"] for p in body["photos"]] == sides
    stored = Path(get_settings().projects_dir) / body["id"] / "photos" / "north.jpg"
    assert Image.open(stored).size == (1600, 1067)  # long side capped


async def test_duplicate_facade_label_is_rejected(client: AsyncClient) -> None:
    files = [("plan", ("plan.pdf", _pdf(), "application/pdf"))]
    sides = ["north", "north"]
    for i in range(2):
        files.append(("photos", (f"p{i}.jpg", _jpeg(200, 100), "image/jpeg")))
    r = await client.post("/api/v1/projects", data={"name": "t", "sides": sides}, files=files)
    assert r.status_code == 422
    assert "façade side" in r.json()["error"]["message"]
