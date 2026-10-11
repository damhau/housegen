"""Spike "scene as data": edits on the data-built house, timed in the open page (GPU).

    uv run --project backend python spikes/scene-as-data/edits.py --variants <dir> --url <version page> \\
        --chrome <CfT 145> --out <dir>

Each edit changes interior.json's pieces in the page and rebuilds only them (js/edits.js), then
draws one frame; the layout audit is then run again in the same page. Pictures before and after
each edit, and edits.json with the times (ms): the edit, the frame, the audit.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from pathlib import Path

from playwright.async_api import Page, async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "scripts"))
import walk_sheet as ws

EDITS_JS = "() => import(new URL('src/edits.js', location.href).href)"


def look(at: list[float], yaw: float) -> tuple[list[float], list[float]]:
    x, y, z = at
    return [x, y, z], [x - math.sin(yaw), y - 0.12, z - math.cos(yaw)]


async def frame(page: Page) -> float:
    return await page.evaluate(
        """async () => { const t = performance.now(); window.__house.renderOnce();
           await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
           return performance.now() - t; }"""
    )


async def shot(page: Page, view: tuple[list[float], list[float]], path: Path) -> None:
    await page.evaluate("([p, t]) => window.__house.setCamera(p, t)", list(view))
    await page.wait_for_timeout(1500)
    await page.screenshot(path=str(path), type="jpeg", quality=90)


async def run(args: argparse.Namespace) -> None:
    out: Path = args.out
    data = json.loads((args.variants / "interior.json").read_text())
    pieces = data["pieces"]

    def find(pred) -> dict:
        return next(p for p in pieces if pred(p))

    towels = find(lambda p: p.get("kit") == "towelStack")
    sofa = find(lambda p: p.get("kit") == "model" and p["args"][0] == "sofa-grey-cushions")
    fixtures = [p["id"] for p in pieces if p.get("kit") == "fixture"]
    bath = look([1.31, 1.47, -2.24], 0.39)
    salon = look([-1.05, 1.47, 2.08], -2.16)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            executable_path=args.chrome,
            headless=True,
            args=ws.GPU_ARGS,
            env={**os.environ, **ws.GPU_ENV},
        )
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        await ws.serve_local_kit(page, ws.kit_files(args.kit), False)
        await ws.serve_local_src(page, args.variants / "data" / "src")
        await page.goto(args.url)
        await page.wait_for_function(
            "() => window.__house && window.__house.ready", timeout=240_000
        )
        gl = str(await page.evaluate("() => window.__house.gl"))
        if any(s in gl.lower() for s in ws.SOFTWARE_GL):
            sys.exit(f"software WebGL ({gl})")
        replay_ms = await page.evaluate("() => window.__interior.ms")
        await page.evaluate(
            "() => document.querySelectorAll('body > div').forEach((d) => (d.style.visibility = 'hidden'))"
        )
        await page.evaluate(EDITS_JS)
        audit0 = await page.evaluate(
            "async () => { const e = await import(new URL('src/edits.js', location.href).href);"
            " const t = performance.now(); const lines = await e.audit(); return { ms: performance.now() - t, lines }; }"
        )
        result: dict = {"gl": gl, "replay_ms": replay_ms, "audit_before": audit0, "edits": []}

        async def edit(name: str, js: str, arg, view, label: str) -> None:
            await shot(page, view, out / f"{label}-before.jpg")
            ms = await page.evaluate(
                "async ([js, arg]) => { const e = await import(new URL('src/edits.js', location.href).href);"
                " const t = performance.now(); await (new Function('e', 'arg', js))(e, arg); return performance.now() - t; }",
                [js, arg],
            )
            draw = await frame(page)
            a = await page.evaluate(
                "async () => { const e = await import(new URL('src/edits.js', location.href).href);"
                " const t = performance.now(); const lines = await e.audit(); return { ms: performance.now() - t, n: lines.length }; }"
            )
            await shot(page, view, out / f"{label}-after.jpg")
            row = {
                "edit": name,
                "edit_ms": round(ms, 1),
                "frame_ms": round(draw, 1),
                "audit_ms": round(a["ms"], 1),
                "audit_lines": a["n"],
            }
            result["edits"].append(row)
            print(row, flush=True)

        await edit("move the towel stack 30 cm", "e.move(arg, 0, 0.3)", towels["id"], bath, "move")
        await edit(
            "replace the sofa with sofa-modular-grey",
            "return e.swap(arg, (p) => { p.args = ['sofa-modular-grey', { width: 2.4, length: 1.0 }]; })",
            sofa["id"],
            salon,
            "replace",
        )
        await edit(
            f"all {len(fixtures)} fixtures in black",
            "return Promise.all(arg.map((id) => e.swap(id, (p) => { p.args[1] = { ...(p.args[1] || {}), finish: 'black' }; })))",
            fixtures,
            salon,
            "restyle",
        )
        await browser.close()
    (out / "edits.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
    print(
        f"replay {replay_ms:.0f} ms; audit before {audit0['ms']:.0f} ms ({len(audit0['lines'])} lines)"
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--url", required=True)
    ap.add_argument("--variants", type=Path, required=True)
    ap.add_argument("--kit", default="2026-10-11-v31")
    ap.add_argument("--chrome", default=os.environ.get("WALK_CHROME"))
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    with ws.gpu_lock():
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
