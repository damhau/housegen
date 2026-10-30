"""Eucalyptus branches for a vase (made here: no CC0 model fits). Writes made/eucalyptus.glb.

    uv run --no-project --python 3.11 --with bpy python made/eucalyptus.py [seed]

Stems curve up and out from the vase's mouth (origin, +Z up, metres); round silver-dollar leaves in
opposite pairs, shrinking toward the tips, each a slightly cupped disc turned to the light.
"""

import math
import os
import random
import sys

import bpy
from mathutils import Matrix, Vector

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 7
rng = random.Random(seed)
bpy.ops.wm.read_factory_settings(use_empty=True)


def material(name, rgb, rough, sss=0.0):
    m = bpy.data.materials.new(name)
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = (*rgb, 1)
    p.inputs["Roughness"].default_value = rough
    p.inputs["Subsurface Weight"].default_value = sss
    return m


leaf_mat = material("eucalyptus-leaf", (0.24, 0.32, 0.27), 0.5, 0.15)
stem_mat = material("eucalyptus-stem", (0.30, 0.22, 0.16), 0.6)

verts, faces = [], []


def disc(center, normal, up, r, cup=0.25, n=14):
    """A leaf: a round disc of radius r, its rim lifted by cup*r, oriented by normal and up."""
    z = normal.normalized()
    x = up.cross(z).normalized()
    y = z.cross(x)
    base = len(verts)
    verts.append(center + z * (-cup * r * 0.3))
    for i in range(n):
        a = 2 * math.pi * i / n
        rr = r * (1.0 + 0.06 * math.cos(2 * a))
        p = center + x * (math.cos(a) * rr) + y * (math.sin(a) * rr * 0.92) + z * (cup * r * 0.5)
        verts.append(p)
    for i in range(n):
        faces.append((base, base + 1 + i, base + 1 + (i + 1) % n))


stem_curves = []
for s in range(9):
    az = 2 * math.pi * s / 9 + rng.uniform(-0.3, 0.3)
    lean = rng.uniform(0.25, 0.6)
    length = rng.uniform(0.45, 0.7)
    pts = []
    for i in range(12):
        t = i / 11
        r = math.sin(lean) * length * t ** 1.4
        h = length * t * math.cos(lean * 0.6)
        droop = -0.08 * t ** 3 * lean
        pts.append(Vector((math.cos(az) * r, math.sin(az) * r, h + droop)))
    stem_curves.append(pts)
    # leaves in opposite pairs along the stem, smaller toward the tip
    for i in range(2, 12):
        t = i / 11
        p = pts[i]
        d = (pts[min(i + 1, 11)] - pts[i - 1]).normalized()
        side = d.cross(Vector((0, 0, 1)))
        if side.length < 1e-3:
            side = Vector((1, 0, 0))
        side.normalize()
        rot = Matrix.Rotation(rng.uniform(-0.5, 0.5) + (i % 2) * math.pi / 2, 3, d)
        side = rot @ side
        r = 0.028 * (1.15 - 0.6 * t) * rng.uniform(0.85, 1.1)
        for sgn in (1, -1):
            c = p + side * sgn * (r * 1.05)
            nrm = (Vector((0, 0, 1)) * 0.6 + side * sgn * 0.5 + Vector((rng.uniform(-.3, .3), rng.uniform(-.3, .3), 0))).normalized()
            disc(c, nrm, d, r)

me = bpy.data.meshes.new("eucalyptus-leaves")
me.from_pydata([tuple(v) for v in verts], [], faces)
me.update()
leaves = bpy.data.objects.new("eucalyptus-leaves", me)
leaves.data.materials.append(leaf_mat)
bpy.context.scene.collection.objects.link(leaves)
for poly in me.polygons:
    poly.use_smooth = True

cu = bpy.data.curves.new("eucalyptus-stems", "CURVE")
cu.dimensions = "3D"
cu.bevel_depth = 0.0022
cu.bevel_resolution = 2
for pts in stem_curves:
    sp = cu.splines.new("POLY")
    sp.points.add(len(pts) - 1)
    for p, q in zip(sp.points, pts):
        p.co = (*q, 1)
stems = bpy.data.objects.new("eucalyptus-stems", cu)
stems.data.materials.append(stem_mat)
bpy.context.scene.collection.objects.link(stems)
bpy.context.view_layer.objects.active = stems
stems.select_set(True)
bpy.ops.object.convert(target="MESH")
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eucalyptus.glb")
bpy.ops.export_scene.gltf(filepath=out, export_format="GLB")
print("wrote", out, len(verts), "leaf verts")
