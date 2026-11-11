"""A listing photo of a room of any housegen house, with no per-room settings: every choice made from the house itself.

What it decides, from the export (house.blend from export.py's GLB) and rooms.json:
- the storey: the room under the camera (rooms.json); everything below is done for that storey;
- finishes: the project's own, each textured finish at its Poly Haven set's full resolution where the set is there
  (finish_textures), plaster relief and a faint unevenness on paint, glazed tiles, linen curtains;
- dressing, from the kit's own pieces (userData name + footprint) and the object library: rugs, plants, wall art,
  armchairs with a throw, beds with cloth-simulated bedding (made/bedset.py, cached per size), cushions on sofas,
  tables, counters and bathrooms dressed;
- the sun: a real sun position for the site's latitude that puts sun patches in the room the camera sees (camera
  rays, then shadow rays toward each candidate, then thumbnails);
- the grade: AgX High Contrast; exposure and white balance from a preview, to a target median of the room and
  a target warmth of its walls.
"""

import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time

import addon_utils
import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import artlib  # noqa: E402
import dress  # noqa: E402
import objlib  # noqa: E402
import paths  # noqa: E402
import stage  # noqa: E402
import staging
import style  # noqa: E402
import still  # noqa: E402
from dress import model, place, principled  # noqa: E402
from post import post  # noqa: E402

LAT = 46.5
SKY = 0.55  # the hand-tuned rooms' value (0.5-0.6)
# the sun lamp against the sky: at 16 a patch on the floor or a wall is 5-8x the sky's light around it (5.5, the
# hand-tuned rooms' value, gave 2-3x: patches the judges did not see, "flat light")
SUN_POWER = 16.0
# Listing photos show the finishes as specified (#91). Lit by daylight and warm room lamps only, the white balance that
# keeps the lamp-lit walls white turns white tiles in the window's light blue, and a white ceiling above windows that
# stop short of it goes dark. Listing photographers add a flash bounced off the ceiling (flambient); 150 W makes both
# neutral (2026-11-11: Montelly Bain tiles b-r +38 -> 0, attic ceiling 64 -> ~120 sRGB). Env override for tuning only.
FLASH_W = float(os.environ.get("PHOTO_FLASH_W", "150"))
LAMPS = 0.4  # the lamps of a room without sun, x their full strength
THUMBS = 10  # candidate suns rendered as thumbnails
LOOK = "AgX - High Contrast"
TARGET_MEDIAN, TARGET_WALL_B, TARGET_WALL_A = 155.0, 4.0, 0.5  # sRGB luminance 0-255 of the room; CIELAB b*, a* of its walls
CLIP_MAX = 0.03  # at most this share of the frame at white...
CLIP_PULL = 0.5  # ...by lowering the median's exposure at most this many stops (darker than that reads as a grey veil)
CACHE = paths.CACHE
EUCALYPTUS = os.path.join(paths.MADE, "eucalyptus.glb")
THROW = os.path.join(paths.MADE, "throw.glb")
PREVIEW_W, PREVIEW_SAMPLES = 768, 32
FINISHES_JS = os.path.join(HERE, "..", "..", "kit", "finishes.js")
BED_SIZES = (0.9, 1.2, 1.4, 1.6, 1.8)


def log(*a):
    print("PHOTO", *a, flush=True)


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


def finish_sets(path=FINISHES_JS):
    """kit/finishes.js TEXTURES: finish name -> (Poly Haven id, the kit's grade of its colour map or None)."""
    if not os.path.exists(path):
        return {}
    src = open(path, encoding="utf-8").read()
    block = src[src.index("export const TEXTURES = {"):]
    block = block[:block.index("\n};")]
    out = {}
    for line in block.splitlines():
        m = re.match(r'\s*(?:"([\w-]+)"|([\w-]+)):\s*\{(.*)\},?\s*$', line)
        tex = m and re.search(r'\bid:\s*"(\w+)"', m.group(3))
        if not tex:
            continue
        g = re.search(r"\bgrade:\s*\{([^}]*)\}", m.group(3))
        grade = {k: float(v) for k, v in re.findall(r"(\w+):\s*(-?[\d.]+)", g.group(1))} if g else None
        out[m.group(1) or m.group(2)] = (tex.group(1), grade)
    return out


_GRADED = {}


def graded(path, grade):
    """The colour map with kit/finishes.js gradeTexture applied (the same sums on 8-bit sRGB values), so the scan
    at full resolution is the finish the viewer shows, not the raw photograph of it."""
    if not grade:
        return path
    key = (path, tuple(sorted(grade.items())))
    if key in _GRADED:
        return _GRADED[key]
    px = np.asarray(Image.open(path).convert("RGB"), dtype=np.float64)
    sat, con, bri, hue = (grade.get(k, d) for k, d in (("saturation", 1), ("contrast", 1), ("brightness", 1), ("hue", 0)))
    if hue:
        a = math.radians(hue)
        c, s_ = math.cos(a), math.sin(a)
        m = np.array([
            [0.213 + c * 0.787 - s_ * 0.213, 0.715 - c * 0.715 - s_ * 0.715, 0.072 - c * 0.072 + s_ * 0.928],
            [0.213 - c * 0.213 + s_ * 0.143, 0.715 + c * 0.285 + s_ * 0.14, 0.072 - c * 0.072 - s_ * 0.283],
            [0.213 - c * 0.213 - s_ * 0.787, 0.715 - c * 0.715 + s_ * 0.715, 0.072 + c * 0.928 + s_ * 0.072],
        ])
        px = px @ m.T
    y = px @ np.array([0.2126, 0.7152, 0.0722])
    px = y[..., None] + (px - y[..., None]) * sat
    px = ((px - 128) * con + 128) * bri
    out = os.path.join(tempfile.mkdtemp(prefix="finish-"), os.path.basename(os.path.dirname(path)) + "-diff.png")
    Image.fromarray(np.clip(np.rint(px), 0, 255).astype(np.uint8)).save(out)
    _GRADED[key] = out
    return out


