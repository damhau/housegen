"""Styling for photo.py (#84): props where a stager would put them, by rules on what the camera sees. Nothing here
knows a room or a camera: it reads the export's pieces, the rooms the camera sees the floor of, and the frame.

- `floor_spots`: free floor the camera sees in a room, along a wall, clear of door swings and passages, scored by
  where the piece would land in the frame (the sides, not the middle; not cut; not too close).
- `top_spots`: free, flat places on a piece's top (a table, a nightstand): rays down a disc.
- the rules, by piece: a bed gets a rug under its lower two-thirds, a pair of prints over it, and its nightstands a
  lamp at the back, books and a small plant, a carafe and a glass; a dining table napkins on its plates, candles, a
  carafe and a bowl of fruit; a bathroom a towel on a bar on a plain wall it faces, and a stool with towels; the
  living room or kitchen the camera sees most a sideboard against a plain wall, styled; each room a floor accent (a
  tall plant in a corner, else branches in a floor vase); deep window sills a bud vase; a balcony or terrace seen
  through the glass a bistro set.
- `edge_clutter`, last: small props cut by a side of the frame, and a plant right in front of the lens, go.

photo.py calls `style(scene, cam, rays, rooms, sy, pieces)` after the camera is set, and passes itself as `H` (its
helpers: trace, kinds, box, frame_of, in_frame, place, model...).
"""

import math
import os
import subprocess
import sys

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Matrix, Vector

H = None  # photo.py's module, set by photo.main
HERE = os.path.dirname(os.path.abspath(__file__))
SKIP = set(os.environ.get("STYLE_SKIP", "").split(","))  # rules left out, for experiments: rug,bedside,bedart,table,
# stool,rail,sideboard,accent,sill,outdoor,edge


def log(*a):
    H.log("style:", *a)


def prop(what, arg=None):
    """A made piece (made/bathset.py), made once and cached."""
    path = os.path.join(H.CACHE, f"prop-{what}{'' if arg is None else f'-{arg}'}.glb")
    if not os.path.exists(path):
        args = [sys.executable, os.path.join(HERE, "made", "bathset.py"), what, path]
        subprocess.run(args + ([] if arg is None else [str(arg)]), check=True)
    return path


def put(scene, path, at, turn_rad=0.0, scale=1.0):
    """A made piece standing at Blender point `at`, turned about z."""
    f = Matrix.Translation(at) @ Matrix.Rotation(turn_rad, 4, "Z")
    return H.in_frame(scene, f, path, (0, 0, 0), (scale, scale, scale))


# ---------------------------------------------------------------- what the camera sees

def seen_rooms(rays, rooms, sy):
    """The storey's rooms whose floor the camera sees, with their number of floor samples, most first."""
    plans = [r for r in rooms if abs(r["y"] - sy) < 0.1]
    count = {}
    for r in rays:
        if not r["interior"] or r["hit"] is None:
            continue
        loc, n, _, _ = r["hit"]
        if n.z < 0.9 or abs(loc.z - sy) > 0.1:
            continue
        x, _, z = H.to_scene(loc)
        for i, rm in enumerate(plans):
            if H.inside(rm["polygon"], x, z):
                count[i] = count.get(i, 0) + 1
                break
    return sorted(((plans[i], c) for i, c in count.items()), key=lambda t: -t[1])


def seen_any(rays, rooms, sy):
    """The storey's rooms by the number of interior samples of any surface in them (floor, walls, a tub), most
    first: a bathroom seen from its door shows little floor."""
    plans = [r for r in rooms if abs(r["y"] - sy) < 0.1]
    count = {}
    for r in rays:
        if not r["interior"] or r["hit"] is None:
            continue
        loc, n, _, _ = r["hit"]
        if not sy - 0.1 <= loc.z <= sy + 2.4:
            continue
        x, _, z = H.to_scene(loc + n * 0.05)  # off the surface, into the room it faces
        for i, rm in enumerate(plans):
            if H.inside(rm["polygon"], x, z):
                count[i] = count.get(i, 0) + 1
                break
    return sorted(((plans[i], c) for i, c in count.items()), key=lambda t: -t[1])


def in_view(scene, cam, p, margin=0.0):
    v = world_to_camera_view(scene, cam, p)
    return v.z > 0.3 and margin <= v.x <= 1 - margin and margin <= v.y <= 1 - margin, v


def visible(scene, dg, cam, p, slack=0.08):
    """Nothing stands between the camera and point p."""
    o = cam.matrix_world.translation
    d = p - o
    hit = H.trace(scene, dg, o, d.normalized(), far=d.length)
    return hit is None or (hit[0] - o).length >= d.length - slack


def in_room(o, room):
    lo, hi = H.box(o)
    x, _, z = H.to_scene((lo + hi) / 2)
    return H.inside(room["polygon"], x, z)


# ---------------------------------------------------------------- free places

def seg_dist(p, a, b):
    ax, az = a
    bx, bz = b
    dx, dz = bx - ax, bz - az
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - az) * dz) / max(1e-9, dx * dx + dz * dz)))
    return math.hypot(p[0] - ax - t * dx, p[1] - az - t * dz), (dx, dz)


def doors_of(scene, sy):
    """Door swings and openings on the storey: their boxes (Blender), grown by a passage."""
    out = []
    for o in scene.objects:
        if o.get("kind") in ("door", "interiorDoor"):
            lo, hi = H.box(o)
            if sy - 0.2 <= lo.z <= sy + 0.3:
                out.append((lo, hi))
    return out


def floor_free(scene, dg, x, y, sy, r):
    """A disc of radius r on the bare floor (no rug), nothing above it up to 2 m."""
    for k in range(9):
        dx, dy = (0.0, 0.0) if k == 0 else (r * math.cos(k * math.pi / 4), r * math.sin(k * math.pi / 4))
        hit = H.trace(scene, dg, Vector((x + dx, y + dy, sy + 2.0)), Vector((0, 0, -1)), far=2.2)
        if hit is None or hit[0].z > sy + 0.04 or "roomFloor" not in H.kinds(hit[2]):  # the floor itself, not a rug
            return False
    return True


