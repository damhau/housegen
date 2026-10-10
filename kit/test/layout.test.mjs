// housekit/layout: the furniture layout measured and the rooms against the plan (#67, #68, #70), on
// small fixtures (no renderer needed: rays against the meshes, plane geometry).
import { test } from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import * as house from "../house.js";
import { floorPlan } from "../interior.js";
import fx from "../furnish.js";
import { layout, auditLines, layoutReport, pieceType } from "../layout.js";

/** A piece of furniture of our own: a box w × h × d (origin at its bottom centre, facing +z). */
function box(name, [w, h, d], extra = {}) {
  const g = new THREE.Group();
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), new THREE.MeshStandardMaterial());
  m.position.y = h / 2;
  g.add(m);
  g.userData = { kind: "furniture", name, footprint: [w, d], ...extra };
  return g;
}
const put = (root, p, x, z, rot = 0, y = 0.015) => { root.add(fx.place(p, [x, z], rot, y)); return p; };

// a 6 × 4 m flat: a bedroom (west) and a bathroom (east) split by a partition with a door, the outer
// walls 0.3 m thick, a window on the north façade of the bedroom
function scene({ rooms = null } = {}) {
  const root = new THREE.Group();
  const footprint = [[-3.3, -2.3], [3.3, -2.3], [3.3, 2.3], [-3.3, 2.3]];
  root.add(house.perimeterWalls({ polygon: footprint, y: 0, height: 2.6, thickness: 0.3,
    openings: { 0: [{ offset: 4.2, sill: 0.9, width: 1.2, height: 1.3 }] }, makeUnit: (o) => house.windowUnit({ width: o.width, height: o.height }) }));
  root.add(floorPlan({ y: 0, height: 2.5, rooms: rooms ?? [
    { name: "Chambre — App. 1", use: "bedroom", polygon: [[-3, -2], [-0.05, -2], [-0.05, 2], [-3, 2]], area: 10.5 },
    { name: "SDB — App. 1", use: "bath", polygon: [[0.05, -2], [3, -2], [3, 2], [0.05, 2]], area: 11.1 },
  ], partitions: [{ from: [0, -2], to: [0, 2], openings: [{ offset: 2.6, width: 0.8, door: { hinge: "start", swing: "left" } }] }] }));
  root.updateMatrixWorld(true);
  return root;
}
const find = (L, re) => L.findings.filter((f) => re.test(f.text));

test("what a piece is, from its name", () => {
  assert.equal(pieceType("bed-oak-linen"), "bed");
  assert.equal(pieceType("nightstand"), "nightstand");
  assert.equal(pieceType("towelRail"), "towels");
  assert.equal(pieceType("towelStack"), "decor");
  assert.equal(pieceType("photo-frame"), "decor");
  assert.equal(pieceType("wall-art-gallery"), "art");
  assert.equal(pieceType("oak wall shelf"), "shelf");
  assert.equal(pieceType("kitchenRun"), "kitchen");
  assert.equal(pieceType("fridge-black-glass"), "appliance");
});

test("plan check: over 5 % and over 0.5 m², with the cause; the rooms with no area from the plan", () => {
  // the bedroom: 2.95 × 4 = 11.8 m² against 10.5 printed (+12 %, +1.3 m²); the bath: 2.95 × 4 = 11.8 against 11.1 (+6 %, +0.7 m²)
  const L = layout(scene());
  const lines = find(L, /room area off the plan/);
  assert.equal(lines.length, 2);
  assert.match(lines[0].text, /"Chambre — App\. 1" measures 11\.80 m², the plan prints 10\.50 m² \(\+12\.4 %, \+1\.30 m²\)/);
  // a small room 9 % off but 0.2 m² is not flagged; a room with no area is listed
  const L2 = layout(scene({ rooms: [
    { name: "WC", use: "wc", polygon: [[-3, -2], [-1.95, -2], [-1.95, 0], [-3, 0]], area: 1.9 },
    { name: "Hall", use: "hall", polygon: [[-1.85, -2], [3, -2], [3, 2], [-1.85, 2]] },
  ] }));
  assert.equal(find(L2, /room area off the plan/).length, 0);
  assert.match(find(L2, /no area from the plan/)[0].text, /1 room \("Hall"\)/);
});

test("supports: the pot floating 1.5 cm over its shelf, the shelf fixed to the wall", () => {
  const root = scene();
  // a wall shelf against the north wall's inner face (z = -2), its top at 1.2005 m; the pot 1.5 cm above it
  put(root, box("oak wall shelf", [0.36, 0.025, 0.22]), -2.5, -2 + 0.11 + 0.005, 0, 1.175);
  put(root, box("plant-small", [0.2, 0.27, 0.2]), -2.5, -1.88, 0, 1.2155);
  root.updateMatrixWorld(true);
  const L = layout(root);
  const f = find(L, /plant-small/);
  assert.equal(f.length, 1);
  assert.match(f[0].text, /floats 1\.5 cm above "oak wall shelf"/);
  assert.equal(find(L, /oak wall shelf.*fixed to nothing/).length, 0);
});

