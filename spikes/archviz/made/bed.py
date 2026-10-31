"""An upholstered platform bed for Chambre 4 (made here, replacing the kit's bed whose bedding is baked into
one mesh): a linen-covered base on a recessed plinth, a padded headboard, a mattress and two sleeping pillows.
Writes made/bed.glb in the house's frame (Blender coordinates of the exported house: x = scene x,
y = -scene z, z up), materials named for dress.py: bed-fabric, bed-linen, bed-plinth.

    uv run --no-project --python 3.11 --with bpy python made/bed.py

The room: head on the west wall (x 0.10), centred on scene z 3.2, floor at y 2.75; a 160 x 200 mattress.
"""

import math
import os

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
FLOOR, WALL, CZ = 2.75, 0.10, 3.2
W, L = 1.60, 2.00          # mattress
FRAME_W, FRAME_H = 1.80, 0.30
cy = -CZ

bpy.ops.wm.read_factory_settings(use_empty=True)


def mat(name, rgb, rough):
    m = bpy.data.materials.new(name)
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = (*rgb, 1)
    p.inputs["Roughness"].default_value = rough
    return m


fabric, linen, plinth = mat("bed-fabric", (0.50, 0.47, 0.43), 0.9), mat("bed-linen", (0.88, 0.87, 0.85), 0.9), mat("bed-plinth", (0.05, 0.05, 0.05), 0.7)


def block(name, x0, x1, y0, y1, z0, z1, m, bevel, sub=0, puff=0.0):
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    o = bpy.context.active_object
    o.name = name
    o.scale = (x1 - x0, y1 - y0, z1 - z0)
    o.location = ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)
    bpy.ops.object.transform_apply(scale=True, location=False)
    if bevel:
        b = o.modifiers.new("bevel", "BEVEL")
        b.width = bevel
        b.segments = 5
        bpy.ops.object.modifier_apply(modifier="bevel")
    if sub:
        s = o.modifiers.new("sub", "SUBSURF")
        s.levels = sub
        bpy.ops.object.modifier_apply(modifier="sub")
    if puff:  # a filled cushion: the faces bulge a little
        c = o.modifiers.new("puff", "CAST")
        c.factor = puff
        bpy.ops.object.modifier_apply(modifier="puff")
    bpy.ops.object.shade_smooth()
    o.data.materials.append(m)
    return o


x0 = WALL + 0.10  # the base starts behind the headboard's padding
block("bed-plinth", x0 + 0.12, x0 + L - 0.08, cy - FRAME_W / 2 + 0.12, cy + FRAME_W / 2 - 0.12, FLOOR, FLOOR + 0.08, plinth, 0.0)
block("bed-base", x0, x0 + L + 0.06, cy - FRAME_W / 2, cy + FRAME_W / 2, FLOOR + 0.08, FLOOR + FRAME_H, fabric, 0.035)
block("bed-mattress", x0 + 0.03, x0 + 0.03 + L, cy - W / 2, cy + W / 2, FLOOR + FRAME_H, FLOOR + FRAME_H + 0.22, linen, 0.06, sub=1)
block("bed-headboard", WALL, WALL + 0.12, cy - FRAME_W / 2 - 0.05, cy + FRAME_W / 2 + 0.05, FLOOR + 0.08, FLOOR + 1.15, fabric, 0.045, sub=1)
top = FLOOR + FRAME_H + 0.22
for k, dy in enumerate(() if os.environ.get("BED_PILLOWS") != "1" else (-0.40, 0.40)):  # made/bedding.py inflates them
    p = block(f"bed-pillow-{k}", -0.35, 0.35, -0.25, 0.25, -0.06, 0.06, linen, 0.05, sub=2, puff=0.2)
    p.rotation_euler = (0.0, math.radians(-38), math.radians(2 if k else -3))
    p.location = (WALL + 0.12 + 0.24, cy + dy, top + 0.17)
bpy.ops.export_scene.gltf(filepath=os.path.join(HERE, "bed.glb"), export_format="GLB")
print("wrote bed.glb; mattress top", round(top, 3))
