"""Dressing a room from the object library (objlib): what a stager would bring, chosen per room and per surface, so two
rooms of a house don't hold the same things. Nothing here knows a room or a camera: it reads the kit's pieces, the free
surfaces and walls, and what the camera sees. style.py calls these instead of its fixed rules when the library is on
(OBJ_LIB=0 keeps the fixed rules, as the control).

- `swap_kit`, on the whole storey before the camera is set: the kit's lamps, pendants, plants, vases, benches and mirrors
  become library pieces, standing where the kit put them.
- `surface` rules (`nightstand`, `coffee`, `dining`, `sideboard`, `sill`): an arrangement per room from a few, each slot
  a role, each object fitted to the place it lands.
- `floor`: what stands on the floor, more of it in a larger room (a plant, a floor lamp, a pouf, a basket, a tall vase).
- `sofa_side`, `bed_foot`, `wall`, `desk`: side tables, benches, mirrors and clocks, a dressed desk.
"""

import math

import bpy
from mathutils import Vector

import objlib
import style as S

H = None  # photo.py's module, set by photo.main


def log(*a):
    print("PHOTO stage:", *a, flush=True)


def sz(e):
    return objlib.size(e)


def within(w=9.0, d=9.0, h=9.0, low=0.0):
    """A fit test: at most w x d across (either way round), at most h tall, at least `low` tall."""
    def fit(e):
        a, b, c = sz(e)
        return max(a, b) <= max(w, d) and min(a, b) <= min(w, d) and low <= c <= h
    return fit


def put(scene, room, e, at, turn_deg=0.0, height=None):
    root, size = H.place(scene, objlib.path(e), at, height=height, turn_deg=turn_deg, name=e["id"], tallest=e["role"] == "plant")
    root["obj_id"] = e["id"]
    root["obj_role"] = e["role"]
    log(f"{e['role']} {e['id']} ({size.x:.2f} x {size.y:.2f} x {size.z:.2f}) in {room['name'] if isinstance(room, dict) else room}")
    return root, size


def at_b(p, z=None):
    """A Blender point as a scene point for place()."""
    return (p.x, p.z if z is None else z, -p.y)


def as_piece(root, kind_name):
    """A library piece the rules treat like the kit's own: storey_pieces and the frame rules see it."""
    lo, hi = H.box(root)
    root["kind"] = "furniture"
    root["name"] = kind_name
    root["footprint"] = [hi.x - lo.x, hi.y - lo.y]
    return root


# ---------------------------------------------------------------- pieces made of two


NOT_LEAVES = ("_pot", "_dirt", "_pebbles", "_soil", "_ground")


def foliage(scene, path, at, height, turn_deg=0.0, seed=0):
    """A plant out of its pot: its leaves and stems only, base centred at scene point `at`. A file holding variants side
    by side (names ending _a, _b...; a tree's bark_a goes with its leaves_a) gives the one `seed` draws."""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in new if o.type == "MESH"]
    letter = {o: o.name.split(".")[0].rsplit("_", 1)[-1] for o in meshes}
    variants = sorted({v for v in letter.values() if len(v) == 1})
    if variants:
        chosen = variants[seed % len(variants)]
        meshes = [o for o in meshes if letter[o] == chosen]
    meshes = [o for o in meshes if not o.name.split(".")[0].endswith(NOT_LEAVES)]
    for o in [o for o in new if o.type == "MESH" and o not in meshes]:
        bpy.data.objects.remove(o, do_unlink=True)
    root = bpy.data.objects.new("foliage", None)
    scene.collection.objects.link(root)
    for o in meshes:
        wm = o.matrix_world.copy()
        o.parent = root
        o.matrix_world = wm
    bpy.context.view_layer.update()
    lo, hi = H.box(root)
    for o in meshes:
        o.location -= Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
    s = height / max(hi.z - lo.z, 1e-3)
    root.scale = (s, s, s)
    root.rotation_euler[2] = math.radians(turn_deg)
    root.location = (at[0], -at[2], at[1])
    bpy.context.view_layer.update()
    return root


def soil(scene, x, y, z, r):
    """Dark earth in a pot's mouth."""
    me = bpy.data.meshes.new("soil")
    n = 32
    me.from_pydata([(r * math.cos(2 * math.pi * k / n), r * math.sin(2 * math.pi * k / n), 0.0) for k in range(n)], [], [tuple(range(n))])
    o = bpy.data.objects.new("soil", me)
    o.location = (x, y, z)
    scene.collection.objects.link(o)
    m = bpy.data.materials.get("archviz-soil") or bpy.data.materials.new("archviz-soil")
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = (0.035, 0.026, 0.02, 1)
    p.inputs["Roughness"].default_value = 0.95
    me.materials.append(m)
    return o


def adopt(root, child):
    """child now moves and hides with root, staying where it is."""
    bpy.context.view_layer.update()
    wm = child.matrix_world.copy()
    child.parent = root
    child.matrix_parent_inverse = root.matrix_world.inverted()
    child.matrix_world = wm


def potted(scene, room, at, total, floor=True, turn_deg=0.0, max_r=None):
    """A library plant in a library pot, standing at scene point `at`, about `total` m tall in all: one object for the
    rules (hide, frame). (root, size of the pot), or None if the room has no pot or plant left."""
    role = "pot_floor" if floor else "pot_table"
    limit = (lambda e: max(sz(e)[:2]) <= 2 * max_r and sz(e)[2] <= total * 0.6) if max_r else (lambda e: sz(e)[2] <= total * 0.6)
    pot = objlib.pick(room, role, fit=limit) or objlib.pick(room, "pot_table", fit=limit)
    if pot is None:
        return None
    tall = total >= 1.2
    plant = objlib.pick(room, "plant", fit=lambda e: bool(e.get("tall")) == tall) or objlib.pick(room, "plant")
    if plant is None:
        return None
    root, size = put(scene, room, pot, at, turn_deg)
    rim = size.z * 0.93
    x, y, z = at
    leaves = foliage(scene, objlib.path(plant), (x, y + rim - 0.01, z), max(total - rim, 0.12), turn_deg + 40,
                     seed=objlib._h(f"{room['name'] if isinstance(room, dict) else room}|{plant['id']}"))
    adopt(root, leaves)
    adopt(root, soil(scene, x, -z, y + rim, min(size.x, size.y) * 0.44))
    root["obj_role"] = "plant"
    log(f"plant {plant['id']} in {pot['id']}, {total:.2f} m tall")
    return root, size


