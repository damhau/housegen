"""Room dressing (made here, for photo.py and style.py): a towel draped over a tub's rim by cloth simulation, a stack
of folded towels, an oak bath caddy with a candle and a soap, a pair of pump bottles on a stone tray; and the styling
props: a stack of cloth-bound books, a glass carafe and tumbler, pillar candles on a stone tray, a folded linen
napkin, a round oak stool (bare, or with folded towels), a terry bath mat, a towel on a wall bar.

    uv run --no-project --python 3.11 --with bpy python made/bathset.py <what> <out.glb> [rim width | seed]

what: drape (over a rim `rim width` m wide, 0.10 by default) | stack | caddy | bottles | pair (no tray) |
      books (seed: the colours and the twist) | carafe | carafe1 (no tumbler) | candles | napkin | stool | stooltowels | bathmat | rail

Frames (Blender, z up), as photo.py places them:
- drape: the rim's top centre at the origin, the rim along x, the room on -y. Hangs about 0.30 outside and 0.20 inside.
- rail: the bar's centre at the origin, along x, the wall at y = +0.075, the room on -y.
- stack, caddy, bottles: their base centred on the origin. The caddy is 0.80 long along y (photo.py stretches it
  across the tub), 0.20 wide along x.

Material names (photo.py gives each its Cycles material): towel-white, towel-sand, oak, amber-glass, wax,
pump-black, ceramic, stone, soap, cover-<colour>, pages, glass, water, napkin, mat-terry.
"""

import math
import os
import random
import sys

import bpy
from mathutils import Vector

WHAT, OUT = sys.argv[1], sys.argv[2]
RIM = float(sys.argv[3]) if len(sys.argv) > 3 else 0.10
SEED = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 0

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
    # the revolved normals outward (away from the axis), so a shell grows inward and glass refracts the right way
    # (normals_make_consistent guesses wrong on an open cup)
    out = sum(f.normal.x * f.center.x + f.normal.y * f.center.y for f in o.data.polygons)
    if out < 0:
        o.data.flip_normals()
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


def rail():
    """A black towel bar on two stand-offs, a bath towel folded in three hung over it, the front a little longer.
    The bar's centre at the origin, along x; the wall at y = +0.075 (the stand-offs' feet); the room on -y."""
    black = mat("pump-black", (0.02, 0.02, 0.02), 0.45)
    bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=0.011, depth=0.60, rotation=(0, math.pi / 2, 0))
    bar = bpy.context.active_object
    bar.name = "bar"
    bpy.ops.object.shade_smooth()
    bar.data.materials.append(black)
    parts = [bar]
    for k, x in enumerate((-0.27, 0.27)):
        bpy.ops.mesh.primitive_cylinder_add(vertices=16, radius=0.008, depth=0.075, location=(x, 0.0375, 0),
                                            rotation=(math.pi / 2, 0, 0))
        post = bpy.context.active_object
        bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=0.022, depth=0.008, location=(x, 0.071, 0),
                                            rotation=(math.pi / 2, 0, 0))
        rose = bpy.context.active_object
        for o in (post, rose):
            o.name = f"{o.name}{k}"
            bpy.ops.object.select_all(action="DESELECT")
            active(o)
            bpy.ops.object.shade_smooth()
            o.data.materials.append(black)
            parts.append(o)
    collider(bar, thick=0.003)
    wall = block("wall", (0.8, 0.02, 1.0), (0, 0.085, -0.4), mat("collider", (0.5, 0.5, 0.5)))
    collider(wall)
    # the sheet by its length s: the back 0.36 m already hanging between the bar and the wall (a flat sheet would
    # start through the wall), the front 0.44 m flat toward the room, to fall and crease
    nx, ns, back, front = 30, 64, 0.36, 0.44
    verts = []
    for j in range(ns + 1):
        s = -back + (back + front) * j / ns
        if s < -0.022:
            y, z = 0.022, 0.019 - (-s - 0.022)
        else:
            y, z = -s, 0.019
        for i in range(nx + 1):
            verts.append((-0.225 + 0.45 * i / nx, y, z))
    faces = [(j * (nx + 1) + i, j * (nx + 1) + i + 1, (j + 1) * (nx + 1) + i + 1, (j + 1) * (nx + 1) + i)
             for j in range(ns) for i in range(nx)]
    me = bpy.data.meshes.new("towel")
    me.from_pydata(verts, [], faces)
    towel = bpy.data.objects.new("towel", me)
    scene.collection.objects.link(towel)
    towel.data.materials.append(mat("towel-sand", (0.62, 0.55, 0.46), 0.95))
    pin = towel.vertex_groups.new(name="pin")  # the strip on top of the bar holds; the rest hangs
    pin.add([v.index for v in towel.data.vertices if v.co.z > 0.0 and -0.008 < v.co.y < 0.022], 1.0, "REPLACE")
    cloth(towel, mass=0.4, bend=1.5).settings.vertex_group_mass = "pin"
    settle([towel], 90)
    finish(towel, 0.018)
    bpy.data.objects.remove(wall, do_unlink=True)
    return [towel, *parts]


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


