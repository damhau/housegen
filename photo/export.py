"""A saved version's house as a GLB plus its rooms, from its scene page loaded headless on the GPU.

    python export.py <scene page url> <out dir>

Writes house.glb and rooms.json ({"style": the house style src/style.js names or null, "rooms": the floor plans'
rooms with their storey level}). Lamps the light budget
hides are real lights for a path tracer; instanced foliage (Blender's importer makes one object per card)
and custom-shader meshes are left out. The app's scene pages need a session or a token: RENDER_SERVICE_TOKEN
from the environment goes to the page's host as the app's token cookie, and to no other host.
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

ARGS = [
    "--use-gl=angle",
    "--use-angle=gl-egl",
    "--enable-gpu-rasterization",
    "--ignore-gpu-blocklist",
    "--disable-dev-shm-usage",
    "--no-sandbox",
]
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
GL_PROBE = "() => window.__house.gl"
# the scene's own file, whatever its renderer: a version drawn by a kit without styles still names the one it wants
STYLE = """async () => {
  try { return (await import(new URL('./src/style.js', document.baseURI).href)).default?.style ?? null; }
  catch { return null; }
}"""
TOKEN_COOKIE = "housegen_token"  # the app's (backend core/config.py)


def token_cookie(url: str, token: str) -> list[dict]:
    """The token as a cookie of the page's host only (a header would go to every host the page loads from)."""
    parts = urlsplit(url)
    if not token or parts.scheme not in ("http", "https"):
        return []
    origin = f"{parts.scheme}://{parts.netloc}"
    return [{"name": TOKEN_COOKIE, "value": token, "url": origin, "httpOnly": True,
             "secure": parts.scheme == "https", "sameSite": "Lax"}]


async def export(url: str, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=ARGS)
        context = await browser.new_context(viewport={"width": 800, "height": 500}, accept_downloads=True)
        await context.add_cookies(token_cookie(url, os.environ.get("RENDER_SERVICE_TOKEN", "")))
        page = await context.new_page()
        await page.goto(url, timeout=120_000)
        await page.wait_for_function("() => window.__house && window.__house.ready", timeout=600_000)
        gl = str(await page.evaluate(GL_PROBE))
        if "swiftshader" in gl.lower():
            raise RuntimeError(f"the export would draw in software: {gl}")
        rooms = await page.evaluate("async () => (await window.__house.report()).rooms")
        style = await page.evaluate(STYLE)
        (out / "rooms.json").write_text(json.dumps({"style": style, "rooms": rooms}, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
        async with page.expect_download(timeout=600_000) as dl:
            res = await page.evaluate(EXPORT)
        await (await dl.value).save_as(str(out / "house.glb"))
        await browser.close()
    return {"gl": gl, "bytes": res["bytes"], "skipped": res["skipped"], "rooms": len(rooms), "style": style}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(export(sys.argv[1], Path(sys.argv[2])))))
