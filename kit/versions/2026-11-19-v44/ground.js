// housekit/ground — the builder's ground and its site, where the real surroundings take over (context.js, #65,
// #123). The site is the box of what the camera frames (the house and the garden it holds), grown by the garden
// pieces the builder left out of the framing (paths, a driveway, planting, boundaries) where they lie on its
// ground within GARDEN_REACH, then SITE_MARGIN wider. The builder's ground is every mesh tagged
// `kind: "terrain"` and any large flat mesh, whatever its tags (a lawn drawn out to 90 m around the plot, a
// backdrop): context.js clips it to the site, so it never lies over the real land beyond. Trees, hedges and
// garden pieces are never ground, whatever their size, and never clipped.
//
//   const own = ownGround(houseGroup)   → { boxes: [site] or [], flat, terrains, sheets, garden }
//   clipToSite(houseGroup, own.terrains, own.boxes[0])   → how many meshes

import * as THREE from "three";

export const SITE_MARGIN = 3; // metres of the builder's ground kept around its site
// metres past the framed box within which a garden piece left out of the framing counts as the site: a plot's
// paths and hedges, not the builder's trees along a road it drew out to the horizon
export const GARDEN_REACH = 15;
// a mesh at least this wide both ways (metres), its faces nearly level (SHEET_LEVEL: the mean |normal.y|
// weighted by area) and its height a fifth of its width at most, is ground
export const SHEET_SPAN = 40;
const SHEET_LEVEL = 0.9;

/** How level a mesh's faces are, in the world: their mean |normal.y|, weighted by area (0 upright, 1 level). */
export function levelOf(mesh) {
  const pos = mesh.geometry?.attributes?.position;
  if (!pos) return 0;
  const index = mesh.geometry.index;
  const count = Math.floor((index ? index.count : pos.count) / 3);
  const normals = new THREE.Matrix3().getNormalMatrix(mesh.matrixWorld);
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
  // a lawn subdivided to the decimetre has hundreds of thousands of faces: a sample tells as well
  const every = Math.max(1, Math.floor(count / 20000));
  let up = 0, all = 0;
  for (let t = 0; t < count; t += every) {
    const [i, j, k] = [0, 1, 2].map((q) => (index ? index.getX(t * 3 + q) : t * 3 + q));
    a.fromBufferAttribute(pos, i);
    b.fromBufferAttribute(pos, j).sub(a);
    c.fromBufferAttribute(pos, k).sub(a);
    const n = b.cross(c).applyMatrix3(normals);
    up += Math.abs(n.y);
    all += n.length();
  }
  return all > 0 ? up / all : 0;
}

/** Whether a mesh is a sheet of ground: large, nearly flat, its faces level (its world box: `box`). */
export function isSheet(mesh, box = new THREE.Box3().setFromObject(mesh)) {
  if (!mesh.isMesh || mesh.isInstancedMesh || box.isEmpty()) return false;
  const span = Math.min(box.max.x - box.min.x, box.max.z - box.min.z);
  if (span < SHEET_SPAN || box.max.y - box.min.y > span / 5) return false;
  return levelOf(mesh) >= SHEET_LEVEL;
}

/** `a` cut down to `b` seen from above (x and z), in place. */
function cutXZ(a, b) {
  a.min.x = Math.max(a.min.x, b.min.x); a.max.x = Math.min(a.max.x, b.max.x);
  a.min.z = Math.max(a.min.z, b.min.z); a.max.z = Math.min(a.max.z, b.max.z);
  return a;
}

/**
 * The scene's own ground: the site's box (none without anything framed), the builder's ground meshes (`terrains`:
 * the tagged ones, then the sheets left out of the framing), how many sheets and garden pieces counted, and
 * `flat` when no framed mesh is tagged as terrain (the builder's ground is then y = 0). A sheet the camera
 * frames is the plot itself: part of the site, as before.
 */
