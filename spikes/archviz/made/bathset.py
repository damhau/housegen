"""Bathroom dressing (made here, for photo.py): a towel draped over a tub's rim by cloth simulation, a stack of
folded towels, an oak bath caddy with a candle and a soap, a pair of pump bottles on a stone tray.

    uv run --no-project --python 3.11 --with bpy python made/bathset.py <what> <out.glb> [rim width]

what: drape (over a rim `rim width` m wide, 0.10 by default) | stack | caddy | bottles | pair (no tray)

Frames (Blender, z up), as photo.py places them:
- drape: the rim's top centre at the origin, the rim along x, the room on -y. Hangs about 0.30 outside and 0.20 inside.
- stack, caddy, bottles: their base centred on the origin. The caddy is 0.80 long along y (photo.py stretches it
  across the tub), 0.20 wide along x.

Material names (photo.py gives each its Cycles material): towel-white, towel-sand, oak, amber-glass, wax,
pump-black, ceramic, stone, soap.
"""

import math
import os
import sys

import bpy
from mathutils import Vector

WHAT, OUT = sys.argv[1], sys.argv[2]
RIM = float(sys.argv[3]) if len(sys.argv) > 3 else 0.10

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
MATS = {}


def mat(name, rgb, rough=0.6, metal=0.0, transmission=0.0):
    if name not in MATS:
        m = bpy.data.materials.new(name)
        p = m.node_tree.nodes["Principled BSDF"]
        p.inputs["Base Color"].default_value = (*rgb, 1)
        p.inputs["Roughness"].default_value = rough
        p.inputs["Metallic"].default_value = metal
        p.inputs["Transmission Weight"].default_value = transmission
        MATS[name] = m
    return MATS[name]


def active(o):
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = o
    o.select_set(True)


def apply(o, mod):
    active(o)
    bpy.ops.object.modifier_apply(modifier=mod.name)


def block(name, size, at, m, bevel=0.0, segments=4, sub=0):
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    o = bpy.context.active_object
    o.name = name
    o.scale = size
    bpy.ops.object.transform_apply(scale=True)
    o.location = at
    if bevel:
        b = o.modifiers.new("bevel", "BEVEL")
        b.width, b.segments = bevel, segments
        apply(o, b)
    if sub:
        s = o.modifiers.new("sub", "SUBSURF")
        s.levels = sub
        apply(o, s)
    bpy.ops.object.shade_smooth()
    o.data.materials.append(m)
    return o


def lathe(name, profile, m, thick=0.0, steps=48):
    """A body of revolution about z from (r, z) points, bottom to top; `thick`: a shell (glass) that thick."""
    me = bpy.data.meshes.new(name)
    me.from_pydata([(r, 0.0, z) for r, z in profile], [(i, i + 1) for i in range(len(profile) - 1)], [])
    o = bpy.data.objects.new(name, me)
    scene.collection.objects.link(o)
    s = o.modifiers.new("screw", "SCREW")
    s.steps = s.render_steps = steps
    s.use_merge_vertices = True
    s.use_smooth_shade = True
    apply(o, s)
    if thick:
        sol = o.modifiers.new("shell", "SOLIDIFY")
        sol.thickness = thick
        sol.offset = -1.0
        apply(o, sol)
    active(o)
    bpy.ops.object.shade_smooth()
    o.data.materials.append(m)
    return o


def cylinder(name, r, h, at, m, verts=40):
    bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=r, depth=h, location=(at[0], at[1], at[2] + h / 2))
    o = bpy.context.active_object
    o.name = name
    bpy.ops.object.shade_smooth()
    o.data.materials.append(m)
    return o


def collider(o, thick=0.004, friction=6.0):
    o.modifiers.new("collide", "COLLISION")
    o.collision.thickness_outer = thick
    o.collision.cloth_friction = friction


def towel_sheet(name, along, across, nx, ny, m):
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=nx, y_subdivisions=ny, size=1.0)
    o = bpy.context.active_object
    o.name = name
    o.scale = (along, across, 1)
    bpy.ops.object.transform_apply(scale=True)
    o.data.materials.append(m)
    return o


