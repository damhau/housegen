"""#42 bake spike, step 2: one storey's light baked into a lightmap with Cycles on the GPU.

    uv run --no-project --python 3.11 --with bpy --with xatlas python -u bake.py <house.glb> <rooms.json> <storey y> <out_dir> [size samples]

Frame: three.js (x east, y up, z south) imports into Blender as (x, -z, y). The storey's meshes are
those whose box overlaps its floor-to-ceiling band and whose centre lies in (or within 40 cm of) one
of its rooms. Each gets its own mesh data and a second UV map, "lightmap", unwrapped (smart project)
and packed with the others into one atlas at an even texel density. The bake is Cycles' diffuse
light alone (direct + indirect, no surface colour: what three.js wants as a lightMap, times pi), from
the presentation look's sky and sun and the scene's lamps. Writes lightmap.exr (half float),
storey.glb (the baked meshes with both UV maps: TEXCOORD_1 is the lightmap's) and bake.json (timings).
"""

import json
import math
import sys
import time

import bpy  # first: addon_utils and mathutils come with it
import addon_utils  # noqa: E402
import bmesh  # noqa: E402
import numpy as np  # noqa: E402
import xatlas  # noqa: E402
from mathutils import Vector  # noqa: E402

SUN = {"elevation": 42, "azimuth": 200}  # the presentation look's sun (runtime.js PRESENTATION_LOOK)


def inside(poly, x, z):
    c = False
    for i in range(len(poly)):
        (xi, zi), (xj, zj) = poly[i], poly[i - 1]
        if (zi > z) != (zj > z) and x < (xj - xi) * (z - zi) / (zj - zi) + xi:
            c = not c
    return c


def near(poly, x, z, margin):
    if inside(poly, x, z):
        return True
    for i in range(len(poly)):
        (ax, az), (bx, bz) = poly[i - 1], poly[i]
        dx, dz = bx - ax, bz - az
        L2 = dx * dx + dz * dz or 1
        t = max(0, min(1, ((x - ax) * dx + (z - az) * dz) / L2))
        if math.hypot(x - ax - t * dx, z - az - t * dz) < margin:
            return True
    return False


def use_gpu():
    addon_utils.enable("cycles", default_set=True)
    prefs = bpy.context.preferences.addons["cycles"].preferences
    prefs.compute_device_type = "CUDA"
    prefs.refresh_devices()
    for d in prefs.devices:
        d.use = d.type == "CUDA"
    return [d.name for d in prefs.devices if d.use]


def sky(scene):
    world = bpy.data.worlds.new("sky")
    scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    s = nt.nodes.new("ShaderNodeTexSky")
    s.sky_type = "MULTIPLE_SCATTERING"
    s.sun_elevation = math.radians(SUN["elevation"])
    s.sun_rotation = math.radians(SUN["azimuth"])
    bg = nt.nodes.new("ShaderNodeBackground")
    out = nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(s.outputs[0], bg.inputs[0])
    nt.links.new(bg.outputs[0], out.inputs[0])