def stems_in(scene, room, x, y, z, height, turn_deg=0.0, root=None):
    """The room's branches, standing at a scene point (a vase's mouth), moving with `root` if given."""
    e = objlib.pick(room, "stems") or next(iter(objlib.picks(room, "stems")), None)
    if e is None:
        return None
    br, _ = H.place(scene, objlib.path(e), (x, y, z), height=height, turn_deg=turn_deg, name=e["id"])
    br["obj_id"] = e["id"]
    if root is not None:
        adopt(root, br)
    log(f"branches {e['id']}")
    return br


def vase_with(scene, room, at, h_max, r_max, stems=True, turn_deg=0.0, low=0.0):
    """A library vase, branches in it if `stems`. Returns (root, size) or None."""
    e = objlib.pick(room, "vase", fit=lambda e: low <= sz(e)[2] <= h_max and max(sz(e)[:2]) <= 2 * r_max)
    if e is None:
        return None
    root, size = put(scene, room, e, at, turn_deg)
    if stems:
        stems_in(scene, room, at[0], at[1] + size.z * 0.8, at[2], max(size.z * 1.5, 0.3), turn_deg + 70, root)
    return root, size


def books_at(scene, room, p, turn_rad, scale=1.0, slot=0):
    """A stack of books at Blender point p, its colours the room's own. Returns the stack."""
    seed = objlib._h(f"{room['name'] if isinstance(room, dict) else room}|books|{slot}") % 12
    return S.put(scene, S.prop("books", seed), p, turn_rad, scale)


def small_on(scene, room, p, r, slot):
    """Something small standing on Blender point p, at most r in radius; what depends on the room."""
    kinds = objlib.choose(room, f"small{slot}", [("pot",), ("vase",), ("candle",), ("sculpture",), ("jar",), ("curio",), ("vase", "stems")])
    x, z, y = p.x, p.z, -p.y
    if kinds[0] == "pot":
        if potted(scene, room, (x, z, y), 0.26, floor=False, max_r=r):
            return True
    if kinds[0] == "vase":
        if vase_with(scene, room, (x, z, y), 0.24 if len(kinds) == 1 else 0.17, r, stems=len(kinds) > 1):
            return True
    if kinds[0] in ("candle", "sculpture", "jar", "curio"):
        e = objlib.pick(room, kinds[0], fit=within(2 * r, 2 * r, 0.3))
        if e is not None:
            put(scene, room, e, (x, z, y), turn_deg=objlib._h(e["id"]) % 60 - 30)
            return True
    if vase_with(scene, room, (x, z, y), 0.2, r, stems=False):
        return True
    return False


# ---------------------------------------------------------------- the kit's own pieces


def wall_at(scene, dg, p, reach=0.4):
    """(distance, normal into the room) of the nearest wall within reach of p, None if none."""
    best = None
    for k in range(16):
        a = k * math.pi / 8
        hit = H.trace(scene, dg, p, Vector((math.cos(a), math.sin(a), 0)), far=reach)
        if hit is not None and (best is None or (hit[0] - p).length < best[0]):
            n = Vector((hit[1].x, hit[1].y, 0))
            if n.length > 0.5:
                best = ((hit[0] - p).length, n.normalized())
    return best


def room_of(o):
    """The room a piece stands in; a stand-in with a name of its own if it is in none."""
    x, y, z = H.to_scene(o.matrix_world.translation)
    r = H.room_at(objlib.ROOMS, x, y + 0.05, z)
    return r if r else {"name": f"none-{round(y)}", "use": ""}


def swap_kit(scene, pieces):
    """The kit's lamps, pendants, plants, vases, benches and mirrors, from the library, where the kit put them. The kit's
    LIGHT objects stay, wherever the piece they came with goes (photo.lamps_on turns them on by position)."""
    n = {}

    def count(what):
        n[what] = n.get(what, 0) + 1

    for o in pieces:
        nm = H.name(o).lower()
        if o.hide_render:
            continue
        lo, hi = H.box(o)
        t = o.matrix_world.translation
        room = room_of(o)
        c = (lo + hi) / 2
        if nm.endswith("table lamp"):
            e = objlib.pick(room, "table_lamp", fit=within(0.5, 0.5, 0.68))
            if e:
                H.hide(o)
                root, _ = put(scene, room, e, (t.x, lo.z, -t.y), turn_deg=math.degrees(H.turn(o)))
                as_piece(root, "table lamp (library)")
                count("table lamps")
        elif nm.endswith("floor lamp"):
            e = objlib.pick(room, "floor_lamp", fit=within(1.2, 1.2, 2.1))
            if e:
                H.hide(o)
                root, _ = put(scene, room, e, (t.x, lo.z, -t.y), turn_deg=math.degrees(H.turn(o)))
                as_piece(root, "floor lamp (library)")
                count("floor lamps")
        elif nm.endswith("desk lamp"):
            e = objlib.pick(room, "desk_lamp")
            if e:
                H.hide(o)
                root, _ = put(scene, room, e, (t.x, lo.z, -t.y), turn_deg=math.degrees(H.turn(o)))
                as_piece(root, "desk lamp (library)")
                count("desk lamps")
        elif "pendant" in nm or "chandelier" in nm:
            wide = "chandelier" in nm or "cluster" in nm or "linear" in nm
            e = (objlib.pick(room, "chandelier") if wide else None) or objlib.pick(room, "pendant")
            if e:
                H.hide(o)
                # its bottom where the kit's was, unless its cord is too short to reach the ceiling from there. A long
                # cord just runs up into the slab, which hides it
                base = max(lo.z, hi.z - sz(e)[2])
                root, _ = put(scene, room, e, (c.x, base, -c.y), turn_deg=math.degrees(H.turn(o)))
                as_piece(root, "pendant (library)")
                count("pendants")
        elif nm in ("plant-large", "plant-leafy-white-pot", "plant-small"):
            h = hi.z - lo.z
            onfloor = lo.z - t.z < 0.1 and h >= 0.5 and lo.z < t.z + 0.1 and nm != "plant-small"
            at = (t.x, lo.z, -t.y)
            turn = (objlib._h(f"{room['name']}|{nm}|{t.x:.1f}") % 360)
            H.hide(o)
            done = potted(scene, room, at, max(h, 1.35) if onfloor else max(min(h, 0.45), 0.25), floor=onfloor, turn_deg=turn,
                          max_r=None if onfloor else max(hi.x - lo.x, hi.y - lo.y) / 2 + 0.03)
            if done:
                count("plants")
            else:
                for c in H.family(o):
                    c.hide_render = False
        elif nm in ("vase-white", "vase-dry-branches"):
            done = vase_with(scene, room, (c.x, lo.z, -c.y), max(hi.z - lo.z, 0.3) + 0.05, max(hi.x - lo.x, hi.y - lo.y) / 2 + 0.05,
                             stems=objlib.choose(room, "kit-vase", [True, False]), low=0.6 * (hi.z - lo.z))
            if done:
                H.hide(o)
                count("vases")
        elif nm == "bench":
            fp = list(o.get("footprint", (hi.x - lo.x, hi.y - lo.y)))
            long_, short = max(fp), min(fp)
            e = objlib.pick(room, "bench", fit=lambda e: max(sz(e)[:2]) <= long_ + 0.1 and min(sz(e)[:2]) <= short + 0.12)
            if e:
                H.hide(o)
                a, b, _ = sz(e)
                # along the kit bench's long side
                turn = math.degrees(H.turn(o)) + (0.0 if (fp[0] >= fp[1]) == (a >= b) else 90.0)
                root, _ = put(scene, room, e, (t.x, lo.z, -t.y), turn_deg=turn)
                as_piece(root, "bench (library)")
                count("benches")
        elif "mirror" in nm and "light" not in nm:
            width = max(hi.x - lo.x, hi.y - lo.y)
            e = objlib.pick(room, "mirror", fit=lambda e: sz(e)[0] <= width * 1.3 and sz(e)[2] <= (hi.z - lo.z) * 1.4 and sz(e)[1] < 0.2)
            if e is None:
                continue
            H.hide(o)  # the trace goes through what the render hides: the wall behind, not the kit's mirror
            bpy.context.view_layer.update()
            wall = wall_at(scene, bpy.context.evaluated_depsgraph_get(), c)
            if wall is None:
                for k in H.family(o):
                    k.hide_render = False
                continue
            dist, normal = wall
            q = c - normal * (dist - sz(e)[1] / 2 - 0.002)
            put(scene, room, e, (q.x, c.z - sz(e)[2] / 2, -q.y), turn_deg=math.degrees(math.atan2(normal.x, -normal.y)))
            count("mirrors")
    log("kit swapped:", n)
    return n


