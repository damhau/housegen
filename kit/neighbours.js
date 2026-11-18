// housekit/neighbours — the doors and windows of the neighbouring houses in the surroundings (#124), and how the
// roofs of houses newer than the aerial photo look (#123: tiles down to 5°, in real roof colours). Generic but in proportion to each house: the count follows a
// façade's length and its storeys (about one window per PER_WINDOW metres and storey at most), spaced irregularly
// by the house's id, stacked in columns; a gable gets one attic window when it is tall enough; a small low volume
// gets a garage door or one small window; a house one front door, on the façade facing the nearest road. Each
// opening is real geometry (instanced): a reveal, a frame, glass that reflects the sky, a sill, and shutters or
// exterior blinds on some houses.
//
//   const blockers = new Blockers(houses)                  every house's triangles, for what hides a façade
//   const plan = planOpenings(house, { groundAt, road, blockers })   → { openings, rows, small }
//   const group = openingMeshes(openings)                  a few InstancedMeshes
//   roofLook(pixels)                                       "photo" when the photo shows a finished roof there
//   roofTint(photographed, hash)                           a tiled roof's colour: a real one, of a photographed roof's kind
//
// A house: { id, source, pts: [[x, y, z]…] in the scene, wall: [i…], roof: [i…], render: THREE.Color }.
// Façade frame: n the wall's outward normal (the triangles wind counter-clockwise seen from outside), t along it
// to the right seen from outside, y up.

import * as THREE from "three";

// renders seen on Swiss houses: cream, beige, off-white, light grey, pale yellow, sand
export const RENDERS = ["#efe7d6", "#e7dcc6", "#f2efe8", "#dddcd6", "#efe4c2", "#e3d6bd"].map((c) => new THREE.Color(c));

export const PER_WINDOW = 4; // metres of façade per window and storey, at most
const MIN_FACADE = 2.5; // a façade shorter than this gets no opening
const SMALL_AREA = 40, SMALL_HEIGHT = 5; // m², m: a garage, a shed, an annex (one storey under its roof)
const TINY_AREA = 8, TINY_HEIGHT = 2.2;
const LOW_HEIGHT = 2.6; // m: lower than this, whatever its size, a volume has no storey of windows (a garage, a shelter)
const GARAGE_ROAD = 14; // metres from a road within which a small volume's façade gets a garage door
const ROAD_SEARCH = 35; // metres a façade looks out for a road
const LOW_WINDOW = 1.0; // m: a window's height under eaves too low for a whole one
const STEP = 0.3; // sampling of a façade's wall (metres)
const FLAT = 0.04; // metres a wall may move in or out under one opening
const JAG = 0.5; // the same on the lidar's stepped walls
const INSIDE = 25; // how far a probe looks for the wall it would leave a building by
const SILL_OVER_ROOF = 0.9; // metres of wall a window keeps over a lower roof
// metres from the site past which a house's openings lose their depth (reveal, sill: a pixel or two there),
// as the trees past MID_TREES lose leaves: most openings stand that far, and every one is drawn in each frame
export const FLAT_BEYOND = 100;

// shutters: Vaud's painted and wooden ones; blinds: aluminium, dark grey, sand; frames: white, anthracite, wood
const SHUTTERS = ["#556b52", "#6f8069", "#4b5d50", "#8b9290", "#7c3d31", "#8a6a48", "#5f7179"];
const BLINDS = ["#a9acae", "#5a5e62", "#c4bfb3"];
const FRAMES = { white: "#eeeeea", anthracite: "#3b3e42", wood: "#7a5a3e" };
// no white or pale grey: on a render a door must read as one
const DOORS = ["#6a4a33", "#3b3e42", "#7d5c3f", "#2e4756", "#556b52", "#7c3d31", "#5d554c"];
const STONE = "#cbc3b0"; // a molasse surround
const TRIM = ["#b9b6ae", "#a7a39a", "#cfccc4"]; // sills, canopies, steps

// a roof face more level than 5° is flat (gravel); steeper, it is tiled: a 10° lean-to or a low hip is tiles
export const FLAT_ROOF = Math.cos((5 * Math.PI) / 180);
const srgb = (hex) => [0, 2, 4].map((k) => parseInt(hex.slice(1 + k, 3 + k), 16) / 255);
// the region's roofs (sRGB): clay tiles, terracotta and brown; fibre cement and concrete tiles, grey and anthracite
export const ROOF_TINTS = {
  terracotta: ["#86523f", "#8f5843", "#7a4b3b"].map(srgb),
  brown: ["#6b3d2e", "#5a3a2e", "#7a4632"].map(srgb),
  grey: ["#666560", "#6e6d69", "#5a5d61"].map(srgb),
  anthracite: ["#4a4c4f", "#525458", "#44464a"].map(srgb),
};

/** The id's hash: the render a house wears (RENDERS) and the seed of its choices. */
export function houseHash(id) {
  return [...String(id)].reduce((a, ch) => (a * 31 + ch.charCodeAt(0)) >>> 0, 7);
}

