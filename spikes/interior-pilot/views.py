"""Close-up views of the pilot rooms (#interior pilot): the walk glides to each spot and looks there.

    # from backend/
    uv run python ../spikes/interior-pilot/views.py --src <scene src dir> --out <dir> \\
        --chrome <Chrome for Testing 145> [--kit 2026-10-11-v30]

Same page, GPU set-up and local overrides as scripts/walk_sheet.py (it is imported).
A view: room, spot [x, z], the point looked at [x, z], pitch (radians, negative = down).
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "scripts"))
import walk_sheet as ws  # noqa: E402

VIEWS = [
    ("bath-vanity", "SDB — Appartement 1", (1.55, -2.45), (0.125, -3.1), -0.22),
    ("bath-wc", "SDB — Appartement 1", (1.5, -3.5), (0.125, -2.7), -0.25),
    ("bed", "Chambre 1 — Appartement 1", (3.2, -1.45), (3.64, -4.6), -0.12),
]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="fa8bd00053e6")
    ap.add_argument("--kit", default="2026-10-11-v30")
    ap.add_argument("--src", type=Path)
    ap.add_argument("--chrome", required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    url = (
        f"{ws.DEV_BASE}/scenes/{args.project}/scene/index.html?look=presentation&view=southeast"
        f"&context=/scenes/{args.project}/context/&kit=dev"
    )
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            executable_path=args.chrome, args=ws.GPU_ARGS, env={**os.environ, **ws.GPU_ENV}, headless=True
        )
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        await ws.serve_local_kit(page, ws.kit_files(args.kit), True)
        if args.src:
            await ws.serve_local_src(page, args.src)
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        await page.goto(url)
        await page.wait_for_function("() => window.__house && window.__house.ready", timeout=240_000)
        gl = str(await page.evaluate("() => window.__house.gl"))
        if any(s in gl.lower() for s in ws.SOFTWARE_GL):
            sys.exit(f"software WebGL ({gl}): stopping")
        print("gl:", gl, flush=True)
        current = None
        for name, room, (x, z), (lx, lz), pitch in VIEWS:
            if room != current:
                await page.evaluate("(n) => window.__house.startWalk(n)", room)
                await ws.settle(page, 25)
                current = room
            yaw = math.atan2(-(lx - x), -(lz - z))
            await page.evaluate(
                "([x, z, yaw, pitch]) => window.__house.walkTo(x, z, { yaw, pitch })",
                [x, z, yaw, pitch],
            )
            await page.wait_for_timeout(6500)  # the glide (at most ~6 s in a room)
            await page.evaluate(
                "() => document.querySelectorAll('body > div').forEach((d) => (d.style.visibility = 'hidden'))"
            )
            await ws.settle(page, 6)
            await page.wait_for_timeout(800)  # the frame at rest converges
            await page.screenshot(path=str(args.out / f"{name}.jpg"), type="jpeg", quality=90)
            pos = await page.evaluate("() => window.__house.position")
            print(name, "at", pos, flush=True)
        for e in errors[:10]:
            print("console error:", e[:300])
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
