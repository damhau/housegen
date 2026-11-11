"""Exterior listing photos (#93): the house in its surroundings, in Cycles.

At prepare, `build` turns the imported house into outside.blend: the project's surroundings when it has them (the
surveyed ground under the aerial photo, the neighbours, the land to the horizon; kit/context.js's data and frame),
the hedges' cores merged into one mass; `viewpoints` scores cameras around the house by ray casts and gives each a
sun from a real date and hour at the site. At shoot, `run` adds what the export cannot carry (the hedges' leaves, the
trees, grass near the camera: real CC0 plants instanced by geometry nodes) and renders one photo.
"""

import datetime as dt
import json
import math
import os
import tempfile
import time
from zoneinfo import ZoneInfo

import bpy  # noqa: I001 (bmesh and mathutils load with bpy)
import bmesh
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from PIL import Image

import paths
import still

PLANTS = os.path.join(paths.ASSETS, "outside")  # Poly Haven, CC0 (spikes/archviz/ph_get.py --blend / --hdri)
TREE = ("tree_small_02", "tree_small_02_2k.blend")
GRASS = ("grass_medium_01", "grass_medium_01_2k.blend")
SKY_HDRI = ("kloofendal_48d_partly_cloudy_puresky", "kloofendal_48d_partly_cloudy_puresky_4k.exr")

SHELL = {"building", "perimeter", "wall", "slab", "window", "door"}
INDOOR = {"furniture", "roomFloor", "ceiling", "partition", "interiorDoor", "skirting", "windowSills", "curtainRails",
          "wallTiles", "floorPlan"}
GROUND = ("real-ground", "far")
WIDTH, HEIGHT = 1920, 1080
FOV = 46.0  # vertical, at 16:9: a 24 mm lens on full frame
EYES = (1.7, 4.5)  # a photographer standing; on a mast, over the hedges
FILL = 0.85  # the house's share of the frame's width
STEP_DEG = 15
APART_DEG = 40.0  # two viewpoints from closer directions than this show the same sides
MAX_VIEWS = 6
GRID = (40, 22)
LEAVES_PER_M2, GRASS_PER_M2, GRASS_RADIUS = 220.0, 200.0, 30.0
TREES_RADIUS = 260.0  # the surroundings' trees farther than this from the camera stay out (the far photo has them)
SUN_POWER, SKY_GAIN = 4.5, 0.55
LOOK = "AgX - Medium High Contrast"
# the season agencies shoot in, the hours with the sun high enough to model the façades
DATES = ((4, 25), (5, 25), (6, 21), (7, 20), (8, 20), (9, 10))
HOURS = [h + m / 60 for h in range(7, 21) for m in (0, 15, 30, 45)]
ZONE = ZoneInfo("Europe/Zurich")


def log(*a):
    print(*a, flush=True)


def ancestors(o):
    while o is not None:
        yield o
        o = o.parent


def is_shell(o):
    ks = [a.get("kind") for a in ancestors(o)]
    return any(k in SHELL for k in ks) and not any(k in INDOOR for k in ks)


def indoors(o):
    return any(a.get("kind") in INDOOR for a in ancestors(o))


def to_blender(x, y, z):
    return Vector((x, -z, y))


def to_kit(v):
    return v.x, v.z, -v.y


def material(name, rgb, rough=0.9, spec=0.2):
    m = bpy.data.materials.new(name)
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = (*rgb, 1)
    p.inputs["Roughness"].default_value = rough
    p.inputs["Specular IOR Level"].default_value = spec
    return m


def lawn_material():
    m = bpy.data.materials.get("lawn")
    if m:
        return m
    m = bpy.data.materials.new("lawn")
    nt = m.node_tree
    p = nt.nodes["Principled BSDF"]
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 0.35
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (0.05, 0.09, 0.025, 1)
    ramp.color_ramp.elements[1].color = (0.11, 0.15, 0.04, 1)
    nt.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], p.inputs["Base Color"])
    p.inputs["Roughness"].default_value = 0.95
    # grass at a grazing angle reflects little sky: a default specular paints the lawn pale blue
    p.inputs["Specular IOR Level"].default_value = 0.05
    return m


def house_box(scene):
    """The house's shell (walls, roofs, slabs, openings) as (min, max) in Blender metres."""
    lo, hi = Vector((1e9, 1e9, 1e9)), Vector((-1e9, -1e9, -1e9))
    for o in scene.objects:
        if o.type == "MESH" and not o.hide_render and is_shell(o):
            for c in o.bound_box:
                w = o.matrix_world @ Vector(c)
                lo = Vector(map(min, lo, w))
                hi = Vector(map(max, hi, w))
    return lo, hi


def site_box(scene):
    """The builder's site (every mesh but its ground), like context.js's ownGround: (min, max) in x, y."""
    lo, hi = Vector((1e9, 1e9)), Vector((-1e9, -1e9))
    for o in scene.objects:
        if o.type != "MESH" or o.hide_render or o.get("kind") == "terrain":
            continue
        for c in o.bound_box:
            w = o.matrix_world @ Vector(c)
            lo, hi = Vector((min(lo.x, w.x), min(lo.y, w.y))), Vector((max(hi.x, w.x), max(hi.y, w.y)))
    return lo, hi


def within(p, box, m=0.0):
    lo, hi = box
    return lo.x - m <= p.x <= hi.x + m and lo.y - m <= p.y <= hi.y + m


# ---------------------------------------------------------------- prepare: the scene around the house


