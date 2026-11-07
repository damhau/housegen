"""The photo pipeline's wall art (made/art/): museum works in the public domain (made/art_fetch.py, museum.json) and
prints made here (made/art_gen.py, gen.json), framed in Blender to each print's own shape.

Which print hangs where is worked out once for the whole house (every room of rooms.json, the same answer in every
process that renders one of its rooms), so no print hangs twice in a house until the library runs out:
- rooms in order of how likely they are to be photographed (living, dining, bedrooms, ...);
- each room's slots: `hang` (the print hang_art puts on the wall the camera sees most), `pair` (two prints of one
  series over a bed), `lean` (the print leaning on a sideboard), `swap1`/`swap2` (the kit's own wall art);
- a print goes to rooms its style suits, in an order shuffled per house.

ART_LIB=0: the old single print (made/art-print.jpg) in every slot, for A/B checks.
"""

import hashlib
import json
import math
import os
import random

try:
    import bpy
    from mathutils import Vector
except ImportError:  # the allocation alone, outside Blender (`python3 artlib.py <rooms.json>`)
    bpy = Vector = None

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "made", "art")
ON = os.environ.get("ART_LIB", "1") != "0"

ORDER = ["living", "dining", "bedroom", "office", "kitchen", "bath", "hall", "wc", "laundry"]
SLOTS = {
    "living": ["hang", "lean", "swap1", "swap2"],
    "dining": ["hang", "lean", "swap1"],
    "bedroom": ["pair", "hang", "lean", "swap1"],
    "office": ["hang", "swap1"],
    "kitchen": ["hang", "swap1"],
    "bath": ["hang"], "hall": ["hang"], "wc": ["hang"], "laundry": ["hang"],
}
ALL = set(ORDER)
SUITS = {  # museum styles by room; made-here prints suit any room
    "japanese": {"living", "dining", "bedroom", "office", "hall"},
    "botanical": {"bedroom", "dining", "kitchen", "bath", "hall", "wc", "living", "laundry"},
    "abstract": {"living", "dining", "office", "bedroom", "hall"},
    "drawing": {"bedroom", "living", "office", "hall"},
    "landscape": {"living", "dining", "bedroom", "office", "hall"},
    "interior": {"living", "bedroom", "hall", "office"},
    "photo": {"bath", "hall", "office", "kitchen", "living", "wc", "laundry"},
    "stilllife": {"dining", "kitchen", "living"},
}
# frame: colour, roughness, metallic, face width (m), depth (m)
FRAMES = {
    "oak": ((0.55, 0.40, 0.26), 0.55, 0.0, 0.022, 0.03),
    "ash": ((0.74, 0.62, 0.47), 0.6, 0.0, 0.02, 0.03),
    "black": ((0.03, 0.03, 0.03), 0.45, 0.0, 0.015, 0.025),
    "white": ((0.86, 0.86, 0.84), 0.4, 0.0, 0.02, 0.03),
    "brass": ((0.80, 0.62, 0.36), 0.3, 1.0, 0.01, 0.022),
}
# by style: the frames it may get and the mount (a fraction of the long side; 0 = the print runs to the frame)
DRESS = {
    "japanese": (["black", "oak", "ash"], 0.11),
    "botanical": (["oak", "white", "brass", "ash"], 0.10),
    "abstract": (["black", "white", "oak"], 0.08),
    "drawing": (["white", "oak", "ash"], 0.12),
    "landscape": (["white", "oak", "black"], 0.10),
    "interior": (["black", "oak"], 0.08),
    "photo": (["black", "white"], 0.13),
    "stilllife": (["oak", "black"], 0.0),
    "painting": (["oak", "black", "ash"], 0.0),
    "gen": (["oak", "black", "ash"], None),  # None: a mount for half the series; no white frame (white paper: it vanishes)
}
OLD = {"id": "old-print", "file": "../art-print.jpg", "w": 1200, "h": 1600, "style": "gen-organic", "series": "old",
       "frame": "oak", "mount": 0.085}

DARK = 0.3  # a print darker than this on average reads as a hole in a bright wall (the plums, 2026-11-02): left out
LIB, BY, ALLOC, ROOMS, USED, EXTRA = [], {}, {}, [], set(), {}


def _h(s):
    return int(hashlib.sha1(s.encode()).hexdigest()[:8], 16)


def _shape(e):
    a = e["w"] / e["h"]
    return "portrait" if a < 0.9 else "square" if a <= 1.12 else "landscape"


def _dress(e):
    st = e["style"]
    kind = "gen" if st.startswith("gen-") else "painting" if (e.get("type") or "").lower() == "painting" else st
    frames, mount = DRESS.get(kind, DRESS["abstract"])
    key = _h(e["series"])
    if mount is None:
        mount = 0.09 if key % 2 else 0.0
    return frames[key % len(frames)], mount


