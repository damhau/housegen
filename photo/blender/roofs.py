"""The neighbours' roofs from the backend's roof spec (#145): `roof_spec` on each building of context/buildings.json
(#143), drawn as the viewer draws them (#144, kit/roofs.js; its sizes, colours and shares are copied here). Each
pitched face wears its cover, a CC0 scan at its real size with its courses along the eave, white-balanced, graded and
tinted by the house's colour, a little weathered; ridges and hips get a cap, eaves a gutter; level faces are gravel,
sedum or a membrane in a colour of their own; each quad of `panels` is a solar module (dark glass, an aluminium frame,
a faint grid of cells). One material per finish and the houses' colours as a face attribute: a street of houses costs
about thirty materials, not one per house."""

import math
import os

import bpy
from mathutils import Vector

import dress

FLAT_ROOF = math.cos(math.radians(5))  # kit/neighbours.js: a face more level than 5 deg is flat
WALL_LIKE = 0.35  # kit/context.js drawnAs: a lidar roof's face steeper than 70 deg wears the house's render
UP = Vector((0.0, 0.0, 1.0))

# kit/roofs.js LOOKS and kit/finishes.js: the scan (Poly Haven or ambientCG, CC0, under PHOTO_ASSETS/textures), the
# metres one repeat covers, the saturation it is graded to, the roughness; the standing seam is drawn by the shader
COVERS = {
    "clay-interlocking": ("clay_roof_tiles_03", 2.6, 0.35, 0.85),
    "clay-beaver": ("RoofingTiles002", 1.15, 0.0, 0.85),
    "fibre-cement": ("grey_roof_tiles_02", 1.8, 0.0, 0.9),
    "concrete-tile": ("RoofingTiles006", 2.5, 0.2, 0.8),
    "metal-seam": (None, 1.0, 1.0, 0.62),
}
FLATS = {"gravel": ("gravel_floor_02", 2.0, 1.0), "green": ("leafy_grass", 2.0, 1.0), "membrane": (None, 2.0, 0.75)}
FLAT_TINTS = {
    "gravel": ("#aaa497", "#a09a8f", "#b3ada1", "#968f86"),
    "green": ("#6e7643", "#77794a", "#636d3d", "#7a7250"),
    "membrane": ("#5e6165", "#7d8185", "#a9adb0"),
}
GUTTERS = {"zinc": "#9a9fa2", "copper": "#7f5a43", "anthracite": "#45484c"}
RIDGE_ANGLE = math.cos(math.radians(8))  # two faces closer than this make no ridge
MIN_EAVE = 1.2  # metres: a shorter level edge gets no gutter
CAP = {"tile": (0.15, 0.08), "metal": (0.08, 0.04)}  # half the width of a ridge cap, its rise over the ridge
CAP_LIFT = 0.02
GUTTER = ((0, -0.04), (0.13, -0.02), (0.13, -0.08), (0.1, -0.13), (0.035, -0.13), (0, -0.09))  # (out, up), metres
MODULE = {"thick": 0.04, "frame": 0.035, "cell": 0.166, "line": 0.004}
# kit/neighbours.js ROOF_TINTS and kit/roofs.js BY_FAMILY, LOW_PITCH: what the kit draws for a field the spec lacks
ROOF_TINTS = {
    "terracotta": ("#86523f", "#8f5843", "#7a4b3b"),
    "brown": ("#6b3d2e", "#5a3a2e", "#7a4632"),
    "grey": ("#666560", "#6e6d69", "#5a5d61"),
    "anthracite": ("#4a4c4f", "#525458", "#44464a"),
}
BY_FAMILY = {
    "terracotta": (("clay-interlocking", 0.75), ("clay-beaver", 0.25)),
    "brown": (("clay-interlocking", 0.6), ("clay-beaver", 0.25), ("concrete-tile", 0.15)),
    "grey": (("fibre-cement", 0.45), ("concrete-tile", 0.4), ("metal-seam", 0.15)),
    "anthracite": (("fibre-cement", 0.45), ("concrete-tile", 0.3), ("metal-seam", 0.25)),
}
LOW_PITCH = 15
# kit/roofs.js: a pitched cover's colour moves this far to its own grey; the palette's terracottas read fresh orange
# in the sun next to the aerial photo
WEATHER = 0.2


