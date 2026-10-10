// housekit/quantities — the SIA 416 surfaces and volumes of the built scene, its finishes take-off
// and the figures of its habitability check (#47, #48), read the way plan2d.js reads the plan:
// nothing is asked of the scene code, everything comes from what the kit tagged.
//
//   quantities(root, { measure })   the figures (see the shape at the end of quantities()); `measure`
//                                   draws the height maps (gpuMeasure in a page, a fake in the tests)
//   gpuMeasure(renderer)            the height maps drawn with WebGL: the top of a building seen from
//                                   above, the underside of a roof seen from below
//   siaClass(room), flatOf(name)    a room's SIA 416 class and the flat its name gives
//   planGaps(rooms, { pct, abs })   the rooms whose model area is off the area printed on the plan (the
//                                   report's "Écart" and the builder's scene audit, #70)
//
// Units are metres, +x east, +z south, +y up. Walls whose `kind` the scene removed (a basement kept out
// of the camera's framing) are still read from the geometry the kit left in their userData.

import * as THREE from "three";

// --------------------------------------------------------------------------
// Rooms: class, flat, plan check
// --------------------------------------------------------------------------

// SIA 416: SUP utile principale, SUS utile secondaire, SD dégagement, SI installations (#47)
const SUS_USES = new Set(["storage", "cellar", "laundry", "garage", "attic"]);
const TECHNICAL = /local technique|chaufferie|technique|\bpac\b|technik|heizung|heizraum|boiler/i;
export const HABITABLE = new Set(["living", "dining", "kitchen", "kitchen-living", "bedroom", "office"]);

/** A room's SIA 416 class from its use; a `storage` room named as a plant room is SI (#47). */
export function siaClass(room) {
  const use = room.use ?? "";
  if (use === "technical") return "SI";
  // the builder filed plant rooms as storage before the kit had "technical": the name decides, for storage only
  if (use === "storage") return TECHNICAL.test(room.name ?? "") ? "SI" : "SUS";
  if (SUS_USES.has(use)) return "SUS";
  if (use === "hall" || use === "stair") return "SD";
  return "SUP";
}

/** The flat a room's name gives ("Chambre 2 — Appartement 1, étage" → "App. 1"), "Commun" when none. */
export function flatOf(name) {
  const rest = String(name ?? "").split(/\s+[—–]\s+/)[1];
  if (!rest) return null;
  const m = /\b(?:appartement|apartment|appart\.?|app\.?|logement|flat|wohnung|unit)\s*([0-9A-Za-z]+)/i.exec(rest);
  return m ? `App. ${m[1]}` : "Commun";
}

/** A room's short name: its name before " — ". */
export const shortName = (name) => String(name ?? "").split(/\s+[—–]\s+/)[0];

/**
 * The rooms whose area in the model is off the area printed on the plan: more than `pct` (a share) AND
 * more than `abs` m². Each comes back with its gap (m², share) and a likely cause (#70):
 *   "slope"    larger in the model on a storey under the roof: the plan leaves out a low strip (a knee wall)
 *   "outline"  smaller in the model by more than 10 %: the room's outline is likely wrong
 *   "small"    a small room (under 4 m²): a few centimetres of partition change the share a lot
 *   "outline"  otherwise
 * `rooms`: [{ sn, plan, sloped? }] (anything else is passed through).
 */
export function planGaps(rooms, { pct = 0.05, abs = 0 } = {}) {
  const out = [];
  for (const r of rooms) {
    if (!(r.plan > 0)) continue;
    const gap = r.sn - r.plan, share = gap / r.plan;
    if (!(Math.abs(share) > pct && Math.abs(gap) > abs)) continue;
    const cause = gap > 0 && r.sloped ? "slope" : r.plan < 4 && Math.abs(gap) < 0.5 ? "small" : "outline";
    out.push({ ...r, gap, share, cause });
  }
  return out;
}

// --------------------------------------------------------------------------
// Geometry helpers
// --------------------------------------------------------------------------

export function polyArea(p) {
  let a = 0;
  for (let i = 0; i < p.length; i++) {
    const [x1, z1] = p[i], [x2, z2] = p[(i + 1) % p.length];
    a += x1 * z2 - x2 * z1;
  }
  return Math.abs(a) / 2;
}
const signedArea = (p) => p.reduce((a, [x1, z1], i) => { const [x2, z2] = p[(i + 1) % p.length]; return a + x1 * z2 - x2 * z1; }, 0) / 2;

export function inside(p, x, z) {
  let c = false;
  for (let i = 0, j = p.length - 1; i < p.length; j = i++) {
    const [xi, zi] = p[i], [xj, zj] = p[j];
    if ((zi > z) !== (zj > z) && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) c = !c;
  }
  return c;
}
const segDist = (x, z, [ax, az], [bx, bz]) => {
  const dx = bx - ax, dz = bz - az, L2 = dx * dx + dz * dz;
  let t = L2 ? ((x - ax) * dx + (z - az) * dz) / L2 : 0;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(x - ax - t * dx, z - az - t * dz);
};
const polyDist = (p, x, z) => { let d = Infinity; for (let i = 0; i < p.length; i++) d = Math.min(d, segDist(x, z, p[i], p[(i + 1) % p.length])); return d; };
const bbox = (pts) => pts.reduce((b, [x, z]) => [Math.min(b[0], x), Math.min(b[1], z), Math.max(b[2], x), Math.max(b[3], z)], [Infinity, Infinity, -Infinity, -Infinity]);

/** An axis-aligned rectangle's sides [Δx, Δz], a turned one's [first edge, second edge], else null. */
function rectDims(p) {
  if (p.length !== 4) return null;
  const xs = new Set(p.map((q) => q[0].toFixed(4))), zs = new Set(p.map((q) => q[1].toFixed(4)));
  if (xs.size === 2 && zs.size === 2) {
    const b = bbox(p);
    return [b[2] - b[0], b[3] - b[1]];
  }
  const e = (i) => [p[(i + 1) % 4][0] - p[i][0], p[(i + 1) % 4][1] - p[i][1]];
  for (let i = 0; i < 4; i++) {
    const a = e(i), b = e((i + 1) % 4);
    if (Math.abs(a[0] * b[0] + a[1] * b[1]) > 1e-6 * Math.hypot(...a) * Math.hypot(...b)) return null;
  }
  return [Math.hypot(...e(0)), Math.hypot(...e(1))];
}

/** A label point well inside a polygon: the 5 cm grid point farthest from its edges (and that distance). */
function labelPoint(p) {
  const [x0, z0, x1, z1] = bbox(p);
  let best = [p[0][0], p[0][1]], bd = -1;
  for (let x = x0; x <= x1; x += 0.05) for (let z = z0; z <= z1; z += 0.05) {
    if (!inside(p, x, z)) continue;
    const d = polyDist(p, x, z);
    if (d > bd) { bd = d; best = [x, z]; }
  }
  return { at: best, room: bd };
}

/** ∫ min(cap, h(x, z)) along a segment (the mockup's 400 samples). */
function alongArea(a, b, h, cap = Infinity, n = 400) {
  const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
  let s = 0;
  for (let k = 0; k < n; k++) {
    const t = (k + 0.5) / n;
    s += Math.min(cap, h(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t));
  }
  return (s / n) * len;
}

/** Collinear consecutive points out, and the closing duplicate. */
function simplify(p, tol = 1e-3) {
  let q = p.filter((pt, i) => i === 0 || Math.hypot(pt[0] - p[i - 1][0], pt[1] - p[i - 1][1]) > tol);
  if (q.length > 1 && Math.hypot(q[0][0] - q.at(-1)[0], q[0][1] - q.at(-1)[1]) < tol) q.pop();
  let changed = true;
  while (changed && q.length > 3) {
    changed = false;
    for (let i = 0; i < q.length; i++) {
      const a = q[(i + q.length - 1) % q.length], b = q[i], c = q[(i + 1) % q.length];
      const cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]);
      if (Math.abs(cross) < tol * Math.hypot(c[0] - a[0], c[1] - a[1])) { q.splice(i, 1); changed = true; break; }
    }
  }
  return q;
}

/** A polygon clipped by the half-plane a·x + b·z <= c (Sutherland–Hodgman, one edge). */
function clipHalf(poly, a, b, c) {
  const out = [];
  for (let i = 0; i < poly.length; i++) {
    const P = poly[i], Q = poly[(i + 1) % poly.length];
    const fp = a * P[0] + b * P[1] - c, fq = a * Q[0] + b * Q[1] - c;
    if (fp <= 0) out.push(P);
    if ((fp < 0 && fq > 0) || (fp > 0 && fq < 0)) {
      const t = fp / (fp - fq);
      out.push([P[0] + (Q[0] - P[0]) * t, P[1] + (Q[1] - P[1]) * t]);
    }
  }
  return out;
}

/** The share of the square [x0, x0+c] × [z0, z0+c] inside polygon p. */
function cellCover(p, x0, z0, c) {
  let q = clipHalf(p, -1, 0, -x0);
  q = clipHalf(q, 1, 0, x0 + c);
  q = clipHalf(q, 0, -1, -z0);
  q = clipHalf(q, 0, 1, z0 + c);
  return q.length < 3 ? 0 : Math.abs(signedArea(q)) / (c * c);
}

/** The polygon `p` (counter-clockwise or not) with each edge moved inward by d[i]. */
function insetPolygon(p, d) {
  const sgn = signedArea(p) > 0 ? 1 : -1;
  const lines = p.map((a, i) => {
    const b = p[(i + 1) % p.length], L = Math.hypot(b[0] - a[0], b[1] - a[1]);
    const u = [(b[0] - a[0]) / L, (b[1] - a[1]) / L], n = [-u[1] * sgn, u[0] * sgn]; // inward
    return { a: [a[0] + n[0] * d[i], a[1] + n[1] * d[i]], u };
  });
  return lines.map((l, i) => {
    const m = lines[(i + lines.length - 1) % lines.length];
    const den = m.u[0] * l.u[1] - m.u[1] * l.u[0];
    if (Math.abs(den) < 1e-9) return l.a;
    const t = ((l.a[0] - m.a[0]) * l.u[1] - (l.a[1] - m.a[1]) * l.u[0]) / den;
    return [m.a[0] + m.u[0] * t, m.a[1] + m.u[1] * t];
  });
}

const V = new THREE.Vector3();
const world = (obj, x, y, z) => V.set(x, y, z).applyMatrix4(obj.matrixWorld).clone();
const xz = (v) => [v.x, v.z];

function kindOf(o) {
  for (let a = o; a; a = a.parent) if (a.userData?.kind) return a.userData.kind;
  return null;
}
const isWallData = (u) => !!u && Array.isArray(u.from) && Array.isArray(u.to) && typeof u.height === "number"
  && typeof u.thickness === "number" && typeof u.y === "number" && (u.kind === "wall" || (u.kind == null && typeof u.angle === "number" && Array.isArray(u.normal)));

