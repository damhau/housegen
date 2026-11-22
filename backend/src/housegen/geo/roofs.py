"""Each neighbour's roof (#143), decided once so the viewer (kit/context.js) and the listing exteriors
(photo/blender/outside.py) draw the same one: `roof_spec` on each building of buildings.json (`roof`
there is the roof's triangles).

  cover        its pitched faces' (more than 5° off level): clay-interlocking, clay-beaver,
               fibre-cement, concrete-tile, metal-seam
  flat         its level faces': gravel, green, membrane
  colour       the cover's tint, sRGB "#rrggbb": a real roof colour of `family` (the kit's ROOF_TINTS),
               shaded per house
  family       terracotta, brown, grey, anthracite: the aerial photo's median over the roof, or, for a
               roof the photo does not show (a house newer than it), a photographed neighbour's
  year         its construction year (the federal building register, GWR), None when unknown
  pv_kw        the power of the photovoltaic plants on it (the BFE's register), None when none
  panels       every solar module as its 4 corners in the frame of `positions`, lifted off the roof:
               [front-low-left, front-low-right, high-right, high-left] seen from the front, so
               (c1 - c0) x (c3 - c0) points out of the module's face
  why          what decided the colour ("photo" | "neighbours"), the cover ("year" | "colour" | "hash")
               and the panels ("bfe" | "photo" | None)
  egid         optional: the register's building id
  flat_colour  optional: the photo's median over the level faces (sRGB), where it shows them

The registers come from geo.admin's identify, no key needed.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from typing import Any

import httpx
import numpy as np

logger = logging.getLogger(__name__)

IDENTIFY = "https://api3.geo.admin.ch/rest/services/api/MapServer/identify"
GWR = "ch.bfs.gebaeude_wohnungs_register"
PLANTS = "ch.bfe.elektrizitaetsproduktionsanlagen"
PAGE = 200  # identify's most results an answer; `offset` pages past it
MAX_PAGES = 25
BEYOND = (
    30  # metres past the surroundings' square asked about: a house on its edge keeps its record
)
GWR_STANDING = {1003, 1004}  # gstat: under construction, existing

FLAT_ROOF = math.cos(math.radians(5))  # the kit's: a face more level than 5° is flat
SAMPLE_PIXELS = 1800  # the kit samples the photo at this size (context.js newerRoofs)
# the region's roofs (sRGB), kit/neighbours.js ROOF_TINTS
ROOF_TINTS: dict[str, list[str]] = {
    "terracotta": ["#86523f", "#8f5843", "#7a4b3b"],
    "brown": ["#6b3d2e", "#5a3a2e", "#7a4632"],
    "grey": ["#666560", "#6e6d69", "#5a5d61"],
    "anthracite": ["#4a4c4f", "#525458", "#44464a"],
}
FAMILIES = list(ROOF_TINTS)
CLAY = {"terracotta", "brown"}

# solar modules
MODULE_W, MODULE_H = 1.0, 1.7  # metres: across, up the slope (portrait on a pitched face)
MODULE_AREA = MODULE_W * MODULE_H
M2_PER_KW = 5.5
GAP = 0.02  # metres between modules
SHARE = 0.7  # of a face, at most
EDGE = 0.4  # metres kept clear of a pitched face's edges (eaves, verges, ridge, hips)
FLAT_EDGE = 0.6  # and of a flat roof's (its parapet)
LIFT = 0.12  # metres off the roof
FLAT_TILT = 12.0  # degrees: the rows on a flat roof, facing south
WINTER_SUN = (
    20.0  # degrees: the noon sun at the winter solstice (46.5° N); rows clear of each other's shade
)
PANEL_MAX_TILT = 60.0  # degrees: no module on a steeper face
SOUTHISH = -0.17  # a face facing within 100° of south takes modules
NEAR_RECORD = 3.0  # metres: a register's point outside every footprint goes to one this close
TOUCHING = (
    0.5  # metres: a building this close to another is part of it (an annex, a block cut in volumes)
)
NEAR_PLANT = (
    10.0  # metres: a plant the register's buildings do not place goes to a house this close
)


@dataclass
class Record:
    egid: str
    x: float
    z: float
    year: int | None
    category: int | None  # gkat: 1020 one use, residential; 1060 not residential…
    storeys: int | None
    area: float | None  # m², the footprint the register knows


@dataclass
class Plant:
    egid: str | None
    x: float
    z: float
    kw: float


# GBAUP: the construction period when the register has no year, its middle year
PERIODS = {
    8011: 1900,
    8012: 1932,
    8013: 1953,
    8014: 1965,
    8015: 1975,
    8016: 1983,
    8017: 1988,
    8018: 1993,
    8019: 1998,
    8020: 2003,
    8021: 2008,
    8022: 2013,
    8023: 2018,
    8024: 2023,
}


async def _identify(
    layer: str, e0: float, n0: float, ext: float, client: httpx.AsyncClient, geometry: bool
) -> list[dict[str, Any]]:
    box = f"{e0 - ext},{n0 - ext},{e0 + ext},{n0 + ext}"
    out: list[dict[str, Any]] = []
    for page in range(MAX_PAGES):
        r = await client.get(
            IDENTIFY,
            params={
                "geometryType": "esriGeometryEnvelope",
                "geometry": box,
                "imageDisplay": "1000,1000,96",
                "mapExtent": box,
                "tolerance": 0,
                "layers": f"all:{layer}",
                "sr": 2056,
                "returnGeometry": str(geometry).lower(),
                "geometryFormat": "geojson",
                "limit": PAGE,
                "offset": page * PAGE,
            },
            timeout=60,
        )
        r.raise_for_status()
        found = r.json().get("results")
        if not isinstance(found, list):
            raise RuntimeError(f"identify answered {r.text[:200]}")
        out += found
        if len(found) < PAGE:
            break
    return out


def _attrs(res: dict[str, Any]) -> dict[str, Any]:
    return res.get("properties") or res.get("attributes") or {}


def _int(v: Any) -> int | None:
    try:
        return int(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def construction_year(a: dict[str, Any]) -> int | None:
    """The register's year, else the middle of its construction period."""
    year = _int(a.get("gbauj"))
    if year is not None and 1000 < year < 2100:
        return year
    return PERIODS.get(_int(a.get("gbaup")) or 0)


