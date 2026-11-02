"""Candidate owner cameras for a room, as fast untextured thumbnails (Workbench): one from each corner of the room,
0.5 m in, at eye height, looking across the room. For picking a camera the way an owner would in the viewer, on the
bare export (no dressing, no light), so the picker does not see the photo the pipeline will make.

    uv run --no-project --python 3.11 --with bpy --with pillow python frame_cands.py <house.blend> <rooms.json> \
        "<room name>" <out dir> [eye=1.3] [fov=58]

Writes <out dir>/<k>.png and sheet.jpg; prints each candidate's camera spec ("x,y,z>tx,ty,tz@fov", scene axes).
"""
import json
import math
import os
import sys

import bpy
from mathutils import Vector


def main():
    blend, rooms_json, name, out, *rest = sys.argv[1:]
    eye = float(rest[0]) if rest else 1.3
    fov = float(rest[1]) if len(rest) > 1 else 58.0
    rooms = json.load(open(rooms_json, encoding="utf-8"))
    rooms = rooms["rooms"] if isinstance(rooms, dict) else rooms
    room = next(r for r in rooms if r["name"] == name)
    poly, y = room["polygon"], room["y"]
    cx, cz = sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly)
    bpy.ops.wm.open_mainfile(filepath=blend)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "MATERIAL"
    scene.display.shading.show_shadows = True
    scene.display.shading.show_cavity = True
    scene.render.resolution_x, scene.render.resolution_y = 480, 270
    for o in scene.objects:  # the outside's big pieces would only get in the way of judging the room
        if o.get("kind") in ("terrain", "context"):
            o.hide_render = True
    cam_data = bpy.data.cameras.new("cand")
    cam = bpy.data.objects.new("cand", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    cam_data.sensor_fit = "VERTICAL"
    cam_data.angle_y = math.radians(fov)
    cam_data.clip_start = 0.05
    os.makedirs(out, exist_ok=True)
    specs = []
    n = len(poly)
    for k in range(n):
        p, a, b = Vector(poly[k]), Vector(poly[k - 1]), Vector(poly[(k + 1) % n])
        bis = ((a - p).normalized() + (b - p).normalized())
        if bis.length < 1e-3:
            continue
        inward = bis.normalized()
        c = p + inward * 0.5 * math.sqrt(2)
        if (Vector((cx, cz)) - c).dot(inward) <= 0:  # a reflex corner: its bisector points out of the room
            continue
        t = Vector((cx, cz)) + (Vector((cx, cz)) - c) * 0.4
        pos, tgt = (c.x, y + eye, c.y), (t.x, y + 1.0, t.y)
        bl = lambda v: Vector((v[0], -v[2], v[1]))  # scene (x east, y up, z south) -> Blender
        cam.location = bl(pos)
        d = bl(tgt) - bl(pos)
        d.z = 0  # level, like the owner's walk camera: the photo pipeline keeps verticals straight
        cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
        scene.render.filepath = os.path.join(out, f"{len(specs) + 1}.png")
        bpy.ops.render.render(write_still=True)
        spec = f"{pos[0]:.2f},{pos[1]:.2f},{pos[2]:.2f}>{tgt[0]:.2f},{tgt[1]:.2f},{tgt[2]:.2f}@{fov:.0f}"
        specs.append(spec)
        print(f"CAND {len(specs)}: {spec}")
    from PIL import Image, ImageDraw
    cols = 3
    rows = (len(specs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 484, rows * 274), "white")
    for i in range(len(specs)):
        im = Image.open(os.path.join(out, f"{i + 1}.png")).convert("RGB")
        ImageDraw.Draw(im).text((8, 8), str(i + 1), fill=(255, 0, 0))
        sheet.paste(im, ((i % cols) * 484, (i // cols) * 274))
    sheet.save(os.path.join(out, "sheet.jpg"), quality=85)
    json.dump(specs, open(os.path.join(out, "cands.json"), "w"))


if __name__ == "__main__":
    main()
