"""A dressed bed of any size (made here, for photo.py): the upholstered platform bed of made/bed.py and the
cloth-simulated bedding of made/bedding.py, built for a W x L mattress in the frame of a kit piece: origin at
the bottom centre of the head wall, the bed running toward -y (Blender), across x, z up. The kit's pieces face
-y (three.js +z), their back at +y = depth/2: photo.py moves this frame's origin there.

    uv run --no-project --python 3.11 --with bpy python made/bedset.py <W> <L> <out.glb> [frames]

Objects: bed-plinth, bed-base, bed-mattress, bed-headboard (materials bed-plinth / bed-fabric / bed-linen),
duvet, duvet-fold, throw. Prints the mattress top (z).
"""

import math
import os
import sys

import bpy
from mathutils import Matrix

W, L, OUT = float(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
FRAMES = int(sys.argv[4]) if len(sys.argv) > 4 else 50
FRAME_W, FRAME_H, MATTRESS_H = W + 0.20, 0.30, 0.22
TOP = FRAME_H + MATTRESS_H

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene


# --- the bed, built along +x from the head wall at x = 0, centred on y = 0 (turned to -y at the end) ---
def mat(name, rgb, rough):
    m = bpy.data.materials.new(name)
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = (*rgb, 1)
    p.inputs["Roughness"].default_value = rough
    return m


fabric, linen, plinth = mat("bed-fabric", (0.50, 0.47, 0.43), 0.9), mat("bed-linen", (0.88, 0.87, 0.85), 0.9), mat("bed-plinth", (0.05, 0.05, 0.05), 0.7)


def block(name, x0, x1, y0, y1, z0, z1, m, bevel, sub=0):
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    o = bpy.context.active_object
    o.name = name
    o.scale = (x1 - x0, y1 - y0, z1 - z0)
    o.location = ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)
    bpy.ops.object.transform_apply(scale=True, location=False)
    if bevel:
        b = o.modifiers.new("bevel", "BEVEL")
        b.width = bevel
        b.segments = 5
        bpy.ops.object.modifier_apply(modifier="bevel")
    if sub:
        s = o.modifiers.new("sub", "SUBSURF")
        s.levels = sub
        bpy.ops.object.modifier_apply(modifier="sub")
    bpy.ops.object.shade_smooth()
    o.data.materials.append(m)
    return o


x0 = 0.10  # the base starts behind the headboard's padding
bed = [
    block("bed-plinth", x0 + 0.12, x0 + L - 0.08, -FRAME_W / 2 + 0.12, FRAME_W / 2 - 0.12, 0.0, 0.08, plinth, 0.0),
    block("bed-base", x0, x0 + L + 0.06, -FRAME_W / 2, FRAME_W / 2, 0.08, FRAME_H, fabric, 0.035),
    block("bed-mattress", x0 + 0.03, x0 + 0.03 + L, -W / 2, W / 2, FRAME_H, TOP, linen, 0.06, sub=1),
    block("bed-headboard", 0.0, 0.12, -FRAME_W / 2 - 0.05, FRAME_W / 2 + 0.05, 0.08, 1.15, fabric, 0.045, sub=1),
]
bpy.ops.mesh.primitive_plane_add(size=8, location=(1.5, 0, 0))
floor = bpy.context.active_object


def collider(o, thick=0.008, friction=8.0):
    o.modifiers.new("collide", "COLLISION")
    o.collision.thickness_outer = thick
    o.collision.cloth_friction = friction


for o in (*bed, floor):
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


def sheet(name, along, across, nx, ny):
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=nx, y_subdivisions=ny, size=1.0)
    o = bpy.context.active_object
    o.name = name
    o.scale = (along, across, 1)
    bpy.ops.object.transform_apply(scale=True)
    return o


# --- pillows (colliders only: they lift the duvet's head end; the scene's pillows are CC0 models) ---
pillows = []
for k, dy in enumerate((-W / 4, W / 4) if W >= 1.2 else (0.0,)):
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    p = bpy.context.active_object
    p.scale = (0.66, min(0.46, W / 2 - 0.02) if W >= 1.2 else 0.46, 0.04)
    bpy.ops.object.transform_apply(scale=True)
    sub = p.modifiers.new("grid", "SUBSURF")
    sub.subdivision_type = "SIMPLE"
    sub.levels = 4
    bpy.ops.object.modifier_apply(modifier="grid")
    p.rotation_euler = (0.0, math.radians(62), math.radians(3 if k else -4))
    p.location = (0.12 + 0.20, dy, TOP + 0.30)
    bpy.ops.object.transform_apply(location=True, rotation=True)
    zs = [v.co.z for v in p.data.vertices]
    g = p.vertex_groups.new(name="pin")
    g.add([v.index for v in p.data.vertices if v.co.z < min(zs) + 0.03], 1.0, "REPLACE")
    cloth_settings(p, 0.15, 0.05, tension=10.0, pressure=9.0, self_collide=False, pin="pin")
    pillows.append(p)
settle(pillows, FRAMES)
for p in pillows:
    smooth(p)
    collider(p, 0.006)

# --- the duvet: soft linen that shrinks a little as it settles (creases), pinned under the pillows ---
along = L - 0.02
duvet = sheet("duvet", along, W + 0.45, 96, 100)
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
duvet.location = (0.70 + along / 2, 0.0, TOP + 0.03)
cloth_settings(duvet, 0.5, 0.05, tension=12.0, shrink=0.08, pin="pin")
settle([duvet], FRAMES)
collider(duvet, 0.012)
# its top turned back
fold = sheet("duvet-fold", 0.42, W + 0.40, 18, 90)
fold.location = (0.95, 0.0, TOP + 0.14)
cloth_settings(fold, 0.35, 0.08, tension=12.0)
settle([fold], FRAMES)
for o in (duvet, fold):
    if "collide" in o.modifiers:
        o.modifiers.remove(o.modifiers["collide"])
    sol = o.modifiers.new("thick", "SOLIDIFY")
    sol.thickness = 0.02
    sol.offset = 1.0
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.modifier_apply(modifier="thick")
    smooth(o)
    collider(o, 0.01)

# --- the throw: a folded band across the foot ---
throw = sheet("throw", 0.55, W + 0.70, 24, 96)
throw.rotation_euler[2] = math.radians(5)
throw.location = (L - 0.15, -0.05, TOP + 0.25)
cloth_settings(throw, 0.4, 0.15, tension=12.0)
settle([throw], FRAMES)
sol = throw.modifiers.new("thick", "SOLIDIFY")
sol.thickness = 0.012
sol.offset = 1.0
bpy.context.view_layer.objects.active = throw
bpy.ops.object.modifier_apply(modifier="thick")
smooth(throw)

# --- turned into the kit's frame (head wall at y = 0, the bed toward -y) and written ---
made = [*bed, duvet, fold, throw]
turn = Matrix.Rotation(math.radians(-90), 4, "Z")
bpy.ops.object.select_all(action="DESELECT")
for o in made:
    for m in list(o.modifiers):
        o.modifiers.remove(m)
    o.matrix_world = turn @ o.matrix_world
    o.select_set(True)
bpy.context.view_layer.objects.active = made[0]
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
bpy.ops.export_scene.gltf(filepath=OUT, export_format="GLB", use_selection=True)
print(f"BEDSET {OUT}: {W} x {L}, mattress top {TOP:.2f}")
