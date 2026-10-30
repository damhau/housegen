"""Finishes, textiles and decor for the archviz still (still.py merges these STEPS).

Positions are scene metres (three.js frame: x east, y up, z south). Assets: CC0 Poly Haven downloads under
assets/ (ph_get.py), our own under made/.
"""

import math
import os

import bpy
import numpy as np
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
TEX = os.path.join(HERE, "assets", "textures")
MODELS = os.path.join(HERE, "assets", "models")
STEPS = {}


def step(fn):
    STEPS[fn.__name__] = fn
    return fn


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


def world_mapping(nt, size, turn_deg=0.0):
    """Texture coordinates from the world position: one tile = `size` metres."""
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    m = nt.nodes.new("ShaderNodeMapping")
    m.inputs["Scale"].default_value = (1 / size, 1 / size, 1 / size)
    m.inputs["Rotation"].default_value[2] = math.radians(turn_deg)
    nt.links.new(geo.outputs["Position"], m.inputs["Vector"])
    return m.outputs["Vector"]


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


def floor_material(scene, at):
    """The material of the room floor under scene point (x, z)."""
    x, z = at
    for o in objects_of(scene, "roomFloor"):
        ps = [o.matrix_world @ Vector(c) for c in o.bound_box]
        if min(p.x for p in ps) <= x <= max(p.x for p in ps) and min(-p.y for p in ps) <= z <= max(-p.y for p in ps) \
                and max(p.z for p in ps) < 0.2:
            for s in o.material_slots:
                if s.material and principled(s.material).inputs["Base Color"].is_linked:
                    return s.material
    raise RuntimeError("no textured floor under the room")


@step
def finishes(scene):
    """The same finishes with texture: the oak floor as 1.7 m tiles of real planks (its colour kept: the photo
    scan is scaled to the old texture's mean), a fine plaster relief and a matte roughness on walls and ceilings."""
    floor = floor_material(scene, (-3.0, 2.5))
    p = principled(floor)
    nt = floor.node_tree
    old = p.inputs["Base Color"].links[0].from_node.image
    d = os.path.join(TEX, "laminate_floor_02_4k")
    diff = image(os.path.join(d, "diff.jpg"))
    ratio = np.clip(mean_linear(old) / mean_linear(diff), 0.3, 3.0)
    vec = world_mapping(nt, 1.7, 90)  # planks run north-south, toward the glazing
    col = tex_node(nt, diff, vec)
    tint = nt.nodes.new("ShaderNodeMixRGB")
    tint.blend_type = "MULTIPLY"
    tint.inputs[0].default_value = 1.0
    tint.inputs[2].default_value = (*ratio, 1)
    nt.links.new(col.outputs["Color"], tint.inputs[1])
    nt.links.new(tint.outputs[0], p.inputs["Base Color"])
    nt.links.new(normal_map(nt, image(os.path.join(d, "nor_gl.jpg"), True), vec, 0.7), p.inputs["Normal"])
    nt.links.new(rough_map(nt, image(os.path.join(d, "rough.jpg"), True), vec, FLOOR_ROUGH[0], FLOOR_ROUGH[1]), p.inputs["Roughness"])
    print(f"finishes: floor {floor.name} tinted by {np.round(ratio, 2)}")
    # walls, partitions and the slabs' undersides: their colour, a plaster relief, matte
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
    # the terrace outside the dining glazing: its tiles at a stone grey (white in the sun, they blow out)
    dg = bpy.context.evaluated_depsgraph_get()
    ok, _, _, _, hit, _ = scene.ray_cast(dg, blender((-2.5, 3.0, 5.8)), Vector((0, 0, -1)))
    if ok and hit.material_slots:
        m = hit.material_slots[0].material
        q = principled(m)
        nt = m.node_tree
        dim = nt.nodes.new("ShaderNodeMixRGB")
        dim.blend_type = "MULTIPLY"
        dim.inputs[0].default_value = 1.0
        dim.inputs[2].default_value = (0.52, 0.51, 0.49, 1)
        if q.inputs["Base Color"].is_linked:
            nt.links.new(q.inputs["Base Color"].links[0].from_socket, dim.inputs[1])
        else:
            dim.inputs[1].default_value = q.inputs["Base Color"].default_value
        nt.links.new(dim.outputs[0], q.inputs["Base Color"])
        q.inputs["Roughness"].default_value = 0.75
        print(f"finishes: terrace {m.name} at stone grey")