async def records(e0: float, n0: float, radius: float, client: httpx.AsyncClient) -> list[Record]:
    """The register's standing buildings around the anchor, one per building (it lists each entrance)."""
    out: dict[str, Record] = {}
    for res in await _identify(GWR, e0, n0, radius + BEYOND, client, geometry=False):
        a = _attrs(res)
        egid = str(a.get("egid") or "")
        if not egid or egid in out or _int(a.get("gstat")) not in GWR_STANDING:
            continue
        e, n = a.get("gkode"), a.get("gkodn")
        if e is None or n is None:
            continue
        area = a.get("garea")
        out[egid] = Record(
            egid=egid,
            x=round(float(e) - e0, 2),
            z=round(n0 - float(n), 2),
            year=construction_year(a),
            category=_int(a.get("gkat")),
            storeys=_int(a.get("gastw")),
            area=float(area) if isinstance(area, (int, float)) else None,
        )
    return list(out.values())


_POWER = re.compile(r"([\d'\u2019.,]+)\s*(kW|MW|W)\b", re.IGNORECASE)


def power_kw(text: Any) -> float | None:
    """'13.05 kW' → 13.05 (the register's power is text; "1'250.5 kW", "13,05 kW" read too)."""
    m = _POWER.search(str(text or ""))
    if not m:
        return None
    digits = re.sub("['\u2019]", "", m.group(1))
    digits = digits.replace(",", "") if "." in digits else digits.replace(",", ".")
    try:
        v = float(digits)
    except ValueError:
        return None
    return v * {"kw": 1.0, "mw": 1000.0, "w": 0.001}[m.group(2).lower()]


async def plants(e0: float, n0: float, radius: float, client: httpx.AsyncClient) -> list[Plant]:
    """The photovoltaic plants around the anchor (the BFE's register of power plants)."""
    out = []
    for res in await _identify(PLANTS, e0, n0, radius + BEYOND, client, geometry=True):
        a = _attrs(res)
        if a.get("sub_category_en") != "Photovoltaic":
            continue
        kw = power_kw(a.get("total_power")) or power_kw(a.get("initial_power"))
        coords = (res.get("geometry") or {}).get("coordinates")
        if not kw or not coords:
            continue
        out.append(
            Plant(
                egid=str(a["egid"]) if a.get("egid") else None,
                x=round(float(coords[0]) - e0, 2),
                z=round(n0 - float(coords[1]), 2),
                kw=kw,
            )
        )
    return out


def house_hash(id_: Any) -> int:
    """kit/neighbours.js houseHash: the seed of a house's choices."""
    h = 7
    for ch in str(id_):
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return h


def _hue(r: float, g: float, b: float) -> float:
    mx, mn = max(r, g, b), min(r, g, b)
    if mx == mn:
        return 0.0
    d = mx - mn
    if mx == r:
        h = ((g - b) / d) % 6
    elif mx == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return (h * 60 + 360) % 360


def roof_family(rgb: Any) -> str:
    """kit/neighbours.js roofFamily: which real roof colour a photographed roof's median (sRGB 0 to 1)
    is. The photo's haze greys them all: a little warmth is clay, none fibre cement or concrete."""
    r, g, b = (float(v) for v in rgb)
    mx, mn = max(r, g, b), min(r, g, b)
    light = (mx + mn) / 2
    sat = (mx - mn) / (1 - abs(2 * light - 1)) if mx > mn else 0.0
    hue = _hue(r, g, b)
    if (hue <= 40 or hue >= 340) and sat >= 0.06:
        return "terracotta" if light >= 0.47 else "brown"
    return "grey" if light >= 0.45 else "anthracite"


def _percentile(values: np.ndarray, q: float) -> float:
    s = np.sort(values)
    return float(s[min(len(s) - 1, max(0, math.floor(q * (len(s) - 1) + 0.5)))])


