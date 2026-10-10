"""The surfaces and volumes report (#47, #48): settings saved on the project, defaults, the PDF."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from housegen.core import db
from housegen.core.config import get_settings
from housegen.jobs.manager import job_manager
from housegen.projects.storage import ProjectStorage
from housegen.render import renderer


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
        data={"name": "Villa"},
        files=[("plans", ("p.pdf", _pdf(), "application/pdf"))],
    )
    return str(r.json()["id"])


async def test_settings_start_empty_and_are_saved(client: AsyncClient) -> None:
    pid = await _project(client)
    r = await client.get(f"/api/v1/projects/{pid}/report")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["settings"]["weights"] == {"balcony": 0.5, "terrace": 0.33, "garden": 0.1}
    assert out["settings"]["prices"]["building_m3"] == {}
    assert out["defaults"] == {"parcel": None, "north": None, "datum": None}

    body = {
        "description": "villa de deux appartements",
        "building_names": {"b0": "Villa", "b1": "Garage"},
        "slab_thickness": 0.3,
        "weights": {"balcony": 0.5, "terrace": 0.25, "garden": 0.1},
        "prices": {
            "building_m3": {"b0": 850, "b1": 600},
            "land_m2": 1200,
            "sale_m2": {"App. 1": 9500},
        },
        "terraces": {"0": "App. 1"},
        "gardens": {"App. 2": 120.5},
    }
    r = await client.put(f"/api/v1/projects/{pid}/report", json=body)
    assert r.status_code == 200, r.text
    got = (await client.get(f"/api/v1/projects/{pid}/report")).json()["settings"]
    assert got["building_names"] == {"b0": "Villa", "b1": "Garage"}
    assert got["prices"]["building_m3"] == {"b0": 850, "b1": 600}
    assert got["prices"]["land_m2"] == 1200
    assert got["prices"]["pool_each"] is None
    assert got["weights"]["terrace"] == 0.25
    assert got["terraces"] == {"0": "App. 1"}
    assert got["gardens"] == {"App. 2": 120.5}
    # the project itself is untouched
    assert (await client.get(f"/api/v1/projects/{pid}")).json()["name"] == "Villa"


async def test_settings_are_checked(client: AsyncClient) -> None:
    pid = await _project(client)
    r = await client.put(f"/api/v1/projects/{pid}/report", json={"weights": {"balcony": 1.5}})
    assert r.status_code == 422
    r = await client.put(f"/api/v1/projects/{pid}/report", json={"prices": {"land_m2": -1}})
    assert r.status_code == 422
    assert (await client.get("/api/v1/projects/nope/report")).status_code == 404


async def test_defaults_come_from_the_surroundings(client: AsyncClient) -> None:
    pid = await _project(client)
    st = ProjectStorage(pid)
    (st.root / "context").mkdir(parents=True, exist_ok=True)
    ctx = {
        "place": {
            "label": "Le Mont-sur-Lausanne 3013 (CH 1609 8345 7625)",
            "kind": "parcel",
            "e": 1,
            "n": 2,
        },
        "alignment": {"x": 0, "z": 0, "rotation": -23.5, "ground": 668.15, "set": True},
    }
    (st.root / "context" / "context.json").write_text(json.dumps(ctx), encoding="utf-8")
    d = (await client.get(f"/api/v1/projects/{pid}/report")).json()["defaults"]
    assert d == {"parcel": "BF n° 3013, Le Mont-sur-Lausanne", "north": -23.5, "datum": 668.15}


async def test_pdf_prints_the_page_it_is_given(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = await _project(client)
    seen: list[str] = []

    async def fake_pdf(html: str) -> bytes:
        seen.append(html)
        return b"%PDF-1.7 fake"

    monkeypatch.setattr(renderer.local, "pdf", fake_pdf)
    r = await client.post(
        f"/api/v1/projects/{pid}/report/pdf",
        json={"html": "<!doctype html><p>SIA 416</p>", "filename": "Villa SIA 416.pdf"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert "Villa-SIA-416.pdf" in r.headers["content-disposition"]
    assert r.content.startswith(b"%PDF")
    assert seen == ["<!doctype html><p>SIA 416</p>"]