def books(seed=0):
    """Three cloth-bound books in a stack, largest at the bottom, each a little turned: boards 3 mm proud of the
    page block, a spine on one long side. Muted cloth colours, shuffled by `seed`."""
    rnd = random.Random(seed)
    cloths = [("cover-sage", (0.30, 0.34, 0.27)), ("cover-sand", (0.55, 0.47, 0.37)), ("cover-ivory", (0.74, 0.71, 0.64)),
              ("cover-charcoal", (0.09, 0.09, 0.09)), ("cover-clay", (0.45, 0.26, 0.19)), ("cover-ink", (0.10, 0.13, 0.18))]
    rnd.shuffle(cloths)
    pages = mat("pages", (0.80, 0.77, 0.70), 0.85)
    out, z = [], 0.0
    for k, (w, d, h) in enumerate(((0.26, 0.19, 0.034), (0.235, 0.165, 0.026), (0.21, 0.145, 0.021))):
        name, rgb = cloths[k]
        cm = mat(name, rgb, 0.8)
        b = 0.0028
        parts = [block(f"book{k}-bottom", (w, d, b), (0, 0, b / 2), cm, bevel=0.0008, segments=2),
                 block(f"book{k}-top", (w, d, b), (0, 0, h - b / 2), cm, bevel=0.0008, segments=2),
                 block(f"book{k}-spine", (w, 0.004, h), (0, -d / 2 + 0.002, h / 2), cm, bevel=0.0015, segments=3),
                 block(f"book{k}-pages", (w - 0.006, d - 0.006, h - 2 * b), (0, 0.0, h / 2), pages, bevel=0.0005,
                       segments=1)]
        book = join(parts, f"book{k}")
        book.rotation_euler[2] = math.radians(rnd.uniform(-9, 9) + (180 if rnd.random() < 0.5 else 0))
        book.location = (rnd.uniform(-0.008, 0.008), rnd.uniform(-0.008, 0.008), z)
        out.append(book)
        z += h
    return out


def join(parts, name):
    bpy.ops.object.select_all(action="DESELECT")
    for o in parts:
        o.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.join()
    o = bpy.context.active_object
    o.name = name
    return o


def carafe(tumbler=True):
    """A clear glass carafe two-thirds full of water, a tumbler beside it (or not)."""
    glass = mat("glass", (0.96, 0.97, 0.97), 0.0, transmission=1.0)
    body = lathe("carafe", [(0.0, 0.0), (0.040, 0.0), (0.044, 0.004), (0.046, 0.10), (0.041, 0.155), (0.027, 0.19),
                            (0.024, 0.222), (0.0255, 0.228)], glass, thick=0.0025)
    water = lathe("water", [(0.0, 0.0045), (0.0395, 0.0045), (0.041, 0.12), (0.0, 0.12)],
                  mat("water", (1.0, 1.0, 1.0), 0.0, transmission=1.0))
    if not tumbler:
        return [body, water]
    tumbler = lathe("tumbler", [(0.0, 0.0), (0.031, 0.0), (0.033, 0.004), (0.035, 0.088)], glass, thick=0.002)
    tumbler.location = (0.088, 0.018, 0.0)
    return [body, water, tumbler]


