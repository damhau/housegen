"""The lidar around a place (swissSURFACE3D, the classified point cloud): what swissBUILDINGS3D misses.

swissBUILDINGS3D is a few years behind: a neighbourhood built since its edition is missing (#66),
while the newest lidar flight (2025 around Lausanne) has it. Its points are read by area from the
cloud-optimised files (COPC) over HTTP, at 0.5 m, in the same local frame as the rest (x east,
z south, y the altitude, LN02 like swissALTI3D: no offset).

Classes (ASPRS, as swisstopo uses them): 2 ground, 3 vegetation, 6 building.

A new building's walls are the survey's outline (AV/MO, the land cover's "batiment") where one lies
under it, else its lidar outline squared; its roofs are simple (flat, a single pitch, a gable, a hip)
or, where none fits, a plain gable or flat at the measured heights (#73).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx
import numpy as np

from housegen.geo import swiss

logger = logging.getLogger(__name__)

COLLECTION = "ch.swisstopo.swisssurface3d"
CELL = 0.5  # metres: the query's resolution and the grid the buildings are found on
GROUND, VEGETATION, BUILDING = 2, 3, 6
MIN_AREA = 12.0  # m²: a garden shed and up
MIN_HEIGHT = 2.5  # m above the ground
ROOF_FIT = 0.6  # m (RMS): a simple roof shape further off the lidar's heights does not fit
# a roof none fits is drawn plain (#73: a faceted one read as rock, two overlapping wings cut through
# their walls): flat where half its heights lie within FLAT_IQR, else a gable
FLAT_IQR = 0.5  # m
# a house and its lower wing or garage are two volumes, each with its own roof (#73): split where two
# neighbouring cells differ by more than STEP (a wall: steeper than any roof); a piece under MIN_PART
# (a dormer, a chimney, a porch) stays with its neighbours
STEP = 1.0  # m
MIN_PART = 10.0  # m²
# the walls (#73): the survey's outline of a building under the house, when at least SURVEY_SHARE of it
# lies on the house's cells (the lidar sees the roof, eaves included, so a surveyed house lies inside)
SURVEY_SHARE = 0.6
# else the lidar's outline squared: the rectangle on its axes, cut at one or two corners (an L, a T, a
# Z) when a cut gains NOTCH_GAIN and is NOTCH_SIDE wide each way, if it covers the cells within
# SQUARE_FIT (intersection over union, on a SQUARE_CELL grid; less on a small part, whose outline the
# CELL staircase blurs more), the traced outline otherwise
SQUARE_FIT = 0.85
# a part beside walls already drawn (a garage by its house) takes their axes down to BESIDE_FIT: on a
# small part the cells' staircase weighs more, and the walls it meets are surer than its own outline
BESIDE_FIT = 0.7
NOTCH_GAIN = 0.04
NOTCH_SIDE = 2.0  # m
SQUARE_CELL = 0.25  # m
SNAP = 1.5  # m: a squared part this close to walls already drawn is grown to meet them


@dataclass
class Cloud:
    x: np.ndarray  # local, metres east of the anchor
    z: np.ndarray  # local, metres south
    y: np.ndarray  # altitude (LN02)
    cls: np.ndarray  # ASPRS class
    year: int | None  # the flight


async def cloud(e0: float, n0: float, radius: int, client: httpx.AsyncClient) -> Cloud | None:
    """The newest classified points over the square anchor ± radius, or None where the newest edition
    is not cloud-optimised (older flights come as zipped LAS only)."""
    tiles = [
        (ke * 1000 + 500, kn * 1000 + 500)
        for ke in range(int((e0 - radius) // 1000), int((e0 + radius) // 1000) + 1)
        for kn in range(int((n0 - radius) // 1000), int((n0 + radius) // 1000) + 1)
    ]
    items = await asyncio.gather(*(swiss._latest_item(client, COLLECTION, e, n) for e, n in tiles))
    hrefs, years = [], []
    for it in items:
        if not it:
            continue
        for a in it["assets"].values():
            if a["href"].endswith(".copc.laz"):
                hrefs.append(a["href"])
                years.append(int(it["properties"].get("datetime", "0")[:4] or 0))
    if not hrefs:
        return None
    return await asyncio.to_thread(_read, hrefs, e0, n0, radius, min(years) or None)


def _read(hrefs: list[str], e0: float, n0: float, radius: int, year: int | None) -> Cloud:
    import laspy

    parts: list[np.ndarray] = []
    bounds = laspy.copc.Bounds(
        mins=np.array([e0 - radius, n0 - radius]), maxs=np.array([e0 + radius, n0 + radius])
    )
    for href in hrefs:
        with laspy.CopcReader.open(href) as r:
            p = r.query(bounds=bounds, resolution=CELL)
            parts.append(
                np.c_[
                    np.asarray(p.x) - e0,
                    -(np.asarray(p.y) - n0),
                    np.asarray(p.z),
                    np.asarray(p.classification),
                ]
            )
    a = np.concatenate(parts) if parts else np.zeros((0, 4))
    return Cloud(x=a[:, 0], z=a[:, 1], y=a[:, 2], cls=a[:, 3].astype(np.int16), year=year)


def _grid(radius: int) -> tuple[int, Any]:
    from rasterio.transform import Affine

    n = round(2 * radius / CELL)
    # rows run north → south (z grows), columns west → east
    return n, Affine(CELL, 0, -radius, 0, CELL, -radius)


def footprints(buildings: list[swiss.Building], radius: int) -> np.ndarray:
    """Where the given buildings stand (their roofs seen from above), on the CELL grid."""
    from rasterio.features import rasterize
    from shapely.geometry import Polygon

    n, transform = _grid(radius)
    shapes = []
    for b in buildings:
        p = np.asarray(b.positions, dtype=np.float64).reshape(-1, 3)
        r = np.asarray(b.roof, dtype=np.int64).reshape(-1, 3)
        for t in r:
            tri = Polygon(p[t][:, [0, 2]])
            if tri.area > 1e-4:
                shapes.append((tri, 1))
    if not shapes:
        return np.zeros((n, n), dtype=bool)
    out = rasterize(shapes, out_shape=(n, n), transform=transform, all_touched=True, fill=0)
    return np.asarray(out, dtype=bool)


def _found(
    c: Cloud, known: list[swiss.Building], radius: int
) -> tuple[np.ndarray, np.ndarray, list[tuple[int, Any]]]:
    """The lidar's buildings that `known` lacks, on the CELL grid: the highest point of each cell (NaN
    where none), the buildings' labels, and the (label, slices) of each large enough and not cut by the
    square's edge (half a building: swissBUILDINGS3D's are kept by their centre)."""
    from scipy import ndimage as ndi

    n, _ = _grid(radius)
    sel = c.cls == BUILDING
    i = np.clip(((c.x[sel] + radius) / CELL).astype(int), 0, n - 1)
    j = np.clip(((c.z[sel] + radius) / CELL).astype(int), 0, n - 1)
    top = np.full((n, n), np.nan)
    np.fmax.at(top, (j, i), c.y[sel])
    mask = ndi.binary_closing(~np.isnan(top), iterations=2)
    # what swissBUILDINGS3D already has (and a 1.5 m margin: its outlines and the lidar's differ a little)
    mask &= ~ndi.binary_dilation(footprints(known, radius), iterations=3)
    labels, _ = ndi.label(mask)
    edge = 4  # cells (2 m)
    found = []
    for k, sl in enumerate(ndi.find_objects(labels), start=1):
        if sl is None or (labels[sl] == k).sum() * CELL * CELL < MIN_AREA:
            continue
        if min(sl[0].start, sl[1].start) < edge or max(sl[0].stop, sl[1].stop) > n - edge:
            continue
        found.append((k, sl))
    return top, labels, found


def unsurveyed(
    c: Cloud, known: list[swiss.Building], radius: int, surveyed: list[Any]
) -> list[tuple[float, float]]:
    """Where to ask the survey about the lidar's new buildings: a point deep inside each part of them
    (MIN_AREA and up, the eaves' strips left out) that none of the `surveyed` surfaces (polygons, local,
    of any kind) covers."""
    from rasterio.features import rasterize
    from scipy import ndimage as ndi

    n, transform = _grid(radius)
    _, labels, found = _found(c, known, radius)
    free = np.isin(labels, [k for k, _ in found])
    shapes = [(p, 1) for p in surveyed if not p.is_empty]
    if shapes:
        free &= rasterize(shapes, out_shape=(n, n), transform=transform, fill=0, dtype="uint8") == 0
    free = ndi.binary_opening(free, structure=np.ones((3, 3)), iterations=2)
    parts, _ = ndi.label(free)
    depth = ndi.distance_transform_edt(free)
    out = []
    for k, sl in enumerate(ndi.find_objects(parts), start=1):
        if sl is None:
            continue
        m = parts[sl] == k
        if m.sum() * CELL * CELL < MIN_AREA:
            continue
        j, i = np.unravel_index(int(np.argmax(np.where(m, depth[sl], -1))), m.shape)
        x = -radius + (i + sl[1].start + 0.5) * CELL
        z = -radius + (j + sl[0].start + 0.5) * CELL
        out.append((round(float(x), 2), round(float(z), 2)))
    return out


def new_buildings(
    c: Cloud,
    known: list[swiss.Building],
    ground: np.ndarray,
    radius: int,
    step: float = 1.0,
    survey: Sequence[Any] = (),
) -> list[swiss.Building]:
    """The buildings of the lidar that `known` lacks, as plain volumes with walls down to the ground
    (#73). The walls stand on the survey's outline of the building (`survey`: its buildings' polygons,
    local) where it has one under the house, else on the lidar's outline squared (a rectangle, an L…).
    A building whose roof steps (a house and its lower wing or garage) is cut straight across into one
    volume per level. Each volume has the simple roof (flat, a single pitch, a gable, a hip) that fits
    the lidar's heights, or, when none does, a plain gable or flat roof at the heights it measured."""
    from scipy import ndimage as ndi
    from shapely import contains_xy, unary_union

    _, transform = _grid(radius)
    top, labels, found = _found(c, known, radius)
    polys = [p for p in survey if p.geom_type == "Polygon" and p.area >= 1.0]
    owners = _owners(polys, labels, transform)
    size = ground.shape[0]

    def ground_at(x: float, z: float) -> float:
        gi = int(np.clip(round((x + radius) / step), 0, size - 1))
        gj = int(np.clip(round((z + radius) / step), 0, size - 1))
        return float(ground[gj, gi])

    out: list[swiss.Building] = []
    tally = {"survey": 0, "squared": 0, "traced": 0, "plain": 0}
    for k, sl in found:
        m = labels[sl] == k
        roof = np.where(m, top[sl], np.nan)
        # the cells inside the outline with no point (closing, a skylight): the nearest roof height
        missing = np.isnan(roof)
        if missing.all():
            continue
        idx = ndi.distance_transform_edt(missing, return_distances=False, return_indices=True)
        smooth = ndi.median_filter(roof[tuple(idx)], size=3)
        here = transform @ transform.translation(sl[1].start, sl[0].start)
        volumes = _volumes(m, smooth)
        mine = [p for p, o in zip(polys, owners, strict=True) if o == k]
        walls = _outlines(volumes, here, mine)
        if not walls:
            continue
        rings = np.concatenate([np.asarray(w.exterior.coords) for w, _ in walls])
        base = min(ground_at(x, z) for x, z in rings) - 0.3
        if np.nanpercentile(roof, 95) - base < MIN_HEIGHT:
            continue
        level = np.zeros(m.shape, np.int64)
        for v, cells in enumerate(volumes):
            level[cells] = v
        # the roof heights of the building's cells, at their centres, and each one's level
        cj, ci = np.nonzero(m & ~missing)
        hx = -radius + (ci + sl[1].start + 0.5) * CELL
        hz = -radius + (cj + sl[0].start + 0.5) * CELL
        hh, hl = smooth[cj, ci], level[cj, ci]
        positions: list[np.ndarray] = []
        roof_tris: list[int] = []
        wall_tris: list[int] = []
        plain = False
        for wall, _ in walls:
            inside = contains_xy(wall, hx, hz)
            for piece, lv in _split(wall, hx[inside], hz[inside], hl[inside]):
                own = contains_xy(piece, hx, hz)
                if (own & (hl == lv)).sum() >= 16:
                    own &= hl == lv
                if own.sum() < 4:
                    continue
                at, creases, simple = _roof(hx[own], hz[own], hh[own], piece)
                plain |= not simple
                p, r, w = _mesh(piece, at, creases, base)
                k0 = len(positions)
                positions += p
                roof_tris += [k0 + t for t in r]
                wall_tris += [k0 + t for t in w]
        if not roof_tris:
            continue
        kinds = {kind for _, kind in walls}
        tally["survey" if "survey" in kinds else "traced" if "traced" in kinds else "squared"] += 1
        tally["plain"] += plain
        centre = unary_union([w for w, _ in walls]).centroid
        cx, cz = centre.x, centre.y
        out.append(
            swiss.Building(
                id=f"lidar-{round(cx)}-{round(cz)}",
                height=round(float(np.nanpercentile(roof, 95)) - base, 2),
                year=c.year,
                positions=[round(float(q), 2) for p in positions for q in p],
                roof=[int(t) for t in roof_tris],
                wall=[int(t) for t in wall_tris],
                source="lidar",
            )
        )
    logger.info("geo.lidar_buildings", extra={"houses": len(out), **tally})
    return out


