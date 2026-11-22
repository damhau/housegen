// The neighbours' roofs drawn (#144): faces by plane, tile rows along the eave, caps on ridges and hips, gutters at
// the eaves, the solar modules on their quads, and the cover the kit picks without the backend's spec.
import test from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import {
  COVERS, LOW_PITCH, capTriangles, flatFamily, gutterTriangles, moduleMatrix, planRoofs, roofChoice, roofEdges, roofFaces,
  roofPitch, specPanels, uvOf, validSpec,
} from "../roofs.js";
import { ROOF_TINTS } from "../neighbours.js";

/** A gabled roof over x 0–w, z 0–d: eaves at `eave` along x (north and south), the ridge along x at `ridge`. */
function gabled({ w = 12, d = 8, eave = 6, ridge = 9 } = {}) {
  const pts = [[w, eave, 0], [0, eave, 0], [0, eave, d], [w, eave, d], [w, ridge, d / 2], [0, ridge, d / 2]];
  const [ne, nw, sw, se, re, rw] = [0, 1, 2, 3, 4, 5];
  return { pts, roof: [ne, rw, nw, ne, re, rw, re, sw, rw, re, se, sw] };
}

/** A hipped roof over x 0–12, z 0–8: eaves at 6 all round, the ridge from x 4 to 8 at 8. */
function hipped() {
  const pts = [[12, 6, 0], [0, 6, 0], [0, 6, 8], [12, 6, 8], [8, 8, 4], [4, 8, 4]];
  const [ne, nw, sw, se, re, rw] = [0, 1, 2, 3, 4, 5];
  return { pts, roof: [ne, rw, nw, ne, re, rw, re, sw, rw, re, se, sw, nw, rw, sw, se, re, ne] };
}

const flatBox = () => ({ pts: [[0, 7, 0], [0, 7, 10], [10, 7, 10], [10, 7, 0]], roof: [0, 1, 2, 0, 2, 3] });
const near = (a, b, eps = 1e-6) => Math.abs(a - b) <= eps;

test("a gabled roof: two pitched faces, each with its eave frame (u level along it, v up the slope from it)", () => {
  const { pts, roof } = gabled();
  const faces = roofFaces(pts, roof);
  assert.equal(faces.length, 2);
  for (const f of faces) {
    assert.equal(f.level, false);
    assert.ok(near(f.slope, THREE.MathUtils.radToDeg(Math.atan2(3, 4)), 1e-6), `slope ${f.slope}`);
    assert.ok(near(f.T.y, 0), "T is level");
    assert.ok(f.B.y > 0, "B goes up the slope");
    // the eave at v = 0, the ridge 5 m up the slope (a 4 m run, a 3 m rise)
    const vs = f.tris.flat().map((p) => uvOf(f, p)[1]);
    assert.ok(near(Math.min(...vs), 0) && near(Math.max(...vs), 5, 1e-6), `v ${Math.min(...vs)}…${Math.max(...vs)}`);
  }
  // seen from the south (the south face's outside), u grows eastward: to the right
  const south = faces.find((f) => f.n.z > 0);
  assert.ok(uvOf(south, [12, 6, 8])[0] > uvOf(south, [0, 6, 8])[0]);
  assert.ok(near(roofPitch(faces), 36.87, 0.01));
});

test("caps on the ridge and the hips, never on the eaves or the verges; gutters on the eaves only", () => {
  const g = roofEdges(roofFaces(gabled().pts, gabled().roof));
  assert.equal(g.ridges.length, 1, "the ridge");
  const r = g.ridges[0];
  assert.ok(near(Math.abs(r.a[0] - r.b[0]), 12) && near(r.a[1], 9) && near(r.b[1], 9));
  assert.equal(g.eaves.length, 2, "north and south eaves, not the verges");
  for (const e of g.eaves) assert.ok(near(e.a[1], 6) && near(e.b[1], 6) && near(Math.abs(e.a[0] - e.b[0]), 12));

  const h = roofEdges(roofFaces(hipped().pts, hipped().roof));
  assert.equal(h.ridges.length, 5, "the ridge and four hips");
  assert.equal(h.eaves.length, 4, "an eave all round");

  const f = roofEdges(roofFaces(flatBox().pts, flatBox().roof));
  assert.deepEqual([f.ridges.length, f.eaves.length], [0, 0]);
});

