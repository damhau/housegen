"""Spike "scene as data": run the recorder variant on the GPU and write interior.json.

    uv run --project backend python spikes/scene-as-data/record.py \\
        --url https://housegen-dev.apps.dhconsulting.ch/scenes/<id>/versions/<n>/index.html \\
        --variants <out of make_variants.py> --kit 2026-10-11-v31 --chrome <CfT 145>

The scene page comes from dev, its modules from <variants>/rec/src, the kit's from this checkout.
Writes <variants>/interior.json, <variants>/data/src/interior-data.js (the same, as a module the
scene page can import), and <variants>/audit-original.json (the runtime's report, for checks.py).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "scripts"))
import walk_sheet as ws


async def run(args: argparse.Namespace) -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            executable_path=args.chrome,
            headless=True,
            args=ws.GPU_ARGS,
            env={**os.environ, **ws.GPU_ENV},
        )
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        await ws.serve_local_kit(page, ws.kit_files(args.kit), False)
        await ws.serve_local_src(page, args.variants / "rec" / "src")
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        t0 = time.monotonic()
        await page.goto(args.url)
        await page.wait_for_function(
            "() => window.__house && window.__house.ready", timeout=240_000
        )
        gl = str(await page.evaluate("() => window.__house.gl"))
        print(f"gl: {gl}; ready in {time.monotonic() - t0:.1f} s")
        if any(s in gl.lower() for s in ws.SOFTWARE_GL):
            sys.exit("software WebGL: stopping")
        rec = await page.evaluate("() => window.__interiorRecord")
        report = await page.evaluate("() => window.__house.report ? window.__house.report() : null")
        await browser.close()
    if not rec:
        sys.exit(f"no record (console errors: {errors[:5]})")
    out = args.variants
    (out / "interior.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False))
    (out / "data" / "src" / "interior-data.js").write_text(
        "export default " + json.dumps(rec, ensure_ascii=False) + ";\n"
    )
    (out / "audit-original.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    kinds: dict[str, int] = {}
    for p in rec["pieces"]:
        k = p.get("kit") or (p.get("code") or {}).get("fn") or ("light" if p.get("light") else "?")
        kinds[k] = kinds.get(k, 0) + 1
    print(
        f"pieces {len(rec['pieces'])}, unknown {len(rec['unknown'])}, floor plans {len(rec['floorPlans'])}"
    )
    print("by maker:", dict(sorted(kinds.items(), key=lambda kv: -kv[1])))
    for u in rec["unknown"][:20]:
        print("unknown:", u["name"], u["type"], u["meshes"], "meshes", u.get("spec"))
    for e in errors[:10]:
        print("console error:", e[:300])


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--url", required=True)
    ap.add_argument("--variants", type=Path, required=True)
    ap.add_argument("--kit", default="2026-10-11-v31")
    ap.add_argument("--chrome", default=os.environ.get("WALK_CHROME"))
    args = ap.parse_args()
    with ws.gpu_lock():
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
