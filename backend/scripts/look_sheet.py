"""Side-by-side renders of one scene under several looks, plus luminance stats: the human gate
for any change to how the kit draws a house.

    uv run python scripts/look_sheet.py --scene data/projects/<id>/versions/<n> \
        --look quality=high --look look=presentation --look "look=ultra&samples=16" \
        --views southeast north aerial --out /tmp/sheet.png

Serves the kit and the scene directory itself (no backend needed, no database touched), renders
through the app's own headless Renderer (same browser flags, same JPEG pipeline as the version
pictures), and writes one contact sheet: a row per view, a column per look, each cell labelled
with its mean luminance and 10/50/90 percentiles (p10 = the shade, p50 = the walls, p90 = the sky).
Run it from `backend/`. With --scene omitted it renders the kit's template.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import os
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))
os.environ.setdefault("RENDER_TIMEOUT_MS", "600000")

from housegen.core.config import get_settings  # noqa: E402
from housegen.render.renderer import Renderer  # noqa: E402


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def serve(www: Path) -> tuple[ThreadingHTTPServer, int]:
    handler = functools.partial(QuietHandler, directory=str(www))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def stats(img: Image.Image) -> tuple[float, list[int]]:
    hist = img.convert("L").histogram()
    total = sum(hist)
    mean = sum(i * n for i, n in enumerate(hist)) / total
    pct, acc, targets = [], 0, [0.1, 0.5, 0.9]
    for i, n in enumerate(hist):
        acc += n
        while len(pct) < 3 and acc >= targets[len(pct)] * total:
            pct.append(i)
    return mean, pct + [255] * (3 - len(pct))


async def render_all(
    scene_url: str, looks: list[str], views: list[str], out_dir: Path
) -> dict[str, dict[str, Path]]:
    renderer = Renderer()
    images: dict[str, dict[str, Path]] = {}
    try:
        for look in looks:
            # the Renderer adds headless, quality, view and size itself; `quality=` in a look
            # overrides its default
            quality = "high"
            for part in look.split("&"):
                if part.startswith("quality="):
                    quality = part.split("=", 1)[1]
            res = await renderer.render(
                f"{scene_url}?{look}", views, out_dir / look.replace("&", "_"), quality=quality
            )
            for err in res.errors:
                print(f"  [{look}] error: {err[:200]}", file=sys.stderr)
            print(f"  {look}: {len(res.images)} views in {res.duration_ms / 1000:.0f} s")
            images[look] = res.images
    finally:
        await renderer.close()
    return images


def sheet(
    images: dict[str, dict[str, Path]], looks: list[str], views: list[str], out: Path, cell_w: int
) -> None:
    cells: dict[tuple[str, str], tuple[str, Image.Image]] = {}
    for look in looks:
        for view in views:
            path = images.get(look, {}).get(view)
            if path is None:
                continue
            img = Image.open(path).convert("RGB")
            mean, (p10, p50, p90) = stats(img)
            print(f"{look:34s} {view:14s} mean={mean:6.1f}  p10={p10:3d} p50={p50:3d} p90={p90:3d}")
            label = f"{view} · {look}   mean {mean:.0f} / p10 {p10} p50 {p50} p90 {p90}"
            cells[(look, view)] = (
                label,
                img.resize((cell_w, int(img.height * cell_w / img.width)), Image.LANCZOS),
            )
    if not cells:
        print("nothing rendered", file=sys.stderr)
        return
    cell_h = max(c[1].height for c in cells.values()) + 22
    board = Image.new("RGB", (len(looks) * cell_w, len(views) * cell_h), "white")
    draw = ImageDraw.Draw(board)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
    except OSError:
        font = ImageFont.load_default()
    for j, view in enumerate(views):
        for i, look in enumerate(looks):
            cell = cells.get((look, view))
            if cell is None:
                continue
            x, y = i * cell_w, j * cell_h
            board.paste(cell[1], (x, y + 22))
            draw.text((x + 6, y + 4), cell[0], fill="black", font=font)
    board.save(out)
    print(f"wrote {out}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--scene",
        type=Path,
        help="directory holding index.html + src/ (a version snapshot); default: the kit template",
    )
    ap.add_argument(
        "--look",
        action="append",
        required=True,
        help="query string of one look, e.g. quality=high or look=presentation&p_env=0.3",
    )
    ap.add_argument("--views", nargs="+", default=["southeast", "north", "aerial", "south-photo"])
    ap.add_argument("--out", type=Path, required=True, help="the contact sheet (PNG)")
    ap.add_argument("--cell-width", type=int, default=560)
    args = ap.parse_args()

    settings = get_settings()
    scene = (args.scene or settings.KIT_DIR / "template").resolve()
    if not (scene / "index.html").exists():
        sys.exit(f"no index.html in {scene}")
    with tempfile.TemporaryDirectory(prefix="look-sheet-") as tmp:
        www = Path(tmp)
        (www / "kit" / "vendor").mkdir(parents=True)
        for name in ("house.js", "runtime.js"):
            (www / "kit" / name).symlink_to(settings.KIT_DIR / name)
        (www / "kit" / "vendor" / "three").symlink_to(settings.KIT_DIR / "node_modules" / "three")
        (www / "scene").symlink_to(scene)
        server, port = serve(www)
        try:
            renders = Path(tmp) / "renders"
            images = asyncio.run(
                render_all(
                    f"http://127.0.0.1:{port}/scene/index.html", args.look, args.views, renders
                )
            )
            args.out.parent.mkdir(parents=True, exist_ok=True)
            sheet(images, args.look, args.views, args.out, args.cell_width)
        finally:
            server.shutdown()


if __name__ == "__main__":
    main()