function random(seed) {
  let s = seed >>> 0 || 1;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const pick = (rnd, list) => list[Math.floor(rnd() * list.length) % list.length];

/** A house's look: storey height, window size, frames, shutters or blinds, door. Newer houses (the lidar's) are modern. */
export function houseStyle(id, source) {
  const hash = houseHash(id), rnd = random(hash ^ 0x5bd1e995);
  const modern = source === "lidar";
  const r = rnd();
  const closure = modern ? (r < 0.7 ? "blinds" : "none") : r < 0.45 ? "shutters" : r < 0.8 ? "blinds" : "none";
  const f = rnd();
  const frame = modern ? (f < 0.6 ? "anthracite" : "white") : f < 0.6 ? "white" : f < 0.75 ? "anthracite" : "wood";
  return {
    hash,
    modern,
    storey: 2.75 + rnd() * 0.3,
    width: modern ? 1.1 + rnd() * 0.5 : 1.0 + rnd() * 0.35,
    height: 1.35 + rnd() * 0.2,
    density: 0.75 + rnd() * 0.25,
    closure,
    frame: FRAMES[frame],
    shutter: pick(rnd, SHUTTERS),
    blind: pick(rnd, BLINDS),
    surround: !modern && closure === "shutters" && rnd() < 0.5 ? STONE : null,
    band: modern ? 0.06 : 0.12, // the reveal's width around the opening
    depth: modern ? 0.1 : 0.12, // how far it stands out of the wall
    door: pick(rnd, DOORS),
    canopy: rnd() < 0.5,
    french: rnd() < 0.6,
    trim: pick(rnd, TRIM),
    seed: hash,
  };
}

/**
 * A house's façades: its wall triangles grouped by plane (direction and offset), each with its outward normal,
 * the direction along it, its extent, and its triangles in (along, up) coordinates with their 3D corners.
 */
export function facadesOf(pts, wall, jag = 0) {
  const planes = new Map();
  for (let t = 0; t < wall.length; t += 3) {
    const a = pts[wall[t]], b = pts[wall[t + 1]], c = pts[wall[t + 2]];
    const ux = b[0] - a[0], uy = b[1] - a[1], uz = b[2] - a[2], vx = c[0] - a[0], vy = c[1] - a[1], vz = c[2] - a[2];
    let nx = uy * vz - uz * vy, nz = ux * vy - uy * vx;
    const l = Math.hypot(nx, nz);
    if (l < 1e-6) continue; // a level sliver
    nx /= l; nz /= l;
    const off = a[0] * nx + a[2] * nz;
    const key = `${Math.round(Math.atan2(nz, nx) * 20)}:${Math.round(off * 3)}`;
    if (!planes.has(key)) planes.set(key, { sx: 0, sz: 0, tris: [] });
    const p = planes.get(key);
    p.sx += nx * l; p.sz += nz * l;
    p.tris.push([a, b, c, [nx, nz]]);
  }
  let groups = [...planes.values()];
  if (jag > 0) groups = merged(groups, jag);
  const out = [];
  for (const p of groups) {
    const l = Math.hypot(p.sx, p.sz) || 1, n = [p.sx / l, p.sz / l];
    const t = [n[1], -n[0]]; // to the right, seen from outside
    const tris = p.tris.map(([a, b, c, tn]) => ({
      q: [a, b, c].map((v) => [v[0] * t[0] + v[2] * t[1], v[1]]),
      v: [a, b, c],
      n: tn,
    }));
    let lo = Infinity, hi = -Infinity, bottom = Infinity, top = -Infinity;
    for (const tr of tris) for (const [u, y] of tr.q) { lo = Math.min(lo, u); hi = Math.max(hi, u); bottom = Math.min(bottom, y); top = Math.max(top, y); }
    out.push({ n, t, tris, lo, hi, len: hi - lo, bottom, top, jag });
  }
  return out;
}

/**
 * The planes of a stepped wall taken as one façade: the lidar's outlines zigzag by a few decimetres around the
 * real wall, in pieces about a metre long. Planes within 4° and `jag` metres of each other, end to end, merge.
 */
function merged(groups, jag) {
  const info = groups.map((g) => {
    const l = Math.hypot(g.sx, g.sz) || 1, n = [g.sx / l, g.sz / l];
    let x = 0, z = 0, k = 0;
    for (const [a, b, c] of g.tris) for (const v of [a, b, c]) { x += v[0]; z += v[2]; k++; }
    return { n, c: [x / k, z / k] };
  });
  const range = (g, t) => {
    let lo = Infinity, hi = -Infinity;
    for (const [a, b, c] of g.tris) for (const v of [a, b, c]) { const u = v[0] * t[0] + v[2] * t[1]; lo = Math.min(lo, u); hi = Math.max(hi, u); }
    return [lo, hi];
  };
  const root = groups.map((_, i) => i);
  const find = (i) => (root[i] === i ? i : (root[i] = find(root[i])));
  for (let i = 0; i < groups.length; i++) for (let j = i + 1; j < groups.length; j++) {
    const ni = info[i].n, nj = info[j].n;
    if (ni[0] * nj[0] + ni[1] * nj[1] < Math.cos(0.07)) continue;
    const d = (info[j].c[0] - info[i].c[0]) * ni[0] + (info[j].c[1] - info[i].c[1]) * ni[1];
    if (Math.abs(d) > jag) continue;
    const t = [ni[1], -ni[0]], [a0, a1] = range(groups[i], t), [b0, b1] = range(groups[j], t);
    if (Math.max(a0, b0) - Math.min(a1, b1) > 0.6) continue;
    root[find(i)] = find(j);
  }
  const out = new Map();
  groups.forEach((g, i) => {
    const r = find(i);
    if (!out.has(r)) out.set(r, { sx: 0, sz: 0, tris: [] });
    const m = out.get(r);
    m.sx += g.sx; m.sz += g.sz;
    m.tris.push(...g.tris);
  });
  return [...out.values()];
}

/** The wall at (u, y) on a façade: the point on its triangle and that triangle's normal, or null off the wall. */
function wallAt(f, u, y) {
  if (!Number.isFinite(u) || !Number.isFinite(y)) return null;
  for (const tr of f.tris) {
    const [[x0, y0], [x1, y1], [x2, y2]] = tr.q;
    const d = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2);
    if (Math.abs(d) < 1e-9) continue;
    const a = ((y1 - y2) * (u - x2) + (x2 - x1) * (y - y2)) / d;
    const b = ((y2 - y0) * (u - x2) + (x0 - x2) * (y - y2)) / d;
    const c = 1 - a - b;
    if (a < -1e-4 || b < -1e-4 || c < -1e-4) continue;
    const [p, q, r] = tr.v;
    return { p: [a * p[0] + b * q[0] + c * r[0], a * p[1] + b * q[1] + c * r[1], a * p[2] + b * q[2] + c * r[2]], n: tr.n };
  }
  return null;
}