def passage(scene, dg, x, y, sy, away, r):
    """Free distance from the piece's edge toward the room (away from its wall), at knee height."""
    o = Vector((x, y, sy + 0.35)) + away * r
    hit = H.trace(scene, dg, o, away, far=3.0)
    return 3.0 if hit is None else (hit[0] - o).length


def before_glass(scene, dg, x, y, sy, away, d1, h):
    """The spot stands in front of a window or a glazed door, or an opening: a ray toward its wall at some height
    goes through (trace passes clear glass) instead of meeting the wall."""
    for hh in (0.3, 0.55 * h, min(h, 1.8)):
        o = Vector((x, y, sy + hh))
        hit = H.trace(scene, dg, o, -away, far=d1 + 0.6)
        if hit is None or (hit[0] - o).length > d1 + 0.35:
            return True
    return False


def shown(scene, cam, o):
    """Some of piece o's box is in frame."""
    lo, hi = H.box(o)
    pts = [Vector((a, b, c)) for a in (lo.x, hi.x) for b in (lo.y, hi.y) for c in (lo.z, hi.z)] + [(lo + hi) / 2]
    return any(in_view(scene, cam, p, 0.0)[0] for p in pts)


def floor_spots(scene, cam, room, sy, r, h, taken=(), step=0.1):
    """Free floor spots for a piece of radius r and height h in `room` that the camera sees, best first:
    (score, (x, y) Blender, corner, frame x)."""
    dg = bpy.context.evaluated_depsgraph_get()
    poly = [tuple(p) for p in room["polygon"]]
    edges = list(zip(poly, poly[1:] + poly[:1]))
    doors = doors_of(scene, sy)
    xs, zs = [p[0] for p in poly], [p[1] for p in poly]
    cpos = cam.matrix_world.translation
    out, why = [], {}
    nx, nz = int((max(xs) - min(xs)) / step), int((max(zs) - min(zs)) / step)
    for i in range(nx + 1):
        for j in range(nz + 1):
            x, z = min(xs) + i * step, min(zs) + j * step
            if not H.inside(poly, x, z):
                why["poly"] = why.get("poly", 0) + 1
                continue
            ds = sorted((*seg_dist((x, z), a, b), a) for a, b in edges)
            d1, (ex, ez), wa = ds[0]
            if not r + 0.03 <= d1 <= r + 0.25:  # against a wall, not in the middle of the room
                why["wall"] = why.get("wall", 0) + 1
                continue
            corner = any(d <= r + 0.35 and abs(ex * vz - ez * vx) > 0.5 * math.hypot(ex, ez) * math.hypot(vx, vz)
                         for d, (vx, vz), _ in ds[1:])
            y = -z
            if any(lo.x - r - 0.25 <= x <= hi.x + r + 0.25 and lo.y - r - 0.25 <= y <= hi.y + r + 0.25 for lo, hi in doors):
                why["door"] = why.get("door", 0) + 1
                continue
            if any(math.hypot(x - tx, y - ty) < tr + r + 0.6 for tx, ty, tr in taken):
                why["taken"] = why.get("taken", 0) + 1
                continue
            ok, base = in_view(scene, cam, Vector((x, y, sy)), 0.04)
            okt, top = in_view(scene, cam, Vector((x, y, sy + h)), 0.0)
            if not ok or base.z < 1.4:  # in frame, and not right in front of the lens
                why["frame"] = why.get("frame", 0) + 1
                continue
            if not floor_free(scene, dg, x, y, sy, r):
                why["floor"] = why.get("floor", 0) + 1
                continue
            # away from the nearest wall, into the room: from the wall's nearest point to the spot
            t = max(0.0, min(1.0, ((x - wa[0]) * ex + (z - wa[1]) * ez) / max(1e-9, ex * ex + ez * ez)))
            n = Vector((x - wa[0] - t * ex, -(z - wa[1] - t * ez), 0)).normalized()
            if before_glass(scene, dg, x, y, sy, n, d1, h):
                why["glass"] = why.get("glass", 0) + 1
                continue
            if passage(scene, dg, x, y, sy, n, r) < 0.6 and not corner:
                why["passage"] = why.get("passage", 0) + 1
                continue
            if not visible(scene, dg, cam, Vector((x, y, sy + h * 0.5)), slack=r + 0.05):
                why["hidden"] = why.get("hidden", 0) + 1
                continue
            dist = (Vector((x, y, sy)) - cpos).length
            score = 0.0
            score += 1.0 if corner else 0.0
            score += 1.0 if base.x < 0.32 or base.x > 0.68 else 0.0  # the sides of the frame, not its middle
            score += 0.5 if okt else 0.0  # its top in frame too
            score += 0.5 if 2.0 <= dist <= 5.5 else 0.0
            score -= 0.3 * abs(base.x - 0.5)  # but not hugging the frame's edge
            out.append((score, (x, y), corner, base.x))
    log(f"floor spots r={r} in {room['name']}: {len(out)} kept, rejected {why}")
    return sorted(out, key=lambda t: -t[0])


def top_spots(scene, dg, o, floor_z, r, us=None, vs=None):
    """Free flat places for a disc of radius r on piece o's top, in its frame: (u, v, height), the highest level
    first. Every ray down a disc must meet the same flat level."""
    w, d = o["footprint"]
    f = H.frame_of(o, floor_z)
    if us is None:
        us = [-w / 2 + r + k * (w - 2 * r) / 8 for k in range(9)] if w > 2 * r else [0.0]
    if vs is None:
        vs = [-d / 2 + r + k * (d - 2 * r) / 6 for k in range(7)] if d > 2 * r else [0.0]
    out = []
    for u in us:
        for v in vs:
            ring = [(u, v)] + [(u + rr * math.cos(k * math.pi / 6), v + rr * math.sin(k * math.pi / 6))
                               for rr in (r * 0.5, r) for k in range(12)]
            hs = H.heights_down(scene, dg, f, ring, 2.0)
            if any(h is None or nz < 0.95 for h, nz in hs):
                continue
            if max(h for h, _ in hs) - min(h for h, _ in hs) > 0.006:
                continue
            out.append((u, v, hs[0][0]))
    return out


