"""Swiss public geodata around a place (#39): where it is, its ground, its aerial photo, its buildings.

All from swisstopo open data (© swisstopo), no key needed:
  - geo.admin SearchServer: an address or "parcel number + commune" → LV95 (EPSG:2056)
  - swissALTI3D (0.5 m terrain, cloud-optimised GeoTIFF, read by window over HTTP)
  - SWISSIMAGE (10 cm aerial photo, through the WMS)
  - swissBUILDINGS3D as the 3D Tiles map.geo.admin.ch draws (Draco glTF in b3dm, current edition)

Everything is returned in a LOCAL frame around an anchor point (metres): x east, z SOUTH (the kit's
frame: -z is north), y the altitude above sea level (LN02).
"""

from __future__ import annotations

import asyncio
import json
import math
import struct
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

import httpx
import numpy as np

SEARCH = "https://api3.geo.admin.ch/rest/services/api/SearchServer"
STAC = "https://data.geo.admin.ch/api/stac/v0.9/collections"
WMS = "https://wms.geo.admin.ch/"
BUILDINGS = "https://3d.geo.admin.ch/ch.swisstopo.swissbuildings3d.3d/v1/tileset.json"
EARTH = 6_371_000.0
UA = {"User-Agent": "housegen (https://github.com/damhau/housegen)"}


@dataclass
class Place:
    label: str  # plain text ("Le Mont-sur-Lausanne 3013", "Chemin de la Viane 59 1052 Le Mont-sur-Lausanne")
    kind: str  # "parcel" | "address" | "place"
    e: float  # LV95 easting
    n: float  # LV95 northing


@dataclass
class Building:
    id: str
    height: float
    year: int | None
    positions: list[float] = field(default_factory=list)  # local x, y (altitude), z
    roof: list[int] = field(default_factory=list)  # triangles (vertex indices)
    wall: list[int] = field(default_factory=list)


def _plain(label: str) -> str:
    import re

    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", label)).replace(" #", "").strip()


async def search(query: str, client: httpx.AsyncClient, limit: int = 8) -> list[Place]:
    """Addresses and parcels matching `query` ("Chemin de la Viane 59 Le Mont", "3013 Le Mont-sur-Lausanne")."""
    r = await client.get(
        SEARCH,
        params={
            "searchText": query,
            "type": "locations",
            "sr": "2056",
            "limit": limit,
            "origins": "address,parcel,gg25,zipcode",
        },
    )
    r.raise_for_status()
    out = []
    for res in r.json().get("results", []):
        a = res.get("attrs", {})
        if "y" not in a or "x" not in a:
            continue
        # geo.admin names LV95 easting y and northing x
        out.append(
            Place(
                label=_plain(a.get("label", "")),
                kind=a.get("origin", "place"),
                e=float(a["y"]),
                n=float(a["x"]),
            )
        )
    return out


def lv95_to_wgs84(e: float, n: float) -> tuple[float, float]:
    from pyproj import Transformer

    lon, lat = Transformer.from_crs(2056, 4326, always_xy=True).transform(e, n)
    return float(lon), float(lat)


# --------------------------------------------------------------------------- terrain


async def _latest_item(
    client: httpx.AsyncClient, collection: str, e: float, n: float
) -> dict[str, Any] | None:
    lon, lat = lv95_to_wgs84(e, n)
    d = 1e-4
    r = await client.get(
        f"{STAC}/{collection}/items",
        params={"bbox": f"{lon - d},{lat - d},{lon + d},{lat + d}", "limit": 20},
    )
    r.raise_for_status()
    items = r.json().get("features", [])
    return max(items, key=lambda f: f["id"]) if items else None