// --------------------------------------------------------------------------
// Reading the scene
// --------------------------------------------------------------------------

/** The kit's exterior walls (whatever grouped them, also those whose kind was removed), in world metres. */
export function readWalls(root) {
  const walls = [], seen = new Set();
  root.traverse((o) => {
    const u = o.userData;
    if (!isWallData(u)) return;
    const P = o.parent ?? o;
    const f = world(P, u.from[0], u.y, u.from[1]), t = world(P, u.to[0], u.y, u.to[1]);
    const key = [f.x, f.z, t.x, t.z, f.y, u.height].map((v) => v.toFixed(2)).join(",");
    if (seen.has(key)) return;
    seen.add(key);
    walls.push({ from: xz(f), to: xz(t), y: f.y, height: u.height, thickness: u.thickness, obj: o });
  });
  return walls;
}

/**
 * Window and door units: tagged, or kind-less units hung in a kind-less kit wall. A unit inside another
 * (a scene wrapping the kit's unit in its own tagged group) and the same unit twice count once.
 */
export function readUnits(root) {
  const out = [], seen = new Set();
  const isUnit = (o) => {
    const u = o.userData;
    if (!u || typeof u.width !== "number") return null;
    if (u.kind === "window" || u.kind === "door") return u.kind;
    return u.kind == null && typeof u.height === "number" && isWallData(o.parent?.userData) ? "window" : null;
  };
  root.traverse((o) => {
    const kind = isUnit(o);
    if (!kind) return;
    for (let a = o.parent; a; a = a.parent) if (isUnit(a)) return;
    const e = o.matrixWorld.elements, u = o.userData;
    const key = [e[12], e[13], e[14], u.width, u.height ?? 2.1].map((v) => v.toFixed(2)).join(",");
    if (seen.has(key)) return;
    seen.add(key);
    out.push({ kind, w: u.width, h: u.height ?? 2.1, x: e[12], y: e[13], z: e[14], obj: o });
  });
  return out;
}

/** The closed outlines the walls of one level make (outer faces, exterior on the right of from → to). */
function chainOutlines(walls) {
  const segs = walls.map((w) => ({ ...w, used: false }));
  const loops = [];
  for (const s0 of segs) {
    if (s0.used) continue;
    const chain = [s0];
    s0.used = true;
    let cur = s0;
    for (let guard = 0; guard < segs.length; guard++) {
      if (Math.hypot(cur.to[0] - s0.from[0], cur.to[1] - s0.from[1]) < 0.06 && chain.length >= 3) {
        loops.push(chain);
        break;
      }
      const next = segs.find((s) => !s.used && Math.hypot(s.from[0] - cur.to[0], s.from[1] - cur.to[1]) < 0.06);
      if (!next) break;
      next.used = true;
      chain.push(next);
      cur = next;
    }
  }
  return loops.map((chain) => {
    const poly = simplify(chain.map((s) => s.from));
    // the wall thickness behind each edge of the outline (the chain's wall lying along it)
    const thick = poly.map((a, i) => {
      const b = poly[(i + 1) % poly.length], m = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
      const w = chain.reduce((best, s) => (segDist(m[0], m[1], s.from, s.to) < segDist(m[0], m[1], best.from, best.to) ? s : best), chain[0]);
      return w.thickness;
    });
    return { polygon: poly, thickness: thick };
  }).filter((l) => polyArea(l.polygon) > 2);
}

/** Walls grouped into buildings: a wall within 0.6 m of another belongs to its building. */
function clusterWalls(walls) {
  const parent = walls.map((_, i) => i);
  const find = (i) => (parent[i] === i ? i : (parent[i] = find(parent[i])));
  const near = (a, b) => Math.min(segDist(a.from[0], a.from[1], b.from, b.to), segDist(a.to[0], a.to[1], b.from, b.to),
    segDist(b.from[0], b.from[1], a.from, a.to), segDist(b.to[0], b.to[1], a.from, a.to)) < 0.6 + Math.max(a.thickness, b.thickness);
  for (let i = 0; i < walls.length; i++) for (let j = i + 1; j < walls.length; j++) if (near(walls[i], walls[j])) parent[find(i)] = find(j);
  const groups = new Map();
  walls.forEach((w, i) => { const k = find(i); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(w); });
  return [...groups.values()];
}

export function readFloorPlans(root) {
  const plans = [];
  root.traverse((o) => { if (o.userData?.kind === "floorPlan") plans.push(o); });
  return plans.map((o) => {
    const u = o.userData;
    const y = world(o, 0, u.y, 0).y;
    const W = (p) => xz(world(o, p[0], u.y, p[1]));
    return {
      obj: o, y, height: u.height ?? 2.5, ceiling: u.ceiling !== false,
      rooms: (u.rooms ?? []).map((r) => ({ ...r, local: r.polygon, polygon: r.polygon.map(W) })),
      partitions: (u.partitions ?? []).map((p) => ({ ...p, from: W(p.from), to: W(p.to) })),
    };
  }).sort((a, b) => a.y - b.y);
}

const FR_NAMES = { neg: (n) => (n === -1 ? "Sous-sol" : `Sous-sol ${-n}`), 0: "Rez-de-chaussée", 1: "1er étage" };
const storeyLabel = (n) => (n < 0 ? FR_NAMES.neg(n) : FR_NAMES[n] ?? `${n}e étage`);

// --------------------------------------------------------------------------
// Height maps (GPU in a page, a fake in the tests)
// --------------------------------------------------------------------------

const DETAIL_KINDS = new Set(["furniture", "window", "door", "interiorDoor", "terrain", "leaves", "tree", "bush", "hedge",
  "car", "planter", "bench", "bicycle", "swingSet", "skirting", "windowSills", "curtainRails", "cornice", "wallTiles", "core", "wood",
  "prop", "fence", "pathway", "groundPatch", "merged"]);

/** Whether a mesh belongs to the envelope a height map reads (not furniture, glass, units, small parts). */
function envelopeMesh(o, { small = true, skip = null } = {}) {
  if (!o.isMesh || o.isInstancedMesh || o.isSkinnedMesh || !o.geometry?.attributes?.position) return false;
  const k = kindOf(o);
  if (DETAIL_KINDS.has(k) || skip?.has(k)) return false;
  const m = Array.isArray(o.material) ? o.material[0] : o.material;
  if (m && ((m.transmission ?? 0) > 0.2 || (m.transparent && (m.opacity ?? 1) < 0.85))) return false;
  if (small && k !== "ceiling") {
    // parts under 2.5 m (panels, roof windows, chimneys, flues, gutters) and thin rods are left out:
    // a roof's height is its surface, not what sits on it (holes they close are filled afterwards)
    // (measured in the mesh's own frame: a ridge rod turned with its building is still thin)
    if (!o.geometry.boundingBox) o.geometry.computeBoundingBox();
    const size = o.geometry.boundingBox.getSize(new THREE.Vector3());
    const e = o.matrixWorld.elements;
    const dims = [size.x * Math.hypot(e[0], e[1], e[2]), size.y * Math.hypot(e[4], e[5], e[6]), size.z * Math.hypot(e[8], e[9], e[10])].sort((a, b) => b - a);
    if (dims[0] < 2.5 || dims[1] < 0.3) return false;
  }
  return true;
}

// the height relative to the camera (modelViewMatrix is composed in double precision on the CPU and the
// camera stands at the heights measured: small numbers, a few micrometres better than world y in float)
const HEIGHT_VS = `
  varying float vZ;
  void main() {
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    vZ = mv.z;
    gl_Position = projectionMatrix * mv;
  }`;
const HEIGHT_FS = `
  varying float vZ;
  void main() { gl_FragColor = vec4(vZ, 1.0, 0.0, 1.0); }`;
const UNDER_SKIP = new Set(["partition", "wall", "perimeter"]);
const LAYER = 29; // the height maps' layer (30: merged meshes, 31: the walk's grids)

/**
 * The height maps drawn with WebGL: `measure({ mode, frame, cell, nx, nz, y0 }, root)` → Float32Array
 * (row j = frame v, column i = frame u, NaN where nothing is hit). mode "top": the highest envelope
 * surface seen from above; "under": the lowest one above y0 seen from below.
 */
export function gpuMeasure(renderer) {
  const mats = new Map();
  const mat = (side) => {
    if (!mats.has(side)) mats.set(side, new THREE.ShaderMaterial({ vertexShader: HEIGHT_VS, fragmentShader: HEIGHT_FS, side }));
    return mats.get(side);
  };
  return async ({ mode, frame, cell, nx, nz, y0 = 0, base = null }, root) => {
    root.updateMatrixWorld(true);
    const objs = [];
    // seen from below, a ceiling or a roof: not the walls (a door's lintel is no ceiling)
    const skip = mode === "under" ? UNDER_SKIP : null;
    root.traverse((o) => { if (envelopeMesh(o, { skip })) objs.push(o); });
    const scene = new THREE.Scene();
    const u = new THREE.Vector3(frame.u[0], 0, frame.u[1]), v = new THREE.Vector3(frame.v[0], 0, frame.v[1]);
    const W = nx * cell, H = nz * cell;
    const centre = new THREE.Vector3(frame.o[0] + u.x * W / 2 + v.x * H / 2, 0, frame.o[1] + u.z * W / 2 + v.z * H / 2);
    const camY = mode === "top" ? (base ?? 0) : y0;
    const cam = new THREE.OrthographicCamera(-W / 2, W / 2, H / 2, -H / 2, mode === "top" ? -1000 : 0.001, 1000);
    // top: looking down, screen up = -v (row 0 = the far v edge); under: looking up from y0, screen up = +v
    const back = new THREE.Vector3(0, mode === "top" ? 1 : -1, 0);
    const up = new THREE.Vector3().crossVectors(back, u);
    centre.y = camY;
    new THREE.Matrix4().makeBasis(u, up, back).setPosition(centre).decompose(cam.position, cam.quaternion, cam.scale);
    cam.updateMatrixWorld(true);
    cam.layers.set(LAYER);
    // each mesh drawn alone in a scene of its own would lose its parents' transforms: draw the
    // meshes where they are, on a layer of their own, with the height material
    const saved = objs.map((o) => ({ o, material: o.material, layers: o.layers.mask, chain: [] }));
    for (const s of saved) {
      const m = Array.isArray(s.o.material) ? s.o.material[0] : s.o.material;
      s.o.material = mat(m?.side ?? THREE.FrontSide);
      s.o.layers.set(LAYER);
      for (let a = s.o; a && a !== root.parent; a = a.parent) if (!a.visible) { s.chain.push(a); a.visible = true; }
    }
    const top = root.parent ?? root;
    const target = new THREE.WebGLRenderTarget(nx, nz, { type: THREE.FloatType, format: THREE.RGBAFormat, depthBuffer: true });
    const prev = { target: renderer.getRenderTarget(), color: renderer.getClearColor(new THREE.Color()), alpha: renderer.getClearAlpha(),
      planes: renderer.clippingPlanes, auto: renderer.shadowMap.autoUpdate, bg: top.background, fog: top.fog, override: top.overrideMaterial };
    const px = new Float32Array(nx * nz * 4);
    try {
      renderer.shadowMap.autoUpdate = false;
      renderer.clippingPlanes = [];
      if (top.isScene) Object.assign(top, { background: null, fog: null, overrideMaterial: null });
      renderer.setRenderTarget(target);
      renderer.setClearColor(0x000000, 0);
      renderer.clear();
      renderer.render(top, cam);
      renderer.readRenderTargetPixels(target, 0, 0, nx, nz, px);
    } finally {
      renderer.setRenderTarget(prev.target);
      renderer.setClearColor(prev.color, prev.alpha);
      renderer.clippingPlanes = prev.planes;
      renderer.shadowMap.autoUpdate = prev.auto;
      if (top.isScene) Object.assign(top, { background: prev.bg, fog: prev.fog, overrideMaterial: prev.override });
      for (const s of saved) {
        s.o.material = s.material;
        s.o.layers.mask = s.layers;
        for (const a of s.chain) a.visible = false;
      }
      target.dispose();
      scene.clear();
    }
    const out = new Float32Array(nx * nz);
    for (let r = 0; r < nz; r++) for (let i = 0; i < nx; i++) {
      const q = (r * nx + i) * 4, j = mode === "top" ? nz - 1 - r : r;
      // view z: y - camY looking down (the camera's back is up), camY - y looking up
      out[j * nx + i] = px[q + 1] > 0.5 ? (mode === "top" ? camY + px[q] : camY - px[q]) : NaN;
    }
    return out;
  };
}

