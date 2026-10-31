"""A listing photo of a room of any housegen house, with no per-room settings: the recipe the dining room and
the bedroom were tuned with by hand (still.py, dress.py), with every choice made from the house itself.

    uv run --no-project --python 3.11 --with bpy --with numpy --with pillow python photo.py \
        <house.blend> <rooms.json> "<camera>" <out.png> [w h samples]

<camera>: "x,y,z>tx,ty,tz@vfov" in scene metres (x east, y up, z south), as the owner frames it in the viewer.
The only input besides the export (house.blend from export.py's GLB, rooms.json). What it decides:
- the storey: the room under the camera (rooms.json); everything below is done for that storey;
- finishes: wood floors as the plank scan in their own colour, plaster relief on walls and ceilings, linen
  curtains, light exterior paving seen through the glass as stone (ground) or a timber deck (upper floors);
- dressing, from the kit's own pieces (userData name + footprint): rugs -> a boucle rug of the same size, plants
  -> CC0 scans, wall art -> our print, armchairs -> the CC0 lounge chair with a cloth throw, beds -> our bed with
  cloth-simulated bedding (made/bedset.py, cached per size), cushions on sofas, a runner and eucalyptus on dining
  tables, a board and a fruit bowl on the free stretch of the kitchen counter;
- the sun: a real sun position for the site's latitude (LAT, default 46.5) that puts sun patches in the room
  the camera sees (camera rays, then shadow rays toward each candidate);
- the grade: AgX High Contrast; exposure and white balance from a preview, to a target median of the room and
  a target warmth of its walls.
"""

import json
import math
import os
import subprocess
import sys
import time

os.environ.setdefault("VIEW_GAIN", "2.2")  # still.py reads it on import

import bpy  # noqa: E402
import addon_utils  # noqa: E402
import numpy as np  # noqa: E402
from mathutils import Matrix, Vector  # noqa: E402
from PIL import Image  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dress  # noqa: E402
import still  # noqa: E402
from dress import model, place, principled  # noqa: E402
from post import post  # noqa: E402

LAT = float(os.environ.get("LAT", "46.5"))
SKY = 0.55  # the hand-tuned rooms' value (0.5-0.6)
# the sun lamp against the sky: at 16 a patch on the floor or a wall is 5-8x the sky's light around it (5.5, the
# hand-tuned rooms' value, gave 2-3x: patches the judges did not see, "flat light")
SUN_POWER = float(os.environ.get("PHOTO_SUN_POWER", "16"))
LAMPS = float(os.environ.get("PHOTO_LAMPS", "0.4"))  # the lamps of a room without sun, x their full strength
THUMBS = int(os.environ.get("THUMBS", "10"))  # candidate suns rendered as thumbnails (0: the ray count alone)
LOOK = "AgX - High Contrast"
TARGET_MEDIAN, TARGET_WALL_B, TARGET_WALL_A = 155.0, 4.0, 0.5  # sRGB luminance 0-255 of the room; CIELAB b*, a* of its walls
CLIP_MAX = float(os.environ.get("CLIP_MAX", "0.03"))  # at most this share of the frame at white...
CLIP_PULL = 0.5  # ...by lowering the median's exposure at most this many stops (darker than that reads as a grey veil)
CACHE = os.path.join(HERE, "work", "cache")
ART = os.path.join(HERE, "made", "art.glb")  # 0.62 x 0.82 m, facing +y
EUCALYPTUS = os.path.join(HERE, "made", "eucalyptus.glb")
THROW = os.path.join(HERE, "made", "throw.glb")
BED_SIZES = (0.9, 1.2, 1.4, 1.6, 1.8)


def log(*a):
    print("PHOTO", *a, flush=True)


# ---------------------------------------------------------------- geometry

def to_scene(v):
    return (v.x, v.z, -v.y)


def inside(poly, x, z):
    c = False
    for i in range(len(poly)):
        (x1, z1), (x2, z2) = poly[i], poly[i - 1]
        if (z1 > z) != (z2 > z) and x < (x2 - x1) * (z - z1) / (z2 - z1) + x1:
            c = not c
    return c


def family(o):
    return [o, *o.children_recursive]


def hide(o):
    for c in family(o):
        c.hide_render = True


def box(o):
    ps = [c.matrix_world @ Vector(v) for c in family(o) if c.type == "MESH" for v in c.bound_box]
    if not ps:
        t = o.matrix_world.translation
        return t.copy(), t.copy()
    return Vector([min(p[i] for p in ps) for i in range(3)]), Vector([max(p[i] for p in ps) for i in range(3)])


def kinds(o):
    out = set()
    while o is not None:
        out.add(o.get("kind"))
        o = o.parent
    return out


def name(o):
    return str(o.get("name", ""))


def turn(o):
    return o.matrix_world.to_euler().z


def frame_of(o, floor_z):
    """The piece's own frame (Blender): origin at its footprint centre on the floor, facing -y."""
    t = o.matrix_world.translation
    return Matrix.Translation((t.x, t.y, floor_z)) @ Matrix.Rotation(turn(o), 4, "Z")


def material_at(obj, face):
    me = obj.data
    if obj.type != "MESH" or face < 0 or face >= len(me.polygons):
        return None, None
    slot = me.polygons[face].material_index
    if slot >= len(obj.material_slots):
        return None, None
    return obj.material_slots[slot].material, slot


def trace(scene, dg, origin, direction, far=200.0):
    """The first surface a ray meets, through clear glass and what the render hides; None for the sky."""
    o = origin.copy()
    for _ in range(12):
        ok, loc, normal, face, obj, _ = scene.ray_cast(dg, o, direction, distance=far)
        if not ok:
            return None
        orig = getattr(obj, "original", obj)
        m, _ = material_at(obj, face)
        if orig.hide_render or (m is not None and m.name.startswith("archviz-glass")):
            o = loc + direction * 1e-3
            continue
        return loc, normal, orig, face
    return None


# ---------------------------------------------------------------- the room

def room_at(rooms, x, y, z):
    found = [r for r in rooms if r["y"] - 0.05 <= y <= r["y"] + 3.0 and inside(r["polygon"], x, z)]
    return max(found, key=lambda r: r["y"]) if found else None


def storey_pieces(scene, sy):
    out = []
    for o in scene.objects:
        if o.get("kind") == "furniture" and not o.hide_render and sy - 0.1 <= o.matrix_world.translation.z <= sy + 2.3:
            out.append(o)
    return out


def ground(scene):
    """The export's terrain (flat pale green without its shader) and the garden's low pieces more than 3 m from
    the house go: the HDRI's own ground shows (still.photo_ground, from the house's walls instead of numbers)."""
    walls = [o for o in scene.objects if o.type == "MESH" and "wall" in kinds(o)]
    lo = Vector([min((o.matrix_world @ Vector(c))[i] for o in walls for c in o.bound_box) for i in range(3)])
    hi = Vector([max((o.matrix_world @ Vector(c))[i] for o in walls for c in o.bound_box) for i in range(3)])
    n = 0
    for o in scene.objects:
        if o.get("kind") == "terrain":
            hide(o)
    for o in scene.objects:
        if o.type != "MESH" or o.hide_render:
            continue
        ps = [o.matrix_world @ Vector(c) for c in o.bound_box]
        if max(p.z for p in ps) < 2.0 and (min(p.x for p in ps) > hi.x + 3 or max(p.x for p in ps) < lo.x - 3
                                            or min(p.y for p in ps) > hi.y + 3 or max(p.y for p in ps) < lo.y - 3):
            o.hide_render = True
            n += 1
    log(f"ground: terrain and {n} garden pieces hidden")


PLANK = (2.1, 0.19)  # length and width of the floor's planks, metres


def plank_floor(m, turn_deg=90):
    """A wood floor laid plank by plank: long planks (PLANK) in staggered rows, each cut from its own place in a
    seamless oak scan (Poly Haven oak_veneer_01, CC0, 1.83 m; its grain along the plank), its own tone (±7 %) and
    gloss, micro-bevelled V-joints at the long sides and the ends. Tinted to the export's floor colour."""
    q = principled(m)
    nt = m.node_tree
    old = q.inputs["Base Color"].links[0].from_node.image
    d = os.path.join(dress.TEX, "oak_veneer_01")
    diff = dress.image(os.path.join(d, "diff.jpg"))
    ratio = np.clip(dress.mean_linear(old) / dress.mean_linear(diff), 0.3, 3.0)
    length, width = PLANK
    geo = node(nt, "ShaderNodeNewGeometry")
    rot = node(nt, "ShaderNodeMapping")
    rot.inputs["Rotation"].default_value[2] = math.radians(turn_deg)
    nt.links.new(geo.outputs["Position"], rot.inputs["Vector"])
    xyz = node(nt, "ShaderNodeSeparateXYZ")
    nt.links.new(rot.outputs["Vector"], xyz.inputs[0])

    def math_(op, *args):
        n = node(nt, "ShaderNodeMath", _operation=op)
        for i, v in enumerate(args):
            if isinstance(v, (int, float)):
                n.inputs[i].default_value = v
            else:
                nt.links.new(v, n.inputs[i])
        return n.outputs[0]

    along, across = xyz.outputs["X"], xyz.outputs["Y"]
    row = math_("FLOOR", math_("DIVIDE", across, width))
    stagger = node(nt, "ShaderNodeTexWhiteNoise", _noise_dimensions="1D")
    nt.links.new(row, stagger.inputs["W"])
    a2 = math_("ADD", along, math_("MULTIPLY", stagger.outputs["Value"], length))
    col = math_("FLOOR", math_("DIVIDE", a2, length))
    cell = node(nt, "ShaderNodeCombineXYZ")
    nt.links.new(col, cell.inputs["X"])
    nt.links.new(row, cell.inputs["Y"])
    rnd = node(nt, "ShaderNodeTexWhiteNoise", _noise_dimensions="3D")
    nt.links.new(cell.outputs["Vector"], rnd.inputs["Vector"])
    la = math_("SUBTRACT", a2, math_("MULTIPLY", col, length))  # 0..length along the plank
    lb = math_("SUBTRACT", across, math_("MULTIPLY", row, width))  # 0..width across it
    # the scan: grain (its v) along the plank, from a place of the plank's own
    sc = node(nt, "ShaderNodeSeparateColor")
    nt.links.new(rnd.outputs["Color"], sc.inputs[0])
    uv = node(nt, "ShaderNodeCombineXYZ")
    nt.links.new(math_("ADD", math_("DIVIDE", lb, 1.83), sc.outputs["Red"]), uv.inputs["X"])
    nt.links.new(math_("ADD", math_("DIVIDE", la, 1.83), sc.outputs["Green"]), uv.inputs["Y"])
    vec = uv.outputs["Vector"]
    tex = dress.tex_node(nt, diff, vec)
    # the plank's tone: the floor's colour, ±7 %, a slight warm or grey drift
    tone = node(nt, "ShaderNodeVectorMath", _operation="MULTIPLY_ADD")
    tone.inputs[1].default_value = (0.14, 0.14, 0.14)
    tone.inputs[2].default_value = tuple(0.93 * r for r in ratio)
    nt.links.new(rnd.outputs["Value"], tone.inputs[0])
    drift = node(nt, "ShaderNodeVectorMath", _operation="MULTIPLY_ADD")
    drift.inputs[1].default_value = (0.05, 0.02, -0.04)
    drift.inputs[2].default_value = (0.975, 0.99, 1.02)
    nt.links.new(sc.outputs["Blue"], drift.inputs[0])
    c1 = node(nt, "ShaderNodeVectorMath", _operation="MULTIPLY")
    nt.links.new(tex.outputs["Color"], c1.inputs[0])
    nt.links.new(tone.outputs[0], c1.inputs[1])
    c2 = node(nt, "ShaderNodeVectorMath", _operation="MULTIPLY")
    nt.links.new(c1.outputs[0], c2.inputs[0])
    nt.links.new(drift.outputs[0], c2.inputs[1])
    # the joints: distance to the plank's edge in metres; the V-groove is dark and its bevel catches the light
    e = math_("MINIMUM", math_("MINIMUM", la, math_("SUBTRACT", length, la)),
              math_("MINIMUM", lb, math_("SUBTRACT", width, lb)))
    groove = node(nt, "ShaderNodeMapRange", _interpolation_type="SMOOTHSTEP")
    groove.inputs["From Min"].default_value, groove.inputs["From Max"].default_value = 0.0003, 0.0011
    groove.inputs["To Min"].default_value, groove.inputs["To Max"].default_value = 0.55, 1.0
    nt.links.new(e, groove.inputs["Value"])
    c3 = node(nt, "ShaderNodeVectorMath", _operation="SCALE")
    nt.links.new(c2.outputs[0], c3.inputs[0])
    nt.links.new(groove.outputs["Result"], [x for x in c3.inputs if x.name == "Scale"][0])
    for lk in list(q.inputs["Base Color"].links):
        nt.links.remove(lk)
    nt.links.new(c3.outputs[0], q.inputs["Base Color"])
    # gloss: the scan's roughness in FLOOR_ROUGH, each plank x0.85-1.15, matte in the groove
    rough = dress.rough_map(nt, dress.image(os.path.join(d, "rough.jpg"), True), vec, *dress.FLOOR_ROUGH)
    rr = math_("MULTIPLY", rough, math_("MULTIPLY_ADD", sc.outputs["Green"], 0.3, 0.85))
    rj = math_("MAXIMUM", rr, math_("SUBTRACT", 1.0, groove.outputs["Result"]))
    for lk in list(q.inputs["Roughness"].links):
        nt.links.remove(lk)
    nt.links.new(rj, q.inputs["Roughness"])
    # relief: the grain, then the bevel
    bevel = node(nt, "ShaderNodeMapRange", _interpolation_type="SMOOTHSTEP")
    bevel.inputs["From Min"].default_value, bevel.inputs["From Max"].default_value = 0.0003, 0.0025
    nt.links.new(e, bevel.inputs["Value"])
    bump = node(nt, "ShaderNodeBump", Distance=0.0008, Strength=1.0)
    nt.links.new(bevel.outputs["Result"], bump.inputs["Height"])
    nt.links.new(dress.normal_map(nt, dress.image(os.path.join(d, "nor_gl.jpg"), True), vec, 0.5), bump.inputs["Normal"])
    for lk in list(q.inputs["Normal"].links):
        nt.links.remove(lk)
    nt.links.new(bump.outputs["Normal"], q.inputs["Normal"])
    log(f"floor: {m.name} in {length} x {width} m planks, tinted by {np.round(ratio, 2)}")


