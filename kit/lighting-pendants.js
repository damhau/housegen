// housekit lighting: pendants (ceiling fixtures on a cable or a rod). See lighting.js for the contract.
//
// Every pendant hangs from its fixing point (the origin, everything at y ≤ 0): `drop` is the distance
// from the ceiling to the fixture's bottom, `over: true` makes it an accent (a pendant over a table,
// an island or a bar, lit by day), else it is general light ("ceiling", off by day).
//
// Helpers of this file (lighting-parts.js is shared and stays as it is): a seeded random, an
// assembler that merges a fixture's parts by material (a cluster of ten shells is a handful of draw
// calls, not fifty), rods and cables between two points, canopies, and a sheet-metal shade turned on
// a lathe (the finish outside, a white inside that glows while the fixture is on).

import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import { metal, finishes, cyl, lit, suspension, addGlow, addCone, tagFixture, opal, clearGlass, bulbGlow, glowMaterial, WARM, NEUTRAL } from "./lighting-parts.js";

// ---------------------------------------------------------------- helpers of this file

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, Number(v)));
const r3 = (v) => Math.round(v * 1000) / 1000;
const ONE = new THREE.Vector3(1, 1, 1), UP = new THREE.Vector3(0, 1, 0);
const KEEP = new Set(["position", "normal", "uv"]);