def _owners(polys: list[Any], labels: np.ndarray, transform: Any) -> list[int]:
    """The lidar building (label) each survey outline belongs to: the one SURVEY_SHARE of its cells
    lie on; 0 for none."""
    from rasterio.features import rasterize

    if not polys:
        return []
    ids = rasterize(
        [(p, i + 1) for i, p in enumerate(polys)],
        out_shape=labels.shape,
        transform=transform,
        fill=0,
        dtype="int32",
    )
    total = np.bincount(ids.ravel(), minlength=len(polys) + 1)
    on = (ids > 0) & (labels > 0)
    width = int(labels.max()) + 1
    pairs, counts = np.unique(ids[on].astype(np.int64) * width + labels[on], return_counts=True)
    owners, best = [0] * len(polys), [0] * len(polys)
    for pair, count in zip(pairs, counts, strict=True):
        pid, label = divmod(int(pair), width)
        if count > best[pid - 1] and count >= SURVEY_SHARE * total[pid]:
            owners[pid - 1], best[pid - 1] = label, int(count)
    return owners


def _outlines(
    volumes: list[np.ndarray], transform: Any, survey: list[Any]
) -> list[tuple[Any, str]]:
    """A building's walls: the survey's outlines it has ("survey"), then, the larger first, each part of
    a volume none covers (MIN_PART and up; the eaves' strips around a surveyed house left out), squared
    ("squared") or, not nearly orthogonal, traced ("traced"). A part beside walls already drawn (a
    garage by its house) is squared with them, so it meets them along a straight wall at right
    angles. Counter-clockwise, none overlapping another."""
    from rasterio.features import rasterize
    from scipy import ndimage as ndi
    from shapely import unary_union
    from shapely.geometry import Polygon
    from shapely.geometry.polygon import orient

    shape = volumes[0].shape
    walls: list[tuple[Any, str]] = []
    under = np.zeros(shape, np.int32)  # the wall (1-based) each cell is under
    taken: Any = None

    def add(outline: Any, kind: str) -> None:
        nonlocal taken
        walls.append((orient(outline, sign=1.0), kind))
        taken = outline if taken is None else taken.union(outline)
        cells = rasterize([(outline, 1)], out_shape=shape, transform=transform, fill=0) > 0
        under[cells & (under == 0)] = len(walls)

    for p in sorted(survey, key=lambda q: -q.area):
        outline = _largest(Polygon(p.exterior).simplify(0.05).buffer(0), taken)
        if outline is not None and outline.area >= 1.0:
            add(outline, "survey")
    parts = []
    for v in volumes:
        # the eaves' strips a survey outline leaves around it go; so does a ragged edge, unless
        # that leaves nothing of a small shed
        free = v & (under == 0)
        for strips in (2,) if (v & (under > 0)).any() else (2, 1):
            rest = ndi.binary_opening(free, structure=np.ones((3, 3)), iterations=strips)
            labels, count = ndi.label(rest)
            found = [labels == k for k in range(1, count + 1)]
            found = [c for c in found if c.sum() * CELL * CELL >= MIN_PART]
            if found:
                break
        parts += found
    for cells in sorted(parts, key=lambda c: -int(c.sum())):
        cells = cells & (under == 0)
        if cells.sum() * CELL * CELL < MIN_PART:
            continue
        reach = round(SNAP / CELL)
        beside = np.unique(under[ndi.binary_dilation(cells, iterations=reach) & (under > 0)])
        outline = None
        if len(beside):
            near = [walls[b - 1][0] for b in beside]
            angle = _angles(max(near, key=lambda w: w.area))[0]
            squared, ok = _square(cells, transform, [angle], BESIDE_FIT)
            if ok:
                outline = _largest(_snap(squared, unary_union(near), angle), taken)
        if outline is None:
            squared, ok = _square(cells, transform)
            outline = _largest(squared, taken)
        # a sliver left between walls (narrower than 1.5 m) is no volume
        if outline is not None and outline.area >= MIN_PART and outline.buffer(-0.75).area > 0:
            add(outline, "squared" if ok else "traced")
    return walls


