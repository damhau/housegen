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
  props        the equipment on its flat faces (#146), placed at random from the house's hash:
               {"kind": vent | skylight-dome | skylight-flat | hatch | hvac | stack | housing | rail |
               walkway, "at": [x, y, z] the middle of its footprint on the roof (y the roof's surface),
               "size": [w, d, h] metres (w along yaw), "yaw": radians about +y (0: w along +x, so w runs
               along (cos yaw, 0, -sin yaw) and d along (sin yaw, 0, cos yaw))}
  egid         optional: the register's building id
  flat_colour  optional: the photo's median over the level faces (sRGB), where it shows them

The registers come from geo.admin's identify, no key needed.
"""

from __future__ import annotations

import logging
import math
import random
import re
from dataclasses import dataclass, field
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
    mid = tri.mean(axis=1)
    groups: list[list[int]] = []
    normals: list[np.ndarray] = []
    offsets: list[float] = []
    for i in np.argsort(-area):
        for k, (gn, gd) in enumerate(zip(normals, offsets, strict=True)):
            # measured along the group's normal: the altitudes (hundreds of metres) would turn a
            # fraction of a degree between two triangles' own normals into metres
            if float(gn @ n[i]) > cos_same and abs(float(gn @ mid[i]) - gd) < SAME_PLANE_M:
                groups[k].append(int(i))
                break
        else:
            groups.append([int(i)])
            normals.append(n[i])
            offsets.append(float(n[i] @ mid[i]))
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
    fps: list[Any],
    held: dict[int, list[Record]],
    found: list[Plant],
    roofed: list[bool] | None = None,
) -> dict[int, list[Plant]]:
    """The photovoltaic plants on each building: a plant on the register's building of its egid (at that
    building's point: the address can be off the house), else on the house nearest its point, within
    NEAR_PLANT. Only on buildings with a roof (`roofed`, all by default): the survey has entries that
    are walls alone."""
    from shapely.geometry import Point

    can = roofed or [True] * len(fps)
    by_egid: dict[str, tuple[Record, list[int]]] = {}
    for k, rs in held.items():
        for r in rs:
            by_egid.setdefault(r.egid, (r, []))[1].append(k)
    out: dict[int, list[Plant]] = {}
    for pl in found:
        rec, ks = by_egid.get(pl.egid or "", (None, []))
        if rec is not None:
            pl = Plant(egid=pl.egid, x=rec.x, z=rec.z, kw=pl.kw)
        ks = [i for i in ks if can[i]]
        if ks:
            k = max(ks, key=lambda i: fps[i].area)
        else:
            pt = Point(pl.x, pl.z)
            near = [(f.distance(pt), i) for i, f in enumerate(fps) if can[i] and not f.is_empty]
            if not near or min(near)[0] > NEAR_PLANT:
                continue
            k = min(near)[1]
        out.setdefault(k, []).append(pl)
    return out


PHOTO_SOUTH = 0.3  # a face facing this much south at least: no roof's own shade on it
PHOTO_BLUE = 0.45  # of its pixels panel-blue
PHOTO_BRIGHT = (0.2, 0.5)  # their median brightest channel: darker is a shadow, brighter a pale
# roof, a pool or a tarp (modules read 0.26 to 0.34 on Le Mont and Montelly; a pale grey roof 0.75)
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
    if float(blue.mean()) < PHOTO_BLUE:
        return []
    lo, hi = PHOTO_BRIGHT
    if not lo <= float(np.median(mx[blue])) <= hi:
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
    roofed = [bool(b.get("roof")) for b in buildings]
    on = match_plants(fps, held, found or [], roofed)
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
        fs: list[Face] = []
        try:
            fs = faces(b["positions"], b.get("roof") or [])
            if plants_here:
                panels = _plant_modules(fs, fps[k], plants_here)
            elif shown:
                panels = [m for f in fs for m in photo_modules(f, photo, radius)]
        # a survey mesh the geometry cannot follow loses its modules, not the rest of its roof
        except Exception as e:
            logger.warning("geo.roof_modules_failed", extra={"building": b["id"], "error": str(e)})
            panels = []
        panels_why = ("bfe" if plants_here else "photo") if panels else None
        try:
            equipment = props(fs, panels, h, year)
        except Exception as e:
            logger.warning("geo.roof_props_failed", extra={"building": b["id"], "error": str(e)})
            equipment = []
        spec: dict[str, Any] = {
            "cover": cover,
            "flat": flat_kind(level_px),
            "colour": roof_tint(family, h),
            "family": family,
            "year": year,
            "pv_kw": round(sum(pl.kw for pl in plants_here), 2) if plants_here else None,
            "panels": panels,
            "why": {"colour": colour_why, "cover": cover_why, "panels": panels_why},
            "props": equipment,
            "egid": rec.egid if rec else None,
        }
        if len(level_px) >= 20:
            spec["flat_colour"] = _hex(_median(level_px))
        out.append(spec)
    return out


def _plant_modules(fs: list[Face], fp: Any, plants_here: list[Plant]) -> list[list[list[float]]]:
    """The plants' modules, each plant's on the part of a multi-part entry nearest its point."""
    from shapely.geometry import Point

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


# the equipment on the flat roofs (#146)
PROP_EDGE = 0.8  # metres: kept clear of a flat roof's edges (its parapet)
PROP_GAP = 0.4  # metres between two pieces
VENT_GAP = 0.25  # between the pipes of a cluster
MODULE_CLEAR = 0.3  # metres round a solar module
HIGHER = 0.3  # metres: a part of the building this much higher than a flat face is not on it
TINY_ROOF = 8.0  # m²: a flat face this small takes nothing
SMALL_ROOF = 25.0  # m²: a garage's, an annex's: nothing, or one vent
BLOCK_ROOF = 180.0  # m²: a block's from here, a house's under it
BLOCK_PART = 80.0  # m²: a face this large of a building whose flat faces add up to BLOCK_TOTAL
BLOCK_TOTAL = 400.0
HOUSING_ROOF = 350.0  # m²: a lift or stair housing from here
STANDS_ON = (
    1.0  # metres: a higher part of the building this close stands on the face (one there already)
)
TRIES = 40
RAIL_IN = 0.9  # metres: a fall-protection rail inside the roof's edge


@dataclass
class _Roof:
    """One flat face being equipped: where a piece may stand, what is placed, its axes."""

    face: Face
    inner: Any  # where a footprint may lie: PROP_EDGE inside the face, clear of higher parts
    outline: Any  # the face seen from above
    yaw: float  # its long side's direction
    rng: random.Random
    taken: list[tuple[Any, float]] = field(default_factory=list)  # footprints, and their gap
    props: list[dict[str, Any]] = field(default_factory=list)


def footprint(x: float, z: float, w: float, d: float, yaw: float) -> Any:
    """A piece's footprint seen from above: w along (cos yaw, -sin yaw) in (x, z), d across it."""
    from shapely.geometry import Polygon

    c, s = math.cos(yaw), math.sin(yaw)
    return Polygon(
        [
            (x + a * c * w / 2 + b * s * d / 2, z - a * s * w / 2 + b * c * d / 2)
            for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1))
        ]
    )