/** The wall's lowest and highest point over the vertical line at u (NaN off the façade). */
function spanAt(f, u) {
  let lo = Infinity, hi = -Infinity;
  for (const tr of f.tris) {
    const q = tr.q;
    for (let k = 0; k < 3; k++) {
      const [ua, ya] = q[k], [ub, yb] = q[(k + 1) % 3];
      if ((u < Math.min(ua, ub) - 1e-6) || (u > Math.max(ua, ub) + 1e-6)) continue;
      const y = Math.abs(ub - ua) < 1e-9 ? null : ya + ((u - ua) / (ub - ua)) * (yb - ya);
      for (const v of y === null ? [ya, yb] : [y]) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    }
  }
  return Number.isFinite(lo) ? [lo, hi] : [NaN, NaN];
}

/** The wall's foot and top every 25 cm along a façade: [u, lowest, highest] (computed once). */
function profileOf(f) {
  if (!f.profile) {
    f.profile = [];
    for (let u = f.lo + 0.05; u <= f.hi - 0.05; u += 0.25) f.profile.push([u, ...spanAt(f, u)]);
  }
  return f.profile;
}

/**
 * Whether the rectangle [u0, u1] × [y0, y1] of a façade is wall throughout, flat (within FLAT of its centre's
 * depth): returns the wall at its centre ({ p, n }) or null.
 */
function onWall(f, u0, u1, y0, y1) {
  const cu = (u0 + u1) / 2, cy = (y0 + y1) / 2;
  const centre = wallAt(f, cu, cy);
  if (!centre) return null;
  // flat: the centre's own plane; stepped (f.jag): the façade's, through the outermost step, the opening's reveal
  // reaching back to the deepest (`recess`)
  const n = f.jag ? f.n : centre.n;
  const d0 = centre.p[0] * n[0] + centre.p[2] * n[1];
  let lo = d0, hi = d0;
  const nu = Math.max(2, Math.ceil((u1 - u0) / STEP)), ny = Math.max(2, Math.ceil((y1 - y0) / STEP));
  for (let i = 0; i <= nu; i++) for (let j = 0; j <= ny; j++) {
    const w = wallAt(f, u0 + 0.01 + (i / nu) * (u1 - u0 - 0.02), y0 + 0.01 + (j / ny) * (y1 - y0 - 0.02));
    if (!w) return null;
    const d = w.p[0] * n[0] + w.p[2] * n[1];
    lo = Math.min(lo, d); hi = Math.max(hi, d);
    if (hi - lo > (f.jag || FLAT)) return null;
  }
  if (!f.jag) return { ...centre, recess: 0 };
  return { p: [centre.p[0] + n[0] * (hi - d0), centre.p[1], centre.p[2] + n[1] * (hi - d0)], n, recess: hi - lo };
}

/** The point at (u, y) of a façade on the plane of the wall triangle `ref` (a wallAt result), `out` metres out of it. */
function at(f, ref, u, y, out = 0) {
  const d0 = ref.p[0] * ref.n[0] + ref.p[2] * ref.n[1];
  const tn = f.t[0] * ref.n[0] + f.t[1] * ref.n[1], nn = f.n[0] * ref.n[0] + f.n[1] * ref.n[1];
  // along the façade's own t, then across along its n onto the triangle's plane: u stays u
  const s = (d0 - u * tn) / (Math.abs(nn) > 0.5 ? nn : 1);
  return [f.t[0] * u + f.n[0] * s + ref.n[0] * out, y, f.t[1] * u + f.n[1] * s + ref.n[1] * out];
}

/** The wall triangle's own direction along the façade (its normal turned to the right, seen from outside). */
const alongOf = (ref) => [ref.n[1], -ref.n[0]];

/** Every house's triangles in a grid of cells, for the segments a façade's openings look out along. */
export class Blockers {
  constructor(houses, cell = 8) {
    this.cell = cell;
    const tris = [];
    for (const h of houses) {
      for (const list of [h.wall, h.roof]) for (let t = 0; t < list.length; t += 3) {
        tris.push([h.pts[list[t]], h.pts[list[t + 1]], h.pts[list[t + 2]], list === h.wall]);
      }
    }
    // per triangle: a corner, its two edges and, for a wall, its outward normal (walls wind outward); its box
    this.geo = new Float64Array(tris.length * 12);
    this.box = new Float64Array(tris.length * 6);
    this.stamp = new Uint32Array(tris.length);
    this.visit = 0;
    this.grid = new Map();
    tris.forEach(([a, b, c, wall], k) => {
      const g = this.geo, o = k * 12;
      for (let i = 0; i < 3; i++) {
        g[o + i] = a[i];
        g[o + 3 + i] = b[i] - a[i];
        g[o + 6 + i] = c[i] - a[i];
      }
      if (wall) {
        g[o + 9] = g[o + 4] * g[o + 8] - g[o + 5] * g[o + 7];
        g[o + 10] = g[o + 5] * g[o + 6] - g[o + 3] * g[o + 8];
        g[o + 11] = g[o + 3] * g[o + 7] - g[o + 4] * g[o + 6];
      }
      for (let i = 0; i < 3; i++) {
        this.box[k * 6 + i] = Math.min(a[i], b[i], c[i]);
        this.box[k * 6 + 3 + i] = Math.max(a[i], b[i], c[i]);
      }
      const x0 = Math.floor(this.box[k * 6] / cell), x1 = Math.floor(this.box[k * 6 + 3] / cell);
      const z0 = Math.floor(this.box[k * 6 + 2] / cell), z1 = Math.floor(this.box[k * 6 + 5] / cell);
      for (let i = x0; i <= x1; i++) for (let j = z0; j <= z1; j++) {
        const key = i * 100003 + j;
        if (!this.grid.has(key)) this.grid.set(key, []);
        this.grid.get(key).push(k);
      }
    });
  }

