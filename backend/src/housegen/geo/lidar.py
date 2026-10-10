"""The lidar around a place (swissSURFACE3D, the classified point cloud): what swissBUILDINGS3D misses.

swissBUILDINGS3D is a few years behind: a neighbourhood built since its edition is missing (#66),
while the newest lidar flight (2025 around Lausanne) has it. Its points are read by area from the
cloud-optimised files (COPC) over HTTP, at 0.5 m, in the same local frame as the rest (x east,
z south, y the altitude, LN02 like swissALTI3D: no offset).

Classes (ASPRS, as swisstopo uses them): 2 ground, 3 vegetation, 6 building.
"""

from __future__ import annotations

import asyncio
import logging
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


def new_buildings(
    c: Cloud,
    known: list[swiss.Building],
    ground: np.ndarray,
    radius: int,
    step: float = 1.0,
) -> list[swiss.Building]:
    """The buildings of the lidar that `known` lacks: an outline (a rectangle when it nearly is one)
    with walls down to the ground and a roof following the lidar's roof heights (smoothed), so a
    gable, a hip, a flat roof or an L-shaped house each come out as they are."""
    from rasterio.features import shapes as vectorize
    from scipy import ndimage as ndi
    from shapely.geometry import Polygon, shape
    from shapely.geometry.polygon import orient

    n, transform = _grid(radius)
    sel = c.cls == BUILDING
    i = np.clip(((c.x[sel] + radius) / CELL).astype(int), 0, n - 1)
    j = np.clip(((c.z[sel] + radius) / CELL).astype(int), 0, n - 1)
    top = np.full((n, n), np.nan)
    np.fmax.at(top, (j, i), c.y[sel])
    mask = ~np.isnan(top)
    mask = ndi.binary_closing(mask, iterations=2)
    # what swissBUILDINGS3D already has (and a 1.5 m margin: its outlines and the lidar's differ a little)
    mask &= ~ndi.binary_dilation(footprints(known, radius), iterations=3)
    labels, _ = ndi.label(mask)
    size = ground.shape[0]

    def ground_at(x: float, z: float) -> float:
        gi = int(np.clip(round((x + radius) / step), 0, size - 1))
        gj = int(np.clip(round((z + radius) / step), 0, size - 1))
        return float(ground[gj, gi])

    out: list[swiss.Building] = []
    for k, sl in enumerate(ndi.find_objects(labels), start=1):
        if sl is None:
            continue
        m = labels[sl] == k
        if m.sum() * CELL * CELL < MIN_AREA:
            continue
        roof = np.where(m, top[sl], np.nan)
        # the cells inside the outline with no point (closing, a skylight): the nearest roof height
        missing = np.isnan(roof)
        if missing.all():
            continue
        idx = ndi.distance_transform_edt(missing, return_distances=False, return_indices=True)
        filled = roof[tuple(idx)]
        smooth = ndi.median_filter(filled, size=3)
        # the outline, from the cells (rasterio's polygons follow the cell edges), simplified
        polys = [
            shape(g)
            for g, v in vectorize(
                m.astype(np.uint8),
                mask=m,
                transform=transform @ transform.translation(sl[1].start, sl[0].start),
            )
            if v == 1
        ]
        if not polys:
            continue
        outline = max(polys, key=lambda p: p.area)
        outline = Polygon(outline.exterior).simplify(0.6, preserve_topology=True).buffer(0)
        if outline.is_empty or outline.geom_type != "Polygon" or outline.area < MIN_AREA:
            continue
        rect = outline.minimum_rotated_rectangle
        if outline.area / max(rect.area, 1e-6) >= 0.85:
            outline = rect
        outline = orient(outline, sign=1.0)
        ring = np.asarray(outline.exterior.coords)[:-1]
        base = min(ground_at(x, z) for x, z in ring) - 0.3
        if np.nanpercentile(roof, 95) - base < MIN_HEIGHT:
            continue
        # the roof heights of the building's own cells, at their centres
        cj, ci = np.nonzero(m & ~missing)
        hx = -radius + (ci + sl[1].start + 0.5) * CELL
        hz = -radius + (cj + sl[0].start + 0.5) * CELL
        roof_at, creases = roof_shape(hx, hz, smooth[cj, ci], outline)
        positions, roof_tris, wall_tris = _mesh(outline, roof_at, creases, base)
        cx, cz = outline.centroid.x, outline.centroid.y
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
    return out


def roof_shape(
    x: np.ndarray, z: np.ndarray, h: np.ndarray, outline: Any
) -> tuple[Any, list[tuple[np.ndarray, np.ndarray]]]:
    """The roof that fits the lidar's heights best: flat, a single pitch, a gable or a hip along either
    axis of the outline's rectangle, or (none fits within 0.6 m) the heights themselves, smoothed.
    Returns the height at a point and the creases (ridges: segments the mesh must have edges along)."""
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
    if rms > 0.6:
        return _faceted(x, z, h), creases
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
    return at, creases


def _faceted(x: np.ndarray, z: np.ndarray, h: np.ndarray) -> Any:
    """A roof none of the simple shapes fits (an L-shaped house, two wings): its cells grouped into
    faces by the way they slope (flat, or one of 8 directions) and fitted with a plane each; a point
    takes the plane of its nearest cell, so the roof is made of planes, not of the lidar's bumps."""
    from scipy import ndimage as ndi

    i = np.round((x - x.min()) / CELL).astype(int)
    j = np.round((z - z.min()) / CELL).astype(int)
    grid = np.full((j.max() + 1, i.max() + 1), np.nan)
    grid[j, i] = h
    known = ~np.isnan(grid)
    idx = ndi.distance_transform_edt(~known, return_distances=False, return_indices=True)
    full = ndi.gaussian_filter(grid[tuple(idx)], 1.0)
    dz, dx = np.gradient(full, CELL)
    slope = np.hypot(dx, dz)
    kind = np.where(slope < 0.12, 8, np.round(np.arctan2(dz, dx) / (np.pi / 4)).astype(int) % 8)
    planes = np.full((*grid.shape, 3), np.nan)
    assigned = np.zeros(grid.shape, dtype=bool)
    for k in range(9):
        labels, count = ndi.label((kind == k) & known)
        for seg in range(1, count + 1):
            cells = labels == seg
            if cells.sum() < 16:  # under 4 m²: taken by its neighbours
                continue
            cj, ci = np.nonzero(cells)
            a = np.c_[np.ones(len(ci)), ci * CELL, cj * CELL]
            coef, *_ = np.linalg.lstsq(a, grid[cj, ci], rcond=None)
            planes[cells] = coef
            assigned |= cells
    if not assigned.any():
        planes[known] = np.c_[grid[known], np.zeros((known.sum(), 2))]
        assigned = known
    near = ndi.distance_transform_edt(~assigned, return_distances=False, return_indices=True)
    planes = planes[tuple(near)]
    x0, z0 = float(x.min()), float(z.min())

    def at(px: float, pz: float) -> float:
        ci = int(np.clip(round((px - x0) / CELL), 0, grid.shape[1] - 1))
        cj = int(np.clip(round((pz - z0) / CELL), 0, grid.shape[0] - 1))
        a0, b0, c0 = planes[cj, ci]
        return float(a0 + b0 * (px - x0) + c0 * (pz - z0))

    return at


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
