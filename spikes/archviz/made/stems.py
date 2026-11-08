"""Branches for a vase (made here; no CC0 models of these). Writes made/stems/<kind>-<seed>.glb and adds them to
made/objects.json with the role "stems".

    uv run -q --no-project --python 3.11 --with bpy==5.0.1 python -I made/stems.py

Each stands on its origin (the vase's mouth), +Z up, metres, about 0.6 m tall; the rules scale it to its vase.
"""

import json
import math
import os
import random

import bpy
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "stems")
SEEDS = (1, 2)
#        kind: tone, stem colour
KINDS = {"silver": ("green", (0.30, 0.22, 0.16)), "olive": ("green", (0.28, 0.24, 0.17)), "ruscus": ("green", (0.14, 0.20, 0.09)),
         "lunaria": ("neutral", (0.42, 0.33, 0.21)), "billy": ("warm", (0.28, 0.34, 0.17)), "bunny": ("neutral", (0.55, 0.45, 0.29)),
         "twig": ("neutral", (0.09, 0.065, 0.05)), "blossom": ("cool", (0.11, 0.07, 0.055)), "pampas": ("warm", (0.62, 0.52, 0.36))}


class Part:
    """One material's triangles."""

    def __init__(self, name, rgb, rough, sss=0.0, thin=0.0):
        self.name, self.rgb, self.rough, self.sss, self.thin = name, rgb, rough, sss, thin
        self.verts, self.faces = [], []

    def add(self, vs, fs):
        base = len(self.verts)
        self.verts.extend(vs)
        self.faces.extend(tuple(base + i for i in f) for f in fs)


def axes(normal, up):
    z = normal.normalized()
    x = up.cross(z)
    x = x.normalized() if x.length > 1e-4 else Vector((1, 0, 0))
    return x, z.cross(x), z


def disc(part, c, normal, up, r, cup=0.25, n=12):
    """A round leaf, its rim lifted."""
    x, y, z = axes(normal, up)
    vs = [c - z * (cup * r * 0.3)]
    for i in range(n):
        a = 2 * math.pi * i / n
        rr = r * (1.0 + 0.06 * math.cos(2 * a))
        vs.append(c + x * (math.cos(a) * rr) + y * (math.sin(a) * rr * 0.92) + z * (cup * r * 0.5))
    part.add(vs, [(0, 1 + i, 1 + (i + 1) % n) for i in range(n)])


def blade(part, base, along, normal, length, width, curl=0.15, n=5):
    """A pointed leaf from `base` in direction `along`, folded a little along its rib and curling back at the tip."""
    a = along.normalized()
    side = normal.cross(a)
    side = side.normalized() if side.length > 1e-4 else Vector((1, 0, 0))
    up = a.cross(side).normalized()
    vs = []
    for i in range(n + 1):
        t = i / n
        w = width * math.sin(math.pi * min(t * 1.08, 1.0)) ** 0.8 / 2
        rib = base + a * (length * t) - up * (curl * length * t * t)
        vs += [rib - side * w + up * (w * 0.25), rib, rib + side * w + up * (w * 0.25)]
    fs = []
    for i in range(n):
        k = i * 3
        fs += [(k, k + 3, k + 4, k + 1), (k + 1, k + 4, k + 5, k + 2)]
    part.add(vs, fs)


def ball(part, c, rx, ry, rz, bumps=None, nu=12, nv=8):
    """An ellipsoid, its surface pushed in and out by `bumps(unit direction)` if any."""
    vs, fs = [], []
    for j in range(nv + 1):
        v = math.pi * j / nv
        for i in range(nu):
            u = 2 * math.pi * i / nu
            d = Vector((math.sin(v) * math.cos(u), math.sin(v) * math.sin(u), math.cos(v)))
            k = 1.0 + (bumps(d) if bumps else 0.0)
            vs.append(c + Vector((d.x * rx * k, d.y * ry * k, d.z * rz * k)))
    for j in range(nv):
        for i in range(nu):
            a, b = j * nu + i, j * nu + (i + 1) % nu
            fs.append((a, b, b + nu, a + nu))
    part.add(vs, fs)