  /** Whether the segment from p along the unit direction d for `len` metres meets a triangle. */
  hit(p, d, len) {
    let found = false;
    this.#each(p, d, len, (k) => {
      const t = this.#ray(k, p, d);
      found = t > 1e-4 && t < len;
      return found;
    });
    return found;
  }

  /**
   * Whether p stands inside a building (a wing drawn over the wall, or the lidar's overlapping volumes): the first
   * wall the ray from p along d meets within `len` metres turns its back to p.
   */
  inside(p, d, len) {
    let best = len, back = false;
    const g = this.geo;
    this.#each(p, d, len, (k) => {
      const o = k * 12;
      if (g[o + 9] === 0 && g[o + 10] === 0 && g[o + 11] === 0) return false;
      const t = this.#ray(k, p, d);
      if (t > 1e-4 && t < best) {
        best = t;
        back = g[o + 9] * d[0] + g[o + 10] * d[1] + g[o + 11] * d[2] > 0;
      }
      return false;
    });
    return back;
  }

  #each(p, d, len, visit) {
    const c = this.cell, box = this.box, stamp = ++this.visit;
    const ex = p[0] + d[0] * len, ey = p[1] + d[1] * len, ez = p[2] + d[2] * len;
    const x0 = Math.min(p[0], ex), x1 = Math.max(p[0], ex), y0 = Math.min(p[1], ey), y1 = Math.max(p[1], ey);
    const z0 = Math.min(p[2], ez), z1 = Math.max(p[2], ez);
    for (let i = Math.floor(x0 / c); i <= Math.floor(x1 / c); i++) {
      for (let j = Math.floor(z0 / c); j <= Math.floor(z1 / c); j++) {
        const list = this.grid.get(i * 100003 + j);
        if (!list) continue;
        for (const k of list) {
          if (this.stamp[k] === stamp) continue;
          this.stamp[k] = stamp;
          const b = k * 6;
          if (box[b] > x1 || box[b + 3] < x0 || box[b + 1] > y1 || box[b + 4] < y0 || box[b + 2] > z1 || box[b + 5] < z0) continue;
          if (visit(k)) return;
        }
      }
    }
  }

  /** Möller–Trumbore: the distance along d from o to triangle k, -1 when it misses. */
  #ray(k, o, d) {
    const g = this.geo, i = k * 12;
    const e1x = g[i + 3], e1y = g[i + 4], e1z = g[i + 5], e2x = g[i + 6], e2y = g[i + 7], e2z = g[i + 8];
    const px = d[1] * e2z - d[2] * e2y, py = d[2] * e2x - d[0] * e2z, pz = d[0] * e2y - d[1] * e2x;
    const det = e1x * px + e1y * py + e1z * pz;
    if (Math.abs(det) < 1e-12) return -1;
    const inv = 1 / det, sx = o[0] - g[i], sy = o[1] - g[i + 1], sz = o[2] - g[i + 2];
    const u = (sx * px + sy * py + sz * pz) * inv;
    if (u < 0 || u > 1) return -1;
    const qx = sy * e1z - sz * e1y, qy = sz * e1x - sx * e1z, qz = sx * e1y - sy * e1x;
    const v = (d[0] * qx + d[1] * qy + d[2] * qz) * inv;
    if (v < 0 || u + v > 1) return -1;
    return (e2x * qx + e2y * qy + e2z * qz) * inv;
  }
}

function percentile(values, q) {
  const s = [...values].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.max(0, Math.round(q * (s.length - 1))))];
}

/** The area a house covers seen from above (m², its convex hull: some entries come without a roof). */
function footprint(pts) {
  const p = pts.map((v) => [v[0], v[2]]).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const half = (list) => {
    const h = [];
    for (const q of list) {
      while (h.length >= 2 && cross(h[h.length - 2], h[h.length - 1], q) <= 0) h.pop();
      h.push(q);
    }
    return h.slice(0, -1);
  };
  const hull = [...half(p), ...half([...p].reverse())];
  let area = 0;
  for (let i = 0; i < hull.length; i++) {
    const a = hull[i], b = hull[(i + 1) % hull.length];
    area += a[0] * b[1] - b[0] * a[1];
  }
  return Math.abs(area) / 2;
}

/**
 * The openings of one house: windows by storey and column, attic windows in tall gables, its front door (or a
 * small volume's garage door or one window). `groundAt(x, z)`: the ground's height; `road(x, z, dx, dz, max)`: the
 * distance to a road from (x, z) along (dx, dz) within `max` metres (Infinity: none); `blockers`: a Blockers of
 * the houses, for what stands in front of a façade.
 */