/** A seeded random in [0, 1) (mulberry32): the same parameters give the same fixture. */
function random(seed = 1) {
  let a = (Math.round(seed) * 2654435761) >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** A placement: at [x, y, z], turned by the quaternion `q`. */
const at = (x, y, z, q = null) => new THREE.Matrix4().compose(new THREE.Vector3(x, y, z), q ?? new THREE.Quaternion(), ONE);
const turnY = (a) => new THREE.Quaternion().setFromAxisAngle(UP, a);
const centroid = (ps) => ps.reduce((c, p) => ({ x: c.x + p.x / ps.length, y: c.y + p.y / ps.length, z: c.z + p.z / ps.length }), { x: 0, y: 0, z: 0 });
const lightColour = (light) => (light === "neutral" ? NEUTRAL : WARM);

/**
 * A fixture's parts gathered by material and merged on build(): one mesh per material (and shadow
 * flag). add(geometry, material, matrix?, { shadow }); shadow false for the glowing parts.
 */
function assembler() {
  const sets = new Map();
  return {
    add(geometry, material, matrix = null, { shadow = true } = {}) {
      const key = `${material.uuid}:${shadow}`;
      if (!sets.has(key)) sets.set(key, { material, shadow, parts: [] });
      const g = geometry.index ? geometry.toNonIndexed() : geometry.clone();
      for (const name of Object.keys(g.attributes)) if (!KEEP.has(name)) g.deleteAttribute(name);
      g.clearGroups();
      if (matrix) g.applyMatrix4(matrix);
      sets.get(key).parts.push(g);
    },
    build(group) {
      for (const { material, shadow, parts } of sets.values()) {
        const mesh = new THREE.Mesh(parts.length > 1 ? mergeGeometries(parts) : parts[0], material);
        mesh.castShadow = mesh.receiveShadow = shadow;
        group.add(mesh);
      }
      return group;
    },
  };
}

/** Build the parts into `g` and tag it, its footprint and height measured on what was built. */
function finishFixture(g, A, { name, family, role, finish }) {
  A.build(g);
  const b = new THREE.Box3().setFromObject(g);
  return tagFixture(g, { name, family, role, finish, footprint: [r3(b.max.x - b.min.x), r3(b.max.z - b.min.z)], height: r3(-b.min.y), hang: true });
}

/** A rod or a cable of radius `r` from `a` to `b` ([x, y, z]). */
function rod(A, a, b, r, material, { seg = 8, shadow = false } = {}) {
  const pa = new THREE.Vector3(...a), pb = new THREE.Vector3(...b);
  const dir = pb.clone().sub(pa);
  const len = dir.length();
  if (len < 1e-4) return;
  const q = new THREE.Quaternion().setFromUnitVectors(UP, dir.normalize());
  const mid = pa.add(pb).multiplyScalar(0.5);
  A.add(new THREE.CylinderGeometry(r, r, len, seg, 1, true), material, at(mid.x, mid.y, mid.z, q), { shadow });
}

/** A round canopy of radius `r` and height `h` on the ceiling at [x, z]. */
function canopy(A, x, z, r, material, h = 0.022) {
  A.add(new THREE.CylinderGeometry(r, r, h, Math.max(16, Math.round(r * 480))), material, at(x, -h / 2, z));
}

/** The same surface facing the other way (winding reversed, normals negated): a shell's inside. */
function flipped(geometry) {
  const g = geometry.index ? geometry.toNonIndexed() : geometry.clone();
  for (const name of ["position", "normal", "uv"]) {
    const a = g.attributes[name];
    if (!a) continue;
    const s = a.itemSize, arr = a.array;
    for (let i = 0; i < a.count; i += 3) {
      for (let k = 0; k < s; k++) {
        const j1 = (i + 1) * s + k, j2 = (i + 2) * s + k;
        const v = arr[j1];
        arr[j1] = arr[j2];
        arr[j2] = v;
      }
    }
  }
  const n = g.attributes.normal.array;
  for (let i = 0; i < n.length; i++) n[i] = -n[i];
  return g;
}

/**
 * A sheet-metal shade turned from `profile` ([radius, y] pairs from the rim up to the top; repeat a
 * point for a sharp edge): `outer` outside, `inner` inside (`t` inward, facing in), a rim ring between.
 */
function shade(A, profile, outer, inner, matrix, { seg = 48, t = 0.0018 } = {}) {
  const pts = profile.map(([r, y]) => new THREE.Vector2(r, y));
  A.add(new THREE.LatheGeometry(pts, seg), outer, matrix);
  const ins = pts.map((p, i) => {
    const a = pts[Math.max(0, i - 1)], b = pts[Math.min(pts.length - 1, i + 1)];
    const nx = b.y - a.y, ny = a.x - b.x, l = Math.hypot(nx, ny) || 1; // outward (the profile goes up)
    return new THREE.Vector2(Math.max(0.001, p.x - (nx / l) * t), p.y - (ny / l) * t);
  }).reverse(); // top to rim: its faces look in
  A.add(new THREE.LatheGeometry(ins, seg), inner, matrix, { shadow: false });
  const r0 = pts[0].x, r1 = ins[ins.length - 1].x;
  A.add(new THREE.RingGeometry(Math.min(r0, r1), Math.max(r0, r1), seg).rotateX(Math.PI / 2).translate(0, pts[0].y, 0), outer, matrix);
}

/** The white inside of a metal shade: it glows a little while the fixture is on (the hidden bulb). */
const shadeInside = (strength = 0.45) =>
  glowMaterial({ color: "#f2efe8", emissive: "#ffdcb0", strength, roughness: 0.6, name: "shade inside" });

let _graphite;
/** A dark grey metal mixed with black in the faceted cluster (black and gunmetal shells). */
const graphite = () => (_graphite ??= new THREE.MeshStandardMaterial({ name: "graphite", color: "#8a8d91", roughness: 0.4, metalness: 0.6 }));

// ---------------------------------------------------------------- globe pendant (the reference)

/**
 * Globe pendant: an opal (or clear, with a bare bulb) glass globe on a cable. `d` its diameter, `drop`
 * from the ceiling to the globe's bottom, `finish` of the canopy and the cap. Glows every way.
 * Role "accent" when it hangs over a table or an island (`over: true`), else "ceiling".
 */
function globePendant({ d = 0.3, drop = 0.7, glass = "opal", finish = "brass", over = false, lm = 600 } = {}) {
  const g = new THREE.Group();
  const m = metal(finish);
  const bottom = suspension(g, drop - d, m);
  cyl(g, 0.035, 0.045, 0.05, bottom - 0.025, m); // the cap
  const cy = bottom - 0.05 - d / 2 + 0.02;
  const globe = new THREE.Mesh(new THREE.SphereGeometry(d / 2, 40, 24), glass === "clear" ? clearGlass() : opal());
  globe.position.y = cy;
  g.add(lit(globe));
  if (glass === "clear") {
    const bulb = new THREE.Mesh(new THREE.SphereGeometry(0.03, 20, 12), bulbGlow());
    bulb.position.y = cy;
    g.add(lit(bulb));
  }
  addGlow(g, [0, cy, 0], lm);
  return tagFixture(g, { name: `${glass} globe pendant`, family: "globePendant", role: over ? "accent" : "ceiling", finish,
    footprint: [d, d], height: drop + 0.025, hang: true });
}

// ---------------------------------------------------------------- faceted cluster

/**
 * A faceted sheet-metal shell (a dodecahedron of circumradius R) hung by a vertex or by a face, open
 * across the two facets that look most along `open`: its outside, its inside (facing in) and its top and
 * bottom (y, from its centre).
 */
function facetedShell(R, rnd, open, byVertex) {
  const g = new THREE.DodecahedronGeometry(R, 0);
  const p = g.attributes.position;
  const v0 = new THREE.Vector3().fromBufferAttribute(p, 0), v1 = new THREE.Vector3().fromBufferAttribute(p, 1), v2 = new THREE.Vector3().fromBufferAttribute(p, 2);
  const up = byVertex ? v0.clone().normalize() : v1.clone().sub(v0).cross(v2.clone().sub(v0)).normalize();
  g.applyQuaternion(new THREE.Quaternion().setFromUnitVectors(up, UP).premultiply(turnY(rnd() * Math.PI * 2)));
  g.scale(1, 0.9 + rnd() * 0.25, 1); // a little squat or tall
  // the face looking most along `open` is left out (its three triangles share its normal)
  const pos = g.attributes.position, uv = g.attributes.uv;
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
  const normals = [];
  for (let i = 0; i < pos.count; i += 3) {
    a.fromBufferAttribute(pos, i); b.fromBufferAttribute(pos, i + 1); c.fromBufferAttribute(pos, i + 2);
    normals.push(b.clone().sub(a).cross(c.clone().sub(a)).normalize());
  }
  let best = 0;
  normals.forEach((n, i) => { if (n.dot(open) > normals[best].dot(open)) best = i; });
  // and its neighbour (63° away) that looks most along `open` too: one wide opening across two facets
  let next = -1;
  normals.forEach((n, i) => {
    const d = n.dot(normals[best]);
    if (d > 0.3 && d < 0.6 && (next < 0 || n.dot(open) > normals[next].dot(open))) next = i;
  });
  const gone = (n) => n.dot(normals[best]) > 0.999 || (next >= 0 && n.dot(normals[next]) > 0.999);
  const P = [], N = [], U = [];
  normals.forEach((n, t) => {
    if (gone(n)) return;
    for (let k = 0; k < 3; k++) {
      const i = t * 3 + k;
      P.push(pos.getX(i), pos.getY(i), pos.getZ(i));
      N.push(n.x, n.y, n.z);
      U.push(uv.getX(i), uv.getY(i));
    }
  });
  const outside = new THREE.BufferGeometry();
  outside.setAttribute("position", new THREE.Float32BufferAttribute(P, 3));
  outside.setAttribute("normal", new THREE.Float32BufferAttribute(N, 3));
  outside.setAttribute("uv", new THREE.Float32BufferAttribute(U, 2));
  outside.computeBoundingBox();
  const inside = flipped(outside).scale(0.99, 0.99, 0.99);
  return { outside, inside, top: outside.boundingBox.max.y, bottom: outside.boundingBox.min.y };
}

/**
 * Faceted cluster: `n` faceted metal shells (`finish` outside, `inside` within, a bare bulb in each of
 * the first `bulbs`), each on its own cable and canopy at staggered heights, within `spread` metres;
 * `drop` from the ceiling to the lowest shell's bottom. Two glows inside the cluster (its two halves)
 * and a cone down from under it. Role "accent" with `over: true`, else "ceiling".
 */
function facetedCluster({ n = 11, size = 0.25, spread = 1.0, drop = 0.95, finish = "black", inside, bulbs, seed = 7, over = false, lm = 1000 } = {}) {
  n = Math.round(clamp(n, 3, 16));
  size = clamp(size, 0.12, 0.4);
  spread = clamp(spread, size * 1.5, 2);
  const nb = Math.round(clamp(bulbs ?? n, 0, n));
  const rnd = random(seed);
  const shellOut = metal(finish), shellAlt = finish === "black" ? graphite() : shellOut;
  const shellIn = metal(inside ?? (finish === "brass" ? "black" : "brass"));
  const cord = finishes.black(), bulb = bulbGlow(2.4);
  const g = new THREE.Group(), A = assembler();

  // where they hang: a sunflower spiral over the spread, each shell opening outward and down, then
  // pushed apart (up and down where two would touch, sideways where a cable would pierce a shell)
  const rx = Math.max(0.04, spread / 2 - size / 2);
  const items = [];
  for (let i = 0; i < n; i++) {
    const r = rx * Math.sqrt((i + 0.5) / n), ang = i * 2.39996 + rnd() * 0.3;
    const R = (size / 2) * (0.82 + 0.32 * rnd());
    const open = new THREE.Vector3(Math.cos(ang) * 0.8 + (rnd() - 0.5) * 0.5, -0.2 - 0.4 * rnd(), Math.sin(ang) * 0.8 + (rnd() - 0.5) * 0.5).normalize();
    items.push({ x: r * Math.cos(ang), z: r * Math.sin(ang), y: rnd() * size * 1.3, R, shell: facetedShell(R, rnd, open, rnd() < 0.6) });
  }
  for (let it = 0; it < 200; it++) {
    let moved = false;
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const p = items[i], q = items[j];
        const dx = p.x - q.x, dz = p.z - q.z, h = Math.hypot(dx, dz) || 1e-6;
        const clear = 0.85 * Math.max(p.R, q.R) + 0.012; // a cable beside the shell above
        if (h < clear) {
          const k = (clear - h) / 2 / h;
          p.x += dx * k; p.z += dz * k; q.x -= dx * k; q.z -= dz * k;
          moved = true;
        }
        const need = 0.95 * (p.R + q.R), hh = Math.hypot(p.x - q.x, p.z - q.z);
        if (hh >= need) continue;
        const v = Math.sqrt(need * need - hh * hh), dy = p.y - q.y;
        if (Math.abs(dy) >= v) continue;
        const s = dy >= 0 ? 1 : -1, push = (v - Math.abs(dy)) / 2 + 0.002;
        p.y += s * push;
        q.y -= s * push;
        moved = true;
      }
    }
    if (!moved) break;
  }
  // the lowest shell's bottom at -drop; a drop too short for the cluster hangs it lower
  const low = Math.min(...items.map((s) => s.y + s.shell.bottom));
  for (const s of items) s.y += -drop - low;
  const high = Math.max(...items.map((s) => s.y + s.shell.top));
  if (high > -0.12) for (const s of items) s.y -= high + 0.12;

  items.forEach((s, i) => {
    const m = at(s.x, s.y, s.z);
    A.add(s.shell.outside, i % 2 ? shellAlt : shellOut, m);
    A.add(s.shell.inside, shellIn, m);
    canopy(A, s.x, s.z, 0.026, shellOut, 0.02);
    if (i < nb) {
      // a bare bulb under its socket, hung from the shell's top
      const by = s.y - 0.012;
      A.add(new THREE.CylinderGeometry(0.0105, 0.012, 0.03, 12), cord, at(s.x, by + 0.036, s.z));
      A.add(new THREE.SphereGeometry(0.023, 16, 12), bulb, at(s.x, by, s.z), { shadow: false });
      rod(A, [s.x, -0.02, s.z], [s.x, by + 0.051, s.z], 0.0022, cord);
    } else {
      rod(A, [s.x, -0.02, s.z], [s.x, s.y + s.shell.top, s.z], 0.0022, cord);
    }
  });

  // light: a glow in each half of the cluster (the bulbs seen through the openings), a cone down
  const sorted = [...items].sort((a, b) => a.x - b.x), half = Math.ceil(n / 2);
  for (const part of [sorted.slice(0, half), sorted.slice(half)]) {
    const c = centroid(part);
    addGlow(g, [c.x, Math.min(c.y, -0.2), c.z], lm * 0.3);
  }
  const c = centroid(items);
  addCone(g, [c.x, -drop + 0.06, c.z], [0, -1, 0], lm * 0.4, { angle: 0.95, penumbra: 0.9 });
  return finishFixture(g, A, { name: "faceted cluster chandelier", family: "facetedCluster", role: over ? "accent" : "ceiling", finish });
}

