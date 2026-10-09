"""#42 bake spike, step 1: a project's house exported to GLB from its scene page on dev, on the GPU.

    cd backend && WALK_CHROME=<chrome 145> uv run python ../spikes/bake/export.py <project> <out_dir>

The page is headless (nothing merged, the scene as built) with a renderer that has __house.houseGroup.
Lamps the light budget hides are real lights for a path tracer; instanced foliage (Blender's importer
makes one object per card) and custom-shader meshes are left out. Writes house.glb and rooms.json
(the floor plans' rooms with their storey level, for the bake to pick a storey).
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright

KIT = os.environ.get("BAKE_KIT", "2026-10-09-v19")
ENV = {**os.environ, "GALLIUM_DRIVER": "d3d12", "MESA_D3D12_DEFAULT_ADAPTER_NAME": "NVIDIA", "LD_LIBRARY_PATH": "/usr/lib/wsl/lib"}
EXPORT = """async () => {
  const { GLTFExporter } = await import('/kit/vendor/three/examples/jsm/exporters/GLTFExporter.js');
  const root = window.__house.houseGroup, skipped = {};
  const skip = (o, why) => { o.visible = false; skipped[why] = (skipped[why] ?? 0) + 1; };
  root.traverse((o) => { if (o.isPointLight || o.isSpotLight) o.visible = true; });
  root.traverse((o) => { if (o.isMesh && (Array.isArray(o.material) ? o.material : [o.material]).some((m) => m?.isShaderMaterial)) skip(o, 'shader'); });
  root.traverse((o) => { if (o.isInstancedMesh) skip(o, 'instanced'); });
  const glb = await new GLTFExporter().parseAsync(root, { binary: true, onlyVisible: true, maxTextureSize: 512 });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([glb], { type: 'model/gltf-binary' }));
  a.download = 'house.glb';
  document.body.appendChild(a);
  a.click();
  return { bytes: glb.byteLength, skipped };
}"""


async def main(project: str, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    url = f"https://housegen-dev.apps.dhconsulting.ch/scenes/{project}/scene/index.html?headless=1&quality=medium&kit={KIT}"
    async with async_playwright() as pw:
        b = await pw.chromium.launch(executable_path=os.environ["WALK_CHROME"], headless=True, env=ENV,
                                     args=["--use-gl=angle", "--use-angle=gl", "--ignore-gpu-blocklist"])
        p = await b.new_page(viewport={"width": 800, "height": 500}, accept_downloads=True)
        await p.goto(url)
        await p.wait_for_function("() => window.__house && window.__house.ready", timeout=600_000)
        gl = await p.evaluate("() => window.__house.gl")
        assert "swiftshader" not in gl.lower(), gl
        rooms = await p.evaluate("() => window.__house.report().rooms")
        (out / "rooms.json").write_text(json.dumps(rooms, ensure_ascii=False, indent=1), encoding="utf-8")
        async with p.expect_download(timeout=600_000) as dl:
            res = await p.evaluate(EXPORT)
        await (await dl.value).save_as(str(out / "house.glb"))
        print(f"house.glb {res['bytes'] / 1e6:.1f} MB, skipped {res['skipped']}; storeys {sorted({r['y'] for r in rooms})}")
        await b.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], Path(sys.argv[2])))