def roof_look(pixels: np.ndarray) -> str:
    """kit/neighbours.js roofLook: "photo" when the photo shows a finished roof there (most pixels grey,
    tile-red or panel-blue, evenly lit), else "tiles" (a building site, a field)."""
    if len(pixels) < 20:
        return "tiles"
    r, g, b = pixels[:, 0], pixels[:, 1], pixels[:, 2]
    mx, mn = pixels.max(axis=1), pixels.min(axis=1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-9), 0)
    hue = _hues(r, g, b, mx, mn)
    grey = (sat < 0.12) & (mx > 0.1) & (mx < 0.85)
    tile = ((hue <= 30) | (hue >= 340)) & (sat > 0.1) & (mx > 0.12) & (mx < 0.7)
    panel = (hue >= 190) & (hue <= 260) & (sat > 0.15)
    like = float((grey | tile | panel).mean())
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    spread = _percentile(lum, 0.8) - _percentile(lum, 0.2)
    return "photo" if like >= 0.62 and spread <= 0.25 else "tiles"


def _hues(
    r: np.ndarray, g: np.ndarray, b: np.ndarray, mx: np.ndarray, mn: np.ndarray
) -> np.ndarray:
    d = np.where(mx > mn, mx - mn, 1.0)
    h = np.where(mx == r, ((g - b) / d) % 6, np.where(mx == g, (b - r) / d + 2, (r - g) / d + 4))
    return np.where(mx > mn, (h * 60 + 360) % 360, 0.0)


def _rgb(hex_: str) -> tuple[float, float, float]:
    return tuple(int(hex_[1 + 2 * k : 3 + 2 * k], 16) / 255 for k in range(3))  # type: ignore[return-value]


def _hex(rgb: Any) -> str:
    """sRGB 0 to 1 as "#rrggbb", halves rounded up as JavaScript rounds them."""
    return "#" + "".join(f"{math.floor(min(1.0, max(0.0, float(v))) * 255 + 0.5):02x}" for v in rgb)


def roof_tint(family: str, hash_: int) -> str:
    """kit/neighbours.js roofTint: a real roof colour of the family, picked and shaded by the hash."""
    tints = ROOF_TINTS[family]
    shade = 0.94 + ((hash_ >> 4) % 13) / 100
    return _hex(v * shade for v in _rgb(tints[(hash_ >> 13) % len(tints)]))


@dataclass
class Face:
    normal: np.ndarray  # unit, upwards
    centre: np.ndarray  # on the face's plane
    area: float  # m², along the slope
    tris: np.ndarray  # (k, 3, 3) its triangles' corners

    @property
    def flat(self) -> bool:
        return float(self.normal[1]) > FLAT_ROOF

    @property
    def tilt(self) -> float:
        return math.degrees(math.acos(min(1.0, float(self.normal[1]))))

    @property
    def south(self) -> float:
        """How much it faces south: 1 south, 0 east or west, -1 north (z is south)."""
        h = math.hypot(float(self.normal[0]), float(self.normal[2]))
        return float(self.normal[2]) / h if h > 1e-6 else 0.0


SAME_PLANE_DEG = 4.0
SAME_PLANE_M = 0.5  # the survey models one roof plane as pieces up to this far apart


def _shape(tris: np.ndarray, gap: float = 0.02) -> Any:
    """Triangles (k, 3, 2) as one shape, closed over seams up to `gap` wide."""
    import shapely

    polys = shapely.polygons(np.asarray(tris, dtype=np.float64))
    polys = polys[shapely.area(polys) > 1e-4]
    if not len(polys):
        return shapely.Polygon()
    shape = shapely.union_all(shapely.make_valid(polys))
    return shape.buffer(gap, join_style="mitre").buffer(-gap, join_style="mitre")


def faces(positions: list[float], roof: list[int]) -> list[Face]:
    """A roof's triangles grouped by plane and by the pieces each plane makes (two houses in one entry,
    two dormers in one plane), largest first."""
    from shapely import contains_xy

    out = []
    for f in _planes(positions, roof):
        plan = _shape(f.tris[:, :, [0, 2]]).buffer(0.05, join_style="mitre")
        parts = getattr(plan, "geoms", None)
        if not parts:
            out.append(f)
            continue
        mid = f.tris.mean(axis=1)
        for part in parts:
            inside = contains_xy(part, mid[:, 0], mid[:, 2])
            if inside.any():
                out.append(_face(f.tris[inside]))
    return sorted(out, key=lambda f: -f.area)


def _face(tris: np.ndarray) -> Face:

    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    n = np.where(n[:, 1:2] < 0, -n, n)
    w = np.linalg.norm(n, axis=1) / 2
    nrm = n.sum(axis=0)
    nrm /= np.linalg.norm(nrm)
    centre = (tris.mean(axis=1) * w[:, None]).sum(axis=0) / w.sum()
    # the area its triangles cover seen from above, along the slope: overlapping ones count once
    plan = _shape(tris[:, :, [0, 2]])
    return Face(normal=nrm, centre=centre, area=float(plan.area) / float(nrm[1]), tris=tris)


