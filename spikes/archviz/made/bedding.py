"""Bedding for TestVillaGille's Chambre 4 (made here, by cloth simulation) on our bed (made/bed.py): two
pillows inflated by pressure against the headboard, a creased linen duvet with its top turned back, a wool
throw across the foot. Writes made/bedding.glb in the house's frame (Blender coordinates of the exported
house: x = scene x, y = -scene z, z up) and made/bedding-preview.jpg.

    uv run --no-project --python 3.11 --with bpy python made/bedding.py [frames]

The bed: head on the west wall (scene x 0.10), a 160 x 200 mattress centred on z 3.2, its top at y 3.27.
"""

import math
import os
import sys

import bpy
import addon_utils
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
frames = int(sys.argv[1]) if len(sys.argv) > 1 else 50
TOP, CY, WALL = 3.27, -3.2, 0.10

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
bpy.ops.import_scene.gltf(filepath=os.path.join(HERE, "bed.glb"))
bed_parts = [o for o in scene.objects if o.type == "MESH"]
bpy.ops.mesh.primitive_plane_add(size=8, location=(1.5, CY, 2.75))
floor = bpy.context.active_object


def collider(o, thick=0.008, friction=8.0):
    o.modifiers.new("collide", "COLLISION")
    o.collision.thickness_outer = thick
    o.collision.cloth_friction = friction


for o in (*bed_parts, floor):
    collider(o)


def cloth_settings(o, mass, bend, tension=15.0, shrink=0.0, pressure=0.0, self_collide=True, pin=None):
    c = o.modifiers.new("cloth", "CLOTH")
    s = c.settings
    s.quality = 10
    s.mass = mass
    s.tension_stiffness = s.compression_stiffness = tension
    s.shear_stiffness = tension / 2
    s.bending_stiffness = bend
    s.air_damping = 2.0
    if shrink:
        s.shrink_min = shrink
    if pressure:
        s.use_pressure = True
        s.uniform_pressure_force = pressure
    if pin:
        s.vertex_group_mass = pin
    c.collision_settings.distance_min = 0.005
    c.collision_settings.use_self_collision = self_collide
    c.collision_settings.self_distance_min = 0.004
    return c


def settle(objs, n):
    scene.frame_start, scene.frame_end = 1, n
    for f in range(1, n + 1):
        scene.frame_set(f)
    for o in objs:
        bpy.ops.object.select_all(action="DESELECT")
        bpy.context.view_layer.objects.active = o
        o.select_set(True)
        bpy.ops.object.modifier_apply(modifier="cloth")
    scene.frame_set(1)


def smooth(o, levels=1):
    sub = o.modifiers.new("smooth", "SUBSURF")
    sub.levels = levels
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.modifier_apply(modifier="smooth")
    bpy.ops.object.shade_smooth()


# --- pillows: flat closed cases inflated by pressure, leaning on the headboard as they fill and slump ---
pillows = []
for k, dz in enumerate((-0.40, 0.40)):
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    p = bpy.context.active_object
    p.name = f"pillow-{k}"
    p.scale = (0.66, 0.46, 0.04)
    bpy.ops.object.transform_apply(scale=True)
    sub = p.modifiers.new("grid", "SUBSURF")
    sub.subdivision_type = "SIMPLE"
    sub.levels = 4
    bpy.ops.object.modifier_apply(modifier="grid")
    p.rotation_euler = (0.0, math.radians(62), math.radians(3 if k else -4))  # the headboard end up
    p.location = (WALL + 0.12 + 0.20, CY + dz, TOP + 0.30)
    bpy.ops.object.transform_apply(location=True, rotation=True)
    # its lowest edge rests on the mattress and does not slide: the pillow leans on the headboard
    zs = [v.co.z for v in p.data.vertices]
    g = p.vertex_groups.new(name="pin")
    g.add([v.index for v in p.data.vertices if v.co.z < min(zs) + 0.03], 1.0, "REPLACE")
    cloth_settings(p, 0.15, 0.05, tension=10.0, pressure=9.0, self_collide=False, pin="pin")
    pillows.append(p)
settle(pillows, frames)
for p in pillows:
    smooth(p)
    collider(p, 0.006)