def floors(scene, rooms, sy, room):
    """Wood floors of the storey: the plank scan in the floor's own colour, planks along the room's long side."""
    xs, zs = [p[0] for p in room["polygon"]], [p[1] for p in room["polygon"]]
    along_z = (max(zs) - min(zs)) >= (max(xs) - min(xs))
    done = set()
    for r in rooms:
        if abs(r["y"] - sy) > 0.1:
            continue
        cx = sum(p[0] for p in r["polygon"]) / len(r["polygon"])
        cz = sum(p[1] for p in r["polygon"]) / len(r["polygon"])
        pts = [(cx, cz)] + [(0.7 * p[0] + 0.3 * cx, 0.7 * p[1] + 0.3 * cz) for p in r["polygon"]]
        for x, z in pts:
            if not inside(r["polygon"], x, z):
                continue
            try:
                m = dress.floor_material(scene, (x, sy, z))
            except RuntimeError:
                continue
            if m.name in done:
                break
            done.add(m.name)
            img = principled(m).inputs["Base Color"].links[0].from_node.image
            c = dress.mean_linear(img)
            if c[0] > 1.25 * c[2]:  # warm: wood (tiles and stone are grey)
                if os.environ.get("FLOOR") == "scan":
                    dress.oak_floor(m, 90 if along_z else 0)
                else:
                    plank_floor(m, 90 if along_z else 0)
            break
    log(f"floors: {sorted(done)}")


def inner_walls(scene, rooms):
    """The room side of the outside walls in the partitions' plaster. The export gives a wall one material inside and
    out, so behind a coloured façade the room's wall came out in the façade's colour, its texture stretched (the
    bedroom's "smeared dark panelling"). A face is on the room side when a step off it, along its normal or, for
    reveals and soffits, across it, lands in a room of its storey."""
    areas = {}
    for o in scene.objects:
        if o.type != "MESH" or "partition" not in kinds(o) or kinds(o) & {"interiorDoor", "door", "window", "skirting"}:
            continue
        for p in o.data.polygons:
            m, _ = material_at(o, p.index)
            if m is not None:
                areas[m] = areas.get(m, 0.0) + p.area
    if not areas:
        return
    plaster = max(areas, key=areas.get)

    def in_room(v):
        return any(r["y"] - 0.05 <= v[1] <= r["y"] + 2.6 and inside(r["polygon"], v[0], v[2]) for r in rooms)

    faces = 0
    for o in scene.objects:
        k = kinds(o)
        if o.type != "MESH" or not {"wall", "perimeter"} <= k or k & {"window", "door", "partition"}:
            continue
        mw, rot = o.matrix_world, o.matrix_world.to_3x3()
        room_side = []
        for p in o.data.polygons:
            c = Vector(to_scene(mw @ p.center))
            n = Vector(to_scene(rot @ p.normal)).normalized()
            steps = [c + n * 0.03]
            h = Vector((n.z, 0.0, -n.x))  # across the face, horizontal
            axes = [Vector(a) for a in ((1, 0, 0), (0, 0, 1))] if h.length < 0.1 else [h.normalized()]
            steps += [c + a * s for a in axes for s in (0.3, -0.3)]
            if any(in_room(s) for s in steps):
                room_side.append(p.index)
        if not room_side:
            continue
        if o.data.users > 1:
            o.data = o.data.copy()
        if plaster.name not in o.data.materials:
            o.data.materials.append(plaster)
        i = list(o.data.materials).index(plaster)
        for f in room_side:
            o.data.polygons[f].material_index = i
        faces += len(room_side)
    log(f"inner walls: {faces} faces of the outside walls in {plaster.name}")


def node(nt, kind, **inputs):
    n = nt.nodes.new(kind)
    for k, v in inputs.items():
        if k.startswith("_"):
            setattr(n, k[1:], v)
        else:
            n.inputs[k].default_value = v
    return n


def glaze_tiles(scene):
    """Ceramic wall tiles that read as tiles: each tile its own tone (±2.5 %), gloss and a slight tilt, so the
    reflections break at the joints; a faint wave in the glaze; recessed, matte grout joints. The export's tile is a
    flat colour with a grout line (its UVs in metres, a Mapping to tiles)."""
    done = []
    for o in scene.objects:
        if o.type != "MESH" or "wallTiles" not in kinds(o):
            continue
        for s in o.material_slots:
            m = s.material
            if m is None or m.name in done or not m.use_nodes:
                continue
            nt = m.node_tree
            p = principled(m)
            bc = p.inputs["Base Color"]
            if not bc.is_linked:
                continue
            img = bc.links[0].from_node
            maps = [lk.from_node for lk in img.inputs["Vector"].links if lk.from_node.bl_idname == "ShaderNodeMapping"]
            if not maps:
                continue
            mp = maps[0]
            w, h = (1 / max(1e-3, mp.inputs["Scale"].default_value[i]) for i in (0, 1))
            cell = node(nt, "ShaderNodeVectorMath", _operation="FLOOR")
            nt.links.new(mp.outputs["Vector"], cell.inputs[0])
            rnd = node(nt, "ShaderNodeTexWhiteNoise", _noise_dimensions="3D")
            nt.links.new(cell.outputs["Vector"], rnd.inputs["Vector"])
            # the joint: distance to the tile's edge in metres, 1 in the joint
            fr = node(nt, "ShaderNodeVectorMath", _operation="FRACTION")
            nt.links.new(mp.outputs["Vector"], fr.inputs[0])
            sep = node(nt, "ShaderNodeSeparateXYZ")
            nt.links.new(fr.outputs["Vector"], sep.inputs[0])
            edge = []
            for axis, size in (("X", w), ("Y", h)):
                inv = node(nt, "ShaderNodeMath", _operation="SUBTRACT")
                inv.inputs[0].default_value = 1.0
                nt.links.new(sep.outputs[axis], inv.inputs[1])
                mn = node(nt, "ShaderNodeMath", _operation="MINIMUM")
                nt.links.new(sep.outputs[axis], mn.inputs[0])
                nt.links.new(inv.outputs[0], mn.inputs[1])
                mt = node(nt, "ShaderNodeMath", _operation="MULTIPLY")
                mt.inputs[1].default_value = size
                nt.links.new(mn.outputs[0], mt.inputs[0])
                edge.append(mt)
            e = node(nt, "ShaderNodeMath", _operation="MINIMUM")
            nt.links.new(edge[0].outputs[0], e.inputs[0])
            nt.links.new(edge[1].outputs[0], e.inputs[1])
            joint = node(nt, "ShaderNodeMapRange", _interpolation_type="SMOOTHSTEP")
            joint.inputs["From Min"].default_value, joint.inputs["From Max"].default_value = 0.0012, 0.0024
            joint.inputs["To Min"].default_value, joint.inputs["To Max"].default_value = 1.0, 0.0
            nt.links.new(e.outputs[0], joint.inputs["Value"])
            # tone
            # each tile its tone (± TILE_TONE) and a slight warm or cool drift
            tone = node(nt, "ShaderNodeVectorMath", _operation="MULTIPLY_ADD")
            tone.inputs[1].default_value = (2 * TILE_TONE,) * 3
            tone.inputs[2].default_value = (1 - TILE_TONE,) * 3
            nt.links.new(rnd.outputs["Value"], tone.inputs[0])
            drift = node(nt, "ShaderNodeVectorMath", _operation="MULTIPLY_ADD")
            drift.inputs[1].default_value = (0.012, 0.0, -0.012)
            drift.inputs[2].default_value = (-0.006, 0.0, 0.006)
            nt.links.new(rnd.outputs["Color"], drift.inputs[0])
            hue = node(nt, "ShaderNodeVectorMath", _operation="ADD")
            nt.links.new(tone.outputs[0], hue.inputs[0])
            nt.links.new(drift.outputs[0], hue.inputs[1])
            mix = node(nt, "ShaderNodeMix", _data_type="RGBA", _blend_type="MULTIPLY")
            mix.inputs["Factor"].default_value = 1.0
            nt.links.new(img.outputs["Color"], [x for x in mix.inputs if x.name == "A" and x.type == "RGBA"][0])
            nt.links.new(hue.outputs[0], [x for x in mix.inputs if x.name == "B" and x.type == "RGBA"][0])
            nt.links.new([x for x in mix.outputs if x.type == "RGBA"][0], bc)
            # gloss: 0.04-0.12 on the glaze, 0.85 in the joint
            sc = node(nt, "ShaderNodeSeparateColor")
            nt.links.new(rnd.outputs["Color"], sc.inputs[0])
            gl = node(nt, "ShaderNodeMath", _operation="MULTIPLY_ADD")
            gl.inputs[1].default_value, gl.inputs[2].default_value = 0.08, 0.04
            nt.links.new(sc.outputs["Green"], gl.inputs[0])
            rough = node(nt, "ShaderNodeMix", _data_type="FLOAT")
            nt.links.new(joint.outputs[0], rough.inputs["Factor"])
            nt.links.new(gl.outputs[0], [x for x in rough.inputs if x.name == "A" and x.type == "VALUE"][0])
            [x for x in rough.inputs if x.name == "B" and x.type == "VALUE"][0].default_value = 0.85
            nt.links.new([x for x in rough.outputs if x.type == "VALUE"][0], p.inputs["Roughness"])
            # the tilt: the export's normal (its bevel at the joint) plus a random 1-2 degrees per tile
            off = node(nt, "ShaderNodeVectorMath", _operation="MULTIPLY_ADD")
            off.inputs[1].default_value = (0.05, 0.05, 0.05)
            off.inputs[2].default_value = (-0.025, -0.025, -0.025)
            nt.links.new(rnd.outputs["Color"], off.inputs[0])
            base = p.inputs["Normal"].links[0].from_socket if p.inputs["Normal"].is_linked else \
                node(nt, "ShaderNodeNewGeometry").outputs["Normal"]
            add = node(nt, "ShaderNodeVectorMath", _operation="ADD")
            nt.links.new(base, add.inputs[0])
            nt.links.new(off.outputs[0], add.inputs[1])
            nrm = node(nt, "ShaderNodeVectorMath", _operation="NORMALIZE")
            nt.links.new(add.outputs[0], nrm.inputs[0])
            # the joint, then a cushioned edge rising over 7 mm (it catches a line of light), then the glaze's wave
            cushion = node(nt, "ShaderNodeMapRange", _interpolation_type="SMOOTHSTEP")
            cushion.inputs["From Min"].default_value, cushion.inputs["From Max"].default_value = 0.0012, 0.007
            nt.links.new(e.outputs[0], cushion.inputs["Value"])
            sunk = node(nt, "ShaderNodeBump", Strength=1.0, Distance=0.002)
            nt.links.new(cushion.outputs["Result"], sunk.inputs["Height"])
            nt.links.new(nrm.outputs[0], sunk.inputs["Normal"])
            wave = node(nt, "ShaderNodeTexNoise", Scale=9.0, Detail=1.0)
            uv = node(nt, "ShaderNodeTexCoord")
            nt.links.new(uv.outputs["Object"], wave.inputs["Vector"])
            bump = node(nt, "ShaderNodeBump", Strength=0.25, Distance=0.002)
            nt.links.new(wave.outputs["Fac"], bump.inputs["Height"])
            nt.links.new(sunk.outputs["Normal"], bump.inputs["Normal"])
            nt.links.new(bump.outputs["Normal"], p.inputs["Normal"])
            p.inputs["Metallic"].default_value = 0.0
            for lk in list(p.inputs["Metallic"].links):
                nt.links.remove(lk)
            done.append(m.name)
    log(f"tiles: glazed {done}")


MOTTLE = float(os.environ.get("MOTTLE", "0.025"))  # the paint's slow unevenness, ± this share of its tone


