"""Shader helpers, plaster relief, curtains, textiles and placed models for the photo.

Positions are scene metres (three.js frame: x east, y up, z south). Assets: CC0 Poly Haven downloads under
PHOTO_ASSETS, our own under PHOTO_MADE.
"""

import math
import os

import bpy
import numpy as np
from mathutils import Vector

import paths

TEX = os.path.join(paths.ASSETS, "textures")
MODELS = os.path.join(paths.ASSETS, "models")
RELIEF = 0.25


def blender(p):
    x, y, z = p
    return Vector((x, -z, y))


def image(path, data=False):
    img = bpy.data.images.load(path, check_existing=True)
    img.colorspace_settings.name = "Non-Color" if data else "sRGB"
    return img


def mean_linear(img, step_px=7):
    """The mean colour of an image in linear RGB (sRGB images are linearised by Blender's pixels)."""
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    return px.reshape(-1, 4)[::step_px, :3].mean(axis=0)


def principled(mat):
    return next(n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")


def tex_node(nt, img, vector, box=False):
    t = nt.nodes.new("ShaderNodeTexImage")
    t.image = img
    if box:
        t.projection = "BOX"
        t.projection_blend = 0.3
    nt.links.new(vector, t.inputs["Vector"])
    return t


def normal_map(nt, img, vector, strength, box=False):
    t = tex_node(nt, img, vector, box)
    n = nt.nodes.new("ShaderNodeNormalMap")
    n.inputs["Strength"].default_value = strength
    nt.links.new(t.outputs["Color"], n.inputs["Color"])
    return n.outputs["Normal"]


def rough_map(nt, img, vector, lo, hi, box=False):
    t = tex_node(nt, img, vector, box)
    r = nt.nodes.new("ShaderNodeMapRange")
    r.inputs["To Min"].default_value = lo
    r.inputs["To Max"].default_value = hi
    nt.links.new(t.outputs["Color"], r.inputs["Value"])
    return r.outputs["Result"]


def objects_of(scene, kind):
    out = []
    for o in scene.objects:
        p = o
        while p is not None and p.get("kind") != kind:
            p = p.parent
        if p is not None and o.type == "MESH":
            out.append(o)
    return out


def plaster(scene):
    """Walls, partitions and the slabs' undersides: their colour, a plaster relief, matte."""
    pl = os.path.join(TEX, "plaster_grey_04")
    nor, rough = image(os.path.join(pl, "nor_gl.jpg"), True), image(os.path.join(pl, "rough.jpg"), True)
    done = set()
    def kinds(o):
        out = set()
        while o is not None:
            out.add(o.get("kind"))
            o = o.parent
        return out

    for kind in ("wall", "partition", "slab"):
        for o in objects_of(scene, kind):
            if kinds(o) & {"window", "door", "interiorDoor", "skirting"}:
                continue
            for s in o.material_slots:
                m = s.material
                if not m or m.name in done or m.name.startswith("archviz"):
                    continue
                q = principled(m)
                c = q.inputs["Base Color"].default_value
                # painted surfaces (light, or coloured by the kit's own texture), not frames or glass
                dark = not q.inputs["Base Color"].is_linked and min(c[:3]) < 0.5
                if q.inputs["Alpha"].default_value < 1 or dark:
                    continue
                tc = m.node_tree.nodes.new("ShaderNodeTexCoord")
                mp = m.node_tree.nodes.new("ShaderNodeMapping")
                mp.inputs["Scale"].default_value = (1 / 1.5,) * 3
                m.node_tree.links.new(tc.outputs["Object"], mp.inputs["Vector"])
                m.node_tree.links.new(normal_map(m.node_tree, nor, mp.outputs["Vector"], RELIEF, box=True), q.inputs["Normal"])
                m.node_tree.links.new(rough_map(m.node_tree, rough, mp.outputs["Vector"], 0.78, 0.92, box=True), q.inputs["Roughness"])
                done.add(m.name)
    print(f"finishes: plaster on {sorted(done)}")


def linen(scene):
    """The curtains let the light through, in their own colour (the project's furnishing, kept)."""
    n = 0
    for o in scene.objects:
        p = o
        while p is not None and "curtain" not in str(p.get("name", "")):
            p = p.parent
        if p is None or o.type != "MESH":
            continue
        for s in o.material_slots:
            m = s.material
            if not m or m.get("linen"):
                continue
            q = principled(m)
            q.inputs["Roughness"].default_value = 0.9
            q.inputs["Sheen Weight"].default_value = 0.4
            nt = m.node_tree
            out = next(x for x in nt.nodes if x.bl_idname == "ShaderNodeOutputMaterial")
            tr = nt.nodes.new("ShaderNodeBsdfTranslucent")
            if q.inputs["Base Color"].is_linked:
                nt.links.new(q.inputs["Base Color"].links[0].from_socket, tr.inputs["Color"])
            else:
                tr.inputs["Color"].default_value = q.inputs["Base Color"].default_value
            mix = nt.nodes.new("ShaderNodeMixShader")
            mix.inputs[0].default_value = 0.35
            nt.links.new(q.outputs[0], mix.inputs[1])
            nt.links.new(tr.outputs[0], mix.inputs[2])
            nt.links.new(mix.outputs[0], out.inputs["Surface"])
            m["linen"] = True
            n += 1
    print(f"linen: {n} curtain materials")


def place(scene, path, at, height=None, turn_deg=0.0, name=None, tallest=False):
    """Import a glTF/GLB and stand it at scene point `at` (its base centred there), `height` metres tall.
    `tallest`: the file holds several variants side by side; keep the tallest."""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    if tallest:
        bpy.context.view_layer.update()
        tops = [o for o in new if o.parent is None]

        def height_of(o):
            ps = [c.matrix_world @ Vector(v) for c in [o, *o.children_recursive] if c.type == "MESH" for v in c.bound_box]
            return max(p.z for p in ps) - min(p.z for p in ps) if ps else 0

        keep = max(tops, key=height_of)
        kept = [keep, *keep.children_recursive]
        drop = [c for o in tops if o is not keep for c in [o, *o.children_recursive]]
        for c in drop:
            bpy.data.objects.remove(c, do_unlink=True)
        new = kept
    root = bpy.data.objects.new(name or os.path.basename(path), None)
    scene.collection.objects.link(root)
    for o in new:
        if o.parent is None:
            o.parent = root
    bpy.context.view_layer.update()
    ps = [o.matrix_world @ Vector(c) for o in new if o.type == "MESH" for c in o.bound_box]
    lo = Vector((min(p.x for p in ps), min(p.y for p in ps), min(p.z for p in ps)))
    hi = Vector((max(p.x for p in ps), max(p.y for p in ps), max(p.z for p in ps)))
    s = height / (hi.z - lo.z) if height else 1.0
    for o in new:
        if o.parent is root:
            o.location -= Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
    root.scale = (s, s, s)
    root.rotation_euler[2] = math.radians(turn_deg)
    root.location = blender(at)
    bpy.context.view_layer.update()
    return root, (hi - lo) * s


def model(name):
    return os.path.join(MODELS, name, f"{name}.gltf")


def rug(scene, center, size, thickness=0.012, floor=0.0):
    """A rug with rounded corners in a cream boucle (CC0 scan), lying on the floor."""
    w, l = size
    bpy.ops.mesh.primitive_plane_add(size=1.0)
    o = bpy.context.active_object
    o.name = "rug"
    o.scale = (w, l, 1)
    bpy.ops.object.transform_apply(scale=True)
    bev = o.modifiers.new("corners", "BEVEL")
    bev.affect = "VERTICES"
    bev.width = 0.06
    bev.segments = 6
    sol = o.modifiers.new("thick", "SOLIDIFY")
    sol.thickness = thickness
    sol.offset = 1.0
    o.location = blender((center[0], floor + 0.006, center[1]))
    m = bpy.data.materials.new("archviz-rug")
    nt = m.node_tree
    p = principled(m)
    d = os.path.join(TEX, "curly_teddy_natural")
    tc = nt.nodes.new("ShaderNodeTexCoord")
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (1 / 0.26, 1 / 0.26, 1 / 0.26)
    nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])
    vec = mp.outputs["Vector"]
    col = tex_node(nt, image(os.path.join(d, "diff.jpg")), vec)
    light = nt.nodes.new("ShaderNodeMixRGB")
    light.blend_type = "MULTIPLY"
    light.inputs[0].default_value = 1.0
    light.inputs[2].default_value = (0.92, 0.88, 0.82, 1)  # an oatmeal wool, a shade under the scan
    nt.links.new(col.outputs["Color"], light.inputs[1])
    # a border band 7 cm wide, 8 cm in from the edge (object space: the rug spans +-w/2, +-l/2)
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(tc.outputs["Object"], sep.inputs[0])

    def dist(axis, half):
        a = nt.nodes.new("ShaderNodeMath")
        a.operation = "ABSOLUTE"
        nt.links.new(sep.outputs[axis], a.inputs[0])
        b = nt.nodes.new("ShaderNodeMath")
        b.operation = "SUBTRACT"
        b.inputs[0].default_value = half
        nt.links.new(a.outputs[0], b.inputs[1])
        return b.outputs[0]

    edge = nt.nodes.new("ShaderNodeMath")
    edge.operation = "MINIMUM"
    nt.links.new(dist("X", w / 2), edge.inputs[0])
    nt.links.new(dist("Y", l / 2), edge.inputs[1])
    band = nt.nodes.new("ShaderNodeMapRange")
    band.interpolation_type = "SMOOTHSTEP"
    nt.links.new(edge.outputs[0], band.inputs["Value"])
    lo, hi = nt.nodes.new("ShaderNodeMath"), nt.nodes.new("ShaderNodeMath")
    lo.operation, hi.operation = "GREATER_THAN", "LESS_THAN"
    lo.inputs[1].default_value, hi.inputs[1].default_value = 0.08, 0.15
    nt.links.new(edge.outputs[0], lo.inputs[0])
    nt.links.new(edge.outputs[0], hi.inputs[0])
    inband = nt.nodes.new("ShaderNodeMath")
    inband.operation = "MULTIPLY"
    nt.links.new(lo.outputs[0], inband.inputs[0])
    nt.links.new(hi.outputs[0], inband.inputs[1])
    border = nt.nodes.new("ShaderNodeMixRGB")
    border.blend_type = "MULTIPLY"
    border.inputs[2].default_value = (0.42, 0.38, 0.34, 1)
    nt.links.new(inband.outputs[0], border.inputs[0])
    nt.links.new(light.outputs[0], border.inputs[1])
    nt.links.new(border.outputs[0], p.inputs["Base Color"])
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 1.0
    bump.inputs["Distance"].default_value = 0.004
    nt.links.new(tex_node(nt, image(os.path.join(d, "disp.jpg"), True), vec).outputs["Color"], bump.inputs["Height"])
    nt.links.new(normal_map(nt, image(os.path.join(d, "nor_gl.jpg"), True), vec, 1.0), bump.inputs["Normal"])
    nt.links.new(bump.outputs["Normal"], p.inputs["Normal"])
    nt.links.new(rough_map(nt, image(os.path.join(d, "rough.jpg"), True), vec, 0.85, 1.0), p.inputs["Roughness"])
    p.inputs["Sheen Weight"].default_value = 0.3
    p.inputs["Sheen Roughness"].default_value = 0.6
    o.data.materials.append(m)
    return o