def _planes(positions: list[float], roof: list[int]) -> list[Face]:
    p = np.asarray(positions, dtype=np.float64).reshape(-1, 3)
    t = np.asarray(roof, dtype=np.int64).reshape(-1, 3)
    if not len(t):
        return []
    tri = p[t]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    area = np.linalg.norm(n, axis=1) / 2
    keep = area > 0.01
    # the survey draws each roof triangle twice
    key = np.sort(np.round(tri[keep] * 50), axis=1).reshape(-1, 9)
    _, once = np.unique(key, axis=0, return_index=True)
    once = np.flatnonzero(keep)[np.sort(once)]
    tri, n, area = tri[once], n[once], area[once]
    n = n / (2 * area[:, None])
    n = np.where(n[:, 1:2] < 0, -n, n)
    cos_same = math.cos(math.radians(SAME_PLANE_DEG))
    groups: list[list[int]] = []
    normals: list[np.ndarray] = []
    offsets: list[float] = []
    for i in np.argsort(-area):
        d = float(n[i] @ tri[i, 0])
        for k, (gn, gd) in enumerate(zip(normals, offsets, strict=True)):
            if float(gn @ n[i]) > cos_same and abs(gd - d) < SAME_PLANE_M:
                groups[k].append(int(i))
                break
        else:
            groups.append([int(i)])
            normals.append(n[i])
            offsets.append(d)
    return [_face(tri[g]) for g in groups]


def _frame(face: Face) -> tuple[np.ndarray, np.ndarray]:
    """A pitched face's right (level, along its eaves seen from the front) and up its slope."""
    n = face.normal
    h = np.array([n[0], 0.0, n[2]])
    right = np.array([-h[2], 0.0, h[0]]) / max(np.linalg.norm(h), 1e-9)
    up = np.cross(n, right)
    if up[1] < 0:
        right, up = -right, -up
    return right, up


def _outline(face: Face, right: np.ndarray, up: np.ndarray) -> Any:
    """The face in its own plane's coordinates (along `right`, `up` from its centre), as one shape."""

    rel = face.tris - face.centre
    uv = np.stack([rel @ right, rel @ up], axis=-1)
    return _shape(uv)


def module_count(kw: float) -> int:
    return max(1, math.floor(kw * M2_PER_KW / MODULE_AREA + 0.5))


@dataclass
class Rows:
    start: float  # the first row's low edge
    pitch: float  # from one row's low edge to the next's
    cells: list[list[float]]  # each row's cells, their left edges (one run)

    def low(self, k: int) -> float:
        return self.start + k * self.pitch


def _rows(inner: Any, w: float, depth: float, pitch: float) -> Rows:
    """Cells of w by depth inside `inner`, in rows `pitch` apart from its low side up, each row its
    longest run. Several phases are tried; the one holding the most cells wins."""
    import shapely
    from shapely.geometry import box

    if inner.is_empty:
        return Rows(0.0, pitch, [])
    shapely.prepare(inner)
    x0, y0, x1, y1 = inner.bounds
    step = w + GAP
    best = Rows(y0, pitch, [])
    for k in range(5):
        start = y0 + k * pitch / 5
        rows: list[list[float]] = []
        bottom = start
        while bottom + depth <= y1 + 1e-9:
            row: list[float] = []
            for j in range(5):
                lefts = np.arange(x0 + j * step / 5, x1 - w + 1e-9, step)
                fits = [
                    float(a) for a in lefts if inner.contains(box(a, bottom, a + w, bottom + depth))
                ]
                run = _longest_run(fits, step)
                if len(run) > len(row):
                    row = run
            rows.append(row)
            bottom += pitch
        if sum(map(len, rows)) > sum(map(len, best.cells)):
            best = Rows(start, pitch, rows)
    return best


def _longest_run(lefts: list[float], step: float) -> list[float]:
    best: list[float] = []
    run: list[float] = []
    for a in lefts:
        if run and a - run[-1] > step * 1.01:
            run = []
        run.append(a)
        if len(run) > len(best):
            best = list(run)
    return best


def _pick(rows: Rows, count: int) -> list[tuple[int, list[float]]]:
    """`count` cells as one even block: the fewest of the middle rows that hold them, each row about as
    long as the others, centred on its run."""
    used = [k for k, c in enumerate(rows.cells) if c]
    if not used or count <= 0:
        return []
    count = min(count, sum(len(rows.cells[k]) for k in used))
    chosen = used
    for m in range(1, len(used) + 1):
        start = (len(used) - m) // 2
        chosen = used[start : start + m]
        if sum(len(rows.cells[k]) for k in chosen) >= count:
            break
    want: dict[int, int] = {}
    left = count
    # the shorter rows first: what they cannot hold goes to the longer ones
    by_room = sorted(chosen, key=lambda k: len(rows.cells[k]))
    for i, k in enumerate(by_room):
        want[k] = min(len(rows.cells[k]), math.ceil(left / (len(by_room) - i)))
        left -= want[k]
    out = []
    for k in chosen:
        run = rows.cells[k]
        skip = (len(run) - want[k]) // 2
        out.append((k, run[skip : skip + want[k]]))
    return out