def paint(scene):
    """Paint that is not a render's perfect white. Walls and ceilings get a faint, slow unevenness in tone (± 2.5 %,
    blobs of about a metre, in world space so no wall repeats another), and the ceilings get the walls' plaster
    relief (dress.plaster leaves the export's ceilings out: they came out as one smooth gradient)."""
    pl = os.path.join(dress.TEX, "plaster_grey_04")
    nor, rough = dress.image(os.path.join(pl, "nor_gl.jpg"), True), dress.image(os.path.join(pl, "rough.jpg"), True)
    done = {}
    for kind in ("wall", "partition", "slab", "ceiling"):
        for o in dress.objects_of(scene, kind):
            if kinds(o) & {"window", "door", "interiorDoor", "skirting", "wallTiles"}:
                continue
            for s in o.material_slots:
                m = s.material
                if m is None or m.name in done or m.name.startswith("archviz") or not m.use_nodes:
                    continue
                nt = m.node_tree
                q = principled(m)
                col = q.inputs["Base Color"]
                if q.inputs["Alpha"].default_value < 1 or (not col.is_linked and min(col.default_value[:3]) < 0.5):
                    continue
                if kind == "ceiling" and not q.inputs["Normal"].is_linked:
                    tc = nt.nodes.new("ShaderNodeTexCoord")
                    mp = node(nt, "ShaderNodeMapping", Scale=(1 / 1.5,) * 3)
                    nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])
                    nt.links.new(dress.normal_map(nt, nor, mp.outputs["Vector"], dress.RELIEF, box=True), q.inputs["Normal"])
                    nt.links.new(dress.rough_map(nt, rough, mp.outputs["Vector"], 0.78, 0.92, box=True), q.inputs["Roughness"])
                geo = node(nt, "ShaderNodeNewGeometry")
                blobs = node(nt, "ShaderNodeTexNoise", Scale=0.9, Detail=3.0, Roughness=0.55)
                nt.links.new(geo.outputs["Position"], blobs.inputs["Vector"])
                k = node(nt, "ShaderNodeMapRange", **{"From Min": 0.3, "From Max": 0.7, "To Min": 1 - MOTTLE, "To Max": 1 + MOTTLE * 0.4})
                nt.links.new(blobs.outputs["Fac"], k.inputs["Value"])
                mix = node(nt, "ShaderNodeMix", _data_type="RGBA", _blend_type="MULTIPLY")
                mix.inputs["Factor"].default_value = 1.0
                a_in = [x for x in mix.inputs if x.name == "A" and x.type == "RGBA"][0]
                b_in = [x for x in mix.inputs if x.name == "B" and x.type == "RGBA"][0]
                if col.is_linked:
                    nt.links.new(col.links[0].from_socket, a_in)
                else:
                    a_in.default_value = col.default_value
                nt.links.new(k.outputs["Result"], b_in)
                nt.links.new([x for x in mix.outputs if x.type == "RGBA"][0], col)
                done[m.name] = kind
    log(f"paint: {done}")


CEILING = os.environ.get("CEILING", "plaster")  # or "concrete": the references' exposed concrete


def concrete_ceilings(scene):
    """Exposed concrete ceilings (the references' Swiss Sichtbeton): the formwork-concrete scan (Poly Haven
    concrete_wall_008, CC0, 2.7 m a tile) at a light warm grey, its own relief and roughness. A finish choice, not
    the export's: CEILING=concrete."""
    d = os.path.join(dress.TEX, "concrete_wall_008")
    diff = dress.image(os.path.join(d, "diff.jpg"))
    gain = np.array((0.50, 0.49, 0.47)) / np.maximum(dress.mean_linear(diff), 1e-3)
    done = []
    for o in dress.objects_of(scene, "ceiling"):
        for s in o.material_slots:
            m = s.material
            if m is None or m.name in done or m.name.startswith("archviz") or not m.use_nodes:
                continue
            nt = m.node_tree
            q = principled(m)
            tc = node(nt, "ShaderNodeTexCoord")
            mp = node(nt, "ShaderNodeMapping", Scale=(1 / 2.7,) * 3)
            nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])
            col = dress.tex_node(nt, diff, mp.outputs["Vector"], box=True)
            tint = node(nt, "ShaderNodeMix", _data_type="RGBA", _blend_type="MULTIPLY")
            tint.inputs["Factor"].default_value = 1.0
            nt.links.new(col.outputs["Color"], [x for x in tint.inputs if x.name == "A" and x.type == "RGBA"][0])
            [x for x in tint.inputs if x.name == "B" and x.type == "RGBA"][0].default_value = (*gain, 1)
            for lk in list(q.inputs["Base Color"].links):
                nt.links.remove(lk)
            nt.links.new([x for x in tint.outputs if x.type == "RGBA"][0], q.inputs["Base Color"])
            nt.links.new(dress.normal_map(nt, dress.image(os.path.join(d, "nor_gl.jpg"), True), mp.outputs["Vector"], 0.8, box=True),
                         q.inputs["Normal"])
            nt.links.new(dress.rough_map(nt, dress.image(os.path.join(d, "rough.jpg"), True), mp.outputs["Vector"], 0.62, 0.88, box=True),
                         q.inputs["Roughness"])
            done.append(m.name)
    log(f"ceilings: concrete {done}")


TILE_TONE = float(os.environ.get("TILE_TONE", "0.05"))


SANITARY = ("bathtub", "basin", "wc", "bidet", "shower", "sink", "toilet")


def sanitaryware(scene, pieces):
    """The kit's sanitaryware: a fired glaze (glossy, faint unevenness) and chrome, both with rounded edges (a 3 mm
    bevel in the shading) that catch a highlight, as moulded and pressed pieces do."""
    done = {}
    for o in pieces:
        if not any(name(o).lower().startswith(s) for s in SANITARY):
            continue
        for c in family(o):
            if c.type != "MESH":
                continue
            for s in c.material_slots:
                m = s.material
                if m is None or m.name in done or m.name.startswith(("archviz", "mirror")) or not m.use_nodes:
                    continue
                p = principled(m)
                col = p.inputs["Base Color"]
                if col.is_linked or p.inputs["Metallic"].is_linked:
                    continue
                rgb = col.default_value[:3]
                metal = p.inputs["Metallic"].default_value > 0.5
                if not metal and min(rgb) < 0.85:
                    continue
                nt = m.node_tree
                bev = node(nt, "ShaderNodeBevel", Radius=0.003)
                bev.samples = 6
                nt.links.new(bev.outputs["Normal"], p.inputs["Normal"])
                if metal:
                    col.default_value = (0.86, 0.87, 0.88, 1)
                    p.inputs["Roughness"].default_value = 0.06
                    done[m.name] = "chrome"
                    continue
                wave = node(nt, "ShaderNodeTexNoise", Scale=14.0, Detail=2.0)
                r = node(nt, "ShaderNodeMapRange", **{"To Min": 0.035, "To Max": 0.09})
                nt.links.new(wave.outputs["Fac"], r.inputs["Value"])
                nt.links.new(r.outputs["Result"], p.inputs["Roughness"])
                p.inputs["Coat Weight"].default_value = 0.3
                p.inputs["Coat Roughness"].default_value = 0.03
                done[m.name] = "glaze"
    log(f"sanitaryware: {done}")


# ---------------------------------------------------------------- dressing

def swap_rugs(scene, pieces):
    n = 0
    for o in pieces:
        if name(o) != "rug":
            continue
        lo, _ = box(o)
        w, d = o["footprint"]
        t = o.matrix_world.translation
        hide(o)
        r = dress.rug(scene, (t.x, -t.y), (w, d), floor=lo.z)
        r.rotation_euler[2] = turn(o)
        n += 1
    return n


def swap_plants(scene, pieces, sy):
    n = 0
    for o in pieces:
        if not name(o).startswith("plant-"):
            continue
        lo, hi = box(o)
        h = hi.z - lo.z
        t = o.matrix_world.translation
        hide(o)
        at, spin = (t.x, lo.z, -t.y), (47 * n + 20) % 360
        if lo.z - sy < 0.1 and h >= 0.8:
            place(scene, model("pachira_aquatica_01"), at, height=max(h, 1.35), turn_deg=spin, tallest=True)
        else:
            place(scene, model("potted_plant_02"), at, height=max(h, 0.6) if lo.z - sy < 0.1 else h, turn_deg=spin)
        n += 1
    return n


def swap_art(scene, pieces):
    n = 0
    for o in pieces:
        nm = name(o).lower()
        if "wall art" not in nm and "wall-art" not in nm:
            continue
        lo, hi = box(o)
        w, h = o["footprint"][0], hi.z - lo.z
        s = min(w / 0.62, h / 0.82, 1.2)
        c = (lo + hi) / 2
        hide(o)
        place(scene, ART, (c.x, lo.z + (h - 0.82 * s) / 2, -c.y), height=0.82 * s, turn_deg=math.degrees(turn(o)) + 180)
        n += 1
    return n


def swap_armchairs(scene, pieces):
    n = 0
    for o in pieces:
        if "armchair" not in name(o).lower():
            continue
        lo, _ = box(o)
        t = o.matrix_world.translation
        hide(o)
        root, _ = place(scene, model("mid_century_lounge_chair"), (t.x, lo.z, -t.y), turn_deg=math.degrees(turn(o)))
        if n == 0:  # one throw in the room, not one per chair
            dress.attach(root, THROW, dress.wool_material())
        n += 1
    return n


def bedset(w, length):
    path = os.path.join(CACHE, f"bedset-{round(w * 100)}x{round(length * 100)}.glb")
    if not os.path.exists(path):
        subprocess.run([sys.executable, os.path.join(HERE, "made", "bedset.py"), str(w), str(length), path], check=True)
    return path


def recolour(root, material):
    for c in root.children_recursive:
        if c.type == "MESH":
            c.data = c.data.copy()
            c.data.materials.clear()
            c.data.materials.append(material)


def velvet(rgb):
    m = bpy.data.materials.new("archviz-velvet")
    q = principled(m)
    q.inputs["Base Color"].default_value = (*rgb, 1)
    q.inputs["Roughness"].default_value = 0.75
    q.inputs["Sheen Weight"].default_value = 0.8
    q.inputs["Sheen Roughness"].default_value = 0.35
    return m


def upholstery_material(rgb=(0.20, 0.17, 0.14)):
    """The bed's upholstery: a bouclé (Poly Haven curly_teddy_natural, CC0, 0.34 m a tile) in a mushroom taupe, about
    half the walls' albedo, so the bed stands off a light wall and its white linen; its pile in the colour, the
    normal and a sheen."""
    m = bpy.data.materials.new("archviz-boucle")
    nt = m.node_tree
    q = principled(m)
    d = os.path.join(dress.TEX, "curly_teddy_natural")
    diff = dress.image(os.path.join(d, "diff.jpg"))
    gain = np.array(rgb) / np.maximum(dress.mean_linear(diff), 1e-3)
    tc = node(nt, "ShaderNodeTexCoord")
    mp = node(nt, "ShaderNodeMapping", Scale=(1 / 0.34,) * 3)
    nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])
    col = dress.tex_node(nt, diff, mp.outputs["Vector"], box=True)
    tint = node(nt, "ShaderNodeMix", _data_type="RGBA", _blend_type="MULTIPLY")
    tint.inputs["Factor"].default_value = 1.0
    nt.links.new(col.outputs["Color"], [x for x in tint.inputs if x.name == "A" and x.type == "RGBA"][0])
    [x for x in tint.inputs if x.name == "B" and x.type == "RGBA"][0].default_value = (*gain, 1)
    nt.links.new([x for x in tint.outputs if x.type == "RGBA"][0], q.inputs["Base Color"])
    nt.links.new(dress.normal_map(nt, dress.image(os.path.join(d, "nor_gl.jpg"), True), mp.outputs["Vector"], 1.0, box=True),
                 q.inputs["Normal"])
    q.inputs["Roughness"].default_value = 0.95
    q.inputs["Sheen Weight"].default_value = 0.6
    q.inputs["Sheen Roughness"].default_value = 0.4
    return m


def upholster_chairs(scene):
    """The dining chairs' shells upholstered in bouclé, a warm light grey (the kit's chair is a grey plastic shell;
    the judges read it as plastic)."""
    mat, done = upholstery_material((0.34, 0.33, 0.31)), set()
    for o in scene.objects:
        if o.get("name") != "dining-chair-grey":
            continue
        for c in o.children_recursive:
            if c.type != "MESH":
                continue
            for sl in c.material_slots:
                m = sl.material
                if m is None or m.name.startswith("archviz") or "chair" not in m.name.lower():
                    continue
                done.add(m.name)
                sl.material = mat
    log(f"chairs: bouclé on {sorted(done)}")


def deck_material():
    """A balcony's timber deck: the deck scan (Poly Haven wood_floor_deck, CC0, 1.8 m a tile: planks with open
    gaps), its varnished orange taken down to weathered larch, a warm grey-brown; matte."""
    m = bpy.data.materials.new("archviz-deck")
    m["stone"] = True
    nt = m.node_tree
    q = principled(m)
    d = os.path.join(dress.TEX, "wood_floor_deck")
    diff = dress.image(os.path.join(d, "diff.jpg"))
    vec = dress.world_mapping(nt, 1.8, 0)
    col = dress.tex_node(nt, diff, vec)
    grey = node(nt, "ShaderNodeHueSaturation", Saturation=0.35)
    nt.links.new(col.outputs["Color"], grey.inputs["Color"])
    mean = dress.mean_linear(diff)
    lum = float(mean @ np.array([0.2126, 0.7152, 0.0722]))
    gain = np.array((0.17, 0.145, 0.12)) / max(lum, 1e-3)
    tint = node(nt, "ShaderNodeMix", _data_type="RGBA", _blend_type="MULTIPLY")
    tint.inputs["Factor"].default_value = 1.0
    nt.links.new(grey.outputs["Color"], [x for x in tint.inputs if x.name == "A" and x.type == "RGBA"][0])
    [x for x in tint.inputs if x.name == "B" and x.type == "RGBA"][0].default_value = (*gain, 1)
    nt.links.new([x for x in tint.outputs if x.type == "RGBA"][0], q.inputs["Base Color"])
    nt.links.new(dress.normal_map(nt, dress.image(os.path.join(d, "nor_gl.jpg"), True), vec, 1.0), q.inputs["Normal"])
    nt.links.new(dress.rough_map(nt, dress.image(os.path.join(d, "rough.jpg"), True), vec, 0.7, 0.9), q.inputs["Roughness"])
    return m


BEDS = []  # (frame at the head of the bed, facing -y; width) of the beds made