# ---------------------------------------------------------------- the rules

def our_rugs(scene):
    return [o for o in scene.objects if o.name.startswith("rug") and o.type == "MESH" and o.data.materials
            and o.data.materials[0].name.startswith("archviz-rug")]


def bed_rug(scene, room, sy):
    """A rug under the lower two-thirds of the bed, 0.6 m proud at the sides and 0.7 m at the foot, shrunk to the
    room; the room's other rugs go."""
    beds = [(f, w) for f, w in H.BEDS if H.inside(room["polygon"], *H.to_scene(f.translation)[::2])]
    if not beds:
        return 0
    f, w = beds[0]
    length = 2.0  # our bed, its head at the frame's origin, its foot at -2.0 along y
    poly = room["polygon"]
    cover = 0.65 * length
    for k in range(9):  # shrink the margins until the rug fits the room
        side, foot = 0.6 - 0.05 * k, 0.7 - 0.05 * k
        rw, rl = w + 2 * side, cover + foot
        corners = [f @ Vector((sx * rw / 2, -(length - cover) - e, 0)) for sx in (-1, 1) for e in (0.0, rl)]
        if all(H.inside(poly, *H.to_scene(p)[::2]) for p in corners):
            break
    else:
        log("no room for a rug under the bed")
        return 0
    for o in our_rugs(scene):
        if H.inside(poly, *H.to_scene(o.matrix_world.translation)[::2]):
            o.hide_render = True
    centre = f @ Vector((0, -(length - cover) - rl / 2, 0))
    x, _, z = H.to_scene(centre)
    rug = H.dress.rug(scene, (x, z), (rw, rl), floor=sy)
    rug.rotation_euler[2] = f.to_euler().z
    log(f"rug {rw:.2f} x {rl:.2f} under the bed")
    return 1


def bed_art(scene, room, sy):
    """The print over a bed becomes a pair, together about 0.7 of the bed's width, at the same height, if the wall
    behind takes them (no window, door or piece in the way)."""
    dg = bpy.context.evaluated_depsgraph_get()
    n = 0
    for f, w in H.BEDS:
        if not H.inside(room["polygon"], *H.to_scene(f.translation)[::2]):
            continue
        fi = f.inverted()
        for o in [o for o in scene.objects if o.name.startswith("art.glb") and o.parent is None and not o.hide_render]:
            lo, hi = H.box(o)
            c = (lo + hi) / 2
            loc = fi @ c
            if abs(loc.x) > w / 2 or not -0.4 < loc.y < 0.6 or not 0.6 < loc.z < 2.4:  # on the wall behind the head
                continue
            into = (f.to_3x3() @ Vector((0, -1, 0))).normalized()  # off the wall, into the room
            side = (f.to_3x3() @ Vector((1, 0, 0))).normalized()
            mid = f @ Vector((0, loc.y, loc.z))
            if os.environ.get("STYLE_DEBUG"):
                log("bed art at", tuple(round(v, 2) for v in c), "bed origin", tuple(round(v, 2) for v in f.translation),
                    "into", tuple(round(v, 2) for v in into), "mid", tuple(round(v, 2) for v in mid))
            H.hide(o)
            for sc in (min(1.0, (0.72 * w - 0.06) / 2 / 0.62), 0.8, 0.7):
                pw, ph = 0.62 * sc, 0.82 * sc
                ok = True
                for e in (-1, 1):
                    for dx in (pw * 0.55, pw + 0.06):
                        for dz in (-ph / 2 + 0.05, ph / 2 - 0.05):
                            q = mid + side * (e * dx) + Vector((0, 0, dz)) + into * 0.4
                            hit = H.trace(scene, dg, q, -into, far=0.6)
                            # the wall's plane (or a finish laid on it), facing the room; nothing in front
                            if hit is None or not 0.32 < (hit[0] - q).length < 0.5 or hit[1].dot(into) < 0.9 \
                                    or H.kinds(hit[2]) & {"window", "door", "interiorDoor", "furniture"}:
                                if os.environ.get("STYLE_DEBUG"):
                                    log("bed art miss", o.name, tuple(round(v, 2) for v in loc), round(sc, 2), e, round(dx, 2),
                                        round(dz, 2), None if hit is None else (round((hit[0] - q).length, 3),
                                                                                  round(hit[1].dot(into), 2), hit[2].name,
                                                                                  sorted(k for k in H.kinds(hit[2]) if k)))
                                ok = False
                if ok:
                    break
            else:
                o.hide_render = False
                for c_ in o.children_recursive:
                    c_.hide_render = False
                log("bed art: the wall doesn't take a pair")
                continue
            turn = math.degrees(o.rotation_euler.z)
            for e, path in ((-1, H.ART), (1, H.ART2)):
                at = mid + side * (e * (pw / 2 + 0.03))
                H.place(scene, path, (at.x, c.z - ph / 2, -at.y), height=ph, turn_deg=turn)
            log(f"bed art: a pair of {pw:.2f} x {ph:.2f} prints over the bed ({w} m)")
            n += 1
            break
    return n