test("a ridge one face holds in two pieces (cut at a corner of its own) still gets its cap", () => {
  // the south face's ridge edge split at x = 5 by an extra corner: a T-junction with the north face's whole edge
  const pts = [[12, 6, 0], [0, 6, 0], [0, 6, 8], [12, 6, 8], [12, 9, 4], [0, 9, 4], [5, 9, 4]];
  const [ne, nw, sw, se, re, rw, rm] = [0, 1, 2, 3, 4, 5, 6];
  const roof = [ne, rw, nw, ne, re, rw, rm, sw, rw, rm, se, sw, re, se, rm];
  const { ridges } = roofEdges(roofFaces(pts, roof));
  const length = ridges.reduce((s, r) => s + Math.hypot(r.b[0] - r.a[0], r.b[2] - r.a[2]), 0);
  assert.ok(near(length, 12, 0.01), `ridge caps over ${length} m`);
});

test("a level eave cut in pieces is one gutter; a short one gets none", () => {
  const pts = [[12, 6, 8], [7, 6, 8], [0, 6, 8], [0, 9, 4], [12, 9, 4]];
  const roof = [0, 4, 1, 1, 4, 3, 1, 3, 2];
  const { eaves } = roofEdges(roofFaces(pts, roof));
  assert.equal(eaves.length, 1);
  assert.ok(near(Math.abs(eaves[0].a[0] - eaves[0].b[0]), 12, 1e-6));
  const short = roofEdges(roofFaces([[1, 6, 8], [0, 6, 8], [0, 9, 4], [1, 9, 4]], [0, 3, 2, 0, 2, 1]));
  assert.equal(short.eaves.length, 0);
});

test("a roof plane far from the origin, its triangles tilted by the lidar's noise, stays one face", () => {
  const { pts, roof } = gabled();
  const far = pts.map(([x, y, z]) => [x + 150, y, z - 120]);
  far.push([far[1][0], far[1][1] + 0.05, far[1][2]]); // the north-west corner 5 cm higher for one triangle only
  const noisy = roof.slice();
  noisy[2] = 6;
  const faces = roofFaces(far, noisy);
  assert.equal(faces.length, 2, faces.map((f) => f.tris.length).join(", "));
});

test("a roof whose triangles are listed twice (swissBUILDINGS3D) is drawn once and keeps its eaves", () => {
  const { pts, roof } = hipped();
  const twice = [...pts, ...pts], again = roof.map((i) => i + pts.length);
  const faces = roofFaces(twice, [...roof, ...again]);
  assert.equal(faces.reduce((s, f) => s + f.tris.length, 0), roof.length / 3);
  const { ridges, eaves } = roofEdges(faces);
  assert.deepEqual([ridges.length, eaves.length], [5, 4]);
});

test("a cap lies over both faces, a gutter hangs out from the eave and under it", () => {
  const faces = roofFaces(gabled().pts, gabled().roof);
  const { ridges, eaves } = roofEdges(faces);
  const cap = capTriangles(ridges[0]);
  for (let k = 0; k < cap.length; k += 3) {
    const p = [cap[k], cap[k + 1], cap[k + 2]];
    // over a ridge the roof is the lower of its two planes: a point over it is over one of them
    const over = Math.max(...faces.map((f) => f.n.x * p[0] + f.n.y * p[1] + f.n.z * p[2] - f.d));
    assert.ok(over > 0.015, `cap point ${p} ${over} m over the roof`);
  }
  const e = eaves.find((x) => x.f.n.z > 0); // the south eave, at z = 8
  const gutter = gutterTriangles(e);
  for (let k = 0; k < gutter.length; k += 3) {
    assert.ok(gutter[k + 2] >= 8 - 1e-6 && gutter[k + 2] <= 8.14, `gutter z ${gutter[k + 2]}`);
    assert.ok(gutter[k + 1] < 6 && gutter[k + 1] > 5.8, `gutter y ${gutter[k + 1]}`);
  }
});

