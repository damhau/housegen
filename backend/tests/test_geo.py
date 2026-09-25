"""The surroundings (#39): the plans' parcel, the buildings' tiles in the local frame, the endpoints."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from housegen.core import db
from housegen.core.config import get_settings
from housegen.geo import context, swiss


def _pdf(tmp_path: Path, text: str) -> Path:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((40, 60), text)
    p = tmp_path / "plan.pdf"
    doc.save(p)
    return p


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        (
            "PROPRIETE DE CBA 2000 SA\nBF N°3013 A MONT-SUR-LAUSANNE 1052\nPLANS DE DEMANDE",
            "3013 Mont-sur-Lausanne",
        ),
        ("Villa familiale\nParcelle n° 412, Commune de Savigny\n", "412 Savigny"),
        ("Neubau Einfamilienhaus\nParzelle Nr. 1187 in Wädenswil\n", "1187 Wädenswil"),
    ],
)
def test_the_plans_title_block_names_the_parcel(tmp_path: Path, title: str, expected: str) -> None:
    assert context.suggestion_from_plans([_pdf(tmp_path, title)]) == expected


def test_no_parcel_in_the_plans(tmp_path: Path) -> None:
    assert context.suggestion_from_plans([_pdf(tmp_path, "Ground floor 1:100")]) is None


def _b3dm(e: float, n: float, h: float) -> bytes:
    """A tile of two 4 m cubes (batch 0 at (e, n), batch 1 20 m east), glTF y-up around an RTC centre."""
    import DracoPy
    from pyproj import Transformer

    to_ecef = Transformer.from_crs(4979, 4978, always_xy=True)
    to_wgs = Transformer.from_crs(2056, 4326, always_xy=True)
    rtc = np.array(to_ecef.transform(*to_wgs.transform(e, n), h))
    faces = np.array(
        [
            [0, 1, 3],
            [0, 3, 2],
            [4, 6, 7],
            [4, 7, 5],
            [0, 4, 5],
            [0, 5, 1],
            [2, 3, 7],
            [2, 7, 6],
            [0, 2, 6],
            [0, 6, 4],
            [1, 5, 7],
            [1, 7, 3],
        ]
    )
    pts, tris, batch = [], [], []
    for b, (de, dn) in enumerate([(0.0, 0.0), (20.0, 0.0)]):
        # a 4 m cube standing upright there: its corners placed in LV95 and heights, then to ECEF
        ecef = (
            np.array(
                [
                    to_ecef.transform(*to_wgs.transform(e + de + x, n + dn + y), h + z)
                    for x in (-2, 2)
                    for y in (-2, 2)
                    for z in (-2, 2)
                ]
            )
            - rtc
        )
        # glTF is y-up: the tile's z-up ECEF offset (x, y, z) is stored as (x, z, -y)
        pts.append(np.c_[ecef[:, 0], ecef[:, 2], -ecef[:, 1]])
        tris.append(faces + 8 * b)
        batch += [b] * 8
    points = np.concatenate(pts).astype(np.float32)
    draco = DracoPy.encode(
        points,
        np.concatenate(tris).astype(np.uint32),
        generic_attributes={0: np.array(batch, dtype=np.float32).reshape(-1, 1)},
    )
    gltf = {
        "asset": {"version": "2.0"},
        "nodes": [{"mesh": 0}],
        "meshes": [
            {
                "primitives": [
                    {
                        "attributes": {},
                        "extensions": {
                            "KHR_draco_mesh_compression": {
                                "bufferView": 0,
                                "attributes": {"POSITION": 1, "_BATCHID": 0},
                            }
                        },
                    }
                ]
            }
        ],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(draco)}],
        "buffers": [{"byteLength": len(draco)}],
    }
    js = json.dumps(gltf).encode()
    js += b" " * (-len(js) % 4)
    binary = draco + b"\0" * (-len(draco) % 4)
    glb = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js) + 8 + len(binary))
    glb += (
        struct.pack("<I4s", len(js), b"JSON")
        + js
        + struct.pack("<I4s", len(binary), b"BIN\0")
        + binary
    )
    ft = json.dumps({"BATCH_LENGTH": 2, "RTC_CENTER": rtc.tolist()}).encode()
    ft += b" " * (-(28 + len(ft)) % 8)
    bt = json.dumps({"Height": [4.0, 4.0], "UUID": ["a", "b"]}).encode()
    bt += b" " * (-(28 + len(ft) + len(bt)) % 8)
    head = struct.pack(
        "<4sIIIIII", b"b3dm", 1, 28 + len(ft) + len(bt) + len(glb), len(ft), 0, len(bt), 0
    )
    return head + ft + bt + glb


def test_a_buildings_tile_lands_in_the_local_frame() -> None:
    e0, n0 = 2537559.0, 1157053.0
    # the cubes stand 30 m east and 10 m north of the anchor
    tile = _b3dm(e0 + 30, n0 + 10, 720.0)
    out = swiss.decode_b3dm(tile, e0, n0)
    assert len(out) == 2
    by_id = {attrs["UUID"]: v for attrs, v, _f in out}
    a, b = by_id["a"], by_id["b"]
    assert abs(a[:, 0].mean() - 30) < 0.3  # x east
    assert abs(a[:, 2].mean() - -10) < 0.3  # z south: 10 m north
    assert abs(b[:, 0].mean() - 50) < 0.3
    assert abs(a[:, 1].mean() - 720) < 0.5  # ellipsoidal height, before the terrain sets it
    assert np.ptp(a[:, 1]) == pytest.approx(4, abs=0.05)  # upright: 4 m tall


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    await db.dispose_db()
    from housegen.main import create_app

    app = create_app()
    await db.init_db()

    async def fake_build(
        root: Path, place: swiss.Place, radius: int = 200, step: float = 1.0
    ) -> dict[str, Any]:
        d = context.context_dir(root)
        d.mkdir(parents=True, exist_ok=True)
        ctx = {
            "version": 1,
            "place": {
                "label": place.label,
                "kind": place.kind,
                "e": round(place.e),
                "n": round(place.n),
            },
            "radius": radius,
            "terrain": {"file": "terrain.bin", "size": 3, "step": 1.0},
            "photo": {"file": "photo.jpg", "pixels": 8},
            "buildings": {"file": "buildings.json", "count": 2},
            "alignment": {"x": 0.0, "z": 0.0, "rotation": 0.0, "ground": 667.15, "set": False},
            "credits": context.CREDITS,
            "fetched_at": "2026-09-25T10:00:00+00:00",
        }
        (d / "context.json").write_text(json.dumps(ctx), encoding="utf-8")
        return ctx

    monkeypatch.setattr(context, "build", fake_build)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    await db.dispose_db()
    get_settings.cache_clear()


async def test_fetch_align_and_share_the_surroundings(client: AsyncClient, tmp_path: Path) -> None:
    import pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_text((40, 60), "BF N°3013 A MONT-SUR-LAUSANNE 1052")
    r = await client.post(
        "/api/v1/projects",
        data={"name": "villa"},
        files=[("plans", ("p.pdf", doc.tobytes(), "application/pdf"))],
    )
    pid = r.json()["id"]

    before = (await client.get(f"/api/v1/projects/{pid}/surroundings")).json()
    assert before["exists"] is False
    assert before["suggestion"] == "3013 Mont-sur-Lausanne"
    assert (
        await client.patch(
            f"/api/v1/projects/{pid}/surroundings",
            json={"x": 1, "z": 2, "rotation": 3, "ground": 4},
        )
    ).status_code == 404

    place = {"label": "Le Mont-sur-Lausanne 3013", "kind": "parcel", "e": 2537559.0, "n": 1157053.1}
    got = (await client.post(f"/api/v1/projects/{pid}/surroundings", json={"place": place})).json()
    assert got["exists"]
    assert got["url"] == f"/scenes/{pid}/context/"
    assert got["alignment"]["set"] is False

    aligned = (
        await client.patch(
            f"/api/v1/projects/{pid}/surroundings",
            json={"x": -2, "z": -10, "rotation": -28, "ground": 667.6},
        )
    ).json()
    assert aligned["alignment"] == {
        "x": -2,
        "z": -10,
        "rotation": -28,
        "ground": 667.6,
        "set": True,
    }

    # fetched again at the same place, the owner's alignment stays
    again = (
        await client.post(f"/api/v1/projects/{pid}/surroundings", json={"place": place})
    ).json()
    assert again["alignment"]["rotation"] == -28

    token = (await client.post(f"/api/v1/projects/{pid}/share")).json()["token"]
    shared = (await client.get(f"/api/v1/shared/{token}")).json()
    assert shared["context_url"] == f"/scenes/{pid}/context/"

    assert (await client.delete(f"/api/v1/projects/{pid}/surroundings")).status_code == 204
    assert (await client.get(f"/api/v1/projects/{pid}/surroundings")).json()["exists"] is False


async def test_the_far_landscape_joins_the_surveyed_ground_and_drops_with_the_earth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from housegen.geo import far

    r = far.radii()
    assert r[0] == far.INNER
    assert abs(r[-1] - far.EXTENT) < 1
    assert (np.diff(r) > 0).all()

    # the elevation tiles: a flat 500 m everywhere; the surveyed ground (the disc's): 600 m
    async def flat(_client: object, lon: np.ndarray, _lat: np.ndarray, _z: int) -> np.ndarray:
        return np.full(lon.shape, 500.0, dtype=np.float32)

    monkeypatch.setattr(far, "_sample", flat)
    ground = np.full((401, 401), 600.0, dtype=np.float32)
    h = await far.heights(2537559.0, 1157053.0, None, ground, 200, 1.0)  # type: ignore[arg-type]
    assert h.shape == (far.RINGS, far.AZIMUTHS)
    assert abs(float(h[0].mean()) - 600) < 0.01  # under the disc's rim: the survey
    ring_1km = int(np.argmin(np.abs(r - 1000)))
    assert abs(float(h[ring_1km].mean()) - 500) < 0.2  # past 400 m: the tiles
    # at 80 km the Earth's curvature (with refraction) takes about 437 m off
    assert abs(float(h[-1].mean()) - (500 - 80_000**2 * 0.87 / (2 * 6_371_000))) < 1
