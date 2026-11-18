// The builder's ground and its site under the real surroundings (#123): what context.js clips, what it leaves.
import test from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import { GARDEN_REACH, SITE_MARGIN, clipToSite, isSheet, ownGround } from "../ground.js";

const material = () => new THREE.MeshStandardMaterial();

function box(w, h, d, x = 0, y = 0, z = 0, mat = material()) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
  m.position.set(x, y + h / 2, z);
  return m;
}

/** A level sheet of ground `w` × `d` metres around (x, z), as `surface()` or a PlaneGeometry lying flat. */
function sheet(w, d, { x = 0, y = 0, z = 0, mat = material(), segments = 1 } = {}) {
  const m = new THREE.Mesh(new THREE.PlaneGeometry(w, d, segments, segments), mat);
  m.rotation.x = -Math.PI / 2;
  m.position.set(x, y, z);
  return m;
}

function excluded(o) {
  o.userData.excludeFromBounds = true;
  return o;
}

/** A house 12 × 8 m and its plot's terrain 40 × 30 m, tagged as the kit asks. */
function plot() {
  const g = new THREE.Group();
  g.add(box(12, 7, 8));
  const terrain = sheet(40, 30);
  terrain.userData.kind = "terrain";
  g.add(terrain);
  return { g, terrain };
}

const near = (a, b, eps = 0.01) => Math.abs(a - b) <= eps;
const xz = (b) => [b.min.x, b.min.z, b.max.x, b.max.z];

test("a lawn drawn out to 90 m round the plot, untagged and left out of the framing, is ground: clipped to the site", () => {
  const { g, terrain } = plot();
  const lawn = excluded(sheet(180, 180, { y: -0.02, segments: 60 }));
  g.add(lawn);
  const own = ownGround(g);
  assert.equal(own.sheets, 1);
  assert.deepEqual(own.terrains, [terrain, lawn]);
  const [site] = own.boxes;
  assert.ok(near(site.max.x, 6 + SITE_MARGIN) && near(site.min.z, -4 - SITE_MARGIN), JSON.stringify(xz(site)));
  assert.equal(clipToSite(g, own.terrains, site), 2);
  for (const m of [terrain.material, lawn.material]) assert.equal(m.clippingPlanes.length, 4);
  assert.equal(own.flat, false);
});

test("a 350 m backdrop tagged as terrain and left out of the framing is clipped too; the plot keeps its height", () => {
  const { g } = plot();
  const backdrop = excluded(sheet(350, 350, { y: -0.37 }));
  backdrop.userData.kind = "terrain";
  g.add(backdrop);
  const own = ownGround(g);
  assert.equal(own.sheets, 0);
  assert.equal(own.terrains.length, 2);
  assert.equal(own.flat, false);
  assert.ok(near(own.boxes[0].max.x, 6 + SITE_MARGIN));
});

test("trees, hedges and garden pieces left out of the framing are never ground, whatever their size", () => {
  const { g } = plot();
  // a hedge 60 m long along a road, a row of trees (instanced), a tall tree's crown: none is a level sheet
  const hedge = excluded(box(60, 1.6, 0.8, 0, 0, 14));
  const trees = excluded(new THREE.InstancedMesh(new THREE.PlaneGeometry(60, 60).rotateX(-Math.PI / 2), material(), 3));
  const crown = excluded(new THREE.Mesh(new THREE.SphereGeometry(25, 12, 8), material()));
  g.add(hedge, trees, crown);
  for (const o of [hedge, trees, crown]) assert.equal(isSheet(o), false);
  const own = ownGround(g);
  assert.equal(own.sheets, 0);
  clipToSite(g, own.terrains, own.boxes[0]);
  for (const o of [hedge, trees, crown]) assert.equal(o.material.clippingPlanes, null);
});

test("a garden bed sharing the lawn's material stays whole: the lawn's material is cloned before the clip", () => {
  const { g } = plot();
  const grass = material();
  grass.onBeforeCompile = () => {};
  const lawn = excluded(sheet(180, 180, { mat: grass }));
  const bed = excluded(box(3, 0.1, 2, 25, 0, 0, grass));
  g.add(lawn, bed);
  const own = ownGround(g);
  clipToSite(g, own.terrains, own.boxes[0]);
  assert.equal(bed.material, grass);
  assert.equal(grass.clippingPlanes, null);
  assert.notEqual(lawn.material, grass);
  assert.equal(lawn.material.clippingPlanes.length, 4);
  assert.equal(lawn.material.onBeforeCompile, grass.onBeforeCompile);
});

test("paths and planting left out of the framing widen the site over the builder's ground, never past it", () => {
  const { g } = plot();
  // a driveway to the plot's south edge, a hedge along its east edge, a path past the terrain's west edge
  g.add(excluded(box(3, 0.05, 10, 0, 0, 10)));
  g.add(excluded(box(0.6, 1.5, 20, 19, 0, 0)));
  g.add(excluded(box(8, 0.05, 1.2, -22, 0, 0)));
  const own = ownGround(g);
  assert.equal(own.garden, 3);
  const [minX, minZ, maxX, maxZ] = xz(own.boxes[0]);
  assert.ok(near(maxZ, 15) && near(maxX, 20) && near(minX, -20), JSON.stringify([minX, minZ, maxX, maxZ]));
  assert.ok(near(minZ, -10 - SITE_MARGIN), `north, the hedge: ${minZ}`);
});

test("a garden piece beyond GARDEN_REACH (the builder's trees along a far road) does not widen the site", () => {
  const g = new THREE.Group();
  g.add(box(12, 7, 8));
  const lawn = excluded(sheet(180, 180));
  g.add(lawn);
  g.add(excluded(box(4, 0.1, 4, 6 + GARDEN_REACH + 10, 0, 0)));
  const own = ownGround(g);
  assert.equal(own.garden, 0);
  assert.ok(near(own.boxes[0].max.x, 6 + SITE_MARGIN));
});

test("a lawn the camera frames is the plot: part of the site, as before", () => {
  const g = new THREE.Group();
  g.add(box(12, 7, 8));
  g.add(sheet(50, 44));
  const own = ownGround(g);
  assert.equal(own.sheets, 0);
  assert.deepEqual(own.terrains, []);
  assert.ok(near(own.boxes[0].max.x, 25 + SITE_MARGIN));
  assert.equal(own.flat, true);
});

test("one broken piece (NaN in its geometry) leaves the site as it is", () => {
  const { g } = plot();
  const broken = box(1, 1, 1);
  broken.geometry.attributes.position.setX(0, NaN);
  g.add(broken);
  const [minX, , maxX] = xz(ownGround(g).boxes[0]);
  assert.ok(Number.isFinite(minX) && near(maxX, 6 + SITE_MARGIN));
});

test("nothing framed: no site, nothing clipped", () => {
  const g = new THREE.Group();
  g.add(excluded(sheet(180, 180)));
  const own = ownGround(g);
  assert.deepEqual(own.boxes, []);
  assert.equal(own.terrains.length, 1);
});
