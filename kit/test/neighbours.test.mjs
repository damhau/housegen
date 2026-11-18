// The neighbours' doors and windows (#124): in proportion to each house, on its walls, the door toward the road.
import test from "node:test";
import assert from "node:assert/strict";
import { Blockers, FLAT_ROOF, PER_WINDOW, ROOF_TINTS, facadesOf, houseHash, openingMeshes, planOpenings, roofFamily, roofLook, roofTint } from "../neighbours.js";

/**
 * A gabled box as buildings.json gives it (scene frame): `w` along x, `d` along z, walls from `foot` to `eave`, the
 * ridge along x at `ridge` (flat when ridge = eave). Walls wind outward (n = (b - a) × (c - a)).
 */
function house({ id = "test-house", source = "swissbuildings3d", x0 = 0, z0 = 0, w = 12, d = 8, foot = -1, eave = 6, ridge = 9 } = {}) {
  const pts = [], wall = [], roof = [];
  const add = (p) => pts.push(p) - 1;
  // counter-clockwise on a north-up map: NE, NW, SW, SE
  const corners = [[x0 + w, z0], [x0, z0], [x0, z0 + d], [x0 + w, z0 + d]];
  for (let k = 0; k < 4; k++) {
    const [px, pz] = corners[k], [qx, qz] = corners[(k + 1) % 4];
    const P = add([px, foot, pz]), Q = add([qx, foot, qz]), Qt = add([qx, eave, qz]), Pt = add([px, eave, pz]);
    wall.push(P, Q, Qt, P, Qt, Pt);
    const gable = ridge > eave && px === qx; // the end walls (along z) rise to the ridge
    if (gable) wall.push(Pt, Qt, add([px, ridge, (pz + qz) / 2]));
  }
  const ne = add([x0 + w, eave, z0]), nw = add([x0, eave, z0]), sw = add([x0, eave, z0 + d]), se = add([x0 + w, eave, z0 + d]);
  const re = add([x0 + w, ridge, z0 + d / 2]), rw = add([x0, ridge, z0 + d / 2]);
  roof.push(ne, rw, nw, ne, re, rw, re, sw, rw, re, se, sw);
  return { id, source, pts, wall, roof };
}

const towardSouth = (x, z, dx, dz) => (dz > 0.9 ? 6 : Infinity); // a road south of every house
const rowsOk = (plan) => plan.rows.every((r) => r.count <= Math.max(1, Math.floor(r.len / PER_WINDOW)));
const facing = (o) => o.f.n;

test("a two-storey gabled house: at most a window per 4 m and storey, one attic window a gable, one door", () => {
  const h = house();
  const plan = planOpenings(h, { road: towardSouth, blockers: new Blockers([h]) });
  const windows = plan.openings.filter((o) => o.kind === "window" || o.kind === "french");
  assert.ok(windows.length >= 4, `windows: ${windows.length}`);
  assert.ok(rowsOk(plan), JSON.stringify(plan.rows.map((r) => [r.len, r.k, r.count])));
  // the long façades (12 m) hold 3 a storey at most, over two storeys and the door
  for (const side of [-1, 1]) {
    const here = plan.openings.filter((o) => Math.abs(facing(o)[1] - side) < 0.01);
    assert.ok(here.length <= 2 * 3, `façade ${side}: ${here.length}`);
  }
  // gables: above the eaves, one window at most each
  for (const side of [-1, 1]) {
    const attic = plan.openings.filter((o) => Math.abs(facing(o)[0] - side) < 0.01 && o.y0 > 6);
    assert.ok(attic.length <= 1, `gable ${side}: ${attic.length}`);
  }
  const doors = plan.openings.filter((o) => o.kind === "door");
  assert.equal(doors.length, 1);
});

test("the door: on the façade toward the road, its foot on the ground", () => {
  const h = house({ id: "door-house" });
  const plan = planOpenings(h, { road: towardSouth, blockers: new Blockers([h]) });
  const door = plan.openings.find((o) => o.kind === "door");
  assert.ok(door, "a door");
  assert.ok(facing(door)[1] > 0.99, `door faces ${facing(door)}`);
  assert.ok(Math.abs(door.y0 - 0) < 0.05, `door foot ${door.y0}`);
  // with no road, the longest façade (12 m: north or south)
  const plain = planOpenings(h, { blockers: new Blockers([h]) }).openings.find((o) => o.kind === "door");
  assert.ok(Math.abs(facing(plain)[1]) > 0.99, `door faces ${facing(plain)}`);
});