test("a module's box: as large as its quad, its top on it, its face toward the sky, whichever way the corners turn", () => {
  const quad = [[0, 6, 8], [1.7, 6, 8], [1.7, 6.6, 7.2], [0, 6.6, 7.2]]; // on the south face, 1.7 × 1.0 m
  for (const q of [quad, [...quad].reverse()]) {
    const m = moduleMatrix(q);
    const x = new THREE.Vector3(), y = new THREE.Vector3(), z = new THREE.Vector3();
    m.extractBasis(x, y, z);
    const sizes = [x.length(), z.length()].sort((a, b) => a - b);
    assert.ok(near(sizes[0], 1.0, 1e-6) && near(sizes[1], 1.7, 1e-6), `sizes ${sizes}`);
    assert.ok(near(y.length(), 0.04, 1e-6) && y.y > 0, "0.04 m deep, +y up");
    assert.ok(m.determinant() > 0, "not mirrored");
    const top = new THREE.Vector3(0, 0.5, 0).applyMatrix4(m);
    assert.ok(top.distanceTo(new THREE.Vector3(0.85, 6.3, 7.6)) < 1e-6, `top at ${top.toArray()}`);
  }
});

test("the spec: its covers, flats and colours taken, anything else left to the kit; its panels through the frame", () => {
  const spec = validSpec({ cover: "clay-beaver", flat: "green", colour: "#804020", family: "brown", year: 1975, pv_kw: null });
  assert.equal(spec.cover, "clay-beaver");
  assert.equal(spec.flat, "green");
  assert.equal(spec.family, "brown");
  assert.ok(near(spec.colour[0], 128 / 255) && near(spec.colour[2], 32 / 255));
  assert.deepEqual(validSpec({ cover: "thatch", flat: "lawn", colour: "red", family: "pink" }), { cover: null, flat: null, colour: null, family: null });
  assert.equal(validSpec(null), null);
  const quad = [[1, 2, 3], [2, 2, 3], [2, 3, 3], [1, 3, 3]];
  const panels = specPanels({ panels: [quad, [[0, 0, 0]], [[0, 0, 0], [1, 0, 0], [1, 0, NaN], [0, 0, 1]]] }, (x, y, z) => [x + 10, y, -z]);
  assert.deepEqual(panels, [[[11, 2, -3], [12, 2, -3], [12, 3, -3], [11, 3, -3]]]);
  assert.deepEqual(specPanels(null), []);
});