def finish_textures(scene):
    """The project's finishes at full resolution: a material whose finish (userData.finish) has its Poly Haven set
    under PHOTO_ASSETS/textures/<id>/ takes that set's colour, normal and roughness maps in place of the export's
    512 px ones, on the importer's own mapping. The colour map gets the kit's grade, then is scaled to the export
    map's mean (what is left: the 1k against the 2k scan); a finish without its set keeps the export's maps."""
    sets = finish_sets()
    if not sets:
        log(f"finishes: no {FINISHES_JS}, the export's maps stay")
        return {}, []
    done, kept, gains = {}, set(), {}
    for m in bpy.data.materials:
        f = m.get("finish")
        if f not in sets or m.node_tree is None:
            continue
        tex, grade = sets[f]
        d = os.path.join(dress.TEX, tex)
        files = {k: os.path.join(d, f"{k}.jpg") for k in ("diff", "nor_gl", "rough")}
        if not all(os.path.exists(v) for v in files.values()):
            kept.add(f"{f} ({tex})")
            continue
        nt = m.node_tree
        for n in [n for n in nt.nodes if n.bl_idname == "ShaderNodeTexImage" and n.image]:
            to = {lk.to_node.bl_idname for lk in n.outputs["Color"].links}
            if "ShaderNodeNormalMap" in to:
                n.image = dress.image(files["nor_gl"], True)
            elif "ShaderNodeSeparateColor" in to:  # glTF metallicRoughness: roughness in G, metalness (factor 0) in B
                n.image = dress.image(files["rough"], True)
            elif n.image.colorspace_settings.name == "sRGB":
                new = dress.image(graded(files["diff"], grade))
                if n.image.name not in gains:
                    gains[n.image.name] = np.clip(dress.mean_linear(n.image) / np.maximum(dress.mean_linear(new), 1e-3), 0.3, 3.0)
                gain = nt.nodes.new("ShaderNodeMixRGB")
                gain.blend_type = "MULTIPLY"
                gain.inputs[0].default_value = 1.0
                gain.inputs[2].default_value = (*gains[n.image.name], 1)
                for lk in list(n.outputs["Color"].links):
                    nt.links.new(gain.outputs[0], lk.to_socket)
                nt.links.new(n.outputs["Color"], gain.inputs[1])
                n.image = new
        done.setdefault(f, []).append(m.name)
    log(f"finishes: full resolution {sorted((f, len(v)) for f, v in done.items())}; export's maps kept {sorted(kept)}")
    return done, sorted(kept)


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


MOTTLE = 0.025  # the paint's slow unevenness, ± this share of its tone


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


TILE_TONE = 0.05


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


def swap_rugs(scene, pieces):
    n = 0
    for o in pieces:
        if name(o) != "rug":
            continue
        lo, _ = box(o)
        w, d = o["footprint"]
        t = o.matrix_world.translation
        hide(o)
        if objlib.LIB:
            stage.rug(scene, stage.room_of(o), (t.x, -t.y), (w, d), lo.z, turn(o))
        else:
            r = dress.rug(scene, (t.x, -t.y), (w, d), floor=lo.z)
            r.rotation_euler[2] = turn(o)
        n += 1
    return n


def swap_plants(scene, pieces, sy):
    n = 0
    for o in pieces:
        if not name(o).startswith("plant-") or o.hide_render:  # stage.swap_kit took it
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


def art_room(x, y, z):
    """The room a print at scene point (x, y, z) belongs to: the room there, or the first one a step around it
    (a point on a wall can fall just outside the room's polygon)."""
    for dx, dz in ((0, 0), (0.3, 0), (-0.3, 0), (0, 0.3), (0, -0.3)):
        r = room_at(artlib.ROOMS, x + dx, y, z + dz)
        if r is not None:
            return r
    return None


def ledge_lift(scene, dg, at, n, w, z0, ph):
    """How far a print (bottom centre `at` on the wall, facing `n`, `w` wide, bottom at z0, `ph` tall) goes up so it
    doesn't sit on what stands in front of the wall (a half-wall, a high headboard): 0 when nothing's top is within
    0.12 m below its bottom, else up to 0.2 m above that top; None when the ceiling is too close for that."""
    side, top = Vector((-n.y, n.x, 0)), None

    def standing(o):  # a half-wall or a piece on the floor (a low wardrobe), not a pendant
        while o.parent is not None and o.get("kind") is None:
            o = o.parent
        return box(o)[0].z < z0 - 1.0

    for d in (0.06, 0.15, 0.25, 0.4, 0.6, 0.8, 1.0, 1.2):
        for s in (-w / 3, 0.0, w / 3):
            p = Vector((at.x, at.y, z0 + 0.15)) + n * d + side * s
            hit = trace(scene, dg, p, Vector((0, 0, -1)), far=0.5)
            if hit and hit[1].z > 0.9 and not any(a.name.startswith("art.glb") for a in ancestors(hit[2])) \
                    and (d < 0.45 or standing(hit[2])):
                top = hit[0].z if top is None else max(top, hit[0].z)
    if top is None or top < z0 - 0.12:
        return 0.0
    lift = top + 0.2 - z0
    up = trace(scene, dg, Vector((at.x, at.y, z0 + ph)) + n * 0.1, Vector((0, 0, 1)), far=3.0)
    if up is not None and up[0].z - (z0 + ph + lift) < 0.15:
        return None
    return lift


def swap_art(scene, pieces):
    """The kit's wall art becomes the room's prints from the library (artlib), each fitted to the kit piece's
    box (at most about 1 m on the long side), lifted off a half-wall or headboard it would sit on."""
    n, per_room = 0, {}
    dg = bpy.context.evaluated_depsgraph_get()
    for o in pieces:
        nm = name(o).lower()
        if "wall art" not in nm and "wall-art" not in nm and " art in " not in f" {nm} ":  # Montelly: "abstract art in oak frame"
            continue
        lo, hi = box(o)
        w, h = o["footprint"][0], hi.z - lo.z
        c = (lo + hi) / 2
        room = art_room(c.x, lo.z, -c.y)
        k = per_room[room["name"] if room else ""] = per_room.get(room["name"] if room else "", 0) + 1
        e = artlib.pick(room or "", f"swap{k}", y=lo.z)
        print(f"PHOTO art: kit print at ({c.x:.2f}, {lo.z:.2f}, {-c.y:.2f}) in {room['name'] if room else 'no room'}: swap{k}", flush=True)
        long = min(artlib.fit(e, w * 1.2, h * 1.2), 0.98)
        pw, ph = artlib.size(e, long)
        t = math.radians(math.degrees(turn(o)) + 180)
        z0 = lo.z + (h - ph) / 2
        hide(o)
        lift = ledge_lift(scene, dg, c, Vector((-math.sin(t), math.cos(t), 0)), pw, z0, ph)
        if lift is None:
            log("art: a kit print sits on a ledge under the ceiling, left out")
            continue
        if lift:
            log(f"art: a kit print sits on a ledge, {lift:.2f} m up")
        artlib.build(scene, e, (c.x, z0 + lift, -c.y), long, math.degrees(t))
        n += 1
    return n