def bedsides(scene, cam, room, sy, pieces):
    """Each nightstand the camera sees: its lamp to the back corner away from the bed, a stack of books at the
    front on the bed's side, a small plant on the books."""
    beds = [f for f, _ in H.BEDS]
    n = 0
    for o in pieces:
        nm = H.name(o).lower()
        if "nightstand" not in nm and "bedside" not in nm or len(o.get("footprint", ())) != 2 or not in_room(o, room):
            continue
        lo, hi = H.box(o)
        ok, _ = in_view(scene, cam, (lo + hi) / 2, 0.0)
        if not ok or not beds:
            continue
        w, d = o["footprint"]
        f = H.frame_of(o, lo.z)
        bed = min(beds, key=lambda b: (b.translation - f.translation).length)
        s = 1.0 if (f.inverted() @ bed.translation).x > 0 else -1.0  # the bed's side of the nightstand, along u
        top = hi.z
        for lamp in pieces:  # the lamps standing on it
            llo, lhi = H.box(lamp)
            t = lamp.matrix_world.translation
            if lamp is o or abs(llo.z - top) > 0.03 or not (lo.x <= t.x <= hi.x and lo.y <= t.y <= hi.y):
                continue
            lr = min(0.5 * min(lhi.x - llo.x, lhi.y - llo.y), 0.4 * min(w, d))
            at = f @ Vector((-s * (w / 2 - lr * 0.55), d / 2 - lr * 0.55, top - lo.z))
            lamp.matrix_world.translation = Vector((at.x, at.y, lamp.matrix_world.translation.z))
            log(f"lamp {H.name(lamp)} to the back corner of {nm}")
        bpy.context.view_layer.update()
        scale = min(1.0, (w * 0.62) / 0.26, (d * 0.55) / 0.19)
        books = put(scene, prop("books", n + 1), f @ Vector((s * (w / 2 - 0.13 * scale - 0.015), -(d / 2 - 0.095 * scale - 0.015),
                                                              top - lo.z + 0.001)),
                    H.turn(o) + math.radians(-8 * s), scale)
        bpy.context.view_layer.update()
        blo, bhi = H.box(books)
        c = (blo + bhi) / 2
        H.place(scene, H.model("potted_plant_04"), (c.x + 0.02 * s, bhi.z, -c.y), height=0.15, turn_deg=30)
        # a carafe and its glass at the front corner away from the bed, the glass toward the middle; only where
        # there is room for them beside the books and in front of the lamp (on a small one they crowd the lamp)
        carafe = w >= 0.45 and d >= 0.36
        if carafe:
            put(scene, prop("carafe"), f @ Vector((-s * (w / 2 - 0.06), -(d / 2 - 0.06), top - lo.z + 0.001)),
                H.turn(o) + (0.0 if s > 0 else math.pi) + math.radians(12))
        log(f"{nm} ({w:.2f} x {d:.2f}): books ({scale:.2f}) and a succulent on the bed's side"
            + (", a carafe on the other" if carafe else ""))
        n += 1
    return n


RUNNER = (0.22, 0.17, 0.11)  # flax linen (linear: about sRGB 0.50 0.44 0.36)


def table_extras(scene, cam, room, sy, pieces):
    """The dining table: a napkin on each plate, a candle tray and a carafe on free spots of its centre line."""
    dg = bpy.context.evaluated_depsgraph_get()
    n = 0
    for o in pieces:
        nm = H.name(o).lower()
        if "dining-table" not in nm and "dining table" not in nm or not in_room(o, room):
            continue
        lo, hi = H.box(o)
        w, d = o["footprint"]
        f = H.frame_of(o, lo.z)
        along = Vector((0, 1, 0)) if d >= w else Vector((1, 0, 0))  # the long axis, in its frame
        across = math.pi / 2 if d >= w else 0.0
        plates = 0
        for p in pieces:
            if H.name(p) != "plate":
                continue
            plo, phi = H.box(p)
            t = p.matrix_world.translation
            if not (lo.x <= t.x <= hi.x and lo.y <= t.y <= hi.y):
                continue
            rot = H.turn(o) + across + math.radians(((plates * 37) % 11) - 5)  # its long side across the table
            put(scene, prop("napkin"), Vector((t.x, t.y, phi.z + 0.0005)), rot)
            plates += 1
        glass = None
        for g in pieces:  # the export's wine glasses are an opaque white "glass": clear glass, as on the bath pieces
            t = g.matrix_world.translation
            if "glass" not in H.name(g) or not (lo.x <= t.x <= hi.x and lo.y <= t.y <= hi.y):
                continue
            glass = glass or H.clear_glass(name="archviz-glass-table")
            for c in [g, *g.children_recursive]:
                for sl in c.material_slots if c.type == "MESH" else []:
                    if sl.material is not None and sl.material.name.startswith("glass"):
                        sl.material = glass
        for m in bpy.data.materials:  # the runner in flax linen, not near-white on a white top
            if m.name.startswith("archviz-runner"):
                q = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
                q.inputs["Base Color"].default_value = (*RUNNER, 1)
                q.inputs["Sheen Weight"].default_value = 0.12  # a white sheen at the camera's grazing angle reads grey
                q.inputs["Sheen Tint"].default_value = (*RUNNER, 1)
        length = max(w, d)
        free = []
        for k in range(-8, 9):
            a = along * (k * length / 18)
            spot = top_spots(scene, dg, o, lo.z, 0.125, [a.x], [a.y])
            if spot:
                free.append(spot[0])
        placed, names = [], []
        # candles and a carafe about a quarter from each end, then a bowl of fruit as near the middle as is free
        for what, r, gap, at in (("candles", 0.125, 0.25, 0.27), ("carafe1", 0.05, 0.25, 0.27), ("bowl", 0.125, 0.05, 0.0)):
            best = None
            for u, v, h in free:
                if any(math.hypot(u - pu, v - pv) < r + pr + gap for pu, pv, pr in placed):
                    continue
                key = abs(abs(along.dot(Vector((u, v, 0)))) - length * at)
                if best is None or key < best[0]:
                    best = (key, u, v, h)
            if best:
                _, u, v, h = best
                if what == "bowl":
                    fruit_bowl(scene, f @ Vector((u, v, h + 0.001)))
                else:
                    put(scene, prop(what), f @ Vector((u, v, h + 0.001)), H.turn(o) + 0.4)
                placed.append((u, v, r))
                names.append(what)
        log(f"{nm}: {plates} napkins, {names} on the centre line (free spots along it: "
            f"{[round(along.dot(Vector((u, v, 0))), 2) for u, v, _ in free]})")
        n += 1
    return n


def stool(scene, cam, room, sy, taken):
    """A bathroom: an oak stool with folded towels against a wall, where the camera sees it."""
    spots = floor_spots(scene, cam, room, sy, 0.2, 0.6, taken)
    if not spots:
        log(f"no free floor for a stool in {room['name']}")
        return None
    score, (x, y), corner, fx = spots[0]
    put(scene, prop("stooltowels"), Vector((x, y, sy)), math.radians(17))
    log(f"stool with towels at {H.to_scene(Vector((x, y, sy)))} (frame x {fx:.2f}, score {score:.1f})")
    return (x, y, 0.2)


