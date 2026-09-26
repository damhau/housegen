"""Proof for #40: export a scene (its buildScene group, window.__exportRoot) to GLB from headless Chrome.

    cd backend && uv run python <this> <scene_dir> <out.glb>
"""

import asyncio
import functools
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.async_api import async_playwright

KIT = Path("/home/damien/code/perso/housegen/kit")

EXPORT = """
async () => {
  const { GLTFExporter } = await import('/kit/vendor/three/examples/jsm/exporters/GLTFExporter.js');
  const root = window.__exportRoot;
  const skipped = [];
  // lamps hidden by the runtime's light budget are real lights for a path tracer
  root.traverse((o) => { if (o.isPointLight || o.isSpotLight) o.visible = true; });
  // what a path tracer draws itself (the sky) or cannot use (custom shaders)
  root.traverse((o) => { if (o.isMesh && o.material?.isShaderMaterial) { o.visible = false; skipped.push(o.name || o.type); } });
  // foliage cards (tens of thousands of instances: Blender's importer makes one object of each)
  root.traverse((o) => { if (o.isInstancedMesh) { o.visible = false; skipped.push('instanced:' + (o.userData?.kind ?? o.name)); } });
  const glb = await new GLTFExporter().parseAsync(root, { binary: true, onlyVisible: true, maxTextureSize: 2048 });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([glb], { type: 'model/gltf-binary' }));
  a.download = 'scene.glb';
  document.body.appendChild(a);
  a.click();
  return { bytes: glb.byteLength, skipped };
}
"""


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a: object) -> None:
        pass


async def run(port: int, out: Path) -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
        )
        page = await browser.new_page(viewport={"width": 800, "height": 500}, accept_downloads=True)
        page.on("console", lambda m: print("console:", m.text[:200]) if m.type in ("error", "warning") else None)
        await page.goto(f"http://127.0.0.1:{port}/scene/index.html?headless=1&quality=medium")
        await page.wait_for_function("window.__house && window.__house.ready", timeout=600_000)
        async with page.expect_download(timeout=600_000) as dl:
            info = await page.evaluate(EXPORT)
        await (await dl.value).save_as(str(out))
        print("exported", info["bytes"], "bytes; skipped:", sorted(set(info["skipped"])))
        await browser.close()


def main() -> None:
    scene, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        www = Path(tmp)
        (www / "kit" / "vendor").mkdir(parents=True)
        for p in KIT.glob("*.js"):
            (www / "kit" / p.name).symlink_to(p)
        (www / "kit" / "assets").symlink_to(KIT / "assets")
        (www / "kit" / "sky").symlink_to(KIT / "sky")
        (www / "kit" / "vendor" / "three").symlink_to(KIT / "node_modules" / "three")
        (www / "scene").symlink_to(scene)
        server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(www)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            asyncio.run(run(server.server_address[1], out))
        finally:
            server.shutdown()


if __name__ == "__main__":
    main()