def stem(az, lean, length, droop=0.08, n=12, power=1.4):
    """Up and out from the origin."""
    pts = []
    for i in range(n):
        t = i / (n - 1)
        r = math.sin(lean) * length * t ** power
        h = length * t * math.cos(lean * 0.6) - droop * t ** 3 * lean
        pts.append(Vector((math.cos(az) * r, math.sin(az) * r, h)))
    return pts


def fan(rng, count, lean, length, droop=0.08):
    return [stem(2 * math.pi * s / count + rng.uniform(-0.35, 0.35), rng.uniform(*lean), rng.uniform(*length), droop)
            for s in range(count)]


def branch_off(rng, pts, at, length, lean=0.7):
    """A side shoot from point `at` of a stem, following it and leaving it by `lean`."""
    p = pts[at]
    d = (pts[min(at + 1, len(pts) - 1)] - pts[at - 1]).normalized()
    a = rng.uniform(0, 2 * math.pi)
    side = (d.cross(Vector((math.cos(a), math.sin(a), 0.1)))).normalized()
    out = (d * math.cos(lean) + side * math.sin(lean)).normalized()
    n = 7
    return [p + (out * (t / (n - 1)) + d * (0.3 * (t / (n - 1)) ** 2)) * length for t in range(n)]


def silver(rng, leaf):
    stems = fan(rng, 9, (0.25, 0.6), (0.45, 0.7))
    for pts in stems:
        for i in range(2, 12):
            t = i / 11
            d = (pts[min(i + 1, 11)] - pts[i - 1]).normalized()
            side = d.cross(Vector((0, 0, 1)))
            side = (side if side.length > 1e-3 else Vector((1, 0, 0))).normalized()
            side = Matrix.Rotation(rng.uniform(-0.5, 0.5) + (i % 2) * math.pi / 2, 3, d) @ side
            r = 0.028 * (1.15 - 0.6 * t) * rng.uniform(0.85, 1.1)
            for sgn in (1, -1):
                nrm = Vector((rng.uniform(-.3, .3), rng.uniform(-.3, .3), 0.6)) + side * sgn * 0.5
                disc(leaf, pts[i] + side * sgn * (r * 1.05), nrm, d, r)
    return stems


def narrow(rng, leaf, count, lean, length, every, size, width, droop=0.1):
    """Stems with pointed leaves in pairs, smaller toward the tip."""
    stems = fan(rng, count, lean, length, droop)
    for pts in stems:
        for i in range(2, len(pts)):
            t = i / (len(pts) - 1)
            d = (pts[min(i + 1, len(pts) - 1)] - pts[i - 1]).normalized()
            for k in range(every):
                a = rng.uniform(0, 2 * math.pi)
                side = d.cross(Vector((math.cos(a), math.sin(a), 0.2))).normalized()
                out = (d * 0.55 + side * 0.85).normalized()
                ln = size * (1.15 - 0.55 * t) * rng.uniform(0.8, 1.15)
                blade(leaf, pts[i] + side * 0.002, out, Vector((rng.uniform(-.3, .3), rng.uniform(-.3, .3), 1.0)), ln, ln * width)
    return stems


def olive(rng, leaf):
    return narrow(rng, leaf, 8, (0.3, 0.62), (0.5, 0.72), 3, 0.062, 0.2, droop=0.14)


def ruscus(rng, leaf):
    return narrow(rng, leaf, 10, (0.3, 0.72), (0.42, 0.66), 2, 0.07, 0.42, droop=0.2)