# ---------------------------------------------------------------- rugs


def rug(scene, room, center, size, floor, turn_rad=0.0, role="rug"):
    """A library rug for a w x l area (w along the local x, l along the local y), center in scene x, z; no larger than the
    area. Falls back on the plain one if the room's list has none of about that shape."""
    w, l = size
    want = max(w, l) / max(min(w, l), 0.1)

    def shape(e):
        a, b = sz(e)[:2]
        have = max(a, b) / max(min(a, b), 0.1)
        s = min(max(w, l) / max(a, b), min(w, l) / max(min(a, b), 0.1))  # its weave would show too large or too small
        return abs(have - want) <= 0.45 and (have > 1.05 or want < 1.15) and 0.6 <= s <= 1.45

    wet = isinstance(room, dict) and room.get("use") in ("bath", "wc", "laundry")  # a bath mat, not a carpet
    e = None if wet else objlib.pick(room, role, fit=shape)
    if e is None:
        r = H.dress.rug(scene, center, size, floor=floor)
        r.rotation_euler[2] = turn_rad
        return r
    a, b, c = sz(e)
    s = min(max(w, l) / max(a, b), min(w, l) / min(a, b))
    turn = turn_rad + (0.0 if (a >= b) == (w >= l) else math.pi / 2)
    root, _ = put(scene, room, e, (center[0], floor + 0.002, center[1]), turn_deg=math.degrees(turn), height=c * s)
    root["is_rug"] = True
    log(f"rug {e['id']} at {s:.2f} of its size, for {w:.2f} x {l:.2f}")
    return root


def our_rugs(scene):
    return [o for o in scene.objects if o.get("is_rug")] + S.our_rugs(scene)


# ---------------------------------------------------------------- surfaces


def nightstand(scene, cam, room, sy, pieces):
    """Each nightstand the camera sees: the lamp to the back corner away from the bed, books at the front on the bed's
    side with something small on them, or something small alone; a carafe at the other front corner when there is room."""
    beds = [f for f, _ in H.BEDS]
    n = 0
    for o in pieces:
        nm = H.name(o).lower()
        if "nightstand" not in nm and "bedside" not in nm or len(o.get("footprint", ())) != 2 or not S.in_room(o, room):
            continue
        lo, hi = H.box(o)
        ok, _ = S.in_view(scene, cam, (lo + hi) / 2, 0.0)
        if not ok or not beds:
            continue
        w, d = o["footprint"]
        f = H.frame_of(o, lo.z)
        bed = min(beds, key=lambda b: (b.translation - f.translation).length)
        s = 1.0 if (f.inverted() @ bed.translation).x > 0 else -1.0
        top = hi.z
        for lamp in pieces:
            llo, lhi = H.box(lamp)
            t = lamp.matrix_world.translation
            if lamp is o or lamp.hide_render or abs(llo.z - top) > 0.03 or not (lo.x <= t.x <= hi.x and lo.y <= t.y <= hi.y):
                continue
            at = f @ Vector((-s * (w / 2 - 0.08), d / 2 - 0.08, top - lo.z))
            lamp.matrix_world.translation = Vector((at.x, at.y, lamp.matrix_world.translation.z))
        bpy.context.view_layer.update()
        scale = min(1.0, (w * 0.62) / 0.26, (d * 0.55) / 0.19)
        p = f @ Vector((s * (w / 2 - 0.13 * scale - 0.015), -(d / 2 - 0.095 * scale - 0.015), top - lo.z + 0.001))
        how = objlib.choose(room, f"bedside{n}", ["books+small", "books", "small"])
        if how.startswith("books"):
            books = books_at(scene, room, p, H.turn(o) + math.radians(-8 * s), scale, slot=n)
            bpy.context.view_layer.update()
            if how == "books+small":
                blo, bhi = H.box(books)
                cc = (blo + bhi) / 2
                small_on(scene, room, Vector((cc.x + 0.02 * s, cc.y, bhi.z)), 0.09, f"bed{n}")
        else:
            small_on(scene, room, p, 0.09, f"bed{n}")
        if w >= 0.45 and d >= 0.36 and objlib.choose(room, f"carafe{n}", [True, False]):
            S.put(scene, S.prop("carafe"), f @ Vector((-s * (w / 2 - 0.06), -(d / 2 - 0.06), top - lo.z + 0.001)),
                  H.turn(o) + (0.0 if s > 0 else math.pi) + math.radians(12))
        log(f"{nm} ({w:.2f} x {d:.2f}): {how}")
        n += 1
    return n


COFFEE = (("books+vase", "candle"), ("tray+candle", "books+small"), ("bowl", "books+jar"), ("books+curio", "vase"),
          ("vase", "tray+small"), ("books+sculpture", "bowl"))


