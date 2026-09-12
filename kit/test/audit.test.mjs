// Plausibility audit (#3): bounding-box checks over a scene group, no renderer needed.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import * as house from "../house.js";
import { buildShell } from "../template/src/shell.js";
import { buildGarden } from "../template/src/garden.js";

function trampoline(x, z) {
  const g = new THREE.Group();
  const mat = new THREE.MeshStandardMaterial({ color: "#222" });
  const bed = new THREE.Mesh(new THREE.CylinderGeometry(1.5, 1.5, 0.08, 24), mat);
  bed.position.y = 0.85;
  g.add(bed);
  for (const a of [0, 1.57, 3.14, 4.71]) g.add(house.rod([Math.cos(a) * 1.4, 0, Math.sin(a) * 1.4], [Math.cos(a) * 1.4, 0.85, Math.sin(a) * 1.4], 0.03));
  g.position.set(x, 0, z);
  g.userData = { kind: "prop" };
  g.name = "trampoline";
  return g;
}

function fresh() {
  // flat ground: the template's terrain registers itself globally, so reset before each scene
  house.terrain({ size: [10, 10], heightAt: () => 0 });
  return new THREE.Group();
}

test("a tree planted through a trampoline is reported", () => {
  const g = fresh();
  g.add(trampoline(4, 6));
  g.add(house.leafTree({ position: [4.2, 6.1], height: 7, seed: 3 }));
  const lines = house.audit(g);
  assert.equal(lines.length, 1, lines.join("\n"));
  assert.match(lines[0], /trampoline at \(4, 6\) intersects tree at \(\d(\.\d)?, \d(\.\d)?\)/);
});

test("a bench under the canopy but away from the trunk is fine", () => {
  const g = fresh();
  g.add(house.leafTree({ position: [0, 0], height: 8, spread: 4, seed: 5 }));
  g.add(house.bench({ position: [1.4, 0.2] }));
  assert.deepEqual(house.audit(g), []);
});

test("floating and sunk props are reported against the ground", () => {
  const g = fresh();
  g.add(house.bench({ position: [2, 1.0, 2] }));
  g.add(house.bicycle({ position: [-3, -0.9, -3] }));
  const lines = house.audit(g);
  assert.equal(lines.length, 2, lines.join("\n"));
  assert.match(lines[0], /bench .* floats 1 m above the ground/);
  assert.match(lines[1], /bicycle .* is sunk 0\.9 m into the ground/);
});

test("a bush inside a car and a car through a wall are reported", () => {
  const g = fresh();
  g.add(house.wall({ from: [5, 0], to: [-5, 0], height: 3 }));
  g.add(house.car({ position: [0, 0, 0.3] }));
  g.add(house.leafBush({ position: [0.5, 0.2], radius: 0.6 }));
  const lines = house.audit(g);
  assert.ok(lines.some((l) => /car .* intersects bush|bush .* intersects car/.test(l)), lines.join("\n"));
  assert.ok(lines.some((l) => /car .* intersects the wall/.test(l)), lines.join("\n"));
});

test("ground-storey walls floating above the terrain and oversized openings are reported", () => {
  const g = fresh();
  house.terrain({ size: [40, 40], heightAt: () => -1.0 });
  g.add(house.wall({ from: [5, 0], to: [-5, 0], height: 3, y: 0 }));
  g.add(house.wall({ from: [5, 0], to: [-5, 0], height: 3, y: 3 })); // upper storey: not reported
  g.add(house.door({ height: 4.5 }));
  const lines = house.audit(g);
  assert.equal(lines.filter((l) => /wall .* floats 1 m above the terrain/.test(l)).length, 1, lines.join("\n"));
  assert.ok(lines.some((l) => /door is 4\.5 m tall/.test(l)), lines.join("\n"));
});

test("the template scene audits clean", () => {
  const group = new THREE.Group();
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(400, 400));
  const ctx = { THREE, house, group, ground };
  buildShell(ctx);
  buildGarden(ctx);
  assert.deepEqual(house.audit(group), []);
});