/** A grid in a building's frame: origin o, unit axes u and v, cell size, nx × nz cells. */
function makeGrid(frame, cell, nx, nz, data) {
  const { o, u, v } = frame;
  const toCell = (x, z) => {
    const dx = x - o[0], dz = z - o[1];
    return [(dx * u[0] + dz * u[1]) / cell - 0.5, (dx * v[0] + dz * v[1]) / cell - 0.5];
  };
  const at = (i, j) => (i < 0 || j < 0 || i >= nx || j >= nz ? NaN : data[j * nx + i]);
  return {
    frame, cell, nx, nz, data,
    centre: (i, j) => [o[0] + u[0] * (i + 0.5) * cell + v[0] * (j + 0.5) * cell, o[1] + u[1] * (i + 0.5) * cell + v[1] * (j + 0.5) * cell],
    /** bilinear, ignoring cells with no value */
    sample(x, z) {
      const [fi, fj] = toCell(x, z);
      const i0 = Math.floor(fi), j0 = Math.floor(fj), ti = fi - i0, tj = fj - j0;
      let s = 0, w = 0;
      for (const [di, dj, k] of [[0, 0, (1 - ti) * (1 - tj)], [1, 0, ti * (1 - tj)], [0, 1, (1 - ti) * tj], [1, 1, ti * tj]]) {
        const h = at(i0 + di, j0 + dj);
        if (Number.isFinite(h) && k > 0) { s += h * k; w += k; }
      }
      return w > 1e-9 ? s / w : NaN;
    },
    toCell,
  };
}

/** The cells of a grid whose centre lies inside a polygon (world metres). */
function maskOf(grid, poly) {
  const { nx, nz } = grid, m = new Uint8Array(nx * nz);
  const loc = poly.map(([x, z]) => grid.toCell(x, z));
  const [i0, j0, i1, j1] = bbox(loc);
  for (let j = Math.max(0, Math.floor(j0)); j <= Math.min(nz - 1, Math.ceil(j1)); j++) {
    for (let i = Math.max(0, Math.floor(i0)); i <= Math.min(nx - 1, Math.ceil(i1)); i++) if (inside(loc, i, j)) m[j * nx + i] = 1;
  }
  return m;
}

/** 3 × 3 median, NaN kept where the cell has no value. */
function median3(data, nx, nz) {
  const out = new Float32Array(data.length), buf = [];
  for (let j = 0; j < nz; j++) for (let i = 0; i < nx; i++) {
    const c = data[j * nx + i];
    if (!Number.isFinite(c)) { out[j * nx + i] = c; continue; }
    buf.length = 0;
    for (let dj = -1; dj <= 1; dj++) for (let di = -1; di <= 1; di++) {
      const ii = i + di, jj = j + dj;
      if (ii < 0 || jj < 0 || ii >= nx || jj >= nz) continue;
      const h = data[jj * nx + ii];
      if (Number.isFinite(h)) buf.push(h);
    }
    buf.sort((a, b) => a - b);
    out[j * nx + i] = buf[buf.length >> 1];
  }
  return out;
}

/** Sliding max (or min) over 2r+1 cells along rows then columns (a square element); -Infinity for no value. */
function squareFilter(data, nx, nz, r, max) {
  const better = max ? (a, b) => a >= b : (a, b) => a <= b;
  const run = (get, set, n) => {
    const q = [];
    for (let k = 0; k < n + r; k++) {
      if (k < n) {
        const v = get(k);
        while (q.length && better(v, get(q[q.length - 1]))) q.pop();
        q.push(k);
      }
      const c = k - r;
      if (c < 0) continue;
      while (q[0] < c - r) q.shift();
      set(c, get(q[0]));
    }
  };
  const tmp = new Float32Array(data.length), out = new Float32Array(data.length);
  for (let j = 0; j < nz; j++) run((i) => data[j * nx + i], (i, v) => { tmp[j * nx + i] = v; }, nx);
  for (let i = 0; i < nx; i++) run((j) => tmp[j * nx + i], (j, v) => { out[j * nx + i] = v; }, nz);
  return out;
}

/**
 * Pits closed: a group of cells under `maxArea` m² lower than the surface around it (a closing finds
 * them), filled when it is a hole (a step of more than `cliff` somewhere in it: a roof window's hole seen
 * from above, the sky through it seen from below with the heights negated, cells with no value) or a
 * narrow notch (at most 2 × `notch` wide: the joint of two roof slabs at the ridge). Filled with the plane
 * through the cells around it, or, around a ridge, with the two planes meeting over it. The pits come
 * back ({ cells, empty: cells with no value, centre [i, j], plane [a, b, c]: h = a + b i + c j }).
 * `mask`: the cells a pit may be in (a building's footprint).
 */
function fillPits(data, nx, nz, cell, { cliff = 0.2, depth = 0.0002, maxArea = 4, radius = 1, notch = 0.15, mask = null } = {}) {
  const N = nx * nz;
  const val = new Float32Array(N);
  for (let k = 0; k < N; k++) val[k] = Number.isFinite(data[k]) ? data[k] : -Infinity;
  // a closing removes every pit narrower than the element and leaves planes and ridges as they are
  // (on the grid padded with nothing, so that the edge of the grid is no wall)
  const r = Math.max(1, Math.round(radius / cell));
  const px = nx + 2 * r, pz = nz + 2 * r, pad = new Float32Array(px * pz).fill(-Infinity);
  for (let j = 0; j < nz; j++) pad.set(val.subarray(j * nx, (j + 1) * nx), (j + r) * px + r);
  const pc = squareFilter(squareFilter(pad, px, pz, r, true), px, pz, r, false);
  const closed = new Float32Array(N);
  for (let j = 0; j < nz; j++) closed.set(pc.subarray((j + r) * px + r, (j + r) * px + r + nx), j * nx);
  const cand = new Uint8Array(N);
  for (let k = 0; k < N; k++) if ((!mask || mask[k]) && Number.isFinite(closed[k]) && closed[k] - val[k] > depth) cand[k] = 1;
  const seen = new Uint8Array(N), pits = [];
  const steps = [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1]];
  const near4 = [[1, 0], [-1, 0], [0, 1], [0, -1]];
  for (let k0 = 0; k0 < N; k0++) {
    if (!cand[k0] || seen[k0]) continue;
    const comp = [k0], stack = [k0];
    seen[k0] = 1;
    let border = false;
    while (stack.length) {
      const k = stack.pop(), i = k % nx, j = (k - i) / nx;
      for (const [di, dj] of steps) {
        const ii = i + di, jj = j + dj;
        if (ii < 0 || jj < 0 || ii >= nx || jj >= nz) { border = true; continue; }
        const kk = jj * nx + ii;
        if (cand[kk] && !seen[kk]) { seen[kk] = 1; stack.push(kk); comp.push(kk); }
      }
    }
    if (border || comp.length * cell * cell > maxArea) continue;
    const inPit = new Set(comp);
    // a hole: a step of more than `cliff` between two neighbouring cells of it or of its rim
    let step = false, empty = 0;
    for (const k of comp) {
      if (!Number.isFinite(val[k])) { empty++; step = true; continue; }
      const i = k % nx, j = (k - i) / nx;
      for (const [di, dj] of near4) {
        const ii = i + di, jj = j + dj;
        if (ii < 0 || jj < 0 || ii >= nx || jj >= nz) continue;
        if (Math.abs(val[jj * nx + ii] - val[k]) > cliff) step = true;
      }
    }
    // a notch: no cell farther than `notch` from the surface around it
    let narrow = false;
    if (!step) {
      const reach = Math.ceil(notch / cell) + 1;
      narrow = comp.every((k) => {
        const i = k % nx, j = (k - i) / nx;
        for (let d = 1; d <= reach; d++) {
          for (const [di, dj] of near4) {
            const ii = i + di * d, jj = j + dj * d;
            if (ii >= 0 && jj >= 0 && ii < nx && jj < nz && !inPit.has(jj * nx + ii)) return true;
          }
        }
        return false;
      });
    }
    if (!step && !narrow) continue;
    // the rim: the cells around it, 1 to 3 cells out
    const rim = [], inRim = new Set();
    for (const k of comp) {
      const i = k % nx, j = (k - i) / nx;
      for (let dj = -3; dj <= 3; dj++) for (let di = -3; di <= 3; di++) {
        const ii = i + di, jj = j + dj, kk = jj * nx + ii;
        if (ii < 0 || jj < 0 || ii >= nx || jj >= nz || inPit.has(kk) || inRim.has(kk)) continue;
        if (!Number.isFinite(val[kk])) continue;
        inRim.add(kk);
        rim.push([ii, jj, val[kk]]);
      }
    }
    if (rim.length < 6) continue;
    let ridge = -Infinity;
    let fill = robustPlane(rim);
    if (!fill) {
      // around a ridge: the rim on either side of the pit's long axis, two planes, the lower of the two
      const ci = comp.reduce((a, k) => a + (k % nx), 0) / comp.length, cj = comp.reduce((a, k) => a + Math.floor(k / nx), 0) / comp.length;
      let sii = 0, sjj = 0, sij = 0;
      for (const k of comp) { const di = (k % nx) - ci, dj = Math.floor(k / nx) - cj; sii += di * di; sjj += dj * dj; sij += di * dj; }
      const ang = 0.5 * Math.atan2(2 * sij, sii - sjj), ax = [Math.cos(ang), Math.sin(ang)];
      const side = (p) => (p[0] - ci) * -ax[1] + (p[1] - cj) * ax[0];
      const A = robustPlane(rim.filter((p) => side(p) > 0)), B = robustPlane(rim.filter((p) => side(p) < 0));
      if (A && B) {
        fill = (i, j) => Math.min(A[0] + A[1] * i + A[2] * j, B[0] + B[1] * i + B[2] * j);
        // the ridge: where the two planes meet, over the pit (its highest point)
        const g = [A[1] - B[1], A[2] - B[2]], g2 = g[0] ** 2 + g[1] ** 2, c0 = A[0] - B[0];
        if (g2 > 1e-12) {
          for (const k of comp) {
            const i = k % nx, j = (k - i) / nx, t = (g[0] * i + g[1] * j + c0) / g2;
            const pi = i - g[0] * t, pj = j - g[1] * t;
            ridge = Math.max(ridge, A[0] + A[1] * pi + A[2] * pj);
          }
        }
      }
    } else {
      const P = fill;
      fill = (i, j) => P[0] + P[1] * i + P[2] * j;
    }
    let si = 0, sj = 0;
    const box = [Infinity, Infinity, -Infinity, -Infinity];
    for (const k of comp) {
      const i = k % nx, j = (k - i) / nx;
      data[k] = fill ? fill(i, j) : closed[k];
      si += i; sj += j;
      box[0] = Math.min(box[0], i); box[1] = Math.min(box[1], j); box[2] = Math.max(box[2], i); box[3] = Math.max(box[3], j);
    }
    const plane = robustPlane(rim) ?? fitPlane(rim);
    pits.push({ cells: comp.length, empty, centre: [si / comp.length, sj / comp.length], plane, box, ...(ridge > -Infinity ? { ridge } : {}) });
  }
  return pits;
}