async def terrain(
    e0: float, n0: float, radius: int, client: httpx.AsyncClient, step: float = 1.0
) -> np.ndarray:
    """Altitudes (LN02, m) on a grid of `step` metres over the square anchor ± radius, rows from north
    to south, columns from west to east: grid[j, i] is at local x = -radius + i·step, z = -radius + j·step."""
    size = round(2 * radius / step) + 1
    # the 1 km tiles the square touches, the newest edition of each
    tiles = [
        (ke * 1000 + 500, kn * 1000 + 500)
        for ke in range(int((e0 - radius) // 1000), int((e0 + radius) // 1000) + 1)
        for kn in range(int((n0 - radius) // 1000), int((n0 + radius) // 1000) + 1)
    ]
    items = await asyncio.gather(
        *(_latest_item(client, "ch.swisstopo.swissalti3d", e, n) for e, n in tiles)
    )
    hrefs = []
    for it in items:
        if not it:
            continue
        for a in it["assets"].values():
            if a["href"].endswith("_0.5_2056_5728.tif"):
                hrefs.append(a["href"])
    return await asyncio.to_thread(_sample_terrain, hrefs, e0, n0, radius, step, size)


def _sample_terrain(
    hrefs: list[str], e0: float, n0: float, radius: int, step: float, size: int
) -> np.ndarray:
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject

    out = np.full((size, size), np.nan, dtype=np.float32)
    # sample points on whole metres: the pixel CENTRES of the destination grid
    dst = from_origin(e0 - radius - step / 2, n0 + radius + step / 2, step, step)
    with rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif"
    ):
        for href in hrefs:
            part = np.full((size, size), np.nan, dtype=np.float32)
            with rasterio.open(f"/vsicurl/{href}") as src:
                reproject(
                    source=rasterio.band(src, 1),
                    destination=part,
                    src_transform=src.transform,
                    src_crs=src.crs,
                    src_nodata=src.nodata,
                    dst_transform=dst,
                    dst_crs=src.crs,
                    dst_nodata=np.nan,
                    resampling=Resampling.bilinear,
                )
            out = np.where(np.isnan(out), part, out)
    if np.isnan(out).all():
        raise RuntimeError(
            "no terrain data here (swissALTI3D covers Switzerland and Liechtenstein)"
        )
    # a gap (the rim of the data): the nearest known value along the row, then the column
    if np.isnan(out).any():
        mean = float(np.nanmean(out))
        out = np.where(np.isnan(out), mean, out)
    return out


# --------------------------------------------------------------------------- aerial photo


async def aerial_photo(
    e0: float, n0: float, radius: int, client: httpx.AsyncClient, pixels: int = 4000
) -> bytes:
    """SWISSIMAGE over the square anchor ± radius, north up, as a JPEG (`pixels` on a side)."""
    r = await client.get(
        WMS,
        params={
            "SERVICE": "WMS",
            "VERSION": "1.3.0",
            "REQUEST": "GetMap",
            "LAYERS": "ch.swisstopo.swissimage",
            "STYLES": "",
            "CRS": "EPSG:2056",
            "BBOX": f"{e0 - radius},{n0 - radius},{e0 + radius},{n0 + radius}",
            "WIDTH": pixels,
            "HEIGHT": pixels,
            "FORMAT": "image/jpeg",
        },
        timeout=120,
    )
    r.raise_for_status()
    if not r.headers.get("content-type", "").startswith("image/"):
        raise RuntimeError(f"the aerial photo service answered {r.text[:200]}")
    return r.content


# --------------------------------------------------------------------------- buildings


def _region_box(e0: float, n0: float, radius: int) -> tuple[float, float, float, float]:
    lon, lat = lv95_to_wgs84(e0, n0)
    dl, dp = radius / (EARTH * math.cos(math.radians(lat))), radius / EARTH
    lo, la = math.radians(lon), math.radians(lat)
    return (lo - dl, la - dp, lo + dl, la + dp)


async def _tile_urls(
    client: httpx.AsyncClient, box: tuple[float, float, float, float]
) -> list[str]:
    """The finest content tiles of the buildings tileset that touch the box (radians)."""
    out: list[str] = []

    def hit(r: list[float]) -> bool:
        return not (r[2] < box[0] or r[0] > box[2] or r[3] < box[1] or r[1] > box[3])

    async def walk(node: dict[str, Any], base: str) -> None:
        if not hit(node["boundingVolume"]["region"]):
            return
        content = node.get("content") or {}
        uri = content.get("uri") or content.get("url")
        if uri:
            url = urllib.parse.urljoin(base, uri)
            if uri.endswith(".json"):
                r = await client.get(url)
                r.raise_for_status()
                await walk(r.json()["root"], url)
            elif not node.get("children"):
                out.append(url)
        await asyncio.gather(*(walk(k, base) for k in node.get("children", [])))

    r = await client.get(BUILDINGS)
    r.raise_for_status()
    await walk(r.json()["root"], BUILDINGS)
    return out


def _node_matrix(node: dict[str, Any]) -> np.ndarray:
    if "matrix" in node:
        return np.array(node["matrix"], dtype=np.float64).reshape(4, 4).T
    m = np.eye(4)
    x, y, z, w = node.get("rotation", [0, 0, 0, 1])
    m[:3, :3] = [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]
    m[:3, :3] = m[:3, :3] * np.array(node.get("scale", [1, 1, 1]))
    m[:3, 3] = node.get("translation", [0, 0, 0])
    return m


def decode_b3dm(
    data: bytes, e0: float, n0: float
) -> list[tuple[dict[str, Any], np.ndarray, np.ndarray]]:
    """One tile's buildings: (their attributes, vertices [local x, ellipsoidal height, local z], triangles)."""
    import DracoPy
    from pyproj import Transformer

    magic, _v, _l, ftj, ftb, btj, btb = struct.unpack("<4sIIIIII", data[:28])
    if magic != b"b3dm":
        raise ValueError("not a b3dm tile")
    p = 28
    ft = json.loads(data[p : p + ftj])
    p += ftj + ftb
    bt = json.loads(data[p : p + btj]) if btj else {}
    p += btj + btb
    glb = data[p:]
    jlen = struct.unpack("<I", glb[12:16])[0]
    gltf = json.loads(glb[20 : 20 + jlen])
    bin_start = 20 + jlen + 8
    binary = glb[bin_start:]
    rtc = np.array(ft.get("RTC_CENTER", [0, 0, 0]), dtype=np.float64)
    y_up_to_z_up = np.array(
        [[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], dtype=np.float64
    )
    to_geo = Transformer.from_crs(4978, 4979, always_xy=True)
    to_lv95 = Transformer.from_crs(4326, 2056, always_xy=True)

    verts: dict[int, list[np.ndarray]] = {}
    tris: dict[int, list[np.ndarray]] = {}
    counts: dict[int, int] = {}
    for node in gltf.get("nodes", []):
        if "mesh" not in node:
            continue
        m = y_up_to_z_up @ _node_matrix(node)
        for prim in gltf["meshes"][node["mesh"]]["primitives"]:
            ext = prim.get("extensions", {}).get("KHR_draco_mesh_compression")
            if not ext:
                continue
            bv = gltf["bufferViews"][ext["bufferView"]]
            off = bv.get("byteOffset", 0)
            mesh = DracoPy.decode(binary[off : off + bv["byteLength"]])
            pts = np.asarray(mesh.points, dtype=np.float64).reshape(-1, 3)
            faces = np.asarray(mesh.faces, dtype=np.int64).reshape(-1, 3)
            batch_attr = ext["attributes"].get("_BATCHID")
            batch = (
                np.asarray(mesh.get_attribute_by_unique_id(batch_attr)["data"])
                .reshape(-1)
                .astype(np.int64)
                if batch_attr is not None
                else np.zeros(len(pts), dtype=np.int64)
            )
            ecef = (np.c_[pts, np.ones(len(pts))] @ m.T)[:, :3] + rtc
            lon, lat, h = to_geo.transform(ecef[:, 0], ecef[:, 1], ecef[:, 2])
            e, n = to_lv95.transform(lon, lat)
            local = np.c_[np.asarray(e) - e0, np.asarray(h), -(np.asarray(n) - n0)]
            for b in np.unique(batch[faces[:, 0]]):
                f = faces[batch[faces[:, 0]] == b]
                used = np.unique(f)
                remap = np.full(len(pts), -1, dtype=np.int64)
                base = counts.get(int(b), 0)
                remap[used] = np.arange(len(used)) + base
                verts.setdefault(int(b), []).append(local[used])
                tris.setdefault(int(b), []).append(remap[f])
                counts[int(b)] = base + len(used)
    out = []
    for b, vs in verts.items():
        attrs = {k: v[b] for k, v in bt.items() if isinstance(v, list) and len(v) > b}
        out.append((attrs, np.concatenate(vs), np.concatenate(tris[b])))
    return out


async def buildings(
    e0: float,
    n0: float,
    radius: int,
    client: httpx.AsyncClient,
    ground: np.ndarray,
    step: float = 1.0,
) -> list[Building]:
    """The buildings whose centre lies within the square anchor ± radius, heights in LN02 (the
    ellipsoid-to-geoid offset is measured on the terrain: a building's lowest point stands on it)."""
    urls = await _tile_urls(client, _region_box(e0, n0, radius))
    datas = await asyncio.gather(*(client.get(u) for u in urls))
    raw: list[tuple[dict[str, Any], np.ndarray, np.ndarray]] = []
    for r in datas:
        r.raise_for_status()
        raw.extend(await asyncio.to_thread(decode_b3dm, r.content, e0, n0))
    size = ground.shape[0]

    def ground_at(x: float, z: float) -> float | None:
        i, j = round((x + radius) / step), round((z + radius) / step)
        return float(ground[j, i]) if 0 <= i < size and 0 <= j < size else None

    kept, offsets = [], []
    for attrs, v, f in raw:
        cx, cz = float(v[:, 0].mean()), float(v[:, 2].mean())
        if abs(cx) > radius or abs(cz) > radius:
            continue
        g = ground_at(cx, cz)
        if g is not None:
            offsets.append(g - float(v[:, 1].min()))
        kept.append((attrs, v, f, cx, cz))
    offset = float(np.median(offsets)) if offsets else 0.0
    out: list[Building] = []
    for attrs, v, f, _cx, _cz in kept:
        v = v.copy()
        v[:, 1] += offset
        # roofs: faces turned upwards; walls: upright; the underside is never seen
        a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
        nrm = np.cross(b - a, c - a)
        length = np.linalg.norm(nrm, axis=1) + 1e-12
        up = nrm[:, 1] / length
        # (x east, y up, z south) is right-handed like east-north-up: glTF's winding (counter-clockwise
        # seen from outside) is kept, and an outward normal's y says roof or wall
        roof = f[up > 0.25]
        wall = f[np.abs(up) <= 0.25]
        year = attrs.get("ERSTELLUNG_JAHR")
        out.append(
            Building(
                id=str(attrs.get("UUID") or attrs.get("gml:id") or len(out)),
                height=round(float(attrs.get("Height") or (v[:, 1].max() - v[:, 1].min())), 2),
                year=int(year) if isinstance(year, (int, float)) and year > 0 else None,
                positions=[round(float(x), 2) for x in v.reshape(-1)],
                roof=[int(i) for i in roof.reshape(-1)],
                wall=[int(i) for i in wall.reshape(-1)],
            )
        )
    return out