def srgb(hex_, shade=1.0):
    """A #rrggbb colour, its sRGB values times `shade` (as the kit shades a house's tint), in linear RGB."""
    return tuple(_lin(min(255.0, int(hex_[k:k + 2], 16) * shade)) for k in (1, 3, 5))


def weathered(c):
    """A linear colour WEATHER of the way to its luminance (Rec. 709)."""
    y = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    return tuple(v + (y - v) * WEATHER for v in c)


def colour_of(spec):
    c = spec.get("colour")
    if isinstance(c, str) and len(c) == 7 and c[0] == "#":
        try:
            return srgb(c)
        except ValueError:
            return None
    return None


def drawn_as(b):
    """kit/context.js drawnAs: the triangles (index triples) a building is drawn with as roof and as wall. The lidar's
    roofs hold near-vertical faces (the step between two roof heights), which wear the render; the tops of its eaves
    are roof, their fascia and soffit wall."""
    p, roof, wall = b["positions"], b["roof"], b["wall"]
    tri = [list(roof[t:t + 3]) for t in range(0, len(roof), 3)]
    walls = [list(wall[t:t + 3]) for t in range(0, len(wall), 3)]
    if b.get("source") != "lidar":
        return tri, walls

    def at(i):
        return Vector(p[3 * i:3 * i + 3])

    def level(ids):
        a, b_, c = (at(i) for i in ids)
        n = (b_ - a).cross(c - a)
        return abs(n.y) / (n.length or 1.0), n

    roofs = []
    for ids in tri:
        (walls if level(ids)[0] < WALL_LIKE else roofs).append(ids)
    eave = b.get("eave") or []
    for t in range(0, len(eave), 3):
        ids = list(eave[t:t + 3])
        lv, n = level(ids)
        (roofs if n.y > 0 and lv >= WALL_LIKE else walls).append(ids)
    return roofs, walls


def house_hash(id_):
    """kit/neighbours.js houseHash: the same house draws the same choices in the viewer and in the photo."""
    a = 7
    for ch in str(id_):
        a = (a * 31 + ord(ch)) & 0xFFFFFFFF
    return a


def choice(b, spec, faces):
    """kit/roofs.js roofChoice for a house with a spec: its cover, its flat finish, and the linear colours of its
    tiles, its level faces and its gutters. A field the spec lacks is drawn as the viewer draws it."""
    h = house_hash(b["id"])
    family = spec.get("family") if spec.get("family") in ROOF_TINTS else "terracotta"
    cover = spec.get("cover")
    if cover not in COVERS:
        pitched = [f for f in faces.values() if not f.level]
        area = sum(f.area for f in pitched)
        pitch = sum(f.slope * f.area for f in pitched) / area if area else 30.0
        cover = "metal-seam" if pitch < LOW_PITCH else _share(BY_FAMILY[family], h >> 5)
    flat = spec.get("flat")
    if flat not in FLATS:
        flat = _share((("gravel", 0.72), ("green", 0.18), ("membrane", 0.1)), h >> 3)
    tile = colour_of(spec)
    if tile is None:
        tints = ROOF_TINTS[family]
        tile = srgb(tints[(h >> 13) % len(tints)], 0.94 + ((h >> 4) % 13) / 100)
    tints = FLAT_TINTS[flat]
    level = srgb(tints[(h >> 11) % len(tints)], 0.95 + ((h >> 2) % 11) / 100)
    g = ((h >> 13) % 1000) / 1000
    if b.get("source") == "lidar":
        gutter = "anthracite" if g < 0.6 else "zinc"
    else:
        gutter = "copper" if g < 0.25 else "zinc"
    return cover, flat, weathered(tile), level, srgb(GUTTERS[gutter])


def _share(shares, bits):
    r = (bits % 1000) / 1000
    for name, share in shares:
        r -= share
        if r < 0:
            return name
    return shares[0][0]