export function planOpenings(house, { groundAt = () => 0, road = () => Infinity, blockers = null, flat = false } = {}) {
  const style = houseStyle(house.id, house.source);
  const rnd = random(style.seed);
  const render = house.render ?? RENDERS[style.hash % RENDERS.length];
  const result = { openings: [], rows: [], small: false, style };
  const facades = facadesOf(house.pts, house.wall, house.source === "lidar" ? JAG : 0).filter((f) => f.len >= MIN_FACADE);
  if (!facades.length) return result;
  const free = (p, d, len) => !blockers?.hit(p, d, len);

  // the ground floor: a step above most of the ground around the walls (a slope shows a lower storey downhill)
  // the ground along the wall's foot and half a metre out, the higher (a slope falling away from the wall
  // would put a door's foot under the grass)
  const ground = (f, ref, u) => {
    const p = at(f, ref, u, 0, 0.1), q = at(f, ref, u, 0, 0.6);
    return Math.max(groundAt(p[0], p[2]), groundAt(q[0], q[2]));
  };
  // (a wall that starts well above the ground stands on a lower part of the house: it does not say where its
  // ground floor is)
  const levels = [], high = [];
  const refOf = new Map(), midOf = new Map();
  for (const f of facades) {
    // the anchor: the wall's foot nearest the middle (a plane's walls may stand apart, a notch between them)
    let mid = (f.lo + f.hi) / 2, bot = NaN;
    for (let k = 0; k <= 40 && !Number.isFinite(bot); k++) {
      const u = (f.lo + f.hi) / 2 + (k % 2 ? 1 : -1) * Math.ceil(k / 2) * (f.len / 40);
      [bot] = spanAt(f, u);
      mid = u;
    }
    const ref = wallAt(f, mid, bot + 0.05) ?? wallAt(f, mid, (f.bottom + f.top) / 2);
    if (!ref) continue;
    refOf.set(f, ref);
    midOf.set(f, mid);
    for (const u of [f.lo + 0.4, mid, f.hi - 0.4]) {
      const [b] = spanAt(f, u);
      if (!Number.isFinite(b)) continue;
      const g = ground(f, ref, u);
      (b < g + 1.2 ? levels : high).push(Math.max(g, b));
    }
  }
  if (!levels.length && !high.length) return result;
  const floor0 = (levels.length ? percentile(levels, 0.7) : Math.min(...high)) + 0.15;
  const top = Math.max(...facades.map((f) => f.top));
  // how tall it stands on its lowest side: a garage dug into a slope shows its door downhill only
  const low = levels.length ? Math.min(...levels) : floor0;
  const area = footprint(house.pts);
  Object.assign(result, { floor0, top, area });
  if (area < TINY_AREA || top - low < TINY_HEIGHT) return result;
  const small = (area < SMALL_AREA && top - floor0 < SMALL_HEIGHT) || top - floor0 < LOW_HEIGHT;
  result.small = small;

  const { band, depth } = style;
  const add = (o) => result.openings.push({ ...o, band, depth, render, style, flat });
  // an opening's rectangle on its façade fits when it is all wall, flat, above the ground and nothing stands in
  // front of it (a wing, a lower roof against the wall, the house next door) for `out` metres; returns its anchor
  const misses = (result.misses = { wall: 0, ground: 0, blocked: 0 });
  const fits = (f, u, w, y0, h, { ground: g = null, out = 0.8 } = {}) => {
    const ref = onWall(f, u - w / 2 - band - 0.04, u + w / 2 + band + 0.04, y0 - 0.08, y0 + h + band + 0.08);
    if (!ref) { misses.wall++; return null; }
    if (g !== null && y0 < g) { misses.ground++; return null; }
    // the head only as far as the reveal stands out: a roof's plane drawn on past the eaves passes over it
    const probes = [[0, h / 2, out], [-w / 2 - band, -0.06, out], [w / 2 + band, -0.06, out],
      [-w / 2 - band, h + band, depth + 0.05], [w / 2 + band, h + band, depth + 0.05]];
    for (const [du, dy, len] of probes) {
      const p = at(f, ref, u + du, y0 + dy, 0.02), d = [ref.n[0], 0, ref.n[1]];
      if (!free(p, d, len) || (len === out && blockers?.inside(p, d, INSIDE))) { misses.blocked++; return null; }
    }
    // a sill right on a lower roof reads as cut by it
    for (const du of [-w / 2 - band, w / 2 + band]) {
      if (!free(at(f, ref, u + du, y0 + 0.02, 0.3), [0, -1, 0], SILL_OVER_ROOF)) { misses.blocked++; return null; }
    }
    return ref;
  };
  const groundUnder = (f, ref, u, w) => Math.max(ground(f, ref, u - w / 2), ground(f, ref, u + w / 2), ground(f, ref, u));

  // the façade facing the nearest road (the survey's asphalt), else the longest
  const roadOf = (f) => {
    const ref = refOf.get(f);
    if (!ref) return Infinity;
    let best = Infinity;
    for (const u of [f.lo + f.len * 0.25, (f.lo + f.hi) / 2, f.hi - f.len * 0.25]) {
      const p = at(f, ref, u, 0, 0.3);
      for (const turn of [0, 0.4, -0.4]) {
        const c = Math.cos(turn), s = Math.sin(turn);
        best = Math.min(best, road(p[0], p[2], c * f.n[0] - s * f.n[1], s * f.n[0] + c * f.n[1], ROAD_SEARCH));
      }
    }
    return best;
  };
  const byRoad = facades.map((f) => ({ f, d: roadOf(f) })).sort((a, b) => a.d - b.d || b.f.len - a.f.len);
  const longest = [...facades].sort((a, b) => b.len - a.len);
  const front = Number.isFinite(byRoad[0].d) ? byRoad.map((r) => r.f) : longest;

  // a small low volume: a garage door on a façade toward a close road, else one small window, each on its own
  // façade's ground. Also what a house gets when its walls hold nothing else (eaves too low for a storey's windows)
  const annex = () => {
    for (const { f, d } of byRoad) {
      if (!(d < GARAGE_ROAD) || f.len < 3.2 || !refOf.has(f)) continue;
      const u = midOf.get(f), w = Math.min(2.6, f.len - 2 * band - 0.5), ref0 = refOf.get(f);
      const y0 = groundUnder(f, ref0, u, w) + 0.01;
      if (Math.max(spanAt(f, u - w / 2)[0], spanAt(f, u + w / 2)[0]) > y0 + 0.08) continue;
      const ref = fits(f, u, w, y0, 2.1, { out: 1.0 });
      if (ref) { add({ kind: "garage", f, ref, u, y0, w, h: 2.1 }); return; }
    }
    for (const f of longest) {
      const ref0 = refOf.get(f);
      if (!ref0) continue;
      for (const du of [0, -0.25, 0.25]) {
        const u = midOf.get(f) + du * f.len, y0 = groundUnder(f, ref0, u, 0.8) + 1.2;
        const ref = fits(f, u, 0.8, y0, 0.7);
        if (ref) { add({ kind: "window", f, ref, u, y0, w: 0.8, h: 0.7, shutters: false, blind: 0 }); return; }
      }
    }
  };
  if (small) {
    annex();
    return result;
  }

  // the columns of each façade: at most one window per PER_WINDOW metres, irregular by the house
  const W = style.width, HIGH = style.height, S = style.storey;
  const pitch = style.closure === "shutters" ? 2 * W + 2 * band + 0.3 : W + 2 * band + 0.6;
  const columns = new Map();
  for (const f of facades) {
    const most = Math.floor(f.len / PER_WINDOW);
    let n = f.len < PER_WINDOW ? (rnd() < 0.6 ? 1 : 0) : Math.max(1, Math.min(most, Math.round((f.len / PER_WINDOW) * style.density)));
    const margin = Math.max(0.7, W / 2 + band + 0.35);
    while (n > 0 && (f.len - 2 * margin) / n < pitch) n--;
    if (n === 0 && f.len >= W + 2 * band + 1.0) n = rnd() < 0.5 ? 1 : 0;
    const slot = n ? (f.len - 2 * margin) / n : 0;
    const us = [];
    for (let i = 0; i < n; i++) us.push(f.lo + margin + (i + 0.5) * slot + (rnd() - 0.5) * 0.36 * Math.max(0, slot - pitch + W));
    columns.set(f, us);
  }

  // the front door: in a column of the façade toward the road (or its middle), on the ground
  let door = null;
  for (const f of front.slice(0, 3)) {
    const ref0 = refOf.get(f);
    if (!ref0) continue;
    const mid = midOf.get(f);
    const options = [...(columns.get(f) ?? [])].sort((a, b) => Math.abs(a - mid) - Math.abs(b - mid));
    if (!options.length) options.push(mid);
    for (const u of options) {
      const w = 1.05, h = 2.15;
      // on the ground, or on the wall's foot where the house stands a little above it (a step up to it)
      const g = groundUnder(f, ref0, u, w), foot = Math.max(spanAt(f, u - w / 2)[0], spanAt(f, u + w / 2)[0]);
      if (!(foot < g + 1.2)) continue;
      const y0 = Math.max(g, foot) + 0.02;
      // a canopy over it where the wall rises high enough
      for (const canopy of style.canopy ? [true, false] : [false]) {
        const ref = fits(f, u, w, y0, h + (canopy ? 0.35 : 0), { out: 1.2 });
        if (!ref) continue;
        const low = Math.min(ground(f, ref, u - w / 2), ground(f, ref, u + w / 2));
        door = { kind: "door", f, ref, u, y0, w, h, canopy, step: y0 - low > 0.15 ? y0 - low + 0.1 : 0 };
        break;
      }
      if (door) break;
    }
    if (door) break;
  }
  if (door) add(door);

  // the windows, storey by storey; a row the wall only partly holds (a gable) gets one window at its middle
  const french = style.french ? (longest.find((f) => f !== door?.f && (columns.get(f) ?? []).length >= 2) ?? null) : null;
  for (const f of facades) {
    const ref0 = refOf.get(f);
    if (!ref0) continue;
    const us = columns.get(f) ?? [];
    for (let k = -2; k < 12; k++) {
      const sill = floor0 + k * S + 0.9;
      if (sill + LOW_WINDOW > f.top) break;
      // how much of the façade holds a whole window at this height (a lower one under low eaves)
      let across = [], H = HIGH;
      for (const h of [HIGH, LOW_WINDOW]) {
        across = [];
        for (const [u, b, t] of profileOf(f)) if (b <= sill - 0.1 && t >= sill + h + band + 0.1) across.push(u);
        H = h;
        if (across.length) break;
      }
      if (!across.length) continue;
      const span = across[across.length - 1] - across[0];
      const partial = span < f.len * 0.7 && span < f.len - 1.5;
      const row = { facade: f, k, len: f.len, count: 0 };
      if (partial) {
        // an attic window in the gable: smaller, centred on what the wall holds
        if (k < 1 || span < 1.9) continue;
        const u = (across[0] + across[across.length - 1]) / 2, w = Math.min(0.9, W), h = Math.min(1.1, H);
        const y0 = sill + 0.1;
        const ref = fits(f, u, w, y0, h, { ground: groundUnder(f, ref0, u, w) + 0.4 });
        if (ref) { add({ kind: "window", f, ref, u, y0, w, h, shutters: false, blind: 0 }); row.count++; }
        result.rows.push(row);
        continue;
      }
      for (const u of us) {
        if (us.length >= 3 && rnd() < 0.08) continue; // now and then a blank in a column (in a row long enough to hide it)
        if (door && door.f === f && Math.abs(u - door.u) < W / 2 + door.w / 2 + 2 * band + 0.2 && sill < door.y0 + door.h + band + 0.5) continue;
        const g = groundUnder(f, ref0, u, W);
        let y0 = sill, h = H, ref = null;
        if (k === 0 && f === french && us.indexOf(u) < 2 && g <= floor0 + 0.05 && g >= floor0 - 0.6) {
          y0 = floor0 + 0.02; h = 2.2;
          ref = fits(f, u, W, y0, h, { ground: g });
        }
        // a French window the ground or a neighbour refuses is still a window
        if (!ref) { y0 = sill; h = H; ref = fits(f, u, W, y0, h, { ground: g + 0.45 }); }
        if (!ref) continue;
        const shutters = style.closure === "shutters" && Boolean(onWall(f, u - W - band - 0.05, u + W + band + 0.05, y0, y0 + h));
        const blind = style.closure === "blinds" && rnd() < 0.45 ? 0.15 + rnd() * 0.85 : 0;
        add({ kind: h > 2 ? "french" : "window", f, ref, u, y0, w: W, h, shutters, blind });
        row.count++;
      }
      result.rows.push(row);
    }
  }
  if (!result.openings.length) annex();
  return result;
}

