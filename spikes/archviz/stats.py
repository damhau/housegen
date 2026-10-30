"""Image statistics: mean luminance and p10/p50/p90 (0-255, Rec.709 weights on the sRGB values), and the
white balance of a wall patch as CIELAB a*/b* (D65; 0/0 = neutral, b* > 0 = warm/yellow, a* > 0 = magenta).

    stats.py <img>[@x0,y0,x1,y1] ...     the patch in fractions of the width/height
"""

import sys

import numpy as np
from PIL import Image


def lab(rgb):
    c = rgb / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = lin @ m.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return 116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])


for arg in sys.argv[1:]:
    path, _, patch = arg.partition("@")
    im = np.asarray(Image.open(path).convert("RGB")).astype(float)
    lum = 0.2126 * im[..., 0] + 0.7152 * im[..., 1] + 0.0722 * im[..., 2]
    p10, p50, p90 = np.percentile(lum, [10, 50, 90])
    line = f"{path}: mean {lum.mean():.0f} p10 {p10:.0f} p50 {p50:.0f} p90 {p90:.0f}"
    if patch:
        x0, y0, x1, y1 = (float(v) for v in patch.split(","))
        h, w = lum.shape
        px = im[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)].reshape(-1, 3).mean(axis=0)
        L, a, b = lab(px)
        line += f" | wall L* {L:.0f} a* {a:+.1f} b* {b:+.1f}"
    print(line)
