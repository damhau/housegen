"""The lidar's neighbours before and after a change to geo/lidar.py (#73), seen from the ground.

For each project, its scene page on dev is opened twice in the presentation look with the
surroundings on: once as dev serves it, once with `context/buildings.json` replaced by a local file
(the same context built with this checkout). Each lidar house of either file gets one cell: the
aerial photo around it (the local file's outline in red, dev's in yellow), then the house from
`--distance` metres away and `--up` metres above its foot, before (dev) and after (local), from the
same viewpoint. The side the camera stands on is the one the fewest other buildings hide.

    # from backend/: a context built locally into /tmp/gilles/buildings.json, against dev's
    uv run python scripts/neighbours_sheet.py --site bb4dc4652d41 2 /tmp/gilles/buildings.json --out /tmp/n

On Modal's RTX PRO 6000 (the files go up in the scene directory's src/):
    uvx modal run deploy/modal_sheet.py --script neighbours_sheet.py --scene <dir> --out /tmp/n \\
        --args "--site bb4dc4652d41 2 {scene}/src/gilles.json --out {out}"

The pages and their files come from dev, read with the read-only token (walk_sheet.py's).
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import math
import os
import re
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from playwright.async_api import Browser, Page, Route, async_playwright
from shapely import unary_union
from shapely.geometry import LineString, Point, Polygon
from walk_sheet import DEV_BASE, GPU_ARGS, GPU_ENV, HOST_GPU, HOST_GPU_ARGS, present_token

RENDER = (640, 480)
CELL = (320, 240)
CROP = 40.0  # metres of aerial photo around a house
# the sides tried, the sunlit ones first (degrees from north, clockwise)
SIDES = (180, 135, 225, 90, 270, 45, 315, 0)


def footprint(b: dict[str, Any]) -> Any:
    """A building's ground plan: its roof triangles seen from above."""
    p = np.asarray(b["positions"], float).reshape(-1, 3)[:, [0, 2]]
    tris = [Polygon(p[t]) for t in np.asarray(b["roof"], int).reshape(-1, 3)]
    return unary_union([t.buffer(0.05) for t in tris if t.area > 1e-6])


