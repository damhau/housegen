// The builder's ground and its site under the real surroundings (#123): what context.js clips, what it leaves.
import test from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import { GARDEN_REACH, SITE_MARGIN, SITE_SIDES, clipToSite, isSheet, lawnColour, ownGround, siteDistance } from "../ground.js";

const material = () => new THREE.MeshStandardMaterial();

function box(w, h, d, x = 0, y = 0, z = 0, mat = material()) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
  m.position.set(x, y + h / 2, z);
  return m;
}

/** The house: 12 × 8 m, its walls tagged as the kit's are. */
function home() {
  const h = box(12, 7, 8);
  h.userData.kind = "wall";
  return h;
}

/** A level sheet of ground `w` × `d` metres around (x, z), as `surface()` or a PlaneGeometry lying flat. */
function sheet(w, d, { x = 0, y = 0, z = 0, mat = material(), segments = 1 } = {}) {
  const m = new THREE.Mesh(new THREE.PlaneGeometry(w, d, segments, segments), mat);
  m.rotation.x = -Math.PI / 2;
  m.position.set(x, y, z);
  return m;
}

/** Level ground over a polygon [[x, z], ...] (with holes), as a builder cuts its terrain to the plot. */
function surface(polygon, holes = [], mat = material()) {
  const shape = new THREE.Shape(polygon.map(([x, z]) => new THREE.Vector2(x, -z)));
  for (const h of holes) shape.holes.push(new THREE.Path(h.map(([x, z]) => new THREE.Vector2(x, -z))));
  return new THREE.Mesh(new THREE.ShapeGeometry(shape).rotateX(-Math.PI / 2), mat);
}

/** A hedge from a to b ([x, z]): bushes of instanced leaf cards, 1.5 m high, as leafBush draws them. */
function hedge(a, b, { y = 0 } = {}) {
  const leaf = new THREE.PlaneGeometry(0.15, 0.15);
  const n = Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1]) / 0.5);
  const leaves = new THREE.InstancedMesh(leaf, material(), (n + 1) * 12);
  const m = new THREE.Matrix4();
  let k = 0;
  for (let i = 0; i <= n; i++) for (let j = 0; j < 12; j++) {
    const t = i / n, ang = (j / 12) * Math.PI * 2, r = 0.45;
    m.makeTranslation(a[0] + (b[0] - a[0]) * t + r * Math.cos(ang), y + 0.2 + (j % 4) * 0.4, a[1] + (b[1] - a[1]) * t + r * Math.sin(ang));
    leaves.setMatrixAt(k++, m);
  }
  return leaves;
}

/** A tree at (x, z): a trunk standing on the ground, its crown 3 m up, `spread` wide. */
function tree(x, z, spread, y = 0) {
  const g = new THREE.Group();
  g.add(box(0.3, 4, 0.3, x, y, z));
  const crown = new THREE.Mesh(new THREE.SphereGeometry(spread / 2, 12, 8), material());
  crown.position.set(x, y + 3 + spread / 2, z);
  g.add(crown);
  return g;
}

function excluded(o) {
  o.userData.excludeFromBounds = true;
  return o;
}

/** A house 12 × 8 m and its plot's terrain 40 × 30 m, tagged as the kit asks. */
function plot() {
  const g = new THREE.Group();
  g.add(home());
  const terrain = sheet(40, 30);
  terrain.userData.kind = "terrain";
  g.add(terrain);
  return { g, terrain };
}

const near = (a, b, eps = 0.01) => Math.abs(a - b) <= eps;
const xz = (b) => [b.min.x, b.min.z, b.max.x, b.max.z];
const inside = (site, x, z) => siteDistance(site, x, z).d < 0;
/** Whether the clip planes keep a point at (x, z). */
const kept = (planes, x, z) => planes.every((p) => p.distanceToPoint(new THREE.Vector3(x, 0, z)) >= 0);

test("a lawn drawn out to 90 m round the plot, untagged and left out of the framing, is ground: clipped to the site", () => {
  const { g, terrain } = plot();
  const lawn = excluded(sheet(180, 180, { y: -0.02, segments: 60 }));
  g.add(lawn);
  const own = ownGround(g);
  assert.equal(own.sheets, 1);
  assert.deepEqual(own.terrains, [terrain, lawn]);
  assert.equal(own.plot, "house");
  // the house and its apron, nothing more: no garden says where the plot ends
  assert.ok(near(own.box.max.x, 6 + SITE_MARGIN, 0.1) && near(own.box.min.z, -4 - SITE_MARGIN, 0.1), JSON.stringify(xz(own.box)));
  assert.equal(clipToSite(g, own.terrains, own.site), 2);
  for (const m of [terrain.material, lawn.material]) {
    assert.equal(m.clippingPlanes.length, own.site.length);
    assert.ok(kept(m.clippingPlanes, 0, 0) && kept(m.clippingPlanes, 8.5, 6.5));
    assert.ok(!kept(m.clippingPlanes, 10, 0) && !kept(m.clippingPlanes, 0, -8) && !kept(m.clippingPlanes, 60, 60));
  }
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
  assert.ok(near(own.box.max.x, 6 + SITE_MARGIN, 0.1));
});