def _snap(outline: Any, anchor: Any, angle: float) -> Any:
    """`outline` (right-angled on the axes `angle`) grown across a gap of up to SNAP towards `anchor`
    on each side that faces it (the eaves of a house over its garage leave one between their roofs'
    cells), so the two meet along a wall."""
    from shapely import affinity
    from shapely.geometry import box

    origin = anchor.centroid
    r = affinity.rotate(outline, -angle, origin=origin)
    a = affinity.rotate(anchor, -angle, origin=origin)
    x0, z0, x1, z1 = r.bounds
    grown = r
    for side in range(4):
        strip = (
            box(x0 - SNAP, z0, x0, z1),
            box(x1, z0, x1 + SNAP, z1),
            box(x0, z0 - SNAP, x1, z0),
            box(x0, z1, x1, z1 + SNAP),
        )[side]
        hit = strip.intersection(a)
        if hit.area <= 1e-3:
            continue
        hx0, hz0, hx1, hz1 = hit.bounds
        fill = (
            box(hx1, z0, x0, z1),
            box(x1, z0, hx0, z1),
            box(x0, hz1, x1, z0),
            box(x0, z1, x1, hz0),
        )[side]
        grown = grown.union(fill)
    return affinity.rotate(grown, angle, origin=origin)


def _largest(g: Any, taken: Any = None) -> Any:
    """The largest polygon of `g` once `taken` is cut out of it; None when nothing is left, or when it
    would surround a part of `taken` (the walls are drawn from the outer ring only)."""
    from shapely.geometry import Polygon

    if taken is not None:
        g = g.difference(taken)
    polys = [p for p in getattr(g, "geoms", [g]) if p.geom_type == "Polygon" and not p.is_empty]
    if not polys:
        return None
    best = max(polys, key=lambda p: p.area)
    if any(Polygon(r).area > 0.5 for r in best.interiors):
        return None
    return Polygon(best.exterior)


