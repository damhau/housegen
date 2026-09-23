import asyncio, functools, sys, threading, time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from playwright.async_api import async_playwright
WWW = Path(__file__).parent / "www"
class Q(SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
async def main(out, query):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Q, directory=str(WWW)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    async with async_playwright() as pw:
        b = await pw.chromium.launch(channel="chrome", headless=True, args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        page = await b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on("console", lambda m: errs.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        await page.goto(f"http://127.0.0.1:{srv.server_address[1]}/scene/pt.html?{query}")
        t = time.time()
        while True:
            await asyncio.sleep(20)
            st = await page.evaluate("() => window.__pt || null")
            print(int(time.time() - t), "s", st, errs[:3], flush=True)
            if errs or (st and st.get("ready")):
                break
        import base64
        url = await page.evaluate("() => document.querySelector('canvas').toDataURL('image/png')")
        Path(out).write_bytes(base64.b64decode(url.split(",")[1]))
        await b.close()
asyncio.run(main(sys.argv[1], sys.argv[2]))
