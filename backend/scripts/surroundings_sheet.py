"""The surroundings seen from chosen viewpoints, renderer beside renderer (#124): the neighbours' doors,
windows and roofs judged on a real project before a kit change is pushed.

A project's scene page on dev is opened the way the viewer opens it (presentation look, surroundings on),
the camera put at each `--view` (scene metres, `name=x,y,z@tx,ty,tz`), and each frame shot with what it
costs (the median of renders each followed by a pixel read, as walk_sheet.py measures it). One sheet: a row
per view, a column per renderer; a JSON with the load time, the MB and every frame's cost.

    # on Modal's RTX PRO 6000 (deploy/modal_sheet.py), v39 beside this checkout's working copy:
    uvx modal run deploy/modal_sheet.py --script surroundings_sheet.py --out /tmp/surroundings \\
        --args "--project bb4dc4652d41 --kit 2026-11-17-v39 --kit dev \\
                --view plot=0,8,20@40,3,0 --out {out}"

The kit's modules come from THIS checkout (`dev`: the working copy, else `kit/versions/<name>/`), the page and
its assets from dev, read with the read-only token: see walk_sheet.py, whose helpers this reuses.
`--buildings` serves a local buildings.json instead of dev's (a context built with this checkout's geo code,
as neighbours_sheet.py does); on Modal it goes up in the scene directory's src/:

    uvx modal run deploy/modal_sheet.py --script surroundings_sheet.py --scene <dir with src/gilles.json> \\
        --out /tmp/surroundings --args "--project bb4dc4652d41 --buildings {scene}/src/gilles.json ..."
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import statistics
import sys
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
from playwright.async_api import Browser, Page, Route, async_playwright
from walk_sheet import (
    DEV_BASE,
    GPU_ARGS,
    GPU_ENV,
    HOST_GPU,
    HOST_GPU_ARGS,
    RENDER_JS,
    SOFTWARE_GL,
    _font,
    gpu_lock,
    kit_files,
    present_token,
    serve_local_kit,
)

# what the neighbours hold once the page is ready: their meshes, the instances of their doors and windows, what
# they took to build (ms, kits from v40) and the GPU programs made so far
CONTEXT_JS = """() => {
  const s = window.__housekitState, out = { meshes: 0, instances: 0, triangles: 0 };
  s?.context?.group.traverse((o) => {
    if (!o.isMesh) return;
    out.meshes++;
    const n = o.isInstancedMesh ? o.count : 1, g = o.geometry;
    if (o.isInstancedMesh) out.instances += n;
    out.triangles += n * (g.index ? g.index.count : g.attributes.position.count) / 3;
  });
  out.built = s?.context?.built ?? null;
  out.programs = s?.renderer?.info.programs.length ?? null;
  return out;
}"""

# where the load's time went: the downloads (the surroundings' files, everything) and what follows the last one
# (the build: the neighbours, the trees, the shaders' first compile)
TIMING_JS = """() => {
  const all = performance.getEntriesByType('resource');
  const end = (list) => Math.max(0, ...list.map((e) => e.responseEnd)) / 1000;
  const ctx = all.filter((e) => e.name.includes('/context/'));
  const now = performance.now() / 1000;
  return { context_fetched_s: +end(ctx).toFixed(2), fetched_s: +end(all).toFixed(2), ready_s: +now.toFixed(2),
           after_fetch_s: +(now - end(all)).toFixed(2) };
}"""

SHOWN_JS = """async () => {
  document.querySelectorAll('body > div').forEach((d) => (d.style.visibility = 'hidden'));
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
}"""


def parse_view(text: str) -> tuple[str, list[float], list[float]]:
    name, _, spec = text.partition("=")
    pos, _, target = spec.partition("@")
    p, t = [float(v) for v in pos.split(",")], [float(v) for v in target.split(",")]
    if not name or len(p) != 3 or len(t) != 3:
        raise argparse.ArgumentTypeError(f"--view {text!r}: name=x,y,z@tx,ty,tz")
    return name, p, t


async def shoot_kit(
    browser: Browser, url: str, kit: str, args: argparse.Namespace, out: Path
) -> dict[str, Any]:
    w, h = args.size
    name, _, extra = kit.partition("?")
    if extra:
        url += "&" + extra
    page = await browser.new_page(viewport={"width": w, "height": h})
    result: dict[str, Any] = {"kit": kit, "console": [], "views": []}
    page.on(
        "console",
        lambda m: (
            result["console"].append(f"{m.type}: {m.text}")
            if m.type in ("warning", "error")
            else None
        ),
    )
    await serve_local_kit(page, kit_files(name), False)
    await serve_buildings(page, args.buildings)
    sizes: list[int] = []
    page.on("requestfinished", lambda r: asyncio.ensure_future(_size(r, sizes)))
    await present_token(page, url)
    t0 = time.monotonic()
    await page.goto(url)
    await page.wait_for_function(
        "() => window.__house && window.__house.ready", timeout=args.timeout * 1000
    )
    result["ready_s"] = round(time.monotonic() - t0, 2)
    result["timing"] = await page.evaluate(TIMING_JS)
    gl = str(await page.evaluate("() => window.__house.gl"))
    if any(s in gl.lower() for s in SOFTWARE_GL):
        await page.close()
        sys.exit(f"[{kit}] the page draws in software ({gl}): stopping")
    await page.wait_for_timeout(500)
    result["mb"] = round(sum(sizes) / 1e6, 1)
    result["context"] = await page.evaluate(CONTEXT_JS)
    # the same page loaded again: dev answers 304 to the browser's revalidations, so these loads time the
    # page's own work without the 65 MB download, whose time varies by more than the change being measured
    result["reloads_s"] = []
    for _ in range(args.loads - 1):
        t1 = time.monotonic()
        await page.reload()
        await page.wait_for_function(
            "() => window.__house && window.__house.ready", timeout=args.timeout * 1000
        )
        result["reloads_s"].append(round(time.monotonic() - t1, 2))
    if result["reloads_s"]:
        result["reload_median_s"] = round(statistics.median(result["reloads_s"]), 2)
    print(
        f"[{kit}] {gl}: ready in {result['ready_s']} s ({result['timing']}), "
        f"reloads {result['reloads_s']}, {result['context']}",
        flush=True,
    )
    frames = out / "frames" / re.sub(r"[^\w.-]+", "-", kit)
    frames.mkdir(parents=True, exist_ok=True)
    for name, pos, target in args.view:
        await page.evaluate("([p, t]) => window.__house.setCamera(p, t)", [pos, target])
        await page.evaluate(SHOWN_JS)
        await page.wait_for_timeout(args.wait)
        file = frames / f"{name}.jpg"
        await page.screenshot(path=str(file), type="jpeg", quality=90)
        ms = round(await page.evaluate(RENDER_JS, 15), 2)
        stats = await page.evaluate("() => window.__house.stats")
        result["views"].append(
            {"name": name, "file": str(file.relative_to(out)), "render_ms": ms, "stats": stats}
        )
        print(f"[{kit}] {name}: {ms} ms", flush=True)
    result["render_ms"] = (
        round(statistics.fmean(v["render_ms"] for v in result["views"]), 2)
        if result["views"]
        else None
    )
    await page.close()
    return result


async def warm_up(browser: Browser, url: str, kit: str, args: argparse.Namespace) -> None:
    """One load thrown away, so that the first renderer measured doesn't pay alone for the browser's cold
    caches (shader programs, the GPU process)."""
    page = await browser.new_page(viewport={"width": args.size[0], "height": args.size[1]})
    await serve_local_kit(page, kit_files(kit.partition("?")[0]), False)
    await serve_buildings(page, args.buildings)
    await present_token(page, url)
    t0 = time.monotonic()
    await page.goto(url)
    await page.wait_for_function(
        "() => window.__house && window.__house.ready", timeout=args.timeout * 1000
    )
    print(f"[warm-up {kit}] ready in {time.monotonic() - t0:.2f} s", flush=True)
    await page.close()


async def serve_buildings(page: Page, local: Path | None) -> None:
    if local is None:
        return

    async def handle(route: Route) -> None:
        await route.fulfill(
            path=str(local), content_type="application/json", headers={"Cache-Control": "no-store"}
        )

    await page.route(re.compile(r"/context/buildings\.json(\?.*)?$"), handle)


async def _size(request: Any, sizes: list[int]) -> None:
    # a request gone with its page counts for nothing
    with contextlib.suppress(Exception):
        s = await request.sizes()
        sizes.append(int(s.get("responseBodySize", 0)) + int(s.get("responseHeadersSize", 0)))


def write_sheet(
    results: list[dict[str, Any]], out: Path, dest: Path, cell: int, limit: int
) -> None:
    """A row per view, a column per renderer, under 'limit' bytes (JPEG quality lowered until it fits)."""
    views = [v["name"] for v in results[0]["views"]]
    first = Image.open(out / results[0]["views"][0]["file"]) if views else None
    ch = int(cell * first.height / first.width) if first else cell
    head, label = 36, 24
    sheet = Image.new("RGB", (cell * len(results), head + len(views) * (ch + label)), "white")
    draw = ImageDraw.Draw(sheet)
    font, small = _font(18), _font(14)
    for k, r in enumerate(results):
        draw.text(
            (k * cell + 8, 8),
            f"{r['kit']}  ·  ready {r['ready_s']} s  ·  {r['render_ms']} ms/frame",
            fill="black",
            font=font,
        )
        for i, v in enumerate(r["views"]):
            img = Image.open(out / v["file"]).convert("RGB").resize((cell, ch), Image.LANCZOS)
            y = head + i * (ch + label)
            draw.text(
                (k * cell + 6, y + 3),
                f"{v['name']}  ·  {v['render_ms']} ms",
                fill="black",
                font=small,
            )
            sheet.paste(img, (k * cell, y + label))
    for q in (85, 78, 70, 62, 55, 48):
        buf = BytesIO()
        sheet.save(buf, "JPEG", quality=q, optimize=True)
        if buf.tell() <= limit or q == 48:
            dest.write_bytes(buf.getvalue())
            print(f"sheet {dest} ({buf.tell() // 1024} KB, quality {q})")
            return


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--project", required=True, help="a project on --base")
    ap.add_argument("--version", type=int, help="that saved version instead of the current scene")
    ap.add_argument("--base", default=DEV_BASE)
    ap.add_argument(
        "--kit",
        action="append",
        help="'dev' or a snapshot name, a query for its page after '?' (dev?lod=0); repeat to compare",
    )
    ap.add_argument(
        "--view", action="append", type=parse_view, required=True, help="name=x,y,z@tx,ty,tz"
    )
    ap.add_argument("--query", default="", help="extra query for the scene page")
    ap.add_argument(
        "--buildings", type=Path, help="a local buildings.json served instead of the project's"
    )
    ap.add_argument(
        "--size", default="1600x1000", type=lambda s: tuple(int(v) for v in s.split("x"))
    )
    ap.add_argument("--wait", type=int, default=700, help="ms after a camera move before the shot")
    ap.add_argument("--cell-width", type=int, default=720)
    ap.add_argument("--max-kb", type=int, default=980, help="the sheet's size at most (KB)")
    ap.add_argument("--chrome", default=os.environ.get("WALK_CHROME"))
    ap.add_argument("--timeout", type=float, default=240.0)
    ap.add_argument(
        "--warm-up", type=int, default=1, help="page loads thrown away before measuring (0: none)"
    )
    ap.add_argument(
        "--loads",
        type=int,
        default=1,
        help="loads of each renderer's page; after the first, from the browser's revalidated cache",
    )
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    if not HOST_GPU and (not args.chrome or not Path(args.chrome).is_file()):
        sys.exit("no Chrome for Testing 145: pass --chrome or set WALK_CHROME (see walk_sheet.py)")
    kits = args.kit or ["dev"]
    for k in kits:
        kit_files(k.partition("?")[0])
    page = f"versions/{args.version}" if args.version is not None else "scene"
    url = (
        f"{args.base}/scenes/{args.project}/{page}/index.html?look=presentation&debug=1"
        f"&context=/scenes/{args.project}/context/&kit=dev"
    )
    if args.query:
        url += "&" + args.query.lstrip("&?")
    args.out.mkdir(parents=True, exist_ok=True)

    async def run() -> list[dict[str, Any]]:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                executable_path=args.chrome or None,
                headless=True,
                args=HOST_GPU_ARGS if HOST_GPU else GPU_ARGS,
                env=dict(os.environ) if HOST_GPU else {**os.environ, **GPU_ENV},
            )
            try:
                for _ in range(args.warm_up):
                    await warm_up(browser, url, kits[0], args)
                return [await shoot_kit(browser, url, k, args, args.out) for k in kits]
            finally:
                await browser.close()

    with gpu_lock():
        results = asyncio.run(run())
    tag = args.project + (f"-v{args.version}" if args.version is not None else "")
    report = {
        "url": url,
        "when": time.strftime("%Y-%m-%d %H:%M"),
        "size": list(args.size),
        "kits": results,
    }
    (args.out / f"surroundings-{tag}.json").write_text(
        json.dumps(report, indent=1), encoding="utf-8"
    )
    write_sheet(
        results, args.out, args.out / f"surroundings-{tag}.jpg", args.cell_width, args.max_kb * 1024
    )
    for r in results:
        print(
            f"{r['kit']}: ready {r['ready_s']} s (reloaded {r.get('reload_median_s')} s), {r['mb']} MB, "
            f"{r['render_ms']} ms/frame, {r['context']}"
        )


if __name__ == "__main__":
    main()