def pitched_modules(face: Face, count: int) -> list[list[list[float]]]:
    """Up to `count` modules flush on a pitched face (portrait, in rows along its eaves), within SHARE
    of it and EDGE of its edges."""
    count = min(count, math.floor(SHARE * face.area / MODULE_AREA))
    if count <= 0 or face.flat or face.tilt > PANEL_MAX_TILT:
        return []
    right, up = _frame(face)
    inner = _outline(face, right, up).buffer(-EDGE, join_style="mitre")
    rows = _rows(inner, MODULE_W, MODULE_H, MODULE_H + GAP)
    out = []
    for k, lefts in _pick(rows, count):
        b = rows.low(k)
        for a in lefts:
            uv = [(a, b), (a + MODULE_W, b), (a + MODULE_W, b + MODULE_H), (a, b + MODULE_H)]
            corners = np.array([face.centre + u * right + v * up for u, v in uv])
            out.append(_seated(corners, face, face.normal * LIFT))
    return out


def _seated(corners: np.ndarray, face: Face, lift: np.ndarray) -> list[list[float]]:
    """A module drawn on the face's mean plane, moved up or down onto the piece of roof under its middle
    (the survey models one roof plane as pieces a little apart), then lifted off it."""
    mid = corners.mean(axis=0)
    y = _roof_at(face.tris, float(mid[0]), float(mid[2]))
    dy = 0.0 if y is None else y - float(mid[1])
    return [[round(float(v), 3) for v in q + lift + (0.0, dy, 0.0)] for q in corners]


def _roof_at(tris: np.ndarray, x: float, z: float) -> float | None:
    """The height of the highest of the triangles over (x, z), None outside them all."""
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    e1, e2 = b[:, [0, 2]] - a[:, [0, 2]], c[:, [0, 2]] - a[:, [0, 2]]
    q = np.array([x, z]) - a[:, [0, 2]]
    den = e1[:, 0] * e2[:, 1] - e2[:, 0] * e1[:, 1]
    ok = np.abs(den) > 1e-9
    den = np.where(ok, den, 1.0)
    v = (q[:, 0] * e2[:, 1] - e2[:, 0] * q[:, 1]) / den
    w = (e1[:, 0] * q[:, 1] - q[:, 0] * e1[:, 1]) / den
    inside = ok & (v >= -1e-6) & (w >= -1e-6) & (v + w <= 1 + 1e-6)
    if not inside.any():
        return None
    y = a[:, 1] + v * (b[:, 1] - a[:, 1]) + w * (c[:, 1] - a[:, 1])
    return float(y[inside].max())


def flat_modules(face: Face, count: int) -> list[list[list[float]]]:
    """Up to `count` modules on a flat roof: landscape, in rows tilted FLAT_TILT to the south (along the
    building's sides closest to that), far enough apart for the winter sun to clear them."""
    from shapely import affinity

    count = min(count, math.floor(SHARE * face.area / MODULE_AREA))
    if count <= 0 or not face.flat:
        return []
    shape = _shape(face.tris[:, :, [0, 2]])
    facing = _flat_facing(shape)
    across = np.array([-facing[1], facing[0]])  # facing south (+z): across runs west
    tilt = math.radians(FLAT_TILT)
    depth, rise = MODULE_W * math.cos(tilt), MODULE_W * math.sin(tilt)
    pitch = depth + rise / math.tan(math.radians(WINTER_SUN))
    # the roof in (across, -facing) coordinates: its rows from the south side north
    local = affinity.affine_transform(shape, [across[0], across[1], -facing[0], -facing[1], 0, 0])
    inner = local.buffer(-FLAT_EDGE, join_style="mitre")
    rows = _rows(inner, MODULE_H, depth, pitch)
    n = face.normal

    def at(a: float, b: float, high: float) -> np.ndarray:
        x, z = a * across[0] - b * facing[0], a * across[1] - b * facing[1]
        y = face.centre[1] - (n[0] * (x - face.centre[0]) + n[2] * (z - face.centre[2])) / n[1]
        return np.array([x, y + high, z])

    out = []
    for k, lefts in _pick(rows, count):
        low = rows.low(k)  # the row's south edge
        for a in lefts:
            corners = np.array(
                [
                    at(a + MODULE_H, low, 0.0),
                    at(a, low, 0.0),
                    at(a, low + depth, rise),
                    at(a + MODULE_H, low + depth, rise),
                ]
            )
            seated = _seated(corners, face, np.array([0.0, LIFT, 0.0]))
            # seated by its middle: the low edge LIFT over the roof, the middle rise / 2 higher
            out.append([[x, round(y + rise / 2, 3), z] for x, y, z in seated])
    return out


def _flat_facing(shape: Any) -> np.ndarray:
    """The direction of the building's sides (its outline's least rectangle) that faces most to the south,
    as a level unit (x, z)."""
    rect = shape.minimum_rotated_rectangle
    xs, zs = rect.exterior.coords.xy
    if len(xs) < 3:
        return np.array([0.0, 1.0])
    edge = np.array([xs[1] - xs[0], zs[1] - zs[0]])
    edge /= max(np.linalg.norm(edge), 1e-9)
    options = [edge, -edge, np.array([-edge[1], edge[0]]), np.array([edge[1], -edge[0]])]
    return max(options, key=lambda d: float(d[1]))


