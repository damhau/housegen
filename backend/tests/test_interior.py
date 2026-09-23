"""The interior job (#33): the plan check's registration, the scene page's imports, the endpoint."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image, ImageDraw

from housegen.agent import pipeline, plancheck
from housegen.core import db
from housegen.core.config import get_settings
from housegen.core.db import session_factory
from housegen.jobs.manager import job_manager
from housegen.projects import crud
from housegen.render import kits

# a small house: outer walls 40 cm, one partition 10 cm, in metres
WALLS = [
    (-5.5, -5.0, 5.5, -4.6),
    (-5.5, 4.6, 5.5, 5.0),
    (-5.5, -5.0, -5.1, 5.0),
    (5.1, -5.0, 5.5, 5.0),
    (-0.05, -4.6, 0.05, 1.0),
]


def _section(bbox: tuple[float, float, float, float], w: int = 1280, h: int = 800) -> Image.Image:
    """What the runtime's plan-section view draws: white, walls dark blue (framing = bbox)."""
    x0, z0, x1, z1 = bbox
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    sx, sz = w / (x1 - x0), h / (z1 - z0)
    for a, b, c, e in WALLS:
        d.rectangle(((a - x0) * sx, (b - z0) * sz, (c - x0) * sx, (e - z0) * sz), fill="#1f3a93")
    return img


def _sheet(ppm: float, ox: float, oy: float) -> Image.Image:
    """A plan sheet: the same walls in grey poché at 1:100 on A3 at 150 dpi, plus some text."""
    img = Image.new("RGB", (2481, 1754), "white")
    d = ImageDraw.Draw(img)
    for a, b, c, e in WALLS:
        d.rectangle(
            (
                ox + (a + 5.5) * ppm,
                oy + (b + 5.0) * ppm,
                ox + (c + 5.5) * ppm,
                oy + (e + 5.0) * ppm,
            ),
            fill=(191, 191, 191),
            outline=(40, 40, 40),
        )
    d.text((200, 200), "PLANS DE DEMANDE D'AUTORISATION 1:100", fill="black")
    d.line((100, 1600, 2300, 1600), fill="black", width=2)
    return img


def test_the_plan_check_registers_the_section_on_the_sheet() -> None:
    dpi = 150.0
    ppm = dpi / 0.0254 / 100
    sheet = _sheet(ppm, ox=1136, oy=554)  # the house's outer NW corner at this sheet pixel
    bbox = (-8.96, -5.6, 8.96, 5.6)
    reg = plancheck.register(_section(bbox), bbox, sheet, dpi)
    assert reg is not None
    assert reg.scale == 100
    assert abs(reg.ppm - ppm) / ppm < 0.02
    # the section's corner (-8.96, -5.6) lands where the sheet puts it
    assert abs(reg.x0 - (1136 + (-8.96 + 5.5) * ppm)) < 4
    assert abs(reg.y0 - (554 + (-5.6 + 5.0) * ppm)) < 4
    assert reg.coverage > 0.9
    jpeg = plancheck.overlay(_section(bbox), sheet, reg, bbox)
    assert Image.open(io.BytesIO(jpeg)).size[0] > 400


def test_scale_from_a_sheet_label() -> None:
    assert plancheck.scale_from_label("Ground floor, double garage and pools — 1:100") == 100
    assert plancheck.scale_from_label("Site layout 1 : 200") == 200
    assert plancheck.scale_from_label("Roof plan") is None


def test_an_older_scene_page_gets_the_interior_imports(tmp_path: Path) -> None:
    page = tmp_path / "index.html"
    page.write_text(
        '<script type="importmap">\n{ "imports": {\n    "three": "/kit/vendor/three/build/three.module.js",\n'
        '    "housekit": "/kit/house.js"\n  } }\n</script>',
        encoding="utf-8",
    )
    assert pipeline.ensure_interior_imports(page)
    html = page.read_text(encoding="utf-8")
    for name, url in pipeline.INTERIOR_IMPORTS.items():
        assert f'"{name}": "{url}"' in html
    assert not pipeline.ensure_interior_imports(page)  # idempotent


def test_the_interior_job_draws_with_a_kit_that_has_the_interior_modules() -> None:
    name = kits.kit_with("interior.js")
    assert (kits.kit_dir(name) / "interior.js").exists()
    assert kits.kit_with("house.js") == kits.pinned_kit()


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    await db.dispose_db()
    from housegen.main import create_app

    app = create_app()
    await db.init_db()
    started: list[tuple[str, ...]] = []
    monkeypatch.setattr(job_manager, "submit", lambda *a, **k: started.append(a))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        c.started = started  # type: ignore[attr-defined]
        yield c
    await db.dispose_db()
    get_settings.cache_clear()


def _pdf() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    doc.new_page()
    return doc.tobytes()


async def test_the_interior_endpoint_needs_an_exterior_and_starts_the_job(
    client: AsyncClient,
) -> None:
    files = [("plans", ("plan.pdf", _pdf(), "application/pdf"))]
    r = await client.post("/api/v1/projects", data={"name": "t"}, files=files)
    pid = r.json()["id"]
    before = await client.post(f"/api/v1/projects/{pid}/interior", data={"message": "ground floor"})
    assert before.status_code == 409  # nothing to furnish yet
    async with session_factory()() as s, s.begin():
        await crud.add_version(s, pid, 1, "generation", "Initial build")
    r = await client.post(
        f"/api/v1/projects/{pid}/interior", data={"message": "ground floor, Scandinavian"}
    )
    assert r.status_code == 202
    assert r.json()["kind"] == "interior"
    assert client.started[-1][2] is pipeline.interior  # type: ignore[attr-defined]
    est = await client.get(f"/api/v1/projects/{pid}/estimate", params={"kind": "interior"})
    assert est.status_code == 200
    assert est.json()["kind"] == "interior"


def test_finish_waits_for_the_walls_of_every_storey_to_match_the_plan(tmp_path: Path) -> None:
    from housegen.agent.tools import PLAN_TRIES, BuilderTools
    from housegen.agent.workspace import Workspace

    t = BuilderTools(Workspace(tmp_path), renderer=None, scene_url="", renders_dir=tmp_path)  # type: ignore[arg-type]
    t.last_check_ok = True
    assert t.finish_blockers() == []
    t.plan_coverage, t.plan_checks = {1: 0.95, 2: 0.51}, {1: 1, 2: 1}
    blockers = t.finish_blockers()
    assert len(blockers) == 1
    assert "storey 2 (51%)" in blockers[0]
    assert "storey 1" not in blockers[0]
    # a storey checked PLAN_TRIES times is let through (some sheets measure poorly)
    t.plan_checks[2] = PLAN_TRIES
    assert t.finish_blockers() == []
