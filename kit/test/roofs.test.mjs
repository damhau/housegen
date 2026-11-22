// The neighbours' roofs drawn (#144): faces by plane, tile rows along the eave, caps on ridges and hips, gutters at
// the eaves, the solar modules on their quads, and the cover the kit picks without the backend's spec; flat roofs'
// parapets and equipment (#146).
import test from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import {
  COVERS, LOW_PITCH, PROPS, capTriangles, flatFamily, gutterTriangles, housingRun, mixHash, moduleMatrix, parapetEdges,
  parapetTriangles, planRoofs, propPieces, roofChoice, roofEdges, roofFaces, roofPitch, specPanels, specProps, uvOf,
  validSpec,
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
  assert.ok(names.includes("Surroundings: ridge caps, gutters and copings"));
  assert.ok(names.includes("Surroundings: parapets and roof housings"), "the flat roof's parapet");
  assert.ok(plan.stats.parapets >= 40 - 1, `parapets ${plan.stats.parapets} m`);
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

/** Triangles (3 floats a corner) → their corners. */
const corners = (t) => Array.from({ length: t.length / 3 }, (_, i) => [t[i * 3], t[i * 3 + 1], t[i * 3 + 2]]);

test("the backend's hash mix: the same numbers as geo/roofs.py's _mix", () => {
  // from Python: roofs._mix(roofs.house_hash(id)) for these ids
  assert.equal(mixHash(1132219421), 1239943596); // lidar-12
  assert.equal(mixHash(1132219422), 1675561047); // lidar-13
  assert.equal(mixHash(1377134745), 4109883920);
});

test("a flat roof's parapet: all round its open edges, mitred, its coping over it; none where the roof goes on", () => {
  const faces = roofFaces(flatBox().pts, flatBox().roof);
  const runs = parapetEdges(faces);
  assert.equal(runs.length, 1);
  assert.equal(runs[0].closed, true);
  assert.equal(runs[0].pts.length, 4, "the diagonal both triangles hold has none");
  const t = parapetTriangles(runs[0], 0.3);
  const outer = corners(t.outer), inner = corners(t.inner), coping = corners(t.coping);
  // the outer face on the façade, from just under the roof to 0.3 m over it
  for (const [x, y, z] of outer) {
    assert.ok(near(x, 0) || near(x, 10) || near(z, 0) || near(z, 10), `outer ${x}, ${z}`);
    assert.ok(near(y, 7 - 0.02) || near(y, 7.3));
  }
  // the inner face 0.25 m in, mitred at the corners (never outside the roof)
  for (const [x, , z] of inner) {
    assert.ok(x >= 0.25 - 1e-9 && x <= 9.75 + 1e-9 && z >= 0.25 - 1e-9 && z <= 9.75 + 1e-9, `inner ${x}, ${z}`);
    assert.ok(near(x, 0.25) || near(x, 9.75) || near(z, 0.25) || near(z, 9.75));
  }
  // the coping 3 cm thick on the upstand, 4 cm over it on both sides, its drips down from it
  assert.ok(near(Math.max(...coping.map((p) => p[1])), 7.33));
  assert.ok(near(Math.min(...coping.map((p) => p[0])), -0.04));
  assert.ok(near(Math.max(...coping.map((p) => p[0])), 10.04));
  assert.ok(near(Math.min(...coping.map((p) => p[1])), 7.33 - 0.06));

  // two flat roofs side by side at the same height: one parapet round both, none along their joint
  const pair = { pts: [[0, 7, 0], [0, 7, 10], [10, 7, 10], [10, 7, 0], [20, 7, 10], [20, 7, 0]], roof: [0, 1, 2, 0, 2, 3, 3, 2, 4, 3, 4, 5] };
  const both = parapetEdges(roofFaces(pair.pts, pair.roof));
  assert.equal(both.length, 1);
  assert.ok(!both[0].pts.some((p) => near(p[0], 10) && p[2] > 0.1 && p[2] < 9.9));
});

test("a parapet stops where a higher part of the house rises, and goes on along a lower one", () => {
  // a low wing (x 0–10 at 4 m) against a tall block (x 10–20 at 10 m): the wing's edge on the block has none, the
  // block's edge over the wing has one
  const pts = [[0, 4, 0], [0, 4, 10], [10, 4, 10], [10, 4, 0], [10, 10, 0], [10, 10, 10], [20, 10, 10], [20, 10, 0]];
  const roof = [0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7];
  const runs = parapetEdges(roofFaces(pts, roof));
  const wing = runs.filter((r) => near(r.pts[0][1], 4)), block = runs.filter((r) => near(r.pts[0][1], 10));
  assert.equal(block.length, 1);
  assert.equal(block[0].closed, true, "the block all round");
  assert.equal(wing.length, 1);
  assert.equal(wing[0].closed, false, "the wing's run stops at the block");
  const along = (r) => r.pts.slice(1).reduce((s, p, i) => s + Math.hypot(p[0] - r.pts[i][0], p[2] - r.pts[i][2]), 0);
  assert.ok(near(along(wing[0]), 30), `wing ${along(wing[0])} m`);
  assert.ok(!wing[0].pts.some((p) => near(p[0], 10) && p[2] > 0.1 && p[2] < 9.9), "nothing along the block's wall");

  // a mansard's flat top: its slopes fall from its edges, so it has none; a pitched roof's tiny level bit neither
  const mansard = {
    pts: [[0, 6, 0], [12, 6, 0], [12, 6, 10], [0, 6, 10], [2, 9, 2], [10, 9, 2], [10, 9, 8], [2, 9, 8]],
    roof: [4, 7, 6, 4, 6, 5, 0, 4, 5, 0, 5, 1, 1, 5, 6, 1, 6, 2, 2, 6, 7, 2, 7, 3, 3, 7, 4, 3, 4, 0],
  };
  assert.deepEqual(parapetEdges(roofFaces(mansard.pts, mansard.roof)), []);
  const bit = { pts: [[0, 7, 0], [0, 7, 1], [1, 7, 1], [1, 7, 0]], roof: [0, 1, 2, 0, 2, 3] };
  assert.deepEqual(parapetEdges(roofFaces(bit.pts, bit.roof)), [], "1 m² of level roof: noise");
});

test("the spec's equipment: known kinds with a place and a size, turned with the frame", () => {
  const spec = { props: [
    { kind: "hvac", at: [10, 5, 20], size: [2, 1, 1.2], yaw: 0.3 },
    { kind: "pool", at: [0, 0, 0], size: [1, 1, 1], yaw: 0 },
    { kind: "vent", at: [0, 0], size: [0.1, 0.1, 0.5], yaw: 0 },
    { kind: "hatch", at: [0, 0, 0], size: [1, 0, 0.5], yaw: 0 },
  ] };
  assert.deepEqual(specProps(spec).map((p) => p.kind), ["hvac"]);
  assert.deepEqual(specProps({}), []);
  // a frame turned by 30°: the middle through it, the yaw 30° more
  const th = Math.PI / 6, toScene = (x, y, z) => [Math.cos(th) * x + Math.sin(th) * z, y - 2, -Math.sin(th) * x + Math.cos(th) * z];
  const [p] = specProps(spec, toScene);
  assert.deepEqual(p.at, toScene(10, 5, 20));
  assert.ok(near(p.yaw, 0.3 + th, 1e-9), `yaw ${p.yaw}`);
  assert.deepEqual(p.size, [2, 1, 1.2]);
  assert.equal(PROPS.length, 9);
});

test("each piece of equipment stands on the roof (its foot a little in it), within its footprint, as high as its size", () => {
  const at = [5, 7, 5];
  const sizes = {
    vent: [[0.14, 0.14, 0.7], [0.32, 0.32, 0.4]], stack: [[0.3, 0.3, 1.6]], "skylight-dome": [[1.2, 1.8, 0.55]],
    "skylight-flat": [[1, 1.5, 0.35]], hatch: [[1, 1.2, 0.5]], hvac: [[3, 1.2, 1.4], [1.6, 1.6, 1.1]],
    housing: [[2.8, 4, 2.6]], rail: [[6, 0.05, 1.1]], walkway: [[7.3, 1, 0.04]],
  };
  for (const [kind, list] of Object.entries(sizes)) for (const size of list) for (const yaw of [0, 0.7]) {
    const pieces = propPieces({ kind, at, size, yaw });
    assert.ok(pieces.length >= 1, kind);
    const [w, d, h] = size, c = Math.cos(yaw), s = Math.sin(yaw);
    // a cap stands over its pipe by half its width at most, a lid by 3 cm, a door by 2 cm
    const over = kind === "vent" || kind === "stack" ? 0.25 * w : 0.03;
    let top = -Infinity, foot = Infinity;
    for (const p of pieces) {
      top = Math.max(top, p.at[1] + p.size[1]);
      foot = Math.min(foot, p.at[1]);
      const dx = p.at[0] - at[0], dz = p.at[2] - at[2], u = dx * c - dz * s, v = dx * s + dz * c;
      assert.ok(Math.abs(u) + p.size[0] / 2 <= w / 2 + over + 1e-9, `${kind} across: ${u} ± ${p.size[0] / 2}`);
      assert.ok(Math.abs(v) + p.size[2] / 2 <= d / 2 + over + 1e-9, `${kind} along: ${v} ± ${p.size[2] / 2}`);
      assert.ok(p.size.every((x) => x > 0), `${kind} ${p.size}`);
      assert.equal(p.yaw, yaw);
    }
    if (kind === "housing") continue;
    assert.ok(near(top, at[1] + h, 0.01), `${kind}: top ${top - at[1]} for ${h}`);
    assert.ok(foot < at[1] && foot >= at[1] - 0.06, `${kind}: foot ${foot - at[1]}`);
  }
  // a walkway of slabs 0.5 m (a centimetre apart), a rail's posts 1.5 m apart at most
  const slabs = propPieces({ kind: "walkway", at, size: [7.5, 1, 0.04], yaw: 0 });
  assert.equal(slabs.length, 15 * 2);
  assert.ok(near(slabs[0].size[0], 0.49, 1e-9));
  const posts = propPieces({ kind: "rail", at, size: [6, 0.05, 1.1], yaw: 0 }).filter((p) => p.size[0] < 0.1);
  assert.equal(posts.length, 5);
});

test("a housing: walls on its footprint and a coping round its top, its inside on the left of its corners", () => {
  const p = { kind: "housing", at: [5, 7, 5], size: [3, 4, 2.6], yaw: 0.4 };
  const run = housingRun(p);
  assert.equal(run.pts.length, 4);
  // the inside on the left walking the corners (seen from above, z down): a negative shoelace
  const area = run.pts.reduce((s, a, i) => { const b = run.pts[(i + 1) % 4]; return s + a[0] * b[2] - b[0] * a[2]; }, 0);
  assert.ok(near(area, -2 * 12, 1e-9), `area ${area}`);
  const t = parapetTriangles(run, 2.6);
  assert.ok(near(Math.max(...corners(t.coping).map((q) => q[1])), 7 + 2.6 + 0.03));
  for (const [x, , z] of corners(t.outer)) {
    const dx = x - 5, dz = z - 5, u = dx * Math.cos(0.4) - dz * Math.sin(0.4), v = dx * Math.sin(0.4) + dz * Math.cos(0.4);
    assert.ok(near(Math.abs(u), 1.5, 1e-9) || near(Math.abs(v), 2, 1e-9), `wall corner ${u}, ${v}`);
  }
});

test("planRoofs: a block's equipment instanced by shape and finish, a housing's walls with the parapets", () => {
  const block = { id: "block", source: "swissbuildings3d", pts: [[0, 20, 0], [0, 20, 30], [40, 20, 30], [40, 20, 0]], roof: [0, 1, 2, 0, 2, 3],
    spec: { flat: "gravel" } };
  block.props = specProps({ props: [
    { kind: "vent", at: [5, 20, 5], size: [0.14, 0.14, 0.7], yaw: 0 },
    { kind: "vent", at: [6, 20, 5], size: [0.3, 0.3, 0.4], yaw: 0 },
    { kind: "skylight-dome", at: [12, 20, 10], size: [1.2, 1.2, 0.55], yaw: 0 },
    { kind: "hvac", at: [20, 20, 12], size: [3, 1.2, 1.4], yaw: 0 },
    { kind: "housing", at: [30, 20, 20], size: [3, 4, 2.6], yaw: 0 },
    { kind: "walkway", at: [20, 20, 16], size: [6, 0.5, 0.04], yaw: 0 },
  ] });
  const plan = planRoofs([block]);
  assert.equal(plan.stats.props, 6);
  assert.equal(plan.stats.parapets, 140);
  const group = plan.meshes();
  const equipment = group.children.filter((m) => m.userData.part === "roof equipment");
  assert.ok(equipment.length >= 4 && equipment.length <= 8, equipment.map((m) => m.name).join(", "));
  // pipe + cap, pipe + mushroom, curb + dome, 2 rails + body + 2 fans, door, 12 slabs
  assert.equal(equipment.reduce((s, m) => s + m.count, 0), 2 + 2 + 2 + 5 + 1 + 12);
  for (const m of equipment) assert.ok(m.instanceColor, `${m.name}: a colour a piece`);
  const walls = group.children.find((m) => m.userData.part === "parapets");
  const look = walls.geometry.attributes.wallLook;
  assert.equal(look.count, walls.geometry.attributes.position.count);
  // a parapet's foot is never darkened; a housing's is (it stands on the roof)
  let low = 0;
  for (let i = 0; i < look.count; i++) if (look.getW(i) < 0.1) low++;
  assert.ok(low > 0 && low < look.count / 2, `${low} of ${look.count}`);
  const gravel = group.children.find((m) => m.userData.roof === "gravel");
  assert.equal(gravel.material.customProgramCacheKey(), "housekit-roofs-gravel");
});
