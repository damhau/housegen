import asyncio, functools, json, threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from playwright.async_api import async_playwright
WWW = Path(__file__).parent / "www"; OUT = Path(__file__).parent
class Q(SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
async def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Q, directory=str(WWW)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    async with async_playwright() as pw:
        b = await pw.chromium.launch(channel="chrome", headless=True, args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        page = await b.new_page(viewport={"width": 900, "height": 560})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on("console", lambda m: errs.append(m.text) if m.type in ("error", "warning") else None)
        await page.goto(f"http://127.0.0.1:{srv.server_address[1]}/scene/index.html?view=in-salon&walk=1&quality=low")
        await page.wait_for_function("() => window.__house && window.__house.ready", timeout=120000)
        pos = lambda: page.evaluate("() => window.__house.position")
        dbg = await page.evaluate("() => window.__house.walkDebug")
        print("blocked doorways", dbg["blocked"])
        print("walkable areas", dbg["reach"])
        print("start", await pos(), "rooms", len(await page.evaluate("() => window.__house.rooms")))
        await page.screenshot(path=str(OUT / "walk-0.png"))
        await page.keyboard.down("w"); await page.wait_for_timeout(1500); await page.keyboard.up("w")
        print("after W (real time, slow renderer)", await pos())
        for name, frm, to in [
            ("salon -> chambre (through the hall)", "Salon", (3.2, -3.0)),
            ("salon -> sdb (hall, chambre, bathroom door)", "Salon", (1.0, -3.2)),
            ("salon -> wc (door on the salon side)", "Salon", (-0.6, 0.0)),
            ("kitchen -> hall app. 2 (no door between: walled off)", "Cuisine / séjour", (-2.0, -3.6)),
        ]:
            await page.evaluate("(n) => window.__house.jumpTo(n)", frm)
            await page.evaluate("([x, z]) => window.__house.walkTo(x, z)", list(to))
            await page.evaluate("() => window.__house.walkTick(20)")
            print(f"{name:52s} target {to} reached {await pos()}")
        await page.evaluate("() => window.__house.jumpTo('Salon')")
        await page.evaluate("() => window.__house.walkTo(3.2, -3.0)")
        await page.evaluate("() => window.__house.walkTick(20)")
        await page.evaluate("() => { window.__house.renderOnce(); }")
        await page.wait_for_timeout(500)
        await page.screenshot(path=str(OUT / "walk-2.png"))
        print("errors", errs[:5])
        await b.close()
asyncio.run(main())
