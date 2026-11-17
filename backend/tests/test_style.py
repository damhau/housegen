"""The house style (#101): src/style.js read and written, the pieces that name their own finish,
the style picked at Furnish, and a Restyle that saves a new version with no run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from housegen.agent import pipeline
from housegen.core import db
from housegen.core.config import get_settings
from housegen.core.db import session_factory
from housegen.jobs.manager import job_manager
from housegen.projects import crud
from housegen.projects.schemas import standard_views
from housegen.projects.storage import ProjectStorage
from housegen.projects.style import literal_values, read_style, style_state, write_style
from housegen.render.kits import kit_dir
from housegen.render.renderer import RenderResult

STYLED = "2026-11-17-v37"  # interior.js, styles.js and a catalogue tagged by style
UNSTYLED = "2026-11-11-v35"  # interior.js, no styles.js
EXTERIOR = "2026-09-12-baseline"  # no interior.js

FURNITURE = """import fx from "housekit/furnish";
import { rug, bathtub } from "housekit/interior";
const wetTiles = { size: [0.3, 0.6], color: "#f3f2ee" };
const own = "black";
export async function furnish(ctx) {
  // a comment that says finish: "brass" does not count
  f.ceiling("track", 2, 3, { finish: "white" });
  f.ceiling("mirrorBar", 2, 3, { finish: "chrome" });
  f.wall("sconce", 1, { finish: own });
  f.pendant("globe", 1, 2, { finish });
  ctx.group.add(rug({ width: 2, depth: 3, color: "#c8bca8" }));
  ctx.group.add(rug({ width: 2, depth: 3 }));
  ctx.group.add(bathtub({ tiles: wetTiles }));
  ctx.group.add(bathtub({ tiles: true }));
  bed({ bedding: "olive" }); kitchen({ handles: style.handles, fittings: "black" });
}
"""


def test_style_js_is_written_keeping_the_owners_overrides(tmp_path: Path) -> None:
    assert read_style(tmp_path) is None
    write_style(tmp_path, "contemporary")
    assert (
        tmp_path / "src" / "style.js"
    ).read_text() == 'export default { style: "contemporary" };\n'
    assert read_style(tmp_path) == "contemporary"

    overrides = 'export default {\n  fixtures: "brass", // style: "x" in a comment\n  rooms: { "Bain": { wallTile: "white" } },\n};\n'
    (tmp_path / "src" / "style.js").write_text(overrides)
    assert read_style(tmp_path) == "scandinavian"  # names none: the kit's default
    write_style(tmp_path, "contemporary")
    text = (tmp_path / "src" / "style.js").read_text()
    assert text.startswith('export default { style: "contemporary",\n  fixtures: "brass"')
    assert '"Bain": { wallTile: "white" }' in text
    write_style(tmp_path, "scandinavian")
    assert read_style(tmp_path) == "scandinavian"
    assert (tmp_path / "src" / "style.js").read_text().count("style:") == 2  # one in the comment


def test_the_pieces_that_name_their_own_finish(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "furniture-ground.js").write_text(FURNITURE)
    (tmp_path / "src" / "style.js").write_text(
        'export default { style: "contemporary", fixtures: "brass" };\n'
    )
    found = [(v.line, v.role, v.text) for v in literal_values(tmp_path)]
    assert found == [
        (7, "fixtures", 'finish: "white"'),
        (9, "fixtures", "finish: own"),
        (11, "rug", 'rug color: "#c8bca8"'),
        (13, "wallTile", "tiles: wetTiles"),
        (15, "bedding", 'bedding: "olive"'),
        (15, "fittings", 'fittings: "black"'),
    ]
    assert all(v.file == "src/furniture-ground.js" for v in literal_values(tmp_path))


def test_a_restyle_needs_an_interior_style_js_a_styled_renderer_and_no_own_finish(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    assert "no interior" in (style_state(tmp_path, False).reason or "")
    assert "before styles" in (style_state(tmp_path, True).reason or "")
    write_style(tmp_path, "scandinavian")
    assert "no house styles" in (style_state(tmp_path, True, styled=False).reason or "")
    assert style_state(tmp_path, True).reason is None
    (tmp_path / "src" / "furniture-ground.js").write_text(FURNITURE)
    state = style_state(tmp_path, True)
    assert state.style == "scandinavian"
    assert len(state.literals) == 6
    assert state.reason is not None
    assert 'finish: "white" in src/furniture-ground.js and 5 more' in state.reason


class FakeRenderer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    async def render(
        self, scene_url: str, views: list[str], out_dir: Path, quality: str = "high", **_: object
    ) -> RenderResult:
        self.calls.append((scene_url, list(views)))
        images: dict[str, Path] = {}
        for v in views:
            images[v] = out_dir / f"{v}.jpg"
            Image.new("RGB", (8, 8), (10, 200, 10)).save(images[v], "JPEG")
        return RenderResult(images=images, errors=[], report=None)


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    await db.dispose_db()
    from housegen.main import create_app

    app = create_app()
    await db.init_db()
    monkeypatch.setattr(job_manager, "submit", lambda *a, **k: None)
    renderer = FakeRenderer()
    monkeypatch.setattr(pipeline, "renderer", renderer)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        c.renderer = renderer  # type: ignore[attr-defined]
        yield c
    await db.dispose_db()
    get_settings.cache_clear()


def _pdf() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    doc.new_page()
    return doc.tobytes()


async def _furnished(
    client: AsyncClient, kit: str = STYLED, style: str | None = "contemporary"
) -> str:
    """A project whose version 1 is furnished (drawn with `kit`), with pictures and a listing set."""
    files = [("plans", ("plan.pdf", _pdf(), "application/pdf"))]
    r = await client.post("/api/v1/projects", data={"name": "t"}, files=files)
    pid = str(r.json()["id"])
    st = ProjectStorage(pid)
    st.ensure()
    st.init_scene_from_template()
    (st.scene_dir / "src" / "furniture-ground.js").write_text(
        'import { floorPlan } from "housekit/interior";\nexport async function furnish() {}\n'
    )
    if style:
        write_style(st.scene_dir, style)
    (st.scene_dir / "listing").mkdir()
    (st.scene_dir / "listing" / "set.json").write_text("{}")
    dst = st.snapshot(1, kit=kit)
    for v in [*standard_views(False), "plan-section-1", "room-1"]:
        Image.new("RGB", (8, 8), (90, 90, 90)).save(dst / "renders" / f"{v}.jpg", "JPEG")
    async with session_factory()() as s, s.begin():
        await crud.add_version(s, pid, 1, "interior", "Interior (contemporary)")
    return pid


async def test_a_restyle_saves_a_new_version_and_restore_brings_the_old_style_back(
    client: AsyncClient,
) -> None:
    pid = await _furnished(client)
    got = (await client.get(f"/api/v1/projects/{pid}/versions/1/style")).json()
    assert got["style"] == "contemporary"
    assert got["restylable"] is True
    assert got["styles"] == ["scandinavian", "contemporary"]

    same = await client.post(
        f"/api/v1/projects/{pid}/versions/1/restyle", json={"style": "contemporary"}
    )
    assert same.status_code == 422
    r = await client.post(
        f"/api/v1/projects/{pid}/versions/1/restyle", json={"style": "scandinavian"}
    )
    assert r.status_code == 200
    project = r.json()
    assert project["current_version"] == 2
    v2 = next(v for v in project["versions"] if v["number"] == 2)
    assert v2["kind"] == "restyle"
    assert v2["style"] == "scandinavian"
    assert v2["kit"] == STYLED  # the source's renderer, not the viewer's default
    st = ProjectStorage(pid)
    assert read_style(st.versions_dir / "2") == "scandinavian"
    assert read_style(st.versions_dir / "1") == "contemporary"
    assert not (st.versions_dir / "2" / "listing").exists()  # the set shows the old style
    assert (st.versions_dir / "1" / "listing" / "set.json").exists()

    # the exterior pictures are the source's; the rooms are drawn again, from the new version
    for task in list(pipeline._BACKGROUND):
        await task
    url, views = client.renderer.calls[-1]  # type: ignore[attr-defined]
    assert url.endswith(f"/scenes/{pid}/versions/2/index.html?kit={STYLED}")
    assert sorted(views) == ["plan-section-1", "room-1"]
    renders = st.versions_dir / "2" / "renders"
    assert {p.stem for p in renders.glob("*.jpg")} == {
        *standard_views(False),
        "plan-section-1",
        "room-1",
    }

    r = await client.post(f"/api/v1/projects/{pid}/versions/1/restore")
    assert r.json()["current_version"] == 3
    assert read_style(st.versions_dir / "3") == "contemporary"
    assert read_style(st.scene_dir) == "contemporary"


async def test_a_restyle_refuses_a_house_furnished_before_styles(client: AsyncClient) -> None:
    pid = await _furnished(client, style=None)
    got = (await client.get(f"/api/v1/projects/{pid}/versions/1/style")).json()
    assert got["restylable"] is False
    assert got["style"] is None
    assert "before styles" in got["reason"]
    r = await client.post(
        f"/api/v1/projects/{pid}/versions/1/restyle", json={"style": "contemporary"}
    )
    assert r.status_code == 409
    old = await _furnished(client, kit=UNSTYLED, style="scandinavian")
    got = (await client.get(f"/api/v1/projects/{old}/versions/1/style")).json()
    assert "no house styles" in got["reason"]
    assert (await client.get(f"/api/v1/projects/{pid}/versions/9/style")).status_code == 404


async def test_furnish_takes_a_style_and_a_listed_renderer(client: AsyncClient) -> None:
    pid = await _furnished(client)
    url = f"/api/v1/projects/{pid}/interior"
    assert (await client.post(url, data={"kit": "2099-01-01-v99"})).status_code == 422
    assert (await client.post(url, data={"kit": EXTERIOR})).status_code == 422  # no interior.js
    no_styles = await client.post(url, data={"kit": UNSTYLED, "style": "contemporary"})
    assert no_styles.status_code == 422
    assert (await client.post(url, data={"style": "baroque"})).status_code == 422
    r = await client.post(url, data={"style": "contemporary", "kit": STYLED})
    assert r.status_code == 202
    assert r.json()["options"] == {"style": "contemporary", "kit": STYLED}
    async with session_factory()() as s:
        job = await crud.get_job(s, r.json()["id"])
    assert json.loads(job.options_json or "{}") == {"style": "contemporary", "kit": STYLED}


def test_furnish_builds_in_the_style_asked_else_the_houses_own(tmp_path: Path) -> None:
    styled, unstyled = kit_dir(STYLED), kit_dir(UNSTYLED)
    assert pipeline.interior_style(unstyled, tmp_path, "contemporary") is None
    assert read_style(tmp_path) is None
    assert pipeline.interior_style(styled, tmp_path, None) == "scandinavian"
    assert pipeline.interior_style(styled, tmp_path, "contemporary") == "contemporary"
    assert pipeline.interior_style(styled, tmp_path, None) == "contemporary"  # the house's own
    assert read_style(tmp_path) == "contemporary"


def test_the_interior_request_names_the_house_style() -> None:
    text = pipeline.house_style_text("contemporary")
    assert text.startswith('## House style: "contemporary"')
    assert "no finish on a fixture" in text