def attach(root, path, material=None):
    """Import a file made in `root`'s own frame (made/throw.py) and fix it to root."""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    for o in [o for o in bpy.data.objects if o not in before]:
        if o.parent is None:
            o.parent = root
            o.matrix_parent_inverse.identity()
        if material and o.type == "MESH":
            o.data.materials.clear()
            o.data.materials.append(material)


def wool_material(scale=(5.8, 1.9), tint=(1.7, 1.6, 1.45)):
    m = bpy.data.materials.new("archviz-wool")
    nt = m.node_tree
    p = principled(m)
    d = os.path.join(TEX, "poly_wool_herringbone")
    uv = nt.nodes.new("ShaderNodeUVMap")
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (*scale, 1)
    nt.links.new(uv.outputs["UV"], mp.inputs["Vector"])
    vec = mp.outputs["Vector"]
    col = tex_node(nt, image(os.path.join(d, "diff.jpg")), vec)
    lift = nt.nodes.new("ShaderNodeMixRGB")
    lift.blend_type = "MULTIPLY"
    lift.inputs[0].default_value = 1.0
    lift.inputs[2].default_value = (*tint, 1)  # oatmeal by default, lighter than the grey scan
    nt.links.new(col.outputs["Color"], lift.inputs[1])
    nt.links.new(lift.outputs[0], p.inputs["Base Color"])
    nt.links.new(normal_map(nt, image(os.path.join(d, "nor_gl.jpg"), True), vec, 1.0), p.inputs["Normal"])
    p.inputs["Roughness"].default_value = 0.95
    p.inputs["Sheen Weight"].default_value = 0.6
    return m


