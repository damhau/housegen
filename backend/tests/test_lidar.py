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


def _footprint(b: swiss.Building, eaves: bool = False) -> Any:
    """The ground plan of `b`: its roof triangles seen from above (the walls' outline), as one
    polygon; with `eaves`, its eaves' too (the roof's outline)."""
    from shapely import unary_union
    from shapely.geometry import Polygon

    p = np.asarray(b.positions).reshape(-1, 3)[:, [0, 2]]
    tris = [Polygon(p[t]) for t in np.asarray(b.roof).reshape(-1, 3)]
    if eaves:
        tris += [Polygon(p[t]) for t in np.asarray(b.eave, dtype=np.int64).reshape(-1, 3)]
    return unary_union([t.buffer(1e-6) for t in tris if t.area > 1e-9]).simplify(0.05)


def _walls(b: swiss.Building) -> Any:
    """The walls' outline of `b` seen from above."""
    from shapely import unary_union
    from shapely.geometry import LineString

    p = np.asarray(b.positions).reshape(-1, 3)[:, [0, 2]]
    sides = [LineString(p[t]) for t in np.asarray(b.wall).reshape(-1, 3)]
    return unary_union([s for s in sides if s.length > 1e-6]).convex_hull


def _eave_faces(b: swiss.Building) -> tuple[np.ndarray, np.ndarray]:
    """The eaves' triangles of `b` (n x 3 x 3) and their unit normals."""
    t = np.asarray(b.positions).reshape(-1, 3)[np.asarray(b.eave, dtype=np.int64)].reshape(-1, 3, 3)
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    return t, n / np.linalg.norm(n, axis=1)[:, None]


def _corners(g: Any) -> list[float]:
    """The angles (degrees, 0-180) between each corner's two walls in the outline of `g`."""
    ring = np.asarray(g.exterior.coords)[:-1]
    out = []
    for i in range(len(ring)):
        a, c = ring[i - 1] - ring[i], ring[(i + 1) % len(ring)] - ring[i]
        cos = a @ c / (np.linalg.norm(a) * np.linalg.norm(c))
        out.append(float(np.degrees(np.arccos(np.clip(cos, -1, 1)))))
    return out


def _turned(pts: np.ndarray, deg: float, cx: float, cz: float) -> np.ndarray:
    """`pts` turned by `deg` around (cx, cz)."""
    t = np.radians(deg)
    x, z = pts[:, 0] - cx, pts[:, 1] - cz
    out = pts.copy()
    out[:, 0] = cx + x * np.cos(t) - z * np.sin(t)
    out[:, 1] = cz + x * np.sin(t) + z * np.cos(t)
    return out


def _l_house() -> np.ndarray:
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
    return np.c_[x[keep], z[keep], GROUND + h[keep], np.full(int(keep.sum()), lidar.BUILDING)]


def test_an_l_shaped_house_under_one_roof_comes_as_a_plain_gable_over_its_l() -> None:
    # the owner's rule (#73): when no simple roof fits, a plain gable at the lidar's eaves and ridge,
    # never the faceted roof or the overlapping wings that used to follow the valleys
    found = lidar.new_buildings(_cloud(_l_house()), [], _ground(), RADIUS)
    assert len(found) == 1
    b = found[0]
    plan = _footprint(b, eaves=True)
    assert plan.geom_type == "Polygon"
    assert plan.area == pytest.approx(16 * 8 + 8 * 8, rel=0.04)
    assert len(plan.exterior.coords) - 1 == 6  # the L
    assert all(abs(a - 90) < 1 for a in _corners(plan))
    t = np.asarray(b.positions).reshape(-1, 3)[np.asarray(b.roof)].reshape(-1, 3, 3)
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    n /= np.linalg.norm(n, axis=1)[:, None]
    # two slopes facing away from each other, nothing else: no facets
    pitch = np.degrees(np.arccos(n[:, 1]))
    facing = np.degrees(np.arctan2(n[:, 2], n[:, 0]))
    assert np.ptp(pitch) < 2
    assert np.abs((facing - facing[0] + 90) % 180 - 90).max() < 2.5  # positions are to the cm
    roof = t.reshape(-1, 3)[:, 1]
    assert roof.max() == pytest.approx(GROUND + 8.0, abs=0.3)
    assert _steepest(b) < 37


def test_a_tilted_house_the_survey_lacks_is_squared_on_its_own_axes() -> None:
    pts = _turned(_house(0, 0, 13, 9), 27, 0, 0)
    (b,) = lidar.new_buildings(_cloud(pts), [], _ground(), RADIUS)
    plan = _footprint(b, eaves=True)
    assert len(plan.exterior.coords) - 1 == 4
    assert all(abs(a - 90) < 1 for a in _corners(plan))
    assert plan.area == pytest.approx(13 * 9, rel=0.06)
    ring = np.asarray(plan.exterior.coords)
    sides = np.diff(ring, axis=0)
    deg = np.degrees(np.arctan2(sides[:, 1], sides[:, 0])) % 90
    assert np.allclose(deg, 27, atol=1.5)
    assert _top(b, 0, 0) == pytest.approx(GROUND + 8.0, abs=0.2)  # the lidar's ridge


