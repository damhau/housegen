"""The ground by type around the house (#50): the cadastral survey's land cover (AV/MO "couverture du sol").

geodienste.ch serves it as a national WMS (the WFS is open in some cantons only, not in Vaud). The WMS
draws the land cover's outlines; every region between them is one surface, and GetFeatureInfo at a
point inside it returns that surface's polygon (holes included) and its kind ("Genre": jardin,
route_chemin, trottoir…). Each region is asked once; the polygons are turned into soft masks on a
0.25 m grid (cover.png): R asphalt, G lawn, B paving, A gravel. What none of them covers (fields,
woods, buildings, water) keeps the aerial photo.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from dataclasses import dataclass, field

import httpx
import numpy as np

logger = logging.getLogger(__name__)

WMS = "https://geodienste.ch/db/av_0/fra"
CELL = 0.5  # metres per pixel of the outline image asked to find the regions
MASK_CELL = 0.25  # metres per pixel of the masks (crisp road edges)
CONCURRENCY = 8

# the survey's kinds (DM.01, French) → the mask they go in
CHANNELS: dict[str, int] = {
    "route_chemin": 0,  # asphalt
    "jardin": 1,  # lawn
    "trottoir": 2,  # paving
    "ilot_circulation": 2,
    "autre_revetement_dur": 2,
    "place_aviation": 2,
    "gravier_sable": 3,  # gravel and bare ground
    "autre_sans_vegetation": 3,
    "voie_ferree": 3,
    "rocher": 3,
    "eboulis": 3,
    "exploitation_de_materiaux": 3,
}
NAMES = ("asphalt", "lawn", "paving", "gravel")


@dataclass
class Surface:
    kind: str
    rings: list[list[tuple[float, float]]] = field(
        default_factory=list
    )  # local (x, z); the first outer


def _params(e0: float, n0: float, ext: float, size: int) -> dict[str, str | int]:
    return {
        "SERVICE": "WMS",
        "VERSION": "1.3.0",
        "LAYERS": "LCSF",
        "STYLES": "",
        "CRS": "EPSG:2056",
        "BBOX": f"{e0 - ext},{n0 - ext},{e0 + ext},{n0 + ext}",
        "WIDTH": size,
        "HEIGHT": size,
    }


_COORDS = re.compile(r"<gml:coordinates>([^<]*)</gml:coordinates>")
_RING = re.compile(r"<gml:(outer|inner)BoundaryIs>(.*?)</gml:\1BoundaryIs>", re.S)
_FEATURE = re.compile(r"<LCSF_feature>(.*?)</LCSF_feature>", re.S)
_GENRE = re.compile(r"<Genre>([^<]*)</Genre>")


def parse_features(gml: str, e0: float, n0: float) -> list[Surface]:
    """The surfaces of a GetFeatureInfo answer (MapServer GML 2), in the local frame."""
    out = []
    for f in _FEATURE.findall(gml):
        genre = _GENRE.search(f)
        if not genre:
            continue
        rings: list[list[tuple[float, float]]] = []
        for kind, body in _RING.findall(f):
            m = _COORDS.search(body)
            if not m:
                continue
            pts = [tuple(map(float, p.split(","))) for p in m.group(1).split()]
            ring = [(e - e0, -(n - n0)) for e, n in pts]
            if kind == "outer":
                rings.insert(0, ring)
            else:
                rings.append(ring)
        if rings:
            out.append(Surface(kind=genre.group(1), rings=rings))
    return out


def regions(outline: np.ndarray, min_pixels: int = 6) -> list[tuple[int, int]]:
    """A point well inside each region between the drawn outlines (column, row)."""
    from scipy import ndimage as ndi

    free = outline > 140  # the lines are dark; the fills (buildings: light grey) are regions too
    labels, count = ndi.label(free)
    if not count:
        return []
    depth = ndi.distance_transform_edt(free)
    out = []
    for k, sl in enumerate(ndi.find_objects(labels), start=1):
        if sl is None:
            continue
        m = labels[sl] == k
        if m.sum() < min_pixels:
            continue
        d = np.where(m, depth[sl], -1)
        j, i = np.unravel_index(int(np.argmax(d)), d.shape)
        out.append((int(i + sl[1].start), int(j + sl[0].start)))
    return out


async def surfaces(e0: float, n0: float, ext: float, client: httpx.AsyncClient) -> list[Surface]:
    """The land cover's surfaces over the square anchor ± ext (empty where the survey has none)."""
    from PIL import Image

    size = round(2 * ext / CELL)
    r = await client.get(
        WMS,
        params={**_params(e0, n0, ext, size), "REQUEST": "GetMap", "FORMAT": "image/png"},
        timeout=60,
    )
    r.raise_for_status()
    if not r.headers.get("content-type", "").startswith("image/"):
        raise RuntimeError(f"the land cover service answered {r.text[:200]}")
    img = np.asarray(Image.open(io.BytesIO(r.content)).convert("L"))
    points = regions(img)
    sem = asyncio.Semaphore(CONCURRENCY)
    seen: set[tuple[str, int, int]] = set()
    out: list[Surface] = []

    async def ask(i: int, j: int) -> None:
        params = {
            **_params(e0, n0, ext, size),
            "REQUEST": "GetFeatureInfo",
            "QUERY_LAYERS": "LCSF",
            "I": i,
            "J": j,
            "INFO_FORMAT": "application/vnd.ogc.gml",
            "FEATURE_COUNT": 1,
        }
        async with sem:
            res = await client.get(WMS, params=params, timeout=60)
        res.raise_for_status()
        for s in parse_features(res.text, e0, n0):
            xs = [p[0] for p in s.rings[0]]
            zs = [p[1] for p in s.rings[0]]
            key = (s.kind, round(min(xs) + max(xs)), round(min(zs) + max(zs)))
            if key not in seen:
                seen.add(key)
                out.append(s)

    await asyncio.gather(*(ask(i, j) for i, j in points))
    return out


