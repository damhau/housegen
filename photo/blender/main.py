"""The listing photo pipeline's entry points, in Blender's Python (bpy 5.0.1).

    python main.py prepare <house.glb> <rooms.json> <out_dir> [--blend] [--context <dir>] [--site <lat>,<lon>]
        <out_dir>/house.blend (the GLB imported; with --blend the first argument is already a .blend, copied) and
        <out_dir>/cameras.json: per room, viewpoints (corners, the long axis both ways, the doorways) scored by ray
        casts on the bare import; `views` holds up to MAX_VIEWS distinct ones, best first. Then the exteriors (#93,
        outside.py): <out_dir>/outside.blend (the house in its surroundings, the project's context.json and files in
        --context) and `exteriors` in cameras.json, viewpoints around the house with a sun at a real date and hour
        (--site: the plot's latitude and longitude).
    python main.py shoot <spec.json>
        spec: {"blend", "rooms", "camera", "out" (.jpg), "width", "height", "samples"[, "sun", "room_lamps",
        "remove", "staging" (furnished | light | empty, staging.py; exteriors are shot as they are)]}; writes the
        photo and <out>.json (the room, the final camera, the sun, the lamps, the pieces in the frame, the device,
        the seconds per phase). With "kind": "exterior", "blend" is outside.blend.
"""

import json
import math
import os
import shutil
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

EYE, FOV = 1.3, 58.0  # 58 deg vertical at 16:9 is an 18 mm lens on full frame
SMALL_EYE, SMALL_FOV = 1.2, 64.0  # agencies shoot bathrooms and small rooms at 16 mm, a little lower
SMALL_AREA = 9.0
MAX_VIEWS = 6
APART_M, APART_DEG = 0.7, 30.0  # two viewpoints closer than this, looking the same way, are one
GRID = (32, 18)
NEAR, NEAR_PENALTY = 1.2, 0.5  # a camera with something closer than this in the middle of its frame
ASPECT = (1920, 1080)


def load_rooms(path):
    rooms = json.load(open(path, encoding="utf-8"))
    return rooms["rooms"] if isinstance(rooms, dict) else rooms


def area(poly):
    return abs(sum(x1 * z2 - x2 * z1 for (x1, z1), (x2, z2) in zip(poly, poly[1:] + poly[:1]))) / 2


def inside(poly, x, z):
    c = False
    for i in range(len(poly)):
        (x1, z1), (x2, z2) = poly[i], poly[i - 1]
        if (z1 > z) != (z2 > z) and x < (x2 - x1) * (z - z1) / (z2 - z1) + x1:
            c = not c
    return c


def centre_of(poly):
    return Vector((sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly)))


def corner_cameras(room):
    """frame_cands.py's cameras: one per convex corner, 0.5 m from both walls, looking past the centre."""
    poly = room["polygon"]
    centre = centre_of(poly)
    out = []
    for k in range(len(poly)):
        p, a, b = Vector(poly[k]), Vector(poly[k - 1]), Vector(poly[(k + 1) % len(poly)])
        bis = (a - p).normalized() + (b - p).normalized()
        if bis.length < 1e-3:
            continue
        inward = bis.normalized()
        c = p + inward * 0.5 * math.sqrt(2)
        if (centre - c).dot(inward) <= 0:  # a reflex corner: its bisector points out of the room
            continue
        out.append(("corner", c, centre + (centre - c) * 0.4))
    return out


def to_wall(poly, origin, d):
    """The distance from `origin` along `d` to the polygon's boundary, or None."""
    best = None
    for i in range(len(poly)):
        a, b = Vector(poly[i - 1]), Vector(poly[i])
        e = b - a
        den = d.x * e.y - d.y * e.x
        if abs(den) < 1e-9:
            continue
        w = a - origin
        t = (w.x * e.y - w.y * e.x) / den
        u = (w.x * d.y - w.y * d.x) / den
        if t > 1e-6 and 0.0 <= u <= 1.0 and (best is None or t < best):
            best = t
    return best