test("on a slope: no window below the ground, the door on the ground at its spot", () => {
  const groundAt = (x) => 0.25 * x; // 3 m over the house
  const h = house({ id: "slope-house", foot: -2, eave: 9, ridge: 11 });
  const plan = planOpenings(h, { groundAt, road: towardSouth, blockers: new Blockers([h]) });
  assert.ok(plan.openings.length > 4);
  for (const o of plan.openings) {
    const half = (o.w / 2) * Math.abs(o.f.t[0]), x = o.ref.p[0] + 0.5 * o.f.n[0];
    const g = Math.max(groundAt(x - half), groundAt(x + half));
    if (o.kind === "door") assert.ok(o.y0 >= g - 0.01 && o.y0 <= g + 0.1, `door at ${o.y0}, ground ${g}`);
    else assert.ok(o.y0 >= g - 0.05, `${o.kind} at ${o.y0}, ground ${g}`);
  }
  assert.ok(rowsOk(plan));
});

test("nothing above the wall: every opening and its reveal under the wall's top where it stands", () => {
  for (const [k, id] of ["a", "b", "c", "d", "e", "f"].entries()) {
    const d = 7 + k, eave = 3 + (k % 3) * 2.8, ridge = eave + 2 + k * 0.4;
    const h = house({ id, w: 9 + k, d, eave, ridge });
    const plan = planOpenings(h, { road: towardSouth, blockers: new Blockers([h]) });
    for (const o of plan.openings) {
      const reach = o.w / 2 + o.band;
      // the gables (east, west) rise to the ridge at z = d / 2
      const topAt = (z) => (Math.abs(o.f.n[0]) > 0.99 ? eave + (ridge - eave) * (1 - Math.abs(z - d / 2) / (d / 2)) : eave);
      const top = Math.abs(o.f.n[0]) > 0.99 ? Math.min(topAt(o.ref.p[2] - reach), topAt(o.ref.p[2] + reach)) : eave;
      assert.ok(o.y0 + o.h + o.band <= top + 1e-6, `${id}: ${o.kind} up to ${o.y0 + o.h + o.band}, wall ${top}`);
    }
  }
});

test("a small low volume: a garage door toward the road, else one small window; a tiny one nothing", () => {
  const garage = house({ id: "garage", w: 6, d: 3.5, foot: -0.3, eave: 2.6, ridge: 2.6 });
  const plan = planOpenings(garage, { road: towardSouth, blockers: new Blockers([garage]) });
  assert.ok(plan.small);
  assert.equal(plan.openings.length, 1);
  assert.equal(plan.openings[0].kind, "garage");
  assert.ok(facing(plan.openings[0])[1] > 0.99);
  const shed = planOpenings(garage, { blockers: new Blockers([garage]) });
  assert.equal(shed.openings.length, 1);
  assert.equal(shed.openings[0].kind, "window");
  assert.ok(shed.openings[0].w <= 0.8 && shed.openings[0].h <= 0.7);
  const tiny = house({ id: "tiny", w: 2, d: 2, foot: 0, eave: 2, ridge: 2 });
  assert.equal(planOpenings(tiny, { road: towardSouth }).openings.length, 0);
  // a garden house: 20 m², one storey under a roof 4 m up, still one opening
  const hut = house({ id: "hut", w: 5, d: 4, foot: 0, eave: 2.8, ridge: 4 });
  const hutPlan = planOpenings(hut, { road: towardSouth, blockers: new Blockers([hut]) });
  assert.ok(hutPlan.small);
  assert.ok(hutPlan.openings.length <= 1, `hut: ${hutPlan.openings.map((o) => o.kind)}`);
});

test("a façade under 2.5 m gets nothing", () => {
  const narrow = house({ id: "narrow", w: 9, d: 2.3, foot: -0.5, eave: 6, ridge: 6 });
  const plan = planOpenings(narrow, { road: towardSouth, blockers: new Blockers([narrow]) });
  assert.ok(plan.openings.every((o) => Math.abs(facing(o)[0]) < 0.5), "none on the 2.3 m ends");
});

test("deterministic by the house's id, and different from one house to the next", () => {
  const key = (plan) => plan.openings.map((o) => `${o.kind}:${o.u.toFixed(3)}:${o.y0.toFixed(3)}`).join("|");
  const a = house({ id: "lidar-1-2", source: "lidar" }), b = house({ id: "lidar-1-2", source: "lidar" });
  assert.equal(key(planOpenings(a, { road: towardSouth })), key(planOpenings(b, { road: towardSouth })));
  const others = ["x1", "x2", "x3", "x4"].map((id) => key(planOpenings(house({ id }), { road: towardSouth })));
  assert.ok(new Set(others).size > 1, "the same layout for every id");
  assert.equal(houseHash("lidar-1-2"), houseHash("lidar-1-2"));
});