def masks(found: list[Surface], ext: float) -> np.ndarray:
    """The four masks (asphalt, lawn, paving, gravel) on the MASK_CELL grid over ± ext, rows north → south."""
    from rasterio.features import rasterize
    from rasterio.transform import Affine
    from shapely.geometry import Polygon

    size = round(2 * ext / MASK_CELL)
    transform = Affine(MASK_CELL, 0, -ext, 0, MASK_CELL, -ext)
    out = np.zeros((size, size, 4), dtype=np.uint8)
    for ch in range(4):
        shapes = []
        for s in found:
            if CHANNELS.get(s.kind) != ch:
                continue
            poly = Polygon(s.rings[0], s.rings[1:]).buffer(0)
            if not poly.is_empty:
                shapes.append((poly, 255))
        if shapes:
            out[:, :, ch] = rasterize(
                shapes, out_shape=(size, size), transform=transform, fill=0, dtype="uint8"
            )
    return out


def cover_png(m: np.ndarray) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(m, "RGBA").save(buf, "PNG", optimize=True)
    return buf.getvalue()


def class_colours(
    m: np.ndarray, photo: np.ndarray, ext: float, radius: int
) -> list[list[float] | None]:
    """The photo's mean colour (0..1, sRGB) under each mask: the kit tints its finishes by the photo's
    colour there relative to this mean (a red court stays red-ish, a dry lawn yellowish)."""
    # the photo spans ± radius at its own resolution; the masks ± ext at MASK_CELL
    size = m.shape[0]
    px = photo.shape[0] / (2 * radius)
    centres = (np.arange(size) + 0.5) * MASK_CELL - ext
    idx = np.clip(((centres + radius) * px).astype(int), 0, photo.shape[0] - 1)
    sample = photo[np.ix_(idx, idx)].reshape(-1, 3) / 255.0
    out: list[list[float] | None] = []
    for ch in range(4):
        w = m[:, :, ch].reshape(-1) > 200
        out.append([round(float(v), 3) for v in sample[w].mean(axis=0)] if w.sum() > 50 else None)
    return out
