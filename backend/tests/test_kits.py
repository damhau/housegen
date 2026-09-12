"""Renderer versions: the registry lists the snapshots and the working copy, the build path is
pinned to a snapshot, and a scene page served with ?kit= loads that snapshot's kit files."""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from housegen.core.config import get_settings
from housegen.render import kits

BASELINE = "2026-09-12-baseline"


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    from housegen.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    get_settings.cache_clear()


def test_registry_lists_the_baseline_and_the_working_copy() -> None:
    out = kits.list_kits()
    names = [k.name for k in out.kits]
    assert names[0] == BASELINE
    assert names[-1] == "dev"
    assert out.pinned == BASELINE
    assert out.latest == BASELINE
    assert [k.name for k in out.kits if k.pinned] == [BASELINE]
    assert out.kits[-1].dev


def test_pinned_kit_is_a_snapshot_with_the_kit_files() -> None:
    s = get_settings()
    assert kits.pinned_kit(s) == BASELINE
    d = kits.kit_dir(BASELINE, s)
    assert (d / "house.js").exists()
    assert (d / "runtime.js").exists()
    assert kits.kit_dir("dev", s) == s.KIT_DIR
    with pytest.raises(KeyError):
        kits.kit_dir("nope", s)
    with pytest.raises(KeyError):
        kits.kit_dir("../house", s)


def test_rewrite_points_the_import_map_at_the_snapshot() -> None:
    html = (get_settings().KIT_DIR / "template" / "index.html").read_text()
    out = kits.rewrite_page(html, BASELINE)
    assert f'"housekit": "/kit/versions/{BASELINE}/house.js"' in out
    assert f'from "/kit/versions/{BASELINE}/runtime.js"' in out
    assert '"/kit/vendor/three/build/three.module.js"' in out  # three stays shared
    assert kits.rewrite_page(html, "dev") == html


async def test_scene_pages_are_served_with_the_chosen_renderer(client: AsyncClient) -> None:
    s = get_settings()
    version = s.projects_dir / "p1" / "versions" / "3"
    version.mkdir(parents=True)
    shutil.copy(s.KIT_DIR / "template" / "index.html", version / "index.html")
    plain = await client.get("/scenes/p1/versions/3/index.html")
    assert plain.status_code == 200
    assert '"/kit/house.js"' in plain.text
    snap = await client.get(f"/scenes/p1/versions/3/index.html?kit={BASELINE}")
    assert snap.status_code == 200
    assert f"/kit/versions/{BASELINE}/runtime.js" in snap.text
    assert (await client.get("/scenes/p1/versions/3/index.html?kit=dev")).text == plain.text
    assert (await client.get("/scenes/p1/versions/3/index.html?kit=nope")).status_code == 404
    assert (await client.get("/scenes/p1/versions/9/index.html")).status_code == 404
    assert (await client.get("/scenes/../x/versions/3/index.html")).status_code in (404, 422)
    # the snapshot's files are served by the kit mount
    js = await client.get(f"/kit/versions/{BASELINE}/house.js")
    assert js.status_code == 200
    assert "export function perimeterWalls" in js.text
