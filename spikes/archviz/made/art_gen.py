"""Made-here wall art for the photo pipeline's library: prints in the styles home staging uses, drawn with numpy (no
AI, no downloads). Each style comes in a few palettes and shapes; two prints of one style and palette make a pair.

    uv run --no-project --with numpy --with pillow python made/art_gen.py [sheet]  -> made/art/gen-*.jpg,
        made/art/gen.json (and work/art/gen-sheet.jpg with `sheet`)

Styles: organic (soft shapes and an arc), arches, field (soft colour fields), rings (thin circles and a disc), leaves
(a branch), sun (a disc over bands), bauhaus (shapes on a grid), hills (layered ridges), strokes (broad brush strokes).
"""

import json
import math
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageStat

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "art")
LONG = 1280

PAPER = {"warm": (0.93, 0.91, 0.87), "cool": (0.92, 0.92, 0.90), "white": (0.95, 0.95, 0.94)}
PALETTES = {  # muted, the colours of the textiles the styling uses (sage, terracotta, sand, charcoal, slate)
    "earth": [(0.72, 0.52, 0.40), (0.55, 0.60, 0.50), (0.84, 0.76, 0.62), (0.36, 0.36, 0.33), (0.78, 0.66, 0.55)],
    "sage": [(0.55, 0.62, 0.52), (0.72, 0.76, 0.66), (0.40, 0.46, 0.40), (0.86, 0.83, 0.74), (0.62, 0.58, 0.48)],
    "slate": [(0.33, 0.40, 0.48), (0.58, 0.66, 0.72), (0.80, 0.78, 0.72), (0.22, 0.26, 0.30), (0.70, 0.62, 0.52)],
    "ochre": [(0.80, 0.62, 0.32), (0.50, 0.52, 0.36), (0.88, 0.80, 0.64), (0.30, 0.28, 0.24), (0.66, 0.42, 0.30)],
    "blush": [(0.82, 0.64, 0.58), (0.64, 0.40, 0.33), (0.90, 0.82, 0.74), (0.44, 0.38, 0.36), (0.74, 0.70, 0.62)],
    "mono": [(0.30, 0.30, 0.29), (0.58, 0.57, 0.55), (0.80, 0.79, 0.76), (0.15, 0.15, 0.15), (0.68, 0.66, 0.62)],
}
ASPECTS = {"portrait": (3, 4), "tall": (2, 3), "square": (1, 1), "landscape": (4, 3), "wide": (3, 2)}

# (style, palette, aspect, seeds, paper): two seeds make a pair
PLAN = [
    ("organic", "earth", "portrait", (3, 8), "warm"), ("organic", "sage", "portrait", (21, 22), "warm"),
    ("organic", "blush", "landscape", (31,), "warm"),
    ("arches", "earth", "portrait", (1, 2), "warm"), ("arches", "slate", "square", (3,), "cool"),
    ("arches", "ochre", "tall", (4,), "warm"),
    ("field", "slate", "portrait", (5, 6), "cool"), ("field", "ochre", "tall", (7,), "warm"),
    ("field", "sage", "landscape", (8,), "warm"),
    ("rings", "mono", "portrait", (9, 10), "white"), ("rings", "earth", "square", (11,), "warm"),
    ("leaves", "sage", "portrait", (12, 13), "warm"), ("leaves", "mono", "tall", (14, 15), "white"),
    ("leaves", "earth", "square", (16,), "warm"),
    ("sun", "earth", "portrait", (17, 18), "warm"), ("sun", "slate", "landscape", (19,), "cool"),
    ("bauhaus", "ochre", "square", (20, 23), "warm"), ("bauhaus", "slate", "portrait", (24,), "cool"),
    ("hills", "slate", "wide", (25,), "cool"), ("hills", "sage", "landscape", (26, 27), "warm"),
    ("hills", "mono", "wide", (28,), "white"),
    ("strokes", "mono", "portrait", (29, 30), "white"), ("strokes", "earth", "landscape", (32,), "warm"),
]


def canvas(aspect, paper):
    a, b = ASPECTS[aspect]
    W, H = (round(LONG * a / b), LONG) if a < b else (LONG, round(LONG * b / a))
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    return np.ones((H, W, 3), np.float32) * np.array(PAPER[paper], np.float32), x, y, W, H


def lay(img, m, col, alpha=0.92):
    m = np.clip(m, 0, 1)[..., None] * alpha
    return img * (1 - m) + np.array(col, np.float32) * m


