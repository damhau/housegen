"""The scene's own decor (#102). A kit that draws the listing photos' lamps, plants, vases, branches, benches, mirrors and
prints from its catalogue (kit/decor.js) tags each piece of it in the export: `catalogue` (the piece's id, on the group
holding its model), `cord` (a pendant's cord as the kit set it: from, to, length), `variant` (a plant's), `art` (a
print's id, on its frame). The photo then shows those pieces and chooses none of their kinds itself (OWN):
- each tagged decor piece is the same file at full resolution, fitted to the box the kit's drew (the export's are
  compressed for the web), a pendant's cord edited as the kit's;
- each print is framed as the kit framed it, with the print's full-resolution image;
- the rules that dressed a room with the library's lamps, plants, vases, branches, benches and mirrors and the wall art
  find none of those roles (objlib.pick) and leave those prints out; books, candles, trays, bowls, cushions stay theirs.
A scene drawn by an older kit carries no tags: the photo dresses it as before.

A kit that dresses the rooms itself (kit/dressing.js, v39 on: the house group's `kitDressing`) adds one group of holders
(kind "dressing", `dressing` what each holds) on the storeys: catalogue pieces, drawn here at full resolution like the
decor (DRESSED widens FULL to their roles), and stand-ins for this pipeline's made props (`made`, `arg`, `side`), which
photo.py replaces with its own (made_piece). The rules that dressed what the kit now dresses then stay out (style.py).
"""

import json
import os

try:
    import bpy
    import numpy as np
    from mathutils import Matrix, Vector
except ImportError:  # the roles alone, outside Blender
    bpy = np = Matrix = Vector = None

import objlib
import paths

OWN = False
DRESSED = False
# the library roles the kit's catalogue chooses for an OWN scene
KIT_ROLES = frozenset({"table_lamp", "floor_lamp", "desk_lamp", "pendant", "chandelier", "pot_table", "pot_floor", "plant",
                       "vase", "stems", "bench", "mirror", "tall_mirror"})
# drawn again at full resolution: those, and the rugs
FULL = KIT_ROLES | {"rug", "runner"}
# and, in a scene the kit dressed, the library roles of what its dressing draws from the catalogue (cushions, candles...)
DRESSING_ROLES = frozenset({"pillow", "lumbar", "candle", "jar", "tray", "bowl", "basket", "pouf"})
# kit/dressing.js MADE: the bathset piece (made/bathset.py) each stand-in is drawn as
MADE = {"books": "books", "carafe": "carafe", "carafe1": "carafe1", "candles": "candles", "napkin": "napkin",
        "sofathrow": "sofathrow", "drape": "drape", "stooltowels": "stooltowels", "rail": "rail"}
NOT_LEAVES = ("_pot", "_dirt", "_pebbles", "_soil", "_ground")  # kit/furnish.js NOT_LEAVES
_ENTRIES, _ART = None, None


def log(*a):
    print("PHOTO catalogue:", *a, flush=True)


def init(scene):
    """Whether the export draws its own decor (kit v38 on): the house group's flag, or a tagged piece; and whether the
    kit dressed its rooms (v39 on)."""
    global OWN, DRESSED
    DRESSED = any(o.get("kitDressing") for o in scene.objects)
    OWN = DRESSED or any(o.get("catalogueDecor") or o.get("catalogue") for o in scene.objects)
    objlib.BANNED = KIT_ROLES if OWN else frozenset()
    log("the scene's own decor" if OWN else "an older kit: the library dresses the rooms",
        "and its own dressing" if DRESSED else "")
    return OWN


def full():
    """The library roles drawn again at full resolution in this scene."""
    return FULL | DRESSING_ROLES if DRESSED else FULL


def made_piece(what, arg=None, side=None):
    """What stands in for a kit stand-in tagged `made` (kit/dressing.js): ("runner", (width, length) m) for a runner,
    ("prop", (bathset piece, its argument, x scale)) for the others; None for one this pipeline does not make."""
    if what == "runner":
        try:
            w, length = (int(v) / 100 for v in str(arg).split("-"))
        except ValueError:
            return None
        return ("runner", (w, length)) if w > 0 and length > 0 else None
    if what not in MADE:
        return None
    try:
        sx = -1.0 if float(side) < 0 else 1.0
    except (TypeError, ValueError):
        sx = 1.0
    return "prop", (MADE[what], None if arg in (None, "") else str(arg), sx)


def holders(scene, sy):
    """The kit dressing's holders still shown on the storey at height `sy`."""
    return [o for o in scene.objects if o.get("kind") == "dressing" and o.get("dressing") and not o.hide_render
            and sy - 0.1 <= o.matrix_world.translation.z <= sy + 2.3]


def entries():
    global _ENTRIES
    if _ENTRIES is None:
        try:
            _ENTRIES = {e["id"]: e for e in json.load(open(objlib.CATALOGUE, encoding="utf-8"))}
        except OSError:
            _ENTRIES = {}
    return _ENTRIES


def prints():
    global _ART
    if _ART is None:
        _ART = {}
        for f in ("museum.json", "gen.json"):
            try:
                _ART.update({e["id"]: e for e in json.load(open(os.path.join(paths.MADE, "art", f), encoding="utf-8"))})
            except OSError:
                pass
    return _ART


