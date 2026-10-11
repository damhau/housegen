"""The fixture catalogue: each light fixture in its booth of the showroom (kit/showroom), on the GPU.

    # from backend/: every registered family with its defaults, all lit
    uv run python scripts/fixture_sheet.py --chrome <Chrome for Testing 145> --out /tmp/fixtures
    # some families or variants (a family, then ~param=value pairs), by day (accents only)
    uv run python scripts/fixture_sheet.py --show "globePendant,globePendant~glass=clear,downlight~mount=recessed" \\
        --lamps day --chrome ... --out /tmp/fixtures

The showroom page, its scene and the kit's modules come from THIS checkout (a worktree's own kit/),
three.js from dev. The walk goes along the corridor and photographs each booth from its front,
then writes one contact sheet (fixtures.jpg: label, role, luminance p50/p90, clipped share) and the
frames. Console errors (a fixture that fails to build) are printed and listed in fixtures.json.

The GPU is shared: the run first takes an exclusive lock on /tmp/housegen-gpu.lock and waits for
it, so parallel sessions and agents render one after the other instead of loading the RTX 3050 (and
the user's PC) together. GPU only, as walk_sheet.py (Chrome for Testing 145, WSLg d3d12).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw
from playwright.async_api import Route, async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import walk_sheet as ws

SHOWROOM = ws.KIT / "showroom"
# the showroom's booth grid (kit/showroom/src/scene.js): width, partition, the camera's spot in the corridor
BOOTH_W, BOOTH_T, STAND_Z = 2.4, 0.12, 1.9


def booth_labels(show: str, families: list[str]) -> list[str]:
    items = [s for s in show.split(",") if s] if show else families
    return [" ".join([i.split("~")[0], *i.split("~")[1:]]) for i in items]


async def run(args: argparse.Namespace) -> dict:
    url = (
        f"{ws.DEV_BASE}/scenes/showroom/scene/index.html?look=presentation&kit=dev"
        f"&lamps={args.lamps}"
        + (f"&show={args.show}" if args.show else "")
        + (f"&{args.query}" if args.query else "")
    )
    out: Path = args.out
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            executable_path=args.chrome,
            headless=True,
            args=ws.GPU_ARGS,
            env={**os.environ, **ws.GPU_ENV},
        )
        page = await browser.new_page(viewport={"width": args.width, "height": args.height})
        await ws.serve_local_kit(page, ws.kit_files(args.kit), True)
        await ws.serve_local_src(page, SHOWROOM / "src")

        async def index(route: Route) -> None:
            await route.fulfill(path=str(SHOWROOM / "index.html"), content_type="text/html")

        await page.route("**/scenes/showroom/scene/index.html*", index)
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        t0 = time.monotonic()
        await page.goto(url)
        await page.wait_for_function(
            "() => window.__house && window.__house.ready", timeout=args.timeout * 1000
        )
        gl = str(await page.evaluate("() => window.__house.gl"))
        print(f"gl: {gl}; ready in {time.monotonic() - t0:.1f} s", flush=True)
        if any(s in gl.lower() for s in ws.SOFTWARE_GL):
            await browser.close()
            sys.exit(f"the page draws in software ({gl}): stopping (see walk_sheet.py --help)")
        families = await page.evaluate(
            "() => import('/kit/lighting.js').then((m) => Object.keys(m.FIXTURES))"
        )
        labels = booth_labels(args.show, families)
        mode = await page.evaluate("() => window.__house.lampMode")
        await page.evaluate("() => window.__house.startWalk('Corridor')")
        await page.evaluate(
            "() => document.querySelectorAll('body > div').forEach((d) => (d.style.visibility = 'hidden'))"
        )
        await ws.settle(page, args.settle_first)
        cells = []
        for i, label in enumerate(labels):
            x = i * (BOOTH_W + BOOTH_T) + BOOTH_W / 2
            await page.evaluate(
                "([x, z, pitch]) => window.__house.walkTo(x, z, { yaw: 0, pitch })",
                [x, STAND_Z, args.pitch],
            )
            await page.wait_for_timeout(3500 if i else 5000)  # the glide to the next booth
            await ws.settle(page, args.settle)
            await page.wait_for_timeout(700)  # the frame at rest converges
            file = out / f"booth-{i + 1:02d}.jpg"
            await page.screenshot(path=str(file), type="jpeg", quality=90)
            st = ws.frame_stats(Image.open(file))
            cells.append({"label": label, "file": str(file), "stats": st})
            print(f"{i + 1:2d} {label}: {st}", flush=True)
        await browser.close()
    result = {"gl": gl, "url": url, "lamp_mode": mode, "booths": cells, "console_errors": errors}
    (out / "fixtures.json").write_text(json.dumps(result, indent=1))
    write_sheet(cells, out / "fixtures.jpg", args.cols, args.cell_width, f"lamps={args.lamps}")
    for e in errors[:20]:
        print("console error:", e[:300])
    return result


def write_sheet(cells: list[dict], path: Path, cols: int, cw: int, title: str) -> None:
    if not cells:
        return
    first = Image.open(cells[0]["file"])
    ch = round(cw * first.height / first.width)
    head, rows = 34, (len(cells) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cw, 30 + rows * (ch + head)), "white")
    d = ImageDraw.Draw(sheet)
    d.text((8, 6), f"fixture showroom, {title}", fill="black", font=ws._font(18))
    for i, c in enumerate(cells):
        x, y = (i % cols) * cw, 30 + (i // cols) * (ch + head)
        s = c["stats"]
        d.text((x + 6, y + 2), c["label"], fill="black", font=ws._font(15))
        d.text(
            (x + 6, y + 18),
            f"p50 {s['p50']} p90 {s['p90']} clip {s['clip']}%",
            fill="#555",
            font=ws._font(13),
        )
        sheet.paste(Image.open(c["file"]).resize((cw, ch), Image.LANCZOS), (x, y + head))
    sheet.save(path, quality=88)
    print(f"wrote {path}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--show", default="", help="fixtures to show (default: every family)")
    ap.add_argument("--lamps", default="all", choices=["all", "day", "off"])
    ap.add_argument(
        "--query",
        default="in_fill=0.1&in_lamps=1",
        help="extra page query; the default is an evening (little fill, the lamps at full strength)",
    )
    ap.add_argument("--kit", default="dev", help="'dev' (this checkout's kit/) or a snapshot name")
    ap.add_argument("--chrome", default=os.environ.get("WALK_CHROME"))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--cols", type=int, default=3)
    ap.add_argument("--cell-width", type=int, default=520)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=800)
    ap.add_argument("--pitch", type=float, default=0.0, help="radians, positive looks up")
    ap.add_argument("--settle", type=float, default=4.0)
    ap.add_argument("--settle-first", type=float, default=25.0)
    ap.add_argument("--timeout", type=float, default=240.0)
    args = ap.parse_args()
    if not args.chrome or not Path(args.chrome).is_file():
        sys.exit(
            "no Chrome for Testing 145: pass --chrome or set WALK_CHROME (see walk_sheet.py --help)"
        )
    ws.kit_files(args.kit)
    args.out.mkdir(parents=True, exist_ok=True)
    with ws.gpu_lock():
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