def modules(fs: list[Face], count: int) -> list[list[list[float]]]:
    """`count` modules over a roof's faces: on the pitched ones facing south first (within 100° of it),
    then the flat ones, each to SHARE of it at most."""
    pitched = sorted(
        (f for f in fs if not f.flat and f.south > SOUTHISH and f.tilt <= PANEL_MAX_TILT),
        key=lambda f: -f.south,
    )
    flats = [f for f in fs if f.flat]
    out: list[list[list[float]]] = []
    for f in [*pitched, *flats]:
        if len(out) >= count:
            break
        more = count - len(out)
        out += flat_modules(f, more) if f.flat else pitched_modules(f, more)
    return out


def sample_photo(photo: np.ndarray) -> np.ndarray:
    """The photo (H x W x 3, uint8) as the kit samples it: SAMPLE_PIXELS across, sRGB 0 to 1."""
    from PIL import Image

    img = Image.fromarray(photo)
    if img.width > SAMPLE_PIXELS:
        img = img.resize((SAMPLE_PIXELS, SAMPLE_PIXELS), Image.Resampling.BOX)
    return np.asarray(img, dtype=np.float32) / 255


def samples(
    p: np.ndarray, roof: np.ndarray, pix: np.ndarray, radius: float, level: bool | None = None
) -> np.ndarray:
    """kit/context.js newerRoofs' samples: the photo's pixels spread over the roof's triangles (all, the
    level ones or the pitched ones), one a quarter square metre, drawn in toward the roof's middle
    (its edges blur into the ground)."""
    if not len(roof):
        return np.zeros((0, 3), dtype=np.float32)
    cx, cz = float(p[roof.reshape(-1), 0].mean()), float(p[roof.reshape(-1), 2].mean())
    h, w = pix.shape[:2]
    out = []
    for a, b, c in roof:
        if level is not None:
            n = np.cross(p[b] - p[a], p[c] - p[a])
            if (abs(n[1]) / (np.linalg.norm(n) or 1) > FLAT_ROOF) != level:
                continue
        ax, az = p[a, 0], p[a, 2]
        ex, ez, fx, fz = p[b, 0] - ax, p[b, 2] - az, p[c, 0] - ax, p[c, 2] - az
        count = min(400, max(1, math.floor(abs(ex * fz - ez * fx) / 2 / 0.25 + 0.5)))
        k = np.arange(count)
        u, v = (k * 0.618034 + 0.31) % 1, (k * 0.754878 + 0.17) % 1
        over = u + v > 1
        u, v = np.where(over, 1 - u, u), np.where(over, 1 - v, v)
        x, z = ax + u * ex + v * fx, az + u * ez + v * fz
        col = np.floor((cx + (x - cx) * 0.85 + radius) / (2 * radius) * w).astype(int)
        row = np.floor((cz + (z - cz) * 0.85 + radius) / (2 * radius) * h).astype(int)
        inside = (col >= 0) & (row >= 0) & (col < w) & (row < h)
        out.append(pix[row[inside], col[inside]])
    return np.concatenate(out) if out else np.zeros((0, 3), dtype=np.float32)


def _median(px: np.ndarray) -> np.ndarray:
    """The kit's median: each channel's middle value (the upper one of an even count)."""
    return np.asarray(np.sort(px, axis=0)[len(px) >> 1])


GREEN_SHARE = 0.5  # of a flat roof's pixels green: a green roof
MEMBRANE_DARK = 0.3  # a flat roof's median luminance under this…
MEMBRANE_EVEN = 0.1  # …and its middle 60 % within this: a membrane


def flat_kind(px: np.ndarray) -> str:
    """A flat roof's finish from the photo: green where it is greenish, membrane where it is dark and
    even, gravel otherwise."""
    if len(px) < 20:
        return "gravel"
    r, g, b = px[:, 0], px[:, 1], px[:, 2]
    mx, mn = px.max(axis=1), px.min(axis=1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-9), 0)
    hue = _hues(r, g, b, mx, mn)
    green = (hue >= 60) & (hue <= 170) & (sat > 0.12)
    if float(green.mean()) >= GREEN_SHARE:
        return "green"
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    spread = _percentile(lum, 0.8) - _percentile(lum, 0.2)
    if float(np.median(lum)) < MEMBRANE_DARK and spread < MEMBRANE_EVEN:
        return "membrane"
    return "gravel"


def _mix(h: int) -> int:
    # houseHash's high bits barely move between ids that differ in their last characters
    # ("lidar-12", "lidar-13"): murmur3's finaliser spreads them
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & 0xFFFFFFFF
    return h ^ (h >> 16)