// ---- geometry

const BOX = new THREE.BoxGeometry(1, 1, 1);
const PANE = new THREE.PlaneGeometry(1, 1);
const PLANES = new Set(["frame", "glass", "panels"]);
let _materials = null;

/**
 * The material of one kind of part. All share one GPU program (each new one costs the page's load a compile):
 * the instance's colour, and louvres for `slats` > 0 (shutters, blinds, a garage door's panels): that many light
 * and shade bands a metre up its height, counted from each instance's own height so a slat stays its size.
 */
function openingMaterial(params, slats = 0) {
  const material = new THREE.MeshStandardMaterial(params);
  material.onBeforeCompile = (shader) => {
    shader.uniforms.slats = { value: slats };
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nuniform float slats;\nvarying float vSlat;")
      .replace("#include <begin_vertex>", `#include <begin_vertex>\n#ifdef USE_INSTANCING\nvSlat = (position.y + 0.5) * length(instanceMatrix[1].xyz) * slats;\n#else\nvSlat = position.y * slats;\n#endif`);
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", "#include <common>\nuniform float slats;\nvarying float vSlat;")
      .replace("#include <map_fragment>", `#include <map_fragment>
        if (slats > 0.0) {
          float s = fract(vSlat);
          // each slat lit along its upper edge, in its neighbour's shade below
          diffuseColor.rgb *= mix(0.7, 1.0, smoothstep(0.0, 0.3, s)) * (1.0 - 0.18 * smoothstep(0.8, 1.0, s));
        }`);
  };
  material.customProgramCacheKey = () => "housekit-neighbours-openings";
  return material;
}

