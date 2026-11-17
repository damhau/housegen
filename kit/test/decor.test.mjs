// housekit decor (#102): the decorative fixtures, the entrance benches and the wall art a scene draws from the
// catalogue once built (the photos frame the same ids): which fixtures become lamps, one lamp per family and room, a
// print per room and none twice, a cord set to its length, the kit's decor models drawn as roles.
import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import { ART } from "../art.js";
import { allocateArt, artSize, catalogueDecor, decorPlan, framedPrint, lampRole, setCord } from "../decor.js";
import fx, { LAMP_ROLES, ROLES, resetObjects } from "../furnish.js";
import { floorPlan } from "../interior.js";
import { OBJECTS } from "../objects.js";
import { setHouseStyle } from "../styles.js";

afterEach(() => {
  setHouseStyle({});
  resetObjects();
  delete globalThis.__housekitSeed;
});

const ROOMS = [
  { name: "Chambre 1", use: "bedroom", polygon: [[0, 0], [4, 0], [4, 3], [0, 3]] },
  { name: "Chambre 2", use: "bedroom", polygon: [[4, 0], [8, 0], [8, 3], [4, 3]] },
  { name: "Séjour", use: "living", polygon: [[0, 3], [8, 3], [8, 8], [0, 8]] },
];

function house() {
  const root = new THREE.Group();
  root.add(floorPlan({ rooms: ROOMS }));
  const at = (p, x, z, y = 0) => { p.position.set(x, y, z); root.add(p); return p; };
  return { root, at };
}

test("the decorative fixtures are catalogue lamps, the LED fittings, wall lights and built-ins keep their shape", () => {
  for (const f of ["globeTableLamp", "mushroomLamp", "drumTableLamp", "deskLamp", "globeFloorLamp", "arcFloorLamp", "tripodFloorLamp",
    "globePendant", "domePendant", "facetedCluster", "discArms", "frameGlobes", "barCluster"]) assert.ok(LAMP_ROLES.has(lampRole(f)), f);
  for (const f of ["ledRing", "linearPendant", "downlight", "track", "flushCeiling", "coveStrip", "underCabinet", "stepLight", "mirrorBar",
    "hexSconce", "globeSconce", "upDown", "swingArm", "pictureLight"]) {
    assert.equal(lampRole(f), null, f);
  }
  setHouseStyle({ lamps: { mushroomLamp: "kit" } });
  assert.equal(lampRole("mushroomLamp"), null, "a house keeps a fixture's own shape");
});

test("every catalogue lamp has its light inside it, a hanging one its cord, in both styles", () => {
  const lamps = OBJECTS.filter((o) => LAMP_ROLES.has(o.role));
  for (const style of ["scandinavian", "contemporary"]) {
    for (const role of LAMP_ROLES) assert.ok(lamps.some((o) => o.role === role && o.styles.includes(style)), `${style} has no ${role}`);
  }
  for (const o of lamps) {
    const [w, d, h] = o.size, [x, y, z] = o.light;
    assert.ok(Math.abs(x) <= w / 2 + 0.01 && Math.abs(z) <= d / 2 + 0.01 && y > 0 && y < h, `${o.id} light ${o.light} outside ${o.size}`);
    if (o.role === "pendant") assert.ok(o.cord && o.cord[0] > y && o.cord[1] <= h + 0.001 && o.cord[1] - o.cord[0] >= 0.1, `${o.id} cord ${o.cord}`);
  }
  assert.ok(!Object.keys(ROLES).some((r) => LAMP_ROLES.has(r)), "a lamp without its light through model()");
});

test("a room's lamps of one family are one piece, other rooms draw others; built-ins are left alone", () => {
  globalThis.__housekitSeed = "decor";
  const { root, at } = house();
  const pair = [at(fx.fixture("globeTableLamp"), 0.5, 0.4, 0.55), at(fx.fixture("globeTableLamp"), 2.5, 0.4, 0.55)];
  const other = at(fx.fixture("globeTableLamp"), 4.5, 0.4, 0.55);
  at(fx.fixture("downlight"), 6, 6, 2.475);
  at(fx.fixture("domePendant", { over: true }), 4, 5, 2.475);
  const plan = decorPlan(root);
  assert.equal(plan.lamps.length, 4);
  const piece = (f) => plan.lamps.find(([g]) => g === f)[1];
  assert.equal(piece(pair[0]).id, piece(pair[1]).id, "a bedside pair");
  assert.notEqual(piece(other).id, piece(pair[0]).id, "the next bedroom's");
  assert.equal(plan.lamps.find(([g]) => g.userData.fixture.family === "domePendant")[1].role, "pendant");
  assert.equal(plan.lamps.find(([, , r]) => r?.name === "Chambre 2")[2].use, "bedroom");
});

