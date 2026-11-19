// housekit/ground — the builder's ground and its site, where the real surroundings take over (context.js, #65,
// #123). The site is the builder's plot seen from above, a convex outline:
// - the plot the builder declared on its ground (`userData.plot = [[x, z], ...]`), when it did;
// - else what stands on the builder's ground (the house with SITE_MARGIN around it, and every garden piece at its
//   foot: hedges, fences, paths, a driveway, trunks; tree crowns and roofs do not count), cut to the builder's plot
//   ground (its terrain meshes, a lawn the camera frames). A hedge round the plot is then its edge, not a box
//   several metres past it, and a terrain drawn wider than the plot ends at the plot.
// The builder's ground is every mesh tagged `kind: "terrain"` and any large flat mesh, whatever its tags (a lawn
// drawn out to 90 m around the plot, a backdrop): context.js clips it to the site, so it never lies over the real
// land beyond. Trees, hedges and garden pieces are never ground, whatever their size, and never clipped.
//
//   const own = ownGround(houseGroup, { heightAt })   → { site: [[x, z], ...] or null, box, flat, terrains, … }
//   clipToSite(houseGroup, own.terrains, own.site)   → how many meshes
//   siteDistance(own.site, x, z)   → { d (< 0 inside), x, z (the nearest point of the outline) }

import * as THREE from "three";

export const SITE_MARGIN = 3; // metres of the builder's ground kept around its house
// metres past the framed box within which a garden piece left out of the framing counts as the site: a plot's
// paths and hedges, not the builder's trees along a road it drew out to the horizon
export const GARDEN_REACH = 15;
// a mesh at least this wide both ways (metres), its faces nearly level (SHEET_LEVEL: the mean |normal.y|
// weighted by area) and its height a fifth of its width at most, is ground
export const SHEET_SPAN = 40;
const SHEET_LEVEL = 0.9;
// metres over the builder's ground within which a piece stands on it: a hedge's foot, a fence, a path; a tree's
// crown, a balcony, a roof's eaves are above it and do not make the plot wider
const AT_FOOT = 1;
const COLUMN = 0.1; // metres: the outlines are gathered in columns this wide (their lowest and highest z)
// the site's outline at most this many sides (a clipping plane each), corners under SIMPLIFY metres cut off first
export const SITE_SIDES = 16;
const SIMPLIFY = 0.15;
// the house: what the runtime frames as the building (its tagged walls and openings), the apron kept around it
const BUILDING = new Set(["wall", "perimeter", "window", "door", "building"]);

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

/** Points seen from above, gathered in columns COLUMN wide: their convex hull is theirs within COLUMN / 2. */
class Columns {
  constructor() { this.cols = new Map(); }
  add(x, z) {
    if (!Number.isFinite(x) || !Number.isFinite(z)) return;
    const k = Math.round(x / COLUMN), c = this.cols.get(k);
    if (!c) this.cols.set(k, [z, z]);
    else if (z < c[0]) c[0] = z;
    else if (z > c[1]) c[1] = z;
  }
  /** The convex hull, counter-clockwise (x right, z up), or null under three points. */
  hull() {
    const pts = [];
    for (const k of [...this.cols.keys()].sort((a, b) => a - b)) {
      const [lo, hi] = this.cols.get(k);
      pts.push([k * COLUMN, lo]);
      if (hi > lo) pts.push([k * COLUMN, hi]);
    }
    return convexHull(pts);
  }
}

const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);

/** The convex hull of [x, z] points sorted by x then z (Andrew's chain), counter-clockwise; null under 3 points. */
function convexHull(pts) {
  if (pts.length < 3) return null;
  const lower = [], upper = [];
  for (const p of pts) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
    lower.push(p);
  }
  for (let i = pts.length - 1; i >= 0; i--) {
    const p = pts[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
    upper.push(p);
  }
  const hull = lower.slice(0, -1).concat(upper.slice(0, -1));
  return hull.length >= 3 ? hull : null;
}

const sortXZ = (pts) => pts.sort((a, b) => a[0] - b[0] || a[1] - b[1]);

/** Convex `subject` cut to convex `clip` (both counter-clockwise), Sutherland–Hodgman; null when nothing is left. */
function intersect(subject, clip) {
  let out = subject;
  for (let i = 0; i < clip.length && out.length; i++) {
    const a = clip[i], b = clip[(i + 1) % clip.length], input = out;
    out = [];
    for (let j = 0; j < input.length; j++) {
      const p = input[j], q = input[(j + 1) % input.length];
      const sp = cross(a, b, p), sq = cross(a, b, q);
      if (sp >= 0) out.push(p);
      if ((sp >= 0) !== (sq >= 0)) {
        const t = sp / (sp - sq);
        out.push([p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t]);
      }
    }
  }
  return out.length >= 3 ? out : null;
}