/** A plane through points [i, j, h] that fits them within 1 cm once those under it are dropped, else null. */
function robustPlane(pts) {
  if (pts.length < 6) return null;
  let P = fitPlane(pts), use = pts;
  for (let it = 0; it < 3; it++) {
    const keep = use.filter(([i, j, h]) => h - (P[0] + P[1] * i + P[2] * j) > -0.005);
    if (keep.length < 6 || keep.length === use.length) break;
    use = keep;
    P = fitPlane(use);
  }
  const rms = Math.sqrt(use.reduce((a, [i, j, h]) => a + (h - (P[0] + P[1] * i + P[2] * j)) ** 2, 0) / use.length);
  return rms < 0.01 && use.length > 0.6 * pts.length ? P : null;
}

/** Least-squares plane h = a + b i + c j through [i, j, h] points. */
function fitPlane(pts) {
  let n = 0, si = 0, sj = 0, sh = 0, sii = 0, sjj = 0, sij = 0, sih = 0, sjh = 0;
  for (const [i, j, h] of pts) { n++; si += i; sj += j; sh += h; sii += i * i; sjj += j * j; sij += i * j; sih += i * h; sjh += j * h; }
  const A = [[n, si, sj], [si, sii, sij], [sj, sij, sjj]], B = [sh, sih, sjh];
  const det = (m) => m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0]) + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
  const D = det(A);
  if (Math.abs(D) < 1e-9) return [sh / n, 0, 0];
  const col = (c) => A.map((row, r) => row.map((v, k) => (k === c ? B[r] : v)));
  return [det(col(0)) / D, det(col(1)) / D, det(col(2)) / D];
}

// --------------------------------------------------------------------------
// The figures
// --------------------------------------------------------------------------

/** Façade triangles: the envelope's faces lying in a façade's plane, facing out, as polygons [s, y]. */
function facadeFaces(root, faces, { yMin = -Infinity } = {}) {
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3(), n = new THREE.Vector3();
  const e1 = new THREE.Vector3(), e2 = new THREE.Vector3();
  root.traverse((o) => {
    // (parts under 2.5 m left out: a light well's rim, a sill, a box against the wall)
    if (!envelopeMesh(o)) return;
    const g = o.geometry, pos = g.attributes.position, idx = g.index;
    const tris = idx ? idx.count / 3 : pos.count / 3;
    const bb = new THREE.Box3().setFromObject(o);
    const corners = [[bb.min.x, bb.min.z], [bb.max.x, bb.min.z], [bb.min.x, bb.max.z], [bb.max.x, bb.max.z]];
    const cand = faces.filter((f) => {
      // the façade's plane crosses the mesh's box, and the mesh runs along the façade: the end of a
      // wall meeting it at a corner lies in its plane too, over the façade wall's own face
      const across = corners.map(([x, z]) => (x - f.a[0]) * f.n[0] + (z - f.a[1]) * f.n[1]);
      const along = corners.map(([x, z]) => (x - f.a[0]) * f.u[0] + (z - f.a[1]) * f.u[1]);
      if (!(Math.min(...across) < 0.02 && Math.max(...across) > -0.02)) return false;
      return Math.max(...along) - Math.min(...along) >= 0.5 * (Math.max(...across) - Math.min(...across));
    });
    if (!cand.length) return;
    for (let t = 0; t < tris; t++) {
      const ia = idx ? idx.getX(3 * t) : 3 * t, ib = idx ? idx.getX(3 * t + 1) : 3 * t + 1, ic = idx ? idx.getX(3 * t + 2) : 3 * t + 2;
      a.fromBufferAttribute(pos, ia).applyMatrix4(o.matrixWorld);
      b.fromBufferAttribute(pos, ib).applyMatrix4(o.matrixWorld);
      c.fromBufferAttribute(pos, ic).applyMatrix4(o.matrixWorld);
      n.crossVectors(e1.subVectors(b, a), e2.subVectors(c, a));
      const area2 = n.length();
      if (area2 < 1e-10) continue;
      n.divideScalar(area2);
      for (const f of cand) {
        if (n.x * f.n[0] + n.z * f.n[1] < 0.999) continue;
        const off = (p) => (p.x - f.a[0]) * f.n[0] + (p.z - f.a[1]) * f.n[1];
        if (Math.abs(off(a)) > 0.01 || Math.abs(off(b)) > 0.01 || Math.abs(off(c)) > 0.01) continue;
        const s = (p) => (p.x - f.a[0]) * f.u[0] + (p.z - f.a[1]) * f.u[1];
        let poly = [[s(a), a.y], [s(b), b.y], [s(c), c.y]];
        poly = clipHalf(poly, -1, 0, 0.005);
        poly = clipHalf(poly, 1, 0, f.len + 0.005);
        poly = clipHalf(poly, 0, -1, -yMin);
        if (poly.length < 3) continue;
        f.area += Math.abs(signedArea(poly));
        for (const [sx, sy] of poly) {
          if (sy > f.top.y + 1e-6) f.top = { y: sy, s: sx };
        }
      }
    }
  });
}

/**
 * The quantities of the scene under `root`. `measure` draws the height maps (gpuMeasure(renderer) in a
 * page); without it the roofs are taken flat at the top of the walls and the storeys have their floor
 * plan's height. `openPassages(rooms, y)` (the runtime's) adds the passages between rooms the floor plan
 * does not list.
 */