def axis_cameras(room):
    """From near one end of the room's long axis, looking along it: the depth of the room in one frame."""
    poly = room["polygon"]
    centre = centre_of(poly)
    pts = [Vector(p) - centre for p in poly]
    xx, zz, xz = (sum(p.x * p.x for p in pts), sum(p.y * p.y for p in pts), sum(p.x * p.y for p in pts))
    a = 0.5 * math.atan2(2 * xz, xx - zz)
    axis = Vector((math.cos(a), math.sin(a)))
    out = []
    for d in (axis, -axis):
        reach = to_wall(poly, centre, -d)
        if reach is None or reach < 1.2:
            continue
        c = centre - d * (reach - 0.4)
        if inside(poly, c.x, c.y):
            out.append(("axis", c, centre + d))
    return out


def door_cameras(room, doors):
    """In each doorway of the room, a step in, looking across it: where a visitor first sees the room."""
    poly = room["polygon"]
    centre = centre_of(poly)
    out = []
    for x, z, bottom in doors:
        if abs(bottom - room["y"]) > 0.3:
            continue
        p = Vector((x, z))
        best = None
        for i in range(len(poly)):
            a, b = Vector(poly[i - 1]), Vector(poly[i])
            e = b - a
            t = max(0.0, min(1.0, (p - a).dot(e) / max(e.length_squared, 1e-9)))
            foot = a + e * t
            if best is None or (p - foot).length < best[0]:
                best = ((p - foot).length, foot, Vector((-e.y, e.x)).normalized())
        if best is None or best[0] > 0.35:
            continue
        _, foot, n = best
        if not inside(poly, *(foot + n * 0.1)):
            n = -n
        c = foot + n * 0.35
        if inside(poly, c.x, c.y):
            out.append(("door", c, centre + (centre - c) * 0.4))
    return out


def door_positions(scene):
    """(x, z, bottom) in scene metres of every door: interior doors and the house's own."""
    out = []
    for o in scene.objects:
        if o.get("kind") not in ("interiorDoor", "door") or (o.parent and o.parent.get("kind") == o.get("kind")):
            continue
        ps = [o.matrix_world @ Vector(c) for c in o.bound_box] if o.type == "MESH" else []
        for c in o.children_recursive:
            if c.type == "MESH":
                ps += [c.matrix_world @ Vector(v) for v in c.bound_box]
        if ps:
            out.append((sum(p.x for p in ps) / len(ps), -sum(p.y for p in ps) / len(ps), min(p.z for p in ps)))
    return out


def room_cameras(room, doors):
    """Every viewpoint of the room as (kind, camera spec)."""
    small = room["use"] in ("bath", "wc") or area(room["polygon"]) < SMALL_AREA
    eye, fov = (SMALL_EYE, SMALL_FOV) if small else (EYE, FOV)
    y = room["y"]
    return [(kind, f"{c.x:.2f},{y + eye:.2f},{c.y:.2f}>{t.x:.2f},{y + 1.0:.2f},{t.y:.2f}@{fov:.0f}")
            for kind, c, t in corner_cameras(room) + axis_cameras(room) + door_cameras(room, doors)]


def distinct(cands):
    """The best candidates that are really different viewpoints (another place or another direction)."""
    def pose(spec):
        p, t = spec.split(">")
        (px, _, pz), (tx, _, tz) = ([float(v) for v in p.split(",")], [float(v) for v in t.split("@")[0].split(",")])
        return Vector((px, pz)), math.degrees(math.atan2(tz - pz, tx - px))

    kept = []
    for c in cands:
        if c["score"] <= 0 or len(kept) == MAX_VIEWS:
            continue
        p, yaw = pose(c["camera"])
        if all((p - q).length > APART_M or abs((yaw - w + 180) % 360 - 180) > APART_DEG
               for q, w in (pose(k["camera"]) for k in kept)):
            kept.append(c)
    return kept


def score_camera(scene, dg, spec, room):
    """(floor, ahead): the share of a ray grid through the frame that lands on this room's own floor, and the
    nearest hit in the middle third of the frame (metres)."""
    import photo
    import still

    still.camera(scene, spec, *ASPECT)
    cam = scene.camera
    bpy.context.view_layer.update()
    corners = cam.data.view_frame(scene=scene)
    x0, x1 = min(c.x for c in corners), max(c.x for c in corners)
    y0, y1 = min(c.y for c in corners), max(c.y for c in corners)
    zc = corners[0].z
    rot, origin = cam.matrix_world.to_3x3(), cam.matrix_world.translation.copy()
    nx, ny = GRID
    floor, ahead = 0, math.inf
    for j in range(ny):
        for i in range(nx):
            fx, fy = (i + 0.5) / nx, (j + 0.5) / ny
            d = (rot @ Vector((x0 + fx * (x1 - x0), y1 - fy * (y1 - y0), zc))).normalized()
            hit = photo.trace(scene, dg, origin, d)
            if hit is None:
                continue
            loc, normal = hit[0], hit[1]
            x, y, z = photo.to_scene(loc)
            if normal.z > 0.7 and abs(y - room["y"]) < 0.1 and inside(room["polygon"], x, z):
                floor += 1
            if 1 / 3 <= fx <= 2 / 3 and 1 / 3 <= fy <= 2 / 3:
                ahead = min(ahead, (loc - origin).length)
    bpy.data.objects.remove(cam)
    return floor / (nx * ny), ahead