test("trees, hedges and garden pieces left out of the framing are never ground, whatever their size", () => {
  const { g } = plot();
  // a hedge 60 m long along a road, a row of trees (instanced), a tall tree's crown: none is a level sheet
  const long = excluded(box(60, 1.6, 0.8, 0, 0, 14));
  const trees = excluded(new THREE.InstancedMesh(new THREE.PlaneGeometry(60, 60).rotateX(-Math.PI / 2), material(), 3));
  const crown = excluded(new THREE.Mesh(new THREE.SphereGeometry(25, 12, 8), material()));
  g.add(long, trees, crown);
  for (const o of [long, trees, crown]) assert.equal(isSheet(o), false);
  const own = ownGround(g);
  assert.equal(own.sheets, 0);
  clipToSite(g, own.terrains, own.site);
  for (const o of [long, trees, crown]) assert.equal(o.material.clippingPlanes, null);
});

test("a garden bed sharing the lawn's material stays whole: the lawn's material is cloned before the clip", () => {
  const { g } = plot();
  const grass = material();
  grass.onBeforeCompile = () => {};
  const lawn = excluded(sheet(180, 180, { mat: grass }));
  const bed = excluded(box(3, 0.1, 2, 25, 0, 0, grass));
  g.add(lawn, bed);
  const own = ownGround(g);
  clipToSite(g, own.terrains, own.site);
  assert.equal(bed.material, grass);
  assert.equal(grass.clippingPlanes, null);
  assert.notEqual(lawn.material, grass);
  assert.equal(lawn.material.clippingPlanes.length, own.site.length);
  assert.equal(lawn.material.onBeforeCompile, grass.onBeforeCompile);
});

test("a plot cut to its boundary and fenced: the site is that boundary, not a box past it (Gilles-Dev4)", () => {
  // a pentagon wider north than south, its terrain cut to it, a 180 m lawn round it with the plot as a hole
  const boundary = [[-17.7, -12.3], [-0.5, -13.5], [21, -13.5], [18.4, 12.1], [-20.5, 13.02]];
  const g = new THREE.Group();
  g.add(home());
  const terrain = surface(boundary);
  terrain.userData.kind = "terrain";
  const lawn = excluded(surface([[-90, -90], [90, -90], [90, 90], [-90, 90]], [boundary]));
  g.add(terrain, lawn);
  for (let i = 0; i < boundary.length; i++) g.add(hedge(boundary[i], boundary[(i + 1) % boundary.length]));
  const own = ownGround(g);
  assert.equal(own.sheets, 1);
  for (const [x, z] of boundary) assert.ok(Math.abs(siteDistance(own.site, x, z).d) < 0.2, `corner ${x}, ${z}`);
  // the old box ran past the south-east corner: 5 m east of it the real ground takes over
  assert.ok(!inside(own.site, 21, 11) && !inside(own.site, 19, 12.5) && inside(own.site, 17, 11));
  clipToSite(g, own.terrains, own.site);
  assert.ok(!kept(lawn.material.clippingPlanes, 21, 11) && kept(terrain.material.clippingPlanes, 0, 12.5));
});

test("hedges round a plot on a terrain drawn far wider: the site follows the hedges and their slant (TestVillaGille)", () => {
  const plotLine = [[-17.3, -11.9], [17.25, -13.0], [17.3, 8.0], [15.0, 11.6], [-20.0, 12.4]];
  const g = new THREE.Group();
  g.add(home());
  const terrain = sheet(85, 70, { segments: 20 });
  terrain.userData.kind = "terrain";
  g.add(terrain);
  for (let i = 0; i < plotLine.length; i++) g.add(hedge(plotLine[i], plotLine[(i + 1) % plotLine.length]));
  const own = ownGround(g);
  assert.equal(own.plot, "house");
  // within a hedge's half width (0.6 m) of its line, all round
  for (const [x, z] of plotLine) {
    const d = siteDistance(own.site, x, z).d;
    assert.ok(d < 0 && d > -0.9, `corner ${x}, ${z}: ${d}`);
  }
  // the west hedge leans 2.7 m: its box's north-west corner is not the plot's
  assert.ok(!inside(own.site, -20.5, -11.5));
  assert.ok(!inside(own.site, 0, -15) && !inside(own.site, 19, 0));
});

