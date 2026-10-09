// housekit/interior: the details a builder leaves out (#43): skirting stopping at doors, inner window sills.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import * as house from "../house.js";
import interior from "../interior.js";

// a 4 x 3 room (x 0..4, z 0..3) on a plan with a door in its east partition, and a window in its north wall
function scene() {
  const root = new THREE.Group();
  const plan = interior.floorPlan({
    y: 0,
    rooms: [{ name: "Salon", use: "living", polygon: [[0, 0], [4, 0], [4, 3], [0, 3]] }, { name: "Hall", use: "hall", polygon: [[4.1, 0], [6, 0], [6, 3], [4.1, 3]] }],
    partitions: [{ from: [4.05, 0], to: [4.05, 3], openings: [{ offset: 1, width: 0.9 }] }],
  });
  root.add(plan);
  const w = house.windowUnit({ width: 1.2, height: 1.4 });
  w.position.set(2, 0.9, -0.15); // in the north wall, its centre line 15 cm behind the room's face (z = 0)
  root.add(w);
  root.updateMatrixWorld(true);
  return { root, plan };
}

const box = (o) => new THREE.Box3().setFromObject(o);

test("skirting runs along the walls and stops at the door", () => {
  const { root } = scene();
  const sk = interior.skirting(root, { name: "Salon", polygon: [[0, 0], [4, 0], [4, 3], [0, 3]] }, { y: 0 });
  assert.equal(sk.userData.kind, "skirting");
  const mesh = sk.children[0];
  const b = box(mesh);
  assert.ok(b.max.y <= 0.061 && b.min.y >= -1e-6, "6 cm high from the floor");
  assert.ok(b.min.x >= -1e-6 && b.max.x <= 4 + 1e-6 && b.min.z >= -1e-6 && b.max.z <= 3 + 1e-6, "inside the room");
  // nothing at the door (the east wall, z from 1 to 1.9)
  const pos = mesh.geometry.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i), z = pos.getZ(i);
    if (x > 3.98) assert.ok(z <= 1 + 1e-6 || z >= 1.9 - 1e-6, `skirting in the doorway at z=${z.toFixed(2)}`);
  }
});

test("a window gets an inner sill at its height, reaching back to the window, nothing for a door", () => {
  const { root } = scene();
  const ws = interior.windowSills(root, { name: "Salon", polygon: [[0, 0], [4, 0], [4, 3], [0, 3]] }, { y: 0 });
  assert.equal(ws.children.length, 1);
  const b = box(ws.children[0]);
  assert.ok(Math.abs(b.max.y - 0.9) < 1e-6, `top at the sill height: ${b.max.y}`);
  assert.ok(b.min.z < -0.1 && b.max.z > 0.02, `from the window (z -0.15) into the room: ${b.min.z}..${b.max.z}`);
  assert.ok(b.min.x < 1.41 && b.max.x > 2.59, "the window's width and a little more");
  const hall = interior.windowSills(root, { name: "Hall", polygon: [[4.1, 0], [6, 0], [6, 3], [4.1, 3]] }, { y: 0 });
  assert.equal(hall.children.length, 0);
});

test("roomDetails adds them once, not in a tiled room, and the audit asks for curtains where there is a window", () => {
  const { root, plan } = scene();
  assert.ok(interior.roomDetails(root) >= 2);
  const kinds = plan.children.map((c) => c.userData.kind).filter((k) => k === "skirting" || k === "windowSills");
  assert.ok(kinds.includes("skirting") && kinds.includes("windowSills"));
  assert.equal(interior.roomDetails(root), 0, "a second pass adds nothing");
  const lines = interior.roomEssentials(root);
  const salon = lines.find((l) => l.startsWith('"Salon"')) ?? "";
  assert.ok(salon.includes("curtains"), salon);
  assert.ok(salon.includes("nothing on the walls"), salon);
});

test("a curtain next to a window gets a rail under the ceiling, a bare window none", () => {
  const { root } = scene();
  const salon = { name: "Salon", polygon: [[0, 0], [4, 0], [4, 3], [0, 3]] };
  assert.equal(interior.curtainRails(root, salon, { y: 0, ceiling: 2.5 }).children.length, 0);
  const c = new THREE.Group();
  c.userData = { kind: "furniture", name: "curtain-grey" };
  c.position.set(1.2, 0, 0.12);
  root.add(c);
  const rails = interior.curtainRails(root, salon, { y: 0, ceiling: 2.5 });
  assert.equal(rails.children.length, 1);
  const b = box(rails.children[0]);
  assert.ok(b.max.y < 2.5 && b.min.y > 2.4, `under the ceiling: ${b.min.y}..${b.max.y}`);
  assert.ok(b.min.x < 1.2 && b.max.x > 2.8, "past both sides of the window");
});

test("a cornice only where the plan asks for one, under the ceiling, cut where an opening reaches it", () => {
  const { root, plan } = scene();
  interior.roomDetails(root);
  assert.ok(!plan.children.some((c) => c.userData.kind === "cornice"), "no cornice by default");
  const salon = { name: "Salon", polygon: [[0, 0], [4, 0], [4, 3], [0, 3]] };
  const c = interior.cornice(root, salon, { y: 0, ceiling: 2.5 });
  const b = box(c.children[0]);
  assert.ok(b.max.y <= 2.5 && b.min.y >= 2.42, `under the ceiling: ${b.min.y.toFixed(3)}..${b.max.y.toFixed(3)}`);
  assert.ok(b.min.x >= -1e-6 && b.max.x <= 4 + 1e-6 && b.min.z >= -1e-6 && b.max.z <= 3 + 1e-6, "inside the room");
  // an open passage to the ceiling in the east wall (x = 4, z 1..2): no cornice across it
  const open = new THREE.Group();
  const p2 = interior.floorPlan({ y: 0, rooms: [], partitions: [{ from: [4.05, 0], to: [4.05, 3], openings: [{ offset: 1, width: 1, height: 2.5, door: false }] }] });
  open.add(p2);
  const cut = interior.cornice(open, salon, { y: 0, ceiling: 2.5 });
  const pos = cut.children[0].geometry.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    if (pos.getX(i) > 3.9) assert.ok(pos.getZ(i) <= 1 + 1e-6 || pos.getZ(i) >= 2 - 1e-6, `cornice across the passage at z=${pos.getZ(i).toFixed(2)}`);
  }
  const withCornice = new THREE.Group();
  withCornice.add(interior.floorPlan({ y: 0, cornice: true, rooms: [salon] }));
  assert.ok(interior.roomDetails(withCornice) >= 1);
  assert.ok(withCornice.children[0].children.some((c) => c.userData.kind === "cornice"));
});