def load():
    out = []
    for f in ("museum.json", "gen.json"):
        p = os.path.join(DIR, f)
        if os.path.exists(p):
            out += json.load(open(p, encoding="utf-8"))
    for e in out:
        if "series" not in e:  # museum works: one series per artist and style
            who = " ".join((e.get("artist") or "anon").replace(",", " ").split()[:2]).lower()
            e["series"] = f"{e['style']}-{who}"
        e["frame"], e["mount"] = _dress(e)
    return [e for e in out if os.path.exists(os.path.join(DIR, e["file"])) and e.get("tone", 1.0) >= DARK]


def suits(e, use):
    return use in (ALL if e["style"].startswith("gen-") else SUITS.get(e["style"], ALL))


def allocate(rooms, lib):
    """{room name: {slot: [print ids]}} for the whole house."""
    rng = random.Random(_h("|".join(sorted(r["name"] for r in rooms))))
    pool = lib[:]
    rng.shuffle(pool)
    used = {e["id"]: 0 for e in pool}
    out = {}

    def take(use, ok=lambda e: True):
        for strict in (True, False):  # an unused print that suits the room, then any unused one
            for e in pool:
                if used[e["id"]] == 0 and ok(e) and (suits(e, use) or not strict):
                    used[e["id"]] += 1
                    return e["id"]
        e = min((e for e in pool if ok(e)), key=lambda e: (used[e["id"]], not suits(e, use)), default=None)
        if e is None:
            return None
        used[e["id"]] += 1
        return e["id"]

    def take_pair(use):
        groups = {}
        for e in pool:
            groups.setdefault((e["series"], _shape(e)), []).append(e)
        for strict in (True, False):
            for (_, shape), g in groups.items():
                free = sorted((e for e in g if used[e["id"]] == 0), key=lambda e: e["w"] / e["h"])
                two = next((free[i:i + 2] for i in range(len(free) - 1)  # two of one series and nearly one shape
                            if free[i + 1]["w"] / free[i + 1]["h"] - free[i]["w"] / free[i]["h"] < 0.08), None)
                if shape != "landscape" and two and (suits(two[0], use) or not strict):
                    for e in two:
                        used[e["id"]] += 1
                    return [e["id"] for e in two]
        return [take(use), take(use)]  # no series left with two free prints: two prints that go together by shape

    for r in sorted(rooms, key=lambda r: (ORDER.index(r["use"]) if r["use"] in ORDER else 99, r["name"])):
        slots = {}
        for s in SLOTS.get(r["use"], []):
            if s == "pair":
                slots[s] = take_pair(r["use"])
            elif s == "lean":
                slots[s] = [take(r["use"], lambda e: _shape(e) != "landscape")]
            else:
                slots[s] = [take(r["use"])]
        out[r["name"]] = slots
    return out


def init(rooms):
    global LIB, BY, ALLOC, ROOMS, USED
    ROOMS = rooms
    LIB = load() if ON else []
    BY = {e["id"]: e for e in LIB}
    ALLOC = allocate(rooms, LIB) if LIB else {}
    USED = {i for s in ALLOC.values() for v in s.values() for i in v}
    n = sum(len(v) for s in ALLOC.values() for v in s.values())
    print(f"PHOTO art: {len(LIB)} prints in the library, {n} slots in {len(ALLOC)} rooms"
          + ("" if ON else " (ART_LIB=0: the old print everywhere)"), flush=True)


def pick(room, slot, k=0, y=0.0):
    """The print for a room's slot (the k-th of a pair); the old print if the library is off.
    A slot the allocation has no print for (a third kit print in a room, a store, no room found: `y` is then the
    print's height) takes a print no room was given. A process renders one storey and knows nothing of the others, so
    each level of the house draws from its own share of the spare prints: no print twice between storeys, and every
    process rendering a room of one storey makes the same calls in the same order, so they agree. (A print's height
    can land in a split level just above its floor, Gille's garages at 0.8 m: they would share the ground floor's
    spares, which is harmless as long as garages hold no kit prints.)"""
    if not LIB:
        return OLD
    name = room["name"] if isinstance(room, dict) else room or ""
    ids = (ALLOC.get(name) or {}).get(slot)
    if ids:
        return BY[ids[min(k, len(ids) - 1)]]
    key = (name, slot, k)
    if key not in EXTRA:
        levels = sorted({r["y"] for r in ROOMS})
        at = room["y"] if isinstance(room, dict) else y
        level = max((i for i, v in enumerate(levels) if v - 0.05 <= at), default=0)
        spare = sorted((e for e in LIB if e["id"] not in USED), key=lambda e: _h(e["id"]))
        taken = set(EXTRA.values())
        free = [e for e in spare[level::len(levels)] if e["id"] not in taken] or LIB  # all used: a repeat, the only way
        EXTRA[key] = min(free, key=lambda e: _h(f"{name}|{slot}|{k}|{e['id']}"))["id"]
    return BY[EXTRA[key]]