def main(glb, rooms_file, storey, out, size=4096, samples=128):
    t0 = time.time()
    timings = {}
    bpy.ops.wm.read_factory_settings(use_empty=True)
    devices = use_gpu()
    bpy.ops.import_scene.gltf(filepath=glb)
    timings["import_s"] = round(time.time() - t0, 1)
    rooms = [r for r in json.load(open(rooms_file)) if abs(r["y"] - storey) < 0.05]
    lo, hi = storey - 0.05, storey + 2.7
    picked = []
    for o in bpy.data.objects:
        if o.type != "MESH":
            continue
        corners = [o.matrix_world @ Vector(c) for c in o.bound_box]
        zs = [c.z for c in corners]
        if max(zs) < lo or min(zs) > hi:
            continue
        cx = sum(c.x for c in corners) / 8
        cy = sum(c.y for c in corners) / 8
        if any(near(r["polygon"], cx, -cy, 0.4) for r in rooms):
            picked.append(o)
    print(f"imported in {timings['import_s']} s; storey {storey}: {len(rooms)} rooms, {len(picked)} meshes to bake", flush=True)
    bpy.ops.object.select_all(action="DESELECT")
    for o in picked:
        o.select_set(True)
    bpy.context.view_layer.objects.active = picked[0]
    bpy.ops.object.make_single_user(object=True, obdata=True)
    # every mesh: its own UVs first (a dummy where it has none), the lightmap second
    for o in picked:
        uvs = o.data.uv_layers
        if len(uvs) == 0:
            uvs.new(name="UVMap")
        while len(uvs) > 1:
            uvs.remove(uvs[-1])
        lm = uvs.new(name="lightmap")
        uvs.active = lm
    # the lightmap UVs: xatlas (the C++ atlas engines use for lightmaps) over every mesh at once, in
    # world units so the texel density is even; Blender's own unwrap and pack took over 15 minutes
    t = time.time()
    atlas = xatlas.Atlas()
    added = []
    for o in picked:
        me = o.data
        if any(len(p.vertices) != 3 for p in me.polygons):
            bm = bmesh.new()
            bm.from_mesh(me)
            bmesh.ops.triangulate(bm, faces=bm.faces[:])
            bm.to_mesh(me)
            bm.free()
        n, f = len(me.vertices), len(me.polygons)
        if f == 0:
            continue
        co = np.empty(n * 3, dtype=np.float32)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        M = np.array(o.matrix_world, dtype=np.float32)
        world = co @ M[:3, :3].T + M[:3, 3]
        loops = np.empty(f * 3, dtype=np.int32)
        me.polygons.foreach_get("vertices", loops)
        atlas.add_mesh(np.ascontiguousarray(world, dtype=np.float32), np.ascontiguousarray(loops.reshape(-1, 3), dtype=np.uint32))
        added.append(o)
    pack = xatlas.PackOptions()
    pack.resolution = size
    pack.padding = 2
    pack.bilinear = True
    atlas.generate(xatlas.ChartOptions(), pack)
    print(f"xatlas: {len(added)} meshes, atlas {atlas.width}x{atlas.height}, {time.time() - t:.0f} s", flush=True)
    for i, o in enumerate(added):
        _vmap, idx, uvs = atlas[i]
        me = o.data
        starts = np.empty(len(me.polygons), dtype=np.int32)
        me.polygons.foreach_get("loop_start", starts)
        per_loop = np.zeros((len(me.loops), 2), dtype=np.float32)
        for k in range(3):
            per_loop[starts + k] = uvs[idx[:, k]]
        me.uv_layers["lightmap"].data.foreach_set("uv", per_loop.reshape(-1))
    picked = added
    timings["unwrap_s"] = round(time.time() - t, 1)
    # one object: Blender bakes each selected object as its own Cycles session (the scene synced and
    # its BVH built every time: 499 meshes kept the GPU idle for over ten minutes)
    t = time.time()
    bpy.ops.object.select_all(action="DESELECT")
    for o in picked:
        o.select_set(True)
    bpy.context.view_layer.objects.active = picked[0]
    bpy.ops.object.join()
    picked = [bpy.context.view_layer.objects.active]
    timings["join_s"] = round(time.time() - t, 1)
    timings["joined"] = len(added)
    # the atlas, the active image node of every material of the baked meshes
    img = bpy.data.images.new("lightmap", size, size, float_buffer=True, alpha=False)
    mats = {m for o in picked for m in o.data.materials if m}
    for m in mats:
        m.use_nodes = True
        node = m.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = img
        for n in m.node_tree.nodes:
            n.select = False
        node.select = True
        m.node_tree.nodes.active = node
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "GPU"
    scene.cycles.samples = samples
    scene.cycles.max_bounces = 8
    scene.cycles.diffuse_bounces = 4
    scene.cycles.caustics_reflective = False
    scene.cycles.caustics_refractive = False
    sky(scene)
    scene.render.bake.margin = 4
    print(f"baking {size}x{size}, {samples} samples on {devices}", flush=True)
    t = time.time()
    bpy.ops.object.bake(type="DIFFUSE", pass_filter={"DIRECT", "INDIRECT"}, margin=4, use_clear=True)
    timings["bake_s"] = round(time.time() - t, 1)
    scene.render.image_settings.file_format = "OPEN_EXR"
    scene.render.image_settings.color_depth = "16"
    img.save_render(f"{out}/lightmap.exr", scene=scene)
    t = time.time()
    bpy.ops.export_scene.gltf(filepath=f"{out}/storey.glb", use_selection=True, export_texcoords=True,
                              export_normals=True, export_materials="EXPORT", export_lights=False, export_image_format="JPEG")
    timings["export_s"] = round(time.time() - t, 1)
    timings.update(meshes=len(added), materials=len(mats), size=size, samples=samples, devices=devices, total_s=round(time.time() - t0, 1))
    json.dump(timings, open(f"{out}/bake.json", "w"), indent=1)
    print(json.dumps(timings), flush=True)


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0], a[1], float(a[2]), a[3], *(int(x) for x in a[4:6]))