// ---------------------------------------------------------------- disc arms

/**
 * Disc arms: from a round canopy, thin rods hold `arms` crossing horizontal arms (square bar, `finish`)
 * at slightly different heights, each ending in two round LED discs (`d` across, a `rim` metal
 * housing, an opal underside); rods to the ceiling hold the two longest arms' far ends. `span` the
 * longest arm, `drop` from the ceiling to the lowest disc's bottom. Up to three cones down, one per
 * group of discs. Role "accent" with `over: true`, else "ceiling".
 */
function discArms({ arms = 3, span = 1.2, d = 0.24, drop = 0.75, finish = "black", rim = "brass", over = false, lm = 1500 } = {}) {
  arms = Math.round(clamp(arms, 2, 4));
  span = clamp(span, 0.6, 2);
  d = clamp(d, 0.12, 0.4);
  drop = clamp(drop, 0.35, 2);
  const m = metal(finish), rm = metal(rim), under = opal(1.7);
  const g = new THREE.Group(), A = assembler();
  const bar = 0.012, t = 0.032, gap = 0.008; // the arms' section, the discs' thickness, the stem between
  canopy(A, 0, 0, 0.08, m, 0.03);
  // each arm a little off the centre (along it and across it), so the hangers stand apart and miss the other arms
  const along = [0.08, -0.06, 0.03, -0.02], lengths = [1, 0.86, 0.74, 0.64];
  const across = arms === 2 ? [-0.035, 0.035] : [-0.035, 0, 0.035, -0.065];
  const yLow = -drop + t + gap + bar / 2;
  const discs = [];
  for (let k = 0; k < arms; k++) {
    const th = 0.3 + (k * Math.PI) / arms;
    const dx = Math.cos(th), dz = Math.sin(th), px = -dz, pz = dx;
    const L = span * lengths[k], y = yLow + k * 0.07;
    const cx = dx * along[k] * span + px * across[k], cz = dz * along[k] * span + pz * across[k];
    A.add(new THREE.BoxGeometry(L, bar, bar), m, at(cx, y, cz, turnY(-th)));
    // its hanger from the canopy, where the arm passes nearest the centre
    const fx = px * across[k], fz = pz * across[k];
    rod(A, [fx, -0.03, fz], [fx, y + bar / 2, fz], 0.0035, m, { shadow: true });
    // a disc under each end, the far one larger
    const ends = [1, -1].map((s) => ({ s, x: cx + (dx * s * L) / 2, z: cz + (dz * s * L) / 2 }));
    ends.sort((a, b) => Math.hypot(b.x, b.z) - Math.hypot(a.x, a.z));
    ends.forEach((e, i) => discs.push({ x: e.x, z: e.z, y: y - bar / 2 - gap - t / 2, r: (i === 0 ? d : d * 0.84) / 2 }));
    if (k < 2) {
      // a rod to the ceiling on the arm, just inside the far disc
      const e = ends[0], back = d / 2 + 0.035;
      const sx = e.x - dx * e.s * back, sz = e.z - dz * e.s * back;
      canopy(A, sx, sz, 0.016, m, 0.014);
      rod(A, [sx, -0.014, sz], [sx, y + bar / 2, sz], 0.0035, m, { shadow: true });
    }
  }
  for (const c of discs) {
    A.add(new THREE.CylinderGeometry(c.r, c.r, t, 48, 1, true), rm, at(c.x, c.y, c.z)); // the housing's side
    A.add(new THREE.CircleGeometry(c.r, 48).rotateX(-Math.PI / 2), rm, at(c.x, c.y + t / 2, c.z)); // its top
    A.add(new THREE.RingGeometry(c.r * 0.9, c.r, 48).rotateX(Math.PI / 2), rm, at(c.x, c.y - t / 2, c.z)); // the lip
    A.add(new THREE.CircleGeometry(c.r * 0.9, 48).rotateX(Math.PI / 2), under, at(c.x, c.y - t / 2 - 0.0006, c.z), { shadow: false });
    A.add(new THREE.CylinderGeometry(0.01, 0.01, gap, 12), m, at(c.x, c.y + t / 2 + gap / 2, c.z)); // the stem to the arm
  }
  // light: the discs in up to three groups around the fixture, a cone down from each group
  const o = centroid(discs);
  discs.sort((a, b) => Math.atan2(a.z - o.z, a.x - o.x) - Math.atan2(b.z - o.z, b.x - o.x));
  const G = Math.min(3, discs.length);
  for (let i = 0; i < G; i++) {
    const part = discs.slice(Math.round((i * discs.length) / G), Math.round(((i + 1) * discs.length) / G));
    const c = centroid(part);
    addCone(g, [c.x, Math.min(...part.map((p) => p.y)) - t / 2 - 0.01, c.z], [0, -1, 0], lm / G, { angle: 1.05, penumbra: 0.85 });
  }
  return finishFixture(g, A, { name: "disc arms chandelier", family: "discArms", role: over ? "accent" : "ceiling", finish });
}