def _square(
    cells: np.ndarray, transform: Any, angles: list[float] | None = None, fit: float = SQUARE_FIT
) -> tuple[Any, bool]:
    """The outline of `cells` with right angles: the rectangle on its own axes (or on `angles`,
    degrees), cut at up to two corners where that follows the cells better (an L, a T, a Z), and True
    when it covers them within `fit`. Otherwise the traced outline, simplified, and False."""
    from rasterio.features import rasterize
    from rasterio.features import shapes as vectorize
    from rasterio.transform import Affine
    from shapely import affinity
    from shapely.geometry import Polygon, box, shape

    traced = [
        shape(g) for g, v in vectorize(cells.astype(np.uint8), mask=cells, transform=transform)
    ]
    raw = Polygon(max(traced, key=lambda p: p.area).exterior)
    simple = raw.simplify(0.6, preserve_topology=True).buffer(0)
    if simple.geom_type != "Polygon" or simple.is_empty:
        simple = raw
    fit = min(fit, 1 - CELL * simple.length / (2 * simple.area))
    origin = raw.centroid
    best: tuple[float, Any] = (-1.0, simple)
    for angle in angles or _angles(simple):
        r = affinity.rotate(raw, -angle, origin=origin)
        x0, z0, x1, z1 = r.bounds
        q = SQUARE_CELL
        shape_ = (int(np.ceil((z1 - z0) / q)), int(np.ceil((x1 - x0) / q)))
        grid = rasterize(
            [(r, 1)], out_shape=shape_, transform=Affine(q, 0, x0, 0, q, z0), fill=0, dtype="uint8"
        )
        iou, rect, notches = _orthogonal(grid, round(NOTCH_SIDE / q))
        if iou <= best[0]:
            continue
        rect_, *cuts = (
            box(x0 + b[0] * q, z0 + b[1] * q, x0 + b[2] * q, z0 + b[3] * q)
            for b in (rect, *notches)
        )
        for notch in cuts:
            rect_ = rect_.difference(notch)
        best = (iou, affinity.rotate(rect_, angle, origin=origin))
    if best[0] >= fit:
        return best[1], True
    return simple, False


