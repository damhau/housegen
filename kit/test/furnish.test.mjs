// housekit/furnish: parametric pieces and wall placement, no renderer needed (scanned models need a browser).
import { test } from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import fx, { maskLicensed } from "../furnish.js";

const room = [[0, 0], [4, 0], [4, 3], [0, 3]]; // x 0..4, z 0..3

function box(p) {
  p.updateMatrixWorld(true);
  return new THREE.Box3().setFromObject(p);
}

test("every parametric piece sits on the floor (or hangs on the wall above it), tagged with its footprint", () => {
  for (const p of [fx.sofa(), fx.bed(), fx.nightstand(), fx.chair(), fx.diningSet(), fx.wardrobe(), fx.kitchenRun(), fx.wc(), fx.basin(), fx.bathtub(), fx.shower(), fx.towelRail(), fx.coatHooks(), fx.bench(), fx.washer({ dryer: true }), fx.bathAccessories(), fx.towelStack(), fx.laundryBasket(), fx.rug()]) {
    const b = box(p);
    assert.equal(p.userData.kind, "furniture");
    // the basin's vanity and the coat hooks hang on the wall; everything else stands on the floor
    // (the towel rail's origin is its own bottom)
    if (["basin", "coatHooks"].includes(p.userData.name)) assert.ok(b.min.y > 0.2, `${p.userData.name} bottom at ${b.min.y.toFixed(3)}`);
    else assert.ok(Math.abs(b.min.y) < 0.02, `${p.userData.name} bottom at ${b.min.y.toFixed(3)}`);
    const [w, d] = p.userData.footprint;
    const size = b.getSize(new THREE.Vector3());
    assert.ok(size.x <= w + 0.12 && size.z <= d + 0.12, `${p.userData.name} ${size.x.toFixed(2)}×${size.z.toFixed(2)} in ${w}×${d}`);
  }
});

test("onWall puts the back against the wall and the front into the room, on every wall and winding", () => {
  for (const poly of [room, [...room].reverse()]) {
    for (let edge = 0; edge < 4; edge++) {
      const a = poly[edge], b = poly[(edge + 1) % 4];
      const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
      const w = fx.wardrobe({ width: 1.0, depth: 0.6 });
      fx.onWall(poly, edge, len / 2, w, { gap: 0 });
      const bb = box(w);
      // inside the room
      assert.ok(bb.min.x > -0.01 && bb.max.x < 4.01 && bb.min.z > -0.01 && bb.max.z < 3.01, `edge ${edge}: out of the room`);
      // its back touches that wall
      const onWallLine = edge % 2 === 0
        ? Math.min(Math.abs(bb.min.z - a[1]), Math.abs(bb.max.z - a[1])) < 0.02
        : Math.min(Math.abs(bb.min.x - a[0]), Math.abs(bb.max.x - a[0])) < 0.02;
      assert.ok(onWallLine, `edge ${edge}: not against its wall`);
      // it faces into the room: its local +z points towards the room's centre
      const f = new THREE.Vector3(0, 0, 1).applyQuaternion(w.quaternion);
      const toCentre = new THREE.Vector3(2 - w.position.x, 0, 1.5 - w.position.z);
      assert.ok(f.dot(toCentre) > 0, `edge ${edge}: faces the wall`);
    }
  }
});

test("a kitchen run keeps its tall units out of the worktop and the sink out of a tall unit", () => {
  const k = fx.kitchenRun({ length: 3.6, tall: [{ at: 0.3, width: 0.6 }], sink: 0.3, hob: 2.0 });
  const b = box(k);
  assert.ok(b.max.y > 2.2 && b.max.y < 2.3, "tall unit to ~2.24 m");
  let steel = 0;
  k.traverse((o) => { if (o.isMesh && o.material === fx.finish.steel()) steel++; });
  assert.equal(steel, 0, "no sink inside the tall unit");
});

test("a bought model is served masked and unmasks to the same bytes", () => {
  const glb = new Uint8Array([0x67, 0x6c, 0x54, 0x46, 2, 0, 0, 0, 1, 2, 3, 250]);
  const masked = maskLicensed(glb, false);
  assert.equal(new TextDecoder().decode(masked.subarray(0, 4)), "HGX1");
  assert.notDeepEqual(masked.subarray(4), glb); // not a GLB anyone can open
  assert.deepEqual(maskLicensed(masked, true), glb);
  assert.throws(() => maskLicensed(glb, true), /not a masked licensed model/);
});