def test_a_house_takes_the_survey_outline_and_the_lidar_heights() -> None:
    from shapely import affinity
    from shapely.geometry import box

    # the survey's walls 0.4 m in from the lidar's roof edge (the eaves) and 2° off its axes
    survey = affinity.rotate(box(4.4, -9.1, 15.6, -0.9), 2, origin=(10, -5))
    (b,) = lidar.new_buildings(
        _cloud(_house(10, -5, 12, 9)), [], _ground(), RADIUS, survey=[survey]
    )
    plan = _footprint(b)
    assert plan.symmetric_difference(survey).area < 0.5
    assert _top(b, 10, -5) == pytest.approx(GROUND + 8.0, abs=0.3)
    assert _top(b, 10, -1.5) == pytest.approx(GROUND + 5.7, abs=0.4)


def _offset(a: Any, b: Any) -> float:
    """How far outline `a` reaches past outline `b` on average (m)."""
    return float((a.area - b.area) / b.length)


def test_a_pitched_roof_reaches_past_the_survey_walls_as_far_as_the_lidar_sees_it() -> None:
    from shapely.geometry import box

    # #123: the lidar's roof reaches 0.8 m past the survey's walls (its edge across the cells, as a
    # real roof's falls); the walls stay on the survey, the roof goes on to its edge
    survey = box(4.95, -8.75, 15.05, -1.25)
    (b,) = lidar.new_buildings(
        _cloud(_house(10, -5, 11.7, 9.1)), [], _ground(), RADIUS, survey=[survey]
    )
    assert _footprint(b).symmetric_difference(survey).area < 0.5
    assert _walls(b).symmetric_difference(survey).area < 0.5
    roof = _footprint(b, eaves=True)
    assert _offset(roof, survey) == pytest.approx(0.8, abs=0.25)
    # the eaves carry the roof's planes on, down to the lidar's eaves (5 m), with a fascia under their
    # edge and a soffit back to the walls, facing down
    t, n = _eave_faces(b)
    tops = t[n[:, 1] > 0.3]
    assert tops[:, :, 1].min() == pytest.approx(GROUND + 5.0, abs=0.35)
    assert len(t[np.abs(n[:, 1]) < 0.1])
    assert len(t[n[:, 1] < -0.3])
    depth: dict[tuple[float, float], list[float]] = {}
    for x, y, z in t.reshape(-1, 3):
        depth.setdefault((round(x, 2), round(z, 2)), []).append(y)
    assert max(np.ptp(ys) for ys in depth.values()) == pytest.approx(lidar.FASCIA, abs=0.02)


def test_eaves_reach_past_survey_walls_within_their_range() -> None:
    from shapely.geometry import box

    # the survey's walls 3 m in from the lidar's roof on each side (a terrace roof, a misplaced
    # outline): no eaves that wide, the usual ones
    survey = box(7, -7, 13, -3)
    (b,) = lidar.new_buildings(
        _cloud(_house(10, -5, 12, 10)), [], _ground(), RADIUS, survey=[survey]
    )
    assert _offset(_footprint(b, eaves=True), survey) == pytest.approx(lidar.EAVE, abs=0.1)


def test_the_walls_of_a_house_the_survey_lacks_stand_inside_its_roof() -> None:
    # the lidar's outline is the roof's, eaves included: the walls stand EAVE inside it
    (b,) = lidar.new_buildings(_cloud(_house(0, 0, 13, 9)), [], _ground(), RADIUS)
    roof = _footprint(b, eaves=True)
    assert roof.area == pytest.approx(13 * 9, rel=0.06)
    walls = _footprint(b)
    assert walls.area == pytest.approx((13 - 2 * lidar.EAVE) * (9 - 2 * lidar.EAVE), rel=0.06)
    assert _walls(b).symmetric_difference(walls).area < 0.5
    assert roof.buffer(0.01).contains(walls)
    # inset, they still face outward: a wall facing in reads as the inside of a building to the openings' probes
    p = np.asarray(b.positions).reshape(-1, 3)
    centre = np.asarray(walls.centroid.coords[0])
    for t in np.asarray(b.wall).reshape(-1, 3):
        n = np.cross(p[t[1]] - p[t[0]], p[t[2]] - p[t[0]])
        if np.hypot(n[0], n[2]) > 1e-6:
            assert n[0] * (p[t, 0].mean() - centre[0]) + n[2] * (p[t, 2].mean() - centre[1]) > 0