test("a garden left out of the framing ends the site at its hedges, not at a terrain drawn 3 m past them, and crowns overhanging don't widen it (Montelly49-Dev4)", () => {
  const g = new THREE.Group();
  g.add(home());
  const terrain = sheet(28, 29, { x: -1, z: 3, segments: 20 });
  terrain.userData.kind = "terrain";
  g.add(terrain);
  const garden = excluded(new THREE.Group());
  garden.add(hedge([-12, -8], [-12, 14]), hedge([-12, 14], [10.5, 14]), hedge([10.5, 14], [10.5, -8]));
  // a driveway to the street at the terrain's north edge, a broad tree by the east hedge
  garden.add(box(5, 0.05, 20, -8, 0, -1.5), tree(9, 4, 6));
  g.add(garden);
  const own = ownGround(g);
  assert.equal(own.plot, "garden");
  assert.ok(own.garden >= 4);
  assert.ok(near(own.box.max.x, 11, 0.15) && near(own.box.max.z, 14.5, 0.15) && near(own.box.min.x, -12.5, 0.15), JSON.stringify(xz(own.box)));
  assert.ok(near(own.box.min.z, -11.5, 0.15), `north: the driveway reaches the terrain's edge (${own.box.min.z})`);
  assert.ok(!inside(own.site, 12, 4), "the crown 3 m up overhangs the hedge: the real ground beyond");
});

test("a garden piece beyond GARDEN_REACH (the builder's trees along a far road) does not widen the site", () => {
  const g = new THREE.Group();
  g.add(home());
  const lawn = excluded(sheet(180, 180));
  g.add(lawn);
  g.add(excluded(box(4, 0.1, 4, 6 + GARDEN_REACH + 10, 0, 0)));
  const own = ownGround(g);
  assert.equal(own.garden, 0);
  assert.ok(near(own.box.max.x, 6 + SITE_MARGIN, 0.1));
});

test("a garden piece past the builder's ground does not widen the site: the real ground would be sunk with nothing over it", () => {
  const { g } = plot();
  g.add(excluded(box(8, 0.05, 1.2, -24, 0, 0)));
  g.add(excluded(box(3, 0.05, 10, 0, 0, 10)));
  const own = ownGround(g);
  assert.ok(near(own.box.min.x, -20, 0.1) && near(own.box.max.z, 15, 0.1), JSON.stringify(xz(own.box)));
});

test("a house at its terrain's edge: the apron stops there (no ditch past it)", () => {
  const g = new THREE.Group();
  g.add(home());
  const terrain = sheet(30, 20, { x: 9, z: 0 });
  terrain.userData.kind = "terrain";
  g.add(terrain);
  const own = ownGround(g);
  assert.ok(near(own.box.min.x, -6, 0.1) && near(own.box.max.x, 6 + SITE_MARGIN, 0.1), JSON.stringify(xz(own.box)));
});

test("a lawn the camera frames is the plot: the site is that lawn", () => {
  const g = new THREE.Group();
  g.add(home());
  g.add(sheet(50, 44));
  const own = ownGround(g);
  assert.equal(own.sheets, 0);
  assert.deepEqual(own.terrains, []);
  assert.ok(near(own.box.max.x, 25, 0.1) && near(own.box.min.z, -22, 0.1), JSON.stringify(xz(own.box)));
  assert.equal(own.flat, true);
});

test("a plot declared on the terrain (userData.plot) is the site, whatever the garden", () => {
  const { g, terrain } = plot();
  terrain.userData.plot = [[-15, -12], [18, -10], [16, 13], [-14, 12]];
  g.add(excluded(box(3, 0.05, 3, 19, 0, 13)));
  const own = ownGround(g);
  assert.equal(own.plot, "declared");
  assert.equal(own.site.length, 4);
  for (const [x, z] of terrain.userData.plot) assert.ok(Math.abs(siteDistance(own.site, x, z).d) < 1e-6);
  // a plot that leaves the house out is a mistake: the garden's rule
  terrain.userData.plot = [[20, 5], [30, 5], [30, 10]];
  assert.equal(ownGround(g).plot, "garden");
});

