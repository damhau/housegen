"""A top-down look at one storey of the imported house (Cycles, a few samples), to place the camera.

    uv run --no-project --python 3.11 --with bpy python plan.py <house.glb|.blend> <out.png> <floor y> [x0 x1 z0 z1]
"""

import math
import sys

import bpy
import addon_utils
from mathutils import Vector

src, out, floor = sys.argv[1], sys.argv[2], float(sys.argv[3])
x0, x1, z0, z1 = (float(v) for v in (sys.argv[4:8] or (-6, 6, -6, 6)))
if src.endswith(".glb"):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=src)
    bpy.ops.wm.save_as_mainfile(filepath=src.replace(".glb", ".blend"))
else:
    bpy.ops.wm.open_mainfile(filepath=src)
scene = bpy.context.scene
addon_utils.enable("cycles", default_set=True)
prefs = bpy.context.preferences.addons["cycles"].preferences
prefs.compute_device_type = "CUDA"
prefs.refresh_devices()
for d in prefs.devices:
    d.use = d.type == "CUDA"
scene.render.engine = "CYCLES"
scene.cycles.device = "GPU"
scene.cycles.samples = 16
scene.cycles.use_denoising = True
# cut the storey: hide whatever starts above floor + 2.2 m (ceilings, slabs, the storeys above, the roof)
cut = floor + 2.2
for o in scene.objects:
    if o.type != "MESH":
        continue
    zs = [(o.matrix_world @ Vector(c)).z for c in o.bound_box]
    if min(zs) > cut or (max(zs) - min(zs) < 0.4 and min(zs) > floor + 1.5) or min(zs) > floor + 2.0:
        o.hide_render = True
w = bpy.data.worlds.new("w"); scene.world = w; w.use_nodes = True
w.node_tree.nodes["Background"].inputs[1].default_value = 1.0
sun = bpy.data.lights.new("sun", "SUN"); sun.energy = 3.0
so = bpy.data.objects.new("sun", sun); scene.collection.objects.link(so); so.rotation_euler = (math.radians(20), 0, math.radians(30))
cam = bpy.data.cameras.new("top"); cam.type = "ORTHO"; cam.ortho_scale = max(x1 - x0, z1 - z0)
co = bpy.data.objects.new("top", cam); scene.collection.objects.link(co)
co.location = Vector(((x0 + x1) / 2, -(z0 + z1) / 2, cut + 5)); co.rotation_euler = (0, 0, 0)
cam.clip_end = 100
scene.camera = co
scene.render.resolution_x = scene.render.resolution_y = 1200
scene.render.filepath = out
bpy.ops.render.render(write_still=True)
print("done", out)