export async function quantities(root, { measure = null, openPassages = null, cell = 0.02 } = {}) {
  root.updateMatrixWorld(true);
  const messages = [];
  const plans = readFloorPlans(root);
  const walls = readWalls(root);
  const units = readUnits(root);

  // ---- buildings: walls grouped, the one holding the rooms first
  const clusters = clusterWalls(walls).map((ws) => {
    const levels = [];
    for (const w of [...ws].sort((p, q) => p.y - q.y)) {
      let L = levels.find((l) => Math.abs(l.y - w.y) < 0.3);
      if (!L) levels.push((L = { y: w.y, walls: [] }));
      L.walls.push(w);
    }
    for (const L of levels) {
      L.outlines = chainOutlines(L.walls);
      L.tall = Math.max(...L.walls.map((w) => w.height));
    }
    const all = levels.flatMap((l) => l.outlines);
    const biggest = all.sort((p, q) => polyArea(q.polygon) - polyArea(p.polygon))[0] ?? null;
    return { walls: ws, levels, biggest };
  }).filter((c) => c.biggest);
  // each floor plan belongs to the building its rooms stand in
  for (const p of plans) p.at = p.rooms.map((r) => labelPoint(r.polygon).at);
  for (const c of clusters) {
    c.plans = plans.filter((p) => p.at.filter(([x, z]) => inside(c.biggest.polygon, x, z)).length > p.at.length / 2);
    c.rooms = c.plans.reduce((a, p) => a + p.rooms.length, 0);
  }
  clusters.sort((p, q) => q.rooms - p.rooms || polyArea(q.biggest.polygon) - polyArea(p.biggest.polygon));
  const annexPlans = new Set(clusters.slice(1).flatMap((c) => c.plans));
  const mainPlans = plans.filter((p) => !annexPlans.has(p));
  if (!clusters.length && !plans.length) {
    return { version: 1, ok: false, messages: ["Aucun mur extérieur ni plan d'étage dans la maquette."], buildings: [], storeys: [] };
  }

  // the ground floor: the level nearest y = 0 among the floor plans (else the walls)
  const levelYs = mainPlans.length ? mainPlans.map((p) => p.y) : clusters[0].levels.filter((l) => l.tall >= 1.5).map((l) => l.y);
  let g0 = 0;
  levelYs.forEach((y, i) => { if (Math.abs(y) < Math.abs(levelYs[g0])) g0 = i; });
  const groundY = levelYs[g0] ?? 0;

  // ---- per building: its levels (storeys), outlines, footprint, frame
  const buildings = clusters.map((c, bi) => {
    const main = bi === 0;
    const ys = main && mainPlans.length ? mainPlans.map((p) => p.y) : c.levels.filter((l) => l.tall >= 1.5).map((l) => l.y);
    // a level's outline: its own walls', else the nearest level's (an attic of gables, a basement whose walls lost their kind)
    const own = (y) => c.levels.find((l) => Math.abs(l.y - y) < 0.3 && l.outlines.length)?.outlines[0] ?? null;
    const outlineAt = (y) => {
      if (own(y)) return own(y);
      const others = c.levels.filter((l) => l.outlines.length).sort((p, q) => (p.y <= y ? 0 : 1) - (q.y <= y ? 0 : 1) || Math.abs(p.y - y) - Math.abs(q.y - y));
      return others[0]?.outlines[0] ?? c.biggest;
    };
    const storeys = ys.map((y) => ({ y, outline: outlineAt(y) }));
    const ground = storeys.reduce((best, s) => (Math.abs(s.y - groundY) < Math.abs(best.y - groundY) ? s : best), storeys[0]);
    const foot = ground.outline;
    // the frame: along the longest edge of the footprint
    let ang = 0, longest = 0;
    foot.polygon.forEach((a, i) => {
      const b = foot.polygon[(i + 1) % foot.polygon.length], L = Math.hypot(b[0] - a[0], b[1] - a[1]);
      if (L > longest) { longest = L; ang = Math.atan2(b[1] - a[1], b[0] - a[0]); }
    });
    ang = ((ang % (Math.PI / 2)) + Math.PI / 2) % (Math.PI / 2);
    if (ang > Math.PI / 4) ang -= Math.PI / 2;
    const u = [Math.cos(ang), Math.sin(ang)], v = [-Math.sin(ang), Math.cos(ang)];
    const all = storeys.flatMap((s) => s.outline.polygon);
    const loc = all.map(([x, z]) => [x * u[0] + z * u[1], x * v[0] + z * v[1]]);
    const [s0, t0, s1, t1] = bbox(loc);
    // the slab under the lowest storey: a tagged slab of the model under its outline, else assumed (setting)
    const lowest = storeys[0];
    let slab = null;
    root.traverse((o) => {
      const d = o.userData;
      if (!(d?.slab || d?.kind === "slab") || !Array.isArray(d.polygon) || typeof d.thickness !== "number") return;
      const top = world(o.parent ?? o, 0, d.y, 0).y;
      if (Math.abs(top - lowest.y) > 0.12) return;
      const poly = d.polygon.map((p) => xz(world(o.parent ?? o, p[0], d.y, p[1])));
      const [cx, cz] = labelPoint(lowest.outline.polygon).at;
      if (inside(poly, cx, cz) && polyArea(poly) > 0.5 * polyArea(lowest.outline.polygon)) slab = Math.max(slab ?? 0, d.thickness + (top - lowest.y));
    });
    return { main, cluster: c, storeys, foot, u, v, ang, box: [s0, t0, s1, t1], slab };
  });

  // ---- the heights: each building's top seen from above, the storeys without a ceiling seen from below
  const margin = 1.5;
  for (const B of buildings) {
    const [s0, t0, s1, t1] = B.box;
    const o0 = [(s0 - margin) * B.u[0] + (t0 - margin) * B.v[0], (s0 - margin) * B.u[1] + (t0 - margin) * B.v[1]];
    const c = Math.max(cell, Math.max(s1 - s0, t1 - t0, 1) / 1600);
    const nx = Math.ceil((s1 - s0 + 2 * margin) / c), nz = Math.ceil((t1 - t0 + 2 * margin) / c);
    B.frame = { o: o0, u: B.u, v: B.v };
    B.gridSize = { cell: c, nx, nz };
    if (measure) {
      const data = await measure({ mode: "top", frame: B.frame, cell: c, nx, nz, base: Math.max(...B.cluster.walls.map((w) => w.y + w.height)) }, root);
      B.top = makeGrid(B.frame, c, nx, nz, data);
      const pits = fillPits(data, nx, nz, c, { mask: maskOf(B.top, B.foot.polygon) });
      B.ridge = Math.max(-Infinity, ...pits.filter((p) => p.ridge !== undefined).map((p) => p.ridge));
    } else {
      const wallTop = Math.max(...B.cluster.walls.map((w) => w.y + w.height));
      B.top = { sample: () => wallTop, flat: wallTop, frame: B.frame, cell: c, nx, nz, data: null };
      messages.push("Toiture prise plate au sommet des murs (pas de mesure de hauteur).");
    }
  }
  const main = buildings[0] ?? null;

  // ---- the storeys of the floor plans (main building)
  const fr = mainPlans;
  const storeys = [];
  const sloped = [];
  for (let si = 0; si < fr.length; si++) {
    const P = fr[si];
    const n = si - g0;
    const prefix = `${n}`;
    const next = fr[si + 1]?.y ?? null;
    const sMain = main?.storeys.find((s) => Math.abs(s.y - P.y) < 0.3) ?? null;
    // the clear height: the floor plan's, or under the roof when the storey has no ceiling (an attic)
    let under = null, roofWindows = [];
    if (!P.ceiling && measure && main) {
      const { cell: c, nx, nz } = main.gridSize;
      const raw = await measure({ mode: "under", frame: main.frame, cell: c, nx, nz, y0: P.y + 0.1 }, root);
      const neg = new Float32Array(raw.length);
      for (let k = 0; k < raw.length; k++) neg[k] = Number.isFinite(raw[k]) ? -(raw[k] - P.y) : NaN;
      const med = median3(neg, nx, nz);
      // cells outside every room (walls, outdoors) are left as they are; the holes the sky shows through are roof windows
      const pits = fillPits(med, nx, nz, c, { cliff: 0.2, maxArea: 4, mask: maskOf(makeGrid(main.frame, c, nx, nz, med), (sMain ?? main.storeys[0]).outline.polygon) });
      for (let k = 0; k < med.length; k++) med[k] = -med[k];
      under = makeGrid(main.frame, c, nx, nz, med);
      for (const p of pits) {
        if (p.empty < 0.5 * p.cells) continue;
        // the ceiling's slope there (the plane around the hole, heights negated): on the roof = in plan / cos
        const gi = -p.plane[1] / c, gj = -p.plane[2] / c;
        const cos = 1 / Math.hypot(1, gi, gj);
        const grid = makeGrid(main.frame, c, nx, nz, med);
        const at = grid.centre(p.centre[0], p.centre[1]);
        // its opening in plan, measured again at 2 mm around it: where the view goes past the ceiling's plane
        let planArea = p.cells * c * c;
        const fine = 0.002, m = 0.1;
        const [bi0, bj0, bi1, bj1] = p.box;
        const o2 = grid.centre(bi0, bj0), fu = main.frame.u, fv = main.frame.v;
        const lw = (bi1 - bi0 + 1) * c + 2 * m, ld = (bj1 - bj0 + 1) * c + 2 * m;
        const fx = Math.ceil(lw / fine), fz = Math.ceil(ld / fine);
        if (fx * fz < 4e6) {
          const lo = [o2[0] - fu[0] * (c / 2 + m) - fv[0] * (c / 2 + m), o2[1] - fu[1] * (c / 2 + m) - fv[1] * (c / 2 + m)];
          const local = await measure({ mode: "under", frame: { o: lo, u: fu, v: fv }, cell: fine, nx: fx, nz: fz, y0: P.y + 0.1 }, root);
          const plane = (i, j) => {
            // the ceiling's plane at a fine cell, from the coarse grid's plane (heights negated there)
            const w = [lo[0] + fu[0] * (i + 0.5) * fine + fv[0] * (j + 0.5) * fine, lo[1] + fu[1] * (i + 0.5) * fine + fv[1] * (j + 0.5) * fine];
            const [ci, cj] = grid.toCell(w[0], w[1]);
            return P.y - (p.plane[0] + p.plane[1] * ci + p.plane[2] * cj);
          };
          let n = 0;
          for (let j = 0; j < fz; j++) for (let i = 0; i < fx; i++) {
            const h = local[j * fx + i];
            if (!Number.isFinite(h) || h - plane(i, j) > 0.01) n++;
          }
          planArea = n * fine * fine;
        }
        roofWindows.push({ at, planArea, area: planArea / cos });
      }
    }
    const clearAt = under
      ? (x, z) => { const h = under.sample(x, z); return Number.isFinite(h) ? h : P.height; }
      : () => P.height;
    const rooms = P.rooms.map((r, i) => {
      const dims = rectDims(r.local);
      return {
        no: `${prefix}.${String(i + 1).padStart(2, "0")}`, name: shortName(r.name), full: r.name, flat: flatOf(r.name), use: r.use, sia: siaClass(r),
        sn: polyArea(r.polygon), plan: r.planArea ?? null, floor: r.floor ?? "oak",
        formula: dims ? `${dims[0].toFixed(2)} × ${dims[1].toFixed(2)}` : `polygone, ${r.polygon.length} sommets`,
        polygon: r.polygon, label: labelPoint(r.polygon), openings: [], wallsGross: 0, tiles: 0, sloped: !P.ceiling,
      };
    });
    const roomAt = (x, z) => rooms.find((r) => inside(r.polygon, x, z));
    // clear height along a room's walls: sampled 2 cm inside the room
    const edgeArea = (r, i, cap = Infinity) => {
      const p = r.polygon, a = p[i], b = p[(i + 1) % p.length];
      const L = Math.hypot(b[0] - a[0], b[1] - a[1]);
      if (L < 1e-6) return 0;
      if (!under) return Math.min(cap, P.height) * L;
      const sgn = signedArea(p) > 0 ? 1 : -1, nI = [-(b[1] - a[1]) / L * sgn, (b[0] - a[0]) / L * sgn];
      const off = 0.02;
      return alongArea([a[0] + nI[0] * off, a[1] + nI[1] * off], [b[0] + nI[0] * off, b[1] + nI[1] * off], clearAt, cap);
    };
    for (const r of rooms) for (let i = 0; i < r.polygon.length; i++) r.wallsGross += edgeArea(r, i);

    // partitions, with their doors on both faces
    const parts = [];
    for (const p of P.partitions) {
      const dx = p.to[0] - p.from[0], dz = p.to[1] - p.from[1], len = Math.hypot(dx, dz);
      if (len < 1e-6) continue;
      const ux = dx / len, uz = dz / len, nx_ = -uz, nz_ = ux;
      const ph = p.height ?? P.height;
      // under a roof: the clear height just outside its faces (along its centre line the doors' lintels hang lower)
      const off = (p.thickness ?? 0.1) / 2 + 0.03;
      const faceAt = (x, z) => Math.max(clearAt(x + nx_ * off, z + nz_ * off), clearAt(x - nx_ * off, z - nz_ * off));
      const gross = under ? alongArea(p.from, p.to, faceAt, ph) : len * Math.min(ph, P.height);
      let holes = 0;
      for (const o of p.openings ?? []) {
        const oh = o.height ?? 2.04, a = o.width * oh;
        if (a >= 1) holes += a;
        const cx = p.from[0] + ux * (o.offset + o.width / 2), cz = p.from[1] + uz * (o.offset + o.width / 2);
        for (const sgn of [1, -1]) {
          const r = roomAt(cx + sgn * nx_ * (p.thickness / 2 + 0.06), cz + sgn * nz_ * (p.thickness / 2 + 0.06));
          if (r) r.openings.push({ kind: o.door === false ? "passage" : "porte", w: o.width, h: oh, sill: o.sill ?? 0, at: [cx, cz] });
        }
      }
      parts.push({ len, t: p.thickness ?? 0.1, gross, holes, n: (p.openings ?? []).length, from: p.from, to: p.to,
        openings: (p.openings ?? []).map((o) => ({ offset: o.offset, width: o.width, door: o.door !== false })) });
    }
    storeys.push({ key: `s${si}`, index: si, n, label: storeyLabel(n), prefix, y: P.y, next, clear: P.height, ceiling: P.ceiling,
      rooms, parts, ext: [], sMain, under, clearAt, roofWindows, edgeArea, plan: P });
    if (!P.ceiling) sloped.push(storeys.at(-1));
  }
  // the top storey under the roof is "Combles"
  for (const s of storeys) if (!s.ceiling && s === storeys.at(-1)) s.label = "Combles";

  // ---- façades of the main building: its outline at the ground floor, one façade per edge
  const facades = [];
  if (main) {
    const poly = main.foot.polygon, sgn = signedArea(poly) > 0 ? 1 : -1;
    poly.forEach((a, i) => {
      const b = poly[(i + 1) % poly.length], len = Math.hypot(b[0] - a[0], b[1] - a[1]);
      const u = [(b[0] - a[0]) / len, (b[1] - a[1]) / len];
      const n = [u[1] * sgn, -u[0] * sgn]; // outward
      facades.push({ index: i, a, b, u, n, len, thickness: main.foot.thickness[i] ?? 0.3, area: 0, top: { y: -Infinity, s: 0 }, openings: [] });
    });
  }

  // ---- windows and doors: on the façade they sit in, the storey their sill is on, the room behind
  const below = storeys.filter((s) => s.n < 0);
  for (const un of units) {
    if (!main) break;
    let best = null;
    for (const f of facades) {
      const s = (un.x - f.a[0]) * f.u[0] + (un.z - f.a[1]) * f.u[1];
      const d = Math.abs((un.x - f.a[0]) * f.n[0] + (un.z - f.a[1]) * f.n[1]);
      if (s < -0.05 || s > f.len + 0.05 || d > f.thickness + 0.3) continue;
      if (!best || d < best.d) best = { f, s, d };
    }
    if (!best) continue;
    const st = [...storeys].reverse().find((s) => un.y >= s.y - 0.15) ?? null;
    if (storeys.length && !st) continue;
    const sill = st ? un.y - st.y : un.y - groundY;
    const kind = un.kind === "door" ? "porte" : st && st.n < 0 ? "soupirail" : sill < 0.3 ? "porte-fenêtre" : "fenêtre";
    const at = [best.f.a[0] + best.f.u[0] * best.s, best.f.a[1] + best.f.u[1] * best.s];
    const op = { kind, w: un.w, h: un.h, sill, at, edge: best.f.index, storey: st?.index ?? null, along: best.s };
    best.f.openings.push(op);
    if (st) {
      st.ext.push(op);
      const r = st.rooms.find((rr) => inside(rr.polygon, at[0] - best.f.n[0] * (best.f.thickness + 0.08), at[1] - best.f.n[1] * (best.f.thickness + 0.08)));
      if (r) r.openings.push(op);
    }
  }

  // ---- wall tiles (faïence), per room: the band heights the kit recorded
  const tileGroups = [];
  root.traverse((o) => { if (o.userData?.kind === "wallTiles") tileGroups.push(o); });
  for (const s of storeys) {
    for (const r of s.rooms) {
      const t = tileGroups.find((g) => g.userData.room === r.full);
      if (!t) continue;
      const d = t.userData, H = d.height ?? 1.2, FH = d.fullHeight ?? 2.4, full = d.full ?? [];
      const poly = (d.polygon ?? r.polygon).map((p) => xz(world(t, p[0], 0, p[1])));
      const rr = { ...r, polygon: poly };
      let area = 0;
      for (let i = 0; i < poly.length; i++) {
        if (d.edges && !d.edges.includes(i)) continue;
        area += s.edgeArea(rr, i, full.includes(i) ? FH : H);
      }
      let holes = 0;
      for (const o of r.openings) {
        let ei = 0, bd = Infinity;
        for (let i = 0; i < poly.length; i++) { const dd = segDist(o.at[0], o.at[1], poly[i], poly[(i + 1) % poly.length]); if (dd < bd) { bd = dd; ei = i; } }
        if (d.edges && !d.edges.includes(ei)) continue;
        const top = full.includes(ei) ? FH : H;
        const a = Math.max(0, Math.min(top, o.sill + o.h) - Math.max(0, o.sill)) * o.w;
        if (a >= 1) holes += a;
      }
      r.tiles = area - holes;
      r.tileFull = full.length;
      r.tileHeight = H;
      r.tileFullHeight = FH;
      r.tilePartial = !!d.edges;
    }
  }
  for (const s of storeys) for (const r of s.rooms) {
    r.holes = r.openings.reduce((a, o) => a + (o.w * o.h >= 1 ? o.w * o.h : 0), 0);
    r.small = r.openings.filter((o) => o.w * o.h < 1).length;
    r.paint = r.wallsGross - r.holes - r.tiles;
  }

  // ---- façade faces (E02) above the ground floor, and the walls against the ground (E01)
  if (main) facadeFaces(root, facades, { yMin: groundY });
  const atticY = sloped.length ? sloped[0].y : null;
  const outFacades = facades.map((f) => {
    const ops = f.openings.filter((o) => o.storey === null || storeys[o.storey].n >= 0);
    const all = ops.reduce((a, o) => a + o.w * o.h, 0);
    const ded = ops.filter((o) => o.w * o.h >= 1), keep = ops.filter((o) => o.w * o.h < 1);
    const gross = f.area + all;
    const baseH = atticY !== null ? atticY - groundY : null;
    const base = baseH !== null ? Math.min(gross, f.len * baseH) : gross;
    const top = gross - base;
    const peak = f.top.s > 0.2 * f.len && f.top.s < 0.8 * f.len;
    return { index: f.index, a: f.a, b: f.b, n: f.n, len: f.len, baseH, base, top: top > 0.005 ? top : 0, topKind: peak ? "pignon" : "mur sous pente",
      gross, holes: ded.reduce((a, o) => a + o.w * o.h, 0), nHoles: ded.length, nKeep: keep.length, net: gross - ded.reduce((a, o) => a + o.w * o.h, 0) };
  });
  let groundWalls = null;
  if (main && below.length) {
    const perimeter = below.reduce((a, s) => Math.max(a, s.sMain ? perimeterOf(s.sMain.outline.polygon) : 0), 0);
    const h = groundY - below[0].y;
    const ops = below.flatMap((s) => s.ext);
    const ded = ops.filter((o) => o.w * o.h >= 1);
    groundWalls = { perimeter, h, gross: perimeter * h, holes: ded.reduce((a, o) => a + o.w * o.h, 0), nHoles: ded.length, small: ops.length - ded.length };
  }

  // ---- volumes (VB) per building: storey bands, the top band up to the roof's outer surface
  const outBuildings = buildings.map((B, bi) => {
    const bands = [];
    const levels = B.storeys;
    for (let i = 0; i < levels.length; i++) {
      const y0 = levels[i].y, y1 = levels[i + 1]?.y ?? null;
      const poly = levels[i].outline.polygon;
      const area = polyArea(poly);
      const v = bandVolume(B, poly, y0, y1);
      const st = B.main ? storeys.find((s) => Math.abs(s.y - y0) < 0.3) : null;
      const label = st ? (y1 === null ? (st.ceiling ? `${st.label} et toiture` : "Combles et toiture") : st.label)
        : (levels.length === 1 ? "Bâtiment" : (y1 === null ? `Niveau ${i + 1} et toiture` : `Niveau ${i + 1}`));
      bands.push({ label, y0, y1, h: y1 === null ? null : y1 - y0, area, dims: rectDims(poly), v, under: y0 < groundY - 0.3, top: y1 === null,
        meanTop: y1 === null && area > 0 ? v / area : null });
    }
    const foot = B.foot;
    const sb = polyArea(foot.polygon);
    const sp = levels.reduce((a, l) => a + polyArea(l.outline.polygon), 0);
    // detached: the shortest distance to the main building
    let gap = null;
    if (!B.main && main) {
      gap = Infinity;
      for (const p of foot.polygon) gap = Math.min(gap, polyDist(main.foot.polygon, p[0], p[1]));
      for (const p of main.foot.polygon) gap = Math.min(gap, polyDist(foot.polygon, p[0], p[1]));
    }
    // an annex: the net area of its rooms, else inside its walls (the main building's is its storeys')
    const own = B.cluster.plans.flatMap((p) => p.rooms);
    const snInner = B.main && storeys.length ? null : own.length ? own.reduce((a, r) => a + polyArea(r.polygon), 0)
      : levels.reduce((a, l) => a + polyArea(insetPolygon(l.outline.polygon, l.outline.thickness)), 0);
    const classes = !B.main && own.length ? Object.fromEntries(["SUP", "SUS", "SD", "SI"].map((k) => [k, own.filter((r) => siaClass(r) === k).reduce((a, r) => a + polyArea(r.polygon), 0)])) : null;
    return {
      key: `b${bi}`, main: B.main, footprint: foot.polygon, sb, dims: rectDims(foot.polygon), sp, sn: snInner, classes, slab: B.slab,
      rooms: B.main ? null : own.map((r) => ({ name: shortName(r.name), use: r.use, sia: siaClass(r), sn: polyArea(r.polygon) })),
      storeys: levels.map((l) => ({ y: l.y, sp: polyArea(l.outline.polygon) })), bands, gap,
      section: sectionOf(B, levels),
    };
  });

  // ---- balconies and terraces
  const balconies = [], terraces = [];
  const outlines = buildings.map((B) => B.foot.polygon);
  root.traverse((o) => {
    const d = o.userData;
    let poly = null, y = null;
    if ((d?.slab || d?.kind === "slab") && Array.isArray(d.polygon)) {
      y = world(o.parent ?? o, 0, d.y, 0).y;
      poly = d.polygon.map((p) => xz(world(o.parent ?? o, p[0], d.y, p[1])));
    } else if ((d?.balcony || d?.kind === "balcony") && typeof d.width === "number") {
      const hw = d.width / 2;
      y = world(o, 0, 0, 0).y;
      poly = [[-hw, 0], [hw, 0], [hw, d.depth], [-hw, d.depth]].map(([x, z]) => xz(world(o, x, 0, z)));
    } else if (d?.kind === "groundPatch" && !d.lawn && Array.isArray(d.polygon)) {
      y = world(o.parent ?? o, 0, d.y ?? 0, 0).y;
      poly = d.polygon.map((p) => xz(world(o.parent ?? o, p[0], d.y ?? 0, p[1])));
    }
    if (!poly || poly.length < 3) return;
    const area = Math.round(polyArea(poly) * 1e6) / 1e6; // the transforms' float noise off
    if (area < 1) return;
    const [cx, cz] = labelPoint(poly).at;
    if (outlines.some((p) => inside(p, cx, cz))) return; // a building's own floor
    if (Math.abs(y - groundY) < 0.4) { terraces.push({ polygon: poly, area }); return; }
    const st = storeys.find((s) => Math.abs(s.y - y) < 0.4);
    if (!st || area > 150) return;
    // its flat: the rooms it runs along
    const along = new Map(), rooms = [];
    for (const r of st.rooms) {
      let L = 0;
      for (let i = 0; i < r.polygon.length; i++) {
        const a = r.polygon[i], b = r.polygon[(i + 1) % r.polygon.length], len = Math.hypot(b[0] - a[0], b[1] - a[1]);
        for (let k = 0; k < 20; k++) {
          const t = (k + 0.5) / 20, x = a[0] + (b[0] - a[0]) * t, z = a[1] + (b[1] - a[1]) * t;
          if (polyDist(poly, x, z) < 0.8 && !inside(poly, x, z)) L += len / 20;
        }
      }
      if (L > 0.3) { rooms.push(r); if (r.flat) along.set(r.flat, (along.get(r.flat) ?? 0) + L); }
    }
    const flat = [...along.entries()].sort((p, q) => q[1] - p[1])[0]?.[0] ?? null;
    // the façades it runs along
    const sides = facades.filter((f) => {
      let L = 0;
      for (let k = 0; k < 40; k++) { const t = (k + 0.5) / 40, x = f.a[0] + (f.b[0] - f.a[0]) * t, z = f.a[1] + (f.b[1] - f.a[1]) * t; if (polyDist(poly, x, z) < 0.15) L += f.len / 40; }
      return L > 0.5;
    }).map((f) => f.index);
    balconies.push({ polygon: poly, area, storey: st.index, flat, rooms: rooms.map((r) => r.no), sides, shape: poly.length === 6 ? "L" : null });
  });

  // ---- exterior works
  const exterior = readExterior(root, outlines, groundY);

  // ---- open-plan rooms: no wall between them (touching outlines, open passages)
  for (const s of storeys) {
    const links = new Set();
    const key = (a, b) => [a.no, b.no].sort().join("|");
    for (let i = 0; i < s.rooms.length; i++) for (let j = i + 1; j < s.rooms.length; j++) {
      if (sharedLength(s.rooms[i].polygon, s.rooms[j].polygon) >= 0.5) links.add(key(s.rooms[i], s.rooms[j]));
    }
    for (const o of s.parts.flatMap((p) => p.openings.filter((q) => !q.door).map((q) => ({ p, q })))) {
      const { p, q } = o, len = Math.hypot(p.to[0] - p.from[0], p.to[1] - p.from[1]);
      const ux = (p.to[0] - p.from[0]) / len, uz = (p.to[1] - p.from[1]) / len;
      const cx = p.from[0] + ux * (q.offset + q.width / 2), cz = p.from[1] + uz * (q.offset + q.width / 2);
      const A = s.rooms.find((r) => inside(r.polygon, cx - uz * (p.t / 2 + 0.06), cz + ux * (p.t / 2 + 0.06)));
      const B = s.rooms.find((r) => inside(r.polygon, cx + uz * (p.t / 2 + 0.06), cz - ux * (p.t / 2 + 0.06)));
      if (A && B && A !== B) links.add(key(A, B));
    }
    if (openPassages) {
      try {
        const named = s.plan.rooms.map((r, i) => ({ ...r, y: s.plan.y, _i: i }));
        for (const d of openPassages(named, s.plan.y) ?? []) {
          const A = s.rooms[d.minus?._i], B = s.rooms[d.plus?._i];
          if (A && B && A !== B) links.add(key(A, B));
        }
      } catch { /* the passages are a help, never a failure */ }
    }
    s.open = [...links].map((k) => k.split("|"));
  }

  // ---- habitability (RLATC art. 25, 27, 28) and the area under 1.30 m in the attic
  const allRoofWindows = [];
  root.traverse((o) => {
    if (o.userData?.kind !== "roofWindow") return;
    const e = o.matrixWorld.elements;
    allRoofWindows.push({ at: [e[12], e[14]], y: e[13], area: (o.userData.width ?? 0) * (o.userData.height ?? 0), tagged: true });
  });
  const rlatc = [];
  for (const s of storeys) {
    const sky = allRoofWindows.length ? allRoofWindows.filter((w) => w.y > s.y && (s.next === null || w.y < s.next + 1)) : s.roofWindows;
    for (const r of s.rooms) {
      // usable area (≥ 1.30 m), the part ≥ 2.40 m, the volume counted from 1.30 m (5 cm grid, as the mockup)
      let use = 0, high = 0, vol = 0, low = 0;
      const xs = r.polygon.map((q) => q[0]), zs = r.polygon.map((q) => q[1]), c = 0.05;
      const hab = HABITABLE.has(r.use);
      if (hab || s.under) {
        for (let x = Math.min(...xs) + c / 2; x < Math.max(...xs); x += c) for (let z = Math.min(...zs) + c / 2; z < Math.max(...zs); z += c) {
          if (!inside(r.polygon, x, z)) continue;
          const h = s.under ? s.clearAt(x, z) : s.clear;
          if (h >= 1.3) { use += c * c; vol += c * c * h; } else low += c * c;
          if (h >= 2.4) high += c * c;
        }
      }
      r.low = s.under ? low : 0;
      if (!hab) continue;
      const win = r.openings.filter((o) => o.edge !== undefined && o.kind !== "porte" && o.kind !== "soupirail");
      const sk = sky.filter((w) => inside(r.polygon, w.at[0], w.at[1]));
      const aWin = win.reduce((a, o) => a + o.w * o.h, 0), aSky = sk.reduce((a, w) => a + w.area, 0);
      const need = Math.max(use / 8, 1), light = aWin + aSky;
      rlatc.push({ no: r.no, name: r.name, flat: r.flat, attic: !!s.under, clear: s.clear, use, high: use ? high / use : 0, aWin, aSky, light, need,
        ratio: light ? use / light : Infinity, vol, ok25: vol >= 20, ok27: s.under ? (use ? high / use >= 0.5 : false) : s.clear >= 2.4, ok28: light >= need,
        open: (s.open ?? []).filter((pair) => pair.includes(r.no)).map((pair) => pair.find((x) => x !== r.no)) });
    }
  }
  // a room open to others: art. 25 with them (the habitable rooms it is open to, and theirs)
  const adj = new Map(rlatc.map((q) => [q.no, new Set()]));
  for (const q of rlatc) for (const o of q.open) if (adj.has(o)) { adj.get(q.no).add(o); adj.get(o).add(q.no); }
  for (const q of rlatc) {
    const group = new Set([q.no]), todo = [q.no];
    while (todo.length) for (const o of adj.get(todo.pop())) if (!group.has(o)) { group.add(o); todo.push(o); }
    q.group = [...group].sort();
    q.groupVol = rlatc.filter((o) => group.has(o.no)).reduce((a, o) => a + o.vol, 0);
  }

  // ---- the attic: its clear heights
  const attic = sloped.length ? (() => {
    const s = sloped.at(-1);
    let min = Infinity, max = -Infinity;
    for (const r of s.rooms) {
      const p = r.polygon, sgn = signedArea(p) > 0 ? 1 : -1;
      for (let i = 0; i < p.length; i++) {
        const a = p[i], b = p[(i + 1) % p.length], L = Math.hypot(b[0] - a[0], b[1] - a[1]);
        if (L < 1e-6) continue;
        const nI = [-(b[1] - a[1]) / L * sgn, (b[0] - a[0]) / L * sgn];
        for (let k = 0; k <= 40; k++) {
          const t = k / 40, h = s.clearAt(a[0] + (b[0] - a[0]) * t + nI[0] * 0.01, a[1] + (b[1] - a[1]) * t + nI[1] * 0.01);
          min = Math.min(min, h);
        }
      }
      const [x0, z0, x1, z1] = bbox(p);
      for (let x = x0 + 0.05; x < x1; x += 0.1) for (let z = z0 + 0.05; z < z1; z += 0.1) if (inside(p, x, z)) max = Math.max(max, s.clearAt(x, z));
    }
    return { storey: s.index, minClear: min, maxClear: max };
  })() : null;

  const flats = [...new Set(storeys.flatMap((s) => s.rooms.map((r) => r.flat)).filter((f) => f && f !== "Commun"))];
  const thick = storeys.flatMap((s) => s.parts.map((p) => p.t));
  const outStoreys = storeys.map((s) => {
    const sp = s.sMain ? polyArea(s.sMain.outline.polygon) : 0;
    const sn = s.rooms.reduce((a, r) => a + r.sn, 0);
    const by = (k) => s.rooms.filter((r) => r.sia === k).reduce((a, r) => a + r.sn, 0);
    return {
      key: s.key, index: s.index, n: s.n, label: s.label, prefix: s.prefix, y: s.y, clear: s.clear, ceiling: s.ceiling,
      clearRange: s.under ? (attic && attic.storey === s.index ? [attic.minClear, attic.maxClear] : null) : null,
      sp, sn, sc: sp - sn, SUP: by("SUP"), SUS: by("SUS"), SD: by("SD"), SI: by("SI"),
      outline: s.sMain?.outline.polygon ?? null,
      rooms: s.rooms.map(({ no, name, full, flat, use, sia, sn: a, plan, floor, formula, polygon, label, openings, wallsGross, tiles, tileFull, tileHeight, tileFullHeight, tilePartial, holes, small, paint, sloped: sl, low }) =>
        ({ no, name, full, flat, use, sia, sn: a, plan, floor, formula, polygon, label, openings, wallsGross, tiles, tileFull, tileHeight, tileFullHeight, tilePartial, holes, small, paint, sloped: sl, low })),
      parts: s.parts, ext: s.ext, open: s.open ?? [], roofWindows: s.roofWindows,
      walls: s.sMain ? wallsOf(main.cluster, s.y, s.sMain.outline) : [],
    };
  });
  // the rooms off the area printed on the plan (planGaps, shared with the builder's audit, #70)
  const gaps = planGaps(outStoreys.flatMap((st) => st.rooms), { pct: 0.05 }).map(({ no, gap, share, cause }) => ({ no, gap, share, cause }));
  const result = {
    version: 1, ok: true, messages, groundY, planGaps: gaps,
    buildings: outBuildings, storeys: outStoreys, facades: outFacades, groundWalls, balconies, terraces, exterior, rlatc, attic, flats,
    partitionThickness: thick.length ? { light: range(thick.filter((t) => t < 0.2)), mass: range(thick.filter((t) => t >= 0.2)) } : null,
  };
  // for debugging in a page: the height maps (not sent with the figures)
  Object.defineProperty(result, "_grids", { value: { tops: buildings.map((B) => B.top), unders: storeys.map((s) => s.under) }, enumerable: false });
  return result;
}