/** A convex outline with its corners under SIMPLIFY metres cut off, then the smallest, to SITE_SIDES sides at most. */
function simplify(poly) {
  const p = poly.slice();
  const height = (i) => {
    const a = p[(i + p.length - 1) % p.length], b = p[i], c = p[(i + 1) % p.length];
    const len = Math.hypot(c[0] - a[0], c[1] - a[1]);
    return len < 1e-9 ? 0 : Math.abs(cross(a, c, b)) / len;
  };
  while (p.length > 3) {
    let best = -1, h = Infinity;
    for (let i = 0; i < p.length; i++) {
      const hi = height(i);
      if (hi < h) { h = hi; best = i; }
    }
    if (h >= SIMPLIFY && p.length <= SITE_SIDES) break;
    p.splice(best, 1);
  }
  return p;
}

function insideConvex(poly, x, z) {
  for (let i = 0; i < poly.length; i++) if (cross(poly[i], poly[(i + 1) % poly.length], [x, z]) < 0) return false;
  return true;
}

/**
 * Where a point stands against the site's outline: `d` the distance to it (negative inside), `x`, `z` its
 * nearest point on the outline.
 */
export function siteDistance(poly, x, z) {
  let best = Infinity, nx = x, nz = z;
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i], b = poly[(i + 1) % poly.length];
    const dx = b[0] - a[0], dz = b[1] - a[1], len2 = dx * dx + dz * dz;
    const t = len2 > 0 ? Math.min(1, Math.max(0, ((x - a[0]) * dx + (z - a[1]) * dz) / len2)) : 0;
    const px = a[0] + dx * t, pz = a[1] + dz * t, e = Math.hypot(x - px, z - pz);
    if (e < best) { best = e; nx = px; nz = pz; }
  }
  return { d: insideConvex(poly, x, z) ? -best : best, x: nx, z: nz };
}

/** The outline's box seen from above (y spanning everything). */
function boxOf(poly) {
  const b = new THREE.Box3(new THREE.Vector3(Infinity, -1e6, Infinity), new THREE.Vector3(-Infinity, 1e6, -Infinity));
  for (const [x, z] of poly) {
    b.min.x = Math.min(b.min.x, x); b.max.x = Math.max(b.max.x, x);
    b.min.z = Math.min(b.min.z, z); b.max.z = Math.max(b.max.z, z);
  }
  return b;
}

/** The plot a builder declared on an object of its scene (`userData.plot`, [[x, z], ...] in the scene's frame). */
function declaredPlot(houseGroup) {
  let found = null;
  houseGroup.traverse((o) => {
    const p = o.userData?.plot;
    if (found || !Array.isArray(p) || p.length < 3) return;
    const pts = p.filter((q) => Array.isArray(q) && Number.isFinite(q[0]) && Number.isFinite(q[q.length - 1]));
    if (pts.length < 3) return;
    const v = new THREE.Vector3();
    found = pts.map((q) => { v.set(q[0], 0, q[q.length - 1]).applyMatrix4(houseGroup.matrixWorld); return [v.x, v.z]; });
  });
  return found ? convexHull(sortXZ(found)) : null;
}

/** The builder's ground height under a region, sampled once on a metre grid (heightAt can be slow: spot heights). */
function footHeights(box, heightAt) {
  if (!heightAt || box.isEmpty()) return () => 0;
  const x0 = Math.floor(box.min.x) - 1, z0 = Math.floor(box.min.z) - 1;
  const nx = Math.ceil(box.max.x) + 2 - x0, nz = Math.ceil(box.max.z) + 2 - z0;
  if (nx * nz > 250000) return () => 0;
  const h = new Float32Array(nx * nz);
  for (let j = 0; j < nz; j++) for (let i = 0; i < nx; i++) {
    const y = heightAt(x0 + i, z0 + j);
    h[j * nx + i] = Number.isFinite(y) ? y : 0;
  }
  return (x, z) => {
    const fi = Math.min(nx - 1.001, Math.max(0, x - x0)), fj = Math.min(nz - 1.001, Math.max(0, z - z0));
    const i = Math.floor(fi), j = Math.floor(fj), u = fi - i, w = fj - j;
    const at = (a, b) => h[b * nx + a];
    return (at(i, j) * (1 - u) + at(i + 1, j) * u) * (1 - w) + (at(i, j + 1) * (1 - u) + at(i + 1, j + 1) * u) * w;
  };
}

