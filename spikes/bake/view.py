"""#42 bake spike, step 3: the baked storey drawn from the walk's own viewpoints, next to the live walk.

    cd backend && WALK_CHROME=<chrome 145> [LM=lightmap_dn.exr] uv run python ../spikes/bake/view.py <bake dir> <walk_sheet JSON> <out.jpg> [flip]

The viewpoints and the live frames come from a walk_sheet.py run (its rooms on the baked storey).
"""

import asyncio
import functools
import json
import os
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageDraw
from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
THREE = HERE.parents[1] / "kit" / "node_modules" / "three"
LM = os.environ.get("LM", "lightmap.exr")
ENV = {**os.environ, "GALLIUM_DRIVER": "d3d12", "MESA_D3D12_DEFAULT_ADAPTER_NAME": "NVIDIA", "LD_LIBRARY_PATH": "/usr/lib/wsl/lib"}


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a: object) -> None:
        pass


async def shoot(port, rooms, storey, out_dir, flip):
    shots = []
    async with async_playwright() as pw:
        b = await pw.chromium.launch(executable_path=os.environ["WALK_CHROME"], headless=True, env=ENV,
                                     args=["--use-gl=angle", "--use-angle=gl", "--ignore-gpu-blocklist"])
        for r in rooms:
            v = r["viewpoint"]
            p = await b.new_page(viewport={"width": 1280, "height": 800})
            p.on("console", lambda m: print("console:", m.text[:200]) if m.type == "error" else None)
            url = f"http://127.0.0.1:{port}/view.html?glb=/bake/storey.glb&lm=/bake/{LM}&at={v['at'][0]},{v['at'][1]}&yaw={v['yaw']}&floor={storey}&flip={flip}"
            await p.goto(url)
            await p.wait_for_function("() => window.__baked", timeout=300_000)
            info = await p.evaluate("() => window.__baked")
            if any(k in str(info.get("gl", "")).lower() for k in ("swiftshader", "llvmpipe", "software")):
                sys.exit(f"software GL ({info['gl']}): stopping")
            f = out_dir / f"baked-{len(shots):02d}.jpg"
            await p.screenshot(path=str(f), type="jpeg", quality=88)
            shots.append((r, f, info))
            print(r["name"], info, flush=True)
            await p.close()
        await b.close()
    return shots


def main():
    bake, walk, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    flip = sys.argv[4] if len(sys.argv) > 4 else "0"
    report = json.loads(walk.read_text())
    kit = report["kits"][0]
    storey = float(os.environ.get("STOREY", "0"))
    rooms = [r for r in kit["rooms"] if r.get("viewpoint") and abs(r["pos"][1] - 1.47 - storey) < 0.5]
    with tempfile.TemporaryDirectory() as tmp:
        www = Path(tmp)
        (www / "three").symlink_to(THREE)
        (www / "bake").symlink_to(bake)
        (www / "view.html").symlink_to(HERE / "view.html")
        server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(www)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            shots = asyncio.run(shoot(server.server_address[1], rooms, storey, out.parent, flip))
        finally:
            server.shutdown()
    W, H = 640, 400
    sheet = Image.new("RGB", (2 * W, len(shots) * (H + 20)), "white")
    d = ImageDraw.Draw(sheet)
    for i, (r, f, info) in enumerate(shots):
        y = i * (H + 20)
        d.text((4, y + 4), f"baked: {r['name']}  (exposure {info['exposure']:.2f})", fill="black")
        d.text((W + 4, y + 4), f"live walk: {r['name']}", fill="black")
        sheet.paste(Image.open(f).convert("RGB").resize((W, H)), (0, y + 20))
        live = walk.parent / r["file"]
        if live.exists():
            sheet.paste(Image.open(live).convert("RGB").resize((W, H)), (W, y + 20))
    sheet.save(out, quality=86)
    print("wrote", out)


if __name__ == "__main__":
    main()