// ---------------------------------------------------------------- frame globes

/**
 * Frame globes: three interlocking rectangles of square bar (`finish`, brass by default): a horizontal
 * one `length` × `depth`, a hanging one across its left end in the plane of its front bar, and an
 * upright one across its right part whose uprights pass through its long bars; thin rods to the
 * ceiling. `globes` (3–4) opal globes of `d` sit on the bars or hang under the upright's top. `drop`
 * from the ceiling to the frame's bottom. Two glows. Role "accent" with `over: true`, else "ceiling".
 */
function frameGlobes({ length = 1.2, depth = 0.4, drop = 0.8, globes = 4, d = 0.13, finish = "brass", over = false, lm = 1000 } = {}) {
  length = clamp(length, 0.7, 1.8);
  depth = clamp(depth, 0.25, 0.6);
  drop = clamp(drop, 0.62, 2);
  globes = Math.round(clamp(globes, 1, 4));
  d = clamp(d, 0.08, 0.2);
  const s = 0.02, m = metal(finish), glass = opal(1.3);
  const g = new THREE.Group(), A = assembler();
  const wc = 0.42 * length, hc = 0.5; // the hanging frame
  const yA = -drop + hc * 0.62; // the horizontal frame
  const xC = -length / 2 + wc * 0.22, zF = depth / 2 - s / 2;
  const xB = length * 0.3, yBtop = Math.min(yA + 0.36, -0.07), yBbot = yA - 0.16; // the upright frame
  const shift = -(xC - wc / 2 + length / 2) / 2; // the whole centred on the fixing point
  const X = (x) => x + shift;
  const bars = (list) => { for (const [x, y, z, bx, by, bz] of list) A.add(new THREE.BoxGeometry(bx, by, bz), m, at(X(x), y, z)); };
  // horizontal: w along x, h along z
  const hz = (cx, cy, cz, w, h) => bars([[cx, cy, cz - h / 2 + s / 2, w, s, s], [cx, cy, cz + h / 2 - s / 2, w, s, s],
    [cx - w / 2 + s / 2, cy, cz, s, s, h - 2 * s], [cx + w / 2 - s / 2, cy, cz, s, s, h - 2 * s]]);
  // upright in the xy plane: w along x, h along y
  const xy = (cx, cy, cz, w, h) => bars([[cx, cy - h / 2 + s / 2, cz, w, s, s], [cx, cy + h / 2 - s / 2, cz, w, s, s],
    [cx - w / 2 + s / 2, cy, cz, s, h - 2 * s, s], [cx + w / 2 - s / 2, cy, cz, s, h - 2 * s, s]]);
  // upright in the zy plane: w along z, h along y
  const zy = (cx, cy, cz, w, h) => bars([[cx, cy - h / 2 + s / 2, cz, s, s, w], [cx, cy + h / 2 - s / 2, cz, s, s, w],
    [cx, cy, cz - w / 2 + s / 2, s, h - 2 * s, s], [cx, cy, cz + w / 2 - s / 2, s, h - 2 * s, s]]);
  hz(0, yA, 0, length, depth);
  xy(xC, -drop + hc / 2, zF, wc, hc);
  zy(xB, (yBtop + yBbot) / 2, 0, depth, yBtop - yBbot);
  // rods to the ceiling: two on the upright's top bar, two on the hanging frame's
  for (const [x, z, y] of [[xB, depth / 2 - 0.07, yBtop], [xB, -depth / 2 + 0.07, yBtop],
    [xC - wc / 2 + 0.07, zF, -drop + hc], [xC + wc / 2 - 0.07, zF, -drop + hc]]) {
    canopy(A, X(x), z, 0.02, m, 0.012);
    rod(A, [X(x), -0.012, z], [X(x), y, z], 0.003, m, { shadow: true });
  }
  // the globes, each on a little cup: on the hanging frame's bottom bar, on the back and the front
  // bars, and one hanging under the upright's top bar
  const R = d / 2, cup = 0.02;
  const spots = [
    { x: xC - wc * 0.1, y: -drop + s + cup + R, z: zF, r: R, up: true },
    { x: -length * 0.08, y: yA + s / 2 + cup + R, z: -depth / 2 + s / 2, r: R, up: true },
    { x: length * 0.1, y: yA + s / 2 + cup + R, z: zF, r: R, up: true },
    { x: xB, y: yBtop - s - cup - R * 1.15, z: 0.01, r: R * 1.15, up: false },
  ].slice(0, globes);
  for (const p of spots) {
    A.add(new THREE.SphereGeometry(p.r, 32, 20), glass, at(X(p.x), p.y, p.z), { shadow: false });
    const cy = p.up ? p.y - p.r - cup / 2 + 0.004 : p.y + p.r + cup / 2 - 0.004;
    A.add(new THREE.CylinderGeometry(p.up ? 0.016 : 0.02, p.up ? 0.02 : 0.016, cup, 16), m, at(X(p.x), cy, p.z));
  }
  // light: a glow between each pair of globes
  const pairs = [spots.slice(0, 2), spots.slice(2)].filter((ps) => ps.length);
  for (const ps of pairs) {
    const c = centroid(ps);
    addGlow(g, [X(c.x), Math.min(c.y, -0.2), c.z], lm / pairs.length);
  }
  return finishFixture(g, A, { name: `${finish} frame globe chandelier`, family: "frameGlobes", role: over ? "accent" : "ceiling", finish });
}