/**
 * Every vertex of a mesh in the world: `each(x, y, z)`. An instance of a larger geometry (more than INSTANCE_POINTS
 * vertices) gives its box's corners.
 */
const INSTANCE_POINTS = 256;
function eachPoint(mesh, each) {
  const pos = mesh.geometry?.attributes?.position;
  if (!pos) return;
  const v = new THREE.Vector3();
  if (!mesh.isInstancedMesh) {
    for (let i = 0; i < pos.count; i++) {
      v.fromBufferAttribute(pos, i).applyMatrix4(mesh.matrixWorld);
      each(v.x, v.y, v.z);
    }
    return;
  }
  let local = [];
  if (pos.count <= INSTANCE_POINTS) {
    for (let i = 0; i < pos.count; i++) local.push(new THREE.Vector3().fromBufferAttribute(pos, i));
  } else {
    if (!mesh.geometry.boundingBox) mesh.geometry.computeBoundingBox();
    const { min, max } = mesh.geometry.boundingBox;
    local = [0, 1, 2, 3, 4, 5, 6, 7].map((k) => new THREE.Vector3(k & 1 ? max.x : min.x, k & 2 ? max.y : min.y, k & 4 ? max.z : min.z));
  }
  const m = new THREE.Matrix4();
  for (let i = 0; i < mesh.count; i++) {
    mesh.getMatrixAt(i, m);
    m.premultiply(mesh.matrixWorld);
    for (const p of local) {
      v.copy(p).applyMatrix4(m);
      each(v.x, v.y, v.z);
    }
  }
}

const inXZ = (b, x, z) => x >= b.min.x && x <= b.max.x && z >= b.min.z && z <= b.max.z;
const boxInXZ = (a, b) => a.min.x >= b.min.x && a.max.x <= b.max.x && a.min.z >= b.min.z && a.max.z <= b.max.z;
const overlapsXZ = (a, b) => a.min.x <= b.max.x && a.max.x >= b.min.x && a.min.z <= b.max.z && a.max.z >= b.min.z;

/**
 * The scene's own ground: the site's outline (null without anything framed) and its box, the builder's ground
 * meshes (`terrains`: the tagged ones, then the sheets left out of the framing), how many sheets and garden pieces
 * counted, where the outline came from (`plot`: "declared", "garden" or "house"), and `flat` when no framed mesh
 * is tagged as terrain (the builder's ground is then y = 0). A sheet the camera frames is the plot itself.
 * `heightAt(x, z)`: the builder's ground (house.groundY), to tell what stands at its foot.
 */
