"""Contact sheets of a bought furniture pack in kit/assets/licensed/<pack> (kit/scripts/licensed/
pack.mjs wrote it): every object in a cell with its name and size, seen three-quarters from the front, a
red stroke on the floor pointing +z (the way furnish.js wants a piece to face).

    uv run python scripts/pack_sheet.py --pack martel --out /tmp/martel   # /tmp/martel-0.png, -1.png…

Serves the kit itself (no backend needed) and draws with the app's
browser (BROWSER_CHANNEL, SwiftShader). Run it from `backend/`.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.async_api import async_playwright

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))

from housegen.core.config import get_settings  # noqa: E402


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


async def shoot(port: int, pack: str, per: int, out: str) -> list[Path]:
    s = get_settings()
    written: list[Path] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            channel=s.BROWSER_CHANNEL or None,
            headless=True,
            args=[
                "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader",
                "--ignore-gpu-blocklist",
            ],
        )
        page = await browser.new_page(viewport={"width": 1920, "height": 1200})
        page.on("console", lambda m: print("console:", m.text) if m.type == "error" else None)
        n, pages = 0, 1
        while n < pages:
            url = f"http://127.0.0.1:{port}/kit/scripts/licensed/sheet.html?pack={pack}&page={n}&per={per}"
            await page.goto(url)
            done = await page.wait_for_function("window.__sheetDone", timeout=600_000)
            pages = (await done.json_value())["pages"]
            path = Path(f"{out}-{n}.png")
            await page.locator("#grid").screenshot(path=str(path))
            written.append(path)
            print(path)
            n += 1
        await browser.close()
    return written


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--pack", required=True, help="directory under kit/assets/licensed")
    ap.add_argument("--per", type=int, default=12, help="objects per sheet")
    ap.add_argument("--out", required=True, help="output prefix: <out>-<page>.png")
    args = ap.parse_args()
    s = get_settings()
    licensed = s.KIT_DIR / "assets" / "licensed"
    if not (licensed / args.pack / "catalog.json").exists():
        sys.exit(
            f"no {licensed / args.pack / 'catalog.json'}: run kit/scripts/licensed/pack.mjs first"
        )
    with tempfile.TemporaryDirectory(prefix="pack-sheet-") as tmp:
        www = Path(tmp)
        (www / "kit" / "vendor").mkdir(parents=True)
        (www / "kit" / "assets").mkdir()
        (www / "kit" / "scripts").mkdir()
        for path in s.KIT_DIR.glob("*.js"):
            (www / "kit" / path.name).symlink_to(path)
        (www / "kit" / "scripts" / "licensed").symlink_to(s.KIT_DIR / "scripts" / "licensed")
        (www / "kit" / "assets" / "licensed").symlink_to(licensed)
        (www / "kit" / "vendor" / "three").symlink_to(s.KIT_DIR / "node_modules" / "three")
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(www))
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            asyncio.run(shoot(server.server_address[1], args.pack, args.per, args.out))
        finally:
            server.shutdown()


if __name__ == "__main__":
    main()