test("a cord set to its length: the canopy moves, the shade stays, the photo service gets the same numbers", () => {
  const wrap = new THREE.Group();
  const geo = new THREE.CylinderGeometry(0.1, 0.1, 1, 8).translate(0, 0.5, 0);
  const m = new THREE.Mesh(geo, new THREE.MeshBasicMaterial());
  const node = new THREE.Group();
  node.scale.setScalar(2); // a glTF node's scale: baked
  node.add(m);
  m.scale.setScalar(0.5);
  wrap.add(node);
  setCord(wrap, [0.3, 0.9], 0.2);
  const b = new THREE.Box3().setFromObject(wrap);
  assert.ok(Math.abs(b.max.y - 0.6) < 1e-6 && Math.abs(b.min.y) < 1e-6, `${b.min.y} ${b.max.y}`);
  assert.deepEqual(wrap.userData.cord, [0.3, 0.9, 0.2]);
});

test("wall art: a print per placeholder, none twice in a house while prints are left, the living room first", async () => {
  globalThis.__housekitSeed = "art";
  const { root, at } = house();
  const ps = [];
  for (const [x, z] of [[1, 2.9], [5, 2.9], [3, 7.9], [6, 7.9]]) ps.push(at(await fx.model("wall-art", { width: 1.1, height: 0.8 }), x, z, 1.2));
  const gallery = at(await fx.model("wall-art-gallery", { width: 1.4, height: 0.9 }), 2, 5, 1.2);
  assert.equal(gallery.userData.styledFrom, "wall-art-gallery");
  const plan = decorPlan(root);
  assert.equal(plan.art.length, 5);
  assert.equal(new Set(plan.art.map(([, e]) => e.id)).size, 5);
  assert.deepEqual(plan.art.slice(0, 3).map(([, , r]) => r.use), ["living", "living", "living"], "the living room's prints first");
  for (const [, e] of plan.art) assert.ok(e.styles.includes("scandinavian"));
  resetObjects();
  assert.equal(catalogueDecor(root), 5);
  for (const p of [...ps, gallery]) {
    assert.equal(typeof p.userData.art, "string");
    const b = new THREE.Box3().setFromObject(p).applyMatrix4(p.matrixWorld.clone().invert());
    assert.ok(b.max.x - b.min.x <= 1.41 && b.max.y - b.min.y <= 0.91, `${p.userData.art} ${b.max.x - b.min.x} x ${b.max.y - b.min.y}`);
    assert.ok(p.getObjectByName("art-print"), "a print in its frame");
  }
  const many = allocateArt(Array.from({ length: ART.length + 3 }, () => ({ use: "hall" })), "contemporary");
  const n = ART.filter((e) => e.styles.includes("contemporary")).length;
  assert.equal(new Set(many.slice(0, n).map((e) => e.id)).size, n, "every print once before one comes back");
});

test("a framed print fits its size, its print on the front face", () => {
  for (const e of ART.slice(0, 12)) {
    const { g, size: [fw, fh] } = framedPrint(e, 0.8);
    assert.deepEqual([fw, fh].map((v) => +v.toFixed(6)), artSize(e, 0.8).map((v) => +v.toFixed(6)));
    assert.ok(Math.abs(Math.max(fw, fh) - 0.8) < 1e-6);
    const b = new THREE.Box3().setFromObject(g);
    assert.ok(Math.abs(b.min.y) < 1e-6 && Math.abs(b.max.y - fh) < 1e-6);
    assert.ok(g.getObjectByName("art-print").position.z > 0, "the print faces +z");
  }
});

test("the kit's decor models are drawn as catalogue roles", async () => {
  const { look } = await import("../styles.js");
  assert.equal(look("models", { kind: "mirror-round" }), "mirror");
  assert.equal(look("models", { kind: "plant-large" }), "floor-plant");
  assert.deepEqual(look("models", { kind: "vase-dry-branches" }), { as: "vase", stems: true });
  setHouseStyle({ style: "contemporary" });
  assert.equal(look("models", { kind: "dining-chair-grey" }), "dining-chair-black");
  assert.equal(look("models", { kind: "wall-art-gallery" }), "wall-art", "a style's own map keeps the kit's");
});