// ---------------------------------------------------------------- LED ring

/**
 * LED ring: a thin ring (`d` across, 0.6–1.2 m; `finish` housing, an opal strip underneath) hung level
 * on three cables from one canopy; `drop` from the ceiling to the ring's underside. A wide cone down
 * and a small glow. Role "accent" with `over: true` (over a dining table), else "ceiling".
 */
function ledRing({ d = 0.9, drop = 0.8, finish = "black", over = false, lm = 1800 } = {}) {
  d = clamp(d, 0.4, 1.4);
  drop = clamp(drop, 0.3, 2);
  const m = metal(finish), cord = finishes.black(), strip = opal(1.9);
  const g = new THREE.Group(), A = assembler();
  const w = 0.024, h = 0.026, ro = d / 2, ri = d / 2 - w, y0 = -drop, y1 = -drop + h, seg = Math.round(clamp(d * 140, 64, 160));
  canopy(A, 0, 0, 0.045, m, 0.022);
  A.add(new THREE.CylinderGeometry(ro, ro, h, seg, 1, true), m, at(0, y0 + h / 2, 0)); // outside
  A.add(flipped(new THREE.CylinderGeometry(ri, ri, h, seg, 1, true)), m, at(0, y0 + h / 2, 0)); // inside
  A.add(new THREE.RingGeometry(ri, ro, seg).rotateX(-Math.PI / 2), m, at(0, y1, 0)); // top
  A.add(new THREE.RingGeometry(ro - 0.0025, ro, seg).rotateX(Math.PI / 2), m, at(0, y0, 0)); // the lips either side of the strip
  A.add(new THREE.RingGeometry(ri, ri + 0.0025, seg).rotateX(Math.PI / 2), m, at(0, y0, 0));
  A.add(new THREE.RingGeometry(ri + 0.0025, ro - 0.0025, seg).rotateX(Math.PI / 2), strip, at(0, y0 - 0.0005, 0), { shadow: false });
  // three cables from the canopy, each to a small fitting on the ring
  const rc = ro - w / 2;
  for (let k = 0; k < 3; k++) {
    const a = -Math.PI / 2 + (k * 2 * Math.PI) / 3;
    const x = rc * Math.cos(a), z = rc * Math.sin(a);
    A.add(new THREE.CylinderGeometry(0.0045, 0.0045, 0.012, 12), m, at(x, y1 + 0.006, z));
    rod(A, [0.008 * Math.cos(a), -0.022, 0.008 * Math.sin(a)], [x, y1 + 0.012, z], 0.0013, cord, { seg: 6 });
  }
  addCone(g, [0, y0 - 0.01, 0], [0, -1, 0], lm * 0.85, { angle: 1.15, penumbra: 0.95 });
  addGlow(g, [0, y0 + 0.03, 0], lm * 0.15);
  return finishFixture(g, A, { name: "LED ring pendant", family: "ledRing", role: over ? "accent" : "ceiling", finish });
}