def _axis(outline: Any) -> float:
    """The yaw of a shape's long side (its least rectangle's), in (-π/2, π/2]."""
    rect = outline.minimum_rotated_rectangle
    if rect.geom_type != "Polygon":
        return 0.0
    xs, zs = rect.exterior.coords.xy
    a = (xs[1] - xs[0], zs[1] - zs[0])
    b = (xs[2] - xs[1], zs[2] - zs[1])
    dx, dz = a if math.hypot(*a) >= math.hypot(*b) else b
    yaw = math.atan2(-dz, dx)
    if yaw <= -math.pi / 2:
        yaw += math.pi
    elif yaw > math.pi / 2:
        yaw -= math.pi
    return yaw


def _free(roof: _Roof, fp: Any, gap: float, only: bool = False) -> bool:
    """Whether a footprint lies inside the roof's room, `gap` from every piece (or the piece's own gap
    when larger, unless `only`)."""
    if not roof.inner.contains(fp):
        return False
    x0, z0, x1, z1 = fp.bounds
    for t, g in roof.taken:
        tx0, tz0, tx1, tz1 = t.bounds
        clear = gap if only else max(gap, g)
        if tx0 - clear > x1 or x0 - clear > tx1 or tz0 - clear > z1 or z0 - clear > tz1:
            continue
        if fp.distance(t) < clear:
            return False
    return True