RAIL_H = 1.2  # a towel bar's height over the floor


def towel_rail(scene, cam, room, sy):
    """A bathroom: a towel on a bar on a plain wall the camera faces (tiles are fine; over the tub is fine): the
    wall plain over the towel's height, nothing in front of it, the towel whole in frame and seen."""
    dg = bpy.context.evaluated_depsgraph_get()
    poly = [tuple(p) for p in room["polygon"]]
    cpos = cam.matrix_world.translation
    best, why = None, {}

    def no(k):
        why[k] = why.get(k, 0) + 1

    for (ax, az), (bx, bz) in zip(poly, poly[1:] + poly[:1]):
        elen = math.hypot(bx - ax, bz - az)
        if elen < 0.7:
            no("short")
            continue
        ux, uz = (bx - ax) / elen, (bz - az) / elen
        nx, nz = -uz, ux
        if not H.inside(poly, (ax + bx) / 2 + nx * 0.3, (az + bz) / 2 + nz * 0.3):
            nx, nz = uz, -ux
        u, n = Vector((ux, -uz, 0)), Vector((nx, -nz, 0))  # along the wall, into the room (Blender)
        k = 0.35
        while k <= elen - 0.35 + 1e-6:
            s0 = Vector((ax + ux * k, -(az + uz * k), sy + RAIL_H)) + n * 0.6
            k += 0.1
            dw = wall_behind(scene, dg, s0, n, 1.0)
            if dw is None:
                no("wall")
                continue
            c = s0 - n * dw  # on the wall's face, at the bar's height
            ds = [wall_behind(scene, dg, c + n * 0.2 + u * a + Vector((0, 0, h)), n, 0.3)
                  for a in (-0.3, -0.15, 0.0, 0.15, 0.3) for h in (-0.45, -0.2, 0.0, 0.08)]
            if any(d is None or abs(d - 0.2) > 0.02 for d in ds):  # one flat, plain face (not a mirror proud of it)
                no("behind")
                continue
            if any(H.trace(scene, dg, c + n * b + u * a + Vector((0, 0, 0.06)), Vector((0, 0, -1)), far=0.55)
                   for a in (-0.3, 0.0, 0.3) for b in (0.03, 0.1)):
                no("occupied")
                continue
            facing = n.dot((cpos - c).normalized())
            if facing < 0.35:  # its face toward the camera, not edge-on
                no("edge-on")
                continue
            corners = [c + n * 0.06 + u * a + Vector((0, 0, h)) for a in (-0.3, 0.3) for h in (0.04, -0.42)]
            views = [in_view(scene, cam, q, 0.02) for q in corners]
            if not all(ok for ok, _ in views) or min(v.z for _, v in views) < 0.9:
                no("frame")
                continue
            seen = sum(visible(scene, dg, cam, c + n * 0.08 + u * a + Vector((0, 0, -0.18)), 0.06)
                       for a in (-0.2, -0.1, 0.0, 0.1, 0.2))
            if seen < 4:
                no("hidden")
                continue
            xs, ys = [v.x for _, v in views], [v.y for _, v in views]
            score = (max(xs) - min(xs)) * (max(ys) - min(ys)) * seen / 5 * facing
            if best is None or score > best[0]:
                best = (score, c.copy(), n.copy(), sum(xs) / 4)
    if best is None:
        log(f"towel rail: no wall in {room['name']} takes one, rejected {why}")
        return None
    score, c, n, fx = best
    put(scene, prop("rail"), c + n * 0.075, math.atan2(n.x, -n.y))
    log(f"towel rail at {tuple(round(v, 2) for v in H.to_scene(c))} (frame x {fx:.2f}, score {score:.3f})")
    return 1


def fruit_bowl(scene, p):
    """A wooden bowl of lemons and a green apple standing at Blender point p (as on the kitchen island)."""
    x, z, y = p.x, p.z, -p.y  # scene: x, up, south
    H.place(scene, H.model("wooden_bowl_01"), (x, z, y), height=0.09)
    for k, (dx, dz, fruit) in enumerate(((-0.05, 0.0, "lemon"), (0.05, 0.03, "lemon"), (0.0, -0.05, "food_apple_01"),
                                         (0.02, 0.06, "lemon"), (-0.01, 0.01, "lemon"))):
        H.place(scene, H.model(fruit), (x + dx, z + 0.035 + (0.03 if k >= 3 else 0), y + dz), height=0.07,
                turn_deg=70 * k)


def accent(scene, cam, room, sy, taken, pieces):
    """A floor accent: a tall plant in a corner; else branches in a floor vase against a wall. Not a second plant
    where the room already has one in view."""
    has_plant = any(o.name.startswith(("pachira", "potted_plant_02")) and o.parent is None and shown(scene, cam, o)
                    for o in scene.objects)
    tall = floor_spots(scene, cam, room, sy, 0.3, 1.5, taken)
    corner = [s for s in tall if s[2]]
    if corner and not has_plant:
        score, (x, y), _, fx = corner[0]
        H.place(scene, H.model("pachira_aquatica_01"), (x, sy, -y), height=1.5, turn_deg=73, tallest=True)
        log(f"plant in a corner at {tuple(round(v, 2) for v in H.to_scene(Vector((x, y, sy))))} (frame x {fx:.2f})")
        return (x, y, 0.3)
    spots = floor_spots(scene, cam, room, sy, 0.16, 1.3, taken)
    if not spots:
        log(f"no free floor for an accent in {room['name']}")
        return None
    score, (x, y), _, fx = spots[0]
    _, size = H.place(scene, H.model("ceramic_vase_01"), (x, sy, -y), height=0.46)
    H.place(scene, H.EUCALYPTUS, (x, sy + size.z * 0.82, -y), height=0.95, turn_deg=40)
    log(f"floor vase with branches at {tuple(round(v, 2) for v in H.to_scene(Vector((x, y, sy))))} (frame x {fx:.2f})")
    return (x, y, 0.16)