@step
def linen(scene):
    """The heavy grey curtains become off-white linen that lets the light through."""
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
            q.inputs["Base Color"].default_value = (0.80, 0.77, 0.71, 1)
            if q.inputs["Base Color"].is_linked:
                m.node_tree.links.remove(q.inputs["Base Color"].links[0])
            q.inputs["Roughness"].default_value = 0.9
            q.inputs["Sheen Weight"].default_value = 0.4
            nt = m.node_tree
            out = next(x for x in nt.nodes if x.bl_idname == "ShaderNodeOutputMaterial")
            tr = nt.nodes.new("ShaderNodeBsdfTranslucent")
            tr.inputs["Color"].default_value = (0.85, 0.80, 0.70, 1)
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


def rug(scene, center, size, thickness=0.012):
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
    o.location = blender((center[0], 0.006, center[1]))
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


@step
def decor(scene):
    """Styling, as an agency stages a listing: a rug under the dining table, a tall plant, a vase of eucalyptus
    on the table, fruit and a board on the island."""
    rug(scene, (-3.17, 2.58), (2.0, 3.0))
    place(scene, model("pachira_aquatica_01"), PLANT_AT, height=PLANT_H, turn_deg=30, tallest=True)
    # the kit's two black bowls at the island's end would hide the board
    for o in scene.objects:
        if o.get("name") == "bowls-black":
            for c in [o, *o.children_recursive]:
                c.hide_render = True
    # the table: eucalyptus in the kit's white vase (its mouth at 0.77 + 0.24 m)
    place(scene, os.path.join(HERE, "made", "eucalyptus.glb"), (-3.17, 0.95, 2.55), height=0.62, turn_deg=10)
    # the island: a board with lemons, a wooden bowl of apples and lemons
    top = 0.925
    _, board = place(scene, model("wooden_cutting_board"), (-2.62, top, 0.02), turn_deg=-25)
    for k, (dx, dz) in enumerate(((-0.05, 0.02), (0.05, -0.03), (0.0, 0.07))):
        place(scene, model("lemon"), (-2.62 + dx, top + board.z, 0.02 + dz), height=0.065, turn_deg=40 * k)
    _, bowl = place(scene, model("wooden_bowl_01"), (-3.2, top, -0.12), height=0.09)
    for k, (dx, dz, f) in enumerate(((-0.05, 0.0, "food_apple_01"), (0.05, 0.03, "food_apple_01"), (0.0, -0.05, "lemon"),
                                      (0.02, 0.06, "food_apple_01"))):
        place(scene, model(f), (-3.2 + dx, top + 0.035 + (0.03 if k == 3 else 0), -0.12 + dz), height=0.075, turn_deg=70 * k)
    # a framed print on the west wall, left of the window (the wall's face at x = -5.10)
    place(scene, os.path.join(HERE, "made", "art.glb"), (-5.10 + 0.016, 1.04, 4.0), turn_deg=-90)
    # under it, a tall ceramic floor vase with long eucalyptus stems
    _, vase = place(scene, model("ceramic_vase_04"), (-4.82, 0.01, 4.12), height=0.5, turn_deg=200)
    place(scene, os.path.join(HERE, "made", "eucalyptus.glb"), (-4.82, 0.01 + vase.z * 0.9, 4.12), height=0.85, turn_deg=70)
    runner(scene, (-3.17, 0.772, 2.58), (0.36, 1.9))
    # a reading corner by the window: the lounge chair, a herringbone wool throw over its arm (cloth simulation)
    root, _ = place(scene, model("mid_century_lounge_chair"), (CHAIR_AT[0], 0.01, CHAIR_AT[1]), turn_deg=CHAIR_TURN)
    attach(root, os.path.join(HERE, "made", "throw.glb"), wool_material())
    # a cushion on its seat, against the back
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    probe = root.matrix_world @ Vector((0.0, -0.12, 2.0))
    ok, seat, *_ = scene.ray_cast(dg, probe, Vector((0, 0, -1)))
    at = root.matrix_world @ Vector((0.05, 0.05, 0.0))
    if ok:
        place(scene, model("throw_pillows_01"), (at.x, seat.z + 0.005, -at.y), height=0.4, turn_deg=CHAIR_TURN + PILLOW_TURN, tallest=True)
    upholster(scene)
    print("decor: rug, pachira, eucalyptus, board, bowl, print, runner")