def soft(d, edge=1.5):
    """Coverage from a signed distance in pixels (negative inside), with a soft anti-aliased edge."""
    return np.clip(0.5 - d / edge, 0, 1)


def stroke_path(img, pts, width, col, alpha=0.9):
    """A line along points (x, y), `width` px, drawn as a mask with PIL at 2x then softened."""
    H, W = img.shape[:2]
    mk = Image.new("L", (W * 2, H * 2), 0)
    ImageDraw.Draw(mk).line([(2 * px, 2 * py) for px, py in pts], fill=255, width=max(2, int(2 * width)), joint="curve")
    m = np.asarray(mk.resize((W, H), Image.Resampling.LANCZOS), np.float32) / 255
    return lay(img, m, col, alpha)


def organic(img, x, y, W, H, pal, rng):
    def blob(cx, cy, rx, ry, wob, ph):
        a = np.arctan2(y - cy, x - cx)
        r = np.hypot((x - cx) / rx, (y - cy) / ry)
        return (1 + wob * np.sin(3 * a + ph) + 0.5 * wob * np.sin(5 * a + 2 * ph) - r) * 40
    for k, col in enumerate(rng.permutation(len(pal))[:4]):
        cx, cy = rng.uniform(0.25, 0.75) * W, (0.2 + 0.2 * k + rng.uniform(-0.05, 0.05)) * H
        img = lay(img, np.clip(blob(cx, cy, rng.uniform(0.18, 0.3) * W, rng.uniform(0.1, 0.16) * H,
                                    rng.uniform(0.06, 0.14), rng.uniform(0, 6)), 0, 1), pal[col])
    t = np.linspace(0, math.pi * rng.uniform(0.9, 1.4), 600) + rng.uniform(0, 6)
    pts = list(zip(W * 0.5 + W * 0.32 * np.cos(t), H * 0.55 + H * 0.3 * np.sin(t)))
    return stroke_path(img, pts, 0.005 * W, (0.2, 0.2, 0.2))


def arches(img, x, y, W, H, pal, rng):
    cx, base = W * rng.uniform(0.42, 0.58), H * rng.uniform(0.72, 0.8)
    r0, n = min(W, H) * rng.uniform(0.34, 0.42), rng.integers(4, 6)
    cols = [pal[i] for i in rng.permutation(len(pal))[:n]]
    d = np.hypot(x - cx, y - base)
    band = r0 / (n + 0.6)
    for i, col in enumerate(cols):
        ro, ri = r0 - i * band, r0 - (i + 1) * band + band * 0.12
        m = soft(d - ro) * (1 - soft(d - max(ri, 0))) * (y <= base)
        img = lay(img, m, col)
    return lay(img, soft(np.abs(y - base) - H * 0.002) * (np.abs(x - cx) < r0 * 1.15), (0.25, 0.25, 0.24), 0.8)