def sills(scene, cam, rays, sy):
    """Deep window sills the camera sees (the export's `windowSills`, 0.5-1.6 m up, at least 0.13 m deep): a bud
    vase with a eucalyptus sprig toward the end nearer the frame's edge. At most two."""
    dg = bpy.context.evaluated_depsgraph_get()
    cands = []
    for o in scene.objects:
        if o.type != "MESH" or "windowSills" not in H.kinds(o) or o.hide_render:
            continue
        mw = o.matrix_world
        for f in o.data.polygons:
            nz = (mw.to_3x3() @ f.normal).normalized().z
            if nz < 0.95:
                continue
            vs = [mw @ o.data.vertices[i].co for i in f.vertices]
            z = max(v.z for v in vs)
            if not sy + 0.5 <= z <= sy + 1.6:
                continue
            e = sorted(((vs[i] - vs[i - 1]) for i in range(len(vs))), key=lambda v: -v.length)
            run, depth = e[0].length, min(v.length for v in e if v.length > 1e-4)
            if depth < 0.13 or run < 0.3:
                continue
            a = Vector((e[0].x, e[0].y, 0)).normalized()
            c = sum(vs, Vector()) / len(vs)
            cands.append((c, a, run, depth, z))
    n, used = 0, []
    for c, a, run, depth, z in cands:
        if n >= 2:
            break
        if any((c - u).length < ur / 2 + 0.05 for u, ur in used):  # another face of a sill already dressed
            continue
        best = None
        for t in (0.32, -0.32, 0.18, -0.18, 0.0):  # toward an end of the sill first
            q = c + a * (t * run)
            q.z = z
            ok, v = in_view(scene, cam, q + Vector((0, 0, 0.35)), 0.03)
            okb, _ = in_view(scene, cam, q, 0.03)
            if not ok or not okb or not visible(scene, dg, cam, q + Vector((0, 0, 0.12)), 0.08):
                continue
            free = all((h := H.trace(scene, dg, q + a * da + Vector((0, db, 0.6)), Vector((0, 0, -1)), far=0.7)) is not None
                       and abs(h[0].z - z) < 0.005 for da in (-0.06, 0.06) for db in (-0.04, 0.04))
            if not free:
                continue
            key = abs(v.x - 0.5)
            if best is None or key > best[0]:
                best = (key, q)
        if best is None:
            continue
        q = best[1]
        used.append((c, run))
        x, _, zz = H.to_scene(q)
        _, size = H.place(scene, H.model("ceramic_vase_01"), (x, z, zz), height=0.17)
        H.place(scene, H.EUCALYPTUS, (x, z + size.z * 0.8, zz), height=0.42, turn_deg=110)
        log(f"sill: a bud vase with a sprig at {(round(x, 2), round(z, 2), round(zz, 2))}, sill {run:.2f} x {depth:.2f}")
        n += 1
    if not n:
        log(f"sill: none in view ({len(cands)} deep sills on the storey)")
    return n


SET = ("outdoor_table_chair_set_01", 1.83, 0.78)  # a folding bistro set: its chairs at the ends of its local y


def glass_normal(scene, dg, cam, p):
    """The horizontal normal of the first pane between the camera and point p (the façade it stands behind)."""
    o = cam.matrix_world.translation
    d = (p - o).normalized()
    for _ in range(12):
        ok, loc, n, face, obj, _ = scene.ray_cast(dg, o, d, distance=(p - o).length)
        if not ok:
            return None
        m, _ = H.material_at(obj, face)
        if m is not None and m.name.startswith("archviz-glass"):
            n = Vector((n.x, n.y, 0))
            return (n.normalized() if n.dot(d) < 0 else -n.normalized()), loc
        o = loc + d * 1e-3
    return None


