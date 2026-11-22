// housekit/roofs — the neighbours' roofs drawn instead of the aerial photo (#144). Each house's pitched faces wear its
// cover (interlocking clay, beaver tails, fibre-cement slates, concrete tiles, standing seam metal), its level faces
// gravel, a green roof or a membrane: one white-balanced finish per kind, tinted per house by vertex colour, the tile
// rows along each face's eave at the cover's real size. A ridge cap runs along its ridges and hips, a gutter along its
// eaves, and its solar modules (dark glass, an aluminium frame, a faint cell grid) are instanced. Its flat roofs (#146)
// get a parapet with a metal coping along their open edges, the equipment the backend placed on them (vents,
// skylights, hatches, units, stacks, housings, rails, slab walkways; instanced), and gravel in darker patches.
//
// What a house wears comes from the backend's roof spec (buildings.json `roof_spec`, #143: cover, flat, colour, family,
// panels and props in the frame of `positions`), else from the kit: the colour family of its photographed roof
// (roofFamily over the photo's median, as neighbours.js does for the newer houses) and its id's hash.
//
//   const choice = roofChoice(house, { photographed, neighbourhood, slope })   → { cover, flat, colour, … }
//   const faces = roofFaces(pts, tris)          the roof's faces by plane, each with its eave frame (u along, v up)
//   const { ridges, eaves, parapets } = roofEdges(faces)   where the caps, the gutters and the parapets go (runs
//                                               along the open edges of its level faces)
//   const roofs = planRoofs(houses, photographed)   geometry and finishes asked for; after finishesReady():
//   roofs.meshes({ wallMaterial })              → a group: a mesh per finish, the caps, gutters and copings, the
//                                                 parapets' outer faces (in the walls' material), modules, equipment
//
// A house: { id, pts: [[x, y, z]…] in the scene, roof: [i…] (its roof triangles), spec, panels: [[c0, c1, c2, c3]…],
// props: specProps(spec), render (its walls' colour) }.

import * as THREE from "three";
import { finishMaterial, metricUV } from "./finishes.js";
import { FLAT_ROOF, ROOF_TINTS, houseHash, roofFamily, roofTint } from "./neighbours.js";

export const COVERS = ["clay-interlocking", "clay-beaver", "fibre-cement", "concrete-tile", "metal-seam"];
export const FLATS = ["gravel", "green", "membrane"];
export const FAMILIES = Object.keys(ROOF_TINTS);

// each kind's finish (finishes.js), or a texture drawn here (`drawn`), and its surface
const LOOKS = {
  "clay-interlocking": { finish: "roof-clay", roughness: 0.85 },
  "clay-beaver": { finish: "roof-beaver", roughness: 0.85 },
  "fibre-cement": { finish: "roof-fibre-cement", roughness: 0.9 },
  "concrete-tile": { finish: "roof-concrete", roughness: 0.8 },
  // coil-coated steel or pre-weathered zinc: a paint, a matt metal, never a mirror
  "metal-seam": { drawn: "seam", roughness: 0.62, metalness: 0 },
  gravel: { finish: "gravel", roughness: 1 },
  green: { finish: "grass", roughness: 1 },
  membrane: { drawn: "membrane", roughness: 0.75 },
};

// the kit's own pick of a cover for a photographed colour family (the house's hash draws in these shares)
const BY_FAMILY = {
  terracotta: [["clay-interlocking", 0.75], ["clay-beaver", 0.25]],
  brown: [["clay-interlocking", 0.6], ["clay-beaver", 0.25], ["concrete-tile", 0.15]],
  grey: [["fibre-cement", 0.45], ["concrete-tile", 0.4], ["metal-seam", 0.15]],
  anthracite: [["fibre-cement", 0.45], ["concrete-tile", 0.3], ["metal-seam", 0.25]],
};
// degrees: a roof pitched less than this keeps the rain out with standing seam metal, no tile does
export const LOW_PITCH = 15;
// a level face's colour (sRGB): washed gravel, sedum, bitumen or a light synthetic sheet
const FLAT_TINTS = {
  gravel: ["#948e82", "#8b857b", "#9c968a", "#827c73"],
  green: ["#6e7643", "#77794a", "#636d3d", "#7a7250"],
  membrane: ["#5e6165", "#7d8185", "#a9adb0"],
};
// gutters: titanium zinc, copper gone brown, anthracite on newer houses
const GUTTERS = { zinc: "#9a9fa2", copper: "#7f5a43", anthracite: "#45484c" };
// ROOF_TINTS lit by the sun read as fresh orange; the aerial photo they were read from shows years of weather and haze
const WEATHER = 0.2; // a cover's colour moves this share of the way to its own grey (linear, Rec. 709 luminance)

const COPLANAR = Math.cos((4 * Math.PI) / 180);
const RIDGE_ANGLE = Math.cos((8 * Math.PI) / 180);
const MIN_EAVE = 1.2; // metres: a shorter level edge gets no gutter
const CAP = { width: 0.15, height: 0.08, lift: 0.02 }; // a ridge tile's half width, its rise over the ridge
const GUTTER = [[0, -0.04], [0.13, -0.02], [0.13, -0.08], [0.1, -0.13], [0.035, -0.13], [0, -0.09]]; // (out, up), metres
const MODULE = { thick: 0.04, frame: 0.035, cell: 0.166, line: 0.004 };
// a flat roof's parapet (#146): the upstand's height (0.25–0.45 m from the house's hash; `low` when the house's level
// faces add up to less than `small` m²: an annex, a dormer), its thickness inward, the coping's overhang, thickness and
// drips; the upstand starts `foot` under the roof. A level face under `minArea` m² (the lidar's noise) has none.
const PARAPET = { thick: 0.25, low: 0.15, small: 25, minArea: 2, over: 0.04, cap: 0.03, drip: 0.06, dripIn: 0.03, foot: 0.02 };
const COPINGS = ["#45484c", "#9a9fa2", "#b4b7b6"]; // anthracite, zinc, light grey aluminium
const FLASHING = "#5e6165"; // the upstand's inner face: the membrane turned up it
// metres: a parapet's edge is probed this far out; a roof of the house there as high or higher, or a slope going on from
// the edge (a mansard's flat top), has none
const PROBE = 0.3;
// the equipment's surfaces: sRGB colour and the material they share (roughness, metalness)
const SURFACES = {
  galvanised: ["#9da3a6", "metal"], steel: ["#a9adb0", "metal"], grey: ["#7f8386", "paint"], unit: ["#b3b6b5", "paint"],
  dark: ["#33363a", "paint"], door: ["#3b3f43", "paint"], dome: ["#e2e4e1", "dome"], glass: ["#1b232a", "glass"],
  slab: ["#a7a298", "mineral"],
};
const FINISHES = { metal: [0.42, 0.6], paint: [0.55, 0.25], dome: [0.25, 0], glass: [0.05, 0], mineral: [0.9, 0] };
export const PROPS = ["vent", "skylight-dome", "skylight-flat", "hatch", "hvac", "stack", "housing", "rail", "walkway"];
const SINK = 0.05; // metres a piece goes into the roof: a level face of the survey tilts up to 5°
const SLAB = 0.5; // metres: a walkway's paving slab
// gravel's darker patches: the multiplier (linear) at a full patch, the noise's two scales (metres), and a wide band
// from its median to its 90th percentile (half a roof touched, a seventh fully: a narrow band reads as camouflage)
const PATCHES = { tint: [0.78, 0.8, 0.72], scales: [3, 1.1], from: 0.5, to: 0.71 };