test("a bought model that cannot be loaded falls back to the parametric piece, at the size asked for", async () => {
  const warn = console.warn;
  console.warn = () => {};
  try {
    const p = await fx.model("bed-oak-linen", { width: 1.4, length: 2.0 });
    assert.equal(p.userData.name, "bed"); // the parametric bed: Node cannot load /kit/assets/
    assert.deepEqual(p.userData.footprint.map((v) => +v.toFixed(2)), [1.46, 2.08]);
    const none = await fx.model("vase-dry-branches");
    assert.equal(none.children.length, 0); // no stand-in for decor
  } finally {
    console.warn = warn;
  }
});

test("a role draws each of its pieces once before any comes back, the same ones for the same scene", async () => {
  const { ROLES, pickObject, resetObjects } = await import("../furnish.js");
  const draw = (seed, n) => {
    globalThis.__housekitSeed = seed;
    resetObjects();
    return Array.from({ length: n }, () => pickObject("vase").piece.id);
  };
  const n = ROLES.vase.length;
  const a = draw("house-a", n);
  assert.equal(new Set(a).size, n);
  assert.deepEqual(draw("house-a", n), a);
  assert.notDeepEqual(draw("house-b", n), a);
  const twice = draw("house-a", 2 * n);
  assert.equal(new Set(twice.slice(n)).size, n);
  delete globalThis.__housekitSeed;
  resetObjects();
});

test("a role prefers a piece near the size asked, one that goes with the room's accent, and quiet large pieces", async () => {
  const { ROLES, pickObject, resetObjects } = await import("../furnish.js");
  globalThis.__housekitSeed = "sizes";
  resetObjects();
  const tall = Math.max(...ROLES.vase.map((o) => o.size[2]));
  const o = pickObject("vase", { height: tall }).piece;
  assert.ok(Math.abs(Math.log(tall / o.size[2])) <= Math.log(1.6), `${o.id} ${o.size[2]} m for ${tall} m`);
  for (const room of ["Séjour", "Chambre 1", "Chambre 2", "Bureau", "Cuisine"]) {
    resetObjects();
    const { piece, accent } = pickObject("cushion", { room });
    assert.ok(["neutral", "dark", accent].includes(piece.tone), `${room} (${accent}) drew a ${piece.tone} cushion`);
  }
  resetObjects();
  const quiet = ROLES.rug.filter((o) => ["neutral", "dark"].includes(o.tone)).length;
  const rugs = ["Séjour", "Chambre 1", "Chambre 2", "Bureau"].slice(0, quiet).map((room) => pickObject("rug", { room }).piece);
  assert.deepEqual(rugs.filter((o) => !["neutral", "dark"].includes(o.tone)), [], "a rug in an accent while quiet ones were left");
  delete globalThis.__housekitSeed;
  resetObjects();
});

test("the decor catalogue: every role has pieces, every piece a real size, no role named like a catalogue model", async () => {
  const { ROLES, MODELS } = await import("../furnish.js");
  assert.ok(Object.keys(ROLES).length >= 20);
  for (const [role, list] of Object.entries(ROLES)) {
    assert.ok(!(role in MODELS), role);
    for (const o of list) assert.ok(o.size.length === 3 && o.size.every((v) => v > 0.001 && v < 4), `${o.id} ${o.size}`);
  }
  await assert.rejects(fx.model("dragon"), /unknown model/);
});

test("furniture: a matched role draws one piece per room, a piece near the size asked, the kit's bed for a single", async () => {
  const { ROLES, pickObject, resetObjects } = await import("../furnish.js");
  globalThis.__housekitSeed = "furniture";
  resetObjects();
  const chairs = new Set(Array.from({ length: 6 }, () => pickObject("dining-chair", { room: "Séjour" }).piece.id));
  assert.equal(chairs.size, 1, "the chairs round one table differ");
  const nightstands = new Set(Array.from({ length: 2 }, () => pickObject("nightstand", { room: "Chambre 1" }).piece.id));
  assert.equal(nightstands.size, 1);
  for (const [role, width, length] of [["sofa", 2.4, 0.95], ["bed", 1.6, 2.0], ["bed", 1.4, 2.0], ["dining-table", 1.8, 0.9]]) {
    resetObjects();
    const o = pickObject(role, { width, length }).piece;
    const [w, l] = o.nominal ?? o.size;
    assert.ok(Math.max(Math.abs(Math.log(width / w)), Math.abs(Math.log(length / l))) <= Math.log(1.25), `${role} ${o.id} ${w}×${l} for ${width}×${length}`);
  }
  for (const o of ROLES.bed) assert.ok(o.nominal && o.mattress && o.mattress.top > 0.2, `${o.id} has no measured mattress`);
  resetObjects();
  const single = await fx.model("bed", { width: 0.9, length: 2.0 });
  assert.equal(single.userData.model, undefined, "a double bed squeezed to a single");
  assert.deepEqual(single.userData.footprint.map((v) => +v.toFixed(2)), [0.96, 2.08]);
  delete globalThis.__housekitSeed;
  resetObjects();
});