def arrange(scene, room, what, p, turn_rad, slot, r=0.13):
    """One arrangement at Blender point p on a flat top."""
    x, z, y = p.x, p.z, -p.y
    if what.startswith("books"):
        books = books_at(scene, room, p, turn_rad + math.radians(8), 1.25, slot=slot)
        bpy.context.view_layer.update()
        blo, bhi = H.box(books)
        c = (blo + bhi) / 2
        q = Vector((c.x, c.y, bhi.z))
        if what == "books+vase":
            vase_with(scene, room, at_b(q), 0.2, 0.08, stems=True)
        elif "+" in what:
            role = what.split("+")[1]
            if role == "small":
                small_on(scene, room, q, 0.08, f"{what}{slot}")
            else:
                e = objlib.pick(room, role, fit=within(0.2, 0.2, 0.3))
                if e:
                    put(scene, room, e, at_b(q), turn_deg=objlib._h(e["id"]) % 50 - 25)
        return True
    if what.startswith("tray"):
        e = objlib.pick(room, "tray", fit=within(0.55, 0.4, 0.15))
        if e is None:
            return arrange(scene, room, "books+small", p, turn_rad, slot, r)
        root, size = put(scene, room, e, (x, z, y), turn_deg=math.degrees(turn_rad) + 6)
        q = Vector((p.x, p.y, p.z + 0.02))
        small_on(scene, room, q, 0.07, f"tray{slot}")
        return True
    if what == "bowl":
        e = objlib.pick(room, "bowl", fit=within(0.36, 0.36, 0.2))
        if e:
            root, size = put(scene, room, e, (x, z, y))
            return True
        return False
    if what == "vase":
        return bool(vase_with(scene, room, (x, z, y), 0.32, r, stems=False))
    if what == "candle":
        e = objlib.pick(room, "candle", fit=within(0.3, 0.3, 0.35))
        if e:
            put(scene, room, e, (x, z, y), turn_deg=20)
            return True
        S.put(scene, S.prop("candles"), p, turn_rad + 0.4)
        return True
    return False


def coffee(scene, cam, room, sy, pieces):
    """A coffee table the camera sees: what the kit left on it goes; an arrangement at each end of its long axis."""
    n = 0
    for o in pieces:
        nm = H.name(o).lower()
        if "coffee" not in nm or "machine" in nm or len(o.get("footprint", ())) != 2 or not S.in_room(o, room):
            continue
        if not S.shown(scene, cam, o):
            continue
        lo, hi = H.box(o)
        top = [Vector((lo.x + (hi.x - lo.x) * i / 6, lo.y + (hi.y - lo.y) * j / 6, hi.z)) for i in range(7) for j in range(7)]
        seen = sum(S.in_view(scene, cam, p, 0.0)[0] for p in top) / len(top)
        if seen < S.COFFEE_SEEN:
            for p in [o] + [p for p in pieces if p is not o and lo.x <= p.matrix_world.translation.x <= hi.x
                            and lo.y <= p.matrix_world.translation.y <= hi.y and lo.z <= p.matrix_world.translation.z <= hi.z + 0.3]:
                H.hide(p)
            log(f"{nm}: {seen * 100:.0f} % of its top in frame, taken out of the shot")
            continue
        for p in pieces:
            t = p.matrix_world.translation
            if p is not o and lo.x <= t.x <= hi.x and lo.y <= t.y <= hi.y and hi.z - 0.08 <= t.z <= hi.z + 0.3:
                H.hide(p)
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        w, d = o["footprint"]
        f = H.frame_of(o, lo.z)
        along = Vector((1, 0, 0)) if w >= d else Vector((0, 1, 0))
        length = max(w, d)
        free = []
        for k in range(-8, 9):
            a = along * (k * length / 18)
            spot = S.top_spots(scene, dg, o, lo.z, 0.12, [a.x], [a.y])
            if spot:
                free.append(spot[0])
        ends = []
        for sgn in (-1, 1):
            cands = [(abs(along.dot(Vector((u, v, 0))) - sgn * length * 0.25), (u, v, h)) for u, v, h in free
                     if sgn * along.dot(Vector((u, v, 0))) > 0.05]
            if cands:
                ends.append(min(cands)[1])
        if len(ends) < 2:
            log(f"{nm}: no free spot toward both ends")
            continue
        cam_at = cam.matrix_world.translation
        far, near = sorted(ends, key=lambda e: -(f @ Vector(e) - cam_at).length)
        a, b = objlib.choose(room, "coffee", COFFEE)
        arrange(scene, room, a, f @ Vector((far[0], far[1], far[2] + 0.001)), H.turn(o), 0)
        arrange(scene, room, b, f @ Vector((near[0], near[1], near[2] + 0.001)), H.turn(o), 1)
        log(f"{nm} ({w:.2f} x {d:.2f}): {a} far, {b} near")
        n += 1
    return n


FRUIT = ("lemon", "food_apple_01", "food_pears_asian_01")


def fruit_in(scene, room, x, z, y):
    """A bowl of the room's fruit at a scene point: the bowl and the mix differ from room to room."""
    e = objlib.pick(room, "bowl", fit=within(0.36, 0.36, 0.2))
    if e is None:
        return False
    _, size = put(scene, room, e, (x, z, y), turn_deg=objlib._h(e["id"]) % 90)
    a, b = objlib.choose(room, "fruit", [(p, q) for p in FRUIT for q in FRUIT])
    r = min(size.x, size.y) * 0.5
    for k, (dx, dz, fruit) in enumerate(((-0.05, 0.0, a), (0.05, 0.03, b), (0.0, -0.05, b), (0.02, 0.06, a), (-0.01, 0.01, b))):
        H.place(scene, H.model(fruit), (x + dx * r / 0.12, z + size.z * 0.55 + (0.03 if k >= 3 else 0), y + dz * r / 0.12), height=0.07,
                turn_deg=70 * k)
    return True


def counter(scene, room, points, z, turn_rad):
    """A kitchen's counter, at Blender points along its free stretch: fruit, then what stands on one in this room."""
    plans = (("fruit", "kitchen", "canister"), ("kitchen", "fruit", "stems"), ("canister", "fruit", "kitchen"), ("fruit", "canister", "kitchen"),
             ("kitchen", "stems", "fruit"), ("bananas", "kitchen", "canister"))
    plan = objlib.choose(room, "counter", plans)
    done = []
    for what, p in zip(plan, points):
        x, y = p.x, -p.y
        ok = False
        if what == "fruit":
            ok = fruit_in(scene, room, x, z, y)
        elif what == "bananas":
            e = objlib.pick(room, "fruit", fit=within(0.4, 0.4, 0.25))
            if e:
                put(scene, room, e, (x, z, y), turn_deg=math.degrees(turn_rad) + 20)
                ok = True
        elif what == "stems":
            ok = bool(vase_with(scene, room, (x, z, y), 0.3, 0.09, stems=True))
        else:
            e = objlib.pick(room, what, fit=within(0.5, 0.42, 0.42))
            if e:
                root, size = put(scene, room, e, (x, z, y), turn_deg=math.degrees(turn_rad) + (objlib._h(e["id"]) % 50) - 25)
                ok = True
                if "cutting_board" in e["id"]:
                    for k, (dx, dz) in enumerate(((-0.05, 0.02), (0.05, -0.03), (0.0, 0.07))):
                        H.place(scene, H.model("lemon"), (x + dx, z + size.z, y + dz), height=0.065, turn_deg=40 * k)
        if ok:
            done.append(what)
    log(f"counter in {room['name']}: {done}")
    return len(done)