const range = (xs) => (xs.length ? [Math.min(...xs), Math.max(...xs)] : null);
const perimeterOf = (p) => p.reduce((a, q, i) => a + Math.hypot(p[(i + 1) % p.length][0] - q[0], p[(i + 1) % p.length][1] - q[1]), 0);

/** The length two polygons share along their edges (rooms with no wall between them). */
function sharedLength(A, B) {
  let L = 0;
  for (let i = 0; i < A.length; i++) {
    const a = A[i], b = A[(i + 1) % A.length], len = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (len < 1e-6) continue;
    const n = Math.max(4, Math.ceil(len / 0.05));
    for (let k = 0; k < n; k++) {
      const t = (k + 0.5) / n;
      if (polyDist(B, a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t) < 0.01) L += len / n;
    }
  }
  return L;
}

/** The walls of a storey for its plan (outer face from → to, thickness, the openings along them). */
function wallsOf(cluster, y, outline) {
  const L = cluster.levels.find((l) => Math.abs(l.y - y) < 0.3);
  const ws = L?.walls.length ? L.walls : null;
  if (ws) return ws.map((w) => ({ from: w.from, to: w.to, thickness: w.thickness }));
  // a storey of gables and boxes: the outline it takes
  return outline.polygon.map((a, i) => ({ from: a, to: outline.polygon[(i + 1) % outline.polygon.length], thickness: outline.thickness[i] }));
}

