import asyncio, functools, base64, threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from playwright.async_api import async_playwright
WWW = Path(__file__).parent / "www"
class Q(SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
JS = """() => { const w = window.__walk; const c = document.createElement('canvas'); c.width = w.nx; c.height = w.nz;
 const x = c.getContext('2d'); const im = x.createImageData(w.nx, w.nz);
 for (let k = 0; k < w.nx * w.nz; k++) { const d = w.clear[k] * 0.05; const v = d === 0 ? 0 : d < w.radius ? 110 : 255;
   im.data[k*4] = v; im.data[k*4+1] = d === 0 ? 0 : d < w.radius ? 60 : 255; im.data[k*4+2] = v; im.data[k*4+3] = 255; }
 x.putImageData(im, 0, 0); return [c.toDataURL(), w.gx0, w.gz0, w.nx, w.nz]; }"""
async def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Q, directory=str(WWW)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    async with async_playwright() as pw:
        b = await pw.chromium.launch(channel="chrome", headless=True, args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        page = await b.new_page(viewport={"width": 600, "height": 400})
        await page.goto(f"http://127.0.0.1:{srv.server_address[1]}/scene/index.html?view=in-salon&walk=1&quality=low")
        await page.wait_for_function("() => window.__house && window.__house.ready", timeout=120000)
        url, x0, z0, nx, nz = await page.evaluate(JS)
        print("grid", x0, z0, nx, nz)
        (Path(__file__).parent / "walk-grid.png").write_bytes(base64.b64decode(url.split(",")[1]))
        await b.close()
asyncio.run(main())