def sill(scene, x, z, y):
    """Something on a window sill: a small vase with branches or a small plant, by the room."""
    r = next((r for dx, dy in ((0, 0), (0.3, 0), (-0.3, 0), (0, 0.3), (0, -0.3), (0.6, 0), (-0.6, 0), (0, 0.6), (0, -0.6))
              for dz in (0.05, -1.0) if (r := H.room_at(objlib.ROOMS, x + dx, z + dz, y + dy))),  # the sill is in the wall
             {"name": "sill", "use": ""})
    if objlib.choose(r, "sill", [True, False]):
        return bool(potted(scene, r, (x, z, y), 0.32, floor=False, max_r=0.08))
    return bool(vase_with(scene, r, (x, z, y), 0.2, 0.07, stems=True))


def dining(scene, cam, room, sy, pieces):
    """A dining table: napkins on its plates (as the fixed rule), then on its centre line candles at a quarter from
    one end, a carafe or a vase from the other, a bowl or a vase toward the middle."""
    dg = bpy.context.evaluated_depsgraph_get()
    n = 0
    for o in pieces:
        nm = H.name(o).lower()
        if "dining-table" not in nm and "dining table" not in nm or not S.in_room(o, room):
            continue
        lo, hi = H.box(o)
        w, d = o["footprint"]
        f = H.frame_of(o, lo.z)
        along = Vector((0, 1, 0)) if d >= w else Vector((1, 0, 0))
        across = math.pi / 2 if d >= w else 0.0
        plates = 0
        napkin = objlib.choose(room, "napkins", [True, True, False])
        for p in pieces:
            if H.name(p) != "plate":
                continue
            plo, phi = H.box(p)
            t = p.matrix_world.translation
            if not (lo.x <= t.x <= hi.x and lo.y <= t.y <= hi.y):
                continue
            if napkin:
                S.put(scene, S.prop("napkin"), Vector((t.x, t.y, phi.z + 0.0005)),
                      H.turn(o) + across + math.radians(((plates * 37) % 11) - 5))
            plates += 1
        glass = None
        for g in pieces:
            t = g.matrix_world.translation
            if "glass" not in H.name(g) or not (lo.x <= t.x <= hi.x and lo.y <= t.y <= hi.y):
                continue
            glass = glass or H.clear_glass(name="archviz-glass-table")
            for c in [g, *g.children_recursive]:
                for sl in c.material_slots if c.type == "MESH" else []:
                    if sl.material is not None and sl.material.name.startswith("glass"):
                        sl.material = glass
        for m in bpy.data.materials:
            if m.name.startswith("archviz-runner"):
                q = next(k for k in m.node_tree.nodes if k.type == "BSDF_PRINCIPLED")
                q.inputs["Base Color"].default_value = (*S.RUNNER, 1)
                q.inputs["Sheen Weight"].default_value = 0.12
                q.inputs["Sheen Tint"].default_value = (*S.RUNNER, 1)
        length = max(w, d)
        free = []
        for k in range(-8, 9):
            a = along * (k * length / 18)
            spot = S.top_spots(scene, dg, o, lo.z, 0.125, [a.x], [a.y])
            if spot:
                free.append(spot[0])
        order = objlib.choose(room, "table", [("candle", "carafe", "fruit"), ("candle", "vase", "fruit"), ("vase", "candle", "bowl"),
                                              ("carafe", "candle", "vase")])
        placed, names = [], []
        for what, r, gap, at in ((order[0], 0.125, 0.25, 0.27), (order[1], 0.08, 0.25, 0.27), (order[2], 0.125, 0.05, 0.0)):
            best = None
            for u, v, h in free:
                if any(math.hypot(u - pu, v - pv) < r + pr + gap for pu, pv, pr in placed):
                    continue
                key = abs(abs(along.dot(Vector((u, v, 0)))) - length * at)
                if best is None or key < best[0]:
                    best = (key, u, v, h)
            if not best:
                continue
            _, u, v, h = best
            p = f @ Vector((u, v, h + 0.001))
            done = False
            if what == "fruit":
                done = fruit_in(scene, room, p.x, p.z, -p.y)
            elif what == "carafe":
                S.put(scene, S.prop("carafe1"), p, H.turn(o) + 0.4)
                done = True
            elif what == "candle":
                done = arrange(scene, room, "candle", p, H.turn(o), 2)
            elif what == "vase":
                done = bool(vase_with(scene, room, (p.x, p.z, -p.y), 0.3, 0.1, stems=objlib.choose(room, "table-stems", [True, False])))
            elif what == "bowl":
                done = arrange(scene, room, "bowl", p, H.turn(o), 3)
            if done:
                placed.append((u, v, r))
                names.append(what)
        log(f"{nm}: {plates} plates, {names} on the centre line")
        n += 1
    return n


def sideboard_top(scene, room, tall_at, low_at, mid_at, top, turn_rad):
    """A sideboard: something tall at the end toward the frame's edge, something mid-height, books with something small
    on them at the other end (a print may lean between them: style.sideboard)."""
    tall = objlib.choose(room, "board-tall", ["plant", "branches", "lamp", "branches"])
    x, _, z = H.to_scene(tall_at)
    done = False
    if tall == "lamp":
        e = objlib.pick(room, "table_lamp", fit=within(0.45, 0.45, 0.65))
        if e:
            put(scene, room, e, (x, top, z), turn_deg=math.degrees(turn_rad))
            done = True
    elif tall == "plant":
        done = bool(potted(scene, room, (x, top, z), 0.55, floor=False, turn_deg=20, max_r=0.14))
    if not done:
        vase_with(scene, room, (x, top, z), 0.42, 0.13, stems=True, low=0.14)
    x, _, z = H.to_scene(mid_at)
    e = objlib.pick(room, "jar", fit=within(0.24, 0.24, 0.32)) or objlib.pick(room, "vase", fit=within(0.24, 0.24, 0.32))
    if e:
        put(scene, room, e, (x, top, z), turn_deg=-35)
    books = books_at(scene, room, Vector((low_at.x, low_at.y, top + 0.001)), turn_rad, slot=7)
    bpy.context.view_layer.update()
    blo, bhi = H.box(books)
    c = (blo + bhi) / 2
    small_on(scene, room, Vector((c.x, c.y, bhi.z)), 0.08, "board")


