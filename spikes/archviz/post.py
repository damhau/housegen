"""The photographic finish on a graded still (global, no AI): a soft bloom around the brightest areas (the
windows) and an unsharp mask that restores the texture the denoiser and the downscale soften.

    post.py <in.png> <out.png> [bloom=0.12] [sharpen=60]
"""

import sys

import numpy as np
from PIL import Image, ImageFilter

src, out = sys.argv[1], sys.argv[2]
bloom = float(sys.argv[3]) if len(sys.argv) > 3 else 0.12
sharpen = int(sys.argv[4]) if len(sys.argv) > 4 else 60
im = Image.open(src).convert("RGB")
a = np.asarray(im).astype(np.float32) / 255
lum = a @ np.array([0.2126, 0.7152, 0.0722], np.float32)
hi = np.clip((lum - 0.8) / 0.2, 0, 1)[..., None] * a
glow = np.asarray(Image.fromarray((hi * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(im.width / 60))).astype(np.float32) / 255
a = 1 - (1 - a) * (1 - glow * bloom * 4)  # screen
im = Image.fromarray((np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8))
im = im.filter(ImageFilter.UnsharpMask(radius=1.6, percent=sharpen, threshold=2))
im.save(out)
print("post", out)
