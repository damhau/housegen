"""The photo pipeline's objects (made/objects.json, made/obj_fetch.py): real products from Amazon Berkeley Objects
(CC BY 4.0) and Poly Haven models (CC0), by role: a rule asks for "a vase for this room", not for one named model.

Which object goes where is worked out once for the whole house (every room of rooms.json, the same answer in every
process that renders one of its rooms), as for the wall art, so an object does not come back in another room until its
role runs out:
- rooms in order of how likely they are to be photographed;
- each room gets a colour accent (warm, cool, green or none), so what lands in it goes together;
- each room gets a short list per role, more than a rule will use: a rule takes the first that fits its surface.

OBJ_LIB=0: the old fixed objects in every rule, for A/B checks.

`python3 objlib.py <rooms.json>`: the allocation, outside Blender.
`python objlib.py sheet <out dir> <role>...` (inside Blender's Python): each role's objects at their real size, numbered.
"""

import hashlib
import json
import math
import os
import random

try:
    import bpy
    from mathutils import Vector
except ImportError:  # the allocation alone, outside Blender
    bpy = Vector = None

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOGUE = os.path.join(HERE, "made", "objects.json")
ON = os.environ.get("OBJ_LIB", "1") != "0"
OBJ_DIR = os.environ.get("OBJ_DIR", os.path.join(HERE, "assets", "objects"))  # Modal: the archviz-objects volume

ORDER = ["living", "dining", "bedroom", "office", "kitchen", "bath", "hall", "wc", "laundry"]
# role: how many a room's rules may ask for, by room use. Every role not listed: none.
SURFACE = {"vase": 4, "stems": 2, "jar": 2, "candle": 2, "tray": 1, "bowl": 2, "sculpture": 1, "curio": 1, "pot_table": 3, "plant": 3}
SOFT = {"pillow": 5, "lumbar": 1}
LIGHT = {"pendant": 2, "chandelier": 1, "table_lamp": 3, "floor_lamp": 2}
SLOTS = {
    "living": {**SURFACE, **SOFT, **LIGHT, "rug": 2, "pot_floor": 2, "side_table": 3, "pouf": 2, "ottoman": 1, "sideboard": 1,
               "mirror": 1, "clock": 1, "basket": 1, "wall_shelf": 1},
    "dining": {**SURFACE, **LIGHT, "rug": 1, "pot_floor": 2, "sideboard": 1, "mirror": 1, "clock": 1, "fruit": 1},
    "bedroom": {**SURFACE, **SOFT, **LIGHT, "rug": 1, "pot_floor": 1, "bench": 2, "pouf": 1, "mirror": 1, "tall_mirror": 1,
                "basket": 1, "clock_small": 1, "desk_lamp": 1, "desk": 1},
    "office": {**SURFACE, **LIGHT, "rug": 1, "pot_floor": 1, "desk_lamp": 2, "desk": 2, "clock": 1, "wall_shelf": 1, "basket": 1},
    "kitchen": {"vase": 1, "stems": 1, "bowl": 2, "fruit": 2, "kitchen": 3, "canister": 1, "pot_table": 1, "plant": 1, "clock": 1, "pendant": 2,
                "rug": 1, "sideboard": 1},
    "bath": {"vase": 1, "stems": 1, "jar": 1, "pot_table": 1, "plant": 1, "basket": 1, "bench": 1, "mirror": 1},
    "hall": {"vase": 1, "stems": 1, "bench": 1, "mirror": 1, "tall_mirror": 1, "runner": 1, "pendant": 1, "basket": 1, "tray": 1, "pot_floor": 1,
             "plant": 1, "side_table": 1},
    "wc": {"vase": 1, "stems": 1, "pot_table": 1, "plant": 1},
    "laundry": {"basket": 1},
}
FEW = 2  # a room's list holds this many candidates per slot
ACCENTS = ("warm", "cool", "green", None)
GOES = {"warm": {"neutral", "dark", "warm"}, "cool": {"neutral", "dark", "cool"}, "green": {"neutral", "dark", "green"},
        None: {"neutral", "dark"}}
LIB, BY, ALLOC, ROOMS, TAKEN = [], {}, {}, [], {}


def _h(s):
    return int(hashlib.sha1(s.encode()).hexdigest()[:8], 16)


def path(e):
    if e["src"] == "abo":
        return os.path.join(OBJ_DIR, f"{e['id']}.glb")
    if e["src"] == "made":
        return os.path.join(HERE, "made", "stems", f"{e['id']}.glb")
    return os.path.join(HERE, "assets", "models", e["id"], f"{e['id']}.gltf")


def load():
    cat = json.load(open(CATALOGUE, encoding="utf-8"))
    return [e for e in cat if os.path.exists(path(e)) and not e.get("off")]