def test_a_flat_roof_has_no_eaves() -> None:
    (b,) = lidar.new_buildings(_cloud(_house(0, 0, 13, 9, "flat", eave=6.0)), [], _ground(), RADIUS)
    assert b.eave == []
    assert _footprint(b).area == pytest.approx(13 * 9, rel=0.06)


def test_a_house_beside_its_garage_keeps_their_common_wall_under_its_eaves() -> None:
    # the house's walls stand in from its roof's edge, but not along the garage it meets
    cloud = _cloud(_house(0, 0, 12, 9), _house(9, 0, 6, 9, "flat", eave=3.0))
    (b,) = lidar.new_buildings(cloud, [], _ground(), RADIUS)
    walls = _footprint(b)
    x0, z0, x1, _ = walls.bounds
    assert x0 == pytest.approx(-6 + lidar.EAVE, abs=0.3)
    assert x1 == pytest.approx(12, abs=0.3)  # the garage's far end: a flat roof, no eaves
    assert z0 == pytest.approx(-4.5, abs=0.3)  # the garage is as wide as the house's roof
    assert _top(b, 5.8, 0) == pytest.approx(GROUND + 8.0, abs=0.4)  # the house's walls meet it


def test_eaves_do_not_reach_into_a_known_building() -> None:
    from shapely.geometry import box

    # a new house whose survey outline meets an old one (a semi-detached pair, the old half known)
    survey = box(0, -4, 10, 4)
    cloud = _cloud(_house(5, 0, 11.5, 9), _house(-6, 0, 12, 8))
    (b,) = lidar.new_buildings(cloud, [_known(-6, 0, 12, 8)], _ground(), RADIUS, survey=[survey])
    assert _footprint(b, eaves=True).intersection(box(-12, -4, 0, 4)).area < 0.05


def test_a_gables_overhang_passes_over_a_garage_well_under_it() -> None:
    from shapely import unary_union
    from shapely.geometry import Polygon, box

    # a gabled house (eaves 5 m) and a flat garage against its east gable: the overhang runs on above
    # a 3 m garage; one nearly up to the eaves would take it through its roof, so it stops there
    house, garage = box(-5.5, -4, 5.5, 4), box(5.5, -3, 11, 3)
    for height, over in ((3.0, True), (4.8, False)):
        cloud = _cloud(_house(0, 0, 12, 9), _house(8.5, 0, 5, 6, "flat", eave=height))
        (b,) = lidar.new_buildings(cloud, [], _ground(), RADIUS, survey=[house, garage])
        t, n = _eave_faces(b)
        tops = unary_union([Polygon(q[:, [0, 2]]) for q in t[n[:, 1] > 0.3]])
        assert (tops.intersection(box(5.5, -2.5, 6.5, 2.5)).area > 1.5) is over


def test_where_a_lower_wing_meets_a_house_its_eaves_run_to_the_walls() -> None:
    from shapely.geometry import Polygon, box

    # a gabled wing (eaves 3.5 m) against the house's east wall (eaves 5 m): round the corner where
    # they meet, the wing's eave runs on to the house's wall; the house's stood out over it as a ledge
    house, wing = box(-5.5, -4, 5.5, 4), box(5.5, -2.5, 11.5, 2.5)
    cloud = _cloud(_house(0, 0, 12, 9), _house(9, 0, 6, 6, eave=3.5, ridge=5.0))
    (b,) = lidar.new_buildings(cloud, [], _ground(), RADIUS, survey=[house, wing])
    t, n = _eave_faces(b)
    corner = box(5.5, 2.5, 5.8, 2.8)
    over = [q for q in t[n[:, 1] > 0.3] if Polygon(q[:, [0, 2]]).intersection(corner).area > 0.005]
    assert sum(Polygon(q[:, [0, 2]]).intersection(corner).area for q in over) > 0.08
    assert max(q[:, 1].max() for q in over) < GROUND + 4.0


def test_a_narrow_part_gets_no_gable() -> None:
    # a 10 x 3 m part with a gable in the lidar: too narrow for one (#123), a single slope or flat
    (b,) = lidar.new_buildings(
        _cloud(_house(0, 0, 10, 3, eave=3.0, ridge=4.0)), [], _ground(), RADIUS
    )
    t = np.asarray(b.positions).reshape(-1, 3)[np.asarray(b.roof)].reshape(-1, 3, 3)
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    n /= np.linalg.norm(n, axis=1)[:, None]
    assert np.abs(n - n[0]).max() < 0.02  # one plane


def test_no_roof_is_steeper_than_max_pitch() -> None:
    # a 6 m wide gable rising 4 m (53°): a single slope or flat instead
    (b,) = lidar.new_buildings(
        _cloud(_house(0, 0, 10, 6, eave=3.0, ridge=7.0)), [], _ground(), RADIUS
    )
    assert _steepest(b) <= lidar.MAX_PITCH + 0.5


