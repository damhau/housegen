"""Paint the two cars out of the meadow HDRI (CC0, Poly Haven): each patch is the bushes right above it, moved
down, with a feathered edge. The original stays in work/hdri-orig/; the result is saved as 16-bit ZIP EXR.

    uv run --no-project --python 3.11 --with bpy --with numpy --with pillow python hdri_clean.py \
        work/hdri-orig/meadow_8k.exr assets/hdri/meadow_8k.exr
"""
import sys

import bpy
import numpy as np
SRC = sys.argv[-2]
OUT = sys.argv[-1]
img = bpy.data.images.load(SRC)
W, H = img.size
a = np.empty(W * H * 4, dtype=np.float32)
img.pixels.foreach_get(a)
a = a.reshape(H, W, 4)[::-1].copy()  # top-down
# (x0, x1, y0, y1) in the 8k top-down frame, and how far up the source lies
patches = [((2604, 2810, 2060, 2152), 96), ((2990, 3096, 2050, 2130), 84)]
for (x0, x1, y0, y1), up in patches:
    f = 10
    src = a[y0 - up:y1 - up, x0:x1].copy()
    yy, xx = np.mgrid[y0:y1, x0:x1]
    m = np.minimum.reduce([xx - x0, x1 - 1 - xx, yy - y0, y1 - 1 - yy]).astype(np.float32)
    m = np.clip(m / f, 0, 1)[..., None]
    a[y0:y1, x0:x1] = a[y0:y1, x0:x1] * (1 - m) + src * m
out = bpy.data.images.new("clean", W, H, alpha=True, float_buffer=True)
out.pixels.foreach_set(a[::-1].ravel())
st = bpy.context.scene.render.image_settings
st.file_format, st.color_depth, st.color_mode, st.exr_codec = "OPEN_EXR", "16", "RGB", "ZIP"
out.save_render(OUT, scene=bpy.context.scene)
print("CLEAN", OUT)
