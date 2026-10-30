"""A framed abstract print (made here): made/art-print.jpg (generated) and made/art.glb (oak frame, white
mount, the print), 0.62 x 0.82 m, its back on the wall plane z=0, facing +Y in Blender (glTF +Z).

    uv run --no-project --python 3.11 --with bpy --with numpy python made/art.py [seed]
"""

import math
import os
import random
import sys

import bpy
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
seed = int(sys.argv[1]) if len(sys.argv) > 1 else 3
rng = random.Random(seed)
W, H = 1200, 1600  # print pixels (0.48 x 0.64 m)

# --- the print: soft organic shapes in muted earth tones on warm paper, a fine charcoal line ---
y, x = np.mgrid[0:H, 0:W].astype(np.float32)
paper = np.array([0.93, 0.91, 0.87], np.float32)
img = np.ones((H, W, 3), np.float32) * paper
palette = [(0.72, 0.52, 0.40), (0.55, 0.60, 0.50), (0.84, 0.76, 0.62), (0.36, 0.36, 0.33), (0.78, 0.66, 0.55)]


def blob(cx, cy, rx, ry, wob, phase):
    a = np.arctan2(y - cy, x - cx)
    r = np.hypot((x - cx) / rx, (y - cy) / ry)
    edge = 1 + wob * np.sin(3 * a + phase) + 0.5 * wob * np.sin(5 * a + 2 * phase)
    return np.clip((edge - r) * 40, 0, 1)


for k, col in enumerate(palette[:4]):
    cx, cy = rng.uniform(0.25, 0.75) * W, (0.2 + 0.2 * k + rng.uniform(-0.05, 0.05)) * H
    m = blob(cx, cy, rng.uniform(0.18, 0.3) * W, rng.uniform(0.1, 0.16) * H, rng.uniform(0.06, 0.14), rng.uniform(0, 6))
    img = img * (1 - m[..., None] * 0.92) + np.array(col, np.float32) * m[..., None] * 0.92
# a single charcoal arc
t = np.linspace(0, math.pi * 1.2, 4000)
ax, ay = W * 0.5 + W * 0.32 * np.cos(t + 0.4), H * 0.55 + H * 0.3 * np.sin(t + 0.4)
for px, py in zip(ax.astype(int), ay.astype(int)):
    img[max(py - 3, 0):py + 3, max(px - 3, 0):px + 3] = (0.2, 0.2, 0.2)
# paper grain
img *= 1 + np.random.default_rng(seed).normal(0, 0.012, (H, W, 1)).astype(np.float32)
img = np.clip(img, 0, 1)
png = os.path.join(HERE, "art-print.jpg")
bpy.ops.wm.read_factory_settings(use_empty=True)
im = bpy.data.images.new("art-print", W, H)
im.pixels.foreach_set(np.concatenate([img[::-1], np.ones((H, W, 1), np.float32)], axis=2).ravel())
im.filepath_raw = png
im.file_format = "JPEG"
im.save()

# --- the frame: oak, 2.2 cm face, 3 cm deep; a white mount; the print ---
FW, FH, FACE, DEPTH, MOUNT = 0.62, 0.82, 0.022, 0.03, 0.07


def box(name, sx, sy, sz, loc, mat):
    bpy.ops.mesh.primitive_cube_add(size=1)
    o = bpy.context.active_object
    o.name = name
    o.scale = (sx, sy, sz)
    o.location = loc
    bpy.ops.object.transform_apply(scale=True)
    o.data.materials.append(mat)
    return o


def mat(name, rgb, rough, image=None):
    m = bpy.data.materials.new(name)
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = (*rgb, 1)
    p.inputs["Roughness"].default_value = rough
    if image:
        t = m.node_tree.nodes.new("ShaderNodeTexImage")
        t.image = image
        m.node_tree.links.new(t.outputs["Color"], p.inputs["Base Color"])
    return m


oak, mount = mat("art-oak", (0.55, 0.40, 0.26), 0.55), mat("art-mount", (0.92, 0.91, 0.88), 0.9)
im.reload()
# the frame lies in X (width) / Z (height), depth along Y (+Y = out of the wall)
for sx, sz, x, z in ((FW, FACE, 0, (FH - FACE) / 2), (FW, FACE, 0, -(FH - FACE) / 2),
                     (FACE, FH - 2 * FACE, (FW - FACE) / 2, 0), (FACE, FH - 2 * FACE, -(FW - FACE) / 2, 0)):
    box("frame", sx, DEPTH, sz, (x, DEPTH / 2, z), oak)
box("mount", FW - 2 * FACE, 0.004, FH - 2 * FACE, (0, DEPTH - 0.012, 0), mount)
bpy.ops.mesh.primitive_plane_add(size=1)
pr = bpy.context.active_object
pr.name = "print"
pr.scale = (FW - 2 * FACE - 2 * MOUNT, FH - 2 * FACE - 2 * MOUNT, 1)
pr.rotation_euler = (math.pi / 2, 0, 0)
pr.location = (0, DEPTH - 0.0095, 0)
bpy.ops.object.transform_apply(scale=True, rotation=True)
pr.data.uv_layers.active.data.foreach_set("uv", [c for uv in ((0, 0), (1, 0), (1, 1), (0, 1)) for c in uv])
pr.data.materials.append(mat("art-print", (1, 1, 1), 0.85, im))
out = os.path.join(HERE, "art.glb")
bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", export_image_format="JPEG")
print("wrote", out, png)