def prepare(src, rooms_json, out_dir, from_blend=False, context=None, site=None):
    """Import the house, save it as house.blend and write cameras.json next to it; then outside.blend."""
    os.makedirs(out_dir, exist_ok=True)
    blend = os.path.join(out_dir, "house.blend")
    if from_blend:
        if os.path.abspath(src) != os.path.abspath(blend):
            shutil.copyfile(src, blend)
        bpy.ops.wm.open_mainfile(filepath=blend)
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.gltf(filepath=src)
        bpy.ops.wm.save_as_mainfile(filepath=blend)
    scene = bpy.context.scene
    scene.render.resolution_x, scene.render.resolution_y = ASPECT
    import photo

    for o in scene.objects:
        # door leaves too: the shoot swings those in the frame out of it (photo.open_doors)
        if o.get("kind") in ("terrain", "context") or photo.door_leaf(o) is o:
            o.hide_render = True
            for c in o.children_recursive:
                c.hide_render = True
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    doors = door_positions(scene)
    out = []
    for room in load_rooms(rooms_json):
        cands = []
        for kind, spec in room_cameras(room, doors):
            floor, ahead = score_camera(scene, dg, spec, room)
            score = floor - (NEAR_PENALTY if ahead < NEAR else 0.0)
            cands.append({"camera": spec, "kind": kind, "score": round(score, 4), "floor": round(floor, 4),
                          "ahead": None if math.isinf(ahead) else round(ahead, 2)})
        cands.sort(key=lambda c: -c["score"])
        best = cands[0] if cands and cands[0]["floor"] > 0 else None
        out.append({"name": room["name"], "use": room["use"], "y": room["y"], "area": round(area(room["polygon"]), 2),
                    "camera": best and best["camera"], "score": best["score"] if best else 0.0,
                    "views": [c["camera"] for c in distinct(cands)], "candidates": cands})
        print(f"PREPARE {room['name']}: {len(cands)} candidates, best {best and best['camera']} "
              f"({best and best['score']})", flush=True)
    import outside

    exteriors = outside.build(blend, out_dir, context, site)["views"]
    with open(os.path.join(out_dir, "cameras.json"), "w", encoding="utf-8") as f:
        json.dump({"rooms": out, "exteriors": exteriors}, f, indent=1, ensure_ascii=False)


def shoot(spec_json):
    """Render one photo from a spec file."""
    import photo

    spec = json.load(open(spec_json, encoding="utf-8"))
    if spec.get("kind") == "exterior":
        import outside

        outside.run(spec["blend"], spec["camera"], spec["out"], int(spec.get("width", 1920)),
                    int(spec.get("height", 1080)), int(spec.get("samples", 256)), sun=spec.get("sun"))
        return
    photo.run(spec["blend"], load_rooms(spec["rooms"]), spec["camera"], spec["out"],
              int(spec.get("width", 1920)), int(spec.get("height", 1080)), int(spec.get("samples", 256)),
              sun=spec.get("sun"), room_lamps=spec.get("room_lamps"), remove=spec.get("remove") or (),
              staging_level=spec.get("staging") or "furnished")


def main(argv):
    if len(argv) >= 4 and argv[0] == "prepare":
        opts = argv[4:]

        def opt(name):
            return opts[opts.index(name) + 1] if name in opts and opts.index(name) + 1 < len(opts) else None

        site = opt("--site")
        lat_lon = [float(v) for v in site.split(",")] if site else None
        prepare(argv[1], argv[2], argv[3], from_blend="--blend" in opts, context=opt("--context"),
                site={"lat": lat_lon[0], "lon": lat_lon[1]} if lat_lon else None)
    elif len(argv) == 2 and argv[0] == "shoot":
        shoot(argv[1])
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
