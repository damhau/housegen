// Trees and bushes: deterministic by seed, sized as asked, one wood mesh + one leaf mesh,
// tubes facing outward. No renderer needed.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import * as house from "../house.js";

function flat() {
  house.terrain({ size: [10, 10], heightAt: () => 0 });
}

function box(o) {
  o.updateMatrixWorld(true);
  return new THREE.Box3().setFromObject(o);
}

function parts(g) {
  const wood = g.children.filter((c) => c.userData.kind === "wood");
  const leaves = g.children.filter((c) => c.userData.kind === "leaves");
  return { wood, leaves };
}

test("a broadleaf tree is one wood mesh and one instanced leaf mesh, sized as asked", () => {
  flat();
  const t = house.leafTree({ position: [0, 0], height: 7, spread: 3.2, seed: 7 });
  const { wood, leaves } = parts(t);
  assert.equal(wood.length, 1);
  assert.equal(leaves.length, 1);
  assert.ok(leaves[0].isInstancedMesh);
  assert.ok(leaves[0].count > 800, `only ${leaves[0].count} leaves`);
  const b = box(t);
  assert.ok(b.max.y > 6.3 && b.max.y < 8.2, `height ${b.max.y}`);
  const w = Math.max(b.max.x - b.min.x, b.max.z - b.min.z);
  assert.ok(w > 2.2 && w < 5.2, `spread ${w}`);
  assert.ok(b.min.y > -0.3, `base ${b.min.y}`);
  assert.deepEqual(t.userData, { kind: "tree", height: 7, spread: 3.2 });
});

test("the same seed gives the same tree, another seed another one", () => {
  flat();
  const a = box(house.leafTree({ position: [0, 0], seed: 4 }));
  const b = box(house.leafTree({ position: [0, 0], seed: 4 }));
  const c = box(house.leafTree({ position: [0, 0], seed: 5 }));
  assert.deepEqual(a.min.toArray(), b.min.toArray());
  assert.deepEqual(a.max.toArray(), b.max.toArray());
  assert.notDeepEqual(a.max.toArray(), c.max.toArray());
});

test("pine and columnar kinds build and keep their proportions", () => {
  flat();
  const pine = box(house.leafTree({ position: [0, 0], height: 9, spread: 3, kind: "pine", seed: 2 }));
  assert.ok(pine.max.y > 8.2 && pine.max.y < 10, `pine height ${pine.max.y}`);
  const col = box(house.leafTree({ position: [0, 0], height: 6, spread: 1.6, kind: "columnar", seed: 3 }));
  const wide = Math.max(col.max.x - col.min.x, col.max.z - col.min.z);
  assert.ok(wide < 3.2, `columnar spread ${wide}`);
});

test("a bush is a dome of leaves on twigs, on the ground", () => {
  flat();
  const b = house.leafBush({ position: [1, 2], radius: 0.8, seed: 3 });
  const { wood, leaves } = parts(b);
  assert.equal(wood.length, 1);
  assert.equal(leaves.length, 1);
  const bb = box(b);
  assert.ok(bb.max.y > 0.9 && bb.max.y < 1.8, `bush height ${bb.max.y}`);
  assert.ok(bb.min.y > -0.1 && bb.min.y < 0.2, `bush base ${bb.min.y}`);
});

test("wood tubes face outward (their normals point away from the axis)", () => {
  flat();
  const t = house.leafTree({ position: [0, 0], height: 7, seed: 1 });
  const wood = parts(t).wood[0].geometry;
  const pos = wood.attributes.position, nor = wood.attributes.normal;
  // the trunk's first ring: vertices 0..8 around the base at (0, 0, 0)
  let outward = 0;
  for (let i = 0; i < 9; i++) {
    const p = new THREE.Vector3().fromBufferAttribute(pos, i);
    const n = new THREE.Vector3().fromBufferAttribute(nor, i);
    p.y = 0;
    if (p.normalize().dot(n) > 0) outward += 1;
  }
  assert.ok(outward >= 8, `${outward} of 9 base normals point outward`);
});