class Plane:
    """A face of the roof: its normal (up), its offset, its area and slope (degrees), and for a pitched face the level
    direction along its eave, the direction up its slope and the lowest values it reaches along them (where its
    courses start)."""

    def __init__(self, total):
        self.n = total.normalized()
        self.area = total.length / 2
        self.level = self.n.z > FLAT_ROOF
        self.slope = math.degrees(math.acos(min(1.0, self.n.z)))
        along = UP.cross(self.n)
        self.along = along.normalized() if along.length > 1e-6 else Vector((1.0, 0.0, 0.0))
        self.up = self.n.cross(self.along).normalized()
        self.u0 = self.v0 = 1e9
        self.d, self.k = 0.0, 0

    def grow(self, pts):
        for q in pts:
            self.u0, self.v0 = min(self.u0, q.dot(self.along)), min(self.v0, q.dot(self.up))
            self.d = (self.d * self.k + self.n.dot(q)) / (self.k + 1)
            self.k += 1

    def uv(self, q):
        return q.dot(self.along) - self.u0, q.dot(self.up) - self.v0

    def below(self, q):
        return self.n.dot(q) - self.d < -0.02


def snap(q):
    """A corner on a 2 cm grid (kit/roofs.js cornerKey): corners closer than that are one corner."""
    return round(q.x * 50), round(q.y * 50), round(q.z * 50)


def unique(pts, tris):
    """The triangles once each: swissBUILDINGS3D's roofs list every triangle twice."""
    seen, out = set(), []
    for ids in tris:
        k = tuple(sorted(snap(pts[i]) for i in ids))
        if k not in seen:
            seen.add(k)
            out.append(ids)
    return out


