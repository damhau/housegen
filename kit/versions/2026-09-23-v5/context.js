// housekit/context — the real surroundings of the plot (#39), for the viewer's presentation looks only:
// the ground as surveyed, the aerial photo on it, the neighbouring buildings. The backend fetched them
// from swisstopo (geo/context.py) into a folder the page is given as ?context=<url>; the build path
// (quality=…: builder, critic, version pictures) never loads them.
//
//   const ctx = await loadContext(url, { houseGroup, heightAt })   → { group, heightAt, disc, credits }
//
// The files are in a local frame around the searched point (x east, z south, y the altitude); the
// alignment in context.json places the scene in it (see geo/context.py). The surroundings are cut out
// under the builder's own ground (its terrain meshes, or the house's footprint on flat ground) and
// blended into it over a ring, and they fade into the meadow at their rim.

import * as THREE from "three";
import { finishMaterial, metricUV } from "./finishes.js";

const RING = 12; // metres over which the real ground joins the builder's
const RIM = 25; // metres over which the photo fades into the meadow at the edge of the disc
const DEG = Math.PI / 180;

/** Scene ← local transform of an alignment: rotate by `rotation`° (clockwise from above) about the scene's origin. */
function transforms(a) {
  const th = (a.rotation ?? 0) * DEG, c = Math.cos(th), s = Math.sin(th);
  // three.js: rotation.y = +th turns +x toward -z (counter-clockwise from above); the scene is turned
  // clockwise by th in the local frame, so local → scene turns counter-clockwise by th
  const toScene = (x, y, z) => {
    const dx = x - a.x, dz = z - a.z;
    return [c * dx + s * dz, y - a.ground, -s * dx + c * dz];
  };
  const toLocal = (x, z) => [a.x + c * x - s * z, a.z + s * x + c * z];
  return { toScene, toLocal };
}

/** The scene's own ground: its terrain meshes' boxes, or the house's footprint (+3 m) when it has none. */
function ownGround(houseGroup) {
  const boxes = [];
  const tmp = new THREE.Box3();
  houseGroup.traverse((o) => {
    if (o.isMesh && o.userData?.kind === "terrain" && !o.userData.excludeFromBounds) {
      tmp.setFromObject(o);
      if (!tmp.isEmpty()) boxes.push(tmp.clone());
    }
  });
  if (boxes.length) return { boxes, flat: false };
  const b = new THREE.Box3();
  houseGroup.traverse((o) => {
    if (!o.isMesh || o.userData?.excludeFromBounds) return;
    tmp.setFromObject(o);
    if (!tmp.isEmpty()) b.union(tmp);
  });
  return { boxes: b.isEmpty() ? [] : [b.expandByScalar(3)], flat: true };
}

const distToBox = (x, z, b) => Math.hypot(Math.max(b.min.x - x, x - b.max.x, 0), Math.max(b.min.z - z, z - b.max.z, 0));

/**
 * Load the surroundings at `url` (a folder with context.json). `heightAt(x, z)` is the scene's own
 * ground (house.groundY), used to join the real ground to it.
 */