def cover_for(family: str, year: int | None, hash_: int) -> tuple[str, str]:
    """A pitched roof's cover from its colour and its age, and why: clay before 1960 (beaver tail
    before 1920), then clay or concrete tiles; fibre cement or concrete tiles on grey from 1960 to
    2000; on anthracite after 2000, standing seam or fibre cement. The house's hash breaks ties."""
    roll = _mix(hash_) % 100
    if family in CLAY:
        if year is None:
            return "clay-interlocking", "colour"
        if year < 1920:
            return "clay-beaver", "year"
        if year < 1960:
            return "clay-interlocking", "year"
        return ("clay-interlocking" if roll < 70 else "concrete-tile"), "hash"
    if year is None:
        return "fibre-cement", "colour"
    if year < 1960:
        return "fibre-cement", "year"
    if year < 2000:
        return ("fibre-cement" if roll < 50 else "concrete-tile"), "hash"
    if family == "anthracite":
        return ("metal-seam" if roll < 50 else "fibre-cement"), "hash"
    return ("concrete-tile" if roll < 50 else "fibre-cement" if roll < 80 else "metal-seam"), "hash"


def footprints(buildings: list[dict[str, Any]]) -> list[Any]:
    """Each building's outline seen from above (its roof's triangles; without any, its points' hull)."""
    from shapely.geometry import MultiPoint, Polygon

    out = []
    for b in buildings:
        p = np.asarray(b["positions"], dtype=np.float64).reshape(-1, 3)[:, [0, 2]]
        t = np.asarray(b.get("roof") or [], dtype=np.int64).reshape(-1, 3)
        shape = _shape(p[t]) if len(t) else None
        if shape is None or shape.is_empty:
            shape = MultiPoint(p).convex_hull if len(p) >= 3 else Polygon()
        out.append(shape)
    return out


def match_records(fps: list[Any], recs: list[Record]) -> dict[int, list[Record]]:
    """The register's entries each building holds, the largest first (a house and its annex): those whose
    point lies inside it (in each of several overlapping volumes: parts of one building), and those
    outside every building within NEAR_RECORD of it. A building with none takes the entries of a
    building it touches (an annex, the rest of a block cut into volumes)."""
    from shapely.geometry import Point

    held: dict[int, list[Record]] = {}
    for r in recs:
        pt = Point(r.x, r.z)
        inside = [k for k, f in enumerate(fps) if not f.is_empty and f.contains(pt)]
        if not inside:
            near = [(f.distance(pt), k) for k, f in enumerate(fps) if not f.is_empty]
            inside = [min(near)[1]] if near and min(near)[0] <= NEAR_RECORD else []
        for k in inside:
            held.setdefault(k, []).append(r)
    out = {k: sorted(rs, key=lambda r: -(r.area or 0.0)) for k, rs in held.items()}
    for k, f in enumerate(fps):
        if k in held or f.is_empty:
            continue
        touching = [i for i in held if fps[i].distance(f) <= TOUCHING]
        if touching:
            out[k] = out[max(touching, key=lambda i: fps[i].area)]
    return out


def match_plants(
    fps: list[Any], held: dict[int, list[Record]], found: list[Plant]
) -> dict[int, list[Plant]]:
    """The photovoltaic plants on each building: a plant on the register's building of its egid (at that
    building's point: the address can be off the house), else on the house nearest its point, within
    NEAR_PLANT."""
    from shapely.geometry import Point

    by_egid: dict[str, tuple[Record, list[int]]] = {}
    for k, rs in held.items():
        for r in rs:
            by_egid.setdefault(r.egid, (r, []))[1].append(k)
    out: dict[int, list[Plant]] = {}
    for pl in found:
        rec, ks = by_egid.get(pl.egid or "", (None, []))
        if rec is not None and ks:
            k = max(ks, key=lambda i: fps[i].area)
            pl = Plant(egid=pl.egid, x=rec.x, z=rec.z, kw=pl.kw)
        else:
            pt = Point(pl.x, pl.z)
            near = [(f.distance(pt), i) for i, f in enumerate(fps) if not f.is_empty]
            if not near or min(near)[0] > NEAR_PLANT:
                continue
            k = min(near)[1]
        out.setdefault(k, []).append(pl)
    return out


PHOTO_SOUTH = 0.3  # a face facing this much south at least: no roof's own shade on it
PHOTO_BLUE = 0.45  # of its pixels panel-blue
PHOTO_BRIGHT = 0.2  # their median brightest channel: darker is a shadow, not a module
PHOTO_AREA = 5.0  # m² of blue at least


def photo_modules(face: Face, photo: np.ndarray, radius: float) -> list[list[list[float]]]:
    """Modules where the photo (full size, uint8) shows the blue of solar panels over most of a pitched
    face lit by the sun, as many as that blue covers. Conservative: a wrong panel is worse than none,
    and black modules or panels on a flat roof (where shadows and skylights read as blue) are left out."""
    from shapely import contains_xy

    if face.flat or face.south < PHOTO_SOUTH or face.tilt > PANEL_MAX_TILT:
        return []
    plan = _shape(face.tris[:, :, [0, 2]]).buffer(-0.3, join_style="mitre")
    if plan.is_empty:
        return []
    size = photo.shape[0]
    cell = 2 * radius / size
    x0, z0, x1, z1 = plan.bounds
    xs, zs = np.meshgrid(np.arange(x0, x1, cell), np.arange(z0, z1, cell))
    inside = contains_xy(plan, xs.ravel(), zs.ravel())
    col = np.floor((xs.ravel()[inside] + radius) / (2 * radius) * size).astype(int)
    row = np.floor((zs.ravel()[inside] + radius) / (2 * radius) * size).astype(int)
    ok = (col >= 0) & (row >= 0) & (col < size) & (row < size)
    px = photo[row[ok], col[ok]].astype(np.float32) / 255
    if len(px) < 50:
        return []
    mx, mn = px.max(axis=1), px.min(axis=1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-9), 0)
    hue = _hues(px[:, 0], px[:, 1], px[:, 2], mx, mn)
    blue = (hue >= 190) & (hue <= 260) & (sat > 0.15)
    if float(blue.mean()) < PHOTO_BLUE or float(np.median(mx[blue])) < PHOTO_BRIGHT:
        return []
    area = float(blue.sum()) * cell * cell / max(float(face.normal[1]), 0.1)
    if area < PHOTO_AREA:
        return []
    return pitched_modules(face, math.floor(area / MODULE_AREA))