CHAIR_AT = tuple(float(v) for v in os.environ.get("CHAIR_AT", "-1.75,3.95").split(","))
PLANT_AT = tuple(float(v) for v in os.environ.get("PLANT_AT", "-1.95,0.01,1.6").split(","))
PLANT_H = float(os.environ.get("PLANT_H", "1.65"))
RELIEF = float(os.environ.get("RELIEF", "0.25"))
CHAIR_TURN = float(os.environ.get("CHAIR_TURN", "140"))


FLOOR_ROUGH = tuple(float(v) for v in os.environ.get("FLOOR_ROUGH", "0.16,0.42").split(","))
PILLOW_TURN = float(os.environ.get("PILLOW_TURN", "0"))


def upholster(scene):
    """The dining chairs' shells in a fine grey wool (upholstered shells), not bare plastic."""
    d = os.path.join(TEX, "knitted_fleece")
    done = set()
    for o in scene.objects:
        if o.get("name") != "dining-chair-grey":
            continue
        for c in o.children_recursive:
            for sl in c.material_slots if c.type == "MESH" else []:
                m = sl.material
                if not m or m.name in done:
                    continue
                q = principled(m)
                nt = m.node_tree
                tc = nt.nodes.new("ShaderNodeTexCoord")
                mp = nt.nodes.new("ShaderNodeMapping")
                mp.inputs["Scale"].default_value = (1 / 0.09,) * 3
                nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])
                nt.links.new(normal_map(nt, image(os.path.join(d, "nor_gl.jpg"), True), mp.outputs["Vector"], 0.6, box=True), q.inputs["Normal"])
                q.inputs["Roughness"].default_value = 0.88
                q.inputs["Sheen Weight"].default_value = 0.35
                done.add(m.name)
    print(f"upholster: {sorted(done)}")


@step
def edges(scene):
    """Real objects have no knife edges: every material shades its edges as if rounded by a few
    millimetres (Cycles' Bevel node in the normal), so they catch the light."""
    n = 0
    for m in bpy.data.materials:
        if not m.node_tree or m.get("bevel"):
            continue
        for q in [x for x in m.node_tree.nodes if x.bl_idname == "ShaderNodeBsdfPrincipled"]:
            nt = m.node_tree
            bev = nt.nodes.new("ShaderNodeBevel")
            bev.samples = 6
            bev.inputs["Radius"].default_value = float(os.environ.get("BEVEL", "0.004"))
            if q.inputs["Normal"].is_linked:
                nt.links.new(q.inputs["Normal"].links[0].from_socket, bev.inputs["Normal"])
            nt.links.new(bev.outputs["Normal"], q.inputs["Normal"])
            n += 1
        m["bevel"] = True
    print(f"edges: bevel on {n} shaders")


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


def wool_material():
    m = bpy.data.materials.new("archviz-wool")
    nt = m.node_tree
    p = principled(m)
    d = os.path.join(TEX, "poly_wool_herringbone")
    uv = nt.nodes.new("ShaderNodeUVMap")
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (5.8, 1.9, 1)
    nt.links.new(uv.outputs["UV"], mp.inputs["Vector"])
    vec = mp.outputs["Vector"]
    col = tex_node(nt, image(os.path.join(d, "diff.jpg")), vec)
    lift = nt.nodes.new("ShaderNodeMixRGB")
    lift.blend_type = "MULTIPLY"
    lift.inputs[0].default_value = 1.0
    lift.inputs[2].default_value = (1.7, 1.6, 1.45, 1)  # an oatmeal wool, lighter than the grey scan
    nt.links.new(col.outputs["Color"], lift.inputs[1])
    nt.links.new(lift.outputs[0], p.inputs["Base Color"])
    nt.links.new(normal_map(nt, image(os.path.join(d, "nor_gl.jpg"), True), vec, 1.0), p.inputs["Normal"])
    p.inputs["Roughness"].default_value = 0.95
    p.inputs["Sheen Weight"].default_value = 0.6
    return m


def runner(scene, at, size):
    """A linen table runner lying along the table (scene z)."""
    bpy.ops.mesh.primitive_plane_add(size=1.0)
    o = bpy.context.active_object
    o.name = "runner"
    o.scale = (size[0], size[1], 1)
    bpy.ops.object.transform_apply(scale=True)
    sol = o.modifiers.new("thick", "SOLIDIFY")
    sol.thickness = 0.002
    sol.offset = 1.0
    o.location = blender(at)
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