def swap_beds(scene, pieces):
    n = 0
    linen, fabric = dress.linen_material(), upholstery_material()
    wool = dress.wool_material((0.5, 2.2), (0.62, 0.61, 0.6))
    for o in pieces:
        nm, fp = name(o).lower(), list(o.get("footprint", ()))
        if "bed" not in nm or "bedside" in nm or len(fp) < 2 or fp[1] < 1.8:
            continue
        w = max([s for s in BED_SIZES if s + 0.2 <= fp[0]] or [BED_SIZES[0]])
        length = 2.0
        lo, _ = box(o)
        rot = turn(o)
        hide(o)
        path = bedset(w, length)
        frame = frame_of(o, lo.z) @ Matrix.Translation((0, fp[1] / 2, 0))
        root = bpy.data.objects.new("bedset", None)
        scene.collection.objects.link(root)
        root.matrix_world = frame
        before = set(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath=path)
        for c in [c for c in bpy.data.objects if c not in before]:
            if c.parent is None:
                c.parent = root
                c.matrix_parent_inverse.identity()
            if c.type != "MESH":
                continue
            mat = c.data.materials[0].name.split(".")[0] if c.data.materials else ""
            pick = {"bed-fabric": fabric, "bed-linen": linen}.get(mat)
            if c.name.startswith("throw"):
                pick = wool
            elif c.name.startswith("duvet"):
                pick = linen
            if pick:
                c.data.materials.clear()
                c.data.materials.append(pick)
        top = 0.52
        pillows, _ = place(scene, model("throw_pillows_01"), to_scene(frame @ Vector((0, -0.30, top))), height=0.52,
                           turn_deg=math.degrees(rot))
        recolour(pillows, linen)
        pillows.scale = (pillows.scale[0] * 1.45 * w / 1.6, pillows.scale[1], pillows.scale[2])
        cushions, _ = place(scene, model("throw_pillows_01"), to_scene(frame @ Vector((0, -0.68, top + 0.03))), height=0.38,
                            turn_deg=math.degrees(rot))
        recolour(cushions, velvet((0.16, 0.19, 0.13)))
        BEDS.append((frame, w))
        n += 1
        log(f"bed: {nm} {fp[0]:.2f} x {fp[1]:.2f} -> {w} x {length}")
    return n


LIMEWASH = tuple(float(v) for v in os.environ.get("LIMEWASH", "0.40,0.335,0.27").split(","))  # linear, a warm greige


def limewash_material():
    """A limewashed wall: a warm greige in soft clouds of tone (the brush's passes) over a fine plaster relief."""
    m = bpy.data.materials.new("archviz-limewash")
    nt = m.node_tree
    p = principled(m)
    tc = node(nt, "ShaderNodeTexCoord")
    clouds = node(nt, "ShaderNodeTexNoise", Scale=1.4, Detail=6.0, Roughness=0.62, Distortion=0.35)
    nt.links.new(tc.outputs["Object"], clouds.inputs["Vector"])
    strokes = node(nt, "ShaderNodeTexNoise", Scale=5.0, Detail=4.0, Roughness=0.5)
    mp = node(nt, "ShaderNodeMapping", Scale=(1.0, 3.0, 1.0))  # longer than high: horizontal passes
    nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])
    nt.links.new(mp.outputs["Vector"], strokes.inputs["Vector"])
    mix = node(nt, "ShaderNodeMath", _operation="MULTIPLY_ADD")
    mix.inputs[1].default_value, mix.inputs[2].default_value = 0.35, 0.0
    nt.links.new(strokes.outputs["Fac"], mix.inputs[0])
    add = node(nt, "ShaderNodeMath", _operation="ADD")
    nt.links.new(clouds.outputs["Fac"], add.inputs[0])
    nt.links.new(mix.outputs[0], add.inputs[1])
    tone = node(nt, "ShaderNodeMapRange", **{"From Min": 0.45, "From Max": 0.85, "To Min": 0.84, "To Max": 1.1})
    nt.links.new(add.outputs[0], tone.inputs["Value"])
    col = node(nt, "ShaderNodeMix", _data_type="RGBA", _blend_type="MULTIPLY")
    col.inputs["Factor"].default_value = 1.0
    [x for x in col.inputs if x.name == "A" and x.type == "RGBA"][0].default_value = (*LIMEWASH, 1)
    nt.links.new(tone.outputs["Result"], [x for x in col.inputs if x.name == "B" and x.type == "RGBA"][0])
    nt.links.new([x for x in col.outputs if x.type == "RGBA"][0], p.inputs["Base Color"])
    pl = os.path.join(dress.TEX, "plaster_grey_04")
    scale = node(nt, "ShaderNodeMapping", Scale=(1 / 1.2,) * 3)
    nt.links.new(tc.outputs["Object"], scale.inputs["Vector"])
    nt.links.new(dress.normal_map(nt, dress.image(os.path.join(pl, "nor_gl.jpg"), True), scale.outputs["Vector"], 0.5, box=True),
                 p.inputs["Normal"])
    p.inputs["Roughness"].default_value = 0.92
    return m


FEATURE_TILE = os.environ.get("FEATURE_TILE", "sage")  # the wet wall's colour; "" leaves it white
TILE_COLOURS = {"sage": (0.30, 0.345, 0.275), "clay": (0.42, 0.25, 0.17), "white": (0.80, 0.78, 0.74)}  # linear


def feature_tile_material(rgb, size=(0.075, 0.30), tone=0.09):
    """Narrow handmade tiles in a stack bond (on the export's UVs, which are in metres): each tile its own tone and
    a slight hue drift, a cloudy glaze, a cushioned edge and a wavy surface that breaks the reflections, glossy;
    a light grout, recessed and matte."""
    m = bpy.data.materials.new("archviz-feature-tile")
    nt = m.node_tree
    q = principled(m)
    w, h = size
    uv = node(nt, "ShaderNodeUVMap")
    mp = node(nt, "ShaderNodeMapping", Scale=(1 / w, 1 / h, 1.0))
    nt.links.new(uv.outputs["UV"], mp.inputs["Vector"])
    cell = node(nt, "ShaderNodeVectorMath", _operation="FLOOR")
    nt.links.new(mp.outputs["Vector"], cell.inputs[0])
    rnd = node(nt, "ShaderNodeTexWhiteNoise", _noise_dimensions="3D")
    nt.links.new(cell.outputs["Vector"], rnd.inputs["Vector"])
    fr = node(nt, "ShaderNodeVectorMath", _operation="FRACTION")
    nt.links.new(mp.outputs["Vector"], fr.inputs[0])
    sep = node(nt, "ShaderNodeSeparateXYZ")
    nt.links.new(fr.outputs["Vector"], sep.inputs[0])
    edge = []
    for axis, length in (("X", w), ("Y", h)):
        inv = node(nt, "ShaderNodeMath", _operation="SUBTRACT")
        inv.inputs[0].default_value = 1.0
        nt.links.new(sep.outputs[axis], inv.inputs[1])
        mn = node(nt, "ShaderNodeMath", _operation="MINIMUM")
        nt.links.new(sep.outputs[axis], mn.inputs[0])
        nt.links.new(inv.outputs[0], mn.inputs[1])
        mt = node(nt, "ShaderNodeMath", _operation="MULTIPLY")
        mt.inputs[1].default_value = length
        nt.links.new(mn.outputs[0], mt.inputs[0])
        edge.append(mt)
    e = node(nt, "ShaderNodeMath", _operation="MINIMUM")
    nt.links.new(edge[0].outputs[0], e.inputs[0])
    nt.links.new(edge[1].outputs[0], e.inputs[1])

    def ramp(a, b, lo, hi):
        r = node(nt, "ShaderNodeMapRange", _interpolation_type="SMOOTHSTEP")
        r.inputs["From Min"].default_value, r.inputs["From Max"].default_value = a, b
        r.inputs["To Min"].default_value, r.inputs["To Max"].default_value = lo, hi
        nt.links.new(e.outputs[0], r.inputs["Value"])
        return r

    joint = ramp(0.0012, 0.0024, 1.0, 0.0)
    # colour: the tile's tone, a hue drift, a cloud in the glaze
    tn = node(nt, "ShaderNodeVectorMath", _operation="MULTIPLY_ADD")
    tn.inputs[1].default_value = (2 * tone,) * 3
    tn.inputs[2].default_value = (1 - tone,) * 3
    nt.links.new(rnd.outputs["Value"], tn.inputs[0])
    drift = node(nt, "ShaderNodeVectorMath", _operation="MULTIPLY_ADD")
    drift.inputs[1].default_value = (0.06, 0.03, -0.04)
    drift.inputs[2].default_value = (0.97, 0.985, 1.02)
    nt.links.new(rnd.outputs["Color"], drift.inputs[0])
    tc = node(nt, "ShaderNodeTexCoord")
    cloud = node(nt, "ShaderNodeTexNoise", Scale=28.0, Detail=3.0)
    nt.links.new(tc.outputs["Object"], cloud.inputs["Vector"])
    cl = node(nt, "ShaderNodeMapRange")
    cl.inputs["From Min"].default_value, cl.inputs["From Max"].default_value = 0.3, 0.7
    cl.inputs["To Min"].default_value, cl.inputs["To Max"].default_value = 0.92, 1.05
    nt.links.new(cloud.outputs["Fac"], cl.inputs["Value"])
    col = node(nt, "ShaderNodeVectorMath", _operation="MULTIPLY")
    col.inputs[1].default_value = rgb
    nt.links.new(tn.outputs[0], col.inputs[0])
    col2 = node(nt, "ShaderNodeVectorMath", _operation="MULTIPLY")
    nt.links.new(col.outputs[0], col2.inputs[0])
    nt.links.new(drift.outputs[0], col2.inputs[1])
    col3 = node(nt, "ShaderNodeVectorMath", _operation="SCALE")
    nt.links.new(col2.outputs[0], col3.inputs[0])
    nt.links.new(cl.outputs[0], [x for x in col3.inputs if x.name == "Scale"][0])
    mix = node(nt, "ShaderNodeMix", _data_type="RGBA")
    nt.links.new(joint.outputs[0], mix.inputs["Factor"])
    nt.links.new(col3.outputs[0], [x for x in mix.inputs if x.name == "A" and x.type == "RGBA"][0])
    [x for x in mix.inputs if x.name == "B" and x.type == "RGBA"][0].default_value = (0.55, 0.53, 0.50, 1)
    nt.links.new([x for x in mix.outputs if x.type == "RGBA"][0], q.inputs["Base Color"])
    # gloss 0.05-0.15 on the glaze, 0.85 in the joint
    sc = node(nt, "ShaderNodeSeparateColor")
    nt.links.new(rnd.outputs["Color"], sc.inputs[0])
    gl = node(nt, "ShaderNodeMath", _operation="MULTIPLY_ADD")
    gl.inputs[1].default_value, gl.inputs[2].default_value = 0.10, 0.05
    nt.links.new(sc.outputs["Green"], gl.inputs[0])
    rough = node(nt, "ShaderNodeMix", _data_type="FLOAT")
    nt.links.new(joint.outputs[0], rough.inputs["Factor"])
    nt.links.new(gl.outputs[0], [x for x in rough.inputs if x.name == "A" and x.type == "VALUE"][0])
    [x for x in rough.inputs if x.name == "B" and x.type == "VALUE"][0].default_value = 0.85
    nt.links.new([x for x in rough.outputs if x.type == "VALUE"][0], q.inputs["Roughness"])
    # relief: the cushioned edge, then the handmade wave, then each tile's own tilt
    bump = node(nt, "ShaderNodeBump", Distance=0.002, Strength=1.0)
    nt.links.new(ramp(0.0012, 0.006, 0.0, 1.0).outputs[0], bump.inputs["Height"])
    wave = node(nt, "ShaderNodeTexNoise", Scale=7.0, Detail=2.0)
    nt.links.new(tc.outputs["Object"], wave.inputs["Vector"])
    bump2 = node(nt, "ShaderNodeBump", Strength=0.35)
    nt.links.new(wave.outputs["Fac"], bump2.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bump2.inputs["Normal"])
    tilt = node(nt, "ShaderNodeVectorMath", _operation="MULTIPLY_ADD")
    tilt.inputs[1].default_value = (0.06,) * 3
    tilt.inputs[2].default_value = (-0.03,) * 3
    nt.links.new(rnd.outputs["Color"], tilt.inputs[0])
    add = node(nt, "ShaderNodeVectorMath", _operation="ADD")
    nt.links.new(bump2.outputs["Normal"], add.inputs[0])
    nt.links.new(tilt.outputs[0], add.inputs[1])
    nrm = node(nt, "ShaderNodeVectorMath", _operation="NORMALIZE")
    nt.links.new(add.outputs[0], nrm.inputs[0])
    nt.links.new(nrm.outputs[0], q.inputs["Normal"])
    q.inputs["Coat Weight"].default_value = 0.15
    q.inputs["Coat Roughness"].default_value = 0.04
    return m


def feature_tiles(scene, room, sy):
    """The wet wall in a designer's tile: the tiled wall the room's bathtub backs onto (the faces of the tile meshes
    in that plane, on this room's side), in feature_tile_material. The other tiled walls keep the white tile."""
    rgb = TILE_COLOURS.get(FEATURE_TILE)
    if rgb is None:
        return 0
    dg = bpy.context.evaluated_depsgraph_get()
    poly = room["polygon"]
    for o in storey_pieces(scene, sy):
        if name(o) != "bathtub" or len(o.get("footprint", ())) != 2:
            continue
        lo, hi = box(o)
        f = frame_of(o, lo.z)
        cx, _, cz = to_scene(f.translation)
        if not inside(poly, cx, cz):
            continue
        back = (f.to_3x3() @ Vector((0, 1, 0))).normalized()
        w = o["footprint"][0]
        hits = (trace(scene, dg, f @ Vector((u * w, 0, hi.z - lo.z + dz)), back, far=2.0)
                for dz in (0.08, 0.2) for u in (0.0, -0.35, 0.35))  # past a window over the tub
        hit = next((x for x in hits if x is not None and "wallTiles" in kinds(x[2])), None)
        if hit is None:
            log("feature tiles: no tiled wall behind the bathtub")
            return 0
        loc, normal = hit[0], hit[1].normalized()
        mat, n = feature_tile_material(rgb), 0
        for t in scene.objects:
            if t.type != "MESH" or "wallTiles" not in kinds(t):
                continue
            mw, rot = t.matrix_world, t.matrix_world.to_3x3()
            faces = []
            for pg in t.data.polygons:
                c, nw = mw @ pg.center, (rot @ pg.normal).normalized()
                x, _, z = to_scene(c + nw * 0.05)
                if nw.dot(normal) > 0.97 and abs((c - loc).dot(normal)) < 0.03 and inside(poly, x, z):
                    faces.append(pg.index)
            if faces:
                reslot(t, faces, mat)
                n += len(faces)
        log(f"feature tiles: {FEATURE_TILE} on the wall behind the bathtub ({n} faces)")
        return n
    return 0