function materials() {
  if (_materials) return _materials;
  // far from the camera the wall and its openings are a few metres apart in depth: drawn a little nearer
  const lift = { polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -2 };
  _materials = {
    reveal: openingMaterial({ roughness: 0.95 }),
    frame: openingMaterial({ roughness: 0.6, ...lift }),
    // dark glass that reflects the sky (the presentation look's environment) more as the view grazes it
    glass: openingMaterial({ color: "#26323c", roughness: 0.04, metalness: 0.35, ...lift, polygonOffsetUnits: -4 }),
    trim: openingMaterial({ roughness: 0.8 }),
    slats: openingMaterial({ roughness: 0.7, ...lift, polygonOffsetUnits: -6 }, 14),
    panels: openingMaterial({ roughness: 0.55, ...lift }, 2.2),
  };
  return _materials;
}

/** The parts of one opening: [mesh name, centre (u, y, out), size (along, up, out), colour]. */
function partsOf(o) {
  const { w, h, band: b, depth: D, style } = o;
  const r = o.ref.recess ?? 0; // on a stepped wall, back to its deepest step
  const parts = [];
  const surround = style.surround ?? o.render;
  if (!o.flat) {
    // the reveal: jambs and head standing out of the wall, so the glass sits back in its shade
    parts.push(["reveal", [-(w + b) / 2, (h + b) / 2, (D - r) / 2], [b, h + b, D + r], surround]);
    parts.push(["reveal", [(w + b) / 2, (h + b) / 2, (D - r) / 2], [b, h + b, D + r], surround]);
    parts.push(["reveal", [0, h + b / 2, (D - r) / 2], [w + 2 * b, b, D + r], surround]);
  } else if (style.surround) {
    parts.push(["frame", [0, (h + b) / 2, 0.015], [w + 2 * b, h + b, 1], surround]);
  }
  // frames, leaves and panels are planes: their edges stand against the reveal's jambs and head, on the sill
  if (o.kind === "garage") {
    parts.push(["panels", [0, h / 2, 0.05], [w, h, 1], style.modern ? style.frame : pick(random(style.seed + 3), ["#e9e7e1", "#8b9290", "#3b3e42"])]);
    return parts;
  }
  if (o.kind === "door") {
    parts.push(["frame", [0, h / 2, 0.06], [w, h, 1], style.door]);
    // a strip of glass on the hinge side, or a pane in the upper part; a handle bar on the other side
    const side = style.hash & 1 ? 1 : -1;
    if (style.hash & 2) parts.push(["glass", [side * (w / 2 - 0.2), h / 2 + 0.15, 0.065], [0.16, h - 0.75, 1], null]);
    else parts.push(["glass", [0, h * 0.72, 0.065], [w - 0.36, h * 0.3, 1], null]);
    parts.push(["trim", [-side * (w / 2 - 0.12), h * 0.48, 0.09], [0.03, 0.4, 0.04], "#b8bcbf"]);
    if (o.canopy) parts.push(["trim", [0, h + b + 0.2, 0.45], [w + 2 * b + 0.5, 0.08, 0.9], style.trim]);
    if (o.step > 0) parts.push(["trim", [0, -o.step / 2, (0.4 - r) / 2], [w + 2 * b + 0.3, o.step, 0.4 + r], style.trim]);
    return parts;
  }
  // the frame behind the glass; two panes either side of a mullion when wide enough
  const fw = 0.065;
  parts.push(["frame", [0, h / 2, 0.04], [w, h, 1], style.frame]);
  if (w >= 0.85) {
    const pw = (w - 3 * fw) / 2;
    parts.push(["glass", [-(pw + fw) / 2, h / 2, 0.055], [pw, h - 2 * fw, 1], null]);
    parts.push(["glass", [(pw + fw) / 2, h / 2, 0.055], [pw, h - 2 * fw, 1], null]);
  } else {
    parts.push(["glass", [0, h / 2, 0.055], [w - 2 * fw, h - 2 * fw, 1], null]);
  }
  // the sill (a French window has a threshold only)
  if (o.kind !== "french" && !o.flat) parts.push(["trim", [0, -0.025, (D + 0.05 - r) / 2], [w + 0.1, 0.05, D + 0.05 + r], style.trim]);
  if (o.shutters) {
    const lw = w / 2 + 0.02;
    for (const side of [-1, 1]) parts.push(["slats", [side * (w / 2 + b + lw / 2 + 0.02), h / 2, 0.025], [lw, h + 0.04, 0.035], style.shutter]);
  }
  if (o.blind > 0) {
    const bh = o.blind * h;
    parts.push(["slats", [0, h - bh / 2, 0.08], [w, bh, 0.01], style.blind]);
  }
  return parts;
}