def _orthogonal(
    g: np.ndarray, side: int
) -> tuple[float, tuple[int, int, int, int], list[tuple[int, int, int, int]]]:
    """The rectangle, cut at up to two corners, that overlaps the cells of `g` best: its intersection
    over union, the rectangle and the notches cut from it, each (col0, row0, col1, row1). A notch is
    `side` cells wide each way and leaves `side` of the rectangle beside it."""
    rows, cols = g.shape
    s = np.zeros((rows + 1, cols + 1))
    s[1:, 1:] = g.cumsum(0).cumsum(1)
    total = s[-1, -1]

    def inside(c0: Any, r0: Any, c1: Any, r1: Any) -> Any:
        return s[r1, c1] - s[r0, c1] - s[r1, c0] + s[r0, c0]

    def iou(inn: Any, area: Any) -> Any:
        return inn / np.maximum(total + area - inn, 1e-9)

    # each side of the rectangle moved to where it overlaps best, the others held
    c0, r0, c1, r1 = 0, 0, cols, rows
    for _ in range(3):
        a = np.arange(0, c1)
        c0 = int(a[np.argmax(iou(inside(a, r0, c1, r1), (c1 - a) * (r1 - r0)))])
        a = np.arange(c0 + 1, cols + 1)
        c1 = int(a[np.argmax(iou(inside(c0, r0, a, r1), (a - c0) * (r1 - r0)))])
        a = np.arange(0, r1)
        r0 = int(a[np.argmax(iou(inside(c0, a, c1, r1), (c1 - c0) * (r1 - a)))])
        a = np.arange(r0 + 1, rows + 1)
        r1 = int(a[np.argmax(iou(inside(c0, r0, c1, a), (c1 - c0) * (a - r0)))])
    inn, area = float(inside(c0, r0, c1, r1)), (c1 - c0) * (r1 - r0)
    score = float(iou(inn, area))
    notches: list[tuple[int, int, int, int]] = []
    w, h = np.meshgrid(np.arange(side, c1 - c0 - side + 1), np.arange(side, r1 - r0 - side + 1))
    if not w.size:
        return score, (c0, r0, c1, r1), notches
    corners = [
        (c0 + 0 * w, r0 + 0 * h, c0 + w, r0 + h),
        (c1 - w, r0 + 0 * h, c1 + 0 * w, r0 + h),
        (c0 + 0 * w, r1 - h, c0 + w, r1 + 0 * h),
        (c1 - w, r1 - h, c1 + 0 * w, r1 + 0 * h),
    ]
    for _ in range(2):
        pick: tuple[float, tuple[int, int, int, int], float] | None = None
        for nc0, nr0, nc1, nr1 in corners:
            cut = inside(nc0, nr0, nc1, nr1)
            v = iou(inn - cut, area - w * h)
            for e0, f0, e1, f1 in notches:  # kept `side` apart from a notch already cut
                v = np.where(
                    (nc0 < e1 + side) & (e0 - side < nc1) & (nr0 < f1 + side) & (f0 - side < nr1),
                    -1.0,
                    v,
                )
            at = np.unravel_index(int(np.argmax(v)), v.shape)
            if pick is None or v[at] > pick[0]:
                b = (int(nc0[at]), int(nr0[at]), int(nc1[at]), int(nr1[at]))
                pick = (float(v[at]), b, float(cut[at]))
        if pick is None or pick[0] < score + NOTCH_GAIN:
            break
        score, b, cut_in = pick
        notches.append(b)
        inn -= cut_in
        area -= (b[2] - b[0]) * (b[3] - b[1])
    return score, (c0, r0, c1, r1), notches


def _angles(p: Any) -> list[float]:
    """An outline's axes, two guesses (degrees): its edges' mean direction (the long ones weigh most),
    and its smallest rectangle's."""
    ring = np.asarray(p.exterior.coords)
    d = np.diff(ring, axis=0)
    w = (d**2).sum(axis=1)
    phi = 4 * np.arctan2(d[:, 1], d[:, 0])
    mean = float(np.degrees(np.arctan2((w * np.sin(phi)).sum(), (w * np.cos(phi)).sum()) / 4))
    rect = np.asarray(p.minimum_rotated_rectangle.exterior.coords)
    e = rect[1] - rect[0]
    smallest = float(np.degrees(np.arctan2(e[1], e[0])))
    gap = (smallest - mean + 45) % 90 - 45
    return [mean] if abs(gap) < 0.5 else [mean, smallest]


def _split(
    outline: Any, x: np.ndarray, z: np.ndarray, level: np.ndarray, depth: int = 0
) -> list[tuple[Any, int]]:
    """An outline over several roof levels (each cell's `level`) cut straight across, along its own
    axes, until each piece is one level: [(piece, its level)]. Each cut leaves the fewest cells on the
    wrong side of it and MIN_PART on each; none is made that does not halve them."""
    from shapely import contains_xy
    from shapely.geometry import Polygon

    if not len(level):
        return [(outline, -1)]
    counts = np.bincount(level)
    major = int(np.argmax(counts))
    wrong = len(level) - int(counts[major])
    if depth >= 3 or wrong * CELL * CELL < MIN_PART:
        return [(outline, major)]
    a = np.radians(_angles(outline)[0])
    best: tuple[float, np.ndarray, float] | None = None
    for axis in (np.array([np.cos(a), np.sin(a)]), np.array([-np.sin(a), np.cos(a)])):
        t = np.c_[x, z] @ axis
        order = np.argsort(t)
        ts = t[order]
        left = np.cumsum(level[order][:, None] == np.arange(len(counts))[None, :], axis=0)
        i = np.arange(1, len(ts))
        on_left = left[i - 1]
        on_right = counts[None, :] - on_left
        cost = (i - on_left.max(1)) + (len(ts) - i - on_right.max(1))
        ok = (
            (np.minimum(i, len(ts) - i) * CELL * CELL >= MIN_PART)
            & (ts[i] - ts[i - 1] > 1e-6)
            & (on_left.argmax(1) != on_right.argmax(1))
        )
        if ok.any():
            j = int(np.argmin(np.where(ok, cost, np.inf)))
            if best is None or cost[j] < best[0]:
                best = (float(cost[j]), axis, float((ts[i[j] - 1] + ts[i[j]]) / 2))
    if best is None or best[0] > 0.5 * wrong:
        return [(outline, major)]
    _, axis, at = best
    across = np.array([-axis[1], axis[0]])
    big = 10 * (outline.length + 1)
    mid = axis * at
    out: list[tuple[Any, int]] = []
    for sign in (-1, 1):
        half = Polygon(
            [
                mid - across * big,
                mid + across * big,
                mid + (across + sign * axis) * big,
                mid + (-across + sign * axis) * big,
            ]
        )
        part = outline.intersection(half)
        for g in getattr(part, "geoms", [part]):
            if g.geom_type != "Polygon" or g.area < 1.0:
                continue
            sel = contains_xy(g, x, z)
            out += _split(Polygon(g.exterior), x[sel], z[sel], level[sel], depth + 1)
    return out