# ---------------------------------------------------------------- the sofa


PILLOW_TILT = 16


def pillow(scene, room, p, turn_rad, width, role="pillow"):
    """A library cushion standing at Blender point p, leaning back, `width` m wide. Returns its depth, or None."""
    e = objlib.pick(room, role)
    if e is None:
        return None
    a, b, c = sz(e)
    s = width / max(a, 0.1)
    root, _ = put(scene, room, e, at_b(p), height=c * s)
    root.rotation_euler = (math.radians(-PILLOW_TILT), 0.0, turn_rad)
    return b * s


def sofa(scene, cam, room, sy, pieces):
    """A sofa the camera sees: a throw over the arm farther from the camera (as the fixed rule), cushions from the library
    at each end, against the back. How many depends on the room."""
    n = 0
    dg = bpy.context.evaluated_depsgraph_get()
    cam_at = cam.matrix_world.translation
    for o in pieces:
        if "archviz_sofa" not in o.keys() or not S.in_room(o, room) or not S.shown(scene, cam, o):
            continue
        seat, back, a0, a1, sv, vf = o["archviz_sofa"]
        w, d = o["footprint"]
        lo, hi = H.box(o)
        f = H.frame_of(o, lo.z)
        ends = []
        for side, edge in ((1, a1), (-1, a0)):
            us = [u for u in (edge + side * 0.02 * k for k in range(0, 30)) if abs(u) <= w / 2 - 0.01]
            hs = H.heights_down(scene, dg, f, [(u, vf) for u in us], hi.z - lo.z + 0.1)
            row = [(u, h) for u, (h, _) in zip(us, hs) if h is not None]
            ah = max((h for _, h in row), default=0.0)
            plateau = [u for u, h in row if h >= ah - 0.04]
            if ah < seat + 0.08 or not plateau:
                continue
            inner = plateau[0]
            outer = max(u * side for u, h in row if h > seat * 0.5) * side
            far = (f @ Vector((side * w / 2, sv, 0)) - cam_at).length
            ends.append((far, side, edge, inner, abs(outer - inner) + 0.02, ah))
        if len(ends) < 2:
            log(f"sofa: arms not found on {H.name(o)}")
            continue
        ends.sort()
        (_, ns, nedge, *_), (ffar, fs, fedge, finner, aw, ah) = ends
        throw = 0.08 <= aw <= 0.45 and seat + 0.08 <= ah <= 0.85 and objlib.choose(room, "throw", [True, True, False])
        if throw:
            path = S.prop("sofathrow", f"{round(aw * 100)}-{round(ah * 100)}-{round(seat * 100)}")
            H.in_frame(scene, f, path, (finner, sv, 0.0), (fs, 1.0, 1.0))
        plan = objlib.choose(room, "cushions", [
            ((ns, nedge, ((0.46, 0.24, 0.0, 6), (0.40, 0.46, 0.07, 14))), (fs, fedge, ((0.44, 0.28, 0.02, 4),))),
            ((ns, nedge, ((0.44, 0.24, 0.0, 8),)), (fs, fedge, ((0.46, 0.24, 0.0, 5), (0.38, 0.46, 0.08, 12)))),
            ((ns, nedge, ((0.46, 0.24, 0.0, 6), (0.40, 0.46, 0.07, 14))), (fs, fedge, ((0.46, 0.24, 0.0, 5), (0.40, 0.46, 0.07, 14)))),
        ])
        for side, edge, row in plan:
            for width, inset, fwd, twist in row:
                u = edge - side * inset
                vs = [vf + 0.02 * k for k in range(40)]
                hs = H.heights_down(scene, dg, f, [(u, v) for v in vs], hi.z - lo.z + 0.1)
                bu = next((v for v, (h, _) in zip(vs, hs) if h is not None and h > seat + 0.12), back)
                v = bu - 0.09 - fwd
                pillow(scene, room, f @ Vector((u, v, seat + 0.003)), H.turn(o) + math.radians(-side * twist), width)
        log(f"sofa {H.name(o)}: cushions from the library{', a throw' if throw else ''}")
        n += 1
    return n


def sofa_side(scene, cam, room, sy, pieces, taken):
    """A side table at a free end of a sofa or armchair the camera sees, a lamp or something small on it."""
    n = 0
    dg = bpy.context.evaluated_depsgraph_get()
    cam_at = cam.matrix_world.translation
    for o in pieces:
        if "archviz_sofa" not in o.keys() or not S.in_room(o, room) or not S.shown(scene, cam, o):
            continue
        w, d = o["footprint"]
        lo, hi = H.box(o)
        f = H.frame_of(o, lo.z)
        e = objlib.pick(room, "side_table", fit=within(0.62, 0.62, 0.72, low=0.38))
        if e is None:
            continue
        a, b, c = sz(e)
        r = max(a, b) / 2
        best = None
        for side in (-1, 1):
            q = f @ Vector((side * (w / 2 + r + 0.05), 0.0, 0.0))
            q.z = lo.z
            if not H.inside(room["polygon"], *H.to_scene(q)[::2]) or not S.floor_free(scene, dg, q.x, q.y, sy, r):
                continue
            ok, v = S.in_view(scene, cam, q + Vector((0, 0, c)), 0.03)
            if not ok or v.z < 1.3 or not S.in_view(scene, cam, q, 0.0)[0]:
                continue
            key = (q - cam_at).length
            if best is None or key > best[0]:
                best = (key, q)
        if best is None:
            log(f"sofa side: no free end on {H.name(o)}")
            continue
        q = best[1]
        root, size = put(scene, room, e, at_b(q), turn_deg=math.degrees(H.turn(o)))
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        tl, th = H.box(root)
        top = Vector((q.x, q.y, th.z + 0.001))
        hit = H.trace(scene, dg, top + Vector((0, 0, 0.3)), Vector((0, 0, -1)), far=0.4)
        if hit is not None and abs(hit[0].z - th.z) < 0.03:  # a flat top at the centre (a shelf-less frame has none)
            top.z = hit[0].z + 0.001
            how = objlib.choose(room, "side-top", ["lamp", "small", "books"])
            if how == "lamp":
                lamp = objlib.pick(room, "table_lamp", fit=within(0.45, 0.45, 0.6))
                if lamp:
                    put(scene, room, lamp, at_b(top), turn_deg=math.degrees(H.turn(o)))
            elif how == "books":
                arrange(scene, room, "books+small", top, H.turn(o), 4)
            else:
                small_on(scene, room, top, min(size.x, size.y) * 0.4, "side")
        taken.append((q.x, q.y, r))
        log(f"side table {e['id']} by {H.name(o)}")
        n += 1
    return n