test("supports: a shelf off the wall with nothing under it, a sunk chest, two cabinets in each other", () => {
  const root = scene();
  put(root, box("oak wall shelf", [0.36, 0.025, 0.22]), -2.5, -1.5, 0, 1.2); // 39 cm off the north wall
  put(root, box("chest", [0.6, 0.5, 0.4]), -2.5, 1.5, 0, -0.025); // 4 cm into the floor (its top at 0.015)
  put(root, box("cabinet", [0.8, 0.9, 0.4]), 1.0, 1.5);
  put(root, box("cabinet", [0.8, 0.9, 0.4]), 1.7, 1.5); // 10 cm into the first
  root.updateMatrixWorld(true);
  const L = layout(root);
  assert.match(find(L, /oak wall shelf/)[0].text, /fixed to nothing: its back is 39 cm/);
  assert.match(find(L, /"chest"/)[0].text, /sunk 4\.0 cm into the floor/);
  assert.match(find(L, /"cabinet" and "cabinet"/)[0].text, /overlap by 10 cm/);
});

test("supports: a vase on a sideboard rests on it (no overlap); a lamp hangs (no support)", () => {
  const root = scene();
  put(root, box("sideboard", [1.2, 0.7, 0.4]), -1.5, 1.7);
  put(root, box("vase", [0.15, 0.3, 0.15]), -1.5, 1.7, 0, 0.715);
  put(root, box("pendant", [0.3, 0.3, 0.3], { hang: true }), -1.5, 0, 0, 1.9);
  root.updateMatrixWorld(true);
  const L = layout(root);
  assert.equal(L.findings.filter((f) => /vase|pendant|sideboard/.test(f.text)).length, 0);
});

test("window: a wardrobe in front of it; a low chest under it is fine", () => {
  const root = scene();
  // the window: 1.2 m wide, centred at x = 3.3 - 4.2 - 0.6 = -1.5 on the north façade, sill at 0.9
  put(root, box("wardrobe", [1.0, 2.2, 0.6]), -1.5, -2 + 0.3);
  root.updateMatrixWorld(true);
  assert.match(find(layout(root), /in front of the window/)[0].text, /"wardrobe" in "Chambre — App\. 1".*covers 100 cm/);
  const r2 = scene();
  put(r2, box("chest", [1.0, 0.8, 0.5]), -1.5, -2 + 0.25);
  r2.updateMatrixWorld(true);
  assert.equal(find(layout(r2), /in front of the window/).length, 0);
});

test("passages: 60 cm beside a double bed; a door swinging into a piece", () => {
  const root = scene();
  // a 1.6 × 2.05 bed, its head against the west wall, 30 cm from the north wall
  put(root, box("bed", [1.6, 0.5, 2.05]), -1.95, -0.9, Math.PI / 2);
  // a box in the door's swing (the door at z 0.6..1.4 on the partition at x = 0: "left" walking from → to
  // (north to south) opens it into the bathroom, east)
  put(root, box("laundryBasket", [0.4, 0.55, 0.4]), 0.35, 0.9);
  root.updateMatrixWorld(true);
  const L = layout(root);
  assert.match(find(L, /"bed" in "Chambre/)[0].text, /beside it \(keep 60 cm on both sides of a double bed\)/);
  assert.match(find(L, /swings into "laundryBasket"/)[0].text, /the door at \[0\.00, 1\.00\]/);
});

test("sizes: a nightstand too narrow, a bed too long for its room", () => {
  const root = scene();
  put(root, box("nightstand", [0.2, 0.5, 0.35]), -2.7, -1.5);
  put(root, box("bed", [1.4, 0.5, 2.0]), -1.5, 0.5); // 2.0 m long in a 4.0 m room: fits
  root.updateMatrixWorld(true);
  const L = layout(root);
  assert.match(find(L, /"nightstand"/)[0].text, /0\.20 m wide: a nightstand is usually/);
  assert.equal(find(L, /does not fit/).length, 0);
});

test("audit lines: plan first, a cap, and the measure table", () => {
  const root = scene();
  for (let i = 0; i < 20; i++) put(root, box("cup", [0.08, 0.1, 0.08]), -2.8 + i * 0.12, 1.0, 0, 0.5); // floating cups
  root.updateMatrixWorld(true);
  const L = layout(root);
  // the cups' line is one (grouped: the same gap over the same support)
  assert.match(find(L, /"cup"/)[0].text, /^"cup" floats 48 cm above the floor/);
  const lines = auditLines(L, { max: 2 });
  assert.match(lines[0], /^room area off the plan/);
  assert.equal(lines.length, 3);
  assert.match(lines[2], /1 more layout findings: measure\(\) lists them all/);
  const t = layoutReport(L);
  assert.equal(t.pieces.length, 20);
  assert.deepEqual(Object.keys(t.pieces[0]).sort(), ["at", "bottom", "d", "h", "name", "room", "rot", "storey", "type", "w", "wall"]);
  assert.equal(t.rooms.length, 2);
});