def runner(scene, at, size, turn_rad=0.0):
    """A linen table runner lying along the table (scene z, turned by `turn_rad` about the vertical)."""
    bpy.ops.mesh.primitive_plane_add(size=1.0)
    o = bpy.context.active_object
    o.name = "runner"
    o.scale = (size[0], size[1], 1)
    bpy.ops.object.transform_apply(scale=True)
    sol = o.modifiers.new("thick", "SOLIDIFY")
    sol.thickness = 0.002
    sol.offset = 1.0
    o.location = blender(at)
    o.rotation_euler[2] = turn_rad
    m = bpy.data.materials.new("archviz-runner")
    nt = m.node_tree
    p = principled(m)
    p.inputs["Base Color"].default_value = (0.66, 0.62, 0.55, 1)
    p.inputs["Roughness"].default_value = 0.92
    p.inputs["Sheen Weight"].default_value = 0.5
    tc = nt.nodes.new("ShaderNodeTexCoord")
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (1 / 0.25,) * 3
    nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])
    nt.links.new(normal_map(nt, image(os.path.join(TEX, "rough_linen", "nor_gl.jpg"), True), mp.outputs["Vector"], 0.8), p.inputs["Normal"])
    o.data.materials.append(m)
    return o


def linen_material():
    """Off-white washed linen: its weave (CC0 scan) and the soft creases of a slept-in duvet."""
    m = bpy.data.materials.new("archviz-duvet")
    nt = m.node_tree
    p = principled(m)
    p.inputs["Base Color"].default_value = (0.86, 0.85, 0.82, 1)
    p.inputs["Roughness"].default_value = 0.9
    p.inputs["Sheen Weight"].default_value = 0.4
    tc = nt.nodes.new("ShaderNodeTexCoord")
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (1 / 0.18,) * 3
    nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])
    weave = normal_map(nt, image(os.path.join(TEX, "rough_linen", "nor_gl.jpg"), True), mp.outputs["Vector"], 1.0, box=True)
    crease = nt.nodes.new("ShaderNodeTexNoise")
    crease.inputs["Scale"].default_value = 2.5
    crease.inputs["Detail"].default_value = 8.0
    crease.inputs["Distortion"].default_value = 1.5
    nt.links.new(tc.outputs["Object"], crease.inputs["Vector"])
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.6
    bump.inputs["Distance"].default_value = 0.03
    nt.links.new(crease.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(weave, bump.inputs["Normal"])
    nt.links.new(bump.outputs["Normal"], p.inputs["Normal"])
    return m
