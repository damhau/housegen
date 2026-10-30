"""The houses swissBUILDINGS3D lacks, from the lidar (#66): found, shaped, kept with the surroundings."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
from PIL import Image

from housegen.geo import context, far, landcover, lidar, swiss

GROUND = 500.0
RADIUS = 40


def _roof(kind: str, L: float, W: float, eave: float, ridge: float):  # type: ignore[no-untyped-def]
    """Height above the ground of a roof over an L x W rectangle centred on 0, its long side along x."""

    def at(u: np.ndarray, v: np.ndarray) -> np.ndarray:
        if kind == "flat":
            return np.full_like(u, eave)
        if kind == "gable":
            return ridge - (ridge - eave) * np.abs(v) / (W / 2)
        half = (L - W) / 2  # hip
        return ridge - (ridge - eave) * np.maximum(np.abs(v), np.abs(u) - half) / (W / 2)

    return at


def _house(
    cx: float,
    cz: float,
    L: float,
    W: float,
    kind: str = "gable",
    eave: float = 5.0,
    ridge: float = 8.0,
) -> np.ndarray:
    u, v = np.meshgrid(
        np.arange(-L / 2, L / 2, 0.25) + 0.125, np.arange(-W / 2, W / 2, 0.25) + 0.125
    )
    u, v = u.ravel(), v.ravel()
    h = _roof(kind, L, W, eave, ridge)(u, v)
    return np.c_[cx + u, cz + v, GROUND + h, np.full(len(u), lidar.BUILDING)]


def _cloud(*parts: np.ndarray) -> lidar.Cloud:
    a = np.concatenate(parts)
    return lidar.Cloud(x=a[:, 0], z=a[:, 1], y=a[:, 2], cls=a[:, 3].astype(np.int16), year=2025)


def _ground() -> np.ndarray:
    return np.full((2 * RADIUS + 1, 2 * RADIUS + 1), GROUND, dtype=np.float32)


def _known(cx: float, cz: float, L: float, W: float) -> swiss.Building:
    x0, x1, z0, z1 = cx - L / 2, cx + L / 2, cz - W / 2, cz + W / 2
    pos = [x0, GROUND + 6, z0, x1, GROUND + 6, z0, x1, GROUND + 6, z1, x0, GROUND + 6, z1]
    return swiss.Building(
        id="old", height=6, year=1990, positions=pos, roof=[0, 2, 1, 0, 3, 2], wall=[]
    )


def test_a_house_the_old_dataset_lacks_comes_from_the_lidar() -> None:
    cloud = _cloud(
        _house(10, -5, 12, 9),  # new: not in swissBUILDINGS3D
        _house(-15, 10, 10, 8),  # already there
        _house(25, 25, 1.5, 1.5),  # a bin: too small
        _house(-25, -25, 8, 6, "flat", eave=1.8),  # a garden wall: too low
    )
    found = lidar.new_buildings(cloud, [_known(-15, 10, 10, 8)], _ground(), RADIUS)
    assert len(found) == 1
    b = found[0]
    assert b.source == "lidar"
    assert b.year == 2025
    assert b.id == "lidar-10--5"
    p = np.asarray(b.positions).reshape(-1, 3)
    roof = p[np.asarray(b.roof)]
    # the ridge at 8 m, the walls down into the ground (0.3 m), the outline on the house's
    assert roof[:, 1].max() == pytest.approx(GROUND + 8.0, abs=0.3)
    assert p[:, 1].min() == pytest.approx(GROUND - 0.3, abs=0.01)
    assert p[:, 0].min() == pytest.approx(4.0, abs=0.6)
    assert p[:, 0].max() == pytest.approx(16.0, abs=0.6)
    assert b.height == pytest.approx(8.3, abs=0.3)
    # every roof triangle faces up
    t = roof.reshape(-1, 3, 3)
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    assert (n[:, 1] > 0).all()


def _top(b: swiss.Building, x: float, z: float) -> float:
    """The highest roof of `b` over (x, z): what a camera above sees there (its volumes overlap)."""
    p = np.asarray(b.positions).reshape(-1, 3)
    best = -np.inf
    for t in np.asarray(b.roof).reshape(-1, 3):
        (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = p[t]
        d = (z1 - z2) * (x0 - x2) + (x2 - x1) * (z0 - z2)
        if abs(d) < 1e-12:
            continue
        w0 = ((z1 - z2) * (x - x2) + (x2 - x1) * (z - z2)) / d
        w1 = ((z2 - z0) * (x - x2) + (x0 - x2) * (z - z2)) / d
        if min(w0, w1, 1 - w0 - w1) >= -1e-9:
            best = max(best, w0 * y0 + w1 * y1 + (1 - w0 - w1) * y2)
    return float(best)


def _steepest(b: swiss.Building) -> float:
    """The steepest roof triangle of `b`, in degrees: a step drawn as roof instead of a wall shows."""
    t = np.asarray(b.positions).reshape(-1, 3)[np.asarray(b.roof)].reshape(-1, 3, 3)
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    return float(np.degrees(np.arccos(np.abs(n[:, 1]) / np.linalg.norm(n, axis=1))).max())


def test_a_house_and_its_lower_garage_are_two_volumes_each_with_its_roof() -> None:
    # a 12 x 9 m gable house (eaves 5 m, ridge 8 m along x) and a 6 x 9 m flat-roofed garage (3 m) as
    # wide as its east end: one rectangle, one roof over both fits neither (#73)
    cloud = _cloud(_house(0, 0, 12, 9), _house(9, 0, 6, 9, "flat", eave=3.0))
    found = lidar.new_buildings(cloud, [], _ground(), RADIUS)
    assert len(found) == 1
    b = found[0]
    assert _top(b, -3, 0) == pytest.approx(GROUND + 8.0, abs=0.2)  # the ridge
    assert _top(b, -3, 3) == pytest.approx(GROUND + 6.0, abs=0.2)  # down the slope
    assert _top(b, 3, -3) == pytest.approx(GROUND + 6.0, abs=0.2)
    assert _top(b, 9, 0) == pytest.approx(GROUND + 3.0, abs=0.1)  # the garage, flat
    assert _top(b, 10.5, 3.5) == pytest.approx(GROUND + 3.0, abs=0.1)
    assert b.height == pytest.approx(8.3, abs=0.3)
    # the step from the garage up to the house is a wall: no roof steeper than the gable's 34°
    assert _steepest(b) < 36


def test_an_l_shaped_house_under_one_roof_comes_as_two_gabled_wings() -> None:
    # a 16 x 8 m bar along x and an 8 x 8 m wing south of its east half, both gabled (eaves 5 m,
    # ridges 8 m), the wing's roof running on into the bar's to the valleys: no step to split at,
    # and no simple roof over the L
    x, z = (
        g.ravel()
        for g in np.meshgrid(np.arange(-8, 8, 0.25) + 0.125, np.arange(-4, 12, 0.25) + 0.125)
    )
    bar, wing = np.abs(z) < 4, (x > 0) & (x < 8)
    h = np.maximum(
        np.where(bar, 8 - 3 * np.abs(z) / 4, -np.inf),
        np.where(wing, 8 - 3 * np.abs(x - 4) / 4, -np.inf),
    )
    keep = bar | wing
    pts = np.c_[x[keep], z[keep], GROUND + h[keep], np.full(int(keep.sum()), lidar.BUILDING)]
    found = lidar.new_buildings(_cloud(pts), [], _ground(), RADIUS)
    assert len(found) == 1
    b = found[0]
    for (px, pz), want in {
        (-6, 0): 8.0,  # the bar's ridge
        (-6, 3): 5.75,
        (4, 10): 8.0,  # the wing's ridge
        (7, 10): 5.75,
        (4, 0): 8.0,  # where the ridges cross
        (2, -2): 6.5,  # the valleys
        (6, 2): 6.5,
    }.items():
        assert _top(b, px, pz) == pytest.approx(GROUND + want, abs=0.25), (px, pz)
    assert _steepest(b) < 39  # the gables' 37°, nothing steeper


def test_a_roof_splits_into_volumes_where_it_steps_not_at_a_chimney() -> None:
    m = np.ones((20, 30), bool)  # 10 x 15 m
    h = np.full(m.shape, 5.0)
    h[8:11, 8:11] = 7.0  # a 1.5 x 1.5 m chimney: stays with its roof
    assert len(lidar._volumes(m, h)) == 1
    h[:, 20:] = 3.0  # a 10 x 5 m lower wing
    parts = lidar._volumes(m, h)
    assert len(parts) == 2
    assert sorted(int(p.sum()) for p in parts) == [200, 400]  # every cell in one of them


@pytest.mark.parametrize(("kind", "creases"), [("gable", 1), ("hip", 5), ("flat", 0)])
def test_the_roof_is_fitted_to_its_shape(kind: str, creases: int) -> None:
    from shapely.geometry import box

    pts = _house(0, 0, 14, 9, kind)
    at, found = lidar.roof_shape(pts[:, 0], pts[:, 1], pts[:, 2], box(-7, -4.5, 7, 4.5))
    assert len(found) == creases
    if kind == "flat":
        assert at(3, 2) == pytest.approx(GROUND + 5.0, abs=0.05)
        return
    assert at(0, 0) == pytest.approx(GROUND + 8.0, abs=0.15)  # the ridge
    assert at(0, 4.5) == pytest.approx(GROUND + 5.0, abs=0.15)  # the eaves
    end = GROUND + (
        5.0 if kind == "hip" else 8.0
    )  # a hip slopes down at the ends too, a gable does not
    assert at(7, 0) == pytest.approx(end, abs=0.15)


async def test_the_surroundings_keep_the_newer_houses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def terrain(*a: Any, **k: Any) -> np.ndarray:
        return np.full((2 * 100 + 1, 2 * 100 + 1), GROUND, dtype=np.float32)

    async def photo(*a: Any, **k: Any) -> bytes:
        buf = io.BytesIO()
        Image.new("RGB", (64, 64), (90, 120, 60)).save(buf, "JPEG")
        return buf.getvalue()

    async def buildings(*a: Any, **k: Any) -> list[swiss.Building]:
        return [_known(-15, 10, 10, 8)]

    async def cloud(*a: Any, **k: Any) -> lidar.Cloud:
        return _cloud(_house(10, -5, 12, 9), _house(-15, 10, 10, 8))

    async def no_far(*a: Any, **k: Any) -> Any:
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(swiss, "terrain", terrain)
    monkeypatch.setattr(swiss, "aerial_photo", photo)
    monkeypatch.setattr(swiss, "buildings", buildings)

    async def cover(*a: Any, **k: Any) -> list[landcover.Surface]:
        return [
            landcover.Surface(
                "jardin", [[(-30.0, -30.0), (30.0, -30.0), (30.0, 30.0), (-30.0, 30.0)]]
            ),
            landcover.Surface(
                "route_chemin", [[(-90.0, 40.0), (90.0, 40.0), (90.0, 46.0), (-90.0, 46.0)]]
            ),
        ]

    monkeypatch.setattr(lidar, "cloud", cloud)
    monkeypatch.setattr(landcover, "surfaces", cover)
    monkeypatch.setattr(far, "heights", no_far)
    place = swiss.Place(label="Le Mont 3013", kind="parcel", e=2537559, n=1157053)
    ctx = await context.build(tmp_path, place, radius=100)
    assert ctx["buildings"] == {"file": "buildings.json", "count": 2, "lidar": 1}
    assert ctx["lidar"] == {"year": 2025}
    assert "swissSURFACE3D" in ctx["credits"][0]
    assert ctx["cover"]["file"] == "cover.png"
    assert ctx["cover"]["extent"] == 100
    assert ctx["cover"]["channels"] == ["asphalt", "lawn", "paving", "gravel"]
    m = np.asarray(Image.open(tmp_path / "context" / "cover.png"))
    assert m.shape == (800, 800, 4)
    assert m[400, 400, 1] == 255  # the garden: lawn at the centre
    assert m[400 + 172, 400, 0] == 255  # the road, 43 m south
    assert m[400 + 172, 400, 1] == 0
    # what used to keep the photo, and each surface's colour (#74)
    assert ctx["cover"]["file2"] == "cover2.png"
    assert ctx["cover"]["channels2"] == ["field", "wood", "water", "pool"]
    assert ctx["cover"]["tint"] == "tint.png"
    m2 = np.asarray(Image.open(tmp_path / "context" / "cover2.png"))
    assert m2.shape == (800, 800, 4)
    assert m2.max() == 0  # neither a field nor a wood here
    tint = np.asarray(Image.open(tmp_path / "context" / "tint.png")).astype(int)
    assert tint.shape == (800, 800, 3)
    assert np.abs(tint[400, 400] - (90, 120, 60)).max() <= 6  # the garden, as photographed (JPEG)
    saved = json.loads((tmp_path / "context" / "buildings.json").read_text())["buildings"]
    assert [b["source"] for b in saved] == ["swissbuildings3d", "lidar"]


async def test_without_the_lidar_the_surroundings_are_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def terrain(*a: Any, **k: Any) -> np.ndarray:
        return np.full((201, 201), GROUND, dtype=np.float32)

    async def photo(*a: Any, **k: Any) -> bytes:
        buf = io.BytesIO()
        Image.new("RGB", (8, 8)).save(buf, "JPEG")
        return buf.getvalue()

    async def buildings(*a: Any, **k: Any) -> list[swiss.Building]:
        return []

    async def failing(*a: Any, **k: Any) -> Any:
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(swiss, "terrain", terrain)
    monkeypatch.setattr(swiss, "aerial_photo", photo)
    monkeypatch.setattr(swiss, "buildings", buildings)
    monkeypatch.setattr(lidar, "cloud", failing)
    monkeypatch.setattr(landcover, "surfaces", failing)
    monkeypatch.setattr(far, "heights", failing)
    ctx = await context.build(
        tmp_path, swiss.Place(label="x", kind="parcel", e=2537559, n=1157053), radius=100
    )
    assert ctx["lidar"] is None
    assert ctx["buildings"]["lidar"] == 0
    assert ctx["credits"] == context.CREDITS
    assert ctx["cover"] is None


def _crown(cx: float, cz: float, h: float, r: float) -> np.ndarray:
    """Vegetation points of a tree: a dome of radius r topping out at h."""
    u, v = np.meshgrid(np.arange(-r, r, 0.25) + 0.125, np.arange(-r, r, 0.25) + 0.125)
    d = np.hypot(u, v)
    keep = d < r
    top = h - (h * 0.4) * (d[keep] / r) ** 2
    return np.c_[cx + u[keep], cz + v[keep], GROUND + top, np.full(keep.sum(), lidar.VEGETATION)]


def test_the_trees_of_the_lidar_have_their_height_crown_and_kind() -> None:
    cloud = _cloud(
        _crown(-10, 5, 14.0, 4.0),  # a broad tree
        _crown(12, -8, 16.0, 2.6),  # a narrow one: a conifer
        _crown(0, -20, 2.0, 1.0),  # a bush
        _crown(20, 20, 0.8, 1.0),  # grass: left out
        _house(-20, -20, 8, 6),  # a house is not a tree
    )
    found = sorted(lidar.trees(cloud, _ground(), RADIUS), key=lambda t: -t.height)
    assert [t.kind for t in found] == ["pine", "broadleaf", "bush"]
    pine, broad, bush = found
    assert broad.x == pytest.approx(-10, abs=0.5)
    assert broad.z == pytest.approx(5, abs=0.5)
    assert broad.height == pytest.approx(14.0, abs=0.3)
    assert broad.radius == pytest.approx(4.0, abs=0.8)
    assert broad.ground == pytest.approx(GROUND)
    assert pine.height == pytest.approx(16.0, abs=0.3)
    assert bush.height == pytest.approx(2.0, abs=0.3)


GML = """<msGMLOutput><LCSF_layer><LCSF_feature><msGeometry><gml:Polygon srsName="EPSG:2056">
<gml:outerBoundaryIs><gml:LinearRing><gml:coordinates>2537500,1157000 2537520,1157000 2537520,1157020 2537500,1157020 2537500,1157000</gml:coordinates></gml:LinearRing></gml:outerBoundaryIs>
<gml:innerBoundaryIs><gml:LinearRing><gml:coordinates>2537505,1157005 2537510,1157005 2537510,1157010 2537505,1157005</gml:coordinates></gml:LinearRing></gml:innerBoundaryIs>
</gml:Polygon></msGeometry><NoOFS>5587</NoOFS><Genre>jardin</Genre><Canton>VD</Canton></LCSF_feature></LCSF_layer></msGMLOutput>"""


def test_the_land_cover_answer_is_read_in_the_local_frame() -> None:
    (s,) = landcover.parse_features(GML, 2537510, 1157010)
    assert s.kind == "jardin"
    assert s.rings[0][0] == (-10.0, 10.0)  # 10 m west, 10 m south
    assert len(s.rings) == 2  # with its hole


def test_each_surface_takes_its_colour_without_its_shadows() -> None:
    # a photo ± 50 m at 0.5 m a pixel: a dry field west with a crane's shadow across it, a green
    # garden east, a bright strip between them that no surface covers (#74)
    photo = np.zeros((200, 200, 3), np.uint8)
    photo[:, :100] = (190, 170, 110)
    photo[:, 100:] = (70, 120, 50)
    photo[90:110, :100] = (40, 35, 30)  # the shadow: 10 % of the field
    photo[:, 96:104] = (250, 250, 250)
    found = [
        landcover.Surface("champ_pre_paturage", [[(-50, -50), (-2, -50), (-2, 50), (-50, 50)]]),
        landcover.Surface("jardin", [[(2, -50), (50, -50), (50, 50), (2, 50)]]),
    ]
    t = landcover.tint(found, photo, ext=40, radius=50).astype(int)
    assert t.shape == (320, 320, 3)  # ± 40 m at 0.25 m
    assert tuple(t[160, 40]) == (190, 170, 110)  # in the shadow: the field's own colour
    assert tuple(t[10, 40]) == (190, 170, 110)
    assert tuple(t[160, 280]) == (70, 120, 50)  # the garden
    # between them: the photo blurred over metres (its bright strip mixed with both sides)
    assert t[160, 160].max() < 200
    assert tuple(t[160, 160]) not in {(190, 170, 110), (70, 120, 50)}
    m2 = landcover.masks(found, 40, landcover.CHANNELS2)
    assert m2[160, 40, 0] == 255  # the field in cover2's R
    assert m2[160, 280].max() == 0  # the garden in none of cover2's


def test_a_large_surface_keeps_its_greener_and_drier_parts() -> None:
    # one 80 x 40 m block of gardens, watered on the west, dry on the east: one colour for all of it
    # would paint the whole block the same (#74)
    photo = np.zeros((200, 200, 3), np.uint8)
    photo[:, :100] = (60, 110, 40)
    photo[:, 100:] = (150, 150, 80)
    found = [landcover.Surface("jardin", [[(-40, -20), (40, -20), (40, 20), (-40, 20)]])]
    t = landcover.tint(found, photo, ext=45, radius=50).astype(int)
    assert np.abs(t[180, 40] - (60, 110, 40)).max() <= 3  # 30 m west of the middle
    assert np.abs(t[180, 320] - (150, 150, 80)).max() <= 3  # 30 m east


def test_a_point_inside_every_region_between_the_outlines() -> None:
    img = np.full((40, 40), 255, dtype=np.uint8)
    img[20, :] = 0  # a line across: two regions
    img[:20, 20] = 0  # and the north half split: three
    pts = landcover.regions(img)
    assert len(pts) == 3
    assert all(img[j, i] == 255 for i, j in pts)