// ---------------------------------------------------------------- bar cluster

/**
 * Bar cluster: `n` (1–5) small metal pendants (`shape` cylinder, cone or mix; `d` across; `finish`
 * outside, white inside, a bulb tip showing) at staggered heights from one canopy, round (in a ring)
 * or linear (in a row, 0.32 m apart); `drop` from the ceiling to the lowest one's rim. A cone down
 * from each (up to three). Role "accent" with `over: true` (over a bar or an island), else "ceiling".
 */
function barCluster({ n = 3, canopy: kind = "round", drop = 0.8, shape = "cylinder", d = 0.11, finish = "brass", light = "warm", over = false, lm = 600 } = {}) {
  n = Math.round(clamp(n, 1, 5));
  d = clamp(d, 0.06, 0.2);
  drop = clamp(drop, 0.35, 2);
  const m = metal(finish), cord = finishes.black(), inner = shadeInside(0.5), bulb = bulbGlow(2.2);
  const g = new THREE.Group(), A = assembler();
  const pos = [];
  if (kind === "linear") {
    const sp = 0.32;
    for (let k = 0; k < n; k++) pos.push([(k - (n - 1) / 2) * sp, 0]);
    A.add(new THREE.BoxGeometry((n - 1) * sp + 0.1, 0.02, 0.06), m, at(0, -0.01, 0));
  } else {
    const rr = n === 1 ? 0 : 0.05 + 0.008 * n;
    for (let k = 0; k < n; k++) {
      const a = Math.PI / 2 + 0.4 + (k * 2 * Math.PI) / n;
      pos.push([rr * Math.cos(a), rr * Math.sin(a)]);
    }
    canopy(A, 0, 0, rr + 0.03, m, 0.02);
  }
  const lift = [0, 0.16, 0.08, 0.12, 0.04];
  const R = d / 2, H = d * 1.35, rc = 0.017;
  const profiles = {
    cylinder: [[R, 0], [R, H], [R, H], [rc, H]],
    cone: [[R, 0], [R * 0.97, 0.008], [0.026, H], [0.026, H], [rc, H]],
  };
  const heads = pos.map(([x, z], k) => {
    const sh = shape === "mix" ? (k % 2 ? "cone" : "cylinder") : profiles[shape] ? shape : "cylinder";
    const y0 = -drop + lift[k];
    shade(A, profiles[sh], m, inner, at(x, y0, z), { seg: 40 });
    A.add(new THREE.CylinderGeometry(rc, rc, 0.05, 20), m, at(x, y0 + H + 0.025, z)); // the collar
    A.add(new THREE.SphereGeometry(Math.min(0.022, R * 0.45), 16, 12), bulb, at(x, y0 + H * 0.42, z), { shadow: false });
    rod(A, [x, -0.02, z], [x, y0 + H + 0.05, z], 0.002, cord);
    return { x, y: y0, z };
  });
  // light: a cone down from each pendant (three at most: more share them)
  const G = Math.min(3, heads.length);
  for (let i = 0; i < G; i++) {
    const part = heads.slice(Math.round((i * heads.length) / G), Math.round(((i + 1) * heads.length) / G));
    const c = centroid(part);
    addCone(g, [c.x, Math.min(...part.map((p) => p.y)) + 0.02, c.z], [0, -1, 0], lm / G, { angle: 0.75, penumbra: 0.8, color: lightColour(light) });
  }
  return finishFixture(g, A, { name: "bar pendant cluster", family: "barCluster", role: over ? "accent" : "ceiling", finish });
}