def houses(buildings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each lidar building of a buildings.json: its footprint (local x, z), foot and top altitude."""
    out = []
    for b in buildings:
        if b.get("source") != "lidar":
            continue
        p = np.asarray(b["positions"], float).reshape(-1, 3)
        out.append(
            {
                "id": b["id"],
                "plan": footprint(b),
                "foot": float(p[:, 1].min()),
                "top": float(p[:, 1].max()),
            }
        )
    return out


def obstacles(buildings: list[dict[str, Any]]) -> list[tuple[str, Any, float]]:
    out = []
    for b in buildings:
        p = np.asarray(b["positions"], float).reshape(-1, 3)
        out.append((b["id"], footprint(b), float(p[:, 1].max())))
    return out


class Terrain:
    """context/terrain.bin: altitudes on a square grid, rows north → south."""

    def __init__(self, data: bytes, size: int, step: float) -> None:
        self.h = np.frombuffer(data, "<f4").reshape(size, size)
        self.size, self.step, self.radius = size, step, (size - 1) * step / 2

    def at(self, x: float, z: float) -> float:
        i = int(np.clip(round((x + self.radius) / self.step), 0, self.size - 1))
        j = int(np.clip(round((z + self.radius) / self.step), 0, self.size - 1))
        return float(self.h[j, i])


def viewpoint(
    house: dict[str, Any],
    others: list[tuple[str, Any, float]],
    ground: Terrain,
    trees: np.ndarray,
    args: argparse.Namespace,
) -> tuple[list[float], list[float]]:
    """Camera and target (local frame, altitudes): about `--distance` away and `--up` above the
    house's foot on the side nothing hides it from (other buildings, the terrain), else closer or
    higher."""
    c = house["plan"].centroid
    target = [c.x, house["foot"] + 0.45 * (house["top"] - house["foot"]), c.y]
    d, up = args.distance, args.up
    tries = [(d, up), (0.7 * d, up), (d, 2 * up), (0.7 * d, 2.5 * up), (0.5 * d, 3 * up)]
    best: tuple[float, list[float]] | None = None
    for rank, (deg, (dist, rise)) in enumerate((deg, t) for t in tries for deg in SIDES):
        a = math.radians(deg)
        x, z = c.x + dist * math.sin(a), c.y - dist * math.cos(a)
        cam = [x, max(house["foot"] + rise, ground.at(x, z) + 2.0), z]
        sight = LineString([(x, z), (c.x, c.y)])
        hidden = 0.0
        for name, plan, top in others:
            if name == house["id"]:
                continue
            if plan.distance(Point(x, z)) < 6 and top > cam[1] - 2:
                hidden += 0.5  # a wall beside the camera fills half the picture
            if not plan.intersects(sight):
                continue
            if plan.contains(Point(x, z)):
                hidden += 3
                continue
            near = min(sight.project(Point(q)) for q in _coords(plan.intersection(sight)))
            line_y = cam[1] + (target[1] - cam[1]) * near / max(sight.length, 1e-6)
            if top > line_y + 0.3:
                hidden += 1
        for t in np.linspace(0.05, 0.8, 16):
            px, pz = x + (c.x - x) * t, z + (c.y - z) * t
            if ground.at(px, pz) > cam[1] + (target[1] - cam[1]) * t:
                hidden += 1
                break
        if len(trees):
            # a crown across the sight line, or one the camera stands in
            seg = np.array([c.x - x, c.y - z])
            rel = trees[:, [0, 2]] - [x, z]
            t = np.clip(rel @ seg / max(float(seg @ seg), 1e-9), 0, 1)
            off = np.hypot(*(rel - t[:, None] * seg).T)
            line = cam[1] + (target[1] - cam[1]) * t
            crossing = (off < trees[:, 4]) & (trees[:, 1] + trees[:, 3] > line) & (t < 0.9)
            hidden += float(crossing.sum()) + 2 * float((np.hypot(*rel.T) < trees[:, 4] + 2).sum())
        if hidden == 0:
            return cam, target
        score = hidden + rank * 0.01
        if best is None or score < best[0]:
            best = (score, cam)
    assert best is not None
    return best[1], target


def _coords(g: Any) -> list[tuple[float, float]]:
    return [q for part in getattr(g, "geoms", [g]) for q in part.coords]


def to_scene(alignment: dict[str, Any], x: float, y: float, z: float) -> list[float]:
    """kit/context.js's local → scene transform."""
    th = math.radians(alignment.get("rotation") or 0.0)
    c, s = math.cos(th), math.sin(th)
    dx, dz = x - alignment.get("x", 0.0), z - alignment.get("z", 0.0)
    return [c * dx + s * dz, y - alignment.get("ground", 0.0), -s * dx + c * dz]


def to_local(alignment: dict[str, Any], x: float, z: float) -> tuple[float, float]:
    """kit/context.js's scene → local transform (ground plan only)."""
    th = math.radians(alignment.get("rotation") or 0.0)
    c, s = math.cos(th), math.sin(th)
    return alignment.get("x", 0.0) + c * x - s * z, alignment.get("z", 0.0) + s * x + c * z


async def fetch(page: Page, url: str) -> bytes:
    r = await page.context.request.get(url)
    if not r.ok:
        raise SystemExit(f"{url}: HTTP {r.status}")
    return await r.body()


async def open_scene(browser: Browser, url: str, local: Path | None, wait_s: int) -> Page:
    page = await browser.new_page(viewport={"width": RENDER[0], "height": RENDER[1]})
    await present_token(page, url)
    if local is not None:

        async def handle(route: Route) -> None:
            await route.fulfill(
                path=str(local),
                content_type="application/json",
                headers={"Cache-Control": "no-store"},
            )

        await page.route(re.compile(r"/context/buildings\.json(\?.*)?$"), handle)
    await page.goto(url)
    await page.wait_for_function(
        "() => window.__house && window.__house.ready", timeout=wait_s * 1000
    )
    errors = await page.evaluate("() => window.__house.errors")
    if errors:
        print(f"page errors: {errors}", flush=True)
    return page


async def shoot(page: Page, cam: list[float], target: list[float]) -> Image.Image:
    await page.evaluate("([p, t]) => window.__house.setCamera(p, t)", [cam, target])
    await page.evaluate(
        "() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))"
    )
    png = await page.screenshot(type="png")
    return Image.open(io.BytesIO(png)).convert("RGB").resize(CELL, Image.Resampling.LANCZOS)


def aerial(
    photo: Image.Image,
    radius: float,
    centre: Any,
    outlines: list[tuple[Any, tuple[int, int, int]]],
) -> Image.Image:
    px = photo.width / (2 * radius)
    x0, z0 = centre.x - CROP / 2, centre.y - CROP / 2
    box = tuple(round(v) for v in ((x0 + radius) * px, (z0 + radius) * px))
    side = round(CROP * px)
    crop = photo.crop((box[0], box[1], box[0] + side, box[1] + side)).resize(
        (CELL[1], CELL[1]), Image.Resampling.LANCZOS
    )
    draw = ImageDraw.Draw(crop)
    k = CELL[1] / CROP
    for g, colour in outlines:
        for poly in getattr(g, "geoms", [g]):
            ring = [((x - x0) * k, (z - z0) * k) for x, z in poly.exterior.coords]
            draw.line(ring, fill=colour, width=2)
    return crop


async def site(
    browser: Browser, project: str, version: int, local: Path, args: argparse.Namespace
) -> tuple[str, list[tuple[str, Image.Image, Image.Image, Image.Image]]]:
    base = f"{args.base}/scenes/{project}"
    url = (
        f"{base}/versions/{version}/index.html?look=presentation&headless=1"
        f"&w={RENDER[0]}&h={RENDER[1]}&context=/scenes/{project}/context/"
    )
    before_page = await open_scene(browser, url, None, args.timeout)
    meta = json.loads(await fetch(before_page, f"{base}/context/context.json"))
    before = json.loads(await fetch(before_page, f"{base}/context/buildings.json"))["buildings"]
    photo = Image.open(io.BytesIO(await fetch(before_page, f"{base}/context/photo.jpg")))
    photo = photo.convert("RGB")
    terrain = meta["terrain"]
    trees = np.zeros((0, 5))
    if meta.get("trees"):
        found = json.loads(await fetch(before_page, f"{base}/context/{meta['trees']['file']}"))
        rows = found.get("trees", []) if isinstance(found, dict) else found
        trees = np.asarray([r[:5] for r in rows], float).reshape(-1, 5)
    ground = Terrain(
        await fetch(before_page, f"{base}/context/{terrain['file']}"),
        int(terrain["size"]),
        float(terrain["step"]),
    )
    after = json.loads(await asyncio.to_thread(local.read_bytes))["buildings"]
    after_page = await open_scene(browser, url, local, args.timeout)
    old, new = houses(before), houses(after)
    cells = list(new)
    cells += [h for h in old if not any(h["plan"].intersects(n["plan"]) for n in new)]
    others = obstacles(after)
    align = meta["alignment"]
    frame = await before_page.evaluate("() => window.__house.frame")
    if frame:  # the project's own house hides what is behind it too
        (x0, _, z0), (x1, y1, z1) = frame["building"]["min"], frame["building"]["max"]
        corners = [to_local(align, x, z) for x, z in ((x0, z0), (x1, z0), (x1, z1), (x0, z1))]
        others.append(("scene", Polygon(corners), y1 + align.get("ground", 0.0)))
    print(
        f"{project}: {len(old)} lidar houses on dev, {len(new)} here; {len(cells)} cells",
        flush=True,
    )
    out = []
    for h in sorted(cells, key=lambda h: (h["plan"].centroid.y, h["plan"].centroid.x)):
        cam, target = viewpoint(h, others, ground, trees, args)
        cam_s, target_s = to_scene(align, *cam), to_scene(align, *target)
        a = await shoot(before_page, cam_s, target_s)
        b = await shoot(after_page, cam_s, target_s)
        near = [o["plan"] for o in old if o["plan"].distance(h["plan"]) < CROP / 2]
        mine = [n["plan"] for n in new if n["plan"].distance(h["plan"]) < CROP / 2]
        outlines = [(g, (255, 210, 0)) for g in near] + [(g, (230, 30, 30)) for g in mine]
        photo_cell = aerial(photo, meta["radius"], h["plan"].centroid, outlines)
        out.append((h["id"], photo_cell, a, b))
    await before_page.close()
    await after_page.close()
    return project, out


def sheet(
    title: str, cells: list[tuple[str, Image.Image, Image.Image, Image.Image]]
) -> Image.Image:
    font = ImageFont.load_default(size=14)
    head = 20
    w = CELL[1] + 2 * CELL[0] + 8
    row = head + CELL[1] + 6
    per_line = 2
    lines = math.ceil(len(cells) / per_line)
    img = Image.new("RGB", (per_line * w + 8, 30 + lines * row), "white")
    draw = ImageDraw.Draw(img)
    draw.text(
        (8, 6),
        f"{title}: aerial (red: after, yellow: before) | before (dev) | after (this checkout)",
        fill="black",
        font=font,
    )
    for i, (name, photo, a, b) in enumerate(cells):
        x, y = 8 + (i % per_line) * w, 30 + (i // per_line) * row
        draw.text((x, y + 2), name, fill="black", font=font)
        img.paste(photo, (x, y + head))
        img.paste(a, (x + CELL[1] + 2, y + head))
        img.paste(b, (x + CELL[1] + CELL[0] + 4, y + head))
    return img


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--site",
        nargs=3,
        action="append",
        required=True,
        metavar=("PROJECT", "VERSION", "BUILDINGS_JSON"),
        help="a project on --base, the version whose page to open, the local buildings.json",
    )
    ap.add_argument("--base", default=DEV_BASE)
    ap.add_argument("--distance", type=float, default=25.0)
    ap.add_argument("--up", type=float, default=10.0)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--chrome", default=os.environ.get("WALK_CHROME"))
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    async def run() -> list[tuple[str, list[Any]]]:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                executable_path=args.chrome or None,
                headless=True,
                args=HOST_GPU_ARGS if HOST_GPU else GPU_ARGS,
                env=dict(os.environ) if HOST_GPU else {**os.environ, **GPU_ENV},
            )
            try:
                return [await site(browser, p, int(v), Path(f), args) for p, v, f in args.site]
            finally:
                await browser.close()

    for project, cells in asyncio.run(run()):
        path = args.out / f"neighbours-{project}.jpg"
        sheet(project, cells).save(path, quality=80, optimize=True)
        print(path, f"{path.stat().st_size / 1e6:.2f} MB", flush=True)


if __name__ == "__main__":
    main()