def swap_armchairs(scene, pieces, decor=True):
    n = 0
    for o in pieces:
        # a catalogue piece (#90, its `model`) is already a real chair, one of many: it stays
        if "armchair" not in name(o).lower() or o.get("model"):
            continue
        lo, _ = box(o)
        t = o.matrix_world.translation
        hide(o)
        root, _ = place(scene, model("mid_century_lounge_chair"), (t.x, lo.z, -t.y), turn_deg=math.degrees(turn(o)))
        if decor and n == 0:  # one throw in the room, not one per chair
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


BEDS = []  # (frame at the head of the bed, facing -y; width) of the beds made


def swap_beds(scene, pieces, decor=True):
    n = 0
    linen, fabric = dress.linen_material(), upholstery_material()
    wool = dress.wool_material((0.5, 2.2), (0.62, 0.61, 0.6))
    for o in pieces:
        nm, fp = name(o).lower(), list(o.get("footprint", ()))
        if "bed" not in nm or "bedside" in nm or len(fp) < 2 or fp[1] < 1.8:
            continue
        lo, _ = box(o)
        frame = frame_of(o, lo.z) @ Matrix.Translation((0, fp[1] / 2, 0))
        if o.get("model"):  # a catalogue bed (#90): one of many, its frame stays
            n += make_up(scene, o, frame, linen, wool, decor)
            BEDS.append((frame, float(next(iter(o.get("nominal") or fp)))))
            continue
        w = max([s for s in BED_SIZES if s + 0.2 <= fp[0]] or [BED_SIZES[0]])
        length = 2.0
        rot = turn(o)
        hide(o)
        path = bedset(w, length)
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
        if decor:
            cushions, _ = place(scene, model("throw_pillows_01"), to_scene(frame @ Vector((0, -0.68, top + 0.03))),
                                height=0.38, turn_deg=math.degrees(rot))
            recolour(cushions, velvet((0.16, 0.19, 0.13)))
        BEDS.append((frame, w))
        n += 1
        log(f"bed: {nm} {fp[0]:.2f} x {fp[1]:.2f} -> {w} x {length}")
    return n


def make_up(scene, o, frame, linen, wool, decor):
    """A catalogue bed made up as the kit's beds are: the kit's bedding (rounded blocks, the piece's own meshes; the
    model is under a child) gives way to a bedset's cloth duvet and throw on its mattress, cushions with `decor`.
    The blocks read as a bare white box in Cycles, and the judges marked the bedroom down for it (#94)."""
    blocks = [c for c in o.children if c.type == "MESH"]
    if not blocks:
        return 0
    inv = frame.inverted()
    spans = {}
    for c in blocks:
        ps = [inv @ (c.matrix_world @ Vector(v)) for v in c.bound_box]
        spans[c] = (Vector([min(p[i] for p in ps) for i in range(3)]), Vector([max(p[i] for p in ps) for i in range(3)]))
    lo, hi = max(spans.values(), key=lambda s: (s[1].x - s[0].x) * (s[1].y - s[0].y))
    # furnish.js dressBed: the duvet is the mattress's width + 8 cm by 68 % of its length + 4 cm, from 2 cm under its
    # top and 5 cm past its foot
    w, length = hi.x - lo.x - 0.08, (hi.y - lo.y - 0.04) / 0.68
    top, head, cx = lo.z + 0.02, lo.y + 0.05 + length, (lo.x + hi.x) / 2
    pillowed = all(s[1].y < head - 0.3 for s in spans.values())
    for c in blocks:
        hide(c)
    size = (max(0.9, round(w * 10) / 10), round(length * 10) / 10)
    # the bedset's mattress starts 13 cm from its head wall, its top 52 cm up
    at = frame @ Matrix.Translation((cx, head + 0.13, top - 0.52))
    root = bpy.data.objects.new("bedset", None)
    scene.collection.objects.link(root)
    root.matrix_world = at
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=bedset(*size))
    for c in [c for c in bpy.data.objects if c not in before]:
        if c.name.startswith("bed-"):
            bpy.data.objects.remove(c, do_unlink=True)
            continue
        if c.parent is None:
            c.parent = root
            c.matrix_parent_inverse.identity()
        if c.type == "MESH":
            c.data.materials.clear()
            c.data.materials.append(wool if c.name.startswith("throw") else linen)
    rot = turn(o)
    if not pillowed:
        pillows, _ = place(scene, model("throw_pillows_01"), to_scene(at @ Vector((0, -0.30, 0.52))), height=0.52,
                           turn_deg=math.degrees(rot))
        recolour(pillows, linen)
        pillows.scale = (pillows.scale[0] * 1.45 * size[0] / 1.6, pillows.scale[1], pillows.scale[2])
    if decor:
        cushions, _ = place(scene, model("throw_pillows_01"), to_scene(at @ Vector((0, -0.68, 0.55))), height=0.38,
                            turn_deg=math.degrees(rot))
        recolour(cushions, velvet((0.16, 0.19, 0.13)))
    log(f"bed: {o.get('model')} made up, mattress {w:.2f} x {length:.2f} top {top:.2f} -> bedset {size}")
    return 1


CUSHION_OAT, CUSHION_RUST = (0.50, 0.45, 0.37), (0.28, 0.11, 0.05)  # linear albedo: an oatmeal boucle, a muted rust