def _volumes(m: np.ndarray, h: np.ndarray) -> list[np.ndarray]:
    """A building's cells (`m`, roof heights `h`) split into its volumes: the regions of at least
    MIN_PART between the cells whose roof jumps more than STEP to a neighbour, each cell left over
    (by a jump, in a small piece) given to the neighbouring volume whose height is nearest its own.
    One mask, `m` itself, when the roof does not step."""
    from scipy import ndimage as ndi

    grid = np.where(m, h, np.nan)
    pad = np.pad(grid, 1, constant_values=np.nan)
    sides = [(1 + dj, 1 + di) for dj, di in ((0, 1), (0, -1), (1, 0), (-1, 0))]
    near = [pad[a : a + grid.shape[0], b : b + grid.shape[1]] for a, b in sides]
    jump = np.zeros(m.shape, bool)
    for nb in near:
        jump |= np.abs(np.nan_to_num(nb - grid, nan=0.0)) > STEP
    labels, count = ndi.label(m & ~jump)
    area = ndi.sum(np.ones(m.shape), labels, range(1, count + 1)) * CELL * CELL
    keep = [k + 1 for k in range(count) if area[k] >= MIN_PART]
    if len(keep) < 2:
        return [m]
    own = np.where(np.isin(labels, keep), labels, 0)
    for _ in range(m.shape[0] + m.shape[1]):
        todo = m & (own == 0)
        if not todo.any():
            break
        best = np.full(m.shape, np.inf)
        take = np.zeros_like(own)
        lp = np.pad(own, 1)
        for (a, b), nb in zip(sides, near, strict=True):
            nl = lp[a : a + m.shape[0], b : b + m.shape[1]]
            d = np.abs(nb - grid)
            ok = todo & (nl > 0) & (d < best)
            best[ok], take[ok] = d[ok], nl[ok]
        if not take.any():
            break
        own = np.where(take > 0, take, own)
    return [own == k for k in keep]


def roof_shape(
    x: np.ndarray, z: np.ndarray, h: np.ndarray, outline: Any
) -> tuple[Any, list[tuple[np.ndarray, np.ndarray]]]:
    """The roof that fits the lidar's heights best: flat, a single pitch, a gable or a hip along either
    axis of the outline's rectangle, or (none fits within ROOF_FIT) a plain one (_plain_roof). Returns
    the height at a point and the creases (ridges: segments the mesh must have edges along)."""
    at, creases, _ = _roof(x, z, h, outline)
    return at, creases


def _roof(
    x: np.ndarray, z: np.ndarray, h: np.ndarray, outline: Any
) -> tuple[Any, list[tuple[np.ndarray, np.ndarray]], bool]:
    """roof_shape's, and whether a simple shape fits."""
    rms, at, creases = _simple_roof(x, z, h, outline)
    if rms <= ROOF_FIT:
        return at, creases, True
    at, creases = _plain_roof(h, outline)
    return at, creases, False


def _plain_roof(h: np.ndarray, outline: Any) -> tuple[Any, list[tuple[np.ndarray, np.ndarray]]]:
    """A roof the simple shapes do not fit, drawn calm: flat (at the heights' median) where half of
    them lie within FLAT_IQR, else a gable along the outline's long axis from the eaves to the ridge the
    lidar measured (its low and high heights)."""
    q25, q75 = np.percentile(h, [25, 75])
    if q75 - q25 < FLAT_IQR:
        level = float(np.median(h))
        return (lambda px, pz: level), []
    eave, ridge = (float(v) for v in np.percentile(h, [5, 95]))
    rect = np.asarray(outline.minimum_rotated_rectangle.exterior.coords)[:4]
    e1, e2 = rect[1] - rect[0], rect[2] - rect[1]
    long_, short_ = (e1, e2) if np.hypot(*e1) >= np.hypot(*e2) else (e2, e1)
    u = long_ / max(float(np.hypot(*long_)), 1e-9)
    v = short_ / max(float(np.hypot(*short_)), 1e-9)
    half = max(float(np.hypot(*short_)) / 2, 1e-6)
    c = rect.mean(axis=0)

    def at(px: float, pz: float) -> float:
        off = abs(float((np.array([px, pz]) - c) @ v))
        return ridge - (ridge - eave) * min(1.0, off / half)

    big = float(np.hypot(*long_)) + 2 * half
    return at, [(c - u * big, c + u * big)]