def planes(pts, tris, within_deg=4.0):
    """The roof's faces: triangles joined across a shared edge while their normals stay within `within_deg` (the
    survey's slopes are not quite flat), then pieces of one slope that a T junction leaves apart. The face of each
    triangle (None for a degenerate one), the faces, and the edges (snapped ends: (triangle, corner) on it)."""
    normals = []
    for ids in tris:
        a, b, c = (pts[k] for k in ids)
        n = (b - a).cross(c - a)
        normals.append(None if n.length < 2e-4 else (n if n.z >= 0 else -n))
    edges = {}
    for t, ids in enumerate(tris):
        if normals[t] is None:
            continue
        for i in range(3):
            ka, kb = snap(pts[ids[i]]), snap(pts[ids[(i + 1) % 3]])
            if ka != kb:
                edges.setdefault((ka, kb) if ka < kb else (kb, ka), []).append((t, i))
    parent = list(range(len(tris)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    near = math.cos(math.radians(within_deg))
    for uses in edges.values():
        for i, _ in uses:
            for j, _ in uses:
                if i < j and normals[i].normalized().dot(normals[j].normalized()) > near:
                    parent[find(i)] = find(j)

    def totals():
        out = {}
        for t in range(len(tris)):
            if normals[t] is not None:
                k = find(t)
                out[k] = out.get(k, Vector()) + normals[t]
        return out

    total = totals()
    roots = list(total)
    for x, i in enumerate(roots):
        for j in roots[x + 1:]:
            ni, nj = total[i].normalized(), total[j].normalized()
            if find(i) != find(j) and ni.dot(nj) > near and abs(ni.dot(pts[tris[j][0]] - pts[tris[i][0]])) < 0.15:
                parent[find(j)] = find(i)
    keys = [None if normals[t] is None else find(t) for t in range(len(tris))]
    faces = {k: Plane(n) for k, n in totals().items()}
    for t, k in enumerate(keys):
        if k is not None:
            faces[k].grow([pts[i] for i in tris[t]])
    return keys, faces, edges


def creases(pts, tris, keys, faces, edges):
    """kit/roofs.js roofEdges: the ridges and hips (an edge two pitched faces share where each falls away from the
    other, not a valley: (a, b, (face, its far corner)) for each side) and the eaves (a level edge of a pitched face
    that no other face holds, at least MIN_EAVE long, the face rising from it: (a, b, face))."""
    ridges, eaves, open_ = [], [], []
    for uses in edges.values():
        ends = []
        for t, i in uses:
            ids = tris[t]
            ends.append((keys[t], pts[ids[i]], pts[ids[(i + 1) % 3]], pts[ids[(i + 2) % 3]]))
        if len(ends) >= 2:
            one = ends[0]
            other = next((e for e in ends if e[0] != one[0]), None)
            if other is None:
                continue
            f1, f2 = faces[one[0]], faces[other[0]]
            if f1.level or f2.level or f1.n.dot(f2.n) > RIDGE_ANGLE:
                continue
            if f1.below(other[3]) and f2.below(one[3]):
                ridges.append((one[1], one[2], (f1, one[3]), (f2, other[3])))
            continue
        k, a, b, c = ends[0]
        f = faces[k]
        if f.level:
            continue
        length, top = (b - a).length, max(a.z, b.z)
        if c.z < top - 0.05:
            open_.append(ends[0])
        elif length >= MIN_EAVE and abs(a.z - b.z) <= max(0.05, 0.02 * length) and c.z > top + 0.1:
            eaves.append((a, b, f))
    return ridges + split_ridges(open_, faces), eaves


def split_ridges(open_, faces):
    """Ridges whose two faces the survey cut at different corners (one face's edge in pieces along the other's): the
    open edges of two faces on one line, over the stretch they share."""
    out = []
    for x, p in enumerate(open_):
        for q in open_[x + 1:]:
            if p[0] == q[0]:
                continue
            f1, f2 = faces[p[0]], faces[q[0]]
            if f1.level or f2.level or f1.n.dot(f2.n) > RIDGE_ANGLE:
                continue
            a, d = p[1], p[2] - p[1]
            length = d.length
            if length < 0.3:
                continue
            d = d / length
            (ta, da), (tb, db) = (_along(r, a, d) for r in (q[1], q[2]))
            if da > 0.03 or db > 0.03:
                continue
            t0, t1 = max(0.0, min(ta, tb)), min(length, max(ta, tb))
            if t1 - t0 < 0.3 or not f1.below(q[3]) or not f2.below(p[3]):
                continue
            out.append((a + d * t0, a + d * t1, (f1, p[3]), (f2, q[3])))
    return out


def _along(r, a, d):
    """How far along the line (a, unit d) the point r lies, and how far off it."""
    w = r - a
    t = w.dot(d)
    return t, (w - d * t).length


def sweep(profile, a, b, closed):
    """A profile (offsets from the line a-b) swept along it: (vertices, quads as index lists)."""
    n = len(profile)
    verts = [a + o for o in profile] + [b + o for o in profile]
    return verts, [[k, (k + 1) % n, n + (k + 1) % n, n + k] for k in range(n if closed else n - 1)]


def cap(a, b, side1, side2, width, height):
    """A ridge cap over the edge a-b: a low rounded tile, its edges on both faces, lifted off them."""
    e = (b - a).normalized()

    def side(f, c):
        s = f.n.cross(e).normalized()
        return -s if s.dot(c - a) < 0 else s

    (f1, c1), (f2, c2) = side1, side2
    s1, s2, up = side(f1, c1), side(f2, c2), (f1.n + f2.n).normalized()
    profile = [s1 * width + f1.n * CAP_LIFT, s1 * width * 0.5 + f1.n * (CAP_LIFT + height * 0.6),
               up * (CAP_LIFT + height), s2 * width * 0.5 + f2.n * (CAP_LIFT + height * 0.6),
               s2 * width + f2.n * CAP_LIFT]
    return sweep(profile, a, b, False)


def gutter(a, b, f):
    """The gutter under the eave a-b: a half-round hung just out of it, toward where the face looks."""
    out = Vector((f.n.x, f.n.y, 0.0)).normalized()
    return sweep([out * o + UP * u for o, u in GUTTER], a, b, True)


def module(quad):
    """A solar module whose glass top is the quad: (vertices, [(face, "glass" | "frame", uv per corner or None)]).
    The frame is MODULE["frame"] wide on the top, its sides MODULE["thick"] deep under it; the glass's UVs count its
    cells, as many as fit."""
    a, b, c, d = quad
    if (b - a).cross(d - a).z < 0:
        b, d = d, b
    n = (b - a).cross(d - a).normalized()
    corners, fw = [a, b, c, d], MODULE["frame"]
    inner = []
    for i, q in enumerate(corners):
        prev, nxt = corners[i - 1], corners[(i + 1) % 4]
        inner.append(q + (nxt - q).normalized() * fw + (prev - q).normalized() * fw)
    below = [q - n * MODULE["thick"] for q in corners]
    faces = []
    for i in range(4):
        j = (i + 1) % 4
        faces.append(([i, j, 4 + j, 4 + i], "frame", None))
        faces.append(([8 + i, 8 + j, j, i], "frame", None))
    wu = max(round((inner[1] - inner[0]).length / MODULE["cell"]), 1)
    wv = max(round((inner[3] - inner[0]).length / MODULE["cell"]), 1)
    faces.append(([4, 5, 6, 7], "glass", [(0.0, 0.0), (wu, 0.0), (wu, wv), (0.0, wv)]))
    return corners + inner + below, faces


def _socket(sockets, name):
    """The socket of that name the node uses now (a Mix node keeps hidden ones of each data type under one name)."""
    return next(s for s in sockets if s.name == name and s.enabled)


class _Graph:
    def __init__(self, nt):
        self.nt = nt

    def _set(self, sock, v):
        if isinstance(v, (int, float)):
            sock.default_value = v
        elif isinstance(v, tuple):
            sock.default_value = (*v, 1.0) if len(v) == 3 else v
        elif v is not None:
            self.nt.links.new(v, sock)

    def op(self, name, a, b=None, c=None):
        n = self.nt.nodes.new("ShaderNodeMath")
        n.operation = name
        for k, v in enumerate((a, b, c)):
            self._set(n.inputs[k], v)
        return n.outputs[0]

    def mix(self, fac, a, b, blend="MIX"):
        n = self.nt.nodes.new("ShaderNodeMix")
        n.data_type, n.blend_type = "RGBA", blend
        self._set(_socket(n.inputs, "Factor"), fac)
        self._set(_socket(n.inputs, "A"), a)
        self._set(_socket(n.inputs, "B"), b)
        return _socket(n.outputs, "Result")

    def mul(self, a, b):
        return self.mix(1.0, a, b, "MULTIPLY")

    def grey(self, v):
        n = self.nt.nodes.new("ShaderNodeCombineXYZ")
        for k in ("X", "Y", "Z"):
            self._set(n.inputs[k], v)
        return n.outputs[0]

    def uv(self, size=1.0):
        node = self.nt.nodes.new("ShaderNodeUVMap")
        node.uv_map = "UVMap"
        s = self.nt.nodes.new("ShaderNodeVectorMath")
        s.operation = "MULTIPLY"
        s.inputs[1].default_value = (1 / size, 1 / size, 1.0)
        self.nt.links.new(node.outputs["UV"], s.inputs[0])
        return s.outputs["Vector"]

    def uv_parts(self, size=1.0):
        sep = self.nt.nodes.new("ShaderNodeSeparateXYZ")
        self.nt.links.new(self.uv(size), sep.inputs[0])
        return sep.outputs["X"], sep.outputs["Y"]

    def tint(self):
        t = self.nt.nodes.new("ShaderNodeAttribute")
        t.attribute_type, t.attribute_name = "GEOMETRY", "tint"
        return t.outputs["Color"]

    def bump(self, height, distance, normal=None):
        n = self.nt.nodes.new("ShaderNodeBump")
        n.inputs["Distance"].default_value = distance
        self._set(n.inputs["Height"], height)
        if normal is not None:
            self.nt.links.new(normal, n.inputs["Normal"])
        return n.outputs["Normal"]

    def scan(self, scan, size, saturation=1.0):
        """kit/finishes.js `match: true`: the scan's colour graded to `saturation` and white-balanced (its mean is
        white), and its normal map, on UVs in metres (one repeat over `size`); None when the scan is not under
        PHOTO_ASSETS/textures."""
        d = os.path.join(dress.TEX, scan)
        if not all(os.path.exists(os.path.join(d, f"{k}.jpg")) for k in ("diff", "nor_gl")):
            return None
        vector = self.uv(size)
        diff, nor = (self.nt.nodes.new("ShaderNodeTexImage") for _ in range(2))
        diff.image = dress.image(os.path.join(d, "diff.jpg"))
        nor.image = dress.image(os.path.join(d, "nor_gl.jpg"), True)
        for t in (diff, nor):
            self.nt.links.new(vector, t.inputs["Vector"])
        mean = [float(c) for c in dress.mean_linear(diff.image)]
        colour = diff.outputs["Color"]
        if saturation < 1.0:
            bw = self.nt.nodes.new("ShaderNodeRGBToBW")
            self.nt.links.new(colour, bw.inputs[0])
            colour = self.mix(saturation, self.grey(bw.outputs[0]), colour)
            lum = 0.2126 * mean[0] + 0.7152 * mean[1] + 0.0722 * mean[2]
            mean = [lum + saturation * (c - lum) for c in mean]
        white = self.mul(colour, tuple(1.0 / max(c, 1e-3) for c in mean))
        normal = self.nt.nodes.new("ShaderNodeNormalMap")
        normal.uv_map = "UVMap"
        self.nt.links.new(nor.outputs["Color"], normal.inputs["Color"])
        return white, normal.outputs["Normal"]


def _material(name):
    m = bpy.data.materials.new(name)
    nt = m.node_tree
    return m, _Graph(nt), next(n for n in nt.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")


def _lin(v):
    c = v / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


# kit/roofs.js drawnTextures "seam", 512 px a metre: two 0.5 m pans a little apart in sheen, and across each seam
# (metres off it) a lit flank, the lit top, a shaded flank (sRGB values); the mean of a metre, which white-balances it
SEAM_PANS = (238, 229)
SEAM = ((-7 / 512, -3 / 512, 205), (-3 / 512, 3 / 512, 252), (3 / 512, 7 / 512, 188))
SEAM_MEAN = sum(_lin(v) for v in SEAM_PANS) / 2 + sum(
    (b - a) * (2 * _lin(v) - sum(_lin(p) for p in SEAM_PANS)) for a, b, v in SEAM)


def cover_material(cover):
    """A pitched roof's cover: the scan graded and white-balanced, times the house's colour (the `tint` face
    attribute); the standing seam, painted steel, drawn: 0.5 m pans, a seam up the slope between them, a lit top
    between a lit and a shaded flank."""
    scan, size, saturation, roughness = COVERS[cover]
    m, g, p = _material(f"roof-{cover}")
    p.inputs["Roughness"].default_value = roughness
    maps = g.scan(scan, size, saturation) if scan else None
    if maps:
        colour, normal = maps
        g.nt.links.new(g.mul(colour, g.tint()), p.inputs["Base Color"])
        g.nt.links.new(normal, p.inputs["Normal"])
        return m
    u, _ = g.uv_parts()
    pan = g.op("FLOORED_MODULO", g.op("FLOOR", g.op("DIVIDE", u, 0.5)), 2.0)
    d = g.op("SUBTRACT", u, g.op("MULTIPLY", g.op("ROUND", g.op("DIVIDE", u, 0.5)), 0.5))
    first, second = (_lin(v) for v in SEAM_PANS)
    value = g.op("MULTIPLY_ADD", pan, second - first, first)
    for a, b, v in SEAM:
        inside = g.op("MULTIPLY", g.op("GREATER_THAN", d, a), g.op("LESS_THAN", d, b))
        value = g.op("MULTIPLY_ADD", inside, g.op("SUBTRACT", _lin(v), value), value)
    top, flank = SEAM[1][1], SEAM[2][1] - SEAM[2][0]
    off = g.op("SUBTRACT", g.op("ABSOLUTE", d), top)
    rise = g.op("MAXIMUM", g.op("SUBTRACT", 1.0, g.op("DIVIDE", off, flank)), 0.0)
    g.nt.links.new(g.mul(g.tint(), g.grey(g.op("DIVIDE", value, SEAM_MEAN))), p.inputs["Base Color"])
    g.nt.links.new(g.bump(g.op("MINIMUM", rise, 1.0), 0.012), p.inputs["Normal"])
    return m


def flat_material(kind):
    """A flat roof's finish, times its colour (the `tint` face attribute): washed gravel or sedum (the ground's scans,
    white-balanced), or a membrane drawn in 1 m rolls lapped across, faintly mottled."""
    scan, size, roughness = FLATS[kind]
    m, g, p = _material(f"roof-flat-{kind}")
    p.inputs["Roughness"].default_value = roughness
    maps = g.scan(scan, size) if scan else None
    if maps:
        colour, normal = maps
        g.nt.links.new(g.mul(colour, g.tint()), p.inputs["Base Color"])
        g.nt.links.new(normal, p.inputs["Normal"])
        return m
    noise = g.nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 3.0
    g.nt.links.new(g.uv(), noise.inputs["Vector"])
    _, v = g.uv_parts()
    lap = g.op("LESS_THAN", g.op("FRACT", v), 0.016)
    mottle = g.op("MULTIPLY_ADD", noise.outputs["Fac"], 0.12, 0.94)
    shade = _lin(196) / _lin(232)
    value = g.op("MULTIPLY", mottle, g.op("MULTIPLY_ADD", lap, shade - 1.0, 1.0))
    g.nt.links.new(g.mul(g.tint(), g.grey(value)), p.inputs["Base Color"])
    g.nt.links.new(g.bump(g.op("SUBTRACT", 1.0, lap), 0.004), p.inputs["Normal"])
    return m


def detail_material(_kind):
    """The ridge caps and the gutters: their colour (the `tint` face attribute), a matt glaze or a dull metal."""
    m, g, p = _material("roof-details")
    g.nt.links.new(g.tint(), p.inputs["Base Color"])
    p.inputs["Roughness"].default_value = 0.65
    p.inputs["Metallic"].default_value = 0.1
    return m


def panel_material(kind):
    """A module's dark glass, reflecting the sky, with a faint grid of cells (MODULE["line"] wide lines at 45 %), or
    its aluminium frame."""
    m, g, p = _material(f"panel-{kind}")
    if kind == "frame":
        p.inputs["Base Color"].default_value = (*srgb("#b9bdc1"), 1.0)
        p.inputs["Metallic"].default_value = 0.7
        p.inputs["Roughness"].default_value = 0.42
        return m
    u, v = g.uv_parts()
    half = MODULE["line"] / MODULE["cell"] / 2

    def on(x):
        return g.op("LESS_THAN", g.op("ABSOLUTE", g.op("SUBTRACT", g.op("FRACT", g.op("ADD", x, 0.5)), 0.5)), half)

    line = g.op("MULTIPLY", g.op("MAXIMUM", on(u), on(v)), 0.45)
    g.nt.links.new(g.mix(line, srgb("#0f1722"), srgb("#4a5662")), p.inputs["Base Color"])
    p.inputs["Metallic"].default_value = 0.15
    p.inputs["Roughness"].default_value = 0.12
    return m


class Slots:
    """The neighbours mesh's material slots: the roof finishes are made on first use and appended to `mats`."""

    def __init__(self, mats):
        self.mats, self.index = mats, {}

    def __call__(self, key):
        if key not in self.index:
            kind, name = key
            make = {"cover": cover_material, "flat": flat_material, "detail": detail_material, "panel": panel_material}
            self.index[key] = len(self.mats)
            self.mats.append(make[kind](name))
        return self.index[key]


def dress_roof(mesh, slots, base, pts, tris, b, place):
    """One building's roof from its spec (`b["roof_spec"]`) into `mesh` (outside.Mesh): its triangles `tris` (indices
    into `pts`, the building's points, in the mesh from index `base`) in their cover or flat finish, ridge caps,
    gutters and the modules of `panels` (`place` takes the spec's local points to Blender). Counts what it drew."""
    tris = unique(pts, tris)
    keys, faces, edges = planes(pts, tris)
    cover, flat, tile, level, rain = choice(b, b["roof_spec"], faces)
    for ids, key in zip(tris, keys):
        f = faces.get(key)
        if f is None:
            continue
        if f.level:
            mesh.face([base + k for k in ids], slots(("flat", flat)), [(pts[k].x, pts[k].y) for k in ids], level)
        else:
            mesh.face([base + k for k in ids], slots(("cover", cover)), [f.uv(pts[k]) for k in ids], tile)
    ridges, eaves = creases(pts, tris, keys, faces, edges)
    metal = cover == "metal-seam"
    width, height = CAP["metal" if metal else "tile"]
    capped = tile if metal else tuple(c * 0.9 for c in tile)
    details = slots(("detail", "roof"))
    for a, b_, s1, s2 in ridges:
        mesh.strip(*cap(a, b_, s1, s2, width, height), details, capped, True)
    for a, b_, f in eaves:
        mesh.strip(*gutter(a, b_, f), details, rain, False)
    count = 0
    for quad in (b["roof_spec"].get("panels") or []):
        if not (isinstance(quad, list) and len(quad) == 4 and all(isinstance(q, list) and len(q) == 3 for q in quad)):
            continue
        verts, parts = module([place(*q) for q in quad])
        at = mesh.add(verts)
        for f, kind, uvs in parts:
            mesh.face([at + k for k in f], slots(("panel", kind)), uvs or [(0.0, 0.0)] * len(f))
        count += 1
    return {"ridges": len(ridges), "gutters": len(eaves), "panels": count}