// ---------------------------------------------------------------- linear pendant

/**
 * Linear pendant: a slim bar `length` long (0.9–2 m; `finish`, an opal diffuser underneath) on two
 * cables with their own small canopies; `drop` from the ceiling to its underside. Two cones down
 * (three from 1.5 m). Role "accent" with `over: true` (over an island or a table), else "ceiling".
 */
function linearPendant({ length = 1.2, drop = 0.8, finish = "black", light = "warm", over = false, lm = 1800 } = {}) {
  length = clamp(length, 0.6, 2.4);
  drop = clamp(drop, 0.25, 2);
  const m = metal(finish), cord = finishes.black(), diffuser = opal(1.8);
  const g = new THREE.Group(), A = assembler();
  const w = 0.036, h = 0.05, y0 = -drop;
  A.add(new THREE.BoxGeometry(length, h, w), m, at(0, y0 + h / 2, 0));
  A.add(new THREE.PlaneGeometry(length - 0.012, w - 0.009).rotateX(Math.PI / 2), diffuser, at(0, y0 - 0.0006, 0), { shadow: false });
  const xa = length / 2 - Math.min(0.15, length * 0.12);
  for (const x of [-xa, xa]) {
    canopy(A, x, 0, 0.028, m, 0.018);
    A.add(new THREE.CylinderGeometry(0.005, 0.005, 0.012, 12), m, at(x, y0 + h + 0.006, 0));
    rod(A, [x, -0.018, 0], [x, y0 + h + 0.012, 0], 0.0015, cord, { seg: 6 });
  }
  const nc = length >= 1.5 ? 3 : 2;
  for (let i = 0; i < nc; i++) {
    const x = (i - (nc - 1) / 2) * (length / nc);
    addCone(g, [x, y0 - 0.01, 0], [0, -1, 0], lm / nc, { angle: 0.95, penumbra: 0.85, color: lightColour(light) });
  }
  return finishFixture(g, A, { name: "linear LED pendant", family: "linearPendant", role: over ? "accent" : "ceiling", finish });
}

// ---------------------------------------------------------------- dome pendant

/**
 * Dome pendant: a metal shade (`shape` dome, cone or drum; `d` across; `finish` outside, white inside)
 * on a collar and a cable, its bulb hidden inside; `drop` from the ceiling to the rim. A cone down and
 * a weak glow from the mouth. Role "accent" with `over: true` (over a table or an island), else "ceiling".
 */