def _simple_roof(
    x: np.ndarray, z: np.ndarray, h: np.ndarray, outline: Any
) -> tuple[float, Any, list[tuple[np.ndarray, np.ndarray]]]:
    """The simple roof shape (flat, a single pitch, a gable, a hip) that fits the heights best, with
    its RMS off them: (rms, the height at a point, the creases)."""
    rect = np.asarray(outline.minimum_rotated_rectangle.exterior.coords)[:4]
    e1, e2 = rect[1] - rect[0], rect[2] - rect[1]
    long_, short_ = (e1, e2) if np.hypot(*e1) >= np.hypot(*e2) else (e2, e1)
    L, W = float(np.hypot(*long_)), float(np.hypot(*short_))
    u, v = long_ / L, short_ / W
    c = rect.mean(axis=0)
    pts = np.c_[x, z] - c
    tu, tv = pts @ u, pts @ v

    def fit(feature: np.ndarray) -> tuple[float, np.ndarray]:
        a = np.c_[np.ones_like(feature), feature]
        coef, *_ = np.linalg.lstsq(a, h, rcond=None)
        return float(np.sqrt(np.mean((a @ coef - h) ** 2))), coef

    candidates: list[tuple[float, str, Any, np.ndarray]] = []
    candidates.append(
        (float(np.std(h)), "flat", None, np.array([float(np.percentile(h, 60)), 0.0]))
    )
    for name, t in (("pitch-u", tu), ("pitch-v", tv)):
        rms, coef = fit(t)
        candidates.append((rms, name, None, coef))
    for name, t, span in (("gable-u", tv, W), ("gable-v", tu, L)):
        for d0 in np.arange(-span / 4, span / 4 + 1e-6, 0.25):
            rms, coef = fit(-np.abs(t - d0))
            candidates.append((rms + 0.02, name, float(d0), coef))
    half = max(0.0, (L - W) / 2)
    rms, coef = fit(-np.maximum(np.abs(tv), np.abs(tu) - half))
    candidates.append((rms + 0.04, "hip", half, coef))
    rms, name, d0, coef = min(candidates, key=lambda k: k[0])
    spread = float(np.percentile(h, 95) - np.percentile(h, 5))
    if spread < 0.8:
        rms, name, d0, coef = candidates[0]
    creases: list[tuple[np.ndarray, np.ndarray]] = []
    r0, s0 = float(coef[0]), float(coef[1])
    dd = float(d0 or 0.0)

    def at(px: float, pz: float) -> float:
        q = np.array([px, pz]) - c
        a, b = float(q @ u), float(q @ v)
        if name == "flat":
            return r0
        if name == "pitch-u":
            return r0 + s0 * a
        if name == "pitch-v":
            return r0 + s0 * b
        if name == "gable-u":
            return r0 + s0 * -abs(b - dd)
        if name == "gable-v":
            return r0 + s0 * -abs(a - dd)
        return r0 + s0 * -max(abs(b), abs(a) - half)

    big = L + W
    if name == "gable-u":
        creases.append((c + v * dd - u * big, c + v * dd + u * big))
    elif name == "gable-v":
        creases.append((c + u * dd - v * big, c + u * dd + v * big))
    elif name == "hip":
        e = u * half
        creases.append((c - e, c + e))
        for sx in (-1, 1):
            for sy in (-1, 1):
                creases.append((c + sx * e, c + sx * e + (sx * u + sy * v) * big))
    return rms, at, creases


def _mesh(
    outline: Any, roof_at: Any, creases: list[tuple[np.ndarray, np.ndarray]], base: float
) -> tuple[list[np.ndarray], list[int], list[int]]:
    """A closed house: the outline's edges split every metre and where a crease crosses them, the roof
    triangulated over those points, a 1 m grid inside and points along the creases, the walls from
    `base` up to the roof along every edge piece."""
    from scipy.spatial import Delaunay
    from shapely import contains_xy
    from shapely.geometry import LineString

    ring = np.asarray(outline.exterior.coords)[:-1]
    edge_pts: list[np.ndarray] = []
    lines = [LineString([a, b]) for a, b in creases]
    for e in range(len(ring)):
        a, b = ring[e], ring[(e + 1) % len(ring)]
        length = float(np.hypot(*(b - a)))
        ts = set(np.linspace(0, 1, max(2, int(np.ceil(length / 1.0)) + 1))[:-1].tolist())
        seg = LineString([a, b])
        for ln in lines:
            hit = seg.intersection(ln)
            if hit.geom_type == "Point":
                ts.add(float(np.hypot(hit.x - a[0], hit.y - a[1]) / max(length, 1e-9)))
        edge_pts += [a + (b - a) * t for t in sorted(ts) if t < 1 - 1e-6]
    outer = np.asarray(edge_pts)
    inner = outline.buffer(-0.3)
    x0, z0, x1, z1 = outline.bounds
    gx, gz = np.meshgrid(np.arange(x0 + 0.5, x1, 1.0), np.arange(z0 + 0.5, z1, 1.0))
    grid = np.c_[gx.ravel(), gz.ravel()]
    crease_pts = []
    for ln in lines:
        part = ln.intersection(inner) if not inner.is_empty else ln.intersection(outline)
        for g in getattr(part, "geoms", [part]):
            if g.is_empty or g.geom_type != "LineString":
                continue
            n = max(2, int(np.ceil(g.length / 0.5)) + 1)
            crease_pts += [
                np.asarray(g.interpolate(t, normalized=True).coords[0])
                for t in np.linspace(0, 1, n)
            ]
    if not inner.is_empty:
        grid = grid[contains_xy(inner, grid[:, 0], grid[:, 1])]
        # grid points next to a crease would make slivers across it
        if crease_pts:
            cp = np.asarray(crease_pts)
            d = np.min(
                np.hypot(grid[:, None, 0] - cp[None, :, 0], grid[:, None, 1] - cp[None, :, 1]),
                axis=1,
            )
            grid = grid[d > 0.35]
    else:
        grid = np.zeros((0, 2))
    pts2 = np.r_[outer, grid, np.asarray(crease_pts).reshape(-1, 2)]
    tri = Delaunay(pts2).simplices
    cen = pts2[tri].mean(axis=1)
    tri = tri[contains_xy(outline, cen[:, 0], cen[:, 1])]
    heights = np.array([roof_at(x, z) for x, z in pts2])
    positions = [np.array([x, hh, z]) for (x, z), hh in zip(pts2, heights, strict=True)]
    roof_tris: list[int] = []
    for a, b, cc in tri:
        nrm = np.cross(positions[b] - positions[a], positions[cc] - positions[a])
        roof_tris += [int(a), int(b), int(cc)] if nrm[1] > 0 else [int(a), int(cc), int(b)]
    wall_tris: list[int] = []
    no = len(outer)
    for e in range(no):
        a2, b2 = outer[e], outer[(e + 1) % no]
        v0 = len(positions)
        positions += [
            np.array([a2[0], base, a2[1]]),
            np.array([b2[0], base, b2[1]]),
            np.array([b2[0], heights[(e + 1) % no], b2[1]]),
            np.array([a2[0], heights[e], a2[1]]),
        ]
        # outward: to the right of a → b on a counter-clockwise outline (x east, z south seen as x, y)
        out_dir = np.array([b2[1] - a2[1], 0.0, -(b2[0] - a2[0])])
        nrm = np.cross(positions[v0 + 1] - positions[v0], positions[v0 + 2] - positions[v0])
        quad = [v0, v0 + 1, v0 + 2, v0, v0 + 2, v0 + 3]
        wall_tris += quad if float(nrm @ out_dir) > 0 else [v0, v0 + 2, v0 + 1, v0, v0 + 3, v0 + 2]
    return positions, roof_tris, wall_tris