test("a wall hidden by the house next door gets no window", () => {
  const a = house({ id: "left", w: 10, d: 8 }), b = house({ id: "right", x0: 10.3, w: 10, d: 8 });
  const blockers = new Blockers([a, b]);
  const plan = planOpenings(a, { road: towardSouth, blockers });
  assert.ok(plan.openings.every((o) => facing(o)[0] < 0.5), "nothing on the east wall, 0.3 m from the neighbour");
});

test("a wing drawn as a house of its own: no opening inside it, none right over its roof", () => {
  const main = house({ id: "main", w: 12, d: 8 }), wing = house({ id: "wing", x0: 3, z0: 7.5, w: 4, d: 3.5, eave: 3, ridge: 3 });
  const plan = planOpenings(main, { blockers: new Blockers([main, wing]) });
  const south = plan.openings.filter((o) => o.f.n[1] > 0.99);
  assert.ok(south.length >= 2, `south: ${south.length}`);
  for (const o of south) {
    const x = o.f.t[0] * o.u, reach = o.w / 2 + o.band;
    if (x + reach > 3 && x - reach < 7) assert.ok(o.y0 >= 3 + 0.85, `${o.kind} at x ${x.toFixed(1)}, y0 ${o.y0.toFixed(2)}`);
  }
});

test("a block and its low annex in one building: each façade's storeys up its own walls", () => {
  // Gilles' lidar-36-12 after #73: a 6.4 m block, an annex 3.3 m high on its west, one entry in buildings.json
  const block = house({ x0: 0, w: 20, d: 11, foot: -0.8, eave: 5.7, ridge: 5.7 });
  const annex = house({ x0: -7, z0: 2, w: 7, d: 6, foot: -0.8, eave: 2.6, ridge: 2.6 });
  const n = block.pts.length;
  const h = {
    id: "lidar-36-12", source: "lidar", pts: [...block.pts, ...annex.pts],
    wall: [...block.wall, ...annex.wall.map((i) => i + n)], roof: [...block.roof, ...annex.roof.map((i) => i + n)],
  };
  const plan = planOpenings(h, { blockers: new Blockers([h]) });
  const upper = plan.openings.filter((o) => o.f.n[1] < -0.99 && o.f.len > 15 && o.y0 > 3);
  assert.ok(upper.length >= 2, `the block's north façade upstairs: ${upper.length}`);
  for (const o of plan.openings.filter((o) => o.f.len < 8)) assert.ok(o.y0 + o.h < 2.6, `${o.kind} on the annex up to ${o.y0 + o.h}`);
  assert.ok(rowsOk(plan));
});

test("the lidar's stepped walls make one façade, its openings reaching back to the deepest step", () => {
  // a 14 m north wall in metre-long steps 0.4 m apart, as the lidar's outlines run before #73
  const pts = [], wall = [], roof = [];
  const add = (p) => pts.push(p) - 1;
  const quad = (a, b, foot, top) => {
    const P = add([a[0], foot, a[1]]), Q = add([b[0], foot, b[1]]), Qt = add([b[0], top, b[1]]), Pt = add([a[0], top, a[1]]);
    wall.push(P, Q, Qt, P, Qt, Pt);
  };
  const ring = [];
  for (let x = 14; x > 0; x -= 1) ring.push([x, (x % 2) * 0.4], [x - 1, (x % 2) * 0.4]);
  ring.push([0, 9], [14, 9]);
  for (let k = 0; k < ring.length; k++) quad(ring[k], ring[(k + 1) % ring.length], -0.5, 6.2);
  const top = [add([14, 6.2, 0]), add([0, 6.2, 0]), add([0, 6.2, 9]), add([14, 6.2, 9])];
  roof.push(top[0], top[1], top[2], top[0], top[2], top[3]);
  const h = { id: "lidar-steps", source: "lidar", pts, wall, roof };
  const north = facadesOf(pts, wall, 0.5).filter((f) => f.n[1] < -0.9);
  assert.equal(north.length, 1);
  assert.ok(north[0].len > 13);
  const plan = planOpenings(h, { blockers: new Blockers([h]) });
  const there = plan.openings.filter((o) => facing(o)[1] < -0.9);
  assert.ok(there.length >= 2, `north: ${there.length}`);
  assert.ok(there.every((o) => o.ref.recess <= 0.5));
  assert.ok(rowsOk(plan));
});

test("openingMeshes: a few instanced meshes, every instance placed", () => {
  const a = house({ id: "m1" }), b = house({ id: "m2", x0: 30 });
  const blockers = new Blockers([a, b]);
  const all = [a, b].flatMap((h) => planOpenings(h, { road: towardSouth, blockers }).openings);
  const group = openingMeshes(all);
  assert.ok(group.children.length <= 6);
  for (const mesh of group.children) {
    assert.ok(mesh.isInstancedMesh);
    for (const v of mesh.instanceMatrix.array) assert.ok(Number.isFinite(v));
  }
});