def surroundings(scene, ctx):
    """The project's surroundings (kit/context.js's data): the surveyed ground beyond the builder's site under the
    aerial photo (its lawn cells, from the survey's land cover, in the lawn material for grass), the neighbours (a
    render on their walls, tiles on pitched roofs), the land to the horizon; returns the lidar's trees (Blender
    position, height, crown radius) and the alignment."""
    meta = json.load(open(os.path.join(ctx, "context.json"), encoding="utf-8"))
    al = meta["alignment"]
    R, N, step = meta["radius"], meta["terrain"]["size"], meta["terrain"]["step"]
    alt = np.fromfile(os.path.join(ctx, meta["terrain"]["file"]), dtype="<f4").reshape(N, N)
    th = math.radians(al.get("rotation") or 0.0)
    c, s = math.cos(th), math.sin(th)

    def place(lx, y, lz):  # context.js transforms().toScene, then into Blender's axes
        dx, dz = lx - al["x"], lz - al["z"]
        return to_blender(c * dx + s * dz, y - al["ground"], -s * dx + c * dz)

    site = site_box(scene)
    for o in [o for o in scene.objects if o.get("kind") == "terrain" and o.type == "MESH"]:
        if max(o.dimensions) > 300:  # the kit's meadow to the horizon: the real land takes over
            o.hide_render = True
            continue
        bm = bmesh.new()
        bm.from_mesh(o.data)
        mw = o.matrix_world
        bmesh.ops.delete(bm, geom=[f for f in bm.faces if not within(mw @ f.calc_center_median(), site, SITE_MARGIN)],
                         context="FACES")
        bm.to_mesh(o.data)
        bm.free()

    cover = np.asarray(Image.open(os.path.join(ctx, meta["cover"]["file"]))) if meta.get("cover") else None
    ext = meta["cover"]["extent"] if cover is not None else 1.0

    def soft_at(lx, lz):  # neither asphalt, paving nor gravel in the survey's land cover: grass can grow
        if cover is None:
            return False
        n = cover.shape[0]
        i, j = int((lx / (2 * ext) + 0.5) * n), int((lz / (2 * ext) + 0.5) * n)
        if not (0 <= i < n and 0 <= j < n):
            return False
        px = cover[j, i]
        return px[0] < 128 and px[2] < 128 and (len(px) < 4 or px[3] < 128)

    own = Heights(scene)
    box = (site[0] - Vector((SITE_MARGIN, SITE_MARGIN)), site[1] + Vector((SITE_MARGIN, SITE_MARGIN)))
    verts = []
    for j in range(N):
        for i in range(N):
            v = place(-R + i * step, float(alt[j, i]), -R + j * step)
            v.z = joined(v, box, own)
            verts.append(v)
    faces, soft = [], []
    for j in range(N - 1):
        for i in range(N - 1):
            lx, lz = -R + (i + 0.5) * step, -R + (j + 0.5) * step
            if math.hypot(lx, lz) > R - step:
                continue
            a = j * N + i
            faces.append((a, a + 1, a + N + 1, a + N))
            soft.append(soft_at(lx, lz))
    me = bpy.data.meshes.new("real-ground")
    me.from_pydata([tuple(v) for v in verts], [], faces)
    uv = me.uv_layers.new(name="UVMap")
    for poly in me.polygons:
        for li in poly.loop_indices:
            k = me.loops[li].vertex_index
            uv.data[li].uv = ((k % N) / (N - 1), 1 - (k // N) / (N - 1))
    me.attributes.new("soft", "INT", "FACE").data.foreach_set("value", [int(x) for x in soft])
    me.materials.append(aerial_material(os.path.join(ctx, meta["photo"]["file"])))
    me.materials.append(lawn_material())
    scene.collection.objects.link(bpy.data.objects.new("real-ground", me))

    n_b = neighbours(scene, ctx, meta, place, site)
    trees = []
    if meta.get("trees"):
        for lx, foot, lz, height, radius, _kind in json.load(open(os.path.join(ctx, meta["trees"]["file"])))["trees"]:
            v = place(lx, foot, lz)
            if not within(v, site):
                trees.append([round(v.x, 2), round(v.y, 2), round(v.z, 2), round(height, 2), round(radius, 2)])
    if meta.get("far"):
        far_land(scene, ctx, meta, place, R)
    log(f"outside: ground {len(faces)} cells ({sum(soft)} soft), {n_b} neighbours, {len(trees)} trees")
    return trees, al


SWITZERLAND = {"lat": 46.8, "lon": 8.2}  # the sun of a project without surroundings: the app's projects are Swiss
SITE_MARGIN, RING, SINK = 3.0, 5.0, 3.0  # context.js: the builder's ground kept, joined over RING, sunk under it


def smoothstep(x, a, b):
    t = min(1.0, max(0.0, (x - a) / (b - a)))
    return t * t * (3 - 2 * t)


class Heights:
    """The builder's ground height at a point (its terrain meshes; 0 where it has none)."""

    def __init__(self, scene):
        bm = bmesh.new()
        for o in scene.objects:
            if o.get("kind") == "terrain" and o.type == "MESH" and not o.hide_render:
                tmp = bmesh.new()
                tmp.from_mesh(o.data)
                tmp.transform(o.matrix_world)
                t = bpy.data.meshes.new("t")
                tmp.to_mesh(t)
                bm.from_mesh(t)
                bpy.data.meshes.remove(t)
                tmp.free()
        self.bvh = BVHTree.FromBMesh(bm) if bm.faces else None
        bm.free()

    def __call__(self, x, y):
        if self.bvh is None:
            return 0.0
        loc = self.bvh.ray_cast(Vector((x, y, 500.0)), Vector((0, 0, -1)))[0]
        return loc.z if loc is not None else 0.0


def joined(v, box, own):
    """context.js joined(): the surveyed ground's height at `v`, sunk under the builder's site and eased into the
    rim of its ground over RING, so the two meet without a step."""
    lo, hi = box
    dx, dy = max(lo.x - v.x, v.x - hi.x, 0.0), max(lo.y - v.y, v.y - hi.y, 0.0)
    d = math.hypot(dx, dy)
    if d <= 0:
        inside = min(v.x - lo.x, hi.x - v.x, v.y - lo.y, hi.y - v.y)
        return own(v.x, v.y) - 0.05 - SINK * smoothstep(inside, 1.5, 4)
    if d < RING:
        cx, cy = min(hi.x, max(lo.x, v.x)), min(hi.y, max(lo.y, v.y))
        t = smoothstep(d, 0, RING)
        return (own(cx, cy) - 0.03) * (1 - t) + v.z * t
    return v.z


AERIAL_TONE = (1.3, 0.5)  # saturation, value: the photo's haze and baked shading taken down under the sun


def aerial_material(path):
    """The aerial photo on the ground: it holds the land's own shading, so it is toned down under the sun."""
    m = bpy.data.materials.new("aerial")
    nt = m.node_tree
    p = nt.nodes["Principled BSDF"]
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(path, check_existing=True)
    tone = nt.nodes.new("ShaderNodeHueSaturation")
    tone.inputs["Saturation"].default_value, tone.inputs["Value"].default_value = AERIAL_TONE
    nt.links.new(tex.outputs["Color"], tone.inputs["Color"])
    nt.links.new(tone.outputs["Color"], p.inputs["Base Color"])
    p.inputs["Roughness"].default_value = 1.0
    p.inputs["Specular IOR Level"].default_value = 0.1
    return m


def facade_material(name, rgb, bay=3.0, storey=2.9, window=(1.3, 1.3), sill=0.9):
    """Render with a grid of dark windows (`bay` and `storey` apart, `window` wide and high): no blank boxes behind."""
    m = bpy.data.materials.new(name)
    nt = m.node_tree
    p = nt.nodes["Principled BSDF"]

    def op(name_, a, b=None):
        n = nt.nodes.new("ShaderNodeMath")
        n.operation = name_
        for k, v in enumerate((a, b)):
            if isinstance(v, (int, float)):
                n.inputs[k].default_value = v
            elif v is not None:
                nt.links.new(v, n.inputs[k])
        return n.outputs[0]

    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(nt.nodes.new("ShaderNodeUVMap").outputs["UV"], sep.inputs[0])
    fu = op("FRACT", op("DIVIDE", sep.outputs["X"], bay))
    fv = op("FRACT", op("DIVIDE", op("SUBTRACT", sep.outputs["Y"], sill), storey))
    half = (1 - window[0] / bay) / 2
    in_u = op("MULTIPLY", op("GREATER_THAN", fu, half), op("LESS_THAN", fu, 1 - half))
    in_v = op("LESS_THAN", fv, window[1] / storey)
    glass = op("MULTIPLY", op("MULTIPLY", in_u, in_v), op("GREATER_THAN", sep.outputs["Y"], sill))
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.inputs["A"].default_value = (*rgb, 1)
    mix.inputs["B"].default_value = (0.02, 0.025, 0.03, 1)
    nt.links.new(glass, mix.inputs["Factor"])
    nt.links.new(mix.outputs["Result"], p.inputs["Base Color"])
    rough = op("MULTIPLY_ADD", glass, -0.75)
    rough.node.inputs[2].default_value = 0.9
    nt.links.new(rough, p.inputs["Roughness"])
    p.inputs["Specular IOR Level"].default_value = 0.3
    return m


def roof_colour(photo, R, local):
    """A roof's own colour: the aerial photo's median over its footprint (the photo leans, the median holds)."""
    n = photo.shape[0]
    xs = [q[0] for q in local]
    zs = [q[2] for q in local]
    cx, cz = (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2
    hx, hz = (max(xs) - min(xs)) / 4, (max(zs) - min(zs)) / 4
    i0, i1 = (int((v / (2 * R) + 0.5) * n) for v in (cx - hx, cx + hx))
    j0, j1 = (int((v / (2 * R) + 0.5) * n) for v in (cz - hz, cz + hz))
    patch = photo[max(0, j0):min(n, j1 + 1), max(0, i0):min(n, i1 + 1)].reshape(-1, 3)
    if not len(patch):
        return (0.12, 0.07, 0.05)
    srgb = np.median(patch, axis=0) / 255.0
    lin = np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)
    return tuple(float(c) * AERIAL_TONE[1] for c in lin)


def neighbours(scene, ctx, meta, place, site):
    """The swissBUILDINGS3D houses around the plot: each roof in its colour on the aerial photo, the walls rendered
    in pale tones with rows of windows."""
    renders = [(0.78, 0.76, 0.70), (0.80, 0.78, 0.74), (0.74, 0.70, 0.62), (0.82, 0.80, 0.77), (0.70, 0.68, 0.64)]
    mats = [facade_material(f"facade-{k}", rgb) for k, rgb in enumerate(renders)]
    photo = np.asarray(Image.open(os.path.join(ctx, meta["photo"]["file"])).convert("RGB"))
    R = meta["radius"]
    verts, faces, which, uvs = [], [], [], []
    n = 0
    for b in json.load(open(os.path.join(ctx, meta["buildings"]["file"]), encoding="utf-8"))["buildings"]:
        local = [b["positions"][k:k + 3] for k in range(0, len(b["positions"]), 3)]
        pts = [place(*q) for q in local]
        if within(sum(pts, Vector()) / len(pts), site):  # the old state of the plot
            continue
        base = len(verts)
        foot = min(q.z for q in pts)
        verts.extend(tuple(q) for q in pts)
        wall = sum(ord(ch) for ch in str(b["id"])) % len(renders)
        roof = len(mats)
        mats.append(material(f"roof-{b['id']}", roof_colour(photo, R, local), 0.75, 0.3))
        for t in range(0, len(b["roof"]), 3):
            ids = [b["roof"][t + q] for q in range(3)]
            faces.append(tuple(base + k for k in ids))
            which.append(roof)
            uvs.append([(0.0, 0.0)] * 3)
        for t in range(0, len(b["wall"]), 3):
            ids = [b["wall"][t + q] for q in range(3)]
            a, bb, cc = (pts[k] for k in ids)
            nrm = (bb - a).cross(cc - a)
            along = Vector((-nrm.y, nrm.x, 0.0)).normalized() if nrm.xy.length > 1e-9 else Vector((1, 0, 0))
            faces.append(tuple(base + k for k in ids))
            which.append(wall)
            uvs.append([(pts[k].dot(along), pts[k].z - foot - 0.6) for k in ids])
        n += 1
    me = bpy.data.meshes.new("neighbours")
    me.from_pydata(verts, [], faces)
    for m in mats:
        me.materials.append(m)
    layer = me.uv_layers.new(name="UVMap")
    for poly, k, tri in zip(me.polygons, which, uvs):
        poly.material_index = k
        for li, uv in zip(poly.loop_indices, tri):
            layer.data[li].uv = uv
    scene.collection.objects.link(bpy.data.objects.new("neighbours", me))
    return n


def far_land(scene, ctx, meta, place, R):
    """The land beyond the disc to the horizon (far.bin, the Earth's curvature already taken off) under its aerial
    photos, fading into the sky's blue with distance."""
    f = meta["far"]
    radii, A = f["radii"], f["azimuths"]
    h = np.fromfile(os.path.join(ctx, f["file"]), dtype="<f4").reshape(len(radii), A)
    verts, local = [], []
    for j, r in enumerate(radii):
        for i in range(A):
            a = i / A * 2 * math.pi
            lx, lz = r * math.cos(a), r * math.sin(a)
            verts.append(tuple(place(lx, float(h[j, i]) - (0.4 if r < R else 0.0), lz)))
            local.append((lx, lz))
    faces = [(j * A + i, j * A + (i + 1) % A, (j + 1) * A + (i + 1) % A, (j + 1) * A + i)
             for j in range(len(radii) - 1) for i in range(A)]
    me = bpy.data.meshes.new("far")
    me.from_pydata(verts, [], faces)
    attr = me.attributes.new("local", "FLOAT2", "POINT")
    attr.data.foreach_set("vector", [c for xy in local for c in xy])
    m = bpy.data.materials.new("far")
    nt = m.node_tree
    p = nt.nodes["Principled BSDF"]
    p.inputs["Roughness"].default_value = 1.0
    p.inputs["Specular IOR Level"].default_value = 0.0
    loc = nt.nodes.new("ShaderNodeAttribute")
    loc.attribute_name = "local"
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(loc.outputs["Vector"], sep.inputs[0])

    def math_node(op, a, b):
        n = nt.nodes.new("ShaderNodeMath")
        n.operation = op
        for k, v in enumerate((a, b)):
            if isinstance(v, (int, float)):
                n.inputs[k].default_value = v
            elif v is not None:
                nt.links.new(v, n.inputs[k])
        return n.outputs[0]

    def photo(spec):
        ext = spec["extent"]
        u = nt.nodes.new("ShaderNodeMath")
        u.operation = "MULTIPLY_ADD"
        nt.links.new(sep.outputs["X"], u.inputs[0])
        u.inputs[1].default_value, u.inputs[2].default_value = 1 / (2 * ext), 0.5
        v = nt.nodes.new("ShaderNodeMath")
        v.operation = "MULTIPLY_ADD"
        nt.links.new(sep.outputs["Y"], v.inputs[0])
        v.inputs[1].default_value, v.inputs[2].default_value = -1 / (2 * ext), 0.5
        uvw = nt.nodes.new("ShaderNodeCombineXYZ")
        nt.links.new(u.outputs[0], uvw.inputs[0])
        nt.links.new(v.outputs[0], uvw.inputs[1])
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(os.path.join(ctx, spec["file"]))
        tex.extension = "EXTEND"
        nt.links.new(uvw.outputs[0], tex.inputs["Vector"])
        return tex.outputs["Color"]

    near, far = (photo(spec) for spec in f["photos"])
    edge = math_node("MAXIMUM", math_node("ABSOLUTE", sep.outputs["X"], None), math_node("ABSOLUTE", sep.outputs["Y"], None))
    to_far = nt.nodes.new("ShaderNodeMapRange")
    to_far.interpolation_type = "SMOOTHSTEP"
    to_far.inputs["From Min"].default_value = 0.8 * f["photos"][0]["extent"]
    to_far.inputs["From Max"].default_value = 0.95 * f["photos"][0]["extent"]
    nt.links.new(edge, to_far.inputs["Value"])
    land = nt.nodes.new("ShaderNodeMix")
    land.data_type = "RGBA"
    nt.links.new(to_far.outputs[0], land.inputs["Factor"])
    nt.links.new(near, land.inputs["A"])
    nt.links.new(far, land.inputs["B"])
    haze = math_node("SUBTRACT", 1.0, math_node("EXPONENT", math_node("MULTIPLY", nt.nodes.new("ShaderNodeCameraData")
                                                                        .outputs["View Distance"], -1 / 25000.0), None))
    lit = nt.nodes.new("ShaderNodeMix")
    lit.data_type = "RGBA"
    lit.inputs["B"].default_value = (0.0, 0.0, 0.0, 1)
    nt.links.new(haze, lit.inputs["Factor"])
    tone = nt.nodes.new("ShaderNodeHueSaturation")
    tone.inputs["Saturation"].default_value, tone.inputs["Value"].default_value = AERIAL_TONE
    nt.links.new(land.outputs["Result"], tone.inputs["Color"])
    nt.links.new(tone.outputs["Color"], lit.inputs["A"])
    nt.links.new(lit.outputs["Result"], p.inputs["Base Color"])
    p.inputs["Emission Color"].default_value = (0.55, 0.66, 0.85, 1)
    nt.links.new(math_node("MULTIPLY", haze, 0.9), p.inputs["Emission Strength"])
    me.materials.append(m)
    scene.collection.objects.link(bpy.data.objects.new("far", me))


def hedge_mass(scene):
    """The hedges' cores (the export keeps them; the leaves are instanced in the page) as one trimmed mass: the hull
    of each core with each of its neighbours (no valley between two bushes), remeshed and smoothed; its leaves come
    at shoot."""
    cores = [o for o in scene.objects if o.get("kind") == "core" and o.type == "MESH"]
    if not cores:
        return 0.0
    pts = []
    for o in cores:
        mw = o.matrix_world
        vs = [mw @ v.co for v in o.data.vertices]
        c = sum(vs, Vector()) / len(vs)
        half = Vector([max(abs((v - c)[a]) for v in vs) or 1.0 for a in range(3)])
        vs = [c + (v - c) * squared(v - c, half) for v in vs]
        pts.append((c, max((v - c).length for v in vs), vs))
    bm = bmesh.new()
    for i, (ci, ri, vi) in enumerate(pts):
        group = [vi]
        for j, (cj, rj, vj) in enumerate(pts):
            if j > i and (cj - ci).length < 1.6 * (ri + rj):
                group.append(vi + vj)
        for vs in group:
            tmp = bmesh.new()
            for v in vs:
                tmp.verts.new(v)
            bmesh.ops.convex_hull(tmp, input=tmp.verts)
            t = bpy.data.meshes.new("t")
            tmp.to_mesh(t)
            bm.from_mesh(t)
            bpy.data.meshes.remove(t)
            tmp.free()
    for o in cores:
        for c in [o, *o.children_recursive]:
            c.hide_render = True
    me = bpy.data.meshes.new("hedge-mass")
    bm.to_mesh(me)
    bm.free()
    mass = bpy.data.objects.new("hedge-mass", me)
    scene.collection.objects.link(mass)
    rem = mass.modifiers.new("remesh", "REMESH")
    rem.mode, rem.voxel_size = "VOXEL", 0.12
    mass.modifiers.new("smooth", "SMOOTH").iterations = 4
    ev = mass.evaluated_get(bpy.context.evaluated_depsgraph_get())
    smooth = bpy.data.meshes.new_from_object(ev)
    mass.modifiers.clear()
    mass.data = smooth
    smooth.materials.append(material("hedge-inside", (0.015, 0.03, 0.01), 0.9, 0.1))
    return sum(p.area for p in smooth.polygons)


def squared(d, half, k=0.65):
    """How far to push a point of a round core toward its box: trimmed hedges are boxes with soft edges."""
    n = Vector((d.x / half.x, d.y / half.y, d.z / half.z))
    inf = max(abs(n.x), abs(n.y), abs(n.z))
    return 1.0 if inf < 1e-6 else 1.0 + k * (n.length / inf - 1.0)


def kit_trees(scene):
    """The kit's trees (their leaves are instanced, not exported): hidden, listed as (x, y, z, height, crown radius)."""
    out = []
    for o in scene.objects:
        if o.get("kind") != "tree":
            continue
        for c in [o, *o.children_recursive]:
            c.hide_render = True
        p = o.matrix_world.translation
        h = float(o.get("height", 4.0))
        out.append([round(p.x, 2), round(p.y, 2), round(p.z, 2), round(h * 1.25, 2), round(float(o.get("spread", h * 0.6)) / 2, 2)])
    return out


# ---------------------------------------------------------------- prepare: viewpoints and their sun


class Caster:
    """Ray casts against a BVH of what can block a view: the house, its garden, the hedges, the neighbours, the ground
    and spheres for the trees' crowns (not in the scene until shoot)."""

    def __init__(self, scene, trees, near_xy, radius=150.0):
        bm = bmesh.new()
        self.tags = []
        for o in scene.objects:
            if o.type != "MESH" or o.hide_render or indoors(o) or o.name == "far":
                continue
            tag = ("shell" if is_shell(o) else "ground" if o.get("kind") == "terrain" or o.name == "real-ground"
                   else "hedge" if o.name == "hedge-mass" else "neighbour" if o.name == "neighbours" else "garden")
            if tag == "shell" and any(a.get("kind") == "window" for a in ancestors(o)):
                tag = "window"
            n0 = len(bm.faces)
            me = o.data
            tmp = bmesh.new()
            tmp.from_mesh(me)
            tmp.transform(o.matrix_world)
            t = bpy.data.meshes.new("t")
            tmp.to_mesh(t)
            bm.from_mesh(t)
            bpy.data.meshes.remove(t)
            tmp.free()
            self.tags += [tag] * (len(bm.faces) - n0)
        for x, y, z, h, r in trees:
            if math.hypot(x - near_xy.x, y - near_xy.y) > radius:
                continue
            n0 = len(bm.faces)
            crown = bmesh.ops.create_icosphere(bm, subdivisions=1, radius=1.0)
            for v in crown["verts"]:
                v.co = Vector((x + v.co.x * r, y + v.co.y * r, z + h - 0.55 * h * 0.5 + v.co.z * 0.3 * h))
            self.tags += ["tree"] * (len(bm.faces) - n0)
        bm.faces.ensure_lookup_table()
        self.bvh = BVHTree.FromBMesh(bm)
        bm.free()

    def cast(self, origin, d, far=1e4):
        loc, nrm, idx, dist = self.bvh.ray_cast(origin, d, far)
        return None if loc is None else (loc, nrm, self.tags[idx], dist)


def camera_frame(spec):
    """(origin, forward, right, up) of a camera spec after still.camera's level-with-shift framing."""
    pos, rest = spec.split(">")
    tgt, fov = rest.split("@")
    p, t = to_blender(*map(float, pos.split(","))), to_blender(*map(float, tgt.split(",")))
    fwd = Vector((t.x - p.x, t.y - p.y, 0)).normalized()
    right = Vector((fwd.y, -fwd.x, 0))
    pitch = math.atan2(t.z - p.z, (t - p).xy.length)
    return p, fwd, right, Vector((0, 0, 1)), math.radians(float(fov)), math.tan(pitch)


def rays(spec, nx, ny):
    """The camera's rays through a grid of the frame: (fx, fy, direction); the frame's centre is raised by the
    lens shift so verticals stay vertical."""
    p, fwd, right, up, fov, shift = camera_frame(spec)
    th = math.tan(fov / 2)
    tw = th * WIDTH / HEIGHT
    out = []
    for j in range(ny):
        for i in range(nx):
            fx, fy = (i + 0.5) / nx, (j + 0.5) / ny
            d = fwd + right * ((2 * fx - 1) * tw) + up * (shift + (1 - 2 * fy) * th)
            out.append((fx, fy, d.normalized()))
    return p, out


def ground_z(caster, x, y):
    hit = caster.cast(Vector((x, y, 300.0)), Vector((0, 0, -1)))
    return (hit[0].z, hit[2]) if hit else (0.0, "ground")


def candidates(caster, box):
    """Cameras on rings around the house, every STEP_DEG, at each height in EYES: the house fills FILL of the frame's
    width (and its height fits), aimed at its centre."""
    lo, hi = box
    c = (lo + hi) / 2
    hx, hy, height = (hi.x - lo.x) / 2, (hi.y - lo.y) / 2, hi.z - lo.z
    foot = lo.z
    th = math.tan(math.radians(FOV) / 2)
    tw = th * WIDTH / HEIGHT
    out = []
    for k in range(360 // STEP_DEG):
        az = k * STEP_DEG
        out_dir = Vector((math.sin(math.radians(az)), math.cos(math.radians(az)), 0))
        right = Vector((-out_dir.y, out_dir.x, 0))
        hw = abs(hx * right.x) + abs(hy * right.y)
        hd = abs(hx * out_dir.x) + abs(hy * out_dir.y)
        d0 = max(hw / (tw * FILL), height * 1.25 / (2 * th)) + hd
        for scale in (1.0, 0.8, 1.2, 0.65, 1.4):
            d = min(max(d0 * scale, 9.0), 70.0)
            x, y = c.x + out_dir.x * d, c.y + out_dir.y * d
            z, what = ground_z(caster, x, y)
            if what != "ground":  # on a roof, in a hedge or a crown
                continue
            for eye in EYES:
                cam = Vector((x, y, z + eye))
                tgt = Vector((c.x, c.y, foot + height * 0.42))
                spec = "{:.2f},{:.2f},{:.2f}>{:.2f},{:.2f},{:.2f}@{:.0f}".format(*to_kit(cam), *to_kit(tgt), FOV)
                out.append({"camera": spec, "azimuth": az, "eye": eye, "distance": round(d, 1)})
    return out


def score(caster, cand, box):
    """How well a viewpoint shows the house: its share of the frame near a third, how much of it is in sight (not
    behind hedges, trees, neighbours), glass in view, nothing close in front of the lens."""
    lo, hi = box
    origin, rs = rays(cand["camera"], *GRID)
    counts = {"shell": 0, "window": 0, "hedge": 0, "tree": 0, "neighbour": 0, "garden": 0, "ground": 0, "sky": 0}
    boxed = hidden = near = 0
    hits = []
    for fx, fy, d in rs:
        hit = caster.cast(origin, d)
        tag = hit[2] if hit else "sky"
        counts[tag] += 1
        if hit and tag not in ("shell", "window", "ground", "sky") and hit[3] < 4.0 and fy > 0.33:
            near += 1
        entry = box_entry(origin, d, lo, hi)
        if entry is not None:
            boxed += 1
            # something in front of the house: through the box's empty corners the sky or the ground is no loss
            if hit and tag not in ("shell", "window", "sky") and hit[3] < entry + 0.3:
                hidden += 1
        if tag in ("shell", "window"):
            hits.append((hit[0], hit[1]))
    n = len(rs)
    house = (counts["shell"] + counts["window"]) / n
    seen = 1 - hidden / boxed if boxed else 0.0
    glass = counts["window"] / max(1, counts["shell"] + counts["window"])
    fill = math.exp(-(((house - 0.32) / 0.15) ** 2))
    s = seen ** 2 * fill * (0.7 + 0.6 * min(glass, 0.25) / 0.25) - near / n * 2 - max(0.0, counts["neighbour"] / n - 0.15)
    cand.update({"score": round(s, 4), "house": round(house, 3), "seen": round(seen, 3), "glass": round(glass, 3),
                 "near": round(near / n, 3), "neighbours": round(counts["neighbour"] / n, 3),
                 "sky": round(counts["sky"] / n, 3)})
    return hits


def box_entry(o, d, lo, hi):
    """Where the ray from `o` along `d` enters the box (lo, hi), or None."""
    t0, t1 = 0.0, 1e9
    for a in range(3):
        if abs(d[a]) < 1e-9:
            if not lo[a] <= o[a] <= hi[a]:
                return None
            continue
        ta, tb = (lo[a] - o[a]) / d[a], (hi[a] - o[a]) / d[a]
        t0, t1 = max(t0, min(ta, tb)), min(t1, max(ta, tb))
    return t0 if t0 <= t1 else None


def compass(az):
    return ("north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west")[round(az / 45) % 8]


def sun_dir(az, el):
    a, e = math.radians(az), math.radians(el)
    return Vector((math.sin(a) * math.cos(e), math.cos(a) * math.cos(e), math.sin(e)))


def sun_table(lat, lon, year):
    """(date, hour, true azimuth, elevation) over the season's days, every quarter hour with the sun 15-55 deg up."""
    from astral import Observer
    from astral.sun import azimuth, elevation

    obs = Observer(lat, lon)
    out = []
    for month, day in DATES:
        for h in HOURS:
            t = dt.datetime(year, month, day, int(h), round((h % 1) * 60), tzinfo=ZONE)
            el = elevation(obs, t)
            if 15 <= el <= 55:
                out.append((t, azimuth(obs, t), el))
    return out


def pick_sun(caster, cand, hits, table, north):
    """The date and hour whose sun lights about three quarters of the house the camera sees (a lit side, a side in
    shade: the volume reads), high enough, never against the lens."""
    fwd = camera_frame(cand["camera"])[1]
    pts = hits[:: max(1, len(hits) // 120)]
    best = None
    for t, true_az, el in table:
        az = (true_az - north) % 360
        s = sun_dir(az, el)
        if Vector((s.x, s.y)).normalized().dot(Vector((fwd.x, fwd.y))) > math.cos(math.radians(110)):
            continue  # the sun ahead of the camera or beside it: a backlit, flat façade
        lit = sum(1 for p, n in pts if n.dot(s) > 0.1 and caster.cast(p + n * 0.02, s, 500.0) is None)
        frac = lit / max(1, len(pts))
        q = 1 - abs(frac - 0.75) - abs(el - 35) / 60
        if best is None or q > best[0]:
            best = (q, t, az, el, frac)
    if best is None:
        return None
    q, t, az, el, frac = best
    return {"sun": [round(az, 2), round(el, 2)], "when": t.strftime("%Y-%m-%d %H:%M"), "lit": round(frac, 2)}


def viewpoints(scene, trees, site, north):
    """Up to MAX_VIEWS distinct exterior viewpoints, best first, each with its sun."""
    box = house_box(scene)
    if box[0].x > box[1].x:
        return []
    c = (box[0] + box[1]) / 2
    caster = Caster(scene, trees, c)
    cands = candidates(caster, box)
    hits = {}
    for cand in cands:
        hits[cand["camera"]] = score(caster, cand, box)
    cands.sort(key=lambda x: -x["score"])
    kept = []
    for cand in cands:
        if cand["score"] <= 0 or len(kept) == MAX_VIEWS:
            continue
        if all(abs((cand["azimuth"] - k["azimuth"] + 180) % 360 - 180) >= APART_DEG for k in kept):
            kept.append(cand)
    site = site or SWITZERLAND
    table = sun_table(site["lat"], site["lon"], dt.date.today().year)
    for k in kept:
        k["side"] = compass((k["azimuth"] + north) % 360)
        k.update(pick_sun(caster, k, hits[k["camera"]], table, north) or {"sun": [still.SUN["azimuth"], still.SUN["elevation"]]})
        log(f"OUTSIDE {k['side']} {k['camera']}: score {k['score']} (house {k['house']}, seen {k['seen']}, glass "
            f"{k['glass']}), sun {k.get('when', 'default')} {k['sun']}")
    return kept


def build(blend, out_dir, ctx=None, site=None):
    """outside.blend and outside.json next to the prepared house.blend: the surroundings, the hedge mass, the
    viewpoints with their sun. `site`: {"lat", "lon"} of the plot (the sun's date and hour need them)."""
    t0 = time.time()
    bpy.ops.wm.open_mainfile(filepath=blend)
    scene = bpy.context.scene
    for o in scene.objects:  # the interiors' camera scoring hid these in house.blend
        if o.get("kind") in ("terrain",):
            o.hide_render = False
    trees = kit_trees(scene)
    area = hedge_mass(scene)
    north = 0.0
    if ctx and os.path.exists(os.path.join(ctx, "context.json")):
        more, al = surroundings(scene, ctx)
        trees += more
        north = float(al.get("rotation") or 0.0)
    views = viewpoints(scene, trees, site, north)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out_dir, "outside.blend"))
    doc = {"views": views, "trees": trees, "hedge_m2": round(area), "north": north, "context": bool(ctx),
           "seconds": round(time.time() - t0, 1)}
    with open(os.path.join(out_dir, "outside.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    log(f"outside: {len(views)} viewpoints, {len(trees)} trees, hedges {area:.0f} m2 ({doc['seconds']} s)")
    return doc


# ---------------------------------------------------------------- shoot


def load(path, prefix):
    with bpy.data.libraries.load(path, link=False) as (src, dst):
        dst.objects = [n for n in src.objects if n.startswith(prefix)]
    return [o for o in dst.objects if o is not None]


def unlinked(name, objs):
    """A collection that is only instanced (hiding a linked one from render would hide its instances too)."""
    col = bpy.data.collections.new(name)
    for o in objs:
        col.objects.link(o)
        o.location = (0, 0, 0)
    return col


def _in(node, ident):
    return next(x for x in node.inputs if x.identifier == ident)


def _out(node, ident):
    return next(x for x in node.outputs if x.identifier == ident)


def scatter(obj, col, density, size, size_random, along_normal, seed, factor=None):
    """Instances of `col`'s objects over `obj`'s faces by geometry nodes (instances stay light where a hair particle
    system keeps a path per particle), each turned at random about its up axis."""
    ng = bpy.data.node_groups.new(f"scatter-{obj.name}", "GeometryNodeTree")
    ng.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    n, link = ng.nodes, ng.links.new
    gin, gout = n.new("NodeGroupInput"), n.new("NodeGroupOutput")
    dist = n.new("GeometryNodeDistributePointsOnFaces")
    dist.inputs["Density"].default_value = density
    dist.inputs["Seed"].default_value = seed
    link(gin.outputs[0], dist.inputs["Mesh"])
    if factor:
        attr = n.new("GeometryNodeInputNamedAttribute")
        attr.data_type = "FLOAT"
        attr.inputs["Name"].default_value = factor
        link(attr.outputs["Attribute"], _in(dist, "Density Factor"))
    info = n.new("GeometryNodeCollectionInfo")
    info.inputs["Collection"].default_value = col
    info.inputs["Separate Children"].default_value = True
    info.inputs["Reset Children"].default_value = True
    inst = n.new("GeometryNodeInstanceOnPoints")
    link(dist.outputs["Points"], inst.inputs["Points"])
    link(info.outputs["Instances"], inst.inputs["Instance"])
    inst.inputs["Pick Instance"].default_value = True
    pick = n.new("FunctionNodeRandomValue")
    pick.data_type = "INT"
    _in(pick, "Min_002").default_value, _in(pick, "Max_002").default_value = 0, len(col.objects) - 1
    pick.inputs["Seed"].default_value = seed + 1
    link(_out(pick, "Value_002"), inst.inputs["Instance Index"])
    turn = n.new("FunctionNodeRandomValue")
    turn.data_type = "FLOAT_VECTOR"
    _in(turn, "Min").default_value, _in(turn, "Max").default_value = (0, 0, 0), (0, 0, 2 * math.pi)
    turn.inputs["Seed"].default_value = seed + 2
    euler = n.new("FunctionNodeEulerToRotation")
    link(_out(turn, "Value"), euler.inputs["Euler"])
    if along_normal:
        rot = n.new("FunctionNodeRotateRotation")
        rot.rotation_space = "LOCAL"
        link(dist.outputs["Rotation"], rot.inputs["Rotation"])
        link(euler.outputs["Rotation"], rot.inputs["Rotate By"])
        link(rot.outputs["Rotation"], inst.inputs["Rotation"])
    else:
        link(euler.outputs["Rotation"], inst.inputs["Rotation"])
    scale = n.new("FunctionNodeRandomValue")
    scale.data_type = "FLOAT"
    _in(scale, "Min_001").default_value = size * (1 - size_random)
    _in(scale, "Max_001").default_value = size * (1 + size_random)
    scale.inputs["Seed"].default_value = seed + 3
    link(_out(scale, "Value_001"), inst.inputs["Scale"])
    join = n.new("GeometryNodeJoinGeometry")
    link(gin.outputs[0], join.inputs[0])
    link(inst.outputs["Instances"], join.inputs[0])
    link(join.outputs[0], gout.inputs[0])
    obj.modifiers.new("scatter", "NODES").node_group = ng


def leaves(scene):
    mass = scene.objects.get("hedge-mass")
    if mass is None:
        return 0
    src = [o for o in load(os.path.join(PLANTS, TREE[0], TREE[1]), "tree_small_02_leaves_") if o.name.endswith("LOD0")]
    scatter(mass, unlinked("hedge-leaves", src), LEAVES_PER_M2, 0.42, 0.45, True, 7)
    return round(sum(p.area for p in mass.data.polygons) * LEAVES_PER_M2)


def plant_trees(scene, trees, cam):
    """A real tree at each tree of the kit and of the survey near the camera, scaled to its height and crown."""
    path = os.path.join(PLANTS, TREE[0], TREE[1])
    near = load(path, "tree_small_02_LOD0")[0]
    far = load(path, "tree_small_02_LOD1")[0]
    w0, h0 = max(near.dimensions.x, near.dimensions.y), near.dimensions.z
    n = 0
    for x, y, z, h, r in trees:
        d = math.hypot(x - cam.x, y - cam.y)
        if d > TREES_RADIUS:
            continue
        t = bpy.data.objects.new(f"tree-{n}", (near if d < 40 else far).data)
        scene.collection.objects.link(t)
        t.location = (x, y, z)
        k = 2 * r / w0
        t.scale = (k, k, h / h0)
        t.rotation_euler = (0, 0, (x * 12.9898 + y * 78.233) % (2 * math.pi))
        n += 1
    return n


def grass(scene, cam, radius=GRASS_RADIUS):
    """Grass clumps on the lawn within `radius` of the camera (the kit's ground, the survey's lawn cells), where
    nothing of the house or its garden covers it."""
    own = [o for o in scene.objects if o.get("kind") == "terrain" and o.type == "MESH" and not o.hide_render]
    lawn = lawn_material()
    for o in own:
        for slot in o.material_slots:
            slot.material = lawn
    covers = bmesh.new()
    for o in scene.objects:
        if (o.type != "MESH" or o.hide_render or o.get("kind") == "terrain" or o.name in ("hedge-mass", *GROUND)
                or o.name == "neighbours" or o.name.startswith("tree-") or indoors(o)):
            continue
        if min((o.matrix_world @ Vector(c)).z for c in o.bound_box) > 1.0:
            continue
        tmp = bmesh.new()
        tmp.from_mesh(o.data)
        tmp.transform(o.matrix_world)
        t = bpy.data.meshes.new("t")
        tmp.to_mesh(t)
        covers.from_mesh(t)
        bpy.data.meshes.remove(t)
        tmp.free()
    bvh = BVHTree.FromBMesh(covers)
    covers.free()
    bm = bmesh.new()
    for o in own + [o for o in (scene.objects.get("real-ground"),) if o]:
        tmp = bmesh.new()
        tmp.from_mesh(o.data)
        tmp.transform(o.matrix_world)
        if o.name == "real-ground":
            soft = tmp.faces.layers.int.get("soft")
            far = [f for f in tmp.faces if not f[soft] or (f.calc_center_median().xy - cam.xy).length > radius]
            bmesh.ops.delete(tmp, geom=far, context="FACES")
        t = bpy.data.meshes.new("t")
        tmp.to_mesh(t)
        bm.from_mesh(t)
        bpy.data.meshes.remove(t)
        tmp.free()
    c2 = Vector((cam.x, cam.y))
    gone = []
    for f in bm.faces:
        p = f.calc_center_median()
        if (p.xy - c2).length > radius or bvh.ray_cast(p + Vector((0, 0, 3.0)), Vector((0, 0, -1)), 3.05)[0] is not None:
            gone.append(f)
    bmesh.ops.delete(bm, geom=gone, context="FACES")
    me = bpy.data.meshes.new("grass-ground")
    bm.to_mesh(me)
    bm.free()
    # thinning out over the last third of the radius: no hard line where the grass ends
    fade = [1 - smoothstep((v.co.xy - c2).length, radius * 0.6, radius) for v in me.vertices]
    me.attributes.new("grass", "FLOAT", "POINT").data.foreach_set("value", fade)
    me.materials.append(lawn)
    em = bpy.data.objects.new("grass-ground", me)
    scene.collection.objects.link(em)
    em.location.z += 0.005
    area = sum(p.area for p in me.polygons)
    clumps = [o for o in load(os.path.join(PLANTS, GRASS[0], GRASS[1]), "grass_medium_01_")
              if o.name.endswith("LOD0") and "geonodes" not in o.name and "tiny" not in o.name]
    scatter(em, unlinked("grass-clumps", clumps), GRASS_PER_M2, 1.0, 0.4, False, 11, factor="grass")
    return round(area)


def sky(scene, az, el):
    """The physical sky and a sun lamp light the scene; the camera and reflections see a real sky (CC0, no ground)."""
    still.sun(scene, SUN_POWER, el, az, 1.0)
    nt = scene.world.node_tree
    physical = nt.nodes["Background"].inputs[0].links[0].from_socket
    env = nt.nodes.new("ShaderNodeTexEnvironment")
    env.image = bpy.data.images.load(os.path.join(PLANTS, SKY_HDRI[0], SKY_HDRI[1]))
    gain = nt.nodes.new("ShaderNodeMixRGB")
    gain.blend_type = "MULTIPLY"
    gain.inputs[0].default_value = 1.0
    gain.inputs[2].default_value = (SKY_GAIN, SKY_GAIN, SKY_GAIN, 1)
    nt.links.new(env.outputs[0], gain.inputs[1])
    path = nt.nodes.new("ShaderNodeLightPath")
    seen = nt.nodes.new("ShaderNodeMath")
    seen.operation = "MAXIMUM"
    nt.links.new(path.outputs["Is Camera Ray"], seen.inputs[0])
    nt.links.new(path.outputs["Is Glossy Ray"], seen.inputs[1])
    mix = nt.nodes.new("ShaderNodeMixRGB")
    nt.links.new(seen.outputs[0], mix.inputs[0])
    nt.links.new(physical, mix.inputs[1])
    nt.links.new(gain.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], nt.nodes["Background"].inputs[0])


def run(blend, cam_spec, out, width=WIDTH, height=HEIGHT, samples=256, sun=None):
    """Photograph the house from `cam_spec` to the JPEG `out`; returns (and writes to `<out>.json`) what it did."""
    import photo
    from post import post

    t0 = time.time()
    bpy.ops.wm.open_mainfile(filepath=blend)
    kind, devices = photo.use_gpu()
    scene = bpy.context.scene
    doc = json.load(open(os.path.join(os.path.dirname(blend), "outside.json"), encoding="utf-8"))
    still.base(scene)
    still.lamps_off(scene)
    still.glass(scene)
    photo.finish_textures(scene)
    scene.render.resolution_x, scene.render.resolution_y = width, height
    still.camera(scene, cam_spec, width, height)
    cam = scene.camera
    cam.data.clip_end = 120000.0
    az, el = (float(v) for v in (sun or (still.SUN["azimuth"], still.SUN["elevation"])))
    sky(scene, az, el)
    n_leaves = leaves(scene)
    n_trees = plant_trees(scene, doc.get("trees", []), cam.location)
    lawn = grass(scene, cam.location)
    seconds = {"setup": round(time.time() - t0, 1)}
    log(f"outside: {n_leaves} leaf clusters, {n_trees} trees, {lawn} m2 of grass; sun {az:.0f}/{el:.0f}")
    t = time.time()
    scene.cycles.samples = samples
    vs = scene.view_settings
    vs.view_transform, vs.look, vs.exposure = "AgX", LOOK, 0.0
    bpy.ops.render.render()
    seconds["render"] = round(time.time() - t, 1)
    with tempfile.TemporaryDirectory() as tmp:
        raw = os.path.join(tmp, "raw.png")
        bpy.data.images["Render Result"].save_render(raw, scene=scene)
        post(raw, out)
    seconds["total"] = round(time.time() - t0, 1)
    info = {"room": None, "use": "exterior", "camera": cam_spec, "sun": f"{az:.1f}/{el:.1f}",
            "sun_deg": [round(az, 2), round(el, 2)], "room_lamps": False, "removed": [], "pieces": [], "lamps": [],
            "device": f"{kind}: {', '.join(devices)}", "seconds": seconds}
    with open(out + ".json", "w", encoding="utf-8") as f:
        json.dump(info, f, indent=1, ensure_ascii=False)
    log(f"outside: {width}x{height} {samples} samples {seconds['render']:.0f} s; total {seconds['total']:.0f} s -> {out}")
    return info