def _keep(roof: _Roof, kind: str, fp: Any, size: tuple[float, float, float], yaw: float) -> Any:
    c = fp.centroid
    y = _roof_at(roof.face.tris, c.x, c.y)
    if y is None:
        n, o = roof.face.normal, roof.face.centre
        y = float(o[1] - (n[0] * (c.x - o[0]) + n[2] * (c.y - o[2])) / n[1])
    roof.taken.append((fp, VENT_GAP if kind == "vent" else PROP_GAP))
    roof.props.append(
        {
            "kind": kind,
            "at": [round(c.x, 3), round(y, 3), round(c.y, 3)],
            "size": [round(v, 2) for v in size],
            "yaw": round(yaw, 4),
        }
    )
    return fp


def _put(
    roof: _Roof,
    kind: str,
    size: tuple[float, float, float],
    near: tuple[float, float] | None = None,
    spread: float = 1.5,
    gap: float = PROP_GAP,
) -> Any:
    """A piece where it fits, at random (within `spread` of `near` when given), along the roof's long
    side: its footprint, None when it fits nowhere."""
    x0, z0, x1, z1 = roof.inner.bounds
    for _ in range(TRIES):
        if near is None:
            x, z = roof.rng.uniform(x0, x1), roof.rng.uniform(z0, z1)
        else:
            x = near[0] + roof.rng.uniform(-spread, spread)
            z = near[1] + roof.rng.uniform(-spread, spread)
        fp = footprint(x, z, size[0], size[1], roof.yaw)
        if _free(roof, fp, gap):
            return _keep(roof, kind, fp, size, roof.yaw)
    return None


def _vents(roof: _Roof, count: int) -> None:
    """Vent pipes in clusters of one to three (a flat's plumbing), now and then a mushroom vent."""
    r = roof.rng
    left = count
    while left > 0:
        if r.random() < 0.3:
            d = r.uniform(0.28, 0.4)
            size = (d, d, r.uniform(0.3, 0.45))
        else:
            d = r.uniform(0.11, 0.16)
            size = (d, d, r.uniform(0.45, 0.9))
        first = _put(roof, "vent", size)
        left -= 1
        if first is None:
            return
        for _ in range(min(left, r.randint(0, 2))):
            c = first.centroid
            d = r.uniform(0.1, 0.15)
            if _put(roof, "vent", (d, d, r.uniform(0.45, 0.9)), (c.x, c.y), 0.9, VENT_GAP):
                left -= 1


def _skylights(roof: _Roof, count: int, year: int | None) -> list[Any]:
    """Skylights in a row along the roof's long side (over a corridor or a stair): domes, or flat glass
    on a curb, more often on a newer house."""
    r = roof.rng
    flat = r.random() < (0.7 if year is not None and year >= 2000 else 0.3)
    if flat:
        w, d = r.choice([(1.0, 1.0), (1.2, 1.2), (1.0, 1.5), (1.2, 2.0), (1.5, 2.5)])
        size = (w, d, r.uniform(0.3, 0.4))
    else:
        w, d = r.choice([(0.9, 0.9), (1.0, 1.0), (1.2, 1.2), (1.0, 1.5), (1.2, 1.8)])
        size = (w, d, r.uniform(0.45, 0.6))
    kind = "skylight-flat" if flat else "skylight-dome"
    step = size[0] + r.uniform(1.2, 2.5)
    c, s = math.cos(roof.yaw), math.sin(roof.yaw)
    out: list[Any] = []
    first = _put(roof, kind, size)
    if first is None:
        return out
    out.append(first)
    o = first.centroid
    for k in range(1, count):
        fp = footprint(o.x + k * step * c, o.y - k * step * s, size[0], size[1], roof.yaw)
        put = _keep(roof, kind, fp, size, roof.yaw) if _free(roof, fp, PROP_GAP) else None
        put = put or _put(roof, kind, size)
        if put is not None:
            out.append(put)
    return out


