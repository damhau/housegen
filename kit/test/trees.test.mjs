// housekit: treeStand, the surroundings' trees (#50): copies of kit trees fitted to each tree, in a few draws.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import { leafTree, treeStand } from "../house.js";

test("a stand: few draws, each tree at its place, height and kind, a quarter of the leaves", () => {
  const trees = [
    { position: [0, 2, 0], height: 15, radius: 4, kind: "broadleaf" },
    { position: [20, 0, 0], height: 18, radius: 3, kind: "pine" },
    { position: [0, 0, 20], height: 2, radius: 1.2, kind: "bush" },
    { position: [20, 0, 20], height: 12, radius: 1.2, kind: "columnar" },
  ];
  const g = treeStand({ trees });
  g.updateMatrixWorld(true);
  const parts = [];
  g.traverse((o) => { if (o.isInstancedMesh) parts.push(o); });
  assert.ok(parts.length <= 12, `${parts.length} draws`);
  // each tree's box: on its ground, as high as asked
  const m = new THREE.Matrix4(), p = new THREE.Vector3();
  const boxes = trees.map(() => new THREE.Box3());
  for (const part of parts) {
    for (let i = 0; i < part.count; i++) {
      part.getMatrixAt(i, m);
      p.setFromMatrixPosition(m);
      const k = trees.findIndex((t) => Math.hypot(p.x - t.position[0], p.z - t.position[2]) < 9);
      assert.ok(k >= 0, `an instance at ${p.toArray().map((v) => v.toFixed(1))} belongs to no tree`);
      boxes[k].expandByPoint(p);
    }
  }
  trees.forEach((t, k) => {
    assert.ok(Math.abs(boxes[k].min.y - t.position[1]) < 0.6, `tree ${k} stands on its ground`);
    assert.ok(Math.abs(boxes[k].max.y - t.position[1] - t.height) < t.height * 0.2, `tree ${k}: ${boxes[k].max.y.toFixed(1)} high for ${t.height}`);
  });
  // about a quarter of a full tree's leaves
  const leaves = parts.filter((o) => o.userData.kind === "leaves").reduce((n, o) => n + o.count, 0);
  let full = 0;
  leafTree({ position: [0, 0, 0], height: 15, spread: 8 }).traverse((o) => { if (o.isInstancedMesh) full += o.count; });
  assert.ok(leaves < full * 1.6, `${leaves} leaves for four trees, a full tree has ${full}`);
});
