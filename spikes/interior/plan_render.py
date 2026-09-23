"""Render the plan section of the spike scene to PNG with the system Chrome (SwiftShader)."""
import asyncio, functools, json, sys, threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from playwright.async_api import async_playwright

WWW = Path(__file__).parent / "www"
class Q(SimpleHTTPRequestHandler):
    def log_message(self, *a): pass

async def main(out: str, query: str) -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Q, directory=str(WWW)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    async with async_playwright() as pw:
        b = await pw.chromium.launch(channel="chrome", headless=True, args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        page = await b.new_page(viewport={"width": 2000, "height": 2000})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        await page.goto(f"http://127.0.0.1:{srv.server_address[1]}/scene/plan.html?{query}")
        try:
            await page.wait_for_function("() => window.__plan && window.__plan.ready", timeout=60000)
        except Exception:
            print("not ready", errs); raise
        info = await page.evaluate("() => window.__plan")
        await page.locator("canvas").screenshot(path=out, omit_background=True)
        print(json.dumps({"W": info["W"], "H": info["H"], "errors": errs}))
        for r in info["rooms"]:
            print(f'  {r["name"]:<18} {r["area"]:6.2f} m2')
        Path(out).with_suffix(".json").write_text(json.dumps(info["partitions"]))
        await b.close()

asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""))