# ---------------------------------------------------------------- the floor


def floor(scene, cam, room, sy, taken, pieces):
    """What stands on the floor where the room shows most floor: more of it in a larger room."""
    xs, zs = [p[0] for p in room["polygon"]], [p[1] for p in room["polygon"]]
    area = (max(xs) - min(xs)) * (max(zs) - min(zs))
    use = room["use"]
    if use in ("bath", "wc", "laundry"):
        plans = [["basket"], ["plant"], ["basket"]]
    elif use == "kitchen":
        plans = [["plant"], ["vase"], []]
    else:
        plans = [["plant", "lamp", "pouf"], ["lamp", "basket", "plant"], ["plant", "pouf", "vase"], ["vase", "lamp", "basket"],
                 ["plant", "basket", "lamp"], ["pouf", "plant", "lamp"]]
    plan = objlib.choose(room, "floor", plans)
    budget = 1 if area < 11 else 2 if area < 22 else 3
    has_plant = any(o.get("obj_role") == "plant" and S.shown(scene, cam, o) for o in scene.objects if o.parent is None)
    has_lamp = any(H.name(o).startswith("floor lamp") and S.shown(scene, cam, o) for o in pieces)
    out = []
    for what in plan:
        if len(out) >= budget:
            break
        if what == "plant" and has_plant:
            what = "vase"
        if what == "lamp" and has_lamp:
            continue
        if what == "plant":
            spots = S.floor_spots(scene, cam, room, sy, 0.3, 1.5, taken)
            corner = [s for s in spots if s[2]] or spots
            if corner:
                _, (x, y), _, fx = corner[0]
                turn = objlib._h(f"{room['name']}|floor") % 360
                if potted(scene, room, (x, sy, -y), objlib.choose(room, "floor-plant", [1.5, 1.05, 1.15]), turn_deg=turn):
                    taken.append((x, y, 0.3))
                    out.append(what)
        elif what == "lamp":
            e = objlib.pick(room, "floor_lamp", fit=within(1.0, 1.0, 2.0))
            if e:
                r = min(max(sz(e)[:2]) / 2, 0.3)
                spots = S.floor_spots(scene, cam, room, sy, r, sz(e)[2], taken)
                corner = [s for s in spots if s[2]] or spots
                if corner:
                    _, (x, y), _, _ = corner[0]
                    put(scene, room, e, (x, sy, -y), turn_deg=objlib._h(e["id"]) % 360)
                    taken.append((x, y, r))
                    out.append(what)
        elif what in ("pouf", "basket"):
            e = objlib.pick(room, what, fit=within(0.7, 0.7, 0.55))
            if e:
                r = max(sz(e)[:2]) / 2
                spots = S.floor_spots(scene, cam, room, sy, r, sz(e)[2], taken)
                if spots:
                    _, (x, y), _, _ = spots[0]
                    put(scene, room, e, (x, sy, -y), turn_deg=objlib._h(e["id"]) % 90)
                    taken.append((x, y, r))
                    out.append(what)
        elif what == "vase":
            spots = S.floor_spots(scene, cam, room, sy, 0.16, 1.3, taken)
            if spots:
                _, (x, y), _, _ = spots[0]
                e = objlib.pick(room, "vase", fit=within(0.5, 0.5, 0.65, low=0.28))
                if e:
                    # a table vase scaled to a floor vase: the same shape a stager would pick at this size
                    h = objlib.choose(room, "floor-vase", [0.46, 0.55])
                    vase, size = put(scene, room, e, (x, sy, -y), height=h)
                    stems_in(scene, room, x, sy + size.z * 0.82, -y, 0.95, 40, root=vase)
                    taken.append((x, y, 0.16))
                    out.append(what)
    log(f"floor of {room['name']} ({area:.0f} m2): {out} of {plan}")
    return out


def bed_foot(scene, cam, room, sy, taken):
    """A bench or a pouf at the foot of a bed with room around it."""
    beds = [(f, w) for f, w in H.BEDS if H.inside(room["polygon"], *H.to_scene(f.translation)[::2])]
    if not beds or objlib.choose(room, "bed-foot", [True, True, False]) is False:
        return 0
    f, w = beds[0]
    dg = bpy.context.evaluated_depsgraph_get()
    e = objlib.pick(room, "bench", fit=lambda e: max(sz(e)[:2]) <= w and min(sz(e)[:2]) <= 0.55) or objlib.pick(room, "pouf", fit=within(0.6, 0.6, 0.5))
    if e is None:
        return 0
    a, b, c = sz(e)
    across, deep = max(a, b), min(a, b)
    q = f @ Vector((0, -2.0 - deep / 2 - 0.05, 0))
    q.z = sy
    corners = [f @ Vector((u * across / 2, -2.05 - v * deep, 0)) for u in (-1, 1) for v in (0, 1)] + [f @ Vector((0, -2.05 - deep - 0.65, 0))]
    if not all(H.inside(room["polygon"], *H.to_scene(p)[::2]) for p in corners) or not S.floor_free(scene, dg, q.x, q.y, sy, deep / 2):
        log("bed foot: no room")
        return 0
    if not S.in_view(scene, cam, q, 0.0)[0]:
        return 0
    turn = math.degrees(f.to_euler().z) + (0.0 if a >= b else 90.0)
    put(scene, room, e, at_b(q), turn_deg=turn)
    taken.append((q.x, q.y, across / 2))
    return 1


# ---------------------------------------------------------------- walls


WALL_ROLES = ("mirror", "clock", "clock_small", "wall_shelf", "tall_mirror")