@dataclass
class Tree:
    x: float  # local, metres east
    z: float  # local, metres south
    ground: float  # altitude of its foot
    height: float  # metres above it
    radius: float  # the crown's, metres
    kind: str  # "broadleaf", "pine" (a conifer), "columnar", "bush" (under 3 m)


MIN_TREE = 1.2  # m: lower is grass, a low wall's cap, noise


def trees(c: Cloud, ground: np.ndarray, radius: int, step: float = 1.0) -> list[Tree]:
    """The trees and bushes of the lidar's vegetation: the canopy height over the ground (0.5 m cells),
    its tops (local maxima, kept apart by a distance that grows with the height), each top's crown
    (the vegetation cells nearest to it), its kind from the crown's shape."""
    from scipy import ndimage as ndi
    from scipy.spatial import cKDTree

    n, _ = _grid(radius)
    sel = c.cls == VEGETATION
    i = np.clip(((c.x[sel] + radius) / CELL).astype(int), 0, n - 1)
    j = np.clip(((c.z[sel] + radius) / CELL).astype(int), 0, n - 1)
    top = np.full((n, n), -np.inf)
    np.maximum.at(top, (j, i), c.y[sel])
    # the ground under each cell's centre (the terrain grid: `step` metres, the square ± radius)
    centres = (np.arange(n) + 0.5) * CELL / step
    gj, gi = np.meshgrid(centres, centres, indexing="ij")
    under = ndi.map_coordinates(ground, [gj, gi], order=1, mode="nearest")
    chm = np.where(np.isfinite(top), top - under, 0.0)
    chm[chm < MIN_TREE] = 0.0
    smooth = ndi.gaussian_filter(chm, 1.0)
    peaks = (smooth == ndi.maximum_filter(smooth, size=5)) & (chm >= MIN_TREE)
    pj, pi = np.nonzero(peaks)
    if not len(pj):
        return []
    # each top's height: the highest cell within a metre of it
    high = ndi.maximum_filter(chm, size=5)[pj, pi]
    order = np.argsort(-high)
    pj, pi, high = pj[order], pi[order], high[order]
    xy = np.c_[(pi + 0.5) * CELL - radius, (pj + 0.5) * CELL - radius]
    # a lower top too close to a higher one is the same crown
    kd = cKDTree(xy)
    kept = np.ones(len(xy), dtype=bool)
    for k in range(len(xy)):
        if not kept[k]:
            continue
        for o in kd.query_ball_point(xy[k], 1.0 + 0.15 * high[k]):
            if o > k:
                kept[o] = False
    xy, high = xy[kept], high[kept]
    # the crowns: every vegetation cell to its nearest top
    vj, vi = np.nonzero(chm >= MIN_TREE)
    cells = np.c_[(vi + 0.5) * CELL - radius, (vj + 0.5) * CELL - radius]
    dist, owner = cKDTree(xy).query(cells)
    near = dist < 0.6 * np.maximum(high[owner], 2.0)
    area = np.bincount(owner[near], minlength=len(xy)) * CELL * CELL
    size = ground.shape[0]
    out: list[Tree] = []
    for (x, z), h, a in zip(xy, high, area, strict=True):
        r = float(np.clip(np.sqrt(a / np.pi), 0.6, max(1.0, 0.5 * h)))
        if h < 3.0:
            kind = "bush"
        elif h > 8 and r / h < 0.13:
            kind = "columnar"
        elif h > 8 and r / h < 0.2:
            kind = "pine"
        else:
            kind = "broadleaf"
        g = float(
            ground[
                int(np.clip(round((z + radius) / step), 0, size - 1)),
                int(np.clip(round((x + radius) / step), 0, size - 1)),
            ]
        )
        out.append(
            Tree(
                x=round(float(x), 2),
                z=round(float(z), 2),
                ground=round(g, 2),
                height=round(float(h), 2),
                radius=round(r, 2),
                kind=kind,
            )
        )
    return out
