"""The photographic finish on a graded still (global, no AI): a tone curve that brings the picture's shadows,
midtones and highlights to where agency renders put them, a soft bloom around the brightest areas (the windows),
and an unsharp mask that restores the texture the denoiser and the downscale soften.

    post.py <in.png> <out.png> [bloom=0.12] [sharpen=60] [tone=1.0]
"""

import sys

import numpy as np
from PIL import Image, ImageFilter


# luminance percentiles (p5, p50, p95, 0-1) of the agency renders the judges score 7-8 (reference/, stats at
# 640 px): bright midtones, lifted shadows, highlights kept under white
TONE_AT = (0.05, 0.50, 0.95)
TONE_TO = (0.32, 0.71, 0.90)


def pchip(xs, ys, x):
    """Monotone cubic through the points (Fritsch-Carlson): a smooth curve that never folds back."""
    xs, ys = np.asarray(xs, np.float64), np.asarray(ys, np.float64)
    h, d = np.diff(xs), np.diff(ys) / np.diff(xs)
    m = np.zeros_like(xs)
    for k in range(1, len(xs) - 1):
        if d[k - 1] * d[k] > 0:
            w1, w2 = 2 * h[k] + h[k - 1], h[k] + 2 * h[k - 1]
            m[k] = (w1 + w2) / (w1 / d[k - 1] + w2 / d[k])
    m[0], m[-1] = d[0], d[-1]
    i = np.clip(np.searchsorted(xs, x) - 1, 0, len(xs) - 2)
    t = (x - xs[i]) / h[i]
    h00, h10, h01, h11 = 2 * t**3 - 3 * t**2 + 1, t**3 - 2 * t**2 + t, -2 * t**3 + 3 * t**2, t**3 - t**2
    return h00 * ys[i] + h10 * h[i] * m[i] + h01 * ys[i + 1] + h11 * h[i] * m[i + 1]


def tone(a, strength=1.0):
    """Move the luminance percentiles toward TONE_TO with a monotone curve; colour ratios kept, highlights
    rolled into white instead of clipping per channel. Returns the image and the (from, to) percentiles."""
    lum = a @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    at = np.percentile(lum[::4, ::4], [100 * q for q in TONE_AT])
    to = [p + strength * (t - p) for p, t in zip(at, TONE_TO)]
    xs, ys = [0.0, *at, 1.0], [0.0, *to, 1.0]
    if not all(np.diff(xs) > 1e-3):
        return a, (at, at)
    new = pchip(xs, ys, np.clip(lum, 0, 1)).astype(np.float32)
    # a lift added evenly to the three channels (a gain would saturate dark foliage and wood toward orange);
    # a cut scaled, so highlights keep their colour
    lift = (new - lum)[..., None]
    gain = (np.minimum(new / np.maximum(lum, 1e-3), 4.0))[..., None]
    b = np.where(lift > 0, a + lift, a * gain)
    over = np.clip(b.max(axis=2, keepdims=True) - 1, 0, None)
    b = np.clip(b + over * 0.6, 0, 1)  # what passes white goes toward white, not to a hue-shifted clip
    return b, (at, to)


def post(src, out, bloom=0.12, sharpen=60, strength=0.0):
    im = Image.open(src).convert("RGB")
    a = np.asarray(im).astype(np.float32) / 255
    if strength:
        a, (at, to) = tone(a, strength)
        print("tone", np.round(np.array(at) * 255), "->", np.round(np.array(to) * 255))
    lum = a @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    hi = np.clip((lum - 0.8) / 0.2, 0, 1)[..., None] * a
    glow = np.asarray(Image.fromarray((hi * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(im.width / 60))).astype(np.float32) / 255
    a = 1 - (1 - a) * (1 - glow * bloom * 4)  # screen
    im = Image.fromarray((np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8))
    im = im.filter(ImageFilter.UnsharpMask(radius=1.6, percent=sharpen, threshold=2))
    im.save(out)
    print("post", out)


if __name__ == "__main__":
    post(sys.argv[1], sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 0.12,
         int(sys.argv[4]) if len(sys.argv) > 4 else 60, float(sys.argv[5]) if len(sys.argv) > 5 else 0.0)
