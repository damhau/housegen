"""A wool throw draped over the lounge chair by cloth simulation (made here). Writes made/throw.glb in the
chair's own frame as dress.place() stands it (its base centred on the origin, unscaled, unturned), and
made/throw-preview.jpg.

    uv run --no-project --python 3.11 --with bpy python made/throw.py [frames]
"""

import math
import os
import sys

import bpy
import addon_utils
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
CHAIR = os.path.join(HERE, "..", "assets", "models", "mid_century_lounge_chair", "mid_century_lounge_chair.gltf")
frames = int(sys.argv[1]) if len(sys.argv) > 1 else 70

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
bpy.ops.import_scene.gltf(filepath=CHAIR)
chair = [o for o in scene.objects if o.type == "MESH"]
tops = [o for o in scene.objects if o.parent is None]
bpy.context.view_layer.update()
ps = [o.matrix_world @ Vector(c) for o in chair for c in o.bound_box]
lo = Vector((min(p.x for p in ps), min(p.y for p in ps), min(p.z for p in ps)))
hi = Vector((max(p.x for p in ps), max(p.y for p in ps), max(p.z for p in ps)))
for o in tops:  # as dress.place(): the base centred on the origin
    o.location -= Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
bpy.context.view_layer.update()
ps = [o.matrix_world @ Vector(c) for o in chair for c in o.bound_box]
lo = Vector((min(p.x for p in ps), min(p.y for p in ps), min(p.z for p in ps)))
hi = Vector((max(p.x for p in ps), max(p.y for p in ps), max(p.z for p in ps)))
print("chair box", [round(v, 2) for v in lo], [round(v, 2) for v in hi])
bpy.ops.mesh.primitive_plane_add(size=4.0)
floor = bpy.context.active_object
floor.name = "floor"
floor.modifiers.new("collide", "COLLISION")
chair = [*chair, floor]
for o in chair:
    if not o.modifiers:
        o.modifiers.new("collide", "COLLISION")
    o.collision.thickness_outer = 0.01
    o.collision.cloth_friction = 8.0

# arm tops: the highest chair surface near each side (x = +-0.42), between the seat and the back
dg = bpy.context.evaluated_depsgraph_get()
arm = max((scene.ray_cast(dg, Vector((ARM_X, y, 2.0)), Vector((0, 0, -1)))[1].z, y)
          for y in (-0.3, -0.2, -0.1, 0.0) for ARM_X in (0.42,))
print("arm top", round(arm[0], 2), "at y", arm[1])
# the throw, folded in three to a 0.48 m band, 1.45 m long, laid across the right armrest
bpy.ops.mesh.primitive_grid_add(x_subdivisions=70, y_subdivisions=26, size=1.0)
throw = bpy.context.active_object
throw.name = "throw"
throw.scale = (1.45, 0.48, 1)
bpy.ops.object.transform_apply(scale=True)
throw.rotation_euler = (0.0, math.radians(-4), math.radians(10))
throw.location = (0.12, arm[1] + 0.12, arm[0] + 0.04)
bpy.ops.object.transform_apply(location=True, rotation=True)
cloth = throw.modifiers.new("cloth", "CLOTH")
s = cloth.settings
s.quality = 10
s.mass = 0.35
s.tension_stiffness = s.compression_stiffness = 12
s.shear_stiffness = 6
s.bending_stiffness = 0.4
s.air_damping = 2.0
cloth.collision_settings.distance_min = 0.006
cloth.collision_settings.use_self_collision = True
cloth.collision_settings.self_distance_min = 0.004
scene.frame_start, scene.frame_end = 1, frames
for f in range(1, frames + 1):
    scene.frame_set(f)
bpy.context.view_layer.objects.active = throw
throw.select_set(True)
bpy.ops.object.modifier_apply(modifier="cloth")
vs = [throw.matrix_world @ v.co for v in throw.data.vertices]
print("throw box", [round(min(v[i] for v in vs), 2) for i in range(3)], [round(max(v[i] for v in vs), 2) for i in range(3)])
sol = throw.modifiers.new("thick", "SOLIDIFY")
sol.thickness = 0.008
bpy.ops.object.modifier_apply(modifier="thick")
sub = throw.modifiers.new("smooth", "SUBSURF")
sub.levels = 1
bpy.ops.object.modifier_apply(modifier="smooth")
bpy.ops.object.shade_smooth()
m = bpy.data.materials.new("throw-wool")
m.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.55, 0.52, 0.47, 1)
throw.data.materials.append(m)

# a preview: the chair and its throw
addon_utils.enable("cycles", default_set=True)
prefs = bpy.context.preferences.addons["cycles"].preferences
prefs.compute_device_type = "CUDA"
prefs.refresh_devices()
for d in prefs.devices:
    d.use = d.type == "CUDA"
scene.render.engine = "CYCLES"
scene.cycles.device = "GPU"
scene.cycles.samples = 32
scene.render.resolution_x, scene.render.resolution_y = 640, 640
w = bpy.data.worlds.new("w")
scene.world = w
w.node_tree.nodes["Background"].inputs[1].default_value = 1.0
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
scene.collection.objects.link(cam)
cam.location = (1.6, -1.6, 1.3)
d = Vector((0, 0, 0.45)) - cam.location
cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
scene.camera = cam
scene.render.filepath = os.path.join(HERE, "throw-preview.jpg")
scene.render.image_settings.file_format = "JPEG"
bpy.ops.render.render(write_still=True)
# only the throw goes out, in this frame
for o in list(scene.objects):
    o.select_set(o is throw)
bpy.ops.export_scene.gltf(filepath=os.path.join(HERE, "throw.glb"), export_format="GLB", use_selection=True)
print("wrote throw.glb")