def feature_wall(scene, room, sy):
    """A designer's one coloured wall: the wall at the head of the room's bed, limewashed from the floor to the
    ceiling and from corner to corner (the stretch of the room's outline it stands on), as a plane 3 mm off it.
    Left plain when an opening is in it."""
    dg = bpy.context.evaluated_depsgraph_get()
    poly = room["polygon"]
    for frame, _ in BEDS:
        hx, _, hz = to_scene(frame.translation)
        if not inside(poly, hx, hz):
            continue
        head = (frame.to_3x3() @ Vector((0, 1, 0))).normalized()
        hit = trace(scene, dg, frame @ Vector((0, -0.05, 1.25)), head, far=0.8)
        if hit is None:
            continue
        loc, normal = hit[0], hit[1]
        px, _, pz = to_scene(loc)
        nx, _, nz = to_scene(normal)
        best = None
        for i in range(len(poly)):
            (ax, az), (bx, bz) = poly[i - 1], poly[i]
            ex, ez = bx - ax, bz - az
            length = math.hypot(ex, ez)
            if length < 1.0 or abs(ex * nx + ez * nz) / length > 0.1:
                continue
            t = ((px - ax) * ex + (pz - az) * ez) / length**2
            d = abs((px - ax) * ez - (pz - az) * ex) / length
            if -0.02 <= t <= 1.02 and d < 0.2 and (best is None or d < best[0]):
                best = (d, (ax, az), (bx, bz))
        if best is None:
            continue
        _, (ax, az), (bx, bz) = best
        y0, y1, up = sy, sy + 2.5, loc + normal * 0.05
        for _ in range(8):  # up to the ceiling, through what hangs on the wall
            top = trace(scene, dg, up, Vector((0, 0, 1)), far=4.0)
            if top is None:
                break
            if kinds(top[2]) & {"ceiling", "slab", "roof", "building"}:
                y1 = to_scene(top[0])[1]
                break
            up = top[0] + Vector((0, 0, 0.002))
        plane = px * nx + pz * nz

        def on_wall(x, z, y, off=0.003):  # the outline's corner, moved onto the wall's plane and off it
            k = x * nx + z * nz - plane - off
            return Vector((x - nx * k, y, z - nz * k))

        corners = [on_wall(ax, az, y0), on_wall(bx, bz, y0), on_wall(bx, bz, y1), on_wall(ax, az, y1)]
        back = Vector((-nx, nz, 0.0))  # towards the wall, in Blender's frame
        for i in range(1, 12):  # openings: a probe a step off the wall must meet it right behind
            for j in range(1, 6):
                q = corners[0].lerp(corners[1], i / 12)
                h = trace(scene, dg, Vector((q.x + nx * 0.05, -(q.z + nz * 0.05), y0 + (y1 - y0) * j / 6)), back, far=0.25)
                if h is None or kinds(h[2]) & {"window", "door", "interiorDoor"}:
                    log("feature wall: an opening in the bed's wall, left plain")
                    return 0
        me = bpy.data.meshes.new("limewash")
        me.from_pydata([Vector((c.x, -c.z, c.y)) for c in corners], [], [(0, 1, 2, 3)])
        me.update()
        if me.polygons[0].normal.dot(Vector((nx, -nz, 0.0))) < 0:
            me.flip_normals()
        ob = bpy.data.objects.new("limewash", me)
        ob["kind"] = "featureWall"  # a wall for the sun's patches, not for the white balance
        scene.collection.objects.link(ob)
        ob.data.materials.append(limewash_material())
        log(f"feature wall: limewash behind the bed, {math.hypot(bx - ax, bz - az):.2f} x {y1 - y0:.2f} m")
        return 1
    return 0


def sofa_cushions(scene, pieces):
    n = 0
    dg = bpy.context.evaluated_depsgraph_get()
    for o in pieces:
        if "sofa" not in name(o).lower():
            continue
        fp = o["footprint"]
        lo, hi = box(o)
        f = frame_of(o, lo.z)
        spot = Vector((fp[0] / 2 - 0.45, 0.12, 0))
        ok, seat, *_ = scene.ray_cast(dg, f @ (spot + Vector((0, 0, hi.z - lo.z + 0.05))), Vector((0, 0, -1)))
        if ok and seat.z - lo.z < 0.7:
            at = f @ spot
            place(scene, model("throw_pillows_01"), (at.x, seat.z + 0.005, -at.y), height=0.4, turn_deg=math.degrees(turn(o)) - 8)
            n += 1
    return n


def within(o, lo, hi):
    t = o.matrix_world.translation
    return lo.x <= t.x <= hi.x and lo.y <= t.y <= hi.y and lo.z - 0.05 <= t.z <= hi.z + 0.05


def dress_tables(scene, pieces):
    n = 0
    for o in pieces:
        nm = name(o).lower()
        if "dining-table" not in nm and "dining table" not in nm:
            continue
        fp = o["footprint"]
        lo, hi = box(o)
        t = o.matrix_world.translation
        along_d = fp[1] >= fp[0]
        rugs = [r for r in pieces if name(r) == "rug"]
        if not any(box(r)[0].x <= t.x <= box(r)[1].x and box(r)[0].y <= t.y <= box(r)[1].y for r in rugs):
            r = dress.rug(scene, (t.x, -t.y), (fp[0] + 1.1, fp[1] + 1.1), floor=lo.z)  # the chairs stay on it
            r.rotation_euler[2] = turn(o)
        dress.runner(scene, (t.x, hi.z + 0.002, -t.y), (0.30, max(fp) - 0.3), turn(o) + (0 if along_d else math.pi / 2))
        vase = next((v for v in pieces if "vase" in name(v).lower() and within(v, lo, hi)), None)
        if vase is not None:
            vlo, vhi = box(vase)
            c = (vlo + vhi) / 2
            place(scene, EUCALYPTUS, (c.x, vhi.z - 0.06, -c.y), height=0.62, turn_deg=10)
        else:
            _, size = place(scene, model("ceramic_vase_02"), (t.x, hi.z + 0.004, -t.y), height=0.26)
            place(scene, EUCALYPTUS, (t.x, hi.z + 0.004 + size.z * 0.85, -t.y), height=0.6, turn_deg=10)
        n += 1
    return n


def dress_counter(scene, pieces):
    """A board with lemons and a wooden bowl of fruit on the longest free stretch of the island (or the longest
    run): the counter top is the height and material most rays down the run's middle meet."""
    runs = [o for o in pieces if name(o) == "kitchenRun" and len(o.get("footprint", ())) == 2]
    if not runs:
        return 0
    run = max(runs, key=lambda o: (o["footprint"][1] >= 0.8, o["footprint"][0]))
    fp = run["footprint"]
    lo, _ = box(run)
    f = frame_of(run, lo.z)
    dg = bpy.context.evaluated_depsgraph_get()
    n_s = 25
    us = [-fp[0] / 2 + 0.12 + k * (fp[0] - 0.24) / (n_s - 1) for k in range(n_s)]
    hits = []
    for u in us:
        ok, loc, _, face, obj, _ = scene.ray_cast(dg, f @ Vector((u, -0.05, 1.25)), Vector((0, 0, -1)))
        m, _ = material_at(obj, face) if ok else (None, None)
        hits.append((round(loc.z - lo.z, 2), m.name if m else None) if ok else (None, None))
    tops = [h for h in hits if h[0] is not None and 0.8 <= h[0] <= 1.0]
    if not tops:
        return 0
    top = max(set(tops), key=tops.count)
    free = [h == top for h in hits]
    best, cur = (0, 0), None  # the longest run of free samples
    for k, ok in enumerate(free + [False]):
        if ok and cur is None:
            cur = k
        elif not ok and cur is not None:
            best = max(best, (k - cur, cur), key=lambda b: b[0])
            cur = None
    length, start = best
    step = (fp[0] - 0.24) / (n_s - 1)
    if length * step < 0.35:
        return 0
    z = lo.z + top[0]
    a = f @ Vector((us[start] + length * step * (0.30 if length * step >= 0.9 else 0.5) - step / 2, -0.05, 0))
    _, bowl = place(scene, model("wooden_bowl_01"), (a.x, z, -a.y), height=0.09)
    for k, (dx, dz, fruit) in enumerate(((-0.05, 0.0, "food_apple_01"), (0.05, 0.03, "food_apple_01"),
                                         (0.0, -0.05, "lemon"), (0.02, 0.06, "food_apple_01"))):
        place(scene, model(fruit), (a.x + dx, z + 0.035 + (0.03 if k == 3 else 0), -a.y + dz), height=0.075, turn_deg=70 * k)
    if length * step >= 0.9:
        b = f @ Vector((us[start] + length * step * 0.72 - step / 2, -0.05, 0))
        _, board = place(scene, model("wooden_cutting_board"), (b.x, z, -b.y), turn_deg=math.degrees(turn(run)) - 25)
        for k, (dx, dz) in enumerate(((-0.05, 0.02), (0.05, -0.03), (0.0, 0.07))):
            place(scene, model("lemon"), (b.x + dx, z + board.z, -b.y + dz), height=0.065, turn_deg=40 * k)
    return 1


# ---------------------------------------------------------------- bathrooms

def bath_piece(what, rim=None):
    """A piece of made/bathset.py, made once and cached (a drape per rim width, in cm)."""
    path = os.path.join(CACHE, f"bath-{what}.glb" if rim is None else f"bath-drape-{round(rim * 100)}.glb")
    if not os.path.exists(path):
        args = [sys.executable, os.path.join(HERE, "made", "bathset.py"), what, path]
        subprocess.run(args + ([] if rim is None else [f"{round(rim * 100) / 100:.2f}"]), check=True)
    return path


def terry(rgb):
    """Cotton towelling: the curly fleece scan (loops) at towel scale, in the towel's colour."""
    m = bpy.data.materials.new("archviz-terry")
    nt = m.node_tree
    p = principled(m)
    d = os.path.join(dress.TEX, "curly_teddy_natural")
    tc = nt.nodes.new("ShaderNodeTexCoord")
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (1 / 0.09,) * 3
    nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])
    vec = mp.outputs["Vector"]
    img = dress.image(os.path.join(d, "diff.jpg"))
    mean = dress.mean_linear(img)
    tint = nt.nodes.new("ShaderNodeMixRGB")
    tint.blend_type = "MULTIPLY"
    tint.inputs[0].default_value = 1.0
    tint.inputs[2].default_value = (*[c / max(1e-3, k) for c, k in zip(rgb, mean)], 1)
    nt.links.new(dress.tex_node(nt, img, vec, box=True).outputs["Color"], tint.inputs[1])
    nt.links.new(tint.outputs[0], p.inputs["Base Color"])
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.8
    bump.inputs["Distance"].default_value = 0.003
    nt.links.new(dress.tex_node(nt, dress.image(os.path.join(d, "disp.jpg"), True), vec, box=True).outputs["Color"],
                 bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], p.inputs["Normal"])
    p.inputs["Roughness"].default_value = 0.95
    p.inputs["Sheen Weight"].default_value = 0.5
    p.inputs["Sheen Roughness"].default_value = 0.5
    return m


def oak_wood(rgb=(0.42, 0.29, 0.17)):
    """Oiled oak for small pieces: the plank scan's grain, box-mapped, in an oak colour."""
    m = bpy.data.materials.new("archviz-oak")
    nt = m.node_tree
    p = principled(m)
    d = os.path.join(dress.TEX, "laminate_floor_02")
    tc = nt.nodes.new("ShaderNodeTexCoord")
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (1 / 0.8,) * 3
    nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])
    img = dress.image(os.path.join(d, "diff.jpg"))
    mean = dress.mean_linear(img)
    tint = nt.nodes.new("ShaderNodeMixRGB")
    tint.blend_type = "MULTIPLY"
    tint.inputs[0].default_value = 1.0
    tint.inputs[2].default_value = (*[c / max(1e-3, k) for c, k in zip(rgb, mean)], 1)
    nt.links.new(dress.tex_node(nt, img, mp.outputs["Vector"], box=True).outputs["Color"], tint.inputs[1])
    nt.links.new(tint.outputs[0], p.inputs["Base Color"])
    p.inputs["Roughness"].default_value = 0.5
    return m


BATH_MATS = {}


def bath_materials(root):
    """The Cycles materials of a bathset piece, by its material names."""
    if not BATH_MATS:
        BATH_MATS.update({"towel-white": terry((0.80, 0.79, 0.76)), "towel-sand": terry((0.52, 0.45, 0.36)),
                          "oak": oak_wood()})
    for c in family(root):
        if c.type != "MESH":
            continue
        for slot in c.material_slots:
            m = slot.material
            if m is None:
                continue
            key = m.name.split(".")[0]
            if key in BATH_MATS:
                slot.material = BATH_MATS[key]
            elif key == "amber-glass":
                q = principled(m)
                q.inputs["Roughness"].default_value = 0.02
                q.inputs["IOR"].default_value = 1.5
            elif key == "wax":
                q = principled(m)
                q.inputs["Subsurface Weight"].default_value = 0.5
                q.inputs["Subsurface Scale"].default_value = 0.01