function domePendant({ shape = "dome", d, drop = 0.7, finish = "black", light = "warm", over = false, lm = 800 } = {}) {
  if (!["dome", "cone", "drum"].includes(shape)) shape = "dome";
  d = clamp(d ?? { dome: 0.4, cone: 0.32, drum: 0.35 }[shape], 0.15, 0.7);
  const m = metal(finish), inner = shadeInside(0.45), bulb = bulbGlow(2);
  const g = new THREE.Group(), A = assembler();
  const R = d / 2, rt = 0.032, rc = 0.026;
  let profile, H, angle;
  if (shape === "dome") {
    // a quarter ellipse over a short upright lip
    H = R * 0.66;
    const lip = 0.012, top = Math.acos(rt / R);
    profile = [[R, 0]];
    for (let i = 0; i <= 12; i++) {
      const a = (i / 12) * top;
      profile.push([R * Math.cos(a), lip + (H - lip) * Math.sin(a)]);
    }
    profile.push([rt, H], [rc, H]);
    angle = 0.95;
  } else if (shape === "cone") {
    H = R * 0.95;
    profile = [[R, 0], [R, 0.008], [R, 0.008], [rt, H], [rt, H], [rc, H]];
    angle = 0.8;
  } else {
    H = d * 0.62;
    profile = [[R, 0], [R, H], [R, H], [rc, H]];
    angle = 0.72;
  }
  drop = clamp(drop, H + 0.2, 2);
  const y0 = -drop;
  shade(A, profile, m, inner, at(0, y0, 0), { seg: 56 });
  A.add(new THREE.CylinderGeometry(rc, rc, 0.07, 24), m, at(0, y0 + H + 0.035, 0)); // the collar
  canopy(A, 0, 0, 0.055, m, 0.025);
  rod(A, [0, -0.025, 0], [0, y0 + H + 0.07, 0], 0.0022, finishes.black());
  // the bulb, hidden up in the shade under its socket
  A.add(new THREE.CylinderGeometry(0.017, 0.017, 0.04, 16), m, at(0, y0 + H - 0.02, 0));
  A.add(new THREE.SphereGeometry(0.03, 20, 14), bulb, at(0, y0 + H - 0.07, 0), { shadow: false });
  addCone(g, [0, y0 + H * 0.35, 0], [0, -1, 0], lm * 0.85, { angle, penumbra: 0.75, color: lightColour(light) });
  addGlow(g, [0, y0 + 0.02, 0], lm * 0.12, { color: lightColour(light), reach: 3 });
  return finishFixture(g, A, { name: `${shape} pendant`, family: "domePendant", role: over ? "accent" : "ceiling", finish });
}

/** The family's entries for lighting.js FIXTURES: { make, kind, doc }. */
export const PENDANTS = {
  globePendant: { make: globePendant, kind: "ceiling",
    doc: "globePendant({ d=0.3, drop=0.7, glass:'opal'|'clear', finish, over=false }) opal or clear glass globe on a cable; over: true above a table or island" },
  facetedCluster: { make: facetedCluster, kind: "ceiling",
    doc: "facetedCluster({ n=11, size=0.25, spread=1.0, drop=0.95, finish='black', inside='brass', bulbs=n, seed=7, over=false }) faceted metal shells open on one face, brass inside, each on its own cable at staggered heights with a bare bulb: a statement over a bed, a stairwell, a table (over: true)" },
  discArms: { make: discArms, kind: "ceiling",
    doc: "discArms({ arms=3, span=1.2, d=0.24, drop=0.75, finish='black', rim='brass', over=false }) black rods and crossing arms ending in round LED discs (brass rim, opal underside): bedrooms, living rooms, over a table (over: true)" },
  frameGlobes: { make: frameGlobes, kind: "ceiling",
    doc: "frameGlobes({ length=1.2, depth=0.4, drop=0.8, globes=4, d=0.13, finish='brass', over=false }) interlocking rectangles of square brass bar carrying opal globes: bedrooms, dining (over: true above the table)" },
  ledRing: { make: ledRing, kind: "ceiling",
    doc: "ledRing({ d=0.9, drop=0.8, finish='black', over=false }) a thin LED ring (d 0.6-1.2) on three cables, opal underneath: living rooms, over a dining table (over: true)" },
  barCluster: { make: barCluster, kind: "ceiling",
    doc: "barCluster({ n=3, canopy:'round'|'linear', drop=0.8, shape:'cylinder'|'cone'|'mix', d=0.11, finish='brass', light:'warm'|'neutral', over=false }) small metal pendants at staggered heights from one canopy: over a bar or a kitchen island (over: true)" },
  linearPendant: { make: linearPendant, kind: "ceiling",
    doc: "linearPendant({ length=1.2, drop=0.8, finish='black', light:'warm'|'neutral', over=false }) a slim bar (length 0.9-2) on two cables, opal underneath: over an island or a dining table (over: true)" },
  domePendant: { make: domePendant, kind: "ceiling",
    doc: "domePendant({ shape:'dome'|'cone'|'drum', d=0.4, drop=0.7, finish='black', light:'warm'|'neutral', over=false }) a metal shade, white inside, the bulb hidden: kitchens, dining, over a table or an island (over: true)" },
};