const srgb = (hex) => [0, 2, 4].map((k) => parseInt(hex.slice(1 + k, 3 + k), 16) / 255);
const linear = ([r, g, b]) => new THREE.Color().setRGB(r, g, b, THREE.SRGBColorSpace);
const v3 = (p) => new THREE.Vector3(p[0], p[1], p[2]);
const Q = 50; // 2 cm: corners closer than that are the same corner
const cornerKey = (p) => `${Math.round(p[0] * Q)},${Math.round(p[1] * Q)},${Math.round(p[2] * Q)}`;
const weathered = (c) => {
  const y = 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b;
  return c.setRGB(c.r + (y - c.r) * WEATHER, c.g + (y - c.g) * WEATHER, c.b + (y - c.b) * WEATHER);
};

/** A roof spec as the backend wrote it, its fields checked one by one (an unknown cover is the kit's to pick). */
export function validSpec(spec) {
  if (!spec || typeof spec !== "object") return null;
  const hex = typeof spec.colour === "string" && /^#[0-9a-f]{6}$/i.test(spec.colour) ? spec.colour : null;
  return {
    cover: COVERS.includes(spec.cover) ? spec.cover : null,
    flat: FLATS.includes(spec.flat) ? spec.flat : null,
    colour: hex ? srgb(hex) : null,
    family: FAMILIES.includes(spec.family) ? spec.family : null,
  };
}

/** The spec's solar modules in the scene: every quad of 4 finite corners, through `toScene(x, y, z)`. */
export function specPanels(spec, toScene = (x, y, z) => [x, y, z]) {
  if (!Array.isArray(spec?.panels)) return [];
  const ok = (q) => Array.isArray(q) && q.length === 4 && q.every((p) => Array.isArray(p) && p.length === 3 && p.every(Number.isFinite));
  return spec.panels.filter(ok).map((q) => q.map((p) => toScene(p[0], p[1], p[2])));
}

/**
 * The spec's roof equipment in the scene (#146): every piece of a known kind with a finite place and a positive size,
 * its middle through `toScene(x, y, z)` and its yaw turned with the frame (width along (cos yaw, 0, -sin yaw)).
 */
export function specProps(spec, toScene = (x, y, z) => [x, y, z]) {
  if (!Array.isArray(spec?.props)) return [];
  const finite = (v) => Array.isArray(v) && v.length === 3 && v.every(Number.isFinite);
  const ok = (p) => PROPS.includes(p?.kind) && finite(p.at) && finite(p.size) && p.size.every((s) => s > 0) && Number.isFinite(p.yaw);
  return spec.props.filter(ok).map((p) => {
    const at = toScene(p.at[0], p.at[1], p.at[2]);
    const to = toScene(p.at[0] + Math.cos(p.yaw), p.at[1], p.at[2] - Math.sin(p.yaw));
    return { kind: p.kind, at, size: [...p.size], yaw: Math.atan2(at[2] - to[2], to[0] - at[0]) };
  });
}

/** The backend's mix of a house's hash (geo/roofs.py `_mix`, murmur3's finaliser): its high bits move with the id's last letter. */
export function mixHash(h) {
  h ^= h >>> 16;
  h = Math.imul(h, 0x85ebca6b);
  h ^= h >>> 13;
  h = Math.imul(h, 0xc2b2ae35);
  h ^= h >>> 16;
  return h >>> 0;
}

/** A real roof colour of a family (sRGB 0–1), picked and shaded by the hash as roofTint does. */
function familyTint(family, hash) {
  const list = ROOF_TINTS[family], shade = 0.94 + ((hash >>> 4) % 13) / 100;
  return list[(hash >>> 13) % list.length].map((v) => Math.min(1, v * shade));
}

/** Which kind of flat roof a photographed level face (its median, sRGB 0–1) is: green, a dark membrane, else gravel. */
export function flatFamily([r, g, b]) {
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), light = (mx + mn) / 2;
  const sat = mx > mn ? (mx - mn) / (1 - Math.abs(2 * light - 1)) : 0;
  let hue = 0;
  if (mx > mn) {
    const d = mx - mn;
    hue = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
    hue = (hue * 60 + 360) % 360;
  }
  if (hue >= 55 && hue <= 160 && sat >= 0.12) return "green";
  return light < 0.3 ? "membrane" : "gravel";
}

/**
 * What a house's roof wears: { cover, flat, colour, flatColour (sRGB 0–1), family, gutter, source }. The spec's fields
 * where it has them; else the family of its photographed roof (`photographed.tiled`, a median, sRGB), or of a
 * neighbour's (`neighbourhood`, picked by the hash) when the photo does not show its own, a cover of that family drawn
 * by the hash (standing seam under LOW_PITCH degrees: `slope`), a real roof colour of it (roofTint); a level face the
 * kind its photographed one looks like (`photographed.flat`), else gravel mostly.
 */
export function roofChoice(house, { photographed = null, neighbourhood = [], slope = 30 } = {}) {
  const hash = houseHash(house.id), spec = validSpec(house.spec);
  const draw = (bits) => ((hash >>> bits) % 1000) / 1000;
  const seen = photographed?.tiled ?? (neighbourhood.length ? neighbourhood[(hash >>> 9) % neighbourhood.length] : null);
  const family = spec?.family ?? (seen ? roofFamily(seen) : FAMILIES[(hash >>> 17) % FAMILIES.length]);
  const colour = spec?.colour ?? (spec?.family ? familyTint(family, hash) : roofTint(seen, hash));
  let cover = spec?.cover ?? null;
  if (!cover) {
    if (slope < LOW_PITCH) cover = "metal-seam";
    else {
      let r = draw(5);
      cover = BY_FAMILY[family].find(([, share]) => (r -= share) < 0)?.[0] ?? BY_FAMILY[family][0][0];
    }
  }
  let flat = spec?.flat ?? null;
  if (!flat) {
    const r = draw(3);
    flat = photographed?.flat ? flatFamily(photographed.flat) : r < 0.72 ? "gravel" : r < 0.9 ? "green" : "membrane";
  }
  const tints = FLAT_TINTS[flat], shade = 0.95 + ((hash >>> 2) % 11) / 100;
  const flatColour = srgb(tints[(hash >>> 11) % tints.length]).map((v) => Math.min(1, v * shade));
  const g = draw(13);
  const gutter = house.source === "lidar" ? (g < 0.6 ? "anthracite" : "zinc") : g < 0.25 ? "copper" : "zinc";
  return { cover, flat, colour, flatColour, family, gutter, source: house.spec ? "spec" : "kit" };
}

/**
 * A roof's triangles (`tris`: indices into `pts`) grouped into faces by plane: { n (unit, up), d, level, slope
 * (degrees), tris: [[a, b, c]…] wound to face up }, a pitched face with its eave frame: T level along it (to the right
 * seen from outside), B up its slope, u0 and v0 its lowest values, so that uvOf gives metres from its eave.
 */