def source(cid):
    """(the full-resolution file of catalogue id `cid`, its library role), (None, None) when there is none."""
    e = entries().get(cid)
    if e is not None:
        p = objlib.path(e)
        return (p, e["role"]) if os.path.exists(p) else (None, None)
    for p, role in ((os.path.join(paths.ASSETS, "models", cid, f"{cid}.gltf"), "plant"),
                    (os.path.join(paths.OBJECTS, f"{cid}.glb"), None), (os.path.join(paths.MADE, "stems", f"{cid}.glb"), "stems")):
        if os.path.exists(p):
            return p, role
    return None, None


def subtree(e):
    """`e`'s descendants that draw it, not those of a tagged piece inside it."""
    out, stack = [], list(e.children)
    while stack:
        c = stack.pop()
        if c.get("catalogue"):
            continue
        out.append(c)
        stack.extend(c.children)
    return out


def depth(o):
    n = 0
    while o.parent is not None:
        n, o = n + 1, o.parent
    return n


def vertices(objs, frame):
    """The vertices of the meshes `objs` in the space of matrix `frame` (n x 3)."""
    inv, chunks = frame.inverted(), []
    for o in objs:
        if o.type != "MESH" or not len(o.data.vertices):
            continue
        co = np.empty(len(o.data.vertices) * 3, dtype=np.float64)
        o.data.vertices.foreach_get("co", co)
        m = np.array(inv @ o.matrix_world)
        co = co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]
        chunks.append(co)
    return np.concatenate(chunks) if chunks else np.zeros((0, 3))


def _tag(o, new):
    """The node name a plant part is known by: the first of it and its ancestors from the file that says pot or variant
    (furnish.js pottedPlant's tag)."""
    a = o
    while a is not None and a in new:
        n = a.name.split(".")[0]
        if n.endswith(NOT_LEAVES) or (len(n) > 2 and n[-2] == "_" and n[-1].isalpha() and n[-1].islower()):
            return n
        a = a.parent
    return o.name.split(".")[0]


def load(path, variant=None, plant=False):
    """The file's meshes baked into its own grounded frame (base centred on the origin), z up: a list of new objects."""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    bpy.context.view_layer.update()
    keep = []
    for o in new:
        if o.type != "MESH":
            continue
        tag = _tag(o, set(new))
        letter = tag[-1] if len(tag) > 2 and tag[-2] == "_" and tag[-1].isalpha() else None
        if plant and (tag.endswith(NOT_LEAVES) or (variant and letter and letter != variant)):
            continue
        keep.append(o)
    for o in keep:
        o.data = o.data.copy()
        o.data.transform(o.matrix_world)
    for o in new:
        if o not in keep:
            bpy.data.objects.remove(o, do_unlink=True)
    for o in keep:
        o.parent = None
        o.matrix_world = Matrix.Identity(4)
    pts = vertices(keep, Matrix.Identity(4))
    if not len(pts):
        return keep
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    shift = Matrix.Translation((-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]))
    for o in keep:
        o.data.transform(shift)
    return keep


def record(cord):
    """A pendant's cord as the kit set it: (from, to, length) or None."""
    try:
        y0, y1, length = (float(v) for v in cord)
    except (TypeError, ValueError):
        return None
    return (y0, y1, length) if y1 > y0 else None


def set_cord(objs, cord):
    """The cord between heights y0 and y1 given `length`, as the kit's setCord: above it moves, below it stays."""
    y0, y1, length = cord
    k, d = length / (y1 - y0), length - (y1 - y0)
    for o in objs:
        co = np.empty(len(o.data.vertices) * 3)
        o.data.vertices.foreach_get("co", co)
        z = co[2::3]
        co[2::3] = np.where(z >= y1, z + d, np.where(z > y0, y0 + (z - y0) * k, z))
        o.data.vertices.foreach_set("co", co)
        o.data.update()


def redraw(scene, e, cid):
    """Tagged piece `e` drawn from its full-resolution file, fitted to its export's box in its own frame. Its library
    role, or None when it stays as exported (not a decor role, no file)."""
    path, role = source(cid)
    if path is None or role not in full():
        return None
    old = subtree(e)
    want = vertices(old, e.matrix_world)
    if not len(want):
        return None
    new = load(path, variant=e.get("variant"), plant=role == "plant")
    cord = record(e.get("cord"))
    if cord:
        set_cord(new, cord)
    have = vertices(new, Matrix.Identity(4))
    if not len(have):
        for o in new:
            bpy.data.objects.remove(o, do_unlink=True)
        return None
    wlo, whi, hlo, hhi = want.min(0), want.max(0), have.min(0), have.max(0)
    s = [(whi[i] - wlo[i]) / (hhi[i] - hlo[i]) if hhi[i] - hlo[i] > 1e-4 else 1.0 for i in range(3)]
    if any(not 0.67 <= v <= 1.5 for v in s):
        log(f"{cid}: its file is {[round(v, 2) for v in s]} of the export's box, fitted all the same")
    fit = Matrix.Translation(Vector([wlo[i] - hlo[i] * s[i] for i in range(3)])) @ Matrix.Diagonal((*s, 1.0))
    meshes = [o for o in old if o.type == "MESH"]
    nodes = sorted((o for o in old if o.type != "MESH"), key=lambda o: -depth(o))  # read before any removal
    for o in meshes:
        bpy.data.objects.remove(o, do_unlink=True)
    for o in nodes:
        if not o.children:  # a tagged piece inside keeps the nodes it hangs from
            bpy.data.objects.remove(o, do_unlink=True)
    for o in new:
        for c in list(o.users_collection):
            c.objects.unlink(o)
        scene.collection.objects.link(o)
        o.parent = e
        o.matrix_parent_inverse = Matrix.Identity(4)
        o.matrix_basis = fit
    e["obj_id"] = cid
    e["obj_role"] = role
    return role