test("a far house's openings: the same ones, flat (no reveal, no sill), in fewer instances", () => {
  const h = house({ id: "far-house" });
  const near = planOpenings(h, { road: towardSouth, blockers: new Blockers([h]) }).openings;
  const far = planOpenings(h, { road: towardSouth, blockers: new Blockers([h]), flat: true }).openings;
  assert.deepEqual(far.map((o) => [o.kind, o.u, o.y0]), near.map((o) => [o.kind, o.u, o.y0]));
  const count = (group) => Object.fromEntries(group.children.map((m) => [m.userData.part, m.count]));
  const [n, f] = [count(openingMeshes(near)), count(openingMeshes(far))];
  assert.ok(!f.reveal, `reveals: ${f.reveal}`);
  assert.ok((f.trim ?? 0) < (n.trim ?? 0), `sills: ${f.trim} against ${n.trim}`);
  assert.equal(f.glass, n.glass);
});

test("roofLook: a finished roof keeps the photo, a field or a building site gets tiles", () => {
  const n = 200, jitter = (k) => ((k * 37) % 11) / 400;
  const grey = Array.from({ length: n }, (_, k) => [0.42 + jitter(k), 0.42 + jitter(k), 0.43 + jitter(k)]);
  const tiles = Array.from({ length: n }, (_, k) => [0.55 + jitter(k), 0.32, 0.26]);
  const field = Array.from({ length: n }, (_, k) => [0.35, 0.5 + jitter(k), 0.25]);
  const site = Array.from({ length: n }, (_, k) => (k % 2 ? [0.85, 0.82, 0.75] : [0.3, 0.27, 0.22]));
  assert.equal(roofLook(grey), "photo");
  assert.equal(roofLook(tiles), "photo");
  assert.equal(roofLook(field), "tiles");
  assert.equal(roofLook(site), "tiles");
  assert.equal(roofLook([]), "tiles");
});

test("a roof face is gravel only when flat (under 5°): a 10° lean-to or a 14° hip is tiles (#123)", () => {
  const level = (deg) => Math.cos((deg * Math.PI) / 180);
  for (const deg of [0, 2, 4.5]) assert.ok(level(deg) > FLAT_ROOF, `${deg}°`);
  for (const deg of [6, 10, 14, 30]) assert.ok(level(deg) <= FLAT_ROOF, `${deg}°`);
});

test("roofFamily: the aerial photo's pale roofs (Le Mont-sur-Lausanne's medians) read as real roof colours", () => {
  // medians of photographed roofs: warm ones are clay, grey ones fibre cement or concrete
  assert.equal(roofFamily([0.66, 0.58, 0.53]), "terracotta");
  assert.equal(roofFamily([0.6, 0.48, 0.41]), "terracotta");
  assert.equal(roofFamily([0.46, 0.41, 0.37]), "brown");
  assert.equal(roofFamily([0.35, 0.3, 0.27]), "brown");
  assert.equal(roofFamily([0.48, 0.46, 0.45]), "grey");
  assert.equal(roofFamily([0.45, 0.47, 0.5]), "grey");
  assert.equal(roofFamily([0.32, 0.31, 0.31]), "anthracite");
  assert.equal(roofFamily([0.38, 0.37, 0.35]), "anthracite");
});

test("roofTint: a real roof colour, never the photo's pale grey, varying from house to house", () => {
  const lum = ([r, g, b]) => 0.2126 * r + 0.7152 * g + 0.0722 * b;
  const all = Object.values(ROOF_TINTS).flat();
  const tints = [];
  for (let k = 0; k < 40; k++) {
    const hash = houseHash(`lidar-${k}-${k * 3}`);
    for (const photographed of [[0.48, 0.46, 0.45], [0.6, 0.48, 0.41], [0.35, 0.3, 0.27], null]) {
      const t = roofTint(photographed, hash);
      tints.push(t);
      assert.ok(t.every((v) => v >= 0 && v <= 1));
      // a palette colour, a few per cent lighter or darker
      assert.ok(all.some((p) => p.every((v, i) => Math.abs(t[i] - v) <= 0.07 * v + 1e-9)), JSON.stringify(t));
      assert.ok(lum(t) < 0.46, `too pale: ${JSON.stringify(t)}`);
    }
    const tile = roofTint([0.6, 0.48, 0.41], hash);
    assert.ok(tile[0] > tile[1] * 1.5, `terracotta: ${JSON.stringify(tile)}`);
  }
  assert.ok(new Set(tints.map((t) => t.map((v) => v.toFixed(3)).join())).size > 20);
  assert.deepEqual(roofTint([0.6, 0.48, 0.41], 12345), roofTint([0.6, 0.48, 0.41], 12345));
});