def outdoor_set(scene, cam, rays, sy):
    """A bistro set on the balcony or terrace the camera sees through the glass: along the façade, fully in frame,
    on clear decking, clear of the glazing by a door's swing."""
    dg = bpy.context.evaluated_depsgraph_get()
    cpos = cam.matrix_world.translation
    hits = [r["hit"] for r in rays if not r["interior"] and r["hit"] is not None]
    floor = [h for h in hits if h[1].z > 0.9 and sy - 0.45 <= h[0].z <= sy + 0.2 and (h[0] - cpos).length < 12]
    if len(floor) < 12:
        log(f"outdoor set: {len(floor)} outdoor floor samples, too few")
        return 0
    levels = {}
    for h in floor:
        levels.setdefault(round(h[0].z / 0.05), []).append(h)
    pts = max(levels.values(), key=len)
    level = sorted(h[0].z for h in pts)[len(pts) // 2]
    name, L, W = SET
    best = None
    for h in pts:
        g = glass_normal(scene, dg, cam, h[0])
        if g is None:
            continue
        n, gl = g  # n points from the glass toward the camera (into the room)
        t = Vector((-n.y, n.x, 0))
        out = -n
        base = Vector((h[0].x, h[0].y, level))
        d = (base - gl).dot(out)  # from the glass outward
        for shift in (0.0, 0.25, 0.5):
            c = base + out * shift
            dd = d + shift
            if dd < W / 2 + 0.45:  # clear of the glazing (a door's swing, a step)
                continue
            ok = True
            for u in (-0.5, -0.25, 0.0, 0.25, 0.5):
                for v in (-0.5, 0.0, 0.5):
                    q = c + t * (u * (L + 0.1)) + out * (v * (W + 0.1))
                    hit = H.trace(scene, dg, Vector((q.x, q.y, level + 2.0)), Vector((0, 0, -1)), far=2.4)
                    if hit is None or abs(hit[0].z - level) > 0.05 or hit[1].z < 0.9:
                        ok = False
                        break
                if not ok:
                    break
            if not ok:
                continue
            corners = [c + t * (a * L / 2) + out * (b * W / 2) + Vector((0, 0, z)) for a in (-1, 1) for b in (-1, 1)
                       for z in (0.0, 0.85)]
            views = [in_view(scene, cam, q, 0.02) for q in corners]
            if not all(v[0] for v in views):
                continue
            size = max(v[1].y for v in views) - min(v[1].y for v in views)
            if best is None or size > best[0]:
                best = (size, c, t, dd)
    if best is None:
        log("outdoor set: no clear spot in frame")
        return 0
    size, c, t, dd = best
    turn = math.degrees(math.atan2(t.y, t.x)) - 90.0
    x, y, z = H.to_scene(c)
    H.place(scene, H.model(name), (x, level, z), turn_deg=turn)
    log(f"outdoor set at {(round(x, 2), round(level, 2), round(z, 2))}, {dd:.2f} m out from the glass, "
        f"{size:.2f} of the frame high")
    return 1


CABINET = ("modern_wooden_cabinet", 2.44, 0.52, 0.68)  # walnut, slatted doors, steel legs (CC0); its front faces -y


def root_of(o):
    while o.parent is not None:
        o = o.parent
    return o


def wall_behind(scene, dg, p, n, reach):
    """How far back along -n from p a plain wall stands (facing the room; not a door, a window or a hung print),
    or None if there is none within reach (glass, an opening, something else)."""
    hit = H.trace(scene, dg, p, -n, far=reach + 0.5)
    if hit is None or hit[1].dot(n) < 0.9 or H.kinds(hit[2]) & {"door", "interiorDoor", "window", "furniture"} \
            or root_of(hit[2]).name.startswith("art"):
        return None
    d = (hit[0] - p).length
    return d if d <= reach else None


def sideboard(scene, cam, room, sy, taken):
    """A low sideboard against a plain wall the camera sees (a window above it is fine): its whole volume free,
    clear of doors, a passage in front, its top in frame and not hidden. Then styled: branches in a vase at the end
    toward the frame's edge, books and a succulent at the other, a print leaning on the wall if the wall is plain."""
    dg = bpy.context.evaluated_depsgraph_get()
    name, L, D, Hc = CABINET
    poly = [tuple(p) for p in room["polygon"]]
    doors = doors_of(scene, sy)
    best, why = None, {}

    def no(k):
        why[k] = why.get(k, 0) + 1

    for (ax, az), (bx, bz) in zip(poly, poly[1:] + poly[:1]):
        elen = math.hypot(bx - ax, bz - az)
        if elen < L + 0.1:
            no("short")
            continue
        ux, uz = (bx - ax) / elen, (bz - az) / elen
        nx, nz = -uz, ux
        if not H.inside(poly, (ax + bx) / 2 + nx * 0.3, (az + bz) / 2 + nz * 0.3):
            nx, nz = uz, -ux
        u, n = Vector((ux, -uz, 0)), Vector((nx, -nz, 0))  # along the wall, into the room (Blender)
        k = L / 2 + 0.05
        while k <= elen - L / 2 - 0.05 + 1e-6:
            e = Vector((ax + ux * k, -(az + uz * k), sy))
            k += 0.1
            s0 = e + n * 0.8 + Vector((0, 0, 0.3))
            dw = wall_behind(scene, dg, s0, n, 1.3)
            if dw is None:
                no("wall")
                continue
            c = s0 - n * (dw - D / 2 - 0.02)
            c.z = sy
            if not all(wall_behind(scene, dg, c + u * a + Vector((0, 0, h)), n, D / 2 + 0.1) is not None
                       for a in (-L / 2 + 0.08, -L / 4, 0.0, L / 4, L / 2 - 0.08) for h in (0.15, 0.4, Hc - 0.04)):
                no("behind")
                continue
            free = True
            for a in (-L / 2 + 0.04, -L / 4, 0.0, L / 4, L / 2 - 0.04):
                for b, top in ((-D / 2 + 0.05, Hc + 0.05), (0.0, Hc + 0.55), (D / 2 - 0.02, Hc + 0.55)):
                    q = c + u * a + n * b + Vector((0, 0, top))
                    hit = H.trace(scene, dg, q, Vector((0, 0, -1)), far=top + 0.2)
                    if hit is None or hit[0].z > sy + 0.05:
                        free = False
            if not free:
                no("occupied")
                continue
            cs = [c + u * (a * (L / 2 + 0.6)) + n * (b * (D / 2 + 0.6)) for a in (-1, 1) for b in (-1, 1)]
            lo = Vector((min(q.x for q in cs), min(q.y for q in cs), 0))
            hi = Vector((max(q.x for q in cs), max(q.y for q in cs), 0))
            if any(dlo.x <= hi.x and lo.x <= dhi.x and dlo.y <= hi.y and lo.y <= dhi.y for dlo, dhi in doors):
                no("door")
                continue
            if any(passage(scene, dg, *(c + u * a).xy, sy, n, D / 2) < 0.75 for a in (-L / 2 + 0.1, 0.0, L / 2 - 0.1)):
                no("passage")
                continue
            if any(seg_dist((tx, ty), tuple((c - u * L / 2).xy), tuple((c + u * L / 2).xy))[0] < tr + D / 2 + 0.4
                   for tx, ty, tr in taken):
                no("taken")
                continue
            tops = [c + u * a + Vector((0, 0, Hc + 0.12)) for a in (-L / 2, -L / 4, 0.0, L / 4, L / 2)]
            views = [in_view(scene, cam, q, 0.02) for q in tops]
            inn = [v for ok, v in views if ok]
            if len(inn) < 4 or views[2][1].z < 1.8:
                no("frame")
                continue
            seen = sum(visible(scene, dg, cam, q, 0.1) for q in tops)
            if seen < 3:
                no("hidden")
                continue
            score = (max(v.x for v in inn) - min(v.x for v in inn)) * seen / 5 + (0.3 if len(inn) == 5 else 0.0)
            if best is None or score > best[0]:
                best = (score, c.copy(), u.copy(), n.copy(), views)
    if best is None:
        log(f"sideboard: no wall in {room['name']} takes one, rejected {why}")
        return None
    score, c, u, n, views = best
    x, _, z = H.to_scene(c)
    H.place(scene, H.model(name), (x, sy, z), turn_deg=math.degrees(math.atan2(n.x, -n.y)))
    bpy.context.view_layer.update()
    top = sy + Hc
    # the tall piece at the end toward the frame's nearer edge, the low one at the other
    e = 1.0 if abs(views[4][1].x - 0.5) >= abs(views[0][1].x - 0.5) else -1.0
    vq = c + u * (e * (L / 2 - 0.3)) - n * 0.04
    vx, _, vz = H.to_scene(vq)
    H.place(scene, H.model("potted_plant_02"), (vx, top, vz), height=0.55, turn_deg=20)
    bq = c - u * (e * (L / 2 - 0.36)) + n * 0.06
    jq = c - u * (e * (L / 2 - 0.66)) - n * 0.05
    jx, _, jz = H.to_scene(jq)
    H.place(scene, H.model("ceramic_vase_04"), (jx, top, jz), height=0.3, turn_deg=-35)
    books = put(scene, prop("books", 9), Vector((bq.x, bq.y, top + 0.001)), math.atan2(n.x, -n.y) + math.radians(-12 * e))
    bpy.context.view_layer.update()
    blo, bhi = H.box(books)
    bc = (blo + bhi) / 2
    H.place(scene, H.model("potted_plant_04"), (bc.x, bhi.z, -bc.y), height=0.16, turn_deg=30)
    # a print leaning on the wall, off-centre toward the books, if the wall above is plain
    sc, tilt = 0.78, math.radians(7)
    aq = c - u * (e * 0.12)
    plain = all(wall_behind(scene, dg, aq + u * a + Vector((0, 0, Hc + h)), n, D / 2 + 0.12) is not None
                for a in (-0.3 * sc - 0.05, 0.0, 0.3 * sc + 0.05) for h in (0.12, 0.4 * sc, 0.8 * sc))
    if plain:
        back = c - n * (D / 2 + 0.02) + n * (0.82 * sc * math.sin(tilt) + 0.03) - u * (e * 0.12)
        ax_, _, az_ = H.to_scene(back)
        art, _ = H.place(scene, H.ART2, (ax_, top + 0.002, az_), height=0.82 * sc, turn_deg=math.degrees(math.atan2(-n.x, n.y)))
        art.rotation_euler[0] = tilt
    log(f"sideboard at {(round(x, 2), round(sy, 2), round(z, 2))} facing {tuple(round(v, 2) for v in n.xy)}, score "
        f"{score:.2f}; a plant at the {'right' if e > 0 else 'left'} end, {'a leaning print' if plain else 'no print (window)'}")
    return (c.x, c.y, L / 2)


def edge_clutter(scene, cam, plants):
    """A small prop cut by a side of the frame reads as clutter, a plant right in front of the lens and cut as a
    dark blob: a stager takes both out of the shot. Only our props and swapped plants (no kit piece). Plants first,
    before the rules (so a floor accent may take a tall plant), props last."""
    n = 0
    for o in list(scene.objects):
        if o.parent is not None or o.type != "EMPTY" or o.hide_render or o.get("kind") is not None:
            continue
        lo, hi = H.box(o)
        size = hi - lo
        plant = o.name.startswith(("pachira", "potted_plant_02"))
        if plant != plants or not plant and (max(size.x, size.y) > 0.5 or size.z > 0.7):
            continue
        vs = [world_to_camera_view(scene, cam, Vector((a, b, cc))) for a in (lo.x, hi.x) for b in (lo.y, hi.y)
              for cc in (lo.z, hi.z)]
        if any(v.z <= 0.05 for v in vs):
            continue
        side_out = [v.x < 0 or v.x > 1 for v in vs]
        inside = [0 <= v.x <= 1 and 0 <= v.y <= 1 for v in vs]
        if not any(inside) or not any(side_out):
            continue
        if plant and min(v.z for v in vs) > 2.0:
            continue
        H.hide(o)
        log(f"edge: {o.name} cut by the frame's side{' in front of the lens' if plant else ''}, taken out")
        n += 1
    return n


def style(scene, cam, rays, rooms, sy, pieces):
    """The rules, room by room, for the rooms the camera sees enough floor of."""
    pieces = H.storey_pieces(scene, sy)  # what is still shown (the swaps hid the kit's pieces they replaced)
    seen = seen_rooms(rays, rooms, sy)
    log("rooms seen:", [(r["name"], c) for r, c in seen])
    if "edge" not in SKIP:
        edge_clutter(scene, cam, plants=True)
        bpy.context.view_layer.update()
    done = []
    taken = []
    for i, (room, count) in enumerate(seen):
        if count < 25:
            continue
        use = room["use"]
        if use == "bedroom":
            if "rug" not in SKIP:
                done.append(("rug", bed_rug(scene, room, sy)))
            if "bedside" not in SKIP:
                done.append(("bedside", bedsides(scene, cam, room, sy, pieces)))
            if "bedart" not in SKIP:
                done.append(("bedart", bed_art(scene, room, sy)))
        if "table" not in SKIP:
            done.append(("table", table_extras(scene, cam, room, sy, pieces)))
        bpy.context.view_layer.update()
        if use in ("bath",) and "stool" not in SKIP:
            t = stool(scene, cam, room, sy, taken)
            if t:
                taken.append(t)
        bpy.context.view_layer.update()
        if "sideboard" not in SKIP and i == 0 and use in ("living", "kitchen", "dining"):
            t = sideboard(scene, cam, room, sy, taken)
            done.append(("sideboard", int(bool(t))))
            if t:
                taken.append(t)
            bpy.context.view_layer.update()
        if "accent" not in SKIP and i == 0 and use not in ("wc", "stair", "storage"):  # one, where most floor shows
            t = accent(scene, cam, room, sy, taken, pieces)
            if t:
                taken.append(t)
    most = seen_any(rays, rooms, sy)  # wall rules: the room the camera sees most of, floor or not
    log("rooms seen, any surface:", [(r["name"], c) for r, c in most[:3]])
    if most and most[0][0]["use"] == "bath" and "rail" not in SKIP:
        done.append(("rail", int(bool(towel_rail(scene, cam, most[0][0], sy)))))
        bpy.context.view_layer.update()
    if "sill" not in SKIP:
        done.append(("sill", sills(scene, cam, rays, sy)))
    if "outdoor" not in SKIP:
        done.append(("outdoor", outdoor_set(scene, cam, rays, sy)))
    bpy.context.view_layer.update()
    if "edge" not in SKIP:
        done.append(("edge", edge_clutter(scene, cam, plants=False)))
    log("done:", done, "floor pieces:", len(taken))