def reframe(e):
    """A print framed by the kit: its full-resolution image. Whether there was one."""
    aid = str(e.get("art"))
    entry = prints().get(aid)
    path = entry and os.path.join(paths.MADE, "art", entry["file"])
    e["art_id"] = aid
    if not path or not os.path.exists(path):
        return False
    img = bpy.data.images.load(path, check_existing=True)
    done = False
    for o in subtree(e):
        if o.type != "MESH" or not o.name.startswith("art-print"):
            continue
        upright(o.data)
        for slot in o.material_slots:
            m = slot.material
            if m is None or not m.use_nodes:
                continue
            for n in m.node_tree.nodes:
                if n.bl_idname == "ShaderNodeTexImage":
                    n.image = img
                    done = True
    return done


def upright(me):
    """Turn a print's UVs to read the image file as it is stored, once per mesh (the flag persists in a saved blend)."""
    # three's GLTFExporter writes a flipY texture's image upside down and keeps the UVs, so the exported UVs only
    # suit that flipped copy, not the file reframe swaps in
    if me.get("uv_upright") or not me.uv_layers:
        return
    for layer in me.uv_layers:
        for d in layer.data:
            d.uv = (d.uv[0], 1.0 - d.uv[1])
    me["uv_upright"] = True


def tagged(pieces):
    """The tagged groups and framed prints of `pieces` (the storey's kit pieces), each once."""
    seen, groups, art = set(), [], []
    for p in pieces:
        for o in (p, *p.children_recursive):
            if o.name in seen:
                continue
            seen.add(o.name)
            if o.get("catalogue"):
                groups.append(o)
            if isinstance(o.get("art"), str):
                art.append(o)
    return groups, art


def own(scene, pieces):
    """The storey's tagged decor at full resolution and its prints' own images: {role: count}."""
    groups, art = tagged(pieces)
    n = {}
    for e in groups:
        role = redraw(scene, e, str(e["catalogue"]))
        if role:
            n[role] = n.get(role, 0) + 1
    n["prints"] = sum(reframe(e) for e in art)
    bpy.context.view_layer.update()
    log("drawn at full resolution:", n)
    return n


def owner(o):
    """What a surface belongs to among the pieces counted: (the kit's catalogue id or print, None) or (None, the
    library id this pipeline placed); (None, None) for anything else."""
    a = o
    while a is not None:
        if a.get("catalogue") or isinstance(a.get("art"), str):
            return str(a.get("catalogue") or a.get("art")), None
        if a.get("made_for"):
            return f"made:{a['made_for']}", None
        if a.get("kind") == "furniture":
            return None, None
        if a.parent is None and (a.get("obj_id") or a.get("art_id")):
            return None, str(a.get("obj_id") or a.get("art_id"))
        a = a.parent
    return None, None


def seen(scene, cam, trace, nx=96, ny=54, least=0.0005):
    """The catalogue pieces and prints the frame shows ("kit") and what this pipeline placed of its own ("added", by
    library id and role), each with its share of the frame: what the photo shows against what the 3D does."""
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    mw = cam.matrix_world
    eye = mw.translation.copy()
    tr, br, bl, tl = cam.data.view_frame(scene=scene)[:4]
    kit, added = {}, {}
    for j in range(ny):
        for i in range(nx):
            u, v = (i + 0.5) / nx, (j + 0.5) / ny
            p = tl.lerp(tr, u).lerp(bl.lerp(br, u), v)
            hit = trace(scene, dg, eye, ((mw @ p) - eye).normalized())
            if hit is None:
                continue
            k, a = owner(hit[2])
            if k:
                kit[k] = kit.get(k, 0) + 1
            elif a:
                added[a] = added.get(a, 0) + 1
    role = {i: e["role"] for i, e in entries().items()}

    def kind(k):
        return role.get(k) or ("print" if k in prints() else "made" if k.startswith("made:") else None)

    def listed(counts, roles):
        return [{"id": k, "role": roles(k), "share": round(c / (nx * ny), 4)}
                for k, c in sorted(counts.items(), key=lambda t: -t[1]) if c / (nx * ny) >= least]

    return {"own": OWN, "dressed": DRESSED, "kit": listed(kit, kind), "added": listed(added, kind)}
