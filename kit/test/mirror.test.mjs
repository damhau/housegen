// housekit/mirror: which meshes reflect the room while walking (the reflection itself needs a browser).
import { test } from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import fx from "../furnish.js";
import { isMirror } from "../mirror.js";

function mirrorsIn(p) {
  p.updateMatrixWorld(true);
  const found = [];
  p.traverse((o) => { if (isMirror(o)) found.push(o); });
  return found;
}

test("the basin's mirror reflects; chrome taps, rails and the shower glass do not", () => {
  assert.equal(mirrorsIn(fx.basin()).length, 1);
  assert.equal(mirrorsIn(fx.basin({ mirror: false })).length, 0);
  for (const p of [fx.towelRail(), fx.shower(), fx.bathAccessories(), fx.wc(), fx.kitchenRun()]) {
    assert.deepEqual(mirrorsIn(p), [], p.userData.name);
  }
});

test("a mirror is a plate: the mirror finish on a thin panel, stretched or not; not on a ball or a sliver", () => {
  const plate = new THREE.Mesh(new THREE.BoxGeometry(0.6, 0.9, 0.01), fx.finish.mirror());
  assert.ok(mirrorsIn(plate).length === 1);
  const stretched = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), fx.finish.mirror());
  stretched.scale.set(0.5, 1.2, 0.012);
  assert.ok(mirrorsIn(stretched).length === 1);
  assert.equal(mirrorsIn(new THREE.Mesh(new THREE.SphereGeometry(0.3), fx.finish.mirror())).length, 0);
  assert.equal(mirrorsIn(new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.1, 0.005), fx.finish.mirror())).length, 0);
  // a polished metal plate of the builder's own reflects too
  const own = new THREE.Mesh(new THREE.PlaneGeometry(0.5, 0.7), new THREE.MeshStandardMaterial({ metalness: 1, roughness: 0 }));
  assert.equal(mirrorsIn(own).length, 1);
});