def lunaria(rng, leaf):
    """Honesty: thin branching stems holding pale papery discs."""
    stems = fan(rng, 7, (0.08, 0.42), (0.5, 0.72), 0.02)
    out = list(stems)
    for pts in stems:
        for at in (5, 7, 9):
            sub = branch_off(rng, pts, at, rng.uniform(0.09, 0.17))
            out.append(sub)
            for q in sub[2:]:
                n = Vector((rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-0.3, 0.3)))
                disc(leaf, q, n, Vector((0, 0, 1)), rng.uniform(0.017, 0.026), cup=0.02)
    return out


def billy(rng, leaf):
    """Billy buttons: straight stems, a yellow ball on each."""
    stems = fan(rng, 13, (0.06, 0.4), (0.4, 0.68), 0.0)
    for pts in stems:
        ball(leaf, pts[-1], 0.016, 0.016, 0.016, lambda d: 0.05 * math.sin(d.x * 19) * math.sin(d.y * 17) * math.sin(d.z * 23))
    return stems


def bunny(rng, leaf):
    """Dried grass heads: soft pale ovals on thin stems."""
    stems = fan(rng, 16, (0.08, 0.52), (0.42, 0.68), 0.05)
    for pts in stems:
        c = pts[-1]
        ln = rng.uniform(0.026, 0.038)
        for _ in range(55):  # a soft oval of little hairs, lighter than a solid ball
            d = Vector((rng.gauss(0, 1), rng.gauss(0, 1), rng.gauss(0.4, 1))).normalized()
            base = c + Vector((d.x * 0.4 * ln * 0.5, d.y * 0.4 * ln * 0.5, d.z * ln * 0.8))
            blade(leaf, base, d + Vector((0, 0, 0.6)), Vector((d.y, -d.x, 0.1)), ln * 0.75, 0.005, curl=0.0, n=2)
    return stems


def twig(rng, leaf):
    """Bare branching twigs with small buds."""
    stems = fan(rng, 6, (0.12, 0.5), (0.5, 0.75), 0.02)
    out = list(stems)
    for pts in stems:
        for at in (4, 6, 8):
            sub = branch_off(rng, pts, at, rng.uniform(0.12, 0.24), lean=0.55)
            out.append(sub)
            for q in sub[2::2]:
                ball(leaf, q, 0.0035, 0.0035, 0.0055, nu=6, nv=4)
    return out


def blossom(rng, leaf):
    """Twigs in flower: five pale petals around each bud, in clusters along the twig."""
    stems = fan(rng, 6, (0.14, 0.52), (0.5, 0.74), 0.02)
    out = list(stems)
    for pts in stems:
        subs = [branch_off(rng, pts, at, rng.uniform(0.1, 0.2), lean=0.6) for at in (4, 6, 8, 10)]
        out += subs
        for line in [pts[6:], *subs]:
            for q in line[1:]:
                for _ in range(rng.randint(1, 2)):
                    c = q + Vector((rng.uniform(-.014, .014), rng.uniform(-.014, .014), rng.uniform(-.012, .012)))
                    n = Vector((rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-0.1, 1)))
                    x, y, _z = axes(n, Vector((0, 0, 1)))
                    for k in range(5):
                        a = 2 * math.pi * k / 5
                        disc(leaf, c + (x * math.cos(a) + y * math.sin(a)) * 0.0085, n, x, 0.0072, cup=0.3, n=6)
    return out


def pampas(rng, leaf):
    """Tall plumes: many fine hairs on the last third of arching stems."""
    stems = fan(rng, 8, (0.12, 0.42), (0.55, 0.72), 0.16)
    for pts in stems:
        for i in range(7, len(pts)):
            t = (i - 7) / (len(pts) - 8)
            d = (pts[min(i + 1, len(pts) - 1)] - pts[i - 1]).normalized()
            for _ in range(42):
                a = rng.uniform(0, 2 * math.pi)
                side = d.cross(Vector((math.cos(a), math.sin(a), 0.1))).normalized()
                out = (d * 0.8 + side * 0.6).normalized()
                ln = rng.uniform(0.04, 0.085) * (1.0 - 0.45 * t)
                base = pts[i] + (pts[min(i + 1, len(pts) - 1)] - pts[i]) * rng.random()
                blade(leaf, base, out, Vector((side.y, -side.x, 0.3)), ln, 0.0038, curl=0.05, n=2)
    return stems