export function roofFaces(pts, tris) {
  const faces = [], drawn = new Set();
  const e1 = new THREE.Vector3(), e2 = new THREE.Vector3();
  for (let t = 0; t + 2 < tris.length; t += 3) {
    let [a, b, c] = [pts[tris[t]], pts[tris[t + 1]], pts[tris[t + 2]]];
    if (!a || !b || !c) continue;
    // swissBUILDINGS3D lists many roof triangles twice: a second copy would close every eave and draw the roof twice
    const key = [a, b, c].map(cornerKey).sort().join("|");
    if (drawn.has(key)) continue;
    drawn.add(key);
    e1.set(b[0] - a[0], b[1] - a[1], b[2] - a[2]);
    e2.set(c[0] - a[0], c[1] - a[1], c[2] - a[2]);
    const n = new THREE.Vector3().crossVectors(e1, e2);
    const area = n.length() / 2;
    if (area < 1e-4) continue;
    n.normalize();
    if (n.y < 0) { n.negate(); [b, c] = [c, b]; }
    // the triangle's middle measured along the group's own normal: each triangle's own normal 100 m from the origin
    // turns half a degree of the lidar's noise into a metre, and cut one roof plane into many faces
    const m = [0, 1, 2].map((k) => (a[k] + b[k] + c[k]) / 3);
    const off = (g) => Math.abs(g.n0.x * m[0] + g.n0.y * m[1] + g.n0.z * m[2] - g.d0);
    let f = faces.find((g) => g.n0.dot(n) > COPLANAR && off(g) < 0.15);
    if (!f) faces.push((f = { n0: n, d0: n.x * m[0] + n.y * m[1] + n.z * m[2], sum: new THREE.Vector3(), tris: [] }));
    f.sum.addScaledVector(n, area);
    f.tris.push([a, b, c]);
  }
  for (const f of faces) {
    f.n = f.sum.lengthSq() > 0 ? f.sum.clone().normalize() : f.n0.clone();
    let d = 0, k = 0;
    for (const tri of f.tris) for (const p of tri) { d += f.n.x * p[0] + f.n.y * p[1] + f.n.z * p[2]; k++; }
    f.d = d / k;
    f.level = f.n.y > FLAT_ROOF;
    f.slope = THREE.MathUtils.radToDeg(Math.acos(Math.min(1, f.n.y)));
    f.area = f.sum.length();
    if (f.level) continue;
    f.T = new THREE.Vector3(f.n.z, 0, -f.n.x).normalize();
    f.B = new THREE.Vector3().crossVectors(f.n, f.T);
    f.u0 = Infinity; f.v0 = Infinity;
    for (const tri of f.tris) for (const p of tri) {
      f.u0 = Math.min(f.u0, p[0] * f.T.x + p[2] * f.T.z);
      f.v0 = Math.min(f.v0, p[0] * f.B.x + p[1] * f.B.y + p[2] * f.B.z);
    }
  }
  return faces;
}

/** A point's texture coordinates on its face, in metres: level, x and z; pitched, along its eave and up from it. */
export function uvOf(f, p) {
  if (f.level) return [p[0], p[2]];
  return [p[0] * f.T.x + p[2] * f.T.z - f.u0, p[0] * f.B.x + p[1] * f.B.y + p[2] * f.B.z - f.v0];
}

/** The pitch of a roof (degrees, its pitched faces' area-weighted mean), null when it has none. */
export function roofPitch(faces) {
  let s = 0, a = 0;
  for (const f of faces) if (!f.level) { s += f.slope * f.area; a += f.area; }
  return a > 0 ? s / a : null;
}

/**
 * Where a roof's caps, gutters and parapets go. Ridges: edges two pitched faces share where each falls away from the
 * other (ridges and hips, not valleys); eaves: level edges of a pitched face that no other face holds, the face rising
 * from them; parapets: the runs parapetEdges finds along the edges of its level faces no other face holds.
 */
export function roofEdges(faces) {
  const edges = new Map();
  faces.forEach((f, fi) => {
    for (const tri of f.tris) for (let k = 0; k < 3; k++) {
      const a = tri[k], b = tri[(k + 1) % 3], c = tri[(k + 2) % 3];
      const ka = cornerKey(a), kb = cornerKey(b);
      if (ka === kb) continue;
      const key = ka < kb ? `${ka}|${kb}` : `${kb}|${ka}`;
      if (!edges.has(key)) edges.set(key, []);
      edges.get(key).push({ fi, a, b, c, ka, kb });
    }
  });
  const below = (f, p) => f.n.x * p[0] + f.n.y * p[1] + f.n.z * p[2] - f.d < -0.02;
  const ridges = [], low = [], open = [], lone = [];
  for (const list of edges.values()) {
    if (list.length >= 2) {
      const one = list[0], other = list.find((e) => e.fi !== one.fi);
      if (!other) continue; // inside a face
      const f1 = faces[one.fi], f2 = faces[other.fi];
      if (f1.level || f2.level || f1.n.dot(f2.n) > RIDGE_ANGLE) continue;
      if (below(f1, other.c) && below(f2, one.c)) ridges.push({ a: one.a, b: one.b, f1, f2, c1: one.c, c2: other.c });
      continue;
    }
    const e = list[0], f = faces[e.fi];
    if (f.level) {
      lone.push(e);
      continue;
    }
    const len = Math.hypot(e.b[0] - e.a[0], e.b[1] - e.a[1], e.b[2] - e.a[2]);
    const top = Math.max(e.a[1], e.b[1]);
    if (e.c[1] < top - 0.05) open.push(e); // the face falls away from it: half a ridge whose other half is cut elsewhere
    else if (Math.abs(e.a[1] - e.b[1]) <= Math.max(0.05, 0.02 * len) && e.c[1] > top + 0.1) low.push(e);
  }
  ridges.push(...splitRidges(open, faces, below));
  return { ridges, eaves: joinEaves(low, faces), parapets: lone.length ? parapetRuns(faces, lone) : [] };
}

/**
 * Where a house's flat roofs get a parapet: the edges of its level faces (minArea m² at least) that no other triangle
 * holds, where the roof falls away. Probed PROBE m out from an edge's middle, a roof triangle of the house there as
 * high as the edge or higher (within PROBE), or one whose plane goes on from the edge (a slope falling from it), means
 * none: the roof goes on, or a higher part of the house rises there. Joined into runs: { pts: [[x, y, z]…], closed },
 * the roof on the left walking pts seen from above (+y), as the faces' triangles wind; a corner in a straight line
 * dropped.
 */
export function parapetEdges(faces) {
  return roofEdges(faces).parapets;
}