def cloth(o, mass=0.3, bend=0.6, tension=20.0):
    c = o.modifiers.new("cloth", "CLOTH")
    s = c.settings
    s.quality = 10
    s.mass = mass
    s.tension_stiffness = s.compression_stiffness = tension
    s.shear_stiffness = tension / 2
    s.bending_stiffness = bend
    s.air_damping = 3.0
    c.collision_settings.distance_min = 0.004
    c.collision_settings.use_self_collision = True
    c.collision_settings.self_distance_min = 0.004
    return c


def settle(objs, n):
    scene.frame_start, scene.frame_end = 1, n
    for f in range(1, n + 1):
        scene.frame_set(f)
    for o in objs:
        apply(o, o.modifiers["cloth"])
    scene.frame_set(1)


def finish(o, thick):
    """Terry towelling: a soft thick edge."""
    sol = o.modifiers.new("thick", "SOLIDIFY")
    sol.thickness = thick
    sol.offset = 0.0
    apply(o, sol)
    s = o.modifiers.new("smooth", "SUBSURF")
    s.levels = 1
    apply(o, s)
    active(o)
    bpy.ops.object.shade_smooth()


def drape():
    """A bath towel folded in three along its length (a 0.30 m band), laid over the rim, more of it outside."""
    rim = block("rim", (0.6, RIM, 0.6), (0, 0, -0.3), mat("collider", (0.5, 0.5, 0.5)), bevel=0.012)
    collider(rim)
    towel = towel_sheet("towel", 0.30, 0.62, 30, 62, mat("towel-white", (0.85, 0.84, 0.81), 0.95))
    towel.location = (0.0, -0.05, 0.008)
    bpy.ops.object.transform_apply(location=True)
    # the strip lying on the rim is pinned: a towel's weight and friction hold it there, a simulation's slide off
    pin = towel.vertex_groups.new(name="pin")
    pin.add([v.index for v in towel.data.vertices if abs(v.co.y) < RIM / 2 - 0.012], 1.0, "REPLACE")
    cloth(towel, mass=0.35, bend=1.2).settings.vertex_group_mass = "pin"
    settle([towel], 60)
    finish(towel, 0.014)
    bpy.data.objects.remove(rim, do_unlink=True)
    return [towel]


def stack():
    """Three folded towels: rounded slabs, their folded front edge fully round, the top one a shade warmer."""
    out = []
    z = 0.0
    for k, (w, d, h, m) in enumerate(((0.30, 0.24, 0.05, "towel-white"), (0.30, 0.24, 0.05, "towel-white"),
                                      (0.27, 0.21, 0.042, "towel-sand"))):
        rgb = (0.85, 0.84, 0.81) if m == "towel-white" else (0.62, 0.55, 0.46)
        o = block(f"towel{k}", (w, d, h), (0.004 * (k - 1), 0.003 * k, z + h / 2), mat(m, rgb, 0.95), bevel=h * 0.45,
                  segments=6, sub=1)
        # a soft sag on top: the middle a few millimetres lower than the folds
        for v in o.data.vertices:
            if v.co.z > 0:
                v.co.z -= 0.004 * (1 - min(1.0, (abs(v.co.x) / (w / 2)) ** 4))
        out.append(o)
        z += h - 0.004
    return out


