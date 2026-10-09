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
LATEST = "2026-10-09-v16"  # the newest snapshot
VIEWER = "2026-10-09-v11"  # the viewer's default (index.json "viewer": the newest waits for review)


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
    assert names[0] == LATEST  # newest snapshot first
    assert BASELINE in names
    assert names[-1] == "dev"
    assert out.pinned == BASELINE  # the build path stays on the baseline
    assert out.latest == VIEWER
    assert [k.name for k in out.kits if k.pinned] == [BASELINE]
    assert out.kits[-1].dev


def test_the_viewer_default_can_stay_on_a_reviewed_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = kits._index
    monkeypatch.setattr(kits, "_index", lambda s: {**real(s), "viewer": BASELINE})
    out = kits.list_kits()
    assert out.kits[0].name == LATEST  # a newer snapshot is still listed first, and can be picked
    assert out.latest == BASELINE  # but nobody gets it by default
    assert kits.viewer_kit() == BASELINE
    # an unknown name: the newest snapshot, as without the entry
    monkeypatch.setattr(kits, "_index", lambda s: {**real(s), "viewer": "nope"})
    assert kits.list_kits().latest == LATEST
    monkeypatch.setattr(
        kits, "_index", lambda s: {k: v for k, v in real(s).items() if k != "viewer"}
    )
    assert kits.list_kits().latest == LATEST


def test_jobs_needing_a_module_take_the_viewer_default_not_a_snapshot_under_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = kits._index
    older = "2026-10-04-v9"  # has interior.js, the pinned baseline does not
    monkeypatch.setattr(kits, "_index", lambda s: {**real(s), "viewer": older})
    assert kits.kit_with("interior.js") == older
    monkeypatch.setattr(kits, "_index", real)
    assert kits.kit_with("interior.js") == kits.viewer_kit()


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
    # a module the snapshot lacks (the baseline predates interior.js) stays on the working copy
    extra = kits.rewrite_page(
        html.replace(
            '"housekit": "/kit/house.js"',
            '"housekit": "/kit/house.js", "housekit/interior": "/kit/interior.js"',
        ),
        BASELINE,
    )
    assert '"housekit/interior": "/kit/interior.js"' in extra
    dev = kits.rewrite_page(html, "dev")
    tag = kits.dev_kit_tag()
    assert f'"housekit": "/kit/house.js?v={tag}"' in dev  # a changed working copy is a new URL
    assert f'from "/kit/runtime.js?v={tag}"' in dev


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
    dev = await client.get("/scenes/p1/versions/3/index.html?kit=dev")
    assert "/kit/house.js?v=" in dev.text
    assert (await client.get("/scenes/p1/versions/3/index.html?kit=nope")).status_code == 404
    assert (await client.get("/scenes/p1/versions/9/index.html")).status_code == 404
    assert (await client.get("/scenes/../x/versions/3/index.html")).status_code in (404, 422)
    # the snapshot's files are served by the kit mount, and every kit or scene file tells the
    # browser to revalidate (the working copy and the scene sources change under the same URL)
    js = await client.get(f"/kit/versions/{BASELINE}/house.js")
    assert js.status_code == 200
    assert "export function perimeterWalls" in js.text
    assert js.headers["cache-control"] == "no-cache"
    dev_js = await client.get("/kit/house.js")
    assert dev_js.headers["cache-control"] == "no-cache"
    again = await client.get("/kit/house.js", headers={"if-none-match": dev_js.headers["etag"]})
    assert again.status_code == 304
    assert (await client.get("/scenes/p1/versions/3/src/scene.js")).status_code == 404


async def test_bought_models_are_served_masked(client: AsyncClient) -> None:
    # their license forbids handing out the files: they are served under /kit/assets/licensed/
    # masked (furnish.js maskLicensed), never as a GLB, and the kit mount lists no directory
    licensed = get_settings().KIT_DIR / "assets" / "licensed"
    files = sorted(licensed.glob("*/*.glbx"))
    assert files, "no bought models in kit/assets/licensed"
    for f in files:
        with f.open("rb") as fh:
            assert fh.read(4) == b"HGX1", f"{f} is not masked"
    r = await client.get(f"/kit/assets/licensed/{files[0].parent.name}/{files[0].name}")
    assert r.status_code == 200
    assert r.content[:4] == b"HGX1"
    assert (await client.get(f"/kit/assets/licensed/{files[0].parent.name}/")).status_code == 404
