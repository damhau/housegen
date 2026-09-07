// Plausibility audit (#3): bounding-box checks over a scene group, no renderer needed.
import "./setup-dom.mjs";
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
  assert.match(lines[0], /trampoline at \(4, 6\) intersects tree at \(4(\.\d)?, 6(\.\d)?\)/);
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

// ---- textures (#16) ----
import { readdirSync, statSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const TEX_DIR = join(dirname(fileURLToPath(import.meta.url)), "..", "assets", "textures");

test("every texture set of the manifest is on disk, under the size caps, and licensed", () => {
  const manifest = JSON.parse(readFileSync(join(TEX_DIR, "manifest.json"), "utf8"));
  const licenses = readFileSync(join(TEX_DIR, "LICENSES.md"), "utf8");
  assert.deepEqual(Object.keys(house.TEXTURES).sort(), Object.keys(manifest).sort());
  let total = 0;
  for (const [name, entry] of Object.entries(manifest)) {
    for (const map of ["color", "normal", "roughness"]) assert.ok(entry.maps.includes(map), `${name} lacks ${map}`);
    for (const map of entry.maps) {
      const size = statSync(join(TEX_DIR, name, `${map}.jpg`)).size;
      assert.ok(size <= 300_000, `${name}/${map}.jpg is ${size} bytes`);
      total += size;
    }
    assert.match(licenses, new RegExp(entry.id), `${entry.id} missing from LICENSES.md`);
  }
  assert.ok(total < 10_000_000, `textures total ${total} bytes`);
  const dirs = readdirSync(TEX_DIR).filter((n) => statSync(join(TEX_DIR, n)).isDirectory());
  assert.deepEqual(dirs.sort(), Object.keys(manifest).sort());
});

test("textured materials: names, tint, cache, and the object form of the plain ones", () => {
  const a = house.mat.tex("roughcast", { color: "#e2d9c8", scale: 1.5 });
  assert.equal(a.userData.texture, "roughcast");
  assert.equal(a.userData.scale, 1.5);
  assert.equal(a.color.getHexString(), "e2d9c8");
  assert.equal(house.mat.tex("roughcast", { color: "#e2d9c8", scale: 1.5 }), a); // cached
  assert.throws(() => house.mat.tex("velvet"), /unknown texture/);
  assert.equal(house.mat.plaster({ color: "#ffffff" }).userData.texture, "plaster");
  assert.equal(house.mat.roof({ texture: "tiles" }).userData.texture, "tiles");
  assert.equal(house.mat.plaster({ color: "#e9e6dd", texture: null }).userData.texture, undefined);
  assert.equal(house.mat.plaster("#e9e6dd"), house.mat.plaster("#e9e6dd")); // positional form untouched
});

test("boxes and planes get UVs in metres so textures tile by world size", () => {
  const b = house.box({ size: [4, 3, 0.5] });
  const uv = b.geometry.attributes.uv;
  let maxU = 0, maxV = 0;
  for (let i = 0; i < uv.count; i++) { maxU = Math.max(maxU, uv.getX(i)); maxV = Math.max(maxV, uv.getY(i)); }
  assert.equal(maxU, 4);
  assert.equal(maxV, 3);
  const plane = house.uvsInMetres(new THREE.PlaneGeometry(10, 20), 10, 20);
  const p = plane.attributes.uv;
  assert.equal(Math.max(...Array.from({ length: p.count }, (_, i) => p.getX(i))), 10);
  assert.equal(Math.max(...Array.from({ length: p.count }, (_, i) => p.getY(i))), 20);
  const r = house.ribbon({ points: [[0, 0], [0, 6]], width: 1.4 });
  assert.ok(r.geometry.attributes.uv, "ribbon has UVs");
});


// ---- vegetation (#15) ----
function triangles(obj) {
  let n = 0;
  obj.traverse((o) => { if (o.isMesh && o.geometry?.index) n += o.geometry.index.count / 3; });
  return n;
}

/** A cheap fingerprint of the geometry (sum of the first positions of every mesh). */
function fingerprint(obj) {
  let h = 0;
  obj.traverse((o) => {
    const p = o.isMesh && o.geometry?.attributes.position;
    if (p) for (let i = 0; i < Math.min(p.count, 300); i++) h += p.getX(i) * 3 + p.getY(i) * 7 + p.getZ(i) * 11;
  });
  return Math.round(h * 1000);
}

test("trees come from ez-tree, sized to height and spread, deterministic by seed", () => {
  assert.equal(house.vegetationEngine(), "ez-tree");
  fresh();
  const a = house.leafTree({ position: [2, 3], height: 8, spread: 4, kind: "broadleaf", seed: 11, detail: "high" });
  assert.equal(a.userData.kind, "tree");
  assert.equal(a.userData.preset, "Oak Medium");
  a.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(a);
  const size = box.getSize(new THREE.Vector3());
  assert.ok(Math.abs(size.y - 8) < 0.05, `height ${size.y}`);
  assert.ok(Math.abs(Math.max(size.x, size.z) - 4) < 0.05, `spread ${Math.max(size.x, size.z)}`);
  assert.ok(Math.abs(box.min.y) < 0.05, `base at ${box.min.y}`);
  const b = house.leafTree({ position: [2, 3], height: 8, spread: 4, kind: "broadleaf", seed: 11, detail: "high" });
  assert.equal(triangles(a), triangles(b));
  assert.equal(fingerprint(a), fingerprint(b)); // same seed → the same tree
  const c = house.leafTree({ position: [2, 3], height: 8, spread: 4, kind: "broadleaf", seed: 12, detail: "high" });
  assert.notEqual(fingerprint(a), fingerprint(c));
  assert.equal(house.leafTree({ position: [0, 0], kind: "pine", seed: 1 }).userData.preset, "Pine Medium");
  assert.equal(house.leafTree({ position: [0, 0], kind: "Ash Large", seed: 1 }).userData.preset, "Ash Large");
});

test("low detail trees carry far fewer triangles; the runtime's default applies", () => {
  fresh();
  const hi = triangles(house.leafTree({ position: [0, 0], height: 7, seed: 5, detail: "high" }));
  const lo = triangles(house.leafTree({ position: [0, 0], height: 7, seed: 5, detail: "low" }));
  assert.ok(lo < hi / 2, `low ${lo} vs high ${hi}`);
  house.setVegetationDetail("low");
  assert.equal(triangles(house.leafTree({ position: [0, 0], height: 7, seed: 5 })), lo);
  house.setVegetationDetail("high");
  assert.equal(triangles(house.leafTree({ position: [0, 0], height: 7, seed: 5 })), hi);
});

test("bushes use the bush presets and keep their kind tag", () => {
  fresh();
  const b = house.leafBush({ position: [1, 1], radius: 0.8, seed: 4 });
  assert.equal(b.userData.kind, "bush");
  b.updateMatrixWorld(true);
  const size = new THREE.Box3().setFromObject(b).getSize(new THREE.Vector3());
  assert.ok(Math.abs(Math.max(size.x, size.z) - 1.6) < 0.05, `width ${Math.max(size.x, size.z)}`);
  const p = house.proceduralTree({ position: [0, 0], height: 6, seed: 3 });
  assert.equal(p.userData.kind, "tree"); // the fallback keeps the audit's tag
});

// ---- interior mapping (#17) ----
test("window panes carry the interior mapping material unless glassOnly", () => {
  const w = house.windowUnit({ width: 1.2, height: 1.3 });
  const panes = [];
  w.traverse((o) => { if (o.isMesh && o.material?.userData?.interior) panes.push(o); });
  assert.equal(panes.length, 1);
  const m = panes[0].material;
  assert.ok(m.isShaderMaterial);
  assert.deepEqual(m.uniforms.uRoom.value.toArray(), [3.2, 2.6, 3.5]);
  const w2 = house.windowUnit({ width: 1.2, height: 1.3 });
  let m2 = null;
  w2.traverse((o) => { if (o.isMesh && o.material?.userData?.interior) m2 = o.material; });
  assert.notEqual(m2.userData.seed, m.userData.seed); // each pane its own variant, in build order
  const plain = house.windowUnit({ width: 1.2, height: 1.3, glassOnly: true });
  let glass = null;
  plain.traverse((o) => { if (o.isMesh && o.material?.isMeshPhysicalMaterial) glass = o.material; });
  assert.ok(glass, "glassOnly keeps the physical glass");
  const lit = house.mat.interior({ lightOn: true, palette: 2, seed: 9, roomDepth: 5 });
  assert.equal(lit.uniforms.uLight.value, 1);
  assert.equal(lit.uniforms.uRoom.value.z, 5);
  assert.equal(lit.userData.seed, 9);
  const d = house.door({ glass: true });
  let dm = null;
  d.traverse((o) => { if (o.isMesh && o.material?.userData?.interior) dm = o.material; });
  assert.ok(dm, "a glazed door shows a room too");
  assert.equal(house.slidingDoor({ glassOnly: true }).userData.kind, "window");
});
