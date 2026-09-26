"""Proof for #40: a scene GLB (export_glb.py) rendered in Cycles on the GPU.

    uv run --no-project --python 3.11 --with bpy python render_villa.py import villa.glb villa.blend
    uv run --no-project --python 3.11 --with bpy python render_villa.py render villa.blend <view> <out.png> [w h samples exposure]

Scene frame (three.js): x east, y up, z south. glTF import puts it at Blender (x, -z, y): north = +Y.
Views are the scene's own (position, target, vertical fov), rendered as a photographer would: a level
camera, the framing moved with the lens shift so vertical lines stay vertical.
"""

import math
import sys
import time

import bpy  # first: addon_utils and mathutils come with it
import addon_utils  # noqa: E402
from mathutils import Vector  # noqa: E402

VIEWS = {
    "bedroom-main": ((4.75, 1.60, -1.30), (3.25, 0.55, -4.30), 68),
    "bedroom-photo": ((4.85, 1.30, -1.25), (3.10, 0.85, -4.40), 60),
    "dining-photo": ((0.25, 1.35, 1.95), (-3.6, 1.0, 3.35), 52),
    "living-photo": ((4.60, 1.30, 4.20), (1.0, 0.95, 2.30), 56),
}
SUN = {"elevation": 42, "azimuth": 200}  # the presentation look's sun (runtime.js PRESENTATION_LOOK)


def blender(p):
    x, y, z = p
    return Vector((x, -z, y))


def use_gpu():
    addon_utils.enable("cycles", default_set=True)
    prefs = bpy.context.preferences.addons["cycles"].preferences
    prefs.compute_device_type = "CUDA"
    prefs.refresh_devices()
    for d in prefs.devices:
        d.use = d.type == "CUDA"
    return [d.name for d in prefs.devices if d.use]


def do_import(glb, out):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    t = time.time()
    bpy.ops.import_scene.gltf(filepath=glb)
    print(f"imported in {time.time() - t:.0f} s: {len(bpy.data.objects)} objects, {len(bpy.data.materials)} materials, "
          f"{len(bpy.data.images)} images, {len([o for o in bpy.data.objects if o.type == 'LIGHT'])} lights")
    bpy.ops.wm.save_as_mainfile(filepath=out)


def setup(scene, exposure):
    scene.render.engine = "CYCLES"
    scene.cycles.device = "GPU"
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.adaptive_threshold = 0.01
    scene.cycles.use_denoising = True
    scene.cycles.denoiser = "OPENIMAGEDENOISE"
    scene.cycles.max_bounces = 12
    scene.cycles.caustics_reflective = False
    scene.cycles.caustics_refractive = False
    scene.cycles.blur_glossy = 1.0
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Base Contrast" if "AgX - Base Contrast" in [
        e.identifier for e in scene.view_settings.bl_rna.properties["look"].enum_items] else "None"
    scene.view_settings.exposure = exposure
    # the sky: Blender's multiple-scattering model, its sun where the presentation look puts it
    world = bpy.data.worlds.new("sky")
    scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    sky = nt.nodes.new("ShaderNodeTexSky")
    sky.sky_type = "MULTIPLE_SCATTERING"
    sky.sun_elevation = math.radians(SUN["elevation"])
    sky.sun_rotation = math.radians(SUN["azimuth"])  # measured: 0 = north (+Y), 90 = east (+X)
    bg = nt.nodes.get("Background") or nt.nodes.new("ShaderNodeBackground")
    out = nt.nodes.get("World Output") or nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(sky.outputs[0], bg.inputs[0])
    nt.links.new(bg.outputs[0], out.inputs[0])
    fix_normals(scene)
    # lamps: three.js intensities were tuned for the rasterizer; give them a real bulb's power
    for o in scene.objects:
        if o.type == "LIGHT" and o.data.type == "POINT":
            o.data.energy = 40.0
            o.data.shadow_soft_size = 0.06


def fix_normals(scene):
    """Meshes whose stored normals point against their faces (some ceiling triangles: invisible
    with three.js's double-sided materials, black in Cycles) shade with their faces' own normals."""
    fixed = []
    for obj in scene.objects:
        me = obj.data if obj.type == "MESH" else None
        if me is None or not me.polygons or me.users > 1 and me.name in fixed:
            continue
        cn = me.corner_normals
        bad = sum(
            1 for poly in me.polygons
            if sum((cn[i].vector for i in poly.loop_indices), Vector()).dot(poly.normal) < 0
        )
        if bad:
            with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
                bpy.ops.mesh.customdata_custom_splitnormals_clear()
            me.shade_flat()
            fixed.append(me.name)
    print(f"normals fixed on {len(fixed)} meshes")


def camera(scene, name, width, height):
    pos, target, fov = VIEWS[name]
    p, t = blender(pos), blender(target)
    cam = bpy.data.cameras.new(name)
    cam.sensor_fit = "VERTICAL"
    cam.angle_y = math.radians(fov)
    cam.clip_start = 0.05
    obj = bpy.data.objects.new(name, cam)
    scene.collection.objects.link(obj)
    d = t - p
    horiz = math.hypot(d.x, d.y)
    yaw = math.atan2(d.y, d.x) - math.pi / 2  # a camera looks down its -Z; level: rotate X by 90°
    obj.location = p
    obj.rotation_euler = (math.pi / 2, 0.0, yaw)
    # two-point perspective: the target's height brought into frame by the lens shift, not a tilt
    pitch = math.atan2(d.z, horiz)
    shift = math.tan(pitch) / (2 * math.tan(cam.angle_y / 2))
    cam.shift_y = shift * (height / max(width, height))
    scene.camera = obj


def do_render(blend, view, out, width=1600, height=1000, samples=512, exposure=0.0):
    bpy.ops.wm.open_mainfile(filepath=blend)
    devices = use_gpu()
    scene = bpy.context.scene
    setup(scene, exposure)
    scene.cycles.samples = samples
    scene.render.resolution_x, scene.render.resolution_y = width, height
    camera(scene, view, width, height)
    import os
    if os.environ.get("DENOISE") == "0":
        scene.cycles.use_denoising = False
    for name in filter(None, os.environ.get("HIDE", "").split(",")):
        bpy.data.objects[name].hide_render = True
    # the kit's room ceilings sit 2 cm inside the builder's floor slab (interior.js): two surfaces
    # 2 cm apart, which a path tracer shades black where they meet; the slab's underside is the ceiling
    hidden = [o for o in scene.objects if o.get("kind") == "ceiling"]
    for o in hidden:
        o.hide_render = True
    print(f"hid {len(hidden)} room ceilings")
    if os.environ.get("WB"):
        scene.view_settings.use_white_balance = True
        scene.view_settings.white_balance_temperature = float(os.environ["WB"])
    if os.environ.get("CLAY") == "1":
        clay = bpy.data.materials.new("clay")
        scene.view_layers[0].material_override = clay
    scene.render.filepath = out
    t = time.time()
    bpy.ops.render.render(write_still=True)
    print(f"{view}: {width}x{height}, {samples} samples, exposure {exposure}, {time.time() - t:.0f} s on {devices}")


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    if cmd == "import":
        do_import(*args)
    else:
        blend, view, out, *rest = args
        nums = [int(rest[0]), int(rest[1]), int(rest[2]), float(rest[3])] if rest else []
        do_render(blend, view, out, *nums)
