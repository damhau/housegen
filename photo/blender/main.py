"""The listing photo pipeline's entry points, in Blender's Python (bpy 5.0.1).

    python main.py prepare <house.glb> <rooms.json> <out_dir> [--blend]
        <out_dir>/house.blend (the GLB imported; with --blend the first argument is already a .blend, copied) and
        <out_dir>/cameras.json: per room, corner cameras scored by ray casts on the bare import, the best one first.
    python main.py shoot <spec.json>
        spec: {"blend", "rooms", "camera", "out" (.jpg), "width", "height", "samples"}; writes the photo and
        <out>.json (the room, the final camera, the sun, the lamps, the device, the seconds per phase).
"""

import json
import math
import os
import shutil
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

EYE, FOV = 1.3, 58.0
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


def corner_cameras(room):
    """frame_cands.py's cameras: one per convex corner, 0.5 m from both walls, at eye height, looking past the centre."""
    poly, y = room["polygon"], room["y"]
    cx, cz = sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly)
    centre = Vector((cx, cz))
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
        t = centre + (centre - c) * 0.4
        out.append(f"{c.x:.2f},{y + EYE:.2f},{c.y:.2f}>{t.x:.2f},{y + 1.0:.2f},{t.y:.2f}@{FOV:.0f}")
    return out


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


def prepare(src, rooms_json, out_dir, from_blend=False):
    """Import the house, save it as house.blend and write cameras.json next to it."""
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
    for o in scene.objects:
        if o.get("kind") in ("terrain", "context"):
            o.hide_render = True
            for c in o.children_recursive:
                c.hide_render = True
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    out = []
    for room in load_rooms(rooms_json):
        cands = []
        for spec in corner_cameras(room):
            floor, ahead = score_camera(scene, dg, spec, room)
            score = floor - (NEAR_PENALTY if ahead < NEAR else 0.0)
            cands.append({"camera": spec, "score": round(score, 4), "floor": round(floor, 4),
                          "ahead": None if math.isinf(ahead) else round(ahead, 2)})
        cands.sort(key=lambda c: -c["score"])
        best = cands[0] if cands and cands[0]["floor"] > 0 else None
        out.append({"name": room["name"], "use": room["use"], "y": room["y"], "area": round(area(room["polygon"]), 2),
                    "camera": best and best["camera"], "score": best["score"] if best else 0.0, "candidates": cands})
        print(f"PREPARE {room['name']}: {len(cands)} candidates, best {best and best['camera']} "
              f"({best and best['score']})", flush=True)
    with open(os.path.join(out_dir, "cameras.json"), "w", encoding="utf-8") as f:
        json.dump({"rooms": out}, f, indent=1, ensure_ascii=False)


def shoot(spec_json):
    """Render one photo from a spec file."""
    import photo

    spec = json.load(open(spec_json, encoding="utf-8"))
    photo.run(spec["blend"], load_rooms(spec["rooms"]), spec["camera"], spec["out"],
              int(spec.get("width", 1920)), int(spec.get("height", 1080)), int(spec.get("samples", 256)))


def main(argv):
    if len(argv) >= 4 and argv[0] == "prepare":
        prepare(argv[1], argv[2], argv[3], from_blend="--blend" in argv[4:])
    elif len(argv) == 2 and argv[0] == "shoot":
        shoot(argv[1])
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
