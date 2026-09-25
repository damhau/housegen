"""The far landscape around a place (#39): the relief out to 80 km and its aerial photos, for the horizon.

  heights   a polar grid around the anchor (AZIMUTHS directions by the RINGS radii), altitudes in m with the
            Earth's curvature taken off (as seen from the anchor, with the usual refraction), so the runtime
            draws it flat: the Terrarium elevation tiles (open, worldwide: Switzerland and its neighbours) at
            ~25 m within 12 km, ~100 m beyond; within 400 m it joins swissALTI3D (the surroundings' ground)
  photos    SWISSIMAGE over ±3 km at 2 m a pixel (mid) and ±80 km at 50 m (far): the WMS covers the
            neighbouring countries too
Same local frame as the surroundings: x east, z south, metres around the anchor.
"""

from __future__ import annotations

import asyncio
import io
import math

import httpx
import numpy as np
from PIL import Image

from housegen.geo import swiss

TERRARIUM = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
EXTENT = 80_000  # metres: the Alps seen from the Plateau are 40 to 90 km away
MID = 3_000
INNER = 170  # the first ring, under the rim of the surroundings' disc
AZIMUTHS = 512
RINGS = 190
EARTH = 6_371_000.0
REFRACTION = 0.13


def radii() -> np.ndarray:
    """Ring radii: 1 m spacing near the disc, growing geometrically to EXTENT."""
    return np.round(INNER * (EXTENT / INNER) ** (np.arange(RINGS) / (RINGS - 1)), 2)


def _tile_xy(lon: np.ndarray, lat: np.ndarray, z: int) -> tuple[np.ndarray, np.ndarray]:
    n = 2**z
    x = (lon + 180.0) / 360.0 * n
    lr = np.radians(lat)
    y = (1.0 - np.log(np.tan(lr) + 1.0 / np.cos(lr)) / math.pi) / 2.0 * n
    return x, y


async def _tiles(
    client: httpx.AsyncClient, z: int, keys: set[tuple[int, int]]
) -> dict[tuple[int, int], np.ndarray]:
    async def one(k: tuple[int, int]) -> tuple[tuple[int, int], np.ndarray]:
        r = await client.get(TERRARIUM.format(z=z, x=k[0], y=k[1]))
        r.raise_for_status()
        a = np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB")).astype(np.float32)
        return k, a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768

    return dict(await asyncio.gather(*(one(k) for k in keys)))


async def _sample(
    client: httpx.AsyncClient, lon: np.ndarray, lat: np.ndarray, z: int
) -> np.ndarray:
    """Terrarium heights at the points (bilinear within 256 px tiles)."""
    fx, fy = _tile_xy(lon, lat, z)
    px, py = fx * 256 - 0.5, fy * 256 - 0.5
    x0, y0 = np.floor(px).astype(np.int64), np.floor(py).astype(np.int64)
    keys = {
        (int(a), int(b))
        for xs in (x0, x0 + 1)
        for ys in (y0, y0 + 1)
        for a, b in zip(xs.ravel() // 256, ys.ravel() // 256, strict=True)
    }
    tiles = await _tiles(client, z, keys)

    def at(ix: np.ndarray, iy: np.ndarray) -> np.ndarray:
        out = np.empty(ix.shape, dtype=np.float32)
        for (tx, ty), t in tiles.items():
            m = (ix // 256 == tx) & (iy // 256 == ty)
            out[m] = t[iy[m] % 256, ix[m] % 256]
        return out

    u, v = px - x0, py - y0
    h00, h10, h01, h11 = at(x0, y0), at(x0 + 1, y0), at(x0, y0 + 1), at(x0 + 1, y0 + 1)
    return ((h00 * (1 - u) + h10 * u) * (1 - v) + (h01 * (1 - u) + h11 * u) * v).astype(np.float32)


async def heights(
    e0: float, n0: float, client: httpx.AsyncClient, ground: np.ndarray, radius: int, step: float
) -> np.ndarray:
    """Altitudes on the polar grid [RINGS, AZIMUTHS], curvature taken off."""
    from pyproj import Transformer

    r = radii()
    a = np.arange(AZIMUTHS) * (2 * math.pi / AZIMUTHS)
    x = r[:, None] * np.cos(a)[None, :]
    zz = r[:, None] * np.sin(a)[None, :]
    lon, lat = Transformer.from_crs(2056, 4326, always_xy=True).transform(e0 + x, n0 - zz)
    lon, lat = np.asarray(lon), np.asarray(lat)
    near = r < 12_000
    h = np.empty(x.shape, dtype=np.float32)
    h[near] = await _sample(client, lon[near], lat[near], 12)
    h[~near] = await _sample(client, lon[~near], lat[~near], 10)
    # within 400 m: the survey's ground (the disc's), easing into the tiles by 400 m
    size = ground.shape[0]
    gi = np.clip((x + radius) / step, 0, size - 1.001)
    gj = np.clip((zz + radius) / step, 0, size - 1.001)
    i0, j0 = np.floor(gi).astype(np.int64), np.floor(gj).astype(np.int64)
    fu, fv = gi - i0, gj - j0
    g = (ground[j0, i0] * (1 - fu) + ground[j0, i0 + 1] * fu) * (1 - fv) + (
        ground[j0 + 1, i0] * (1 - fu) + ground[j0 + 1, i0 + 1] * fu
    ) * fv
    w = np.clip((r - (radius - 10)) / (400 - (radius - 10)), 0, 1)[:, None]
    w = w * w * (3 - 2 * w)
    h = (g * (1 - w) + h * w).astype(np.float32)
    # the Earth's curvature, as seen from the anchor (with the atmosphere's refraction)
    h -= ((r**2) * (1 - REFRACTION) / (2 * EARTH))[:, None].astype(np.float32)
    return h.astype(np.float32)


async def photos(e0: float, n0: float, client: httpx.AsyncClient) -> tuple[bytes, bytes]:
    mid, far = await asyncio.gather(
        swiss.aerial_photo(e0, n0, MID, client, pixels=3072),
        swiss.aerial_photo(e0, n0, EXTENT, client, pixels=3072),
    )

    def jpeg(data: bytes) -> bytes:
        buf = io.BytesIO()
        Image.open(io.BytesIO(data)).convert("RGB").save(
            buf, "JPEG", quality=82, optimize=True, progressive=True
        )
        return buf.getvalue()

    return jpeg(mid), jpeg(far)