/** The volume of one storey band of a building: its outline, from y0 to the next storey (or the roof). */
function bandVolume(B, poly, y0, y1) {
  const T = B.top;
  if (!T.data) {
    const top = y1 ?? T.flat;
    return polyArea(poly) * Math.max(0, top - y0);
  }
  const { cell: c, nx, nz } = T;
  // the outline in grid cells
  const loc = poly.map(([x, z]) => T.toCell(x, z).map((v) => v + 0.5));
  const [i0, j0, i1, j1] = bbox(loc);
  let vol = 0;
  for (let j = Math.max(0, Math.floor(j0) - 1); j <= Math.min(nz - 1, Math.ceil(j1)); j++) {
    for (let i = Math.max(0, Math.floor(i0) - 1); i <= Math.min(nx - 1, Math.ceil(i1)); i++) {
      const d = polyDist(loc, i + 0.5, j + 0.5);
      const cover = d > 0.75 ? (inside(loc, i + 0.5, j + 0.5) ? 1 : 0) : cellCover(loc, i, j, 1);
      if (!cover) continue;
      let h = T.data[j * nx + i];
      if (!Number.isFinite(h)) continue;
      if (y1 !== null) h = Math.min(h, y1);
      vol += cover * c * c * Math.max(0, h - y0);
    }
  }
  return vol;
}