# --- the duvet: a soft linen sheet that shrinks a little as it settles (creases), pinned under the pillows ---
bpy.ops.mesh.primitive_grid_add(x_subdivisions=96, y_subdivisions=100, size=1.0)
duvet = bpy.context.active_object
duvet.name = "duvet"
duvet.scale = (1.98, 2.05, 1)
bpy.ops.object.transform_apply(scale=True)
tex = bpy.data.textures.new("ripple", "CLOUDS")
tex.noise_scale = 0.3
d = duvet.modifiers.new("ripple", "DISPLACE")
d.texture = tex
d.mid_level = 0.0
d.strength = 0.12
bpy.context.view_layer.objects.active = duvet
bpy.ops.object.modifier_apply(modifier="ripple")
xs = [v.co.x for v in duvet.data.vertices]
pin = duvet.vertex_groups.new(name="pin")
pin.add([v.index for v in duvet.data.vertices if v.co.x < min(xs) + 0.03], 1.0, "REPLACE")
duvet.location = (0.80 + 1.98 / 2, CY, TOP + 0.03)
cloth_settings(duvet, 0.5, 0.05, tension=12.0, shrink=0.08, pin="pin")
settle([duvet], frames)
collider(duvet, 0.012)
# its top turned back: a shorter layer over the head end
bpy.ops.mesh.primitive_grid_add(x_subdivisions=18, y_subdivisions=90, size=1.0)
fold = bpy.context.active_object
fold.name = "duvet-fold"
fold.scale = (0.42, 2.0, 1)
bpy.ops.object.transform_apply(scale=True)
fold.location = (1.05, CY, TOP + 0.14)
cloth_settings(fold, 0.35, 0.08, tension=12.0)
settle([fold], frames)
for o in (duvet, fold):
    o.modifiers.remove(o.modifiers["collide"]) if "collide" in o.modifiers else None
    sol = o.modifiers.new("thick", "SOLIDIFY")
    sol.thickness = 0.02
    sol.offset = 1.0
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.modifier_apply(modifier="thick")
    smooth(o)
    collider(o, 0.01)

# --- the throw: a soft folded band across the foot ---
bpy.ops.mesh.primitive_grid_add(x_subdivisions=24, y_subdivisions=96, size=1.0)
throw = bpy.context.active_object
throw.name = "throw"
throw.scale = (0.55, 2.3, 1)
bpy.ops.object.transform_apply(scale=True)
throw.rotation_euler[2] = math.radians(5)
throw.location = (1.95, CY - 0.05, TOP + 0.25)
cloth_settings(throw, 0.4, 0.15, tension=12.0)
settle([throw], frames)
sol = throw.modifiers.new("thick", "SOLIDIFY")
sol.thickness = 0.012
sol.offset = 1.0
bpy.context.view_layer.objects.active = throw
bpy.ops.object.modifier_apply(modifier="thick")
smooth(throw)
made = [duvet, fold, throw]  # the pillows stay here as colliders; the scene's pillows are CC0 models (dress.py)
for o in made:
    vs = [o.matrix_world @ v.co for v in o.data.vertices]
    print(o.name, "box", [round(min(v[i] for v in vs), 2) for i in range(3)], [round(max(v[i] for v in vs), 2) for i in range(3)])

# a preview from the room's doorway
addon_utils.enable("cycles", default_set=True)
prefs = bpy.context.preferences.addons["cycles"].preferences
prefs.compute_device_type = "CUDA"
prefs.refresh_devices()
for dv in prefs.devices:
    dv.use = dv.type == "CUDA"
scene.render.engine = "CYCLES"
scene.cycles.device = "GPU"
scene.cycles.samples = 32
scene.render.resolution_x, scene.render.resolution_y = 800, 500
w = bpy.data.worlds.new("w")
scene.world = w
w.node_tree.nodes["Background"].inputs[1].default_value = 1.0
sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
sun.data.energy = 3
sun.rotation_euler = (math.radians(50), 0, math.radians(30))
scene.collection.objects.link(sun)
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
scene.collection.objects.link(cam)
cam.location = (3.4, -1.95, 4.0)
cam.rotation_euler = (Vector((0.9, -3.3, 3.3)) - cam.location).to_track_quat("-Z", "Y").to_euler()
scene.camera = cam
scene.render.filepath = os.path.join(HERE, "bedding-preview.jpg")
scene.render.image_settings.file_format = "JPEG"
bpy.ops.render.render(write_still=True)
bpy.ops.object.select_all(action="DESELECT")
for o in made:
    o.select_set(True)
bpy.ops.export_scene.gltf(filepath=os.path.join(HERE, "bedding.glb"), export_format="GLB", use_selection=True)
print("wrote bedding.glb")