def cushion(scene, which, at, turn_rad, scale, material):
    """One of the CC0 pair (throw_pillows_01: pillow01 0.57 m, pillow02 0.52 m wide, slumped as on a seat), alone,
    its base centred on Blender point `at`, facing -y turned by turn_rad."""
    root, _ = place(scene, model("throw_pillows_01"), (0.0, 0.0, 0.0))
    keep = [c for c in root.children_recursive if c.type == "MESH" and c.name.split(".")[0].endswith(which)]
    for c in [c for c in root.children_recursive if c.type == "MESH" and c not in keep]:
        bpy.data.objects.remove(c, do_unlink=True)
    bpy.context.view_layer.update()
    ps = [c.matrix_world @ Vector(v) for c in keep for v in c.bound_box]
    mid = Vector(((min(p.x for p in ps) + max(p.x for p in ps)) / 2, (min(p.y for p in ps) + max(p.y for p in ps)) / 2,
                  min(p.z for p in ps)))
    for c in keep:
        c.matrix_world = Matrix.Translation(-mid) @ c.matrix_world
        c.data = c.data.copy()
        c.data.materials.clear()
        c.data.materials.append(material)
    root.scale = (scale, scale, scale)
    root.rotation_euler[2] = turn_rad
    root.location = at
    return root


def arm_pillows_out(o, f, seat):
    """The kit's own pillows standing against a sofa's arms (on the seat, thin along the sofa's length) make room
    for ours and the throw, as a stager would; the ones against the back stay (on a low-backed kit sofa they are its
    back cushions). Whole loose parts of the sofa's meshes (a kit pillow may be two shells); returns how many went."""
    inv = f.inverted()
    n = 0
    done = set()
    for c in family(o):
        if c.type != "MESH" or c.data.name in done:
            continue
        done.add(c.data.name)
        bm = bmesh.new()
        bm.from_mesh(c.data)
        to_f = inv @ c.matrix_world
        seen, drop = set(), []
        for v in bm.verts:
            if v in seen:
                continue
            part, stack = [], [v]
            seen.add(v)
            while stack:
                x = stack.pop()
                part.append(x)
                for e in x.link_edges:
                    y = e.other_vert(x)
                    if y not in seen:
                        seen.add(y)
                        stack.append(y)
            ps = [to_f @ x.co for x in part]
            lo = Vector([min(p[i] for p in ps) for i in range(3)])
            hi = Vector([max(p[i] for p in ps) for i in range(3)])
            size = hi - lo
            on_seat = abs(lo.z - seat) < 0.06 and 0.15 < size.z < 0.45
            if on_seat and size.x < 0.6 and size.y < 0.6 and size.x < 0.6 * size.y:  # thin along the sofa: an arm's
                drop.extend(part)
                n += 1
        if drop:
            bmesh.ops.delete(bm, geom=list(set(drop)), context="VERTS")
            bm.to_mesh(c.data)
            c.data.update()
        bm.free()
    return n