export function ownGround(houseGroup, { heightAt } = {}) {
  houseGroup.updateMatrixWorld(true);
  const framed = new THREE.Box3(), building = new THREE.Box3(), terrains = [], sheets = [], plotGround = [];
  const pieces = [];
  houseGroup.traverse((o) => {
    if (!o.isMesh) return;
    if (o.userData?.kind === "terrain") {
      terrains.push(o);
      if (!o.userData.excludeFromBounds) plotGround.push(o);
      return;
    }
    let excluded = Boolean(o.userData?.excludeFromBounds), house = BUILDING.has(o.userData?.kind);
    for (let p = o.parent; p && p !== houseGroup; p = p.parent) {
      if (p.userData?.kind === "terrain") return;
      if (p.userData?.excludeFromBounds) excluded = true;
      if (BUILDING.has(p.userData?.kind)) house = true;
    }
    const box = new THREE.Box3().setFromObject(o);
    // one broken piece (NaN in its geometry) would make the whole site NaN and clip all the ground away
    if (box.isEmpty() || !Number.isFinite(box.min.x + box.max.x + box.min.z + box.max.z)) return;
    const sheet = isSheet(o, box);
    if (!excluded) {
      framed.union(box);
      if (house) building.union(box);
      if (sheet) plotGround.push(o);
    } else if (sheet) sheets.push(o);
    pieces.push({ o, box, excluded: excluded && !sheet, sheet: excluded && sheet });
  });
  const ground = [...terrains, ...sheets];
  const flat = !terrains.some((t) => !t.userData.excludeFromBounds);
  const result = (site, plot, garden = 0) => ({
    site, box: site ? boxOf(site) : null, flat, terrains: ground, sheets: sheets.length, garden, plot,
  });
  if (framed.isEmpty()) return result(null, null);
  const house = building.isEmpty() ? framed : building;
  const declared = declaredPlot(houseGroup);
  const centre = house.getCenter(new THREE.Vector3());
  // a declared plot that leaves out the house is a mistake: the rule below then
  if (declared && insideConvex(declared, centre.x, centre.z)) return result(simplify(declared), "declared");

  const apron = house.clone().expandByScalar(SITE_MARGIN);
  const reach = framed.clone().expandByScalar(GARDEN_REACH);
  const foot = footHeights(reach, flat ? null : heightAt);
  const content = new Columns();
  for (const x of [apron.min.x, apron.max.x]) for (const z of [apron.min.z, apron.max.z]) content.add(x, z);
  let garden = 0;
  for (const { o, box, excluded, sheet } of pieces) {
    // the house's insides add nothing to the apron round it; a backdrop is not the plot
    if (sheet || boxInXZ(box, apron)) continue;
    if (excluded && !overlapsXZ(box, reach)) continue;
    let used = false;
    eachPoint(o, (x, y, z) => {
      if (y > foot(x, z) + AT_FOOT || (excluded && !inXZ(reach, x, z))) return;
      content.add(x, z);
      used = true;
    });
    if (used && excluded) garden++;
  }
  let site = content.hull();
  if (!site) return result(null, null);
  // cut to the builder's plot ground: the garden pieces only where its ground lies under them (elsewhere the real
  // ground would be sunk under the site with nothing over it), the apron not past its edge (a ditch)
  if (plotGround.length) {
    const under = new Columns();
    for (const g of plotGround) eachPoint(g, (x, y, z) => under.add(x, z));
    const plot = under.hull();
    const cut = plot && intersect(site, plot);
    if (cut) site = cut;
  }
  return result(simplify(site), garden ? "garden" : "house", garden);
}

/**
 * Clip the builder's ground meshes to the site's outline seen from above (the real ground beyond it): returns how
 * many. `site`: the convex outline (ownGround) or a Box3. A material a ground mesh shares with anything else in the
 * scene (a lawn's grass on a garden bed) is cloned first: the clip planes belong to the material, and the bed must
 * stay whole.
 */
export function clipToSite(houseGroup, terrains, site) {
  const poly = site?.isBox3
    ? [[site.min.x, site.min.z], [site.max.x, site.min.z], [site.max.x, site.max.z], [site.min.x, site.max.z]]
    : site;
  // three keeps what lies on the planes' positive side: each side's normal points into the site
  const planes = poly.map((a, i) => {
    const b = poly[(i + 1) % poly.length];
    const n = new THREE.Vector3(-(b[1] - a[1]), 0, b[0] - a[0]).normalize();
    return new THREE.Plane(n, -(n.x * a[0] + n.z * a[1]));
  });
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

/**
 * The colour the builder's plot ground reads as (linear), when it is a lawn: the real ground's grass takes it on
 * toward the plot's edge (context.js), so the plot doesn't read as pasted on. Null for a gravel or paved plot.
 */
export function lawnColour(terrains) {
  let best = null, area = 0;
  for (const t of terrains) {
    if (t.userData?.excludeFromBounds) continue;
    const b = new THREE.Box3().setFromObject(t);
    const a = (b.max.x - b.min.x) * (b.max.z - b.min.z);
    if (a > area) { area = a; best = t; }
  }
  const m = Array.isArray(best?.material) ? best.material[0] : best?.material;
  if (!m) return null;
  let c = m.userData?.baseColor ?? m.color;
  if (!c?.isColor) return null;
  // a ground painted with vertex colours reads as their mean (as the runtime's presentation ground takes it)
  const colors = best.geometry?.attributes?.color;
  if (m.vertexColors && colors?.count) {
    const mean = new THREE.Color(0, 0, 0);
    for (let i = 0; i < colors.count; i++) { mean.r += colors.getX(i); mean.g += colors.getY(i); mean.b += colors.getZ(i); }
    c = mean.multiplyScalar(1 / colors.count).multiply(c);
  }
  const grass = m.userData?.finish === "grass" || (c.g > c.r && c.g > c.b);
  return grass ? c.clone() : null;
}

export default { SITE_MARGIN, GARDEN_REACH, SHEET_SPAN, SITE_SIDES, levelOf, isSheet, ownGround, siteDistance, clipToSite, lawnColour };
