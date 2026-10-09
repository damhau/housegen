"""The walk measured the way a visitor sees it (#52).

A furnished project's scene page is opened on the GPU the way the viewer opens it (presentation
look, surroundings on). Then, for each renderer:

- load: seconds to `ready`, MB transferred by type;
- rooms: each room's arrival frame (`startWalk`, settled), with its luminance p10/p50/p90 (the
  shade, the walls, the windows), the share of clipped pixels and the median saturation (white walls
  read white below about 0.10, beige above);
- frame time: median / p90 / max while turning on the spot in the first room, and during the glide;
- motion: a glide between two rooms of one flat (`__house.walkTo`): its top speed, acceleration and
  turn rate, the speed drops mid-path, the heading it arrives with;
- one contact sheet (a row per room, a column per renderer, plus reference frames when given) and
  one JSON with every number.

    # from backend/: TestVillaGille on dev, the working copy next to the v9 snapshot
    uv run python scripts/walk_sheet.py --project fa8bd00053e6 --kit dev --kit 2026-10-04-v9 \\
        --chrome <dir>/chrome/linux-145.0.7632.159/chrome-linux64/chrome --out /tmp/walk

The scene page and its assets come from dev (`--base`). The kit's modules (`/kit/*.js`) come
from THIS checkout: the working copy for `dev`, `kit/versions/<name>/` for a snapshot. So a change
is measured before it is deployed. `--local-assets` also serves the kit's other files (textures,
sky) from the checkout when it has them.

GPU only. Chrome for Testing 145, because newer Chrome lost WebGL on WSLg's d3d12 driver:
    npx @puppeteer/browsers install chrome@145.0.7632.159 --path <dir>
then --chrome <dir>/chrome/linux-145.0.7632.159/chrome-linux64/chrome (or WALK_CHROME=...). The
browser gets the WSLg GPU environment (GALLIUM_DRIVER=d3d12, ...). A page that draws in software
(SwiftShader, llvmpipe) stops the run: software WebGL loads every core for minutes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from PIL import Image, ImageDraw, ImageFont
from playwright.async_api import Browser, Page, Route, async_playwright

BACKEND = Path(__file__).resolve().parents[1]
KIT = BACKEND.parent / "kit"
DEV_BASE = "https://housegen-dev.apps.dhconsulting.ch"
# rooms a visitor does not tour: storage (cellars, garages, technical rooms) and stairs
SKIP_USES = {"storage", "stair", "technical", "garage"}
SOFTWARE_GL = ("swiftshader", "llvmpipe", "softpipe", "software")
GPU_ENV = {
    "GALLIUM_DRIVER": "d3d12",
    "MESA_D3D12_DEFAULT_ADAPTER_NAME": "NVIDIA",
    "LD_LIBRARY_PATH": "/usr/lib/wsl/lib",
}
GPU_ARGS = [
    "--use-gl=angle",
    "--use-angle=gl",
    "--enable-gpu-rasterization",
    "--ignore-gpu-blocklist",
]
KIT_MODULE = re.compile(r"^/kit/([A-Za-z0-9_-]+\.js)$")


def kit_files(name: str) -> Path:
    path = KIT if name == "dev" else KIT / "versions" / name
    if not (path / "runtime.js").exists():
        sys.exit(
            f"no renderer '{name}' in this checkout (kit/versions/<name>/runtime.js, or 'dev')"
        )
    return path


# --------------------------------------------------------------------------
# numbers from pixels and traces
# --------------------------------------------------------------------------


def _percentile(hist: list[int], q: float) -> int:
    total, acc = sum(hist), 0
    for i, n in enumerate(hist):
        acc += n
        if acc >= q * total:
            return i
    return len(hist) - 1


def frame_stats(img: Image.Image) -> dict[str, float]:
    """Luminance p10/p50/p90 (0-255), % of pixels clipped (>= 250), median saturation (0-1)."""
    rgb = img.convert("RGB")
    lum = rgb.convert("L").histogram()
    sat = rgb.convert("HSV").split()[1].histogram()
    return {
        "p10": _percentile(lum, 0.1),
        "p50": _percentile(lum, 0.5),
        "p90": _percentile(lum, 0.9),
        "clip": round(100 * sum(lum[250:]) / sum(lum), 2),
        "sat50": round(_percentile(sat, 0.5) / 255, 3),
    }


def _wrap(a: float) -> float:
    return math.atan2(math.sin(a), math.cos(a))


def frame_times(ms: list[float]) -> dict[str, float]:
    if not ms:
        return {}
    s = sorted(ms)
    return {
        "median": round(statistics.median(s), 1),
        "p90": round(s[min(len(s) - 1, int(0.9 * len(s)))], 1),
        "max": round(s[-1], 1),
        "frames": len(s),
    }


def glide_metrics(trace: list[list[float]]) -> dict[str, Any]:
    """From rows [t ms, x, z, yaw, pitch] logged at each of the walk's updates: what a visitor feels.

    The trace is resampled every 50 ms; speed, acceleration and turn rate are taken over 100 ms (the
    updates come 14-40 ms apart: differences over one of them are mostly noise). A drop is an update
    whose own step is under 70 % of the one before, between 10 % and 90 % of the path, where the
    camera already moves faster than 0.3 m/s (the old glide's stutter at each waypoint)."""
    moved = [
        i
        for i in range(1, len(trace))
        if math.hypot(trace[i][1] - trace[i - 1][1], trace[i][2] - trace[i - 1][2]) > 1e-4
        or abs(_wrap(trace[i][3] - trace[i - 1][3])) > 1e-4
    ]
    if not moved:
        return {"moved": False}
    rows = trace[moved[0] - 1 : moved[-1] + 1]
    t = [r[0] / 1000 for r in rows]
    steps = [
        math.hypot(rows[i][1] - rows[i - 1][1], rows[i][2] - rows[i - 1][2])
        for i in range(1, len(rows))
    ]
    length = sum(steps)
    v_raw = [s / max(t[i + 1] - t[i], 1e-3) for i, s in enumerate(steps)]
    drops, done = 0, 0.0
    for i in range(1, len(steps)):
        done += steps[i - 1]
        if (
            0.1 * length < done < 0.9 * length
            and v_raw[i - 1] > 0.3
            and v_raw[i] < 0.7 * v_raw[i - 1]
        ):
            drops += 1
    # distance travelled and (unwrapped) heading on a 50 ms grid
    dist = [0.0]
    for s_ in steps:
        dist.append(dist[-1] + s_)
    yaw = [rows[0][3]]
    for i in range(1, len(rows)):
        yaw.append(yaw[-1] + _wrap(rows[i][3] - rows[i - 1][3]))

    def at(series: list[float], x: float) -> float:
        k = max(
            0, min(len(t) - 2, next((i for i in range(len(t) - 1) if t[i + 1] >= x), len(t) - 2))
        )
        span = t[k + 1] - t[k]
        u = (x - t[k]) / span if span > 1e-9 else 0.0
        return series[k] + (series[k + 1] - series[k]) * max(0.0, min(1.0, u))

    grid = [t[0] + 0.05 * i for i in range(int((t[-1] - t[0]) / 0.05) + 1)]
    d = [at(dist, x) for x in grid]
    y = [at(yaw, x) for x in grid]
    v = [(d[i + 1] - d[i - 1]) / 0.1 for i in range(1, len(grid) - 1)]
    w = [(y[i + 1] - y[i - 1]) / 0.1 for i in range(1, len(grid) - 1)]
    tv = grid[1:-1]
    a = [(v[i + 2] - v[i]) / 0.1 for i in range(len(v) - 2)]
    top = max(v, default=0.0)
    gaps = [(t[i + 1] - t[i]) * 1000 for i in range(len(t) - 1)]
    return {
        "moved": True,
        "duration_s": round(t[-1] - t[0], 2),
        "length_m": round(length, 2),
        "speed_max": round(top, 2),
        # seconds from rest to 80 % of the top speed, and from the last time above it to rest
        # (0.0: full speed at once / a dead stop)
        "ramp_up_s": round(
            next((x for x, s_ in zip(tv, v, strict=True) if s_ >= 0.8 * top), t[0]) - t[0], 2
        )
        if v
        else 0,
        "ramp_down_s": round(
            t[-1]
            - next(
                (x for x, s_ in zip(reversed(tv), reversed(v), strict=True) if s_ >= 0.8 * top),
                t[-1],
            ),
            2,
        )
        if v
        else 0,
        "accel_max": round(max((abs(x) for x in a), default=0), 2),
        "turn_rate_max": round(max((abs(x) for x in w), default=0), 2),
        "speed_drops": drops,
        "arrival_yaw_deg": round(math.degrees(rows[-1][3]) % 360, 1),
        "arrival_pitch_deg": round(math.degrees(rows[-1][4]), 1),
        "update_ms": frame_times(gaps),
    }


# --------------------------------------------------------------------------
# the page
# --------------------------------------------------------------------------

SETTLE_JS = """() => ({
  e: window.__house.indoor ? Math.round(window.__house.indoor.exposure * 1000) / 1000 : null,
  fading: (window.__house.lamps?.live ?? []).some((s) => s && s.w > 0 && s.w < 1),
})"""

# times are the frames' own (the rAF timestamp, what the page's loop and the walk's clock see), not
# when this callback runs: that comes after the frame's render and jitters with it
TURN_JS = """async (n) => {
  const times = []; let last = null;
  window.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft" }));
  await new Promise((res) => {
    const f = (ts) => { if (last !== null) times.push(ts - last); last = ts; times.length < n ? requestAnimationFrame(f) : res(); };
    requestAnimationFrame(f);
  });
  window.dispatchEvent(new KeyboardEvent("keyup", { key: "ArrowLeft" }));
  return times.slice(3);
}"""

# the glide logged at each of the walk's own updates: its clock is what the motion runs on (the
# display's frames are multiples of 16.7 ms, and a callback here runs after the frame's render)
GLIDE_JS = """async ([x, z]) => {
  const w = window.__walk, out = [], update = w.update;
  w.update = function (step) {
    const r = update.call(this, step), p = this.camera.position;
    out.push([performance.now(), p.x, p.z, this.yaw, this.pitch]);
    return r;
  };
  window.__house.walkTo(x, z);
  const t0 = performance.now();
  await new Promise((res) => {
    const still = (a, b) => Math.hypot(a[1] - b[1], a[2] - b[2]) < 1e-4 && Math.abs(a[3] - b[3]) < 1e-4 && Math.abs(a[4] - b[4]) < 1e-4;
    const check = () => {
      const n = out.length, quiet = n > 30 && out.slice(-15).every((r, i, a) => i === 0 || still(r, a[i - 1]));
      if (quiet || performance.now() - t0 > 20000) res(); else requestAnimationFrame(check);
    };
    requestAnimationFrame(check);
  });
  delete w.update;
  const s = out.length ? out[0][0] : 0;
  return out.map((r) => [r[0] - s, r[1], r[2], r[3], r[4]]);
}"""

# what one frame costs at the current view: the median of n renders, each followed by a pixel read
# that waits for the GPU to finish it (CPU and GPU time in a row: an upper bound). Renders back to
# back without a read pile up in the browser's command queue and cost several times more each; the
# paced frame time is a multiple of the display's 16.7 ms.
RENDER_JS = """(n) => {
  const cv = document.querySelector("canvas"), gl = cv.getContext("webgl2") || cv.getContext("webgl"), px = new Uint8Array(4);
  const sync = () => gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
  window.__house.renderOnce();
  sync();
  const each = [];
  for (let i = 0; i < n; i++) {
    const t0 = performance.now();
    window.__house.renderOnce();
    sync();
    each.push(performance.now() - t0);
  }
  each.sort((a, b) => a - b);
  return each[n >> 1];
}"""

CENTRE_JS = """(name) => {
  const r = window.__house.rooms.find((r) => r.name === name);
  let x = 0, z = 0;
  for (const [a, b] of r.polygon) { x += a / r.polygon.length; z += b / r.polygon.length; }
  return [x, z];
}"""


async def settle(page: Page, cap: float) -> float:
    """Wait until the exposure has stopped gliding and no lamp is fading (or `cap` seconds)."""
    t0 = time.monotonic()
    await page.wait_for_timeout(500)
    last: dict[str, Any] | None = None
    still = 0
    while time.monotonic() - t0 < cap:
        s = await page.evaluate(SETTLE_JS)
        same = last is not None and (
            s["e"] == last["e"]
            or (s["e"] is not None and last["e"] is not None and abs(s["e"] - last["e"]) < 0.003)
        )
        still = still + 1 if same and not s["fading"] else 0
        if still >= 2:
            break
        last = s
        await page.wait_for_timeout(250)
    await page.wait_for_timeout(150)  # the settled frame drawn
    return round(time.monotonic() - t0, 1)


async def serve_local_kit(page: Page, kit: Path, local_assets: bool) -> None:
    async def handle(route: Route) -> None:
        path = urlsplit(route.request.url).path
        m = KIT_MODULE.match(path)
        local = kit / m.group(1) if m else None
        if (
            local is None
            and local_assets
            and not path.startswith(("/kit/vendor/", "/kit/versions/"))
        ):
            local = KIT / path.removeprefix("/kit/")
        if local is not None and local.is_file():
            ctype = "application/javascript" if local.suffix == ".js" else None
            await route.fulfill(
                path=str(local), content_type=ctype, headers={"Cache-Control": "no-store"}
            )
        else:
            await route.continue_()

    await page.route(re.compile(r"/kit/"), handle)


async def measure_kit(
    browser: Browser, url: str, kit_name: str, args: argparse.Namespace, out: Path
) -> dict[str, Any]:
    w, h = args.size
    page = await browser.new_page(viewport={"width": w, "height": h})
    result: dict[str, Any] = {"kit": kit_name, "console": []}
    page.on(
        "console",
        lambda m: (
            result["console"].append(f"{m.type}: {m.text}")
            if m.type in ("warning", "error")
            else None
        ),
    )
    await serve_local_kit(page, kit_files(kit_name), args.local_assets)
    cdp = await page.context.new_cdp_session(page)
    await cdp.send("Network.enable")
    urls: dict[str, str] = {}
    by_type: dict[str, int] = {}
    cdp.on(
        "Network.responseReceived", lambda e: urls.__setitem__(e["requestId"], e["response"]["url"])
    )

    def finished(e: dict[str, Any]) -> None:
        ext = os.path.splitext(urlsplit(urls.get(e["requestId"], "")).path)[1] or "(none)"
        by_type[ext] = by_type.get(ext, 0) + int(e.get("encodedDataLength", 0))

    cdp.on("Network.loadingFinished", finished)

    t0 = time.monotonic()
    await page.goto(url)
    await page.wait_for_function(
        "() => window.__house && window.__house.ready", timeout=args.timeout * 1000
    )
    result["ready_s"] = round(time.monotonic() - t0, 1)
    gl = str(await page.evaluate("() => window.__house.gl"))
    result["gl"] = gl
    print(f"[{kit_name}] gl: {gl}; ready in {result['ready_s']} s", flush=True)
    if any(s in gl.lower() for s in SOFTWARE_GL):
        await page.close()
        sys.exit(
            f"[{kit_name}] the page draws in software ({gl}): stopping, see the GPU notes in --help"
        )
    result["mb"] = round(sum(by_type.values()) / 1e6, 1)
    result["mb_by_type"] = {
        k: round(v / 1e6, 2) for k, v in sorted(by_type.items(), key=lambda kv: -kv[1])
    }

    all_rooms = await page.evaluate(
        "() => window.__house.rooms.map(r => ({ name: r.name, use: r.use ?? null }))"
    )
    if args.rooms:
        names = set(args.rooms)
        rooms = [r for r in all_rooms if r["name"] in names]
    else:
        rooms = [r for r in all_rooms if (r["use"] or "") not in SKIP_USES]
    result["rooms"] = []
    hide_overlay = "() => document.querySelectorAll('body > div').forEach((d) => (d.style.visibility = 'hidden'))"
    frames = out / "frames" / kit_name
    frames.mkdir(parents=True, exist_ok=True)
    for i, room in enumerate(rooms):
        t1 = time.monotonic()
        await page.evaluate("(n) => window.__house.startWalk(n)", room["name"])
        await page.evaluate(hide_overlay)
        waited = await settle(page, args.settle_first if i == 0 else args.settle)
        file = frames / f"room-{i + 1:02d}.jpg"
        await page.screenshot(path=str(file), type="jpeg", quality=88)
        info = await page.evaluate(
            "() => ({ pos: window.__house.position, stats: window.__house.stats })"
        )
        info["render_ms"] = round(await page.evaluate(RENDER_JS, 20), 1)
        st = frame_stats(Image.open(file))
        result["rooms"].append(
            {
                **room,
                "file": str(file.relative_to(out)),
                "arrive_s": round(time.monotonic() - t1, 1),
                "settle_s": waited,
                **st,
                **info,
            }
        )
        print(f"[{kit_name}] {room['name']}: {st}", flush=True)

    if rooms:
        first = rooms[0]["name"]
        await page.evaluate("(n) => window.__house.startWalk(n)", first)
        await settle(page, args.settle)
        result["frame_turning"] = frame_times(await page.evaluate(TURN_JS, 120))
        print(f"[{kit_name}] paced frame ms turning: {result['frame_turning']}", flush=True)
        # the glide: from the first room to the farthest room of the same flat on the same storey
        start, goal = args.glide.split("|") if args.glide else (first, None)
        await page.evaluate("(n) => window.__house.startWalk(n)", start)
        await settle(page, args.settle)
        if goal is None:
            debug = await page.evaluate("() => window.__house.walkDebug")
            group = next((g for g in debug["reach"] if start in g), [])
            here = await page.evaluate(CENTRE_JS, start)
            far = []
            for name in group:
                if name == start or name not in {r["name"] for r in all_rooms}:
                    continue
                c = await page.evaluate(CENTRE_JS, name)
                far.append((math.hypot(c[0] - here[0], c[1] - here[1]), name))
            goal = max(far)[1] if far else None
        if goal:
            target = await page.evaluate(CENTRE_JS, goal)
            trace = await page.evaluate(GLIDE_JS, target)
            result["glide"] = {"from": start, "to": goal, **glide_metrics(trace)}
            result["glide_trace"] = trace
            print(
                f"[{kit_name}] glide {start} -> {goal}: {json.dumps({k: v for k, v in result['glide'].items() if k not in ('from', 'to')})}",
                flush=True,
            )
    result["effects_disabled"] = any("effects disabled" in c for c in result["console"])
    result["summary"] = rooms_summary(result["rooms"])
    await page.close()
    return result


def rooms_summary(rooms: list[dict[str, Any]]) -> dict[str, float]:
    """Means over the rooms visited: the numbers two runs or two renderers are compared on."""
    if not rooms:
        return {}
    mean = lambda k: round(statistics.fmean(r[k] for r in rooms), 3)  # noqa: E731
    return {
        "rooms": len(rooms),
        "p10": mean("p10"),
        "p50": mean("p50"),
        "p90": mean("p90"),
        "clip": mean("clip"),
        "sat50": mean("sat50"),
        "render_ms": mean("render_ms"),
        "render_ms_max": max(r["render_ms"] for r in rooms),
    }


# --------------------------------------------------------------------------
# the sheet
# --------------------------------------------------------------------------


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _stats_line(s: dict[str, Any]) -> str:
    cost = f"  · {s['render_ms']} ms" if "render_ms" in s else ""
    return f"p10 {s['p10']} p50 {s['p50']} p90 {s['p90']}  clip {s['clip']}%  sat {s['sat50']:.2f}{cost}"


def write_sheet(
    results: list[dict[str, Any]], refs: list[Path], out_dir: Path, file: Path, cell_w: int
) -> None:
    columns: list[tuple[str, list[str], list[tuple[str, Path | None, dict[str, Any] | None]]]] = []
    if refs:
        cells = []
        for r in refs:
            img = Image.open(r)
            cells.append((r.stem, r, frame_stats(img)))
        columns.append(("reference", ["frames given with --reference"], cells))
    room_names: list[str] = []
    for res in results:
        for room in res["rooms"]:
            if room["name"] not in room_names:
                room_names.append(room["name"])
    for res in results:
        g = res.get("glide", {})
        ft, gf = res.get("frame_turning", {}), g.get("update_ms", {})
        head = [
            f"{res['kit']}",
            f"ready {res['ready_s']} s · {res['mb']} MB"
            + ("  · EFFECTS DISABLED" if res["effects_disabled"] else ""),
            f"render {res['summary'].get('render_ms')} ms (max {res['summary'].get('render_ms_max')}); paced {ft.get('median')} / p90 {ft.get('p90')} ms",
            f"glide {g.get('length_m')} m in {g.get('duration_s')} s, v ≤ {g.get('speed_max')} m/s, ramps {g.get('ramp_up_s')} / {g.get('ramp_down_s')} s",
            f"  accel ≤ {g.get('accel_max')} m/s², turn ≤ {g.get('turn_rate_max')} rad/s, {g.get('speed_drops')} drops, frames {gf.get('median')}/{gf.get('max')} ms",
        ]
        by_name = {r["name"]: r for r in res["rooms"]}
        cells = []
        for name in room_names:
            r = by_name.get(name)
            cells.append((name, out_dir / r["file"] if r else None, r))
        columns.append((res["kit"], head, cells))

    if len(columns) == 1 and len(columns[0][2]) > 4:
        # one renderer: its rooms in a grid of `per` columns, the header over the first
        title, head, cells = columns[0]
        per = 4
        columns = [
            (title if i == 0 else "", head if i == 0 else [""], cells[i::per]) for i in range(per)
        ]
    sample = next((p for _, _, cs in columns for _, p, _ in cs if p is not None), None)
    if sample is None:
        print("nothing to draw", file=sys.stderr)
        return
    with Image.open(sample) as im:
        cell_h = int(im.height * cell_w / im.width)
    label_h, head_h = 34, 92
    rows = max(len(cs) for _, _, cs in columns)
    board = Image.new("RGB", (len(columns) * cell_w, head_h + rows * (cell_h + label_h)), "white")
    draw = ImageDraw.Draw(board)
    f_head, f_cell = _font(13), _font(12)
    for ci, (_title, head, cells) in enumerate(columns):
        x = ci * cell_w
        for li, line in enumerate(head):
            draw.text((x + 6, 4 + li * 17), line, fill="black", font=f_head)
        for ri, (name, path, st) in enumerate(cells):
            y = head_h + ri * (cell_h + label_h)
            draw.text((x + 6, y + 2), name[:70], fill="black", font=f_cell)
            if st:
                draw.text((x + 6, y + 17), _stats_line(st), fill="#444", font=f_cell)
            if path is not None and path.exists():
                with Image.open(path) as im:
                    board.paste(
                        im.convert("RGB").resize((cell_w, cell_h), Image.LANCZOS), (x, y + label_h)
                    )
        draw.line([(x, 0), (x, board.height)], fill="#bbb")
    board.save(file, quality=86)
    print(f"wrote {file}")


# --------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    where = ap.add_mutually_exclusive_group(required=True)
    where.add_argument("--project", help="a project on --base: its scene page (current version)")
    where.add_argument("--url", help="any scene page URL (a version page, a local server...)")
    ap.add_argument(
        "--version",
        type=int,
        help="with --project: that saved version instead of the current scene",
    )
    ap.add_argument("--base", default=DEV_BASE)
    ap.add_argument(
        "--kit",
        action="append",
        help="renderer from this checkout: 'dev' (working copy) or a snapshot name; repeat to compare (default: dev)",
    )
    ap.add_argument(
        "--rooms",
        nargs="+",
        help="room names to visit (default: every room but storage and stairs)",
    )
    ap.add_argument(
        "--glide",
        help="'<from room>|<to room>' (default: the first room to the farthest room of its flat)",
    )
    ap.add_argument(
        "--reference",
        type=Path,
        action="append",
        default=[],
        help="reference frames for the sheet's first column",
    )
    ap.add_argument(
        "--local-assets",
        action="store_true",
        help="also serve the kit's non-module files from this checkout",
    )
    ap.add_argument(
        "--chrome",
        default=os.environ.get("WALK_CHROME"),
        help="Chrome for Testing 145 binary (or WALK_CHROME)",
    )
    ap.add_argument(
        "--size", default="1280x800", type=lambda s: tuple(int(v) for v in s.split("x"))
    )
    ap.add_argument(
        "--settle", type=float, default=4.0, help="max seconds for a room's light to settle"
    )
    ap.add_argument(
        "--settle-first",
        type=float,
        default=25.0,
        help="the same for the first room (storey capture, shader compiles)",
    )
    ap.add_argument(
        "--timeout", type=float, default=240.0, help="seconds to wait for the page to be ready"
    )
    ap.add_argument("--cell-width", type=int, default=480)
    ap.add_argument(
        "--out", type=Path, required=True, help="output directory (sheet, JSON, frames)"
    )
    args = ap.parse_args()
    if not args.chrome or not Path(args.chrome).is_file():
        sys.exit("no Chrome for Testing 145: pass --chrome or set WALK_CHROME (see --help)")
    kits = args.kit or ["dev"]
    for k in kits:
        kit_files(k)

    if args.url:
        url, tag = args.url, "page"
    else:
        page = f"versions/{args.version}" if args.version is not None else "scene"
        url = f"{args.base}/scenes/{args.project}/{page}/index.html"
        url += f"?look=presentation&view=southeast&context=/scenes/{args.project}/context/"
        tag = args.project
    url += (
        "&" if "?" in url else "?"
    ) + "kit=dev"  # the modules come from this checkout (see the docstring)
    args.out.mkdir(parents=True, exist_ok=True)

    async def run() -> list[dict[str, Any]]:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                executable_path=args.chrome,
                headless=True,
                args=GPU_ARGS,
                env={**os.environ, **GPU_ENV},
            )
            try:
                return [await measure_kit(browser, url, k, args, args.out) for k in kits]
            finally:
                await browser.close()

    t0 = time.monotonic()
    results = asyncio.run(run())
    report = {
        "url": url,
        "when": time.strftime("%Y-%m-%d %H:%M"),
        "size": list(args.size),
        "kits": results,
    }
    (args.out / f"walk-{tag}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    write_sheet(results, args.reference, args.out, args.out / f"walk-{tag}.jpg", args.cell_width)
    for r in results:
        g = r.get("glide", {})
        print(
            f"{r['kit']}: ready {r['ready_s']} s, {r['mb']} MB; rooms {r['summary']}; turning {r.get('frame_turning')}; "
            f"glide vmax {g.get('speed_max')} ramps {g.get('ramp_up_s')}/{g.get('ramp_down_s')} s turn {g.get('turn_rate_max')} drops {g.get('speed_drops')}"
            + ("; EFFECTS DISABLED" if r["effects_disabled"] else "")
        )
    print(f"done in {time.monotonic() - t0:.0f} s: {args.out / f'walk-{tag}.json'}")


if __name__ == "__main__":
    main()