function parapetRuns(faces, lone, minArea = PARAPET.minArea) {
  const tris = [];
  for (const f of faces) for (const [a, b, c] of f.tris) {
    tris.push({ a, b, c, f, x0: Math.min(a[0], b[0], c[0]), x1: Math.max(a[0], b[0], c[0]),
      z0: Math.min(a[2], b[2], c[2]), z1: Math.max(a[2], b[2], c[2]) });
  }
  const inside = (t, x, z) => {
    const s = (p, q) => (q[0] - p[0]) * (z - p[2]) - (q[2] - p[2]) * (x - p[0]);
    const s1 = s(t.a, t.b), s2 = s(t.b, t.c), s3 = s(t.c, t.a);
    return (s1 >= 0 && s2 >= 0 && s3 >= 0) || (s1 <= 0 && s2 <= 0 && s3 <= 0);
  };
  const heightAt = (f, x, z) => (f.d - f.n.x * x - f.n.z * z) / f.n.y;
  const falls = (a, b) => {
    const dx = b[0] - a[0], dz = b[2] - a[2], len = Math.hypot(dx, dz);
    const mx = (a[0] + b[0]) / 2, mz = (a[2] + b[2]) / 2, my = (a[1] + b[1]) / 2;
    // outward: the roof is on the left of a → b
    const px = mx - (dz / len) * PROBE, pz = mz + (dx / len) * PROBE;
    for (const t of tris) {
      if (px < t.x0 || px > t.x1 || pz < t.z0 || pz > t.z1 || !inside(t, px, pz)) continue;
      if (Math.max(heightAt(t.f, px, pz), heightAt(t.f, mx, mz)) >= my - PROBE) return false;
    }
    return true;
  };
  const open = lone.filter((e) => faces[e.fi].area >= minArea && Math.hypot(e.b[0] - e.a[0], e.b[2] - e.a[2]) >= 0.05 && falls(e.a, e.b));
  const from = new Map(), into = new Set(), used = new Set(), runs = [];
  for (const e of open) {
    if (!from.has(e.ka)) from.set(e.ka, []);
    from.get(e.ka).push(e);
    into.add(e.kb);
  }
  // q in a straight line from p to r, at their height: a corner the run does without
  const inLine = (p, q, r) => {
    const ux = q[0] - p[0], uz = q[2] - p[2], vx = r[0] - q[0], vz = r[2] - q[2];
    const cross = ux * vz - uz * vx, dot = ux * vx + uz * vz;
    return dot > 0 && Math.abs(cross) < 0.01 * Math.hypot(ux, uz) * Math.hypot(vx, vz) && Math.abs(q[1] - p[1]) < 0.02 && Math.abs(r[1] - q[1]) < 0.02;
  };
  const walk = (first) => {
    const pts = [first.a];
    for (let e = first; e && !used.has(e); e = from.get(e.kb)?.find((x) => !used.has(x))) {
      used.add(e);
      if (pts.length >= 2 && inLine(pts[pts.length - 2], pts[pts.length - 1], e.b)) pts[pts.length - 1] = e.b;
      else pts.push(e.b);
    }
    const closed = pts.length > 3 && cornerKey(pts[pts.length - 1]) === cornerKey(pts[0]);
    if (closed) pts.pop();
    if (closed && pts.length > 3 && inLine(pts[pts.length - 1], pts[0], pts[1])) pts.shift();
    runs.push({ pts, closed });
  };
  for (const e of open) if (!used.has(e) && !into.has(e.ka)) walk(e);
  for (const e of open) if (!used.has(e)) walk(e);
  return runs;
}

/**
 * A parapet along a run (parapetEdges), its corners mitred: the upstand's outer face (`outer`, on the edge, from `foot`
 * under it up to `height`), its inner face (`inner`, PARAPET.thick in) and the coping over both with its drips
 * (`coping`); triangles, 3 floats a corner. `height` 0 gives the coping alone (a stair housing's top).
 */
export function parapetTriangles(run, height, p = PARAPET) {
  const pts = run.pts.filter((q, i, all) => i === 0 || cornerKey(q) !== cornerKey(all[i - 1]));
  if (run.closed && pts.length > 1 && cornerKey(pts[0]) === cornerKey(pts[pts.length - 1])) pts.pop();
  const out = { outer: [], inner: [], coping: [] };
  const n = pts.length, segs = run.closed ? n : n - 1;
  if (segs < 1 || n < 2) return out;
  const normal = (i) => {
    const a = pts[i % n], b = pts[(i + 1) % n], dx = b[0] - a[0], dz = b[2] - a[2], len = Math.hypot(dx, dz) || 1;
    return [dz / len, -dx / len];
  };
  // each corner's offset per metre inward: the bisector of its two edges' normals, longer at a sharp corner (capped)
  const mitre = pts.map((_, i) => {
    const prev = i > 0 || run.closed ? normal((i - 1 + n) % n) : null, next = i < segs ? normal(i) : null;
    const [ax, az] = prev ?? next, [bx, bz] = next ?? prev;
    let mx = ax + bx, mz = az + bz;
    const len = Math.hypot(mx, mz);
    if (len < 1e-6) return [ax, az];
    mx /= len; mz /= len;
    const k = 1 / Math.max(0.35, mx * bx + mz * bz);
    return [mx * k, mz * k];
  });
  const at = (i, off, y) => [pts[i][0] + mitre[i][0] * off, pts[i][1] + y, pts[i][2] + mitre[i][1] * off];
  // a vertical band between two offsets' heights, facing out (or in)
  const band = (list, i, j, off, y0, y1, facingIn) => {
    const a0 = at(i, off, y0), b0 = at(j, off, y0), b1 = at(j, off, y1), a1 = at(i, off, y1);
    if (facingIn) list.push(...a0, ...b1, ...b0, ...a0, ...a1, ...b1);
    else list.push(...a0, ...b0, ...b1, ...a0, ...b1, ...a1);
  };
  const top = height + p.cap;
  for (let s = 0; s < segs; s++) {
    const i = s, j = (s + 1) % n;
    if (height > 0) {
      band(out.outer, i, j, 0, -p.foot, height, false);
      band(out.inner, i, j, p.thick, -p.foot, height, true);
    }
    const o0 = at(i, -p.over, top), o1 = at(j, -p.over, top), i0 = at(i, p.thick + p.over, top), i1 = at(j, p.thick + p.over, top);
    out.coping.push(...o0, ...o1, ...i0, ...o1, ...i1, ...i0);
    band(out.coping, i, j, -p.over, top - p.drip, top, false);
    band(out.coping, i, j, p.thick + p.over, top - p.dripIn, top, true);
  }
  return out;
}

/** A face's low edges in a line joined into its eaves (a traced roof cuts one in pieces), MIN_EAVE long at least. */
function joinEaves(low, faces) {
  const lines = new Map();
  for (const e of low) {
    const f = faces[e.fi];
    const [ua] = uvOf(f, e.a), [ub, vb] = uvOf(f, e.b);
    const key = `${e.fi}:${Math.round(vb * 20)}`;
    if (!lines.has(key)) lines.set(key, []);
    lines.get(key).push(ua < ub ? [ua, ub, e.a, e.b] : [ub, ua, e.b, e.a]);
  }
  const eaves = [];
  for (const [key, list] of lines) {
    const f = faces[Number(key.split(":")[0])];
    list.sort((p, q) => p[0] - q[0]);
    let run = list[0];
    const close = () => { if (run[1] - run[0] >= MIN_EAVE) eaves.push({ a: run[2], b: run[3], f }); };
    for (const s of list.slice(1)) {
      if (s[0] <= run[1] + 0.05) { if (s[1] > run[1]) run = [run[0], s[1], run[2], s[3]]; }
      else { close(); run = s; }
    }
    close();
  }
  return eaves;
}

/**
 * Ridges whose two faces were cut at different corners (one face's edge in two pieces along the other's): the open
 * edges of two faces on the same line, over the stretch they share.
 */
function splitRidges(open, faces, below) {
  const out = [];
  for (let i = 0; i < open.length; i++) for (let j = i + 1; j < open.length; j++) {
    const p = open[i], q = open[j];
    if (p.fi === q.fi) continue;
    const f1 = faces[p.fi], f2 = faces[q.fi];
    if (f1.level || f2.level || f1.n.dot(f2.n) > RIDGE_ANGLE) continue;
    const A = v3(p.a), dir = v3(p.b).sub(A), len = dir.length();
    if (len < 0.3) continue;
    dir.divideScalar(len);
    const off = (x) => { const w = v3(x).sub(A); return { t: w.dot(dir), d: w.addScaledVector(dir, -w.dot(dir)).length() }; };
    const qa = off(q.a), qb = off(q.b);
    if (qa.d > 0.03 || qb.d > 0.03) continue;
    const t0 = Math.max(0, Math.min(qa.t, qb.t)), t1 = Math.min(len, Math.max(qa.t, qb.t));
    if (t1 - t0 < 0.3 || !below(f1, q.c) || !below(f2, p.c)) continue;
    const at = (t) => A.clone().addScaledVector(dir, t).toArray();
    out.push({ a: at(t0), b: at(t1), f1, f2, c1: p.c, c2: q.c });
  }
  return out;
}