def in_frame(scene, f, path, at, scale=(1.0, 1.0, 1.0)):
    """Import a bathset piece made in a piece's frame and fix it at local point `at` of frame `f`."""
    root = bpy.data.objects.new(os.path.basename(path), None)
    scene.collection.objects.link(root)
    root.matrix_world = f @ Matrix.Translation(at)
    root.scale = scale
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    for c in [c for c in bpy.data.objects if c not in before]:
        if c.parent is None:
            c.parent = root
            c.matrix_parent_inverse.identity()
    bath_materials(root)
    return root


def heights_down(scene, dg, f, pts, start):
    """Local points (u, v): the height above the frame's floor and the normal of what a ray down meets."""
    out = []
    for u, v in pts:
        hit = trace(scene, dg, f @ Vector((u, v, start)), Vector((0, 0, -1)), far=start + 0.2)
        out.append((hit[0].z - f.translation.z, hit[1].z) if hit else (None, None))
    return out


def dress_tubs(scene, dg, pieces):
    """An oak caddy across the tub (with a candle, a soap, a hand towel) and a bath towel over its front rim. The
    rims from rays down the tub's depth: the front rim runs from the front to where the height drops."""
    n = 0
    for o in pieces:
        if name(o) != "bathtub" or len(o.get("footprint", ())) != 2:
            continue
        w, d = o["footprint"]
        lo, _ = box(o)
        f = frame_of(o, lo.z)
        vs = [-d / 2 + 0.005 + 0.01 * k for k in range(int(d / 0.01))]
        rims = []
        for u in (-w * 0.22, w * 0.22):
            hs = [h for h, _ in heights_down(scene, dg, f, [(u, v) for v in vs], 1.0)]
            front = [h for h in hs[:15] if h is not None]
            back = [h for h in hs[-15:] if h is not None]
            if not front or not back:
                continue
            top_f, top_b = max(front), max(back)
            # the rim: from the front (an apron's lip a few cm lower counts: the towel hangs outside it) to where
            # the height drops into the tub
            i_top = next(i for i, h in enumerate(hs) if h is not None and h >= top_f - 0.005)
            k = next((i for i, h in enumerate(hs) if i > i_top and h is not None and h < top_f - 0.03), None)
            rims.append((u, top_f, top_b, None if k is None else vs[k] + d / 2))
        if len(rims) < 2:
            continue
        (u_d, top_d, _, rim_w), (u_c, top_cf, top_cb, _) = rims
        if abs(top_cf - top_cb) < 0.03:  # level rims: the caddy rests on both
            caddy = in_frame(scene, f, bath_piece("caddy"), (u_c, 0.0, max(top_cf, top_cb)),
                             (1.0, (d - 0.04) / 0.80, 1.0))
            caddy.name = "bath-caddy"
        if rim_w and 0.03 <= rim_w <= 0.2:
            in_frame(scene, f, bath_piece("drape", rim_w), (u_d, -d / 2 + rim_w / 2, top_d))
        log(f"tub: rims {[(round(u, 2), round(a, 2), round(b, 2), r and round(r, 3)) for u, a, b, r in rims]}")
        n += 1
    return n


def free_spot(scene, dg, o, half, floor_z):
    """The point of a piece's top (local u, v, height above the floor) farthest to a side where a rectangle of
    half-sizes `half` (u, v) is flat and free: a basin's deck beside its bowl and tap."""
    w, d = o["footprint"]
    f = frame_of(o, floor_z)
    hu, hv = half
    if w < 2 * hu or d < 2 * hv:
        return None
    nu, nv = max(1, round((w - 2 * hu) / 0.02)), max(1, round((d - 2 * hv) / 0.01))
    us = [-w / 2 + hu + k * (w - 2 * hu) / nu for k in range(nu + 1)]
    vs = [-d / 2 + hv + k * (d - 2 * hv) / nv for k in range(nv + 1)]
    grid = [(u, v) for u in us for v in vs]
    hs = heights_down(scene, dg, f, grid, 1.3)
    flat = [round(h, 2) for h, nz in hs if h is not None and nz > 0.95 and 0.5 < h < 1.2]
    for level in sorted({h for h in flat if flat.count(h) >= 4}, reverse=True):  # a tap's top first, then the deck
        best = None
        for (u, v), (h, nz) in zip(grid, hs):
            if h is None or nz < 0.95 or abs(h - level) > 0.006:
                continue
            rect = [(u + a * hu, v + b * hv) for a in (-1, 0, 1) for b in (-1, 0, 1) if a or b]
            if not all(hh is not None and nn > 0.95 and abs(hh - h) < 0.005 for hh, nn in heights_down(scene, dg, f, rect, 1.3)):
                continue
            walled = 0  # the flat bottom of a bowl: something higher close by in most directions
            for k in range(8):
                a = k * math.pi / 4
                reach = [0.03 * k for k in range(1, int(max(w, d) / 2 / 0.03) + 2)]  # out to the piece's edge
                ring = [(u + math.cos(a) * (hu + r), v + math.sin(a) * (hv + r)) for r in reach]
                walled += any(hh is not None and hh > h + 0.015 for hh, _ in heights_down(scene, dg, f, ring, 1.3))
            if walled >= 6:
                continue
            key = (abs(u), v)
            if best is None or key > best[0]:
                best = (key, (u, v, h))
        if best:
            return best[1]
    return None


def dress_basins(scene, dg, pieces, floor_z):
    """Pump bottles on a free corner of each basin's deck (on a stone tray if there is room); the kit's own
    accessories and towel stacks become ours."""
    n = 0
    for o in pieces:
        nm = name(o)
        if nm in ("bathAccessories", "towelStack") and len(o.get("footprint", ())) == 2:
            lo, _ = box(o)
            hide(o)
            fp = o["footprint"]
            if nm == "towelStack":
                s = min(1.0, fp[0] / 0.30, fp[1] / 0.24) * 1.3
                in_frame(scene, frame_of(o, lo.z), bath_piece("stack"), (0, 0, 0), (s, s, s))
            else:
                in_frame(scene, frame_of(o, lo.z), bath_piece("pair"), (0, 0, 0))
            n += 1
        elif nm == "basin" and len(o.get("footprint", ())) == 2:
            for what, half in (("bottles", (0.115, 0.115)), ("pair", (0.085, 0.045))):
                spot = free_spot(scene, dg, o, half, floor_z)
                if spot:
                    u, v, h = spot
                    root = in_frame(scene, frame_of(o, floor_z), bath_piece(what), (u, v, h + 0.001))
                    if what == "bottles":
                        root.rotation_euler[2] += math.radians(-20 if u > 0 else 20)
                    log(f"basin: {what} at {round(u, 2), round(v, 2)} on its deck {h:.2f} m high")
                    n += 1
                    break
    return n


def dress_storey(scene, sy):
    pieces = storey_pieces(scene, sy)
    done = {
        "rugs": swap_rugs(scene, pieces),
        "plants": swap_plants(scene, pieces, sy),
        "art": swap_art(scene, pieces),
        "armchairs": swap_armchairs(scene, pieces),
        "beds": swap_beds(scene, pieces),
    }
    bpy.context.view_layer.update()
    done["sofas"] = sofa_cushions(scene, pieces)
    done["tables"] = dress_tables(scene, pieces)
    done["counters"] = dress_counter(scene, pieces)
    if "sanitary" not in os.environ.get("SKIP", "").split(","):
        sanitaryware(scene, pieces)
    dg = bpy.context.evaluated_depsgraph_get()
    done["tubs"] = dress_tubs(scene, dg, pieces)
    done["basins"] = dress_basins(scene, dg, pieces, sy)
    log("dressing:", done)


# ---------------------------------------------------------------- what the camera sees

def clear_edges(scene, cam, widest=0.03):
    """A sliver of something right in front of the lens at a side edge of the frame (a wall end, a door jamb)
    reads as a bright or dark bar: turn the camera just enough to lose it. Only for slivers narrower than
    `widest` of the frame, so the owner's framing stays."""
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    corners = cam.data.view_frame(scene=scene)
    x0, x1 = min(c.x for c in corners), max(c.x for c in corners)
    y0, y1 = min(c.y for c in corners), max(c.y for c in corners)
    zc = corners[0].z
    rot, origin = cam.matrix_world.to_3x3(), cam.matrix_world.translation

    def depth(fx, fy):
        d = (rot @ Vector((x0 + fx * (x1 - x0), y1 - fy * (y1 - y0), zc))).normalized()
        hit = trace(scene, dg, origin, d)
        return (hit[0] - origin).length if hit else 1e3

    steps = [k * 0.0025 for k in range(int(widest / 0.0025) + 2)]
    turns = {}
    for side in ("left", "right"):
        widths = []
        for fy in (0.2, 0.35, 0.5, 0.65, 0.8):
            ds = [depth(f if side == "left" else 1 - f, fy) for f in steps]
            for k in range(1, len(ds)):
                near = max(ds[:k])
                if ds[k] > 1.6 * near and ds[k] - near > 0.5:
                    widths.append(steps[k])
                    break
        if len(widths) >= 3:
            turns[side] = max(widths) + 0.005
    # a strip of ceiling along the top edge, a few pixels high (the camera's top edge just over the wall's
    # top): tilt the frame down (less shift) to lose it
    strips = []
    for fx in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        f = 0.0
        while f <= widest + 0.0025:
            d = (rot @ Vector((x0 + fx * (x1 - x0), y1 - f * (y1 - y0), zc))).normalized()
            hit = trace(scene, dg, origin, d)
            if hit is None or hit[1].z > -0.7:
                break
            f += 0.0025
        strips.append(f)
    if sum(f > 0 for f in strips) >= 3 and max(strips) <= widest:
        h = max(strips) + 0.004
        cam.data.shift_y -= h * (y1 - y0) / max(x1 - x0, y1 - y0)
        log(f"frame: a {h * 100:.1f} % ceiling strip at the top edge, frame lowered")
    if len(turns) != 1:
        return
    side, w = next(iter(turns.items()))
    edge = math.atan2(x1, -zc)  # the half angle of the frame, horizontally
    inner = math.atan2(x1 - w * (x1 - x0), -zc)
    delta = edge - inner
    cam.rotation_euler[2] += delta if side == "right" else -delta  # right sliver: turn left, it leaves on the right
    log(f"frame: a {w * 100:.1f} % sliver at the {side} edge, camera turned {math.degrees(delta):.1f} deg")


def camera_rays(scene, cam, rooms, sy, nx=48, ny=27):
    """Rays through a grid of the frame: each sample's pixel (fractions, from the top left), what it meets,
    and whether that is inside the storey's rooms."""
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    corners = cam.data.view_frame(scene=scene)
    x0, x1 = min(c.x for c in corners), max(c.x for c in corners)
    y0, y1 = min(c.y for c in corners), max(c.y for c in corners)
    zc = corners[0].z
    rot, origin = cam.matrix_world.to_3x3(), cam.matrix_world.translation
    plans = [r for r in rooms if abs(r["y"] - sy) < 0.1]
    out = []
    for j in range(ny):
        for i in range(nx):
            fx, fy = (i + 0.5) / nx, (j + 0.5) / ny
            d = (rot @ Vector((x0 + fx * (x1 - x0), y1 - fy * (y1 - y0), zc))).normalized()
            hit = trace(scene, dg, origin, d)
            if hit is None:
                out.append({"fx": fx, "fy": fy, "hit": None, "interior": False})
                continue
            loc, normal, obj, face = hit
            x, y, z = to_scene(loc + normal * 0.05)  # a wall's face lies on the room's outline: test just in front
            interior = sy - 0.05 <= y <= sy + 2.8 and any(inside(r["polygon"], x, z) for r in plans)
            out.append({"fx": fx, "fy": fy, "hit": hit, "interior": interior})
    return out


def ancestors(o):
    while o is not None:
        yield o
        o = o.parent