MAKE = {"silver": (silver, (0.24, 0.32, 0.27), 0.5, 0.15, 0.0, 0.0022), "olive": (olive, (0.13, 0.17, 0.09), 0.45, 0.1, 0.0, 0.002),
        "ruscus": (ruscus, (0.055, 0.14, 0.035), 0.35, 0.12, 0.0, 0.002), "lunaria": (lunaria, (0.78, 0.74, 0.62), 0.45, 0.25, 0.35, 0.0011),
        "billy": (billy, (0.78, 0.47, 0.05), 0.75, 0.0, 0.0, 0.0014), "bunny": (bunny, (0.72, 0.62, 0.44), 0.8, 0.2, 0.0, 0.0008),
        "twig": (twig, (0.22, 0.2, 0.09), 0.6, 0.0, 0.0, 0.0028), "blossom": (blossom, (0.86, 0.72, 0.74), 0.55, 0.3, 0.1, 0.0026),
        "pampas": (pampas, (0.72, 0.6, 0.42), 0.8, 0.2, 0.0, 0.0014)}


def make(kind, seed):
    fn, rgb, rough, sss, thin, stem_r = MAKE[kind]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    leaf = Part(f"stems-{kind}", rgb, rough, sss, thin)
    stems = fn(random.Random(seed * 7919 + len(kind)), leaf)
    me = bpy.data.meshes.new(f"{kind}-leaves")
    me.from_pydata([tuple(v) for v in leaf.verts], [], leaf.faces)
    me.update()
    for poly in me.polygons:
        poly.use_smooth = True
    ob = bpy.data.objects.new(f"{kind}-leaves", me)
    m = bpy.data.materials.new(leaf.name)
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = (*rgb, 1)
    p.inputs["Roughness"].default_value = rough
    p.inputs["Subsurface Weight"].default_value = sss
    p.inputs["Transmission Weight"].default_value = thin
    ob.data.materials.append(m)
    bpy.context.scene.collection.objects.link(ob)
    cu = bpy.data.curves.new(f"{kind}-stems", "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = stem_r
    cu.bevel_resolution = 1
    for pts in stems:
        sp = cu.splines.new("POLY")
        sp.points.add(len(pts) - 1)
        for q, v in zip(sp.points, pts):
            q.co = (*v, 1)
    st = bpy.data.objects.new(f"{kind}-stems", cu)
    sm = bpy.data.materials.new(f"stems-{kind}-stem")
    sp_ = sm.node_tree.nodes["Principled BSDF"]
    sp_.inputs["Base Color"].default_value = (*KINDS[kind][1], 1)
    sp_.inputs["Roughness"].default_value = 0.65
    st.data.materials.append(sm)
    bpy.context.scene.collection.objects.link(st)
    bpy.context.view_layer.objects.active = st
    st.select_set(True)
    bpy.ops.object.convert(target="MESH")
    path = os.path.join(OUT, f"{kind}-{seed}.glb")
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB")
    print(f"{kind}-{seed}: {len(leaf.faces)} faces, {os.path.getsize(path) // 1024} KB", flush=True)


def main():
    os.makedirs(OUT, exist_ok=True)
    entries = []
    for kind, (tone, _) in KINDS.items():
        for seed in SEEDS:
            make(kind, seed)
            entries.append({"id": f"{kind}-{seed}", "src": "made", "role": "stems", "tone": tone, "name": kind})
    path = os.path.join(HERE, "objects.json")
    cat = [e for e in json.load(open(path, encoding="utf-8")) if e["role"] != "stems"]
    json.dump(cat + entries, open(path, "w", encoding="utf-8"), indent=0)


if __name__ == "__main__":
    main()