def caddy():
    """An oak bath caddy: four slats on two cleats, 0.80 x 0.20; a candle in an amber jar, a bar of soap on a
    stone dish, a small folded hand towel."""
    oak = mat("oak", (0.55, 0.40, 0.26), 0.55)
    out = []
    for k in range(4):
        x = -0.075 + k * 0.05
        out.append(block(f"slat{k}", (0.04, 0.80, 0.016), (x, 0, 0.012 + 0.008), oak, bevel=0.004))
    for y in (-0.30, 0.30):
        out.append(block(f"cleat{y:+.1f}", (0.19, 0.03, 0.012), (0, y, 0.006), oak, bevel=0.003))
    top = 0.028
    jar = lathe("jar", [(0.0, 0.0), (0.038, 0.0), (0.040, 0.004), (0.040, 0.085), (0.0385, 0.088)],
                mat("amber-glass", (0.55, 0.26, 0.06), 0.05, transmission=1.0), thick=0.003)
    jar.location = (0.0, 0.17, top)
    wax = cylinder("wax", 0.035, 0.062, (0.0, 0.17, top + 0.004), mat("wax", (0.92, 0.88, 0.80), 0.4))
    wick = cylinder("wick", 0.0015, 0.012, (0.0, 0.17, top + 0.066), mat("pump-black", (0.02, 0.02, 0.02), 0.5), 8)
    dish = lathe("dish", [(0.0, 0.0), (0.045, 0.0), (0.055, 0.012), (0.052, 0.014), (0.0, 0.006)],
                 mat("stone", (0.70, 0.66, 0.60), 0.7))
    dish.location = (0.0, -0.06, top)
    soap = block("soap", (0.07, 0.045, 0.022), (0.0, -0.06, top + 0.007 + 0.011), mat("soap", (0.80, 0.78, 0.70), 0.5),
                 bevel=0.01, segments=5)
    hand = block("hand-towel", (0.17, 0.14, 0.035), (0.005, -0.25, top + 0.0175), mat("towel-sand", (0.62, 0.55, 0.46), 0.95),
                 bevel=0.015, segments=5, sub=1)
    hand.rotation_euler[2] = math.radians(6)
    return [*out, jar, wax, wick, dish, soap, hand]


def bottles(tray=True):
    """Two pump bottles (amber glass, matte stone ceramic), on a round stone tray or (pair) side by side."""
    out = []
    if tray:
        out.append(lathe("tray", [(0.0, 0.0), (0.105, 0.0), (0.11, 0.004), (0.11, 0.012), (0.104, 0.013), (0.0, 0.006)],
                         mat("stone", (0.70, 0.66, 0.60), 0.7)))
    base = 0.006 if tray else 0.0
    for k, (x, glass) in enumerate(((-0.045, True), (0.05, False)) if tray else ((-0.04, True), (0.04, False))):
        m = mat("amber-glass", (0.55, 0.26, 0.06), 0.05, transmission=1.0) if glass else mat("ceramic", (0.82, 0.80, 0.76), 0.55)
        h = 0.17 if glass else 0.15
        body = lathe(f"bottle{k}", [(0.0, 0.0), (0.033, 0.0), (0.035, 0.004), (0.035, h - 0.035), (0.03, h - 0.018),
                                    (0.013, h - 0.008), (0.013, h), (0.0, h)], m, thick=0.003 if glass else 0.0)
        body.location = (x, 0.0, base)
        black = mat("pump-black", (0.02, 0.02, 0.02), 0.45)
        collar = cylinder(f"collar{k}", 0.015, 0.014, (x, 0.0, base + h), black)
        stem = cylinder(f"stem{k}", 0.0045, 0.03, (x, 0.0, base + h + 0.014), black, 16)
        head = block(f"head{k}", (0.018, 0.05, 0.016), (x, -0.012, base + h + 0.044 + 0.008), black, bevel=0.005)
        head.rotation_euler[2] = math.radians(-30 if k else 20)
        out += [body, collar, stem, head]
    return out


made = {"drape": drape, "stack": stack, "caddy": caddy, "bottles": bottles, "pair": lambda: bottles(False)}[WHAT]()
bpy.ops.object.select_all(action="DESELECT")
for o in made:
    for m in list(o.modifiers):
        o.modifiers.remove(m)
    o.select_set(True)
bpy.context.view_layer.objects.active = made[0]
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
ps = [o.matrix_world @ Vector(c) for o in made for c in o.bound_box]
lo = Vector([min(p[i] for p in ps) for i in range(3)])
hi = Vector([max(p[i] for p in ps) for i in range(3)])
os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
bpy.ops.export_scene.gltf(filepath=OUT, export_format="GLB", use_selection=True)
print(f"BATHSET {WHAT} -> {OUT}: box {[round(v, 3) for v in lo]} .. {[round(v, 3) for v in hi]}")