NEW_HOUSE = (
    2015  # a house only the lidar has (newer than swissBUILDINGS3D) is covered as one this new
)


def decide(
    buildings: list[dict[str, Any]],
    photo: np.ndarray,
    radius: float,
    recs: list[Record] | None,
    found: list[Plant] | None,
) -> list[dict[str, Any]]:
    """Each building's roof spec (see the module's docstring), in their order. `photo` is the aerial
    photo over the surroundings' square (uint8, north up); `recs` and `found` the registers' answers
    (None: that register could not be read)."""
    pix = sample_photo(photo)
    fps = footprints(buildings)
    held = match_records(fps, recs or [])
    on = match_plants(fps, held, found or [])
    geo = [
        (
            np.asarray(b["positions"], dtype=np.float64).reshape(-1, 3),
            np.asarray(b.get("roof") or [], dtype=np.int64).reshape(-1, 3),
        )
        for b in buildings
    ]
    pitched_px = [samples(p, t, pix, radius, level=False) for p, t in geo]
    # the neighbourhood's photographed roofs: the colours of those the photo does not show
    palette = [
        _median(px)
        for b, px in zip(buildings, pitched_px, strict=True)
        if b.get("source") != "lidar" and len(px) >= 20
    ]
    out = []
    for k, b in enumerate(buildings):
        p, t = geo[k]
        h = house_hash(b["id"])
        every = samples(p, t, pix, radius)
        shown = b.get("source") != "lidar" or roof_look(every) == "photo"
        own = pitched_px[k] if len(pitched_px[k]) >= 20 else every
        if shown and len(own) >= 20:
            family, colour_why = roof_family(_median(own)), "photo"
        elif palette:
            family, colour_why = roof_family(palette[(h >> 9) % len(palette)]), "neighbours"
        else:
            family, colour_why = FAMILIES[(h >> 17) % len(FAMILIES)], "neighbours"
        rec = held[k][0] if k in held else None
        year = rec.year if rec else None
        built = year if year is not None or b.get("source") != "lidar" else NEW_HOUSE
        cover, cover_why = cover_for(family, built, h)
        level_px = samples(p, t, pix, radius, level=True) if shown else every[:0]
        plants_here = on.get(k, [])
        panels: list[list[list[float]]] = []
        try:
            if plants_here:
                panels = _plant_modules(b, fps[k], plants_here)
            elif shown:
                fs = faces(b["positions"], b.get("roof") or [])
                panels = [m for f in fs for m in photo_modules(f, photo, radius)]
        # a survey mesh the geometry cannot follow loses its modules, not the rest of its roof
        except Exception as e:
            logger.warning("geo.roof_modules_failed", extra={"building": b["id"], "error": str(e)})
            panels = []
        panels_why = ("bfe" if plants_here else "photo") if panels else None
        spec: dict[str, Any] = {
            "cover": cover,
            "flat": flat_kind(level_px),
            "colour": roof_tint(family, h),
            "family": family,
            "year": year,
            "pv_kw": round(sum(pl.kw for pl in plants_here), 2) if plants_here else None,
            "panels": panels,
            "why": {"colour": colour_why, "cover": cover_why, "panels": panels_why},
            "egid": rec.egid if rec else None,
        }
        if len(level_px) >= 20:
            spec["flat_colour"] = _hex(_median(level_px))
        out.append(spec)
    return out


def _plant_modules(b: dict[str, Any], fp: Any, plants_here: list[Plant]) -> list[list[list[float]]]:
    """The plants' modules, each plant's on the part of a multi-part entry nearest its point."""
    from shapely.geometry import Point

    fs = faces(b["positions"], b.get("roof") or [])
    parts = list(getattr(fp, "geoms", [fp]))
    part_of = [_nearest(parts, Point(f.centre[0], f.centre[2])) for f in fs]
    by_part: dict[int, float] = {}
    for pl in plants_here:
        j = _nearest(parts, Point(pl.x, pl.z))
        by_part[j] = by_part.get(j, 0.0) + pl.kw
    panels: list[list[list[float]]] = []
    for j, kw in by_part.items():
        panels += modules([f for f, q in zip(fs, part_of, strict=True) if q == j], module_count(kw))
    return panels


def _nearest(parts: list[Any], pt: Any) -> int:
    return min(range(len(parts)), key=lambda j: parts[j].distance(pt)) if parts else 0