def sofa_measure(scene, pieces):
    """Each sofa's seat height, its back's front face and its arms' inner faces, from rays down its depth and width,
    kept on the piece for style.sofa_dress (which places cushions and a throw once the camera is known)."""
    n = 0
    dg = bpy.context.evaluated_depsgraph_get()
    for o in pieces:
        if "sofa" not in name(o).lower() or len(o.get("footprint", ())) != 2:
            continue
        w, d = o["footprint"]
        lo, hi = box(o)
        f = frame_of(o, lo.z)
        top = hi.z - lo.z + 0.1
        vs = [-d / 2 + 0.02 + 0.02 * k for k in range(int((d - 0.04) / 0.02) + 1)]
        prof = [(v, h) for v, (h, _) in zip(vs, heights_down(scene, dg, f, [(0.0, v) for v in vs], top)) if h is not None]
        front = sorted(h for _, h in prof[: len(prof) // 2] if 0.25 <= h <= 0.65)  # the seat, not its plinth's edge
        seat = front[len(front) // 2] if len(front) >= 3 else None
        back = next((v for v, h in prof if v > prof[0][0] + 0.15 and h > seat + 0.12), None) if seat else None
        if seat is None or back is None:
            log(f"sofa {name(o)}: no seat and back found (heights {[round(h, 2) for _, h in prof]})")
            continue
        gone = arm_pillows_out(o, f, seat)
        if gone:
            bpy.context.view_layer.update()
            dg = bpy.context.evaluated_depsgraph_get()
        sv = (prof[0][0] + back) / 2  # the seat's middle, front to back
        vf = prof[0][0] + 0.10  # along the seat's front: loose back cushions do not reach it, the arms do
        us = [-w / 2 + 0.02 + 0.02 * k for k in range(int((w - 0.04) / 0.02) + 1)]
        row = [(u, h) for u, (h, _) in zip(us, heights_down(scene, dg, f, [(u, vf) for u in us], top))]
        flat = [u for u, h in row if h is not None and abs(h - seat) < 0.05]
        if not flat:
            continue
        a0, a1 = min(flat), max(flat)  # where the seat ends at each arm
        o["archviz_sofa"] = [seat, back, a0, a1, sv, vf]
        log(f"sofa {name(o)} ({w:.2f} x {d:.2f}): seat {seat:.2f} m, back at {back:+.2f}, seat between {a0:+.2f} {a1:+.2f}"
            f"{f', {gone} pillow parts against the arms taken out' if gone else ''}")
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
            if objlib.LIB:
                stage.rug(scene, stage.room_of(o), (t.x, -t.y), (fp[0] + 1.1, fp[1] + 1.1), lo.z, turn(o))
            else:
                r = dress.rug(scene, (t.x, -t.y), (fp[0] + 1.1, fp[1] + 1.1), floor=lo.z)  # the chairs stay on it
                r.rotation_euler[2] = turn(o)
        if objlib.LIB and objlib.choose(stage.room_of(o), "runner", [True, False]) is False:
            n += 1
            continue  # no runner in this room; stage.dining sets the table
        dress.runner(scene, (t.x, hi.z + 0.002, -t.y), (0.30, max(fp) - 0.3), turn(o) + (0 if along_d else math.pi / 2))
        if objlib.LIB:
            n += 1
            continue
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
    if objlib.LIB:
        at = (0.5,) if length * step < 0.9 else (0.25, 0.72) if length * step < 1.6 else (0.18, 0.5, 0.82)
        points = [f @ Vector((us[start] + length * step * k - step / 2, -0.05, 0)) for k in at]
        return stage.counter(scene, stage.room_of(run), points, z, turn(run))
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


def cloth_weave(rgb, scale=0.05, rough=0.85, sheen=0.3, name="archviz-cloth"):
    """A woven cloth (book cloth, napkin linen): the linen scan's weave as relief at `scale` metres a tile, in a flat
    colour with a faint mottle."""
    m = bpy.data.materials.new(name)
    nt = m.node_tree
    p = principled(m)
    d = os.path.join(dress.TEX, "rough_linen")
    tc = node(nt, "ShaderNodeTexCoord")
    mp = node(nt, "ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (1 / scale,) * 3
    nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])
    vec = mp.outputs["Vector"]
    mottle = node(nt, "ShaderNodeTexNoise", Scale=40.0, Detail=4.0)
    nt.links.new(tc.outputs["Object"], mottle.inputs["Vector"])
    mix = node(nt, "ShaderNodeMapRange")
    mix.inputs["To Min"].default_value, mix.inputs["To Max"].default_value = 0.92, 1.06
    nt.links.new(mottle.outputs["Fac"], mix.inputs["Value"])
    col = node(nt, "ShaderNodeVectorMath", _operation="SCALE")
    col.inputs[0].default_value = rgb
    nt.links.new(mix.outputs["Result"], [x for x in col.inputs if x.name == "Scale"][0])
    nt.links.new(col.outputs[0], p.inputs["Base Color"])
    nt.links.new(dress.normal_map(nt, dress.image(os.path.join(d, "nor_gl.jpg"), True), vec, 1.0, box=True), p.inputs["Normal"])
    p.inputs["Roughness"].default_value = rough
    p.inputs["Sheen Weight"].default_value = sheen
    return m


def clear_glass(ior=1.5, name="archviz-glass"):
    """Clear glass for small pieces, transparent to shadow rays: without caustics, the light behind a glass or a
    carafe would otherwise be its dark shadow."""
    m = bpy.data.materials.new(name)
    nt = m.node_tree
    p = principled(m)
    p.inputs["Base Color"].default_value = (1, 1, 1, 1)
    p.inputs["Roughness"].default_value = 0.0
    p.inputs["IOR"].default_value = ior
    p.inputs["Transmission Weight"].default_value = 1.0
    out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
    lp = node(nt, "ShaderNodeLightPath")
    tr = node(nt, "ShaderNodeBsdfTransparent")
    mix = node(nt, "ShaderNodeMixShader")
    nt.links.new(lp.outputs["Is Shadow Ray"], mix.inputs["Fac"])
    nt.links.new(p.outputs["BSDF"], mix.inputs[1])
    nt.links.new(tr.outputs["BSDF"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    return m


BATH_MATS = {}


def bath_materials(root):
    """The Cycles materials of a made piece (made/bathset.py), by its material names."""
    if not BATH_MATS:
        BATH_MATS.update({"towel-white": terry((0.80, 0.79, 0.76)), "towel-sand": terry((0.52, 0.45, 0.36)),
                          "oak": oak_wood(), "glass": clear_glass(), "water": clear_glass(1.33, "archviz-water"),
                          "napkin": cloth_weave((0.30, 0.35, 0.26), 0.04, 0.9, 0.1, "archviz-napkin"),  # sage linen, little sheen
                          "mat-terry": terry((0.78, 0.76, 0.72)), "throw-wool": dress.wool_material(tint=(1.15, 0.86, 0.62))})  # camel
    for c in family(root):
        if c.type != "MESH":
            continue
        for slot in c.material_slots:
            m = slot.material
            if m is None:
                continue
            key = m.name.split(".")[0]
            if key.startswith("cover-") and key not in BATH_MATS:
                rgb = tuple(principled(m).inputs["Base Color"].default_value[:3])
                BATH_MATS[key] = cloth_weave(rgb, 0.035, 0.8, 0.25, f"archviz-{key}")
            if key in BATH_MATS:
                slot.material = BATH_MATS[key]
            elif key == "pages":
                q = principled(m)
                q.inputs["Roughness"].default_value = 0.9
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


def dress_storey(scene, sy, decor=True):
    """The kit's pieces made photographic (swaps, materials) and, with `decor`, dressed (table settings, counters,
    towels and bottles, a throw, cushions)."""
    pieces = storey_pieces(scene, sy)
    if objlib.LIB:
        stage.swap_kit(scene, pieces)
    done = {
        "rugs": swap_rugs(scene, pieces),
        "plants": swap_plants(scene, pieces, sy),
        "art": swap_art(scene, pieces),
        "armchairs": swap_armchairs(scene, pieces, decor),
        "beds": swap_beds(scene, pieces, decor),
    }
    bpy.context.view_layer.update()
    sanitaryware(scene, pieces)
    if decor:
        done["sofas"] = sofa_measure(scene, pieces)
        done["tables"] = dress_tables(scene, pieces)
        done["counters"] = dress_counter(scene, pieces)
        dg = bpy.context.evaluated_depsgraph_get()
        done["tubs"] = dress_tubs(scene, dg, pieces)
        done["basins"] = dress_basins(scene, dg, pieces, sy)
    log("dressing:", done)


def strip_to(scene, rooms, level):
    """What `level` (staging.LEVELS) leaves out of the project's pieces, hidden before anything is dressed."""
    if level == "furnished":
        return []

    def wet(o):
        r = room_at(rooms, *to_scene(o.matrix_world.translation))
        return r is not None and r.get("use") in staging.WET

    tops = [o for o in scene.objects if o.get("kind") == "furniture" and not o.hide_render and piece_top(o) is o]
    gone = staging.strip(tops, level, piece_label, wet, hide)
    log(f"staging {level}: {len(gone)} of {len(tops)} pieces out ({', '.join(sorted(set(gone))[:40])})")
    return gone


def door_leaf(o):
    """The swinging part of an interior door `o` belongs to (the kit's pivot under the door), or None."""
    while o is not None and o.parent is not None:
        if o.type == "EMPTY" and o.parent.get("kind") == "interiorDoor":
            return o
        o = o.parent
    return None


def open_doors(scene, cam, nx=48, ny=27, most=0.03, near=1.2):
    """A photographer swings a door leaf that stands in the picture out of it: hide every leaf that takes more
    than `most` of the frame or stands closer than `near` metres. The casing stays: the doorway reads open."""
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    fr = cam.data.view_frame(scene=scene)
    x0, x1 = min(c.x for c in fr), max(c.x for c in fr)
    y0, y1 = min(c.y for c in fr), max(c.y for c in fr)
    rot, origin = cam.matrix_world.to_3x3(), cam.matrix_world.translation
    seen = {}
    for j in range(ny):
        for i in range(nx):
            d = (rot @ Vector((x0 + (i + 0.5) / nx * (x1 - x0), y1 - (j + 0.5) / ny * (y1 - y0), fr[0].z))).normalized()
            hit = trace(scene, dg, origin, d)
            leaf = hit and door_leaf(hit[2])
            if leaf is not None:
                n, dist = seen.get(leaf.name, (0, math.inf))
                seen[leaf.name] = (n + 1, min(dist, (hit[0] - origin).length))
    gone = []
    for nm, (n, dist) in seen.items():
        if n / (nx * ny) > most or dist < near:
            leaf = scene.objects[nm]
            for o in [leaf, *leaf.children_recursive]:
                o.hide_render = True
            gone.append(nm)
    if gone:
        log(f"swung {len(gone)} door leaves out of the frame")
    return gone


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


ART_SCALES = (1.3, 1.0)  # a hung print's sizes, tried in turn


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

        room = art_room(*to_scene(p0 + n * 0.3))
        piece = artlib.pick(room or "", "hang", y=sy)

        def long_of(sc):  # a landscape print a little longer, so it doesn't read smaller than a portrait one
            return 0.82 * sc * (1.15 if piece["w"] > piece["h"] * 1.12 else 1.0)

        def free(s, sc=1.0):
            fw, fh = artlib.size(piece, long_of(sc))
            half, top = fw / 2 + 0.04, 1.04 + fh + 0.04
            for h in (1.05, (1.05 + top) / 2, top):
                for ds in (-half, 0.0, half):
                    p = p0 + a * (s + ds - a.dot(p0))
                    p.z = sy + h
                    hit = trace(scene, dg, p + n * 0.5, -n, far=0.6)
                    if hit is None or (hit[0] - p).length > 0.03 or not kinds(hit[2]) & {"wall", "partition"} \
                            or kinds(hit[2]) & {"window", "door", "interiorDoor"}:
                        return False
            return True

        s, sc = None, 1.0
        for sc in ART_SCALES:  # the biggest print the wall takes, near the middle of what the camera sees of it
            s = next((c for c in cands if free(c, sc)), None)
            if s is not None:
                break
        log(f"art: wall {tuple(round(v, 2) for v in n)} {len(g)} samples, s {ss[0]:.2f}..{ss[-1]:.2f}, {len(cands)} spots, "
            f"free at {s} (scale {sc})")
        if s is None:
            continue
        p = p0 + a * (s - a.dot(p0)) + n * 0.016
        artlib.build(scene, piece, (p.x, sy + 1.04, -p.y), long_of(sc), math.degrees(math.atan2(-n.x, n.y)))
        log(f"art: on the wall facing {tuple(round(v, 2) for v in n)} at {tuple(round(v, 2) for v in to_scene(p))}")
        return True
    return False


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


RANKED = []  # every sun position pick_sun scored: (score, azimuth, elevation, floor lit, all lit), best first


def pick_sun(scene, rays):
    """The sun position that puts about 20 % of the floor the camera sees in sun (the whole room if little floor
    is in view; an elevation near 30 deg on a tie); if no position reaches 2 %, the presentation look's sun."""
    dg = bpy.context.evaluated_depsgraph_get()
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


PASSAGES = ("hall", "stair", "entrance", "corridor")  # rooms without a window of their own, as a rule
NEXT_SHARE = 0.01  # the share of the frame a room next door must fill to be lit
NEXT_W = 20.0  # W per m2 of a lit hall's floor


def next_rooms_lit(scene, rays, rooms, room, sy):
    """A hall or a staircase seen through a doorway reads as a black hole in a daylight photo: photographers
    switch its lights on. A soft panel under its ceiling, out of the camera's sight (its own sconces read as hot
    spots at the frame's edge, r2a), its power by the floor area; rooms with windows of their own (the living
    room seen from the kitchen) stay as they are."""
    plans = [r for r in rooms if abs(r["y"] - sy) < 0.1 and r is not room and r["use"] in PASSAGES]
    seen = {}
    for r in rays:
        if r["hit"] is None:
            continue
        loc, n, _, _ = r["hit"]
        x, y, z = to_scene(loc + n * 0.05)
        for p in plans:
            if sy - 0.05 <= y <= sy + 2.8 and inside(p["polygon"], x, z):
                seen[p["name"]] = seen.get(p["name"], 0) + 1
                break
    done = []
    for p in plans:
        share = seen.get(p["name"], 0) / max(1, len(rays))
        if share < NEXT_SHARE:
            continue
        poly = p["polygon"]
        area = abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1]))) / 2
        xs, zs = [q[0] for q in poly], [q[1] for q in poly]
        cx, cz = (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2
        if not inside(poly, cx, cz):  # an L: the vertex average, else the first vertex pulled in
            cx, cz = sum(xs) / len(xs), sum(zs) / len(zs)
            if not inside(poly, cx, cz):
                cx, cz = poly[0][0] * 0.5 + cx * 0.5, poly[0][1] * 0.5 + cz * 0.5
        d = bpy.data.lights.new("archviz-next", "AREA")
        d.shape, d.size = "SQUARE", 0.8
        d.energy, d.color = NEXT_W * max(area, 2.0), (1.0, 0.8, 0.6)
        o = bpy.data.objects.new("archviz-next", d)
        o.location = (cx, -cz, p["y"] + 2.3)
        o.visible_camera = False
        o.visible_glossy = False
        scene.collection.objects.link(o)
        done.append(f"{p['name']} ({share * 100:.0f} %, {area:.1f} m2, {d.energy:.0f} W)")
    if done:
        log(f"next rooms lit: {', '.join(done)}")


HDRI_WOOD = -50.0  # the HDRI's direction (degrees from +x, its own frame) where it shows only the wood


def bounce_flash(scene, cam):
    """Flambient, as listing photographers shoot: a soft flash at the camera aimed at the ceiling, which lifts
    the ceiling and the shadows under the window's light and neutralises the floor's warm bounce. Out of the
    camera's sight and its reflections."""
    if FLASH_W <= 0:
        return
    d = bpy.data.lights.new("archviz-flash", "AREA")
    d.shape, d.size = "DISK", 0.5
    d.energy, d.color = FLASH_W, (1.0, 0.97, 0.94)
    o = bpy.data.objects.new("archviz-flash", d)
    o.location = cam.matrix_world.translation + Vector((0, 0, 0.25))
    o.rotation_euler = (math.pi, 0.0, 0.0)  # an area light shines down its -Z: turned over, up
    o.visible_camera = False
    o.visible_glossy = False
    scene.collection.objects.link(o)
    log(f"flash: {FLASH_W:.0f} W at the camera, bounced off the ceiling")


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
    arch = [r["hit"][1].z > 0.7 and to_scene(r["hit"][0])[1] < floor_y or abs(r["hit"][1].z) < 0.2 and bool(kinds(r["hit"][2]) & {"wall", "partition"})
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
        if best is None or score < best[0]:
            best = (score, az, el, f_patch, contrast)
    (rs.resolution_x, rs.resolution_y, cy.samples, rs.image_settings.file_format, vs.view_transform, vs.look, vs.exposure,
     rs.image_settings.color_depth) = keep
    log("sun thumbnails: " + "; ".join(rows))
    return None if best is None else best[1:]


def set_sun(scene, az, el):
    lamp = still.sun(scene, SUN_POWER, el, az, SKY)
    # the HDRI turned so the camera looks at its wood (its other side has a house and a car), whatever the sun
    d = scene.camera.matrix_world.to_3x3() @ Vector((0, 0, -1))
    yaw = math.degrees(math.atan2(d.y, d.x))
    for node in scene.world.node_tree.nodes:
        if node.bl_idname == "ShaderNodeMapping":
            node.inputs["Rotation"].default_value[2] = math.radians(yaw - HDRI_WOOD)
    return lamp


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


def use_gpu():
    addon_utils.enable("cycles", default_set=True)
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for kind in os.environ.get("PHOTO_DEVICE", "OPTIX,CUDA").split(","):
        prefs.compute_device_type = kind
        prefs.refresh_devices()
        devs = [d for d in prefs.devices if d.type == kind]
        if devs:
            for d in prefs.devices:
                d.use = d.type == kind
            return kind, [d.name for d in devs]
    raise SystemExit("no CUDA or OptiX GPU")


def camera_spec(scene, cam, reach):
    """The camera as "x,y,z>tx,ty,tz@fov" in scene metres, aimed at a point `reach` metres ahead horizontally."""
    rs = scene.render
    fov = cam.data.angle_y
    yaw = cam.rotation_euler[2] + math.pi / 2
    rise = cam.data.shift_y * 2 * math.tan(fov / 2) * max(rs.resolution_x, rs.resolution_y) / rs.resolution_y
    p = cam.matrix_world.translation
    t = p + Vector((math.cos(yaw), math.sin(yaw), rise)) * reach

    def fmt(v):
        return ",".join(f"{c:.3f}" for c in to_scene(v))

    return f"{fmt(p)}>{fmt(t)}@{math.degrees(fov):g}"


def lit_lamps(scene, sun):
    out = set()
    for o in scene.objects:
        if o.type == "LIGHT" and not o.hide_render and o != sun:
            piece = next((a for a in ancestors(o) if a.get("name")), None)
            out.add(str(piece["name"]) if piece else o.name)
    return sorted(out)


def piece_top(o):
    """The removable piece an object belongs to, or None for the building: a kit piece (its outermost
    `furniture` ancestor) or a prop this pipeline added (a top-level object other than the house)."""
    top, furn = o, None
    while top.parent is not None:
        if top.get("kind") == "furniture":
            furn = top
        top = top.parent
    if top.get("kind") == "furniture":
        furn = top
    if furn is not None:
        return furn
    return top if top.name != "house" and top.get("kind") is None and not top.name.startswith("still") else None


def piece_label(o):
    return str(o.get("name") or o.name)


def hide_pieces(scene, names):
    """Pieces by id (the object's unique name, one piece) or by label (every piece of that name)."""
    names = set(names)
    gone = []
    for o in scene.objects:
        if o.hide_render or piece_top(o) is not o or not (o.name in names or piece_label(o) in names):
            continue
        hide(o)
        gone.append(o.name)
    if gone:
        log(f"took out {sorted(set(gone))}")
    return sorted(set(gone))


def pieces_in_frame(scene, cam, nx=64, ny=36, least=0.005):
    """What a critic can name: the pieces the camera sees, their share of the frame, where, and the edges cutting them."""
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    fr = cam.data.view_frame(scene=scene)
    x0, x1 = min(c.x for c in fr), max(c.x for c in fr)
    y0, y1 = min(c.y for c in fr), max(c.y for c in fr)
    rot, origin = cam.matrix_world.to_3x3(), cam.matrix_world.translation
    seen = {}
    for j in range(ny):
        for i in range(nx):
            fx, fy = (i + 0.5) / nx, (j + 0.5) / ny
            d = (rot @ Vector((x0 + fx * (x1 - x0), y1 - fy * (y1 - y0), fr[0].z))).normalized()
            hit = trace(scene, dg, origin, d)
            top = hit and piece_top(hit[2])
            if top is None:
                continue
            e = seen.setdefault(top.name, {"label": piece_label(top), "n": 0, "x": [], "y": [], "cut": set(), "dist": []})
            e["n"] += 1
            e["x"].append(fx)
            e["y"].append(fy)
            e["dist"].append((hit[0] - origin).length)
            for edge, on in (("left", i == 0), ("right", i == nx - 1), ("top", j == 0), ("bottom", j == ny - 1)):
                if on:
                    e["cut"].add(edge)
    out = []
    for nm, e in sorted(seen.items(), key=lambda kv: -kv[1]["n"]):
        share = e["n"] / (nx * ny)
        if share < least:
            continue
        out.append({"id": nm, "name": e["label"], "share": round(share, 3), "x": [round(min(e["x"]), 2), round(max(e["x"]), 2)],
                    "y": [round(min(e["y"]), 2), round(max(e["y"]), 2)], "cut_by": sorted(e["cut"]),
                    "distance_m": round(sorted(e["dist"])[len(e["dist"]) // 2], 1)})
    return out


def run(blend, rooms, cam_spec, out, width=1920, height=1080, samples=256, sun=None, room_lamps=None, remove=(),
        staging_level="furnished"):
    """Photograph the room the camera `cam_spec` stands in to the JPEG `out`; what was decided goes to `<out>.json`
    and is returned. `rooms`: rooms.json's list. A final from a preview replays its choices: `sun` (azimuth,
    elevation) and `room_lamps` skip the sun search; `remove` takes pieces (ids from the preview's `pieces`) out.
    `staging_level`: how much of the furnishing shows (staging.LEVELS)."""
    t0 = time.time()
    seconds = {}
    artlib.init(rooms)
    objlib.init(rooms)
    style.H = stage.H = sys.modules[__name__]
    bpy.ops.wm.open_mainfile(filepath=blend)
    kind, devices = use_gpu()
    device = f"{kind}: {', '.join(devices)}"
    scene = bpy.context.scene
    still.base(scene)
    scene.render.use_persistent_data = True
    still.lamps_off(scene)
    still.outside(scene)
    still.glass(scene)
    ground(scene)
    pos = [float(v) for v in cam_spec.split(">")[0].split(",")]
    tgt = [float(v) for v in cam_spec.split(">")[1].split("@")[0].split(",")]
    room = room_at(rooms, *pos)
    if room is None:
        raise SystemExit(f"the camera {pos} is in no room of rooms.json")
    sy = room["y"]
    log(f"room: {room['name']} ({room['use']}), storey y {sy}; {device}")
    finish_textures(scene)
    inner_walls(scene, rooms)
    dress.plaster(scene)
    glaze_tiles(scene)
    paint(scene)
    stripped = strip_to(scene, rooms, staging_level)
    decor = staging_level == "furnished"
    dress.linen(scene)
    upholster_chairs(scene)
    dress_storey(scene, sy, decor)
    scene.render.resolution_x, scene.render.resolution_y = width, height  # view_frame() reads the aspect from it
    still.camera(scene, cam_spec, width, height)
    doors = open_doors(scene, scene.camera)
    clear_edges(scene, scene.camera)
    final_spec = camera_spec(scene, scene.camera, max(0.5, math.hypot(tgt[0] - pos[0], tgt[2] - pos[2])))
    rays = camera_rays(scene, scene.camera, rooms, sy)
    if decor:
        if hang_art(scene, rays, sy):
            rays = camera_rays(scene, scene.camera, rooms, sy)
        style.style(scene, scene.camera, rays, rooms, sy, storey_pieces(scene, sy))
        rays = camera_rays(scene, scene.camera, rooms, sy)
    removed = hide_pieces(scene, remove) if remove else []
    if removed:
        rays = camera_rays(scene, scene.camera, rooms, sy)
    stage.report(scene, scene.camera)
    pieces = pieces_in_frame(scene, scene.camera)
    seconds["setup"] = round(time.time() - t0, 1)
    t = time.time()
    if sun is not None:
        az, el = (float(v) for v in sun)
        frac = 0.0
        log(f"sun: pinned at {az:.0f}/{el:.0f}, room lamps {'on' if room_lamps else 'off'}")
    else:
        az, el, frac = pick_sun(scene, rays)
        log(f"sun: azimuth {az:.0f}, elevation {el:.0f}, {frac * 100:.0f} % of the room in sun ({time.time() - t:.0f} s)")
    lamp = set_sun(scene, az, el)
    cands = spread(RANKED, THUMBS) if sun is None else []
    with tempfile.TemporaryDirectory() as tmp:
        if THUMBS and len(cands) > 1:
            t1 = time.time()
            pick = sun_thumbs(scene, rays, lamp, cands, sy, os.path.join(tmp, "thumb.png"))
            if pick:
                az, el, frac, contrast = pick
                aim_sun(scene, lamp, az, el)
                log(f"sun: thumbnails pick {az:.0f}/{el:.0f}, sun patches on {frac * 100:.0f} % of the room, x{contrast:.1f} "
                    f"the sky's light there ({len(cands)} candidates, {time.time() - t1:.0f} s)")
        lit = room_lamps if room_lamps is not None else frac < 0.05 and not (THUMBS and len(cands) > 1 and frac >= 0.03)
        if lit:
            lamps_on(scene, room)
        next_rooms_lit(scene, rays, rooms, room, sy)
        bounce_flash(scene, scene.camera)
        seconds["sun"] = round(time.time() - t, 1)
        t = time.time()
        scene.render.resolution_x, scene.render.resolution_y = PREVIEW_W, round(PREVIEW_W * height / width)
        scene.cycles.samples = PREVIEW_SAMPLES
        bpy.ops.render.render()
        grade(scene, rays, os.path.join(tmp, "preview.png"))
        seconds["preview"] = round(time.time() - t, 1)
        t = time.time()
        scene.render.resolution_x, scene.render.resolution_y = width, height
        scene.cycles.samples = samples
        bpy.ops.render.render()
        seconds["final"] = round(time.time() - t, 1)
        raw = os.path.join(tmp, "raw.png")
        bpy.data.images["Render Result"].save_render(raw, scene=scene)
        post(raw, out)
    seconds["total"] = round(time.time() - t0, 1)
    sky = next(n for n in scene.world.node_tree.nodes if n.bl_idname == "ShaderNodeTexSky")
    info = {"room": room["name"], "use": room["use"], "camera": final_spec,
            "sun": f"{math.degrees(sky.sun_rotation) % 360:.1f}/{math.degrees(sky.sun_elevation):.1f}",
            "sun_deg": [round(az, 2), round(el, 2)], "room_lamps": bool(lit), "removed": removed, "doors": doors, "pieces": pieces,
            "staging": staging_level, "staged_out": len(stripped),
            "lamps": lit_lamps(scene, lamp), "device": device, "seconds": seconds}
    with open(out + ".json", "w", encoding="utf-8") as f:
        json.dump(info, f, indent=1, ensure_ascii=False)
    log(f"final {width}x{height} {samples} samples {seconds['final']:.0f} s; total {seconds['total']:.0f} s -> {out}")
    return info