/**
 * The openings as a few InstancedMeshes (reveals, frames and leaves, glass, sills and canopies, shutters and
 * blinds, garage doors), each instance in its house's colours.
 */
export function openingMeshes(openings) {
  const mats = materials();
  const lists = { reveal: [], frame: [], glass: [], trim: [], slats: [], panels: [] };
  for (const o of openings) {
    // the opening's frame: its foot on the wall, along the wall, out of it
    const frame = [at(o.f, o.ref, o.u, o.y0, 0), alongOf(o.ref), o.ref.n];
    for (const part of partsOf(o)) lists[part[0]].push([frame, part]);
  }
  const group = new THREE.Group();
  group.name = "Surroundings: doors and windows";
  const colours = new Map();
  const rgb = (c) => {
    if (!colours.has(c)) colours.set(c, new THREE.Color(c));
    return colours.get(c);
  };
  for (const [name, list] of Object.entries(lists)) {
    if (!list.length) continue;
    const mesh = new THREE.InstancedMesh(PLANES.has(name) ? PANE : BOX, mats[name], list.length);
    const mat = mesh.instanceMatrix.array;
    // every part its colour (white: the material's own), so that all share one program
    const tint = new Float32Array(list.length * 3).fill(1);
    list.forEach(([[base, t, n], [, [cu, cy, co], [sx, sy, sz], c]], i) => {
      // column-major: along the wall, up, out of it, the position
      mat.set([t[0] * sx, 0, t[1] * sx, 0, 0, sy, 0, 0, n[0] * sz, 0, n[1] * sz, 0,
        base[0] + t[0] * cu + n[0] * co, base[1] + cy, base[2] + t[1] * cu + n[1] * co, 1], i * 16);
      if (c) rgb(c).toArray(tint, i * 3);
    });
    mesh.instanceColor = new THREE.InstancedBufferAttribute(tint, 3);
    mesh.instanceMatrix.needsUpdate = true;
    mesh.castShadow = name === "reveal" || name === "trim" || name === "slats";
    mesh.receiveShadow = true;
    mesh.userData = { kind: "context", part: name };
    mesh.computeBoundingSphere();
    group.add(mesh);
  }
  return group;
}

/**
 * Whether the aerial photo shows a finished roof under a house newer than it: most of its pixels (sRGB 0–1
 * [r, g, b] triples, sampled inside the roof) grey, tile-red or the blue of solar panels, and evenly lit
 * (a field, bare earth or a building site is neither). "photo" or "tiles".
 */
export function roofLook(pixels) {
  if (pixels.length < 20) return "tiles";
  let like = 0;
  const lums = [];
  for (const [r, g, b] of pixels) {
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), sat = mx > 0 ? (mx - mn) / mx : 0;
    let hue = 0;
    if (mx > mn) {
      const d = mx - mn;
      hue = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
      hue = (hue * 60 + 360) % 360;
    }
    const grey = sat < 0.12 && mx > 0.1 && mx < 0.85;
    const tile = (hue <= 30 || hue >= 340) && sat > 0.1 && mx > 0.12 && mx < 0.7;
    const panel = hue >= 190 && hue <= 260 && sat > 0.15;
    if (grey || tile || panel) like++;
    lums.push(0.2126 * r + 0.7152 * g + 0.0722 * b);
  }
  const spread = percentile(lums, 0.8) - percentile(lums, 0.2);
  return like / pixels.length >= 0.62 && spread <= 0.25 ? "photo" : "tiles";
}

/**
 * Which real roof colour a photographed roof (its median, sRGB 0–1) is: the aerial photo's haze greys and pales
 * them all, so a little warmth is clay (terracotta when light, else brown), none fibre cement or concrete (grey
 * when light, else anthracite).
 */
export function roofFamily([r, g, b]) {
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), light = (mx + mn) / 2;
  const sat = mx > mn ? (mx - mn) / (1 - Math.abs(2 * light - 1)) : 0;
  let hue = 0;
  if (mx > mn) {
    const d = mx - mn;
    hue = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
    hue = (hue * 60 + 360) % 360;
  }
  if ((hue <= 40 || hue >= 340) && sat >= 0.06) return light >= 0.47 ? "terracotta" : "brown";
  return light >= 0.45 ? "grey" : "anthracite";
}

/**
 * A tiled roof's colour (sRGB 0–1 [r, g, b]) for a house the photo shows unbuilt: a real roof colour of the family
 * of `photographed` (a neighbour's photographed roof, sRGB; none: any family), picked and shaded by the house's
 * hash, so neighbours differ.
 */
export function roofTint(photographed, hash) {
  const families = Object.keys(ROOF_TINTS);
  const family = photographed ? roofFamily(photographed) : families[(hash >>> 17) % families.length];
  const list = ROOF_TINTS[family], shade = 0.94 + ((hash >>> 4) % 13) / 100;
  return list[(hash >>> 13) % list.length].map((v) => Math.min(1, v * shade));
}

export default { RENDERS, PER_WINDOW, FLAT_ROOF, ROOF_TINTS, houseHash, houseStyle, facadesOf, Blockers, planOpenings, openingMeshes, roofLook, roofFamily, roofTint };