def allocate(rooms, lib):
    """{room name: {"accent": tone, role: [ids]}} for the whole house: first every room's own picks (as many as its
    rules may ask for), then the spare candidates, so two rooms share an object only once a deck runs out."""
    rng = random.Random(_h("|".join(sorted(r["name"] for r in rooms))))
    decks = {}
    for e in lib:
        decks.setdefault(e["role"], []).append(e)
    for d in decks.values():
        rng.shuffle(d)
    used = {e["id"]: 0 for e in lib}
    out = {}
    accents = list(ACCENTS)
    rng.shuffle(accents)
    ordered = sorted(rooms, key=lambda r: (ORDER.index(r["use"]) if r["use"] in ORDER else 99, r["name"]))
    for k, r in enumerate(ordered):
        out[r["name"]] = {"accent": accents[k % len(accents)]}
    for many in [1] + [FEW - 1] * (FEW > 1):
        for r in ordered:
            slots = out[r["name"]]
            for role, count in SLOTS.get(r["use"], {}).items():
                deck = decks.get(role, [])
                ids = slots.setdefault(role, [])
                for _ in range(count * many):
                    pool = [e for e in deck if e["id"] not in ids]
                    if not pool:
                        break
                    # the least used object that suits the room's colours
                    e = min(pool, key=lambda e: (used[e["id"]], e["tone"] not in GOES[slots["accent"]]))
                    used[e["id"]] += 1
                    ids.append(e["id"])
    return out


def init(rooms):
    global LIB, BY, ALLOC, ROOMS, TAKEN
    ROOMS = rooms
    LIB = load() if ON else []
    BY = {e["id"]: e for e in LIB}
    ALLOC = allocate(rooms, LIB) if LIB else {}
    TAKEN = {}  # how many rooms of the house hold each object
    for slots in ALLOC.values():
        for role, ids in slots.items():
            for i in ids if role != "accent" else ():
                TAKEN[i] = TAKEN.get(i, 0) + 1
    print(f"PHOTO objects: {len(LIB)} in the library, {len(ALLOC)} rooms" + ("" if ON else " (OBJ_LIB=0: the old objects)"),
          flush=True)


def size(e):
    """Width, depth, height in metres (real size from the file; `size` once measured by the sheet)."""
    if e.get("size"):
        return e["size"]
    x, y, z = (e.get("ex") or [0.2, 0.2, 0.2])[:3]
    return [x, z, y]


def picks(room, role):
    """The room's candidates for a role, in order; a room the allocation doesn't know draws by hash."""
    if not LIB:
        return []
    name = room["name"] if isinstance(room, dict) else room or ""
    ids = (ALLOC.get(name) or {}).get(role)
    if ids is not None:
        return [BY[i] for i in ids]
    deck = sorted((e for e in LIB if e["role"] == role), key=lambda e: _h(f"{name}|{role}|{e['id']}"))
    return deck[:6]


USED = set()  # what this process placed, so two rules in one room don't take the same one


def pick(room, role, fit=None):
    """The first of the room's candidates not yet placed here that passes `fit(entry)`, then the rest of the role
    (a surface can be too small for all of its list): those no room holds first, so the room takes the same one
    whatever else this frame placed before it, in an order proper to the room; None if nothing fits."""
    name = room["name"] if isinstance(room, dict) else room or ""
    rest = sorted((e for e in LIB if e["role"] == role), key=lambda e: (TAKEN.get(e["id"], 0), _h(f"{name}|{role}|{e['id']}")))
    for e in [*picks(room, role), *rest]:
        if e["id"] not in USED and (fit is None or fit(e)):
            USED.add(e["id"])
            return e
    return None


def accent(room):
    name = room["name"] if isinstance(room, dict) else room or ""
    return (ALLOC.get(name) or {}).get("accent")


def choose(room, what, options):
    """One of `options` for this room and purpose, the same in every process (which arrangement a table gets)."""
    name = room["name"] if isinstance(room, dict) else room or ""
    return options[_h(f"{name}|{what}") % len(options)]


# ---------------------------------------------------------------- the sheet