def _rail(roof: _Roof, near: Any) -> None:
    """A fall-protection rail along the roof's edge nearest the hatch, RAIL_IN inside it."""
    from shapely.geometry import Point

    shape = max(getattr(roof.outline, "geoms", [roof.outline]), key=lambda g: g.area)
    xs, zs = shape.exterior.coords.xy
    c = np.array([near.centroid.x, near.centroid.y])
    edges = []
    for k in range(len(xs) - 1):
        p, q = np.array([xs[k], zs[k]]), np.array([xs[k + 1], zs[k + 1]])
        length = float(np.linalg.norm(q - p))
        if length < 2 * RAIL_IN + 2.0:
            continue
        e = (q - p) / length
        n = np.array([-e[1], e[0]])
        if not shape.contains(Point(*((p + q) / 2 + 0.5 * n))):
            n = -n
        edges.append((abs(float(np.dot(c - p, n))), p, e, n, length, float(np.dot(c - p, e))))
    want = roof.rng.uniform(4.0, 10.0)
    for _, p, e, n, length, t in sorted(edges, key=lambda x: x[0])[:3]:
        span = min(want, length - 2 * RAIL_IN)
        while span >= 2.0:
            t0 = min(max(t, RAIL_IN + span / 2), length - RAIL_IN - span / 2)
            mid = p + t0 * e + RAIL_IN * n
            yaw = math.atan2(-e[1], e[0])
            fp = footprint(float(mid[0]), float(mid[1]), span, 0.05, yaw)
            if _free(roof, fp, 0.3):
                _keep(roof, "rail", fp, (span, 0.05, 1.1), yaw)
                return
            span *= 0.6


def _walkway(roof: _Roof, start: Any, end: Any, width: float) -> None:
    """Paving slabs from one piece to another along the roof's axes (one turn), clear of the rest."""
    from shapely.geometry import LineString

    c, s = math.cos(roof.yaw), math.sin(roof.yaw)
    a, b = start.centroid, end.centroid
    du = (b.x - a.x) * c - (b.y - a.y) * s  # along the roof's long side
    corner = (a.x + du * c, a.y - du * s)
    ends = [start.buffer(0.1, join_style="mitre"), end.buffer(0.1, join_style="mitre")]
    legs = []
    for p, q, yaw in (((a.x, a.y), corner, roof.yaw), (corner, (b.x, b.y), roof.yaw - math.pi / 2)):
        if math.dist(p, q) < 0.5:
            continue
        line: Any = LineString([p, q])
        for blocked in ends:
            line = line.difference(blocked)
        pieces = [g for g in getattr(line, "geoms", [line]) if not g.is_empty]
        if pieces:
            legs.append((max(pieces, key=lambda g: g.length), yaw))
    planned = []
    for line, yaw in legs:
        (x0, z0), (x1, z1) = line.coords[0], line.coords[-1]
        length = line.length
        if length < 0.5:
            continue
        ux, uz = (x1 - x0) / length, (z1 - z0) / length
        mid = ((x0 + x1) / 2, (z0 + z1) / 2)
        # the first leg runs on under the turn, the second starts past it: one square, not two
        if len(legs) == 2 and math.dist((x1, z1), corner) < 1e-6:
            length += width / 2
            mid = (mid[0] + ux * width / 4, mid[1] + uz * width / 4)
        elif len(legs) == 2 and math.dist((x0, z0), corner) < 1e-6:
            # and a slab's joint between the two
            skip = width / 2 + 0.01
            length -= skip
            mid = (mid[0] + ux * skip / 2, mid[1] + uz * skip / 2)
        if length >= 0.5:
            planned.append((footprint(mid[0], mid[1], length, width, yaw), length, yaw))
    if planned and all(_free(roof, fp, 0.05, only=True) for fp, _, _ in planned):
        for fp, length, yaw in planned:
            _keep(roof, "walkway", fp, (length, width, 0.04), yaw)