export async function loadContext(url, { houseGroup, heightAt }) {
  const base = new URL(url, location.href);
  const get = (f) => fetch(new URL(f, base)).then((r) => { if (!r.ok) throw new Error(`${f}: HTTP ${r.status}`); return r; });
  const meta = await (await get("context.json")).json();
  const [terrainBuf, buildings, photo] = await Promise.all([
    get(meta.terrain.file).then((r) => r.arrayBuffer()),
    get(meta.buildings.file).then((r) => r.json()),
    new THREE.TextureLoader().loadAsync(new URL(meta.photo.file, base).href),
  ]);
  photo.colorSpace = THREE.SRGBColorSpace;
  photo.anisotropy = 8;
  const R = meta.radius, N = meta.terrain.size, step = meta.terrain.step;
  const alt = new Float32Array(terrainBuf);
  const { toScene, toLocal } = transforms(meta.alignment);
  const own = ownGround(houseGroup);
  const ownY = (x, z) => (own.flat ? 0 : (Number.isFinite(heightAt?.(x, z)) ? heightAt(x, z) : 0));

  // the real ground at a local point (bilinear on the grid), in scene y
  const realY = (lx, lz) => {
    const fi = Math.min(N - 1.001, Math.max(0, (lx + R) / step)), fj = Math.min(N - 1.001, Math.max(0, (lz + R) / step));
    const i = Math.floor(fi), j = Math.floor(fj), u = fi - i, v = fj - j;
    const h = (ii, jj) => alt[jj * N + ii];
    return (h(i, j) * (1 - u) + h(i + 1, j) * u) * (1 - v) + (h(i, j + 1) * (1 - u) + h(i + 1, j + 1) * u) * v - meta.alignment.ground;
  };

  // ---- the ground: the disc of the grid, cut out under the scene's own ground, joined to it
  const positions = new Float32Array(N * N * 3), uvs = new Float32Array(N * N * 2), fade = new Float32Array(N * N);
  const hole = new Uint8Array(N * N);
  for (let j = 0; j < N; j++) for (let i = 0; i < N; i++) {
    const k = j * N + i, lx = -R + i * step, lz = -R + j * step;
    const [x, y, z] = toScene(lx, alt[k], lz);
    let yy = y;
    let d = Infinity, nearest = null;
    for (const b of own.boxes) { const e = distToBox(x, z, b); if (e < d) { d = e; nearest = b; } }
    if (nearest) {
      if (d <= 0) hole[k] = 1;
      if (d < RING) {
        // on the rim of the builder's ground: its height there, easing into the real ground
        const cx = Math.min(nearest.max.x, Math.max(nearest.min.x, x)), cz = Math.min(nearest.max.z, Math.max(nearest.min.z, z));
        yy = THREE.MathUtils.lerp(ownY(cx, cz) - 0.03, y, THREE.MathUtils.smoothstep(d, 0, RING));
      }
    }
    positions.set([x, yy, z], k * 3);
    uvs.set([(lx + R) / (2 * R), 1 - (lz + R) / (2 * R)], k * 2);
    const r = Math.hypot(lx, lz);
    fade[k] = THREE.MathUtils.smoothstep(r, R - RIM, R);
  }
  const index = [];
  for (let j = 0; j < N - 1; j++) for (let i = 0; i < N - 1; i++) {
    const a = j * N + i, b = a + 1, c = a + N, d = c + 1;
    if (hole[a] && hole[b] && hole[c] && hole[d]) continue;
    const lx = -R + (i + 0.5) * step, lz = -R + (j + 0.5) * step;
    if (Math.hypot(lx, lz) > R - step) continue; // a disc: no corners poking out of the meadow
    index.push(a, c, b, b, c, d);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geo.setAttribute("uv", new THREE.BufferAttribute(uvs, 2));
  geo.setAttribute("fade", new THREE.BufferAttribute(fade, 1));
  geo.setIndex(index);
  geo.computeVertexNormals();
  const groundMat = new THREE.MeshStandardMaterial({ map: photo, roughness: 1 });
  const meadowColor = { value: new THREE.Color(0.35, 0.36, 0.25) };
  groundMat.onBeforeCompile = (shader) => {
    shader.uniforms.uMeadow = meadowColor;
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nattribute float fade;\nvarying float vFade;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvFade = fade;");
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", "#include <common>\nvarying float vFade;\nuniform vec3 uMeadow;")
      .replace("#include <map_fragment>", "#include <map_fragment>\ndiffuseColor.rgb = mix(diffuseColor.rgb, uMeadow, vFade);");
  };
  const ground = new THREE.Mesh(geo, groundMat);
  ground.receiveShadow = true;
  ground.name = "Surroundings: ground";

  // ---- the neighbours: roofs wear the photo (seen from above: their real colour, panels, windows),
  // walls a render; anything standing on the scene's own ground is the old state of the plot: left out
  const roofPos = [], roofUv = [], wallPos = [];
  let skipped = 0;
  for (const b of buildings.buildings ?? []) {
    const p = b.positions;
    const pts = [];
    for (let k = 0; k < p.length; k += 3) pts.push(toScene(p[k], p[k + 1], p[k + 2]));
    const cx = pts.reduce((s, q) => s + q[0], 0) / pts.length, cz = pts.reduce((s, q) => s + q[2], 0) / pts.length;
    if (own.boxes.some((bx) => distToBox(cx, cz, bx) <= 0)) { skipped++; continue; }
    for (const i of b.roof) {
      roofPos.push(...pts[i]);
      roofUv.push((p[i * 3] + R) / (2 * R), 1 - (p[i * 3 + 2] + R) / (2 * R));
    }
    for (const i of b.wall) wallPos.push(...pts[i]);
  }
  const group = new THREE.Group();
  group.name = "Surroundings";
  group.userData = { kind: "context", excludeFromBounds: true };
  group.add(ground);
  if (roofPos.length) {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(roofPos, 3));
    g.setAttribute("uv", new THREE.Float32BufferAttribute(roofUv, 2));
    g.computeVertexNormals();
    const roofs = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ map: photo, roughness: 0.85 }));
    roofs.castShadow = roofs.receiveShadow = true;
    roofs.name = "Surroundings: roofs";
    group.add(roofs);
  }
  if (wallPos.length) {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(wallPos, 3));
    g.computeVertexNormals();
    metricUV(g);
    const mat = finishMaterial("render", { color: "#e8e3d8" }) ?? new THREE.MeshStandardMaterial({ color: "#e8e3d8", roughness: 0.95 });
    const walls = new THREE.Mesh(g, mat);
    walls.castShadow = walls.receiveShadow = true;
    walls.name = "Surroundings: walls";
    group.add(walls);
  }
  group.traverse((o) => { if (o.isMesh) o.userData = { ...o.userData, excludeFromBounds: true }; });

  // the scene-frame disc the meadow must lie under, and the real ground's height in it
  const [cx, , cz] = toScene(0, 0, 0);
  const inDisc = (x, z) => { const [lx, lz] = toLocal(x, z); return Math.hypot(lx, lz) <= R; };
  return {
    group,
    meta,
    skipped,
    credits: meta.credits ?? [],
    disc: { x: cx, z: cz, radius: R },
    // the real ground under a scene point (NaN outside the disc)
    heightAt: (x, z) => { if (!inDisc(x, z)) return NaN; const [lx, lz] = toLocal(x, z); return realY(lx, lz); },
    setMeadow: (color) => meadowColor.value.copy(color),
  };
}

export default { loadContext };