def candles():
    """Three pillar candles of different heights on a round stone tray, their tops softly melted, one burnt down."""
    tray = lathe("tray", [(0.0, 0.0), (0.115, 0.0), (0.12, 0.004), (0.12, 0.013), (0.113, 0.014), (0.0, 0.006)],
                 mat("stone", (0.70, 0.66, 0.60), 0.7))
    wax = mat("wax", (0.90, 0.87, 0.80), 0.45)
    black = mat("pump-black", (0.02, 0.02, 0.02), 0.5)
    out = [tray]
    for k, (x, y, r, h) in enumerate(((-0.045, 0.02, 0.036, 0.16), (0.04, 0.035, 0.036, 0.11), (0.012, -0.055, 0.03, 0.075))):
        c = lathe(f"candle{k}", [(0.0, 0.0), (r, 0.0), (r, h - 0.004), (r - 0.003, h), (r - 0.007, h - 0.003),
                                 (0.006, h - 0.006), (0.0, h - 0.006)], wax)
        c.location = (x, y, 0.006)
        wick = cylinder(f"wick{k}", 0.0012, 0.012, (x, y, 0.006 + h - 0.007), black, 8)
        out += [c, wick]
    return out


def napkin():
    """A linen napkin folded to a long rectangle: two layers, the folded edge round, the top layer a little short."""
    m = mat("napkin", (0.78, 0.75, 0.69), 0.9)
    a = block("napkin-a", (0.13, 0.24, 0.007), (0, 0, 0.0035), m, bevel=0.0032, segments=4)
    b = block("napkin-b", (0.122, 0.222, 0.007), (0.003, 0.008, 0.0095), m, bevel=0.0032, segments=4)
    for o, k in ((a, 0), (b, 1)):  # cloth doesn't lie flat: a slow wave across it
        for v in o.data.vertices:
            v.co.z += 0.0015 * math.sin(v.co.y * 26 + k) * (0.5 + abs(v.co.x) / 0.13)
    return [join([a, b], "napkin")]


def stool(towels=False):
    """A round oak stool 0.45 m high: a 0.34 m seat with a rounded edge on three splayed, tapered legs; with
    `towels`, the towel stack on its seat."""
    oak = mat("oak", (0.55, 0.40, 0.26), 0.55)
    top = 0.45
    seat = lathe("seat", [(0.0, top - 0.032), (0.160, top - 0.032), (0.168, top - 0.026), (0.170, top - 0.016),
                          (0.168, top - 0.006), (0.160, top), (0.0, top)], oak)
    out = [seat]
    for k in range(3):
        a = math.radians(90 + 120 * k)
        foot = Vector((math.cos(a) * 0.19, math.sin(a) * 0.19, 0.0))
        head = Vector((math.cos(a) * 0.105, math.sin(a) * 0.105, top - 0.03))
        axis = head - foot
        bpy.ops.mesh.primitive_cone_add(vertices=24, radius1=0.013, radius2=0.017, depth=axis.length,
                                        location=(foot + head) / 2)
        leg = bpy.context.active_object
        leg.name = f"leg{k}"
        leg.rotation_mode = "QUATERNION"
        leg.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(axis.normalized())
        bpy.ops.object.shade_smooth()
        leg.data.materials.append(oak)
        out.append(leg)
    if towels:
        for o in stack():
            o.location.z += top
            o.scale = (0.95, 0.95, 1.0)
            out.append(o)
    return out


def bathmat():
    """A terry bath mat 0.80 x 0.50 m, 14 mm of pile, rounded corners and a soft edge."""
    bpy.ops.mesh.primitive_plane_add(size=1.0)
    o = bpy.context.active_object
    o.name = "bathmat"
    o.scale = (0.80, 0.50, 1)
    bpy.ops.object.transform_apply(scale=True)
    b = o.modifiers.new("corners", "BEVEL")
    b.affect, b.width, b.segments = "VERTICES", 0.05, 6
    apply(o, b)
    sol = o.modifiers.new("thick", "SOLIDIFY")
    sol.thickness, sol.offset = 0.014, 1.0
    apply(o, sol)
    e = o.modifiers.new("edge", "BEVEL")
    e.width, e.segments = 0.005, 3
    apply(o, e)
    bpy.ops.object.shade_smooth()
    o.data.materials.append(mat("mat-terry", (0.80, 0.78, 0.74), 0.95))
    return [o]


made = {"drape": drape, "stack": stack, "caddy": caddy, "bottles": bottles, "pair": lambda: bottles(False),
        "books": lambda: books(SEED), "carafe": carafe, "carafe1": lambda: carafe(False), "candles": candles, "napkin": napkin, "stool": stool,
        "stooltowels": lambda: stool(True), "bathmat": bathmat, "rail": rail}[WHAT]()
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