/** The ridge cap over one ridge or hip: a low rounded tile along it, its edges on both faces (triangles, 3 floats a corner). */
export function capTriangles(r, { width = CAP.width, height = CAP.height, lift = CAP.lift } = {}) {
  const a = v3(r.a), b = v3(r.b), e = b.clone().sub(a).normalize();
  const side = (f, c) => {
    const s = new THREE.Vector3().crossVectors(f.n, e).normalize();
    return s.dot(v3(c).sub(a)) < 0 ? s.negate() : s;
  };
  const s1 = side(r.f1, r.c1), s2 = side(r.f2, r.c2), up = r.f1.n.clone().add(r.f2.n).normalize();
  const profile = [
    s1.clone().multiplyScalar(width).addScaledVector(r.f1.n, lift),
    s1.clone().multiplyScalar(width * 0.5).addScaledVector(r.f1.n, lift + height * 0.6),
    up.clone().multiplyScalar(lift + height),
    s2.clone().multiplyScalar(width * 0.5).addScaledVector(r.f2.n, lift + height * 0.6),
    s2.clone().multiplyScalar(width).addScaledVector(r.f2.n, lift),
  ];
  return sweep(profile, a, b, false);
}

/** The gutter under one eave: a half-round hung just out of it (triangles, 3 floats a corner). */
export function gutterTriangles(eave) {
  const out = new THREE.Vector3(eave.f.n.x, 0, eave.f.n.z).normalize();
  const profile = GUTTER.map(([o, u]) => out.clone().multiplyScalar(o).add(new THREE.Vector3(0, u, 0)));
  return sweep(profile, v3(eave.a), v3(eave.b), true);
}

/** A profile (offsets from the line) swept from a to b: its quads as triangles; `closed` joins its last point to its first. */
function sweep(profile, a, b, closed) {
  const tris = [];
  const n = profile.length, last = closed ? n : n - 1;
  for (let k = 0; k < last; k++) {
    const p = profile[k], q = profile[(k + 1) % n];
    const pa = a.clone().add(p), qa = a.clone().add(q), pb = b.clone().add(p), qb = b.clone().add(q);
    tris.push(...pa.toArray(), ...pb.toArray(), ...qb.toArray(), ...pa.toArray(), ...qb.toArray(), ...qa.toArray());
  }
  return tris;
}

/**
 * A solar module's instance matrix from its 4 corners (the top of its glass): a unit box turned into its plane, as
 * wide and long as the quad, MODULE.thick deep under it; the box's +y is the module's face, toward the sky.
 */
export function moduleMatrix(quad, m = new THREE.Matrix4()) {
  const [c0, c1, c2, c3] = quad.map(v3);
  const centre = c0.clone().add(c1).add(c2).add(c3).multiplyScalar(0.25);
  let ex = c1.clone().sub(c0).add(c2.clone().sub(c3)).multiplyScalar(0.5);
  let ez = c3.clone().sub(c0).add(c2.clone().sub(c1)).multiplyScalar(0.5);
  if (new THREE.Vector3().crossVectors(ez, ex).y < 0) [ex, ez] = [ez, ex];
  const X = ex.clone().normalize(), Y = new THREE.Vector3().crossVectors(ez, ex).normalize();
  const Z = new THREE.Vector3().crossVectors(X, Y);
  const w = ex.length(), l = Math.abs(ez.dot(Z));
  centre.addScaledVector(Y, -MODULE.thick / 2);
  return m.makeBasis(X.multiplyScalar(w), Y.multiplyScalar(MODULE.thick), Z.multiplyScalar(l)).setPosition(centre);
}

// ---- materials

let _moduleMaterial = null;

/**
 * The modules' one material: on the box's top, dark glass that reflects the sky inside a thin aluminium frame, a
 * faint grid of cells (MODULE.cell) that fades out rather than flickers once a cell is under a pixel; the sides frame.
 */