def hang_art(scene, rays, sy):
    """A print on the wall the camera sees most, if no print is in view: centred near the middle of what the
    camera sees of that wall, where nothing stands in front of it between 1.05 and 1.9 m (a sideboard below
    is fine), its bottom at 1.04 m."""
    if any(r["hit"] and any(a.name.startswith("art.glb") for a in ancestors(r["hit"][2])) for r in rays):
        return False
    groups = {}
    for r in rays:
        if not r["interior"]:
            continue
        loc, n, obj, _ = r["hit"]
        k = kinds(obj)
        if abs(n.z) > 0.2 or not k & {"wall", "partition"} or k & {"window", "door", "interiorDoor"}:
            continue
        key = (round(math.atan2(n.y, n.x) / (math.pi / 8)), round(n.dot(loc) / 0.05))
        groups.setdefault(key, []).append((loc, n))
    if not groups:
        return False
    dg = bpy.context.evaluated_depsgraph_get()
    for g in sorted(groups.values(), key=len, reverse=True)[:3]:
        n = Vector((g[0][1].x, g[0][1].y, 0)).normalized()
        a = Vector((-n.y, n.x, 0))
        p0 = g[0][0]
        ss = sorted(a.dot(loc) for loc, _ in g)
        mid = ss[len(ss) // 2]
        cands = sorted((ss[0] + 0.4 + 0.1 * k for k in range(int((ss[-1] - ss[0] - 0.8) / 0.1) + 1)), key=lambda v: abs(v - mid))

        def free(s):
            for h in (1.05, 1.45, 1.9):
                for ds in (-0.35, 0.0, 0.35):
                    p = p0 + a * (s + ds - a.dot(p0))
                    p.z = sy + h
                    hit = trace(scene, dg, p + n * 0.5, -n, far=0.6)
                    if hit is None or (hit[0] - p).length > 0.03 or not kinds(hit[2]) & {"wall", "partition"} \
                            or kinds(hit[2]) & {"window", "door", "interiorDoor"}:
                        return False
            return True

        s = next((c for c in cands if free(c)), None)
        log(f"art: wall {tuple(round(v, 2) for v in n)} {len(g)} samples, s {ss[0]:.2f}..{ss[-1]:.2f}, {len(cands)} spots, free at {s}")
        if s is None:
            continue
        p = p0 + a * (s - a.dot(p0)) + n * 0.016
        place(scene, ART, (p.x, sy + 1.04, -p.y), turn_deg=math.degrees(math.atan2(-n.x, n.y)))
        log(f"art: on the wall facing {tuple(round(v, 2) for v in n)} at {tuple(round(v, 2) for v in to_scene(p))}")
        return True
    return False


def reslot(obj, faces, material):
    """Give some faces of a mesh another material (the rest keep theirs)."""
    if obj.data.users > 1:
        obj.data = obj.data.copy()
    if material.name not in obj.data.materials:
        obj.data.materials.append(material)
    i = list(obj.data.materials).index(material)
    for f in faces:
        obj.data.polygons[f].material_index = i


def pave(rays, sy, rooms):
    """Light exterior paving the camera sees through the glass: stone grey on the ground floor, a timber deck
    on upper floors (white in the sun, it blows out; a balcony sharing the walls' material turns sky-blue).
    Face by face: the export's balcony can share a mesh and a material with the walls (a slot-wide swap turned the
    bedroom's walls into decking)."""
    deck = sy > 1.5
    plans = [r for r in rooms if abs(r["y"] - sy) < 0.1]
    done = set()
    for r in rays:
        if r["hit"] is None or r["interior"]:
            continue
        loc, normal, obj, face = r["hit"]
        if normal.z < 0.7 or abs(loc.z - sy) > 0.6:
            continue
        m, slot = material_at(obj, face)
        if m is None or m.get("stone") or (obj.name, slot) in done:
            continue
        q = principled(m)
        c = q.inputs["Base Color"].default_value
        if not q.inputs["Base Color"].is_linked and 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2] < 0.35:
            continue
        done.add((obj.name, slot))
        mw, rot = obj.matrix_world, obj.matrix_world.to_3x3()
        faces = []
        for p in obj.data.polygons:
            if p.material_index != slot or (rot @ p.normal).normalized().z < 0.7:
                continue
            x, y, z = to_scene(mw @ p.center + (rot @ p.normal).normalized() * 0.05)
            if abs(y - sy) <= 0.65 and not any(inside(pl["polygon"], x, z) for pl in plans):
                faces.append(p.index)
        if deck:
            reslot(obj, faces, deck_material())
            continue
        m2 = m.copy()
        reslot(obj, faces, m2)
        q = principled(m2)
        nt = m2.node_tree
        dim = nt.nodes.new("ShaderNodeMixRGB")
        dim.blend_type = "MULTIPLY"
        dim.inputs[0].default_value = 1.0
        dim.inputs[2].default_value = (0.52, 0.51, 0.49, 1)
        if q.inputs["Base Color"].is_linked:
            nt.links.new(q.inputs["Base Color"].links[0].from_socket, dim.inputs[1])
        else:
            dim.inputs[1].default_value = q.inputs["Base Color"].default_value
        nt.links.new(dim.outputs[0], q.inputs["Base Color"])
        q.inputs["Roughness"].default_value = 0.75
        m2["stone"] = True
    log(f"pave: {len(done)} surfaces as {'deck' if deck else 'stone'}")


# ---------------------------------------------------------------- the sun

def sun_positions(lat):
    """(compass azimuth, elevation) of the sun over the year at `lat`: four declinations, every 30 minutes."""
    out = []
    phi = math.radians(lat)
    for decl in (-15.0, 0.0, 15.0, 23.4):
        d = math.radians(decl)
        for k in range(-14, 15):
            h = math.radians(k * 7.5)  # hour angle, 7.5 deg = 30 min of solar time
            el = math.asin(math.sin(phi) * math.sin(d) + math.cos(phi) * math.cos(d) * math.cos(h))
            if not math.radians(12) <= el <= math.radians(60):
                continue
            a = math.atan2(math.sin(h), math.cos(h) * math.sin(phi) - math.tan(d) * math.cos(phi))
            out.append(((math.degrees(a) + 180) % 360, math.degrees(el)))
    return out


SUN_DEBUG = {}
RANKED = []  # every sun position pick_sun scored: (score, azimuth, elevation, floor lit, all lit), best first


def pick_sun(scene, rays):
    """The sun position that puts about 20 % of the floor the camera sees in sun (the whole room if little floor
    is in view; an elevation near 30 deg on a tie); if no position reaches 2 %, the presentation look's sun."""
    dg = bpy.context.evaluated_depsgraph_get()
    pts = [r["hit"] for r in rays if r["interior"]]
    cam_z = bpy.context.scene.camera.matrix_world.translation.z
    up = Vector((0, 0, 1))

    def open_above(r):  # a sun patch reads as one on open floor, not under a table among chair legs
        hit = trace(scene, dg, r["hit"][0] + r["hit"][1] * 2e-3, up, far=1.6)
        return hit is None or (hit[0] - r["hit"][0]).length > 1.5 or "ceiling" in kinds(hit[2])

    floor = [r for r in rays if r["interior"] and r["hit"][1].z > 0.7 and r["hit"][0].z < cam_z - 0.9]
    floor = [r for r in floor if open_above(r)]
    use = floor if len(floor) >= 20 else [r for r in rays if r["interior"] and (r["hit"][1].z < 0.7 or open_above(r))][::2]
    if not use:
        return still.SUN["azimuth"], still.SUN["elevation"], 0.0
    weight = [0.3 + r["fy"] ** 2 for r in use]  # patches in the foreground count more
    every = [r for r in rays if r["interior"]][::4]
    best = None
    for az, el in sun_positions(LAT):
        a, e = math.radians(az), math.radians(el)
        s = Vector((math.sin(a) * math.cos(e), math.cos(a) * math.cos(e), math.sin(e)))
        def lights(r):
            return r["hit"][1].dot(s) > 0.05 and trace(scene, dg, r["hit"][0] + r["hit"][1] * 2e-3, s) is None

        if not any(lights(r) for r in every[::4]):  # a coarse pass: most positions light nothing in view
            continue
        lit = [lights(r) for r in use]
        frac = sum(w for w, ok in zip(weight, lit) if ok) / sum(weight)
        # any surface in view (walls, furniture), for the thumbnails' candidates
        frac_all = sum(lights(r) for r in every) / max(1, len(every))
        if os.environ.get("DEBUG"):
            SUN_DEBUG[(az, el)] = [(r["fx"], r["fy"]) for r, ok in zip(use, lit) if ok]
        score = abs(frac - 0.2) + (0.5 if frac < 0.05 else 0) + abs(el - 30) / 600
        RANKED.append((score, az, el, frac, frac_all))
        if best is None or score < best[0]:
            best = (score, az, el, frac, frac_all)
    RANKED.sort()
    if best is None:
        return still.SUN["azimuth"], still.SUN["elevation"], 0.0
    _, az, el, frac, _ = best
    if frac < 0.02:
        return still.SUN["azimuth"], still.SUN["elevation"], frac
    return az, el, frac


def lamps_on(scene, room):
    """A room the sun does not reach, photographed with its lights on (a bathroom's mirror light, its
    downlights): warm 2700 K, soft."""
    n = 0
    for o in scene.objects:
        if o.type != "LIGHT":
            continue
        x, y, z = to_scene(o.matrix_world.translation)
        if not (room["y"] + 1.0 <= y <= room["y"] + 3.2 and inside(room["polygon"], x, z)):
            continue
        o.hide_render = False
        o.data.color = (1.0, 0.78, 0.56)
        # warm accents under the window's light, not the key: at full strength (18-25 W) the room read as lit
        # from nowhere, with a hard streak from the mirror light (r1 judges); a soft source
        o.data.energy = (18.0 if o.data.type == "SPOT" else 25.0) * LAMPS
        o.data.shadow_soft_size = 0.12
        o.visible_camera = False  # the fixture's diffuser shows, not the bulb
        if o.data.type == "SPOT":
            o.data.spot_size = max(o.data.spot_size, math.radians(100))
            o.data.spot_blend = 0.6
        n += 1
    log(f"lamps: {n} on in {room['name']} (no sun)")


HDRI_WOOD = -50.0  # the HDRI's direction (degrees from +x, its own frame) where it shows only the wood


def aim_sun(scene, lamp, az, el):
    """Turn the sun lamp and the sky's sun (its glow and colour) to a compass azimuth and elevation."""
    sky = next(n for n in scene.world.node_tree.nodes if n.bl_idname == "ShaderNodeTexSky")
    sky.sun_elevation, sky.sun_rotation = math.radians(el), math.radians(az)
    lamp.rotation_euler = (math.pi / 2 - math.radians(el), 0.0, math.pi - math.radians(az))


def spread(ranked, n, apart=12.0):
    """The n best positions at least `apart` degrees from each other (thumbnails of near twins say nothing)."""
    out = []
    for _, az, el, _, frac in sorted(ranked, key=lambda r: abs(r[4] - 0.15)):
        if frac < 0.01:
            continue
        a = Vector((math.sin(math.radians(az)) * math.cos(math.radians(el)), math.cos(math.radians(az)) * math.cos(math.radians(el)), math.sin(math.radians(el))))
        if all(math.degrees(a.angle(b)) >= apart for b, _, _ in out):
            out.append((a, az, el))
        if len(out) == n:
            break
    return [(az, el) for _, az, el in out]


def linear_lum(scene, path):
    """Render at the current settings; the scene-linear luminance, rows top-down."""
    bpy.ops.render.render()
    bpy.data.images["Render Result"].save_render(path, scene=scene)
    img = bpy.data.images.load(path)
    w, h = img.size
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    return px.reshape(h, w, 4)[::-1, :, :3] @ np.array([0.2126, 0.7152, 0.0722], np.float32)


THUMB_W, THUMB_SAMPLES = 192, 24
PATCH_AT, PATCH_TARGET = 2.5, 0.12  # a ray's surface is in a sun patch when the sun adds 2.5x its sky light


def sun_thumbs(scene, rays, lamp, cands, sy, tmp):
    """Render each candidate sun small, once with the sky and once alone; keep the one whose sun patches fall
    on the floor and the walls (where a patch reads as sunlight, not as a brighter white bed) over about
    12 % of the room, foreground first. Returns (azimuth, elevation, patch fraction, the sun-to-sky ratio
    in the patches) or None."""
    rs, vs, cy = scene.render, scene.view_settings, scene.cycles
    keep = (rs.resolution_x, rs.resolution_y, cy.samples, rs.image_settings.file_format, vs.view_transform, vs.look, vs.exposure,
            rs.image_settings.color_depth)
    rs.resolution_x, rs.resolution_y = THUMB_W, round(THUMB_W * keep[1] / keep[0])
    cy.samples = THUMB_SAMPLES
    rs.image_settings.file_format = "OPEN_EXR"
    vs.view_transform, vs.exposure = "Raw", 0.0
    bg = scene.world.node_tree.nodes["Background"].inputs[1]
    sky = bg.default_value
    floor_y = sy + 0.25
    inside = [r for r in rays if r["interior"]]
    arch = [r["hit"][1].z > 0.7 and to_scene(r["hit"][0])[1] < floor_y or abs(r["hit"][1].z) < 0.2 and bool(kinds(r["hit"][2]) & {"wall", "partition", "featureWall"})
            for r in inside]
    weight = np.array([0.3 + r["fy"] ** 2 for r in inside])
    best, rows = None, []
    exr = tmp.replace(".png", ".exr")
    for i, (az, el) in enumerate(cands):
        aim_sun(scene, lamp, az, el)
        full = linear_lum(scene, exr)
        bg.default_value = 0.0
        sun = linear_lum(scene, exr)
        bg.default_value = sky
        h, w = sun.shape

        def at(img, r):
            x, y = min(w - 2, max(1, int(r["fx"] * w))), min(h - 2, max(1, int(r["fy"] * h)))
            return float(img[y - 1:y + 2, x - 1:x + 2].mean())

        s = np.array([at(sun, r) for r in inside])
        b = np.maximum(np.array([at(full, r) for r in inside]) - s, 1e-4)
        ratio = s / b
        lit = ratio > PATCH_AT
        patch = lit & np.array(arch)
        f_patch = float(weight[patch].sum() / weight.sum())
        f_lit = float(weight[lit].sum() / weight.sum())
        contrast = float(np.median(ratio[patch])) if patch.any() else 0.0
        score = abs(f_patch - PATCH_TARGET) + max(0.0, f_lit - 0.4) + (0.5 if f_patch < 0.03 else 0) + abs(el - 30) / 600
        rows.append(f"{az:.0f}/{el:.0f} patch {f_patch * 100:.0f} % lit {f_lit * 100:.0f} % x{contrast:.1f}")
        if os.environ.get("DEBUG"):
            t = np.clip((full / max(np.percentile(full, 60), 1e-4) * 0.35) ** (1 / 2.2), 0, 1)
            Image.fromarray((t * 255).astype(np.uint8)).save(tmp.replace(".png", f"-sun{i}.png"))
        if best is None or score < best[0]:
            best = (score, az, el, f_patch, contrast)
    (rs.resolution_x, rs.resolution_y, cy.samples, rs.image_settings.file_format, vs.view_transform, vs.look, vs.exposure,
     rs.image_settings.color_depth) = keep
    log("sun thumbnails: " + "; ".join(rows))
    return None if best is None else best[1:]