def wall_piece(scene, cam, room, sy, e, centre_h):
    """A flat piece on a plain wall the camera faces: nothing on or in front of it, whole in frame. True if placed."""
    dg = bpy.context.evaluated_depsgraph_get()
    W, _, Hh = sz(e)
    poly = [tuple(p) for p in room["polygon"]]
    cpos = cam.matrix_world.translation
    hung = []  # prints and wall pieces already up: (centre, half width)
    for o in scene.objects:
        if o.parent is None and not o.hide_render and (o.name.startswith("art.glb") or o.get("obj_role") in WALL_ROLES):
            lo, hi = H.box(o)
            hung.append(((lo + hi) / 2, max(hi.x - lo.x, hi.y - lo.y) / 2))
    best = None
    for (ax, az), (bx, bz) in zip(poly, poly[1:] + poly[:1]):
        elen = math.hypot(bx - ax, bz - az)
        if elen < W + 0.3:
            continue
        ux, uz = (bx - ax) / elen, (bz - az) / elen
        nx, nz = -uz, ux
        if not H.inside(poly, (ax + bx) / 2 + nx * 0.3, (az + bz) / 2 + nz * 0.3):
            nx, nz = uz, -ux
        u, n = Vector((ux, -uz, 0)), Vector((nx, -nz, 0))
        k = W / 2 + 0.15
        while k <= elen - W / 2 - 0.15 + 1e-6:
            s0 = Vector((ax + ux * k, -(az + uz * k), sy + centre_h)) + n * 0.6
            k += 0.1
            dw = S.wall_behind(scene, dg, s0, n, 1.0)
            if dw is None:
                continue
            c = s0 - n * dw
            # one statement a wall: not beside a print or another piece (0.8 m clear on the same wall, 0.4 m round a corner)
            if any(abs(n.dot(ac - c)) < 0.15 and abs(u.dot(ac - c)) - aw - W / 2 < 0.8 or (ac - c).length < aw + W / 2 + 0.4
                   for ac, aw in hung):
                continue
            ds = [S.wall_behind(scene, dg, c + n * 0.2 + u * a + Vector((0, 0, h)), n, 0.3)
                  for a in (-W / 2 - 0.25, -W / 4, 0.0, W / 4, W / 2 + 0.25) for h in (-Hh / 2 - 0.08, 0.0, Hh / 2 + 0.08)]
            if any(d is None or abs(d - 0.2) > 0.02 for d in ds):
                continue
            facing = n.dot((cpos - c).normalized())
            if facing < 0.35:
                continue
            corners = [c + n * 0.03 + u * a + Vector((0, 0, h)) for a in (-W / 2, W / 2) for h in (-Hh / 2, Hh / 2)]
            views = [S.in_view(scene, cam, q, 0.02) for q in corners]
            if not all(ok for ok, _ in views) or min(v.z for _, v in views) < 1.2:
                continue
            if sum(S.visible(scene, dg, cam, q, 0.06) for q in corners) < 3:
                continue
            xs, ys = [v.x for _, v in views], [v.y for _, v in views]
            score = (max(xs) - min(xs)) * (max(ys) - min(ys)) * facing
            if best is None or score > best[0]:
                best = (score, c.copy(), n.copy())
    if best is None:
        return False
    _, c, n = best
    base = c + n * 0.012 - Vector((0, 0, Hh / 2))
    put(scene, room, e, at_b(base), turn_deg=math.degrees(math.atan2(n.x, -n.y)))
    return True


def wall(scene, cam, room, sy):
    """A mirror or a clock on a plain wall the camera faces, by room use, if the room's picture left one."""
    use = room["use"]
    plan = {"hall": [("mirror", 1.5)], "bedroom": [("mirror", 1.5), ("clock", 1.9)], "living": [("mirror", 1.5), ("clock", 1.9)],
            "dining": [("clock", 1.9), ("mirror", 1.5)], "kitchen": [("clock", 1.95)], "office": [("clock", 1.9)]}.get(use)
    if not plan:
        return 0
    role, h = objlib.choose(room, "wall", plan + [(None, 0)] if use in ("bedroom", "living") else plan)
    if role is None:
        return 0
    e = objlib.pick(room, role, fit=within(1.0, 0.2, 1.1))
    if e is None or sz(e)[1] > 0.2:
        return 0
    done = wall_piece(scene, cam, room, sy, e, h)
    log(f"wall of {room['name']}: {role} {e['id']}: {'placed' if done else 'no plain wall'}")
    return int(done)


# ---------------------------------------------------------------- a desk


def desk(scene, cam, room, sy, pieces):
    """A desk the camera sees: books, a notebook, supplies, a small plant, where its top is free."""
    n = 0
    for o in pieces:
        nm = H.name(o).lower()
        if "desk" not in nm or "lamp" in nm or len(o.get("footprint", ())) != 2 or not S.in_room(o, room):
            continue
        if not S.shown(scene, cam, o):
            continue
        lo, hi = H.box(o)
        dg = bpy.context.evaluated_depsgraph_get()
        f = H.frame_of(o, lo.z)
        w, d = o["footprint"]
        spots = S.top_spots(scene, dg, o, lo.z, 0.11)
        if not spots:
            continue
        spots.sort(key=lambda s: s[0])
        what = objlib.choose(room, "desk", [("books+small", "desk"), ("desk", "vase"), ("books", "pot")])
        ends = (spots[0], spots[-1])
        for (u, v, h), thing in zip(ends, what):
            p = f @ Vector((u, v, h + 0.001))
            if thing == "desk":
                e = objlib.pick(room, "desk", fit=within(0.5, 0.4, 0.2))
                if e:
                    put(scene, room, e, at_b(p), turn_deg=math.degrees(H.turn(o)) + 8)
            elif thing == "pot":
                small_on(scene, room, p, 0.09, "desk")
            elif thing == "vase":
                vase_with(scene, room, at_b(p), 0.26, 0.09, stems=True)
            else:
                arrange(scene, room, thing, p, H.turn(o), 5)
        log(f"{nm}: {what}")
        n += 1
    return n


# ---------------------------------------------------------------- what a frame shows


def label(o):
    """What an object of the scene belongs to, for counting: a library id, a kit piece's name, a placed model's file;
    None for the building."""
    top = o
    while top is not None:
        if top.get("obj_id"):
            return top["obj_id"]
        if top.get("art_id"):
            return top["art_id"]
        if top.get("kind") == "furniture":
            return str(top.get("name") or top.name)
        if top.parent is None:
            break
        top = top.parent
    if top.type == "EMPTY" and top.get("kind") is None:
        return top.name.split(".")[0]
    return None


def report(scene, cam, nx=128, ny=72):
    """The objects the frame shows and how much of it each takes: `PHOTO seen: <percent> <label>`."""
    dg = bpy.context.evaluated_depsgraph_get()
    mw = cam.matrix_world
    o = mw.translation.copy()
    f = cam.data.view_frame(scene=scene)  # top right, bottom right, bottom left, top left, in the camera's space
    tr, br, bl, tl = f[0], f[1], f[2], f[3]
    count = {}
    for j in range(ny):
        v = (j + 0.5) / ny
        for i in range(nx):
            u = (i + 0.5) / nx
            p = tl.lerp(tr, u).lerp(bl.lerp(br, u), v)
            hit = H.trace(scene, dg, o, ((mw @ p) - o).normalized())
            if hit is None:
                continue
            k = label(hit[2])
            if k:
                count[k] = count.get(k, 0) + 1
    for k, c in sorted(count.items(), key=lambda t: -t[1]):
        print(f"PHOTO seen: {100 * c / (nx * ny):.2f} {k}", flush=True)