/** A section across the building, through its middle, perpendicular to the ridge: the top's profile and the levels. */
function sectionOf(B, levels) {
  const T = B.top;
  const [s0, t0, s1, t1] = B.box;
  if (!T.data) return { axis: "u", span: [s0, s1], profile: [[s0, T.flat], [s1, T.flat]], levels: levels.map((l) => l.y), top: T.flat };
  const { nx, nz, cell: c } = T;
  const margin = 1.5;
  // the grid's middle row and column (the grid starts `margin` before the building's box)
  const jm = Math.min(nz - 1, Math.max(0, Math.round(((t1 - t0) / 2 + margin) / c - 0.5)));
  const im = Math.min(nx - 1, Math.max(0, Math.round(((s1 - s0) / 2 + margin) / c - 0.5)));
  const row = Array.from({ length: nx }, (_, i) => T.data[jm * nx + i]);
  const col = Array.from({ length: nz }, (_, j) => T.data[j * nx + im]);
  // how much the top varies over the building itself (not the drop to the ground past its roof)
  const spread = (pts, from, to) => {
    const f = pts.filter((v, k) => Number.isFinite(v) && k >= from && k < to), m = f.reduce((a, v) => a + v, 0) / (f.length || 1);
    return f.reduce((a, v) => a + (v - m) ** 2, 0) / (f.length || 1);
  };
  const k0 = Math.round(margin / c);
  const axis = spread(row, k0, nx - k0) >= spread(col, k0, nz - k0) ? "u" : "v";
  const pts = axis === "u" ? row : col;
  const [a, b] = axis === "u" ? [s0, s1] : [t0, t1];
  const profile = [];
  const step = Math.max(1, Math.round(0.05 / c));
  for (let k = 0; k < pts.length; k += step) profile.push([a - margin + (k + 0.5) * c, Number.isFinite(pts[k]) ? pts[k] : null]);
  const inner = profile.filter(([q, h]) => h !== null && q >= a && q <= b);
  // the ridge: the highest sample, or where two roof planes meet over the joint between them
  const top = Math.max(B.ridge ?? -Infinity, ...inner.map((p) => p[1]));
  return { axis, span: [a, b], profile, levels: levels.map((l) => l.y), top: Number.isFinite(top) ? top : null };
}

/** The exterior works: pools, paved surfaces, lawns, hedges (tagged, or rows of bushes), fences, trees, the plot. */
function readExterior(root, outlines, groundY) {
  const out = { plot: null, paved: { area: 0, n: 0 }, lawn: { area: 0, n: 0 }, hedges: { length: 0, double: 0, n: 0, fromBushes: false }, fences: { length: 0, n: 0 }, trees: 0, pools: [] };
  const bushes = [];
  const off = (p) => outlines.some((poly) => inside(poly, p[0], p[1]));
  root.traverse((o) => {
    const d = o.userData ?? {};
    const P = o.parent ?? o;
    if (d.kind === "plot" && Array.isArray(d.polygon)) out.plot = polyArea(d.polygon.map((p) => xz(world(P, p[0], 0, p[1]))));
    else if ((d.slab || d.kind === "slab") && Array.isArray(d.polygon)) {
      const y = world(P, 0, d.y, 0).y, poly = d.polygon.map((p) => xz(world(P, p[0], d.y, p[1])));
      if (Math.abs(y - groundY) < 0.4 && !off(labelPoint(poly).at) && polyArea(poly) >= 1) { out.paved.area += polyArea(poly); out.paved.n++; }
    } else if (d.kind === "groundPatch" && Array.isArray(d.polygon)) {
      const a = polyArea(d.polygon);
      if (d.lawn) { out.lawn.area += a; out.lawn.n++; }
    } else if (d.kind === "pathway" && Array.isArray(d.points)) {
      let L = 0;
      for (let i = 0; i + 1 < d.points.length; i++) L += Math.hypot(d.points[i + 1][0] - d.points[i][0], d.points[i + 1][1] - d.points[i][1]);
      out.paved.area += L * (d.width ?? 1.2); out.paved.n++;
    } else if (d.kind === "hedge" && typeof d.length === "number") {
      out.hedges.length += d.length; out.hedges.n++;
      if ((d.thickness ?? 0.6) >= 1) out.hedges.double += d.length;
    } else if (d.kind === "fence" && typeof d.length === "number") { out.fences.length += d.length; out.fences.n++; }
    else if (d.kind === "tree") out.trees++;
    else if (d.kind === "bush") { const e = o.matrixWorld.elements; bushes.push({ at: [e[12], e[14]], r: d.radius ?? 0.8 }); }
    if (d.kind === "pool" || (o.isObject3D && /\b(pool|piscine|swimming)\b/i.test(o.name ?? "") && !out.pools.some((p) => p.obj === o) && !hasPoolAncestor(o))) {
      const m = /(\d+(?:[.,]\d+)?)\s*[x×]\s*(\d+(?:[.,]\d+)?)\s*[x×]\s*(\d+(?:[.,]\d+)?)/i.exec(o.name ?? "");
      const dims = m ? [m[1], m[2], m[3]].map((s) => parseFloat(s.replace(",", "."))) : d.length && d.width ? [d.length, d.width, d.depth ?? null] : null;
      out.pools.push({ obj: o, dims });
    }
  });
  // hedges planted as rows of bushes: bushes in a chain, each 0.8 to 2.2 radii from the next
  if (!out.hedges.n && bushes.length >= 6) {
    const rows = bushRows(bushes);
    for (const r of rows) { out.hedges.length += r.length; out.hedges.double += r.double; out.hedges.n++; }
    out.hedges.fromBushes = rows.length > 0;
  }
  out.pools = out.pools.map(({ dims }) => ({ dims }));
  return out;
}

function hasPoolAncestor(o) {
  for (let a = o.parent; a; a = a.parent) if (/\b(pool|piscine|swimming)\b/i.test(a.name ?? "") || a.userData?.kind === "pool") return true;
  return false;
}

/**
 * Hedges planted as rows of bushes: two bushes closer than 1.2 radii are a pair of a double row (taken
 * as one point between them), then chains of points at most 2.2 radii apart, 5 points at least; the
 * length of a chain is its links plus one spacing.
 */
function bushRows(bushes) {
  const pts = [], paired = new Set(), pairs = [];
  for (let i = 0; i < bushes.length; i++) for (let j = i + 1; j < bushes.length; j++) {
    const d = Math.hypot(bushes[i].at[0] - bushes[j].at[0], bushes[i].at[1] - bushes[j].at[1]);
    if (d < 1.2 * (bushes[i].r + bushes[j].r) / 2) pairs.push([d, i, j]);
  }
  pairs.sort((a, b) => a[0] - b[0]);
  for (const [, i, j] of pairs) {
    if (paired.has(i) || paired.has(j)) continue;
    paired.add(i); paired.add(j);
    pts.push({ at: [(bushes[i].at[0] + bushes[j].at[0]) / 2, (bushes[i].at[1] + bushes[j].at[1]) / 2], r: (bushes[i].r + bushes[j].r) / 2, double: true });
  }
  bushes.forEach((b, i) => { if (!paired.has(i)) pts.push({ ...b, double: false }); });
  const n = pts.length, parent = pts.map((_, i) => i), links = [];
  const find = (i) => (parent[i] === i ? i : (parent[i] = find(parent[i])));
  for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
    const d = Math.hypot(pts[i].at[0] - pts[j].at[0], pts[i].at[1] - pts[j].at[1]);
    if (d <= 2.2 * (pts[i].r + pts[j].r) / 2) links.push([d, i, j]);
  }
  links.sort((a, b) => a[0] - b[0]);
  const tree = [];
  for (const l of links) { const a = find(l[1]), b = find(l[2]); if (a !== b) { parent[a] = b; tree.push(l); } }
  const comps = new Map();
  for (let i = 0; i < n; i++) { const k = find(i); if (!comps.has(k)) comps.set(k, { members: [], links: 0 }); comps.get(k).members.push(i); }
  for (const [d, i] of tree) comps.get(find(i)).links += d;
  const rows = [];
  for (const { members, links: L } of comps.values()) {
    if (members.length < 5) continue;
    const spacing = L / (members.length - 1), length = L + spacing;
    const share = members.filter((i) => pts[i].double).length / members.length;
    rows.push({ length, double: share * length });
  }
  return rows;
}

export const _internals = { fillPits, squareFilter, robustPlane, fitPlane, bushRows, insetPolygon, cellCover };

export default { quantities, gpuMeasure, siaClass, flatOf, planGaps };