def set_sun(scene, az, el):
    still.SUN_AZ, still.SUN_ELEV, still.SUN_POWER = az, el, SUN_POWER
    os.environ["SKY"] = str(SKY)
    before = set(scene.objects)
    still.sun(scene)
    lamp = next(o for o in scene.objects if o not in before and o.type == "LIGHT")
    # the HDRI turned so the camera looks at its wood (its other side has a house and a car), whatever the sun:
    # the hand-tuned rooms (HDRI_TURN 257) did the same
    d = scene.camera.matrix_world.to_3x3() @ Vector((0, 0, -1))
    yaw = math.degrees(math.atan2(d.y, d.x))
    for node in scene.world.node_tree.nodes:
        if node.bl_idname == "ShaderNodeMapping":
            node.inputs["Rotation"].default_value[2] = math.radians(yaw - HDRI_WOOD)
    return lamp


# ---------------------------------------------------------------- the grade

def lab_ab(rgb):
    c = np.asarray(rgb, float) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = lin @ m.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return 500 * (f[0] - f[1]), 200 * (f[1] - f[2])


def wall_samples(rays):
    out = []
    for r in rays:
        if not r["interior"]:
            continue
        loc, n, obj, face = r["hit"]
        if abs(n.z) > 0.2 or not kinds(obj) & {"wall", "partition"} or kinds(obj) & {"window", "door", "interiorDoor"}:
            continue
        m, _ = material_at(obj, face)
        if m is None:
            continue
        q = principled(m)
        c = q.inputs["Base Color"].default_value
        if q.inputs["Base Color"].is_linked or min(c[:3]) > 0.5:
            out.append(r)
    return out


def measure(scene, rays, walls, path):
    bpy.data.images["Render Result"].save_render(path, scene=scene)
    a = np.asarray(Image.open(path).convert("RGB")).astype(float)
    h, w, _ = a.shape

    def px(r):
        x, y = min(w - 2, max(1, int(r["fx"] * w))), min(h - 2, max(1, int(r["fy"] * h)))
        return a[y - 1:y + 2, x - 1:x + 2].reshape(-1, 3).mean(axis=0)

    room = [px(r) for r in rays if r["interior"]]
    lum = np.median([p @ np.array([0.2126, 0.7152, 0.0722]) for p in room]) if room else 128.0
    ab = lab_ab(np.mean([px(r) for r in walls], axis=0)) if walls else None
    return lum, ab


def grade(scene, rays, tmp):
    vs = scene.view_settings
    vs.view_transform = "AgX"
    vs.look = LOOK
    vs.use_white_balance = True
    vs.white_balance_temperature = 6500.0
    vs.white_balance_tint = 0.0
    vs.exposure = 0.0
    walls = wall_samples(rays)

    def clipped():
        return float((np.asarray(Image.open(tmp).convert("RGB")).max(axis=2) >= 250).mean())

    def solve_exposure():
        lo, hi = -2.0, 3.0
        for _ in range(9):
            vs.exposure = (lo + hi) / 2
            lum, _ = measure(scene, rays, walls, tmp)
            lo, hi = (vs.exposure, hi) if lum < TARGET_MEDIAN else (lo, vs.exposure)
        vs.exposure = (lo + hi) / 2
        measure(scene, rays, walls, tmp)
        if clipped() <= CLIP_MAX:
            return
        lo, hi = vs.exposure - CLIP_PULL, vs.exposure  # a darker finish lifts the median's exposure: protect the highlights
        vs.exposure = lo
        measure(scene, rays, walls, tmp)
        if clipped() > CLIP_MAX:
            return
        for _ in range(6):
            vs.exposure = (lo + hi) / 2
            measure(scene, rays, walls, tmp)
            lo, hi = (lo, vs.exposure) if clipped() > CLIP_MAX else (vs.exposure, hi)
        vs.exposure = lo

    def solve(attr, lo, hi, index, target, n=8):
        """Bisect a view setting so the walls' a* (index 0) or b* (1) meets the target; the setting's direction
        is measured at both ends, and an unreachable target leaves it at the nearer end."""
        if not walls:
            return

        def at(v):
            setattr(vs, attr, v)
            return measure(scene, rays, walls, tmp)[1][index] - target

        e_lo, e_hi = at(lo), at(hi)
        if e_lo * e_hi > 0:
            setattr(vs, attr, lo if abs(e_lo) < abs(e_hi) else hi)
            return
        for _ in range(n):
            mid = (lo + hi) / 2
            e = at(mid)
            lo, hi, e_lo = (mid, hi, e) if e * e_lo > 0 else (lo, mid, e_lo)
        setattr(vs, attr, (lo + hi) / 2)

    solve_exposure()
    solve("white_balance_temperature", 4000.0, 12000.0, 1, TARGET_WALL_B)
    solve("white_balance_tint", -30.0, 30.0, 0, TARGET_WALL_A, 6)
    solve("white_balance_temperature", 4000.0, 12000.0, 1, TARGET_WALL_B)
    solve_exposure()
    lum, ab = measure(scene, rays, walls, tmp)
    clip = clipped()
    log(f"grade: exposure {vs.exposure:+.2f}, white {vs.white_balance_temperature:.0f} K, tint "
        f"{vs.white_balance_tint:+.1f} -> room median {lum:.0f}, wall a* b* "
        f"{ab if ab is None else (round(ab[0], 1), round(ab[1], 1))} ({len(walls)} wall samples), "
        f"{clip * 100:.1f} % of the frame at white")


# ---------------------------------------------------------------- main

def use_gpu():
    addon_utils.enable("cycles", default_set=True)
    prefs = bpy.context.preferences.addons["cycles"].preferences
    wsl = "microsoft" in open("/proc/version").read().lower() if os.path.exists("/proc/version") else False
    for kind in os.environ.get("PHOTO_DEVICE", "CUDA" if wsl else "OPTIX,CUDA").split(","):
        prefs.compute_device_type = kind
        prefs.refresh_devices()
        devs = [d for d in prefs.devices if d.type == kind]
        if devs:
            for d in prefs.devices:
                d.use = d.type == kind
            return kind, [d.name for d in devs]
    raise SystemExit("no CUDA or OptiX GPU")


def main():
    blend, rooms_json, cam_spec, out, *rest = sys.argv[1:]
    width, height, samples = (int(v) for v in rest) if rest else (1920, 1080, 256)
    t0 = time.time()
    rooms = json.load(open(rooms_json, encoding="utf-8"))
    rooms = rooms["rooms"] if isinstance(rooms, dict) else rooms
    bpy.ops.wm.open_mainfile(filepath=blend)
    device = use_gpu()
    scene = bpy.context.scene
    still.base(scene)
    scene.render.use_persistent_data = True
    still.lamps_off(scene)
    still.outside(scene)
    still.glass(scene)
    ground(scene)
    pos = [float(v) for v in cam_spec.split(">")[0].split(",")]
    room = room_at(rooms, *pos)
    if room is None:
        raise SystemExit(f"the camera {pos} is in no room of rooms.json")
    sy = room["y"]
    log(f"room: {room['name']} ({room['use']}), storey y {sy}; {device}")
    floors(scene, rooms, sy, room)
    skip = os.environ.get("SKIP", "").split(",")  # steps left out, for experiments
    if "inner" not in skip:
        inner_walls(scene, rooms)
    dress.plaster(scene)
    if "tiles" not in skip:
        glaze_tiles(scene)
    if CEILING == "concrete" and room["use"] not in ("bath", "wc"):  # wet rooms keep a painted ceiling
        concrete_ceilings(scene)
    if "paint" not in skip:
        paint(scene)
    dress.linen(scene)
    upholster_chairs(scene)
    dress_storey(scene, sy)
    if "feature" not in skip:
        feature_wall(scene, room, sy)
        feature_tiles(scene, room, sy)
    scene.render.resolution_x, scene.render.resolution_y = width, height  # view_frame() reads the aspect from it
    still.camera(scene, cam_spec, width, height)
    clear_edges(scene, scene.camera)
    rays = camera_rays(scene, scene.camera, rooms, sy)
    pave(rays, sy, rooms)
    if hang_art(scene, rays, sy):
        rays = camera_rays(scene, scene.camera, rooms, sy)
    t = time.time()
    az, el, frac = pick_sun(scene, rays)
    log(f"sun: azimuth {az:.0f}, elevation {el:.0f}, {frac * 100:.0f} % of the room in sun ({time.time() - t:.0f} s)")
    lamp = set_sun(scene, az, el)
    cands = spread(RANKED, THUMBS)
    if os.environ.get("SUN_PICK"):  # "az/el": a fixed sun, for experiments
        az, el = (float(v) for v in os.environ["SUN_PICK"].split("/"))
        aim_sun(scene, lamp, az, el)
        cands = []
    if THUMBS and len(cands) > 1:
        t = time.time()
        pick = sun_thumbs(scene, rays, lamp, cands, sy, out.replace(".png", "-thumb.png"))
        if pick:
            az, el, frac, contrast = pick
            aim_sun(scene, lamp, az, el)
            log(f"sun: thumbnails pick {az:.0f}/{el:.0f}, sun patches on {frac * 100:.0f} % of the room, x{contrast:.1f} "
                f"the sky's light there ({len(cands)} candidates, {time.time() - t:.0f} s)")
    if frac < 0.05 and not (THUMBS and len(cands) > 1 and frac >= 0.03):
        lamps_on(scene, room)
    log(f"setup {time.time() - t0:.0f} s")
    # the preview for the grade, at the final's aspect
    t = time.time()
    scene.render.resolution_x, scene.render.resolution_y = 768, round(768 * height / width)
    scene.cycles.samples = 32
    bpy.ops.render.render()
    log(f"preview {time.time() - t:.0f} s")
    for power in os.environ.get("PREVIEW_POWERS", "").split(",") if os.environ.get("PREVIEW_POWERS") else []:
        lamp.data.energy = float(power)  # the same scene at other sun strengths, for experiments
        bpy.ops.render.render()
        grade(scene, rays, out.replace(".png", f"-p{power}.png"))
        bpy.data.images["Render Result"].save_render(out.replace(".png", f"-p{power}.png"), scene=scene)
    lit = [o for o in scene.objects if o.type == "LIGHT" and not o.hide_render and o != lamp]
    base = {o.name: o.data.energy for o in lit}
    for k in os.environ.get("PREVIEW_LAMPS", "").split(",") if os.environ.get("PREVIEW_LAMPS") else []:
        for o in lit:  # the room's lamps at other strengths, for experiments
            o.data.energy = base[o.name] * float(k)
        bpy.ops.render.render()
        grade(scene, rays, out.replace(".png", f"-l{k}.png"))
        bpy.data.images["Render Result"].save_render(out.replace(".png", f"-l{k}.png"), scene=scene)
    if os.environ.get("PREVIEW_POWERS") or os.environ.get("PREVIEW_LAMPS"):
        lamp.data.energy = SUN_POWER
        for o in lit:
            o.data.energy = base[o.name]
        bpy.ops.render.render()
    grade(scene, rays, out.replace(".png", "-preview.png"))
    bpy.data.images["Render Result"].save_render(out.replace(".png", "-preview.png"), scene=scene)
    if os.environ.get("DEBUG"):  # the floor samples the chosen sun lights, as red dots on the preview
        im = Image.open(out.replace(".png", "-preview.png")).convert("RGB")
        px = im.load()
        for fx, fy in SUN_DEBUG.get((az, el), []):
            x, y = int(fx * im.width), int(fy * im.height)
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    px[min(im.width - 1, max(0, x + dx)), min(im.height - 1, max(0, y + dy))] = (255, 0, 0)
        im.save(out.replace(".png", "-sun.png"))
    if os.environ.get("PREVIEW_ONLY"):
        return
    t = time.time()
    scene.render.resolution_x, scene.render.resolution_y = width, height
    scene.cycles.samples = samples
    scene.cycles.adaptive_threshold = float(os.environ.get("ADAPTIVE", scene.cycles.adaptive_threshold))
    scene.cycles.denoising_prefilter = os.environ.get("PREFILTER", scene.cycles.denoising_prefilter)
    if os.environ.get("CROP"):  # "x0,x1,y0,y1" in frame fractions, y up: a full-resolution crop, for experiments
        rs = scene.render
        rs.border_min_x, rs.border_max_x, rs.border_min_y, rs.border_max_y = (float(v) for v in os.environ["CROP"].split(","))
        rs.use_border, rs.use_crop_to_border = True, True
    bpy.ops.render.render()
    raw = out.replace(".png", "-raw.png")
    bpy.data.images["Render Result"].save_render(raw, scene=scene)
    post(raw, out)
    # the scene-linear frame and the grade, for regrading without a render (regrade.py)
    vs, ims = scene.view_settings, scene.render.image_settings
    keep = (vs.view_transform, vs.look, vs.exposure, vs.use_white_balance, ims.file_format, ims.color_depth)
    vs.view_transform, vs.exposure, vs.use_white_balance = "Raw", 0.0, False
    ims.file_format, ims.color_depth = "OPEN_EXR", "16"
    bpy.data.images["Render Result"].save_render(out.replace(".png", "-linear.exr"), scene=scene)
    vs.view_transform, vs.look, vs.exposure, vs.use_white_balance, ims.file_format, ims.color_depth = keep
    json.dump({"view_transform": vs.view_transform, "look": vs.look, "exposure": vs.exposure,
               "temperature": vs.white_balance_temperature, "tint": vs.white_balance_tint},
              open(out.replace(".png", "-grade.json"), "w"))
    log(f"final {width}x{height} {samples} samples {time.time() - t:.0f} s; total {time.time() - t0:.0f} s -> {out}")


if __name__ == "__main__":
    main()