function moduleMaterial() {
  if (_moduleMaterial) return _moduleMaterial;
  const m = new THREE.MeshStandardMaterial({ color: "#ffffff", roughness: 0.12, metalness: 0.15 });
  const glass = new THREE.Color("#0f1722"), line = new THREE.Color("#4a5662"), frame = new THREE.Color("#b9bdc1");
  m.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, { uGlass: { value: glass }, uLine: { value: line }, uFrame: { value: frame } });
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nvarying vec3 vModule;\nvarying vec2 vModuleSize;")
      .replace("#include <begin_vertex>", `#include <begin_vertex>
        vModule = vec3(position.x + 0.5, normal.y, position.z + 0.5);
        #ifdef USE_INSTANCING
        vModuleSize = vec2(length(instanceMatrix[0].xyz), length(instanceMatrix[2].xyz));
        #else
        vModuleSize = vec2(1.0);
        #endif`);
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", `#include <common>
        varying vec3 vModule;
        varying vec2 vModuleSize;
        uniform vec3 uGlass, uLine, uFrame;
        float moduleFrame;`)
      .replace("#include <map_fragment>", `#include <map_fragment>
        vec2 mm = vModule.xz * vModuleSize;
        vec2 toEdge = min(mm, vModuleSize - mm);
        moduleFrame = vModule.y > 0.5 ? 1.0 - smoothstep(${MODULE.frame.toFixed(3)} - 0.004, ${MODULE.frame.toFixed(3)}, min(toEdge.x, toEdge.y)) : 1.0;
        vec2 inner = max(vModuleSize - ${(2 * MODULE.frame).toFixed(3)}, vec2(0.1));
        vec2 cells = max(floor(inner / ${MODULE.cell.toFixed(3)} + 0.5), vec2(1.0));
        vec2 g = (mm - ${MODULE.frame.toFixed(3)}) / inner * cells;
        vec2 fw = max(fwidth(g), vec2(1e-5));
        vec2 lw = ${MODULE.line.toFixed(3)} * cells / inner;
        vec2 dist = abs(fract(g + 0.5) - 0.5);
        vec2 onLine = (1.0 - smoothstep(lw * 0.5, lw * 0.5 + fw, dist)) * clamp(lw / fw, 0.0, 1.0);
        float grid = max(onLine.x, onLine.y);
        diffuseColor.rgb = mix(mix(uGlass, uLine, 0.45 * grid), uFrame, moduleFrame);`)
      .replace("#include <roughnessmap_fragment>", "#include <roughnessmap_fragment>\nroughnessFactor = mix(0.12, 0.42, moduleFrame);")
      .replace("#include <metalnessmap_fragment>", "#include <metalnessmap_fragment>\nmetalnessFactor = mix(0.15, 0.7, moduleFrame);");
  };
  m.customProgramCacheKey = () => "housekit-roofs-modules";
  _moduleMaterial = m;
  return m;
}

/**
 * A texture drawn on a canvas, `metres` [u, v] a repeat, and the factor that makes its mean white (linear): standing
 * seam (two 0.5 m pans a metre, the seams up the slope) or a membrane (1 m rolls lapped across, faint mottling).
 */
function drawnTextures(kind) {
  if (typeof document === "undefined") return null;
  const [W, H, metres] = kind === "seam" ? [512, 64, [1, 0.125]] : [256, 256, [2, 2]];
  const canvas = () => Object.assign(document.createElement("canvas"), { width: W, height: H });
  const colour = canvas(), normal = canvas();
  const c = colour.getContext("2d", { willReadFrequently: true }), n = normal.getContext("2d");
  n.fillStyle = "rgb(128,128,255)";
  n.fillRect(0, 0, W, H);
  if (kind === "seam") {
    // two pans a little apart in sheen, each seam a lit top between a lit and a shaded flank
    [[0, 238], [256, 229]].forEach(([x, v]) => { c.fillStyle = `rgb(${v},${v},${v})`; c.fillRect(x, 0, 256, H); });
    for (const x of [0, 256]) {
      c.fillStyle = "rgb(205,205,205)"; c.fillRect(x - 7, 0, 4, H);
      c.fillStyle = "rgb(252,252,252)"; c.fillRect(x - 3, 0, 6, H);
      c.fillStyle = "rgb(188,188,188)"; c.fillRect(x + 3, 0, 4, H);
      n.fillStyle = "rgb(40,128,225)"; n.fillRect(x - 7, 0, 4, H);
      n.fillStyle = "rgb(216,128,225)"; n.fillRect(x + 3, 0, 4, H);
    }
    c.fillStyle = "rgb(205,205,205)"; c.fillRect(W - 7, 0, 4, H);
    n.fillStyle = "rgb(40,128,225)"; n.fillRect(W - 7, 0, 4, H);
  } else {
    c.fillStyle = "rgb(232,232,232)";
    c.fillRect(0, 0, W, H);
    let s = 7;
    const rnd = () => ((s = (s * 16807) % 2147483647) / 2147483647);
    for (let i = 0; i < 90; i++) {
      c.fillStyle = `rgba(${rnd() < 0.5 ? "0,0,0" : "255,255,255"},0.035)`;
      c.beginPath();
      c.ellipse(rnd() * W, rnd() * H, 10 + rnd() * 40, 6 + rnd() * 24, rnd() * Math.PI, 0, Math.PI * 2);
      c.fill();
    }
    // a lap every metre: the upper sheet's edge lit, its shadow under it
    for (const y of [0, H / 2]) {
      c.fillStyle = "rgb(196,196,196)"; c.fillRect(0, y, W, 2);
      c.fillStyle = "rgb(246,246,246)"; c.fillRect(0, y + 2, W, 1);
      n.fillStyle = "rgb(128,60,225)"; n.fillRect(0, y, W, 2);
    }
  }
  const px = c.getImageData(0, 0, W, H).data;
  let sum = 0;
  for (let i = 0; i < px.length; i += 4) sum += new THREE.Color().setRGB(px[i] / 255, px[i] / 255, px[i] / 255, THREE.SRGBColorSpace).r;
  const tex = (cv, srgbSpace) => {
    const t = new THREE.CanvasTexture(cv);
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    t.repeat.set(1 / metres[0], 1 / metres[1]);
    t.anisotropy = 8;
    t.colorSpace = srgbSpace ? THREE.SRGBColorSpace : THREE.NoColorSpace;
    return t;
  };
  return { map: tex(colour, true), normalMap: tex(normal, false), white: 1 / (sum / (W * H)) };
}

/**
 * The simple pieces a piece of roof equipment is drawn with (#146): { shape: box | cylinder | dome, surface (SURFACES),
 * at: the middle of its foot in the scene, size: [along the width, up, along the depth], yaw }, its foot SINK into the
 * roof. A housing's walls, top and coping are the parapets' (planRoofs); here only its door.
 */
export function propPieces({ kind, at, size: [w, d, h], yaw }, shade = () => 0) {
  const c = Math.cos(yaw), s = Math.sin(yaw), out = [];
  const put = (shape, surface, x, y0, z, sx, sy, sz, tone = 0) => out.push({
    shape, surface, tone, yaw,
    at: [at[0] + x * c + z * s, at[1] + y0, at[2] - x * s + z * c],
    size: [sx, sy, sz],
  });
  if (kind === "vent" && w >= 0.25) {
    // a mushroom vent: a low cap wider than its pipe
    put("cylinder", "galvanised", 0, -SINK, 0, w, h - 0.12 + SINK, w);
    put("dome", "galvanised", 0, h - 0.14, 0, 1.3 * w, 0.14, 1.3 * w);
  } else if (kind === "vent") {
    put("cylinder", "galvanised", 0, -SINK, 0, w, h - 0.05 + SINK, w);
    put("cylinder", "galvanised", 0, h - 0.05, 0, 1.5 * w, 0.05, 1.5 * w);
  } else if (kind === "stack") {
    put("cylinder", "steel", 0, -SINK, 0, w, h - 0.06 + SINK, w);
    put("cylinder", "steel", 0, h - 0.06, 0, 1.15 * w, 0.06, 1.15 * w);
    put("cylinder", "dark", 0, h - 0.01, 0, 0.9 * w, 0.013, 0.9 * w);
  } else if (kind === "skylight-dome") {
    put("box", "grey", 0, -SINK, 0, w, 0.25 + SINK, d);
    put("dome", "dome", 0, 0.25, 0, w - 0.06, h - 0.25, d - 0.06);
  } else if (kind === "skylight-flat") {
    put("box", "grey", 0, -SINK, 0, w, h - 0.04 + SINK, d);
    put("box", "glass", 0, h - 0.04, 0, w - 0.08, 0.04, d - 0.08);
  } else if (kind === "hatch") {
    put("box", "grey", 0, -SINK, 0, w, h - 0.06 + SINK, d);
    put("box", "grey", 0, h - 0.06, 0, w + 0.06, 0.06, d + 0.06);
  } else if (kind === "hvac") {
    for (const z of [-0.35 * d, 0.35 * d]) put("box", "dark", 0, -SINK, z, w, 0.12 + SINK, 0.1);
    put("box", "unit", 0, 0.12, 0, w, h - 0.12, d);
    const fans = w > 2 * d ? [-w / 4, w / 4] : [0], fan = 0.7 * Math.min(d, w / 2);
    for (const x of fans) put("cylinder", "dark", x, h - 0.01, 0, fan, 0.013, fan);
  } else if (kind === "rail") {
    const posts = Math.ceil(w / 1.5) + 1;
    for (let i = 0; i < posts; i++) put("box", "galvanised", -w / 2 + 0.02 + (i * (w - 0.04)) / (posts - 1), -SINK, 0, 0.04, h + SINK, 0.04);
    put("box", "galvanised", 0, h - 0.04, 0, w, 0.04, 0.04);
    put("box", "galvanised", 0, h / 2 - 0.02, 0, w, 0.04, 0.04);
  } else if (kind === "walkway") {
    const nx = Math.max(1, Math.round(w / SLAB)), nz = Math.max(1, Math.round(d / SLAB));
    for (let i = 0; i < nx; i++) for (let j = 0; j < nz; j++) {
      const x = -w / 2 + ((i + 0.5) * w) / nx, z = -d / 2 + ((j + 0.5) * d) / nz;
      put("box", "slab", x, -SINK, z, w / nx - 0.01, h + SINK, d / nz - 0.01, shade(i * 31 + j));
    }
  } else if (kind === "housing") {
    put("box", "door", 0, 0.1, d / 2, 0.9, 2.05, 0.04);
  }
  return out;
}

const SHAPES = {
  box: () => new THREE.BoxGeometry(1, 1, 1).translate(0, 0.5, 0),
  cylinder: () => new THREE.CylinderGeometry(0.5, 0.5, 1, 14, 1).translate(0, 0.5, 0),
  dome: () => new THREE.SphereGeometry(0.5, 18, 6, 0, Math.PI * 2, 0, Math.PI / 2).scale(1, 2, 1),
};

/**
 * The corners of a housing's foot (`at` its middle, `size` [w, d, h]), its inside on the left walking them seen from
 * above: its walls and coping are a parapet as high as it is along them.
 */
export function housingRun({ at, size: [w, d], yaw }) {
  const c = Math.cos(yaw), s = Math.sin(yaw);
  const corner = (a, b) => [at[0] + (a * w * c + b * d * s) / 2, at[1], at[2] + (-a * w * s + b * d * c) / 2];
  const pts = [corner(-1, -1), corner(-1, 1), corner(1, 1), corner(1, -1)];
  const area = pts.reduce((sum, p, i) => { const q = pts[(i + 1) % 4]; return sum + p[0] * q[2] - q[0] * p[2]; }, 0);
  return { pts: area > 0 ? pts.reverse() : pts, closed: true };
}

/** Gravel's darker patches (PATCHES): value noise over the roof's x and z, in metres. */
function withPatches(m) {
  const [tr, tg, tb] = PATCHES.tint, [s1, s2] = PATCHES.scales, f = (v) => v.toFixed(3);
  m.onBeforeCompile = (shader) => {
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nvarying vec2 vPatch;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvPatch = position.xz;");
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", `#include <common>
        varying vec2 vPatch;
        float patchHash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
        float patchNoise(vec2 p) {
          vec2 i = floor(p), f = fract(p);
          f = f * f * (3.0 - 2.0 * f);
          return mix(mix(patchHash(i), patchHash(i + vec2(1.0, 0.0)), f.x), mix(patchHash(i + vec2(0.0, 1.0)), patchHash(i + vec2(1.0, 1.0)), f.x), f.y);
        }`)
      .replace("#include <map_fragment>", `#include <map_fragment>
        float patchN = 0.65 * patchNoise(vPatch / ${f(s1)}) + 0.35 * patchNoise(vPatch / ${f(s2)} + 17.0);
        diffuseColor.rgb *= mix(vec3(1.0), vec3(${f(tr)}, ${f(tg)}, ${f(tb)}), smoothstep(${f(PATCHES.from)}, ${f(PATCHES.to)}, patchN));`);
  };
  m.customProgramCacheKey = () => "housekit-roofs-gravel";
  return m;
}

/** The finish's material asked for now (its textures start loading), the vertex-coloured copy made once they are in. */
function lookOf(kind) {
  const look = LOOKS[kind];
  const shared = look.finish ? finishMaterial(look.finish, { color: "#ffffff", roughness: look.roughness }) : null;
  return () => {
    let m;
    if (shared) m = shared.clone();
    else {
      m = new THREE.MeshStandardMaterial({ roughness: look.roughness, metalness: look.metalness ?? 0 });
      const drawn = look.drawn ? drawnTextures(look.drawn) : null;
      if (drawn) Object.assign(m, { map: drawn.map, normalMap: drawn.normalMap, color: new THREE.Color(drawn.white, drawn.white, drawn.white) });
    }
    m.vertexColors = true;
    m.userData = { ...m.userData, roof: kind };
    return m;
  };
}

/**
 * Every house's roof planned: its choice, its faces' triangles by finish (positions, normals, metric uvs, colours), its
 * caps and gutters, its modules, its flat roofs' parapets and equipment (`props`, specProps); the finishes asked for
 * (their textures load while the rest of the surroundings is built). `photographed`: Map(house → { tiled, flat }) of
 * photographed medians; `neighbourhood`: photographed tiled medians to pick from for a house the photo does not show;
 * `roofOf(house)`: the triangles drawn as its roof (by default `roof`). `meshes({ wallMaterial })`, after
 * finishesReady, makes the group; the parapets' outer faces and the housings' walls wear `wallMaterial` (the walls',
 * with an attribute wallLook: the house's render, the height above its foot), else their render as vertex colour.
 */
export function planRoofs(houses, { photographed = new Map(), neighbourhood = [], roofOf = (h) => h.roof } = {}) {
  const lists = new Map(); // kind → { pos, nor, uv, col }
  const details = { pos: [], col: [] };
  const walls = { pos: [], look: [] }; // the parapets' outer faces and the housings' walls: the house's render
  const panels = [];
  const parts = new Map(); // shape:finish → [piece…]
  const stats = { houses: 0, spec: 0, kit: 0, covers: {}, flats: {}, panels: 0, ridges: 0, gutters: 0, parapets: 0, props: 0 };
  const flashing = new THREE.Color(FLASHING);
  const addDetail = (tris, c) => {
    for (let k = 0; k < tris.length; k += 3) {
      details.pos.push(tris[k], tris[k + 1], tris[k + 2]);
      details.col.push(c.r, c.g, c.b);
    }
  };
  // the wall shader's height above the ground darkens a foot: a housing's on the roof, never a parapet's
  const addWall = (tris, c, foot = -Infinity) => {
    for (let k = 0; k < tris.length; k += 3) {
      walls.pos.push(tris[k], tris[k + 1], tris[k + 2]);
      walls.look.push(c.r, c.g, c.b, Math.min(10, tris[k + 1] - foot));
    }
  };
  const listOf = (kind) => {
    if (!lists.has(kind)) lists.set(kind, { pos: [], nor: [], uv: [], col: [] });
    return lists.get(kind);
  };
  const choices = new Map();
  for (const h of houses) {
    const faces = roofFaces(h.pts, roofOf(h) ?? []);
    if (h.panels?.length) panels.push(...h.panels);
    if (!faces.length) continue;
    const choice = roofChoice(h, { photographed: photographed.get(h) ?? null, neighbourhood, slope: roofPitch(faces) ?? 30 });
    choices.set(h, choice);
    stats.houses++;
    stats[choice.source]++;
    const tiled = weathered(linear(choice.colour)), level = linear(choice.flatColour);
    let pitched = false, flat = false;
    for (const f of faces) {
      const kind = f.level ? choice.flat : choice.cover, c = f.level ? level : tiled;
      if (f.level) flat = true; else pitched = true;
      const L = listOf(kind);
      for (const tri of f.tris) for (const p of tri) {
        L.pos.push(p[0], p[1], p[2]);
        L.nor.push(f.n.x, f.n.y, f.n.z);
        L.uv.push(...uvOf(f, p));
        L.col.push(c.r, c.g, c.b);
      }
    }
    if (pitched) stats.covers[choice.cover] = (stats.covers[choice.cover] ?? 0) + 1;
    if (flat) stats.flats[choice.flat] = (stats.flats[choice.flat] ?? 0) + 1;
    const { ridges, eaves, parapets } = roofEdges(faces);
    const cap = tiled.clone().multiplyScalar(choice.cover === "metal-seam" ? 1 : 0.9);
    const capSize = choice.cover === "metal-seam" ? { width: 0.08, height: 0.04 } : {};
    for (const r of ridges) {
      addDetail(capTriangles(r, capSize), cap);
      stats.ridges += Math.hypot(r.b[0] - r.a[0], r.b[1] - r.a[1], r.b[2] - r.a[2]);
    }
    const gutter = new THREE.Color(GUTTERS[choice.gutter]);
    for (const e of eaves) {
      addDetail(gutterTriangles(e), gutter);
      stats.gutters += Math.hypot(e.b[0] - e.a[0], e.b[2] - e.a[2]);
    }
    const m = mixHash(houseHash(h.id)), render = h.render ?? new THREE.Color(1, 1, 1);
    const levelArea = faces.reduce((s, f) => s + (f.level ? f.area : 0), 0);
    const height = levelArea < PARAPET.small ? PARAPET.low : 0.25 + (m % 21) / 100;
    const coping = new THREE.Color(COPINGS[(m >>> 8) % COPINGS.length]);
    for (const run of parapets) {
      const t = parapetTriangles(run, height);
      addWall(t.outer, render);
      addDetail(t.inner, flashing);
      addDetail(t.coping, coping);
      const n = run.pts.length;
      for (let i = 0; i < (run.closed ? n : n - 1); i++) {
        const a = run.pts[i], b = run.pts[(i + 1) % n];
        stats.parapets += Math.hypot(b[0] - a[0], b[2] - a[2]);
      }
    }
    (h.props ?? []).forEach((p, k) => {
      const shade = (i) => ((mixHash(m + k * 977 + i) % 11) - 5) / 100;
      for (const piece of propPieces(p, shade)) {
        const key = `${piece.shape}:${SURFACES[piece.surface][1]}`;
        if (!parts.has(key)) parts.set(key, []);
        parts.get(key).push(piece);
      }
      if (p.kind === "housing") {
        const run = housingRun(p), t = parapetTriangles(run, p.size[2]);
        addWall(t.outer, render, p.at[1]);
        addDetail(t.coping, coping);
        const L = listOf(choice.flat), top = run.pts.map((q) => [q[0], q[1] + p.size[2], q[2]]);
        for (const q of [top[0], top[1], top[2], top[0], top[2], top[3]]) {
          L.pos.push(...q);
          L.nor.push(0, 1, 0);
          L.uv.push(q[0], q[2]);
          L.col.push(level.r, level.g, level.b);
        }
      }
      stats.props++;
    });
  }
  stats.panels = panels.length;
  stats.ridges = Math.round(stats.ridges);
  stats.gutters = Math.round(stats.gutters);
  stats.parapets = Math.round(stats.parapets);
  // asked for now: the textures load while the caller builds the rest
  const looks = new Map([...lists.keys()].map((kind) => [kind, lookOf(kind)]));
  return {
    choices,
    stats,
    meshes({ wallMaterial = null } = {}) {
      const group = new THREE.Group();
      group.name = "Surroundings: drawn roofs";
      for (const [kind, L] of lists) {
        const g = new THREE.BufferGeometry();
        g.setAttribute("position", new THREE.Float32BufferAttribute(L.pos, 3));
        g.setAttribute("normal", new THREE.Float32BufferAttribute(L.nor, 3));
        g.setAttribute("uv", new THREE.Float32BufferAttribute(L.uv, 2));
        g.setAttribute("color", new THREE.Float32BufferAttribute(L.col, 3));
        const mesh = new THREE.Mesh(g, kind === "gravel" ? withPatches(looks.get(kind)()) : looks.get(kind)());
        mesh.castShadow = mesh.receiveShadow = true;
        mesh.name = `Surroundings: ${kind} roofs`;
        mesh.userData = { kind: "context", roof: kind };
        group.add(mesh);
      }
      if (details.pos.length) {
        const g = new THREE.BufferGeometry();
        g.setAttribute("position", new THREE.Float32BufferAttribute(details.pos, 3));
        g.setAttribute("color", new THREE.Float32BufferAttribute(details.col, 3));
        g.computeVertexNormals();
        // a cap lies a few centimetres over its faces: drawn a little nearer, so that it holds far off
        const mat = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.65, metalness: 0.1, side: THREE.DoubleSide,
          polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -2 });
        const mesh = new THREE.Mesh(g, mat);
        mesh.receiveShadow = true;
        mesh.name = "Surroundings: ridge caps, gutters and copings";
        mesh.userData = { kind: "context", part: "roof details" };
        group.add(mesh);
      }
      if (walls.pos.length) {
        const g = new THREE.BufferGeometry();
        g.setAttribute("position", new THREE.Float32BufferAttribute(walls.pos, 3));
        g.computeVertexNormals();
        metricUV(g);
        const look = new THREE.Float32BufferAttribute(walls.look, 4);
        g.setAttribute("wallLook", look);
        g.setAttribute("color", new THREE.Float32BufferAttribute(walls.look.filter((_, i) => i % 4 !== 3), 3));
        // the walls' own material (context.js: their render, each house's colour from wallLook)
        const mesh = new THREE.Mesh(g, wallMaterial ?? new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.95 }));
        mesh.castShadow = mesh.receiveShadow = true;
        mesh.name = "Surroundings: parapets and roof housings";
        mesh.userData = { kind: "context", part: "parapets" };
        group.add(mesh);
      }
      if (panels.length) {
        const mesh = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), moduleMaterial(), panels.length);
        const m = new THREE.Matrix4();
        panels.forEach((q, i) => mesh.setMatrixAt(i, moduleMatrix(q, m)));
        mesh.instanceMatrix.needsUpdate = true;
        mesh.computeBoundingSphere();
        mesh.castShadow = mesh.receiveShadow = true;
        mesh.name = "Surroundings: solar modules";
        mesh.userData = { kind: "context", part: "solar modules" };
        group.add(mesh);
      }
      // the equipment: an instanced mesh a shape and finish, each piece its surface's colour
      const finishes = new Map();
      const m = new THREE.Matrix4(), q = new THREE.Quaternion(), up = new THREE.Vector3(0, 1, 0), c = new THREE.Color();
      for (const [key, list] of parts) {
        const [shape, finish] = key.split(":");
        if (!finishes.has(finish)) {
          const [roughness, metalness] = FINISHES[finish];
          finishes.set(finish, new THREE.MeshStandardMaterial({ color: "#ffffff", roughness, metalness }));
        }
        const mesh = new THREE.InstancedMesh(SHAPES[shape](), finishes.get(finish), list.length);
        list.forEach((p, i) => {
          q.setFromAxisAngle(up, p.yaw);
          mesh.setMatrixAt(i, m.compose(v3(p.at), q, v3(p.size)));
          mesh.setColorAt(i, c.set(SURFACES[p.surface][0]).multiplyScalar(1 + p.tone));
        });
        mesh.instanceMatrix.needsUpdate = true;
        mesh.instanceColor.needsUpdate = true;
        mesh.computeBoundingSphere();
        mesh.castShadow = mesh.receiveShadow = true;
        mesh.name = `Surroundings: roof equipment (${shape}, ${finish})`;
        mesh.userData = { kind: "context", part: "roof equipment" };
        group.add(mesh);
      }
      return group;
    },
  };
}

export default {
  COVERS, FLATS, LOW_PITCH, PROPS, validSpec, specPanels, specProps, mixHash, flatFamily, roofChoice, roofFaces, uvOf, roofPitch,
  roofEdges, parapetEdges, parapetTriangles, propPieces, housingRun, capTriangles, gutterTriangles, moduleMatrix, planRoofs,
};
