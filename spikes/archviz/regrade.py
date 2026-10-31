"""Regrade a photo from its scene-linear frame (photo.py writes <name>-linear.exr and <name>-grade.json):
the same view transform as the render, with the exposure, white balance or look changed, then post.py.
No render, seconds on a CPU.

    regrade.py <name>.png <out.png> [exposure=+0.3] [look="AgX - Base Contrast"] [temperature=6000] [strength=0.5]
"""

import json
import sys

import bpy

from post import post


def regrade(src, out, **change):
    g = json.load(open(src.replace(".png", "-grade.json")))
    shift = change.pop("exposure_shift", 0.0)
    strength = change.pop("strength", 0.0)
    g.update(change)
    scene = bpy.context.scene
    vs = scene.view_settings
    vs.view_transform, vs.look = g["view_transform"], g["look"]
    vs.exposure = g["exposure"] + shift
    vs.use_white_balance = True
    vs.white_balance_temperature, vs.white_balance_tint = g["temperature"], g["tint"]
    scene.render.image_settings.file_format, scene.render.image_settings.color_depth = "PNG", "8"
    img = bpy.data.images.load(src.replace(".png", "-linear.exr"))
    raw = out.replace(".png", "-raw.png")
    img.save_render(raw, scene=scene)
    post(raw, out, strength=strength)
    return g


if __name__ == "__main__":
    src, out, *kv = sys.argv[1:]
    change = {}
    for a in kv:
        k, v = a.split("=", 1)
        change[k] = v if k == "look" else float(v)
    print("regrade", regrade(src, out, **change))
