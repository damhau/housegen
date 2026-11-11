"""The photo pipeline's objects (made/objects.json, made/obj_fetch.py): real products from Amazon Berkeley Objects
(CC BY 4.0) and Poly Haven models (CC0), by role: a rule asks for "a vase for this room", not for one named model.

Which object goes where is worked out once for the whole house (every room of rooms.json, the same answer in every
process that renders one of its rooms), as for the wall art, so an object does not come back in another room until its
role runs out:
- rooms in order of how likely they are to be photographed;
- each room gets a colour accent (warm, cool, green or none), so what lands in it goes together;
- each room gets a short list per role, more than a rule will use: a rule takes the first that fits its surface.

Without a catalogue the rules fall back to their fixed objects.
"""

import hashlib
import json
import os
import random

try:
    import bpy
    from mathutils import Vector
except ImportError:  # the allocation alone, outside Blender
    bpy = Vector = None

import paths

CATALOGUE = os.path.join(paths.MADE, "objects.json")

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
        return os.path.join(paths.OBJECTS, f"{e['id']}.glb")
    if e["src"] == "made":
        return os.path.join(paths.MADE, "stems", f"{e['id']}.glb")
    return os.path.join(paths.ASSETS, "models", e["id"], f"{e['id']}.gltf")


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
    LIB = load() if os.path.exists(CATALOGUE) else []
    BY = {e["id"]: e for e in LIB}
    ALLOC = allocate(rooms, LIB) if LIB else {}
    TAKEN = {}  # how many rooms of the house hold each object
    for slots in ALLOC.values():
        for role, ids in slots.items():
            for i in ids if role != "accent" else ():
                TAKEN[i] = TAKEN.get(i, 0) + 1
    print(f"PHOTO objects: {len(LIB)} in the library, {len(ALLOC)} rooms", flush=True)


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