test("a hedge on a raised garden counts at its foot (heightAt), a crown above it does not", () => {
  const g = new THREE.Group();
  g.add(home());
  const terrain = sheet(60, 60, { y: 5 });
  terrain.userData.kind = "terrain";
  g.add(terrain);
  const garden = excluded(new THREE.Group());
  garden.add(hedge([14, -8], [14, 8], { y: 5 }), tree(-14, 0, 6, 5));
  g.add(garden);
  const own = ownGround(g, { heightAt: () => 5 });
  assert.ok(near(own.box.max.x, 14.5, 0.15), `the hedge on the raised garden: ${own.box.max.x}`);
  assert.ok(near(own.box.min.x, -14.15, 0.1), `the trunk stands, the crown 3 m up (to -17) does not count: ${own.box.min.x}`);
  // without the ground's height the hedge is taken for something 5 m up
  assert.ok(ownGround(g).box.max.x < 10);
});

test("a hedge winding along the plot's edge gives at most SITE_SIDES sides (a clipping plane each)", () => {
  const { g } = plot();
  const garden = excluded(new THREE.Group());
  for (let k = 0; k < 40; k++) {
    const a = (k / 40) * Math.PI * 2, b = ((k + 1) / 40) * Math.PI * 2;
    garden.add(hedge([18 * Math.cos(a), 13 * Math.sin(a)], [18 * Math.cos(b), 13 * Math.sin(b)]));
  }
  g.add(garden);
  const own = ownGround(g);
  assert.ok(own.site.length <= SITE_SIDES, `${own.site.length} sides`);
  assert.ok(inside(own.site, 17, 0) && inside(own.site, 0, 12.5) && !inside(own.site, 19.5, 0));
});

test("the distance to the site: negative inside, the outline's nearest point", () => {
  const site = [[0, 0], [10, 0], [10, 10], [0, 10]];
  assert.deepEqual(siteDistance(site, 5, 2), { d: -2, x: 5, z: 0 });
  assert.deepEqual(siteDistance(site, 13, 14), { d: 5, x: 10, z: 10 });
  assert.equal(siteDistance(site, 5, -1).d, 1);
});

test("the plot's lawn colour: a lawn's, none for a gravel plot or a backdrop", () => {
  const { terrain } = plot();
  terrain.material = new THREE.MeshStandardMaterial({ color: "#87976c" });
  assert.equal(`#${lawnColour([terrain]).getHexString()}`, "#87976c");
  terrain.material.userData = { finish: "grass", baseColor: new THREE.Color("#78815a") };
  assert.equal(`#${lawnColour([terrain]).getHexString()}`, "#78815a");
  terrain.material = new THREE.MeshStandardMaterial({ color: "#a39a8a" });
  assert.equal(lawnColour([terrain]), null);
  const backdrop = excluded(sheet(350, 350, { mat: new THREE.MeshStandardMaterial({ color: "#5d7a3c" }) }));
  assert.equal(lawnColour([terrain, backdrop]), null);
  // painted with vertex colours on a white material: their mean
  terrain.material = new THREE.MeshStandardMaterial({ vertexColors: true });
  const n = terrain.geometry.attributes.position.count;
  terrain.geometry.setAttribute("color", new THREE.BufferAttribute(new Float32Array(n * 3).map((_, i) => [0.2, 0.4, 0.1][i % 3]), 3));
  const mean = lawnColour([terrain]);
  assert.ok(near(mean.r, 0.2) && near(mean.g, 0.4) && near(mean.b, 0.1), mean.getHexString());
});

test("one broken piece (NaN in its geometry) leaves the site as it is", () => {
  const { g } = plot();
  const broken = box(1, 1, 1);
  broken.geometry.attributes.position.setX(0, NaN);
  g.add(broken);
  const own = ownGround(g);
  assert.ok(own.site.every(([x, z]) => Number.isFinite(x + z)) && near(own.box.max.x, 6 + SITE_MARGIN, 0.1));
});

test("nothing framed: no site, nothing clipped", () => {
  const g = new THREE.Group();
  g.add(excluded(sheet(180, 180)));
  const own = ownGround(g);
  assert.equal(own.site, null);
  assert.equal(own.terrains.length, 1);
});

test("a site given as a box clips as its rectangle", () => {
  const { g, terrain } = plot();
  clipToSite(g, [terrain], new THREE.Box3(new THREE.Vector3(-5, 0, -4), new THREE.Vector3(5, 0, 4)));
  const planes = terrain.material.clippingPlanes;
  assert.ok(kept(planes, 4.9, 3.9) && !kept(planes, 5.1, 0) && !kept(planes, 0, -4.1));
});