def size(e, long):
    """The framed print's outer width and height (m) for an outer long side `long`."""
    face, a = FRAMES[e["frame"]][3], e["w"] / e["h"]
    m = e["mount"] * long
    inner = long - 2 * face - 2 * m
    pw, ph = (inner * a, inner) if a < 1 else (inner, inner / a)
    return pw + 2 * m + 2 * face, ph + 2 * m + 2 * face


def fit(e, w, h):
    """The outer long side that fits the framed print in a w x h box."""
    fw, fh = size(e, 1.0)
    return min(w / fw, h / fh)


def _mat(name, rgb, rough, metal=0.0, img=None):
    m = bpy.data.materials.get(name)
    if m:
        return m
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    p = next(n for n in m.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    p.inputs["Base Color"].default_value = (*rgb, 1)
    p.inputs["Roughness"].default_value = rough
    p.inputs["Metallic"].default_value = metal
    if img is not None:
        t = m.node_tree.nodes.new("ShaderNodeTexImage")
        t.image = img
        m.node_tree.links.new(t.outputs["Color"], p.inputs["Base Color"])
    return m


def _box(name, lo, hi, mat, parent):
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    f = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(v, [], f)
    me.materials.append(mat)
    o = bpy.data.objects.new(name, me)
    o.parent = parent
    bpy.context.scene.collection.objects.link(o)
    return o


def build(scene, e, at, long, turn_deg=0.0):
    """Frame print `e` with an outer long side `long` (m), its base centred at scene point `at` (x, y up, z south),
    its back on the wall facing local +y turned by `turn_deg` (as dress.place does). Returns (root, (w, h))."""
    fw, fh = size(e, long)
    rgb, rough, metal, face, depth = FRAMES[e["frame"]]
    root = bpy.data.objects.new("art.glb", None)  # the name the hang/pair/edge rules look for
    scene.collection.objects.link(root)
    root["art_id"] = e["id"]
    fm = _mat(f"art-frame-{e['frame']}", rgb, rough, metal)
    y0, y1 = -depth / 2, depth / 2
    for lo, hi in (((-fw / 2, y0, fh - face), (fw / 2, y1, fh)), ((-fw / 2, y0, 0), (fw / 2, y1, face)),
                   ((-fw / 2, y0, face), (-fw / 2 + face, y1, fh - face)), ((fw / 2 - face, y0, face), (fw / 2, y1, fh - face))):
        _box("art-frame", lo, hi, fm, root)
    m = e["mount"] * long
    if m > 0:
        _box("art-mount", (-fw / 2 + face, y1 - 0.012, face), (fw / 2 - face, y1 - 0.008, fh - face),
             _mat("art-mount", (0.92, 0.91, 0.88), 0.9), root)
    else:  # no mount: a backing board, so nothing shows through
        _box("art-back", (-fw / 2 + face, y0, face), (fw / 2 - face, y0 + 0.004, fh - face),
             _mat("art-mount", (0.92, 0.91, 0.88), 0.9), root)
    img = bpy.data.images.load(os.path.join(DIR, e["file"]), check_existing=True)
    pm = _mat(f"art-print-{e['id']}", (1, 1, 1), 0.85, 0.0, img)
    x0, x1, z0, z1 = -fw / 2 + face + m, fw / 2 - face - m, face + m, fh - face - m
    yp = y1 - (0.0075 if m > 0 else 0.006)
    me = bpy.data.meshes.new("art-print")
    me.from_pydata([(x0, yp, z0), (x1, yp, z0), (x1, yp, z1), (x0, yp, z1)], [], [(0, 3, 2, 1)])  # facing +y
    uv = me.uv_layers.new()
    uv.data.foreach_set("uv", [0, 0, 0, 1, 1, 1, 1, 0])
    me.materials.append(pm)
    po = bpy.data.objects.new("art-print", me)
    po.parent = root
    scene.collection.objects.link(po)
    root.rotation_euler[2] = math.radians(turn_deg)
    root.location = Vector((at[0], -at[2], at[1]))
    bpy.context.view_layer.update()
    print(f"PHOTO art: {e['id']} ({e['style']}, {e['frame']} frame{', mount' if m > 0 else ''}) "
          f"{fw:.2f} x {fh:.2f} m", flush=True)
    return root, (fw, fh)


if __name__ == "__main__":
    import collections
    import sys

    rooms = json.load(open(sys.argv[1], encoding="utf-8"))
    rooms = rooms["rooms"] if isinstance(rooms, dict) else rooms
    init(rooms)
    by = {e["id"]: e for e in LIB}
    used = collections.Counter(i for s in ALLOC.values() for v in s.values() for i in v)
    for r, slots in ALLOC.items():
        if slots:
            print(f"{r[:22]:22}", "  ".join(f"{k}: " + ", ".join(f"{i} [{by[i]['frame']}{'+m' if by[i]['mount'] else ''}]"
                                                              for i in v) for k, v in slots.items()))
    print("repeats:", {k: v for k, v in used.items() if v > 1} or "none", "| unused:", len(LIB) - len(used))