def sheet(out_dir, roles):
    """Each role's objects laid out at their real size, seen from the front (glTF +z) and a little above, numbered."""
    import addon_utils
    from bpy_extras.object_utils import world_to_camera_view
    from PIL import Image, ImageDraw

    import dress

    cat = load()
    sizes = {}
    for role in roles:
        items = [e for e in cat if e["role"] == role]
        if not items:
            continue
        bpy.ops.wm.read_factory_settings(use_empty=True)
        addon_utils.enable("cycles", default_set=True)
        scene = bpy.context.scene
        scene.render.engine = "CYCLES"
        prefs = bpy.context.preferences.addons["cycles"].preferences
        for kind in ("OPTIX", "CUDA"):
            prefs.compute_device_type = kind
            prefs.refresh_devices()
            if any(d.type == kind for d in prefs.devices):
                for d in prefs.devices:
                    d.use = d.type == kind
                scene.cycles.device = "GPU"
                break
        scene.cycles.samples = 48
        scene.cycles.use_denoising = True
        placed = []
        for e in items:
            root, sz = dress.place(scene, path(e), (0.0, 0.0, 0.0), name=e["id"], tallest=role == "plant")
            sizes[e["id"]] = [round(sz.x, 3), round(sz.y, 3), round(sz.z, 3)]
            if role in ("rug", "runner"):  # stood up to face the camera, its far edge on the floor
                root.rotation_euler[0] = math.radians(90)
                root["lift"] = sz.y / 2
                sz = Vector((sz.x, sz.z, sz.y))
            placed.append((e, root, sz))
        widths = sorted(max(sz.x, sz.y) for _, _, sz in placed)
        cell = max(widths[int(len(widths) * 0.9)], 0.08) * 1.25
        cols = max(1, round(math.sqrt(len(placed)) * 1.25))
        rows = math.ceil(len(placed) / cols)
        tall = sorted(sz.z for _, _, sz in placed)[int(len(placed) * 0.9)]
        rowh = max(tall, cell * 0.3) * 1.35
        for k, (e, root, sz) in enumerate(placed):
            root.location = ((k % cols + 0.5) * cell, rows * cell * 0.25, (rows - 1 - k // cols) * rowh + root.get("lift", 0.0))
        floor = bpy.data.meshes.new("wall")
        extent = max(cols * cell, rows * rowh) * 10
        floor.from_pydata([(-extent, cell * rows, -extent), (extent, cell * rows, -extent), (extent, cell * rows, extent),
                           (-extent, cell * rows, extent)], [], [(0, 1, 2, 3)])
        scene.collection.objects.link(bpy.data.objects.new("wall", floor))
        world = bpy.data.worlds.new("w")
        world.use_nodes = True
        world.node_tree.nodes["Background"].inputs[0].default_value = (0.9, 0.9, 0.9, 1)
        world.node_tree.nodes["Background"].inputs[1].default_value = 1.0
        scene.world = world
        sun = bpy.data.lights.new("s", "SUN")
        sun.energy = 2.5
        so = bpy.data.objects.new("s", sun)
        so.rotation_euler = (math.radians(55), math.radians(-25), 0)
        scene.collection.objects.link(so)
        cd = bpy.data.cameras.new("c")
        cd.type = "ORTHO"
        w, h = cols * cell, rows * rowh
        cd.ortho_scale = max(w, h) * 1.02
        cam = bpy.data.objects.new("c", cd)
        scene.collection.objects.link(cam)
        cam.location = (w / 2, -extent / 2, h / 2)
        cam.rotation_euler = (math.radians(90), 0, 0)
        scene.camera = cam
        res = 1800
        scene.render.resolution_x = res if w >= h else max(64, round(res * w / h))
        scene.render.resolution_y = res if h > w else max(64, round(res * h / w))
        out = os.path.join(out_dir, f"sheet-{role}.png")
        scene.render.filepath = out
        bpy.ops.render.render(write_still=True)
        bpy.context.view_layer.update()
        im = Image.open(out).convert("RGB")
        d = ImageDraw.Draw(im)
        index = {}
        for k, (e, root, sz) in enumerate(placed):
            v = world_to_camera_view(scene, cam, root.matrix_world.translation)
            x, y = v.x * im.width, (1 - v.y) * im.height
            d.text((x - 8, y + 1), str(k), fill=(200, 0, 0))
            index[str(k)] = e["id"]
        im.save(out)
        json.dump(index, open(out.replace(".png", ".json"), "w"))
        print(f"PHOTO sheet {role}: {len(placed)} objects", flush=True)
    json.dump(sizes, open(os.path.join(out_dir, "sizes.json"), "w"))


if __name__ == "__main__":
    import collections
    import sys

    if len(sys.argv) > 2 and sys.argv[1] == "sheet":
        sheet(sys.argv[2], sys.argv[3:])
        raise SystemExit
    rooms = json.load(open(sys.argv[1], encoding="utf-8"))
    rooms = rooms["rooms"] if isinstance(rooms, dict) else rooms
    init(rooms)
    count = collections.Counter(i for s in ALLOC.values() for k, v in s.items() if k != "accent" for i in v)
    for r, s in list(ALLOC.items())[:8]:
        print(f"{r[:26]:26} {str(s.get('accent')):7}", {k: len(v) for k, v in s.items() if k != "accent" and v})
    by_role = collections.defaultdict(list)
    for i, c in count.items():
        by_role[BY[i]["role"]].append(c)
    print("candidates per object, by role (max):", {k: max(v) for k, v in by_role.items()})
    print("objects in the library:", len(LIB), "| on some room's list:", len(count))