export function ownGround(houseGroup) {
  houseGroup.updateMatrixWorld(true);
  const framed = new THREE.Box3(), under = new THREE.Box3(), garden = [], terrains = [], sheets = [];
  houseGroup.traverse((o) => {
    if (!o.isMesh) return;
    if (o.userData?.kind === "terrain") {
      terrains.push(o);
      under.union(new THREE.Box3().setFromObject(o));
      return;
    }
    let excluded = Boolean(o.userData?.excludeFromBounds);
    for (let p = o.parent; p && p !== houseGroup; p = p.parent) {
      if (p.userData?.kind === "terrain") return;
      if (p.userData?.excludeFromBounds) excluded = true;
    }
    const box = new THREE.Box3().setFromObject(o);
    // one broken piece (NaN in its geometry) would make the whole site NaN and clip all the ground away
    if (box.isEmpty() || !Number.isFinite(box.min.x + box.max.x + box.min.z + box.max.z)) return;
    const sheet = isSheet(o, box);
    if (sheet) under.union(box);
    if (!excluded) framed.union(box);
    else if (sheet) sheets.push(o);
    else garden.push(box);
  });
  const ground = [...terrains, ...sheets];
  const flat = !terrains.some((t) => !t.userData.excludeFromBounds);
  if (framed.isEmpty()) return { boxes: [], flat, terrains: ground, sheets: sheets.length, garden: 0 };
  // the garden pieces only where the builder's ground lies under them: elsewhere the real ground would be sunk
  // under the site with nothing over it
  const reach = framed.clone().expandByScalar(GARDEN_REACH);
  const grown = framed.clone();
  let counted = 0;
  if (!under.isEmpty()) {
    for (const g of garden) {
      const part = cutXZ(cutXZ(g, reach), under);
      if (part.isEmpty()) continue;
      grown.union(part);
      counted++;
    }
  }
  // the margin only where the builder's ground goes on: past its edge it would sink the real ground into a ditch
  const site = framed.clone().expandByScalar(SITE_MARGIN);
  if (counted) site.union(cutXZ(grown.expandByScalar(SITE_MARGIN), under));
  return { boxes: [site], flat, terrains: ground, sheets: sheets.length, garden: counted };
}

/**
 * Clip the builder's ground meshes to the site's box seen from above (the real ground beyond it): returns how
 * many. A material a ground mesh shares with anything else in the scene (a lawn's grass on a garden bed) is
 * cloned first: the clip planes belong to the material, and the bed must stay whole.
 */
export function clipToSite(houseGroup, terrains, box) {
  const planes = [
    new THREE.Plane(new THREE.Vector3(1, 0, 0), -box.min.x), new THREE.Plane(new THREE.Vector3(-1, 0, 0), box.max.x),
    new THREE.Plane(new THREE.Vector3(0, 0, 1), -box.min.z), new THREE.Plane(new THREE.Vector3(0, 0, -1), box.max.z),
  ];
  const ground = new Set(terrains), shared = new Set();
  houseGroup.traverse((o) => {
    if (!o.material || ground.has(o)) return;
    for (const m of Array.isArray(o.material) ? o.material : [o.material]) shared.add(m);
  });
  const clones = new Map();
  const copy = (m) => {
    const c = m.clone();
    // clone() leaves out a shader patch set on the material itself (a finish's, the scene's own)
    for (const k of ["onBeforeCompile", "customProgramCacheKey"]) if (Object.hasOwn(m, k)) c[k] = m[k];
    return c;
  };
  const clipped = (m) => {
    if (!m) return m;
    if (shared.has(m)) {
      if (!clones.has(m)) clones.set(m, copy(m));
      m = clones.get(m);
    }
    m.clippingPlanes = planes;
    m.clipShadows = true;
    m.needsUpdate = true;
    return m;
  };
  for (const t of terrains) t.material = Array.isArray(t.material) ? t.material.map(clipped) : clipped(t.material);
  return terrains.length;
}

export default { SITE_MARGIN, GARDEN_REACH, SHEET_SPAN, levelOf, isSheet, ownGround, clipToSite };