def test_a_survey_outline_over_a_house_and_its_garage_keeps_both_roofs() -> None:
    from shapely.geometry import box

    survey = box(-6, -4.5, 12, 4.5)
    cloud = _cloud(_house(0, 0, 12, 9), _house(9, 0, 6, 9, "flat", eave=3.0))
    (b,) = lidar.new_buildings(cloud, [], _ground(), RADIUS, survey=[survey])
    assert _footprint(b).symmetric_difference(survey).area < 0.5
    assert _top(b, -3, 0) == pytest.approx(GROUND + 8.0, abs=0.2)
    assert _top(b, 9, 0) == pytest.approx(GROUND + 3.0, abs=0.1)
    assert _steepest(b) < 36


def test_two_attached_houses_keep_their_own_survey_outlines() -> None:
    from shapely.geometry import box

    # a pair of semi-detached houses under one roof: the survey parts them, the lidar cannot
    left, right = box(-8, -4.5, 0, 4.5), box(0, -4.5, 8, 4.5)
    (b,) = lidar.new_buildings(
        _cloud(_house(0, 0, 16, 9)), [], _ground(), RADIUS, survey=[left, right]
    )
    assert _footprint(b).symmetric_difference(left.union(right)).area < 0.5
    assert _top(b, -4, 0) == pytest.approx(GROUND + 8.0, abs=0.2)
    assert _top(b, 4, 3) == pytest.approx(GROUND + 6.0, abs=0.2)


def test_the_plain_roof_is_flat_when_the_heights_barely_vary() -> None:
    from shapely.geometry import box

    rng = np.random.default_rng(1)
    outline = box(0, 0, 14, 8)
    x, z = rng.uniform(0, 14, 500), rng.uniform(0, 8, 500)
    lumpy = GROUND + 4 + rng.normal(0, 0.15, 500)  # a flat roof with its plant and parapets
    at, creases, slope = lidar._plain_roof(x, z, lumpy, outline)
    assert creases == []
    assert slope == 0
    assert at(3, 3) == pytest.approx(float(np.median(lumpy)))
    spread = GROUND + rng.uniform(4, 7, 500)
    at, creases, slope = lidar._plain_roof(x, z, spread, outline)
    (start, end), *_ = creases
    assert start[1] == pytest.approx(4)  # the ridge along the long side
    assert end[1] == pytest.approx(4)
    assert at(7, 4) == pytest.approx(float(np.percentile(spread, 95)))
    assert at(7, 0) == pytest.approx(float(np.percentile(spread, 5)))
    assert np.degrees(np.arctan(slope)) < 45


def test_a_plain_roof_too_steep_for_a_gable_leans_one_way_or_lies_flat() -> None:
    from shapely.geometry import box

    # #123: a part whose heights span 5 m over 3 m stood as a wedge
    rng = np.random.default_rng(2)
    x, z = rng.uniform(0, 5, 400), rng.uniform(0, 3, 400)
    leaning = GROUND + 4 + 0.6 * z + rng.normal(0, 0.9, 400)
    at, creases, slope = lidar._plain_roof(x, z, leaning, box(0, 0, 5, 3))
    assert creases == []
    assert slope == pytest.approx(0.6, abs=0.15)
    assert at(2, 3) > at(2, 0)
    wall_like = GROUND + 4 + 1.8 * z + rng.normal(0, 0.9, 400)
    at, creases, slope = lidar._plain_roof(x, z, wall_like, box(0, 0, 5, 3))
    assert (creases, slope) == ([], 0.0)
    assert at(2, 0) == at(2, 3) == pytest.approx(float(np.median(wall_like)))


async def test_the_survey_is_asked_only_about_the_houses_it_does_not_cover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[list[tuple[float, float]]] = []
    house = landcover.Surface("batiment", [[(4.4, -9.1), (15.6, -9.1), (15.6, -0.9), (4.4, -0.9)]])

    async def at(e0: float, n0: float, points: list[tuple[float, float]], client: Any) -> Any:
        asked.append(points)
        return [house]

    monkeypatch.setattr(landcover, "surfaces_at", at)
    cloud = _cloud(_house(10, -5, 12, 9), _house(-15, 10, 10, 8))
    garden = landcover.Surface("jardin", [[(-25, 0), (-5, 0), (-5, 20), (-25, 20)]])
    found = await context._survey(0, 0, cloud, [], RADIUS, [garden], None)  # type: ignore[arg-type]
    assert len(asked) == 1  # the house in the garden is known to be no building of the survey's
    ((x, z),) = asked[0]
    assert x == pytest.approx(10, abs=4)
    assert z == pytest.approx(-5, abs=3)
    assert [s.kind for s in found] == ["jardin", "batiment"]


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