test("without a spec: a cover of the photographed family, drawn by the hash; standing seam on a low pitch", () => {
  const terracotta = [0.62, 0.42, 0.34], grey = [0.48, 0.48, 0.47];
  const picks = (seen) => new Set(Array.from({ length: 60 }, (_, i) => roofChoice({ id: `h${i}` }, { photographed: { tiled: seen } }).cover));
  for (const c of picks(terracotta)) assert.ok(["clay-interlocking", "clay-beaver"].includes(c), c);
  assert.ok(picks(terracotta).has("clay-interlocking"));
  for (const c of picks(grey)) assert.ok(["fibre-cement", "concrete-tile", "metal-seam"].includes(c), c);
  const choice = roofChoice({ id: "same" }, { photographed: { tiled: terracotta } });
  assert.deepEqual(roofChoice({ id: "same" }, { photographed: { tiled: terracotta } }), choice, "the same house, the same roof");
  assert.equal(choice.source, "kit");
  assert.ok(ROOF_TINTS[choice.family].length);
  assert.equal(roofChoice({ id: "low" }, { photographed: { tiled: terracotta }, slope: LOW_PITCH - 3 }).cover, "metal-seam");
  // a house the photo does not show takes a photographed neighbour's family
  assert.equal(roofChoice({ id: "unseen" }, { neighbourhood: [grey] }).family, roofChoice({ id: "x" }, { photographed: { tiled: grey } }).family);
  // the spec's fields win, the rest is the kit's
  const spec = roofChoice({ id: "s", spec: { cover: "metal-seam", flat: null, colour: "#336699", family: "grey" } }, { photographed: { tiled: terracotta } });
  assert.equal(spec.cover, "metal-seam");
  assert.equal(spec.source, "spec");
  assert.ok(near(spec.colour[2], 0x99 / 255));
  assert.ok(["gravel", "green", "membrane"].includes(spec.flat));
  assert.equal(roofChoice({ id: "s", spec: { family: "anthracite" } }).family, "anthracite");
});

test("a level face photographed: green when it is, a membrane when dark, else gravel", () => {
  assert.equal(flatFamily([0.36, 0.42, 0.25]), "green");
  assert.equal(flatFamily([0.2, 0.2, 0.21]), "membrane");
  assert.equal(flatFamily([0.62, 0.6, 0.56]), "gravel");
});

test("planRoofs: a mesh a finish, the caps and gutters, the modules; the spec's houses and the kit's counted", () => {
  const offset = (r, dx) => ({ ...r, pts: r.pts.map(([x, y, z]) => [x + dx, y, z]) });
  const houses = [
    { id: "a", source: "swissbuildings3d", ...gabled(), spec: { cover: "clay-interlocking", colour: "#8a5540" },
      panels: [[[2, 6.3, 7.4], [3.7, 6.3, 7.4], [3.7, 6.9, 6.6], [2, 6.9, 6.6]]] },
    { id: "b", source: "swissbuildings3d", ...offset(hipped(), 20) },
    { id: "c", source: "lidar", ...offset(flatBox(), 40), spec: { flat: "green" } },
  ];
  const plan = planRoofs(houses, { photographed: new Map([[houses[1], { tiled: [0.5, 0.5, 0.49] }]]) });
  assert.deepEqual([plan.stats.houses, plan.stats.spec, plan.stats.kit, plan.stats.panels], [3, 2, 1, 1]);
  assert.equal(plan.stats.covers["clay-interlocking"], 1);
  assert.equal(plan.stats.flats.green, 1);
  assert.ok(plan.stats.ridges >= 12 + 4, `ridges ${plan.stats.ridges} m`);
  assert.ok(COVERS.includes(plan.choices.get(houses[1]).cover));
  const group = plan.meshes();
  const names = group.children.map((m) => m.name);
  assert.ok(names.includes("Surroundings: clay-interlocking roofs") && names.includes("Surroundings: green roofs"), names.join(", "));
  assert.ok(names.includes("Surroundings: ridge caps and gutters"));
  const modules = group.children.find((m) => m.isInstancedMesh);
  assert.equal(modules.count, 1);
  // each roof vertex wears its metric uv and its house's colour, a fifth of the way to its grey
  const clay = group.children.find((m) => m.name === "Surroundings: clay-interlocking roofs");
  assert.equal(clay.geometry.attributes.uv.count, clay.geometry.attributes.position.count);
  const c = new THREE.Color().setRGB(0x8a / 255, 0x55 / 255, 0x40 / 255, THREE.SRGBColorSpace);
  const grey = 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b;
  assert.ok(near(clay.geometry.attributes.color.getX(0), c.r + (grey - c.r) * 0.2, 1e-6));
  assert.ok(clay.geometry.attributes.color.getX(0) < c.r);
});
