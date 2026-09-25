"""Build the presentation sky (kit/sky/) from a Poly Haven HDRI (CC0).

    cd backend && uv run python ../kit/scripts/make_sky.py [hdri_id]

Writes, for the runtime's presentation look (setupPresentationSky):
  <id>_env.hdr   1k equirectangular radiance with the sun's disc taken out (the directional light
                 is the sun; the map would light the scene with it a second time), for the lighting
  <id>_sky.jpg   8k equirectangular, from the zenith to 12° below the horizon, developed for the screen
                 (1 - exp(-radiance / scale * DEVELOP), sRGB): the runtime takes the clouds from it and
                 lays them over a blue of its own (all 256 levels used: no blocks, no banding)
  sky.json       which files, the scale, the sun's direction in the HDRI, the radiance the map's
                 upper hemisphere shines onto a level surface (to calibrate against), the credit
The HDRI's columns: u = 0.5 + atan2(z, x) / 2π (three.js's equirectangular), so an azimuth (0 = north
= -z, 90 = east = +x) is at u = 0.5 + (azimuth - 90) / 360.
"""

from __future__ import annotations

import io
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

ID = sys.argv[1] if len(sys.argv) > 1 else "kloofendal_48d_partly_cloudy_puresky"
OUT = Path(__file__).resolve().parent.parent / "sky"
BELOW = 12  # degrees below the horizon kept in the visible sky (the meadow covers the rest)
DEVELOP = 12.0  # exposure of the developed picture: the blue sky mid-range, the clouds rolled off to white


def fetch(res: str) -> bytes:
    url = f"https://dl.polyhaven.org/file/ph-assets/HDRIs/hdr/{res}/{ID}_{res}.hdr"
    with urllib.request.urlopen(url, timeout=120) as r:
        return bytes(r.read())


def read_hdr(data: bytes) -> np.ndarray:
    """Radiance (RGBE) file → float32 [h, w, 3]."""
    f = io.BytesIO(data)
    while True:
        line = f.readline().strip()
        if not line:
            break
    dims = f.readline().split()  # -Y h +X w
    h, w = int(dims[1]), int(dims[3])
    out = np.zeros((h, w, 4), dtype=np.uint8)
    for y in range(h):
        head = f.read(4)
        if head[0] == 2 and head[1] == 2 and (head[2] << 8 | head[3]) == w:
            for c in range(4):  # new run-length encoding, one channel at a time
                x = 0
                while x < w:
                    n = f.read(1)[0]
                    if n > 128:
                        n -= 128
                        out[y, x : x + n, c] = f.read(1)[0]
                    else:
                        out[y, x : x + n, c] = np.frombuffer(f.read(n), dtype=np.uint8)
                    x += n
        else:  # flat
            rest = np.frombuffer(head + f.read(4 * w - 4), dtype=np.uint8).reshape(w, 4)
            out[y] = rest
    e = out[..., 3].astype(np.int32)
    scale = np.where(e > 0, np.ldexp(1.0, e - 136), 0.0).astype(np.float32)
    return out[..., :3].astype(np.float32) * scale[..., None]


def write_hdr(img: np.ndarray, path: Path) -> None:
    """float32 [h, w, 3] → flat RGBE (no run-length: three.js's RGBELoader reads both)."""
    h, w, _ = img.shape
    m = img.max(axis=2)
    e = np.where(m > 1e-32, np.ceil(np.log2(np.maximum(m, 1e-32))), -128).astype(np.int32)
    mant = np.where(m[..., None] > 1e-32, img / np.ldexp(1.0, e)[..., None] * 256.0, 0)
    rgbe = np.concatenate(
        [
            np.clip(mant, 0, 255).astype(np.uint8),
            np.clip(e + 128, 0, 255).astype(np.uint8)[..., None],
        ],
        axis=2,
    )
    head = f"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y {h} +X {w}\n".encode()
    path.write_bytes(head + rgbe.tobytes())


def lum(img: np.ndarray) -> np.ndarray:
    return 0.2126 * img[..., 0] + 0.7152 * img[..., 1] + 0.0722 * img[..., 2]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    small = read_hdr(fetch("1k"))
    big = read_hdr(fetch("8k"))
    h, w, _ = small.shape

    # the sun: the brightest spot; its disc and glare = where the radiance exceeds the brightest sky
    L = lum(small)
    sy, sx = np.unravel_index(int(np.argmax(L)), L.shape)
    u, v = (sx + 0.5) / w, (sy + 0.5) / h
    elevation = 90 - v * 180
    azimuth = ((u - 0.5) * 360 + 90) % 360
    sky_only = np.percentile(L[: h // 2], 99.0)
    hot = sky_only * 4 < L
    # replace the disc by the sky's colour around it
    ring = (~hot) & (np.hypot(*np.meshgrid(np.arange(w) - sx, np.arange(h) - sy)) < 25)
    fill = small[ring].mean(axis=0)
    env = small.copy()
    env[hot] = fill
    write_hdr(env, OUT / f"{ID}_env.hdr")

    # cosine-weighted mean radiance of the upper hemisphere (what a level surface receives / π)
    theta = (np.arange(h) + 0.5) / h * np.pi  # from the zenith
    weight = np.clip(np.cos(theta), 0, None) * np.sin(theta)
    irr = float((lum(env) * weight[:, None]).sum() / (weight.sum() * w))

    # the visible sky: 4k, zenith to BELOW° under the horizon, the sun's disc kept but clipped
    H = big.shape[0]
    rows = int(H * (90 + BELOW) / 180)
    part = big[:rows]
    Lb = lum(part)
    scale = float(np.percentile(Lb[Lb < sky_only * 4], 99.9))
    enc = 1.0 - np.exp(-np.clip(part / scale, 0, None) * DEVELOP)
    srgb = np.where(enc <= 0.0031308, enc * 12.92, 1.055 * np.power(enc, 1 / 2.4) - 0.055)
    Image.fromarray((srgb * 255 + 0.5).astype(np.uint8)).save(
        OUT / f"{ID}_sky.jpg", "JPEG", quality=88, optimize=True, progressive=True
    )

    meta = {
        "id": ID,
        "env": f"{ID}_env.hdr",
        "sky": f"{ID}_sky.jpg",
        "skyBelow": BELOW,  # the image runs from +90° to -BELOW°
        "scale": round(scale, 5),
        "develop": DEVELOP,  # pixel = 1 - exp(-radiance / scale * develop)
        "sun": {"azimuth": round(float(azimuth), 2), "elevation": round(float(elevation), 2)},
        "irradiance": round(irr, 5),
        "credit": f"Sky: “{ID}” by Greg Zaal and Jarod Guest, Poly Haven (CC0)",
    }
    (OUT / "sky.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(meta), "sun pixels removed:", int(hot.sum()))
    for f in OUT.iterdir():
        print(f.name, f.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main()