def field(img, x, y, W, H, pal, rng):
    n = rng.integers(2, 4)
    cols = [pal[i] for i in rng.permutation([0, 1, 3, 4])[:n]]  # not pal[2], the shade closest to the paper
    tops = np.sort(rng.uniform(0.08, 0.9, n - 1))
    edges = [0.08, *tops, 0.92]
    noise = np.random.default_rng(int(rng.integers(1 << 30))).normal(0, 1, (H // 16 + 1, W // 16 + 1)).astype(np.float32)
    nz = np.asarray(Image.fromarray(noise).resize((W, H), Image.Resampling.BICUBIC), np.float32)
    for i, col in enumerate(cols):
        a, b = edges[i] * H + 6, edges[i + 1] * H - 6
        m = soft(-(np.minimum(y - a, b - y) + 6 * nz), 14) * soft(-(np.minimum(x - 0.1 * W, 0.9 * W - x) + 6 * nz), 14)
        img = lay(img, m, col, 0.85)
    return img


def rings(img, x, y, W, H, pal, rng):
    """Two or three thin overlapping circles, one solid disc, a short rule: a drawn, quiet composition."""
    ink = (0.2, 0.2, 0.2) if pal[3][0] > 0.4 else pal[3]
    m = min(W, H)
    for _ in range(int(rng.integers(2, 4))):
        cx, cy, r = W * rng.uniform(0.32, 0.68), H * rng.uniform(0.3, 0.62), m * rng.uniform(0.16, 0.3)
        img = lay(img, soft(np.abs(np.hypot(x - cx, y - cy) - r) - m * 0.0022), ink, 0.85)
    cx, cy, r = W * rng.uniform(0.3, 0.7), H * rng.uniform(0.3, 0.6), m * rng.uniform(0.07, 0.12)
    img = lay(img, soft(np.hypot(x - cx, y - cy) - r), pal[int(rng.integers(0, 2))], 0.9)
    yy = H * rng.uniform(0.74, 0.8)
    return lay(img, soft(np.abs(y - yy) - m * 0.002) * (np.abs(x - W / 2) < W * 0.22), ink, 0.8)


def leaves(img, x, y, W, H, pal, rng):
    col, vein = pal[int(rng.integers(0, 3))], pal[3]
    t = np.linspace(0, 1, 400)
    bend = rng.uniform(-0.25, 0.25)
    sx = W * (0.5 + bend * np.sin(math.pi * t) * 0.6)
    sy = H * (0.9 - 0.8 * t)
    img = stroke_path(img, list(zip(sx, sy)), 0.004 * W, vein, 0.8)
    n = rng.integers(7, 11)
    for i in range(n):
        u = 0.12 + 0.82 * i / n
        j = min(int(u * 399), 398)
        bx, by = sx[j], sy[j]
        side = 1 if i % 2 else -1
        ang = math.atan2(sy[j + 1] - sy[j], sx[j + 1] - sx[j]) + side * rng.uniform(0.7, 1.1)
        L, Wd = min(W, H) * rng.uniform(0.16, 0.24) * (1.1 - 0.4 * u), rng.uniform(0.22, 0.32)
        ex, ey = bx + L * math.cos(ang), by + L * math.sin(ang)
        cx, cy = (bx + ex) / 2, (by + ey) / 2
        ca, sa = math.cos(ang), math.sin(ang)
        u_, v_ = (x - cx) * ca + (y - cy) * sa, -(x - cx) * sa + (y - cy) * ca
        d = (np.hypot(u_ / (L / 2), v_ / (L / 2 * Wd)) - 1) * L * Wd / 2
        img = lay(img, soft(d), col, 0.9)
        img = stroke_path(img, [(bx, by), (ex, ey)], 0.0018 * W, vein, 0.35)
    return img


def sun(img, x, y, W, H, pal, rng):
    hz = H * rng.uniform(0.58, 0.68)
    cx, cy, r = W * rng.uniform(0.38, 0.62), hz - H * rng.uniform(0.02, 0.12), min(W, H) * rng.uniform(0.2, 0.27)
    img = lay(img, soft(np.hypot(x - cx, y - cy) - r) * (y < hz), pal[0])
    for i, f in enumerate((0.0, rng.uniform(0.08, 0.14), rng.uniform(0.2, 0.26))):
        a = hz + f * H
        wave = H * 0.012 * np.sin(x / W * math.pi * rng.uniform(1, 3) + rng.uniform(0, 6))
        img = lay(img, soft(a + wave - y), [pal[1], pal[2], pal[4]][i])
    return img


def bauhaus(img, x, y, W, H, pal, rng):
    n = 3
    cw, ch = W / n * 0.8, H / n * 0.8
    for i in range(n):
        for j in range(n):
            if rng.random() < 0.18:
                continue
            cx, cy = W * (0.1 + (i + 0.5) * 0.8 / n), H * (0.1 + (j + 0.5) * 0.8 / n)
            col = pal[int(rng.integers(0, len(pal)))]
            kind = rng.integers(0, 4)
            r = min(cw, ch) * 0.42
            if kind == 0:
                m = soft(np.hypot(x - cx, y - cy) - r)
            elif kind == 1:
                m = soft(np.maximum(np.abs(x - cx), np.abs(y - cy)) - r)
            elif kind == 2:
                rot = rng.integers(0, 4) * math.pi / 2
                m = soft(np.hypot(x - cx, y - cy) - r) * (((x - cx) * math.cos(rot) + (y - cy) * math.sin(rot)) > 0)
            else:
                m = soft(np.abs(y - cy) - r * 0.18) * (np.abs(x - cx) < r)
            img = lay(img, m, col)
    return img


def hills(img, x, y, W, H, pal, rng):
    n = 4
    cols = [pal[2], pal[1], pal[0], pal[3]]
    for i in range(n):
        base = H * (0.42 + 0.13 * i)
        f1, f2, p1, p2 = rng.uniform(1, 2.5), rng.uniform(3, 6), rng.uniform(0, 6), rng.uniform(0, 6)
        ridge = base - H * (0.09 - 0.012 * i) * (np.sin(x / W * math.pi * f1 + p1) + 0.35 * np.sin(x / W * math.pi * f2 + p2))
        img = lay(img, soft(ridge - y, 2.0), cols[i], 0.95)
    cx, cy = W * rng.uniform(0.6, 0.8), H * rng.uniform(0.18, 0.28)
    return lay(img, soft(np.hypot(x - cx, y - cy) - min(W, H) * 0.06), pal[4], 0.85)


def strokes(img, x, y, W, H, pal, rng):
    for k in range(int(rng.integers(2, 4))):
        col = pal[3] if k == 0 else pal[int(rng.integers(0, 3))]
        x0, y0 = W * rng.uniform(0.1, 0.25), H * rng.uniform(0.25, 0.75)
        x1, y1 = W * rng.uniform(0.75, 0.9), y0 + H * rng.uniform(-0.2, 0.2)
        t = np.linspace(0, 1, 300)
        px, py = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t + H * 0.05 * np.sin(math.pi * t * rng.uniform(1, 2))
        wid = W * rng.uniform(0.1, 0.16)
        # dry brush: several thin offset lines of varying alpha
        g = np.random.default_rng(int(rng.integers(1 << 30)))
        for o in np.linspace(-wid / 2, wid / 2, 40):
            img = stroke_path(img, list(zip(px + g.normal(0, 1.5, 300), py + o + g.normal(0, 1.5, 300))),
                              wid / 26, col, float(g.uniform(0.35, 0.8)) * (1 - 0.6 * abs(o) / wid))
    return img


STYLES = {"organic": organic, "arches": arches, "field": field, "rings": rings, "leaves": leaves, "sun": sun,
          "bauhaus": bauhaus, "hills": hills, "strokes": strokes}


def make(style, palette, aspect, seed, paper):
    rng = np.random.default_rng(seed)
    img, x, y, W, H = canvas(aspect, paper)
    img = STYLES[style](img, x, y, W, H, PALETTES[palette], rng)
    # paper: a soft fibre grain and a faint tooth, so the print doesn't read as flat colour
    g = np.random.default_rng(seed + 1000)
    tooth = np.asarray(Image.fromarray(g.normal(0, 1, (H // 3 + 1, W // 3 + 1)).astype(np.float32)).resize(
        (W, H), Image.Resampling.BICUBIC), np.float32)
    img *= (1 + 0.012 * tooth[..., None] + g.normal(0, 0.008, (H, W, 1))).astype(np.float32)
    return Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8))


def main():
    os.makedirs(OUT, exist_ok=True)
    out = []
    for style, palette, aspect, seeds, paper in PLAN:
        for s in seeds:
            pid = f"gen-{style}-{palette}-{s}"
            im = make(style, palette, aspect, s, paper)
            im.save(os.path.join(OUT, pid + ".jpg"), quality=88)
            out.append({"id": pid, "source": "made here (spikes/archviz/made/art_gen.py)", "licence": "made here",
                        "title": f"{style}, {palette}", "style": f"gen-{style}", "series": f"gen-{style}-{palette}",
                        "file": pid + ".jpg", "w": im.width, "h": im.height,
                        "tone": round(ImageStat.Stat(im.convert("L").resize((64, 64))).mean[0] / 255, 3)})
            print(pid, im.size, flush=True)
    json.dump(out, open(os.path.join(OUT, "gen.json"), "w"), indent=1)
    if "sheet" in sys.argv[1:]:
        T, C = 260, 8
        R = (len(out) + C - 1) // C
        sheet = Image.new("RGB", (C * T, R * (T + 16)), (120, 120, 120))
        d = ImageDraw.Draw(sheet)
        for i, e in enumerate(out):
            im = Image.open(os.path.join(OUT, e["file"]))
            im.thumbnail((T - 10, T - 10))
            x, y = (i % C) * T, (i // C) * (T + 16)
            sheet.paste(im, (x + (T - im.width) // 2, y + (T - im.height) // 2))
            d.text((x + 4, y + T), e["id"][4:], fill=(255, 255, 255))
        os.makedirs(os.path.join(HERE, "..", "work", "art"), exist_ok=True)
        sheet.save(os.path.join(HERE, "..", "work", "art", "gen-sheet.jpg"), quality=85)
    print(len(out), "prints")


if __name__ == "__main__":
    main()
