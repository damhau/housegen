"""The rooms of a dev project's scene (name, use, area, polygon, storey y) and the walk's viewpoint per room.

    cd backend && WALK_CHROME=<chrome 145> uv run python ../spikes/archviz/rooms.py <project> <out.json>
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright

KIT = os.environ.get("BAKE_KIT", "2026-10-30-v34")
ENV = {**os.environ, "GALLIUM_DRIVER": "d3d12", "MESA_D3D12_DEFAULT_ADAPTER_NAME": "NVIDIA", "LD_LIBRARY_PATH": "/usr/lib/wsl/lib"}


async def main(project: str, out: Path) -> None:
    url = f"https://housegen-dev.apps.dhconsulting.ch/scenes/{project}/scene/index.html?headless=1&quality=medium&kit={KIT}"
    async with async_playwright() as pw:
        b = await pw.chromium.launch(executable_path=os.environ["WALK_CHROME"], headless=True, env=ENV,
                                     args=["--use-gl=angle", "--use-angle=gl", "--ignore-gpu-blocklist"])
        p = await b.new_page(viewport={"width": 800, "height": 500})
        await p.goto(url)
        await p.wait_for_function("() => window.__house && window.__house.ready", timeout=600_000)
        gl = await p.evaluate("() => window.__house.gl")
        assert "swiftshader" not in gl.lower(), gl
        rooms = await p.evaluate("async () => (await window.__house.report()).rooms")
        # the openings: windows and doors the kit tagged, with their boxes in scene metres
        openings = await p.evaluate("""() => {
          const out = [], THREE = window.__house.THREE;
          window.__house.houseGroup.traverse((o) => {
            const k = o.userData?.kind;
            if (k && /window|door|glaz|opening/i.test(k)) {
              const b = new (THREE?.Box3 ?? o.constructor)(); 
              out.push({ kind: k, name: o.name });
            }
          });
          return out.slice(0, 400);
        }""")
        out.write_text(json.dumps({"rooms": rooms, "openings": openings}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(len(rooms), "rooms,", len(openings), "openings")
        await b.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], Path(sys.argv[2])))