def _equip(roof: _Roof, scale: str, year: int | None, may_house: bool, main: bool) -> None:
    r = roof.rng
    area = roof.face.area
    if scale == "small":
        if r.random() < 0.5:
            _vents(roof, 1)
        return
    if scale == "house":
        if r.random() < 0.4:
            _skylights(roof, 2 if area > 80 and r.random() < 0.3 else 1, year)
        _vents(roof, r.randint(1, 3))
        return
    housing = None
    if may_house and area >= HOUSING_ROOF and r.random() < 0.6:
        housing = _put(
            roof, "housing", (r.uniform(2.4, 3.2), r.uniform(3.0, 4.5), r.uniform(2.4, 2.8))
        )
    units = []
    for _ in range(2 if area >= 800 and r.random() < 0.5 else 1):
        if r.random() < (0.6 if area >= 300 else 0.4):
            size = (r.uniform(1.6, 3.2), r.uniform(0.9, 1.6), r.uniform(1.0, 1.6))
            units.append(_put(roof, "hvac", size))
    lights = _skylights(roof, r.randint(1, min(4, 1 + int(area // 200))), year)
    hatch = None
    # one way up a block, mostly: a hatch on its main roof, on half of its other parts
    if r.random() < (0.3 if housing is not None else 1.0 if main else 0.5):
        hatch = _put(
            roof, "hatch", (r.uniform(0.9, 1.2), r.uniform(0.9, 1.4), r.uniform(0.45, 0.6))
        )
    if r.random() < 0.35:
        d = r.uniform(0.25, 0.45)
        _put(roof, "stack", (d, d, r.uniform(1.0, 2.2)))
    _vents(roof, min(10, 3 + int(area // 150) + r.randint(0, 2)))
    if hatch is not None and r.random() < 0.35:
        _rail(roof, hatch)
    access = hatch if hatch is not None else housing
    target = next((u for u in units if u is not None), None) or (lights[-1] if lights else None)
    if access is not None and target is not None and r.random() < 0.5:
        _walkway(roof, access, target, 1.0 if r.random() < 0.3 else 0.5)


def props(
    fs: list[Face], panels: list[list[list[float]]], hash_: int, year: int | None
) -> list[dict[str, Any]]:
    """The equipment on a building's flat faces (see the module's docstring), drawn by the house's hash:
    clear of its modules and of its higher parts, PROP_EDGE inside each face's edge, never overlapping,
    as many and as large as the face allows (nothing or a vent on an annex, vents and a skylight on a
    house, skylights, a hatch, units, a stack, a housing, a rail and a walkway on a block)."""
    import shapely
    from shapely.geometry import Polygon

    flats = [k for k, f in enumerate(fs) if f.flat and f.area >= TINY_ROOF]
    if not flats:
        return []
    clear = [
        Polygon(np.asarray(q, dtype=np.float64)[:, [0, 2]]).buffer(MODULE_CLEAR, join_style="mitre")
        for q in panels
    ]
    modules_plan = shapely.union_all(clear) if clear else None
    plans = [_shape(f.tris[:, :, [0, 2]]) for f in fs]
    tops = [float(f.tris[:, :, 1].max()) for f in fs]
    total = sum(fs[k].area for k in flats)
    out: list[dict[str, Any]] = []
    for k in flats:
        f, y = fs[k], float(fs[k].centre[1])
        others = [i for i in range(len(fs)) if i != k and not plans[i].is_empty]
        inner = plans[k].buffer(-PROP_EDGE, join_style="mitre")
        for i in others:
            if tops[i] > y + HIGHER:
                inner = inner.difference(plans[i].buffer(PROP_GAP, join_style="mitre"))
        if inner.is_empty:
            continue
        shapely.prepare(inner)
        roof = _Roof(f, inner, plans[k], _axis(plans[k]), random.Random(_mix(hash_) * 64 + k))
        if modules_plan is not None:
            roof.taken.append((modules_plan, 0.0))
        # the survey has a higher part standing on it already (a housing, the block's next storey)
        stands_on = any(
            tops[i] > y + STANDS_ON and plans[i].distance(plans[k]) <= STANDS_ON for i in others
        )
        if f.area < SMALL_ROOF:
            scale = "small"
        elif f.area >= BLOCK_ROOF or (total >= BLOCK_TOTAL and f.area >= BLOCK_PART):
            scale = "block"
        else:
            scale = "house"
        _equip(roof, scale, year, not stands_on, k == flats[0])
        out += roof.props
    return out
