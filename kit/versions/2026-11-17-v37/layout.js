// housekit/layout — the interior measured, piece by piece, for the builder (#67, #68) and the rooms
// against the areas printed on the plan (#70). Deterministic geometry on the tagged scene, no GPU:
//
//   layout(root)              { rooms, pieces, findings }: every furniture piece (its room, size, height
//                             above the floor, nearest wall) and what is wrong with the layout
//   auditLines(L, { max })    the findings as the scene audit's lines (the builder reads them after every render)
//
// The findings, each with its numbers:
//   plan     a room more than 5 % AND 0.5 m² off the area printed on the plan, with the likely cause;
//            the rooms with no printed area recorded
//   window   a piece taller than a window's sill in front of it (within 30 cm, over a third of its
//            width); outside, a plant or a prop within 1 m of a window below its head or of a door
//   size     a piece outside the usual sizes of its kind, or too big for its room
//   passage  no 80 cm passage between a room's doors; less than 60 cm beside a bed or in front of a
//            sofa, 90 cm in front of a wardrobe, a kitchen run or an appliance; a door swinging into a piece
//   support  floating or sunk by more than 1 cm, overhanging its support, two pieces overlapping by more
//            than 1 cm, a wall-hung piece more than 2 cm off the wall

import * as THREE from "three";
import { planGaps, readFloorPlans, readUnits, readWalls, inside, polyArea } from "./quantities.js";

export const RULES = {
  gapShare: 0.05, gapArea: 0.5, // the plan check (#70)
  window: 0.3, outside: 1.0, // in front of a window: inside, outside
  passage: 0.8, bedSide: 0.6, sofaFront: 0.6, storageFront: 0.9,
  // a wall-hung piece: the kit leaves 2 cm behind it (onWall's gap) and a model's box adds a few: "fixed"
  // within 5 cm (a board in the air, a frame across the room, are what this catches)
  float: 0.01, sink: 0.01, overlap: 0.01, fixed: 0.05, supported: 2 / 3,
};

const f2 = (v) => (Math.round(v * 100) / 100).toFixed(2);
const cm = (v) => (Math.abs(v) < 0.05 ? `${(Math.round(v * 1000) / 10).toFixed(1)} cm` : `${Math.round(v * 100)} cm`);
const pt = ([x, z]) => `[${f2(x)}, ${f2(z)}]`;

// --------------------------------------------------------------------------
// What a piece is
// --------------------------------------------------------------------------

const TYPES = [
  ["rug", /\brug\b|carpet|tapis|bath ?mat/],
  ["lamp", /lamp|light|pendant|sconce|chandelier|luminaire|lustre/],
  ["curtain", /curtain|rideau|drape/],
  ["nightstand", /nightstand|bedside|chevet/],
  ["bed", /\bbeds?\b|^bed|bunk|\blit\b/],
  ["sofa", /sofa|couch|canap/],
  ["armchair", /armchair|fauteuil/],
  ["chair", /chair|stool|tabouret|chaise|pouf|ottoman/],
  ["dining", /diningset|dining.?table|table à manger/],
  ["desk", /desk|bureau/],
  ["coffee", /coffee|side.?table|low.?table|table basse/],
  ["table", /table/],
  ["wardrobe", /wardrobe|closet|armoire|dressing|penderie/],
  ["kitchen", /kitchen|island|cuisine|îlot/],
  ["appliance", /fridge|refrigerator|washer|dryer|dishwasher|oven|freezer|frigo|lave-/],
  ["wc", /\bwc\b|toilet/],
  ["basin", /basin|vanity|lavabo|\bsink\b/],
  ["bathtub", /bathtub|\btub\b|baignoire/],
  ["shower", /shower|douche/],
  ["towels", /towel.?rail|towelrail/],
  ["hooks", /hooks?\b|coat/],
  ["mirror", /mirror|miroir/],
  ["decor", /photo.?frame|picture.?frame|book|vase|cup|plate|bowl|bottle|toaster|teapot|clock|candle|glass|basket|stack/],
  ["art", /\bart\b|frames?\b|painting|poster|print|gallery/],
  ["radiator", /radiator/],
  ["shelf", /\bshelf\b|étagère|etagere|wall.?shelf/],
  ["cabinet", /cabinet|sideboard|shoe|cupboard|commode|dresser|bookcase|shelving|cube.?shelf|storage|buffet/],
  ["plant", /plant|ficus|ivy|palm|herbs?\b|flower|vase.*branch/],
];
export function pieceType(name) {
  const n = String(name ?? "").replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase();
  for (const [t, re] of TYPES) if (re.test(n)) return t;
  return "decor";
}
// hung on a wall rather than standing: checked for being fixed, not for resting on something
const WALL_HUNG = new Set(["mirror", "art", "towels", "hooks", "radiator", "shelf", "basin"]);
// what one walks around
const LOW = new Set(["coffee", "chair", "rug"]);

// usual sizes as placed [min, max] of width (along its front), depth, height; null: not checked
const SIZES = {
  bed: { w: [0.8, 2.6], d: [1.85, 2.7], h: [0.25, 1.5], say: "0.9 / 1.4 / 1.6 / 1.8 × 2.0 m (with the duvet over the sides)" },
  sofa: { w: [1.2, 3.8], d: [0.7, 1.2], h: [0.55, 1.1], say: "1.2–3.6 m wide, 0.85–1.0 m deep" },
  armchair: { w: [0.55, 1.15], d: [0.55, 1.15], h: [0.6, 1.2], say: "0.6–1.0 m square" },
  chair: { w: [0.3, 0.75], d: [0.3, 0.75], h: [0.35, 1.15], say: "0.4–0.6 m square, 0.8–1.0 m high" },
  dining: { h: [0.68, 0.8], say: "0.72–0.76 m high" },
  desk: { d: [0.45, 0.95], h: [0.68, 0.8], say: "0.6–0.8 m deep, 0.72–0.76 m high" },
  coffee: { h: [0.25, 0.55], say: "0.3–0.5 m high" },
  wardrobe: { d: [0.45, 0.72], h: [1.6, 2.7], say: "0.55–0.65 m deep, 2.0–2.4 m high" },
  nightstand: { w: [0.3, 0.75], h: [0.35, 0.75], say: "0.4–0.6 m wide, 0.45–0.6 m high" },
  bathtub: { w: [1.4, 1.95], d: [0.65, 0.95], say: "1.5–1.9 × 0.7–0.9 m" },
};

// --------------------------------------------------------------------------
// Reading the scene
// --------------------------------------------------------------------------

function kindOf(o) {
  for (let a = o; a; a = a.parent) if (a.userData?.kind) return a.userData.kind;
  return null;
}

/** A piece's box in its own frame (meshes' geometry boxes brought into it), world-scaled. */
function localBox(o) {
  const inv = new THREE.Matrix4().copy(o.matrixWorld).invert();
  const box = new THREE.Box3(), tmp = new THREE.Box3(), m = new THREE.Matrix4();
  o.traverse((c) => {
    if (!c.isMesh || !c.geometry?.attributes?.position) return;
    if (!c.geometry.boundingBox) c.geometry.computeBoundingBox();
    tmp.copy(c.geometry.boundingBox).applyMatrix4(m.multiplyMatrices(inv, c.matrixWorld));
    box.union(tmp);
  });
  return box;
}

/** The furniture pieces (top-level kind "furniture"), as oriented boxes in world metres. */
export function readPieces(root) {
  root.updateMatrixWorld(true);
  const out = [];
  root.traverse((o) => {
    const u = o.userData;
    if (u?.kind !== "furniture") return;
    for (let a = o.parent; a; a = a.parent) if (a.userData?.kind === "furniture") return;
    const lb = localBox(o);
    if (lb.isEmpty()) return;
    const e = o.matrixWorld.elements;
    const sx = Math.hypot(e[0], e[1], e[2]), sz = Math.hypot(e[8], e[9], e[10]);
    const ux = [e[0] / sx, e[2] / sx], uz = [e[8] / sz, e[10] / sz]; // the piece's x (width) and z (front) in plan
    const c = new THREE.Vector3((lb.min.x + lb.max.x) / 2, 0, (lb.min.z + lb.max.z) / 2).applyMatrix4(o.matrixWorld);
    const w = (lb.max.x - lb.min.x) * sx, d = (lb.max.z - lb.min.z) * sz;
    const wb = new THREE.Box3().setFromObject(o);
    const at = [c.x, c.z];
    const corner = (a, b) => [at[0] + ux[0] * a + uz[0] * b, at[1] + ux[1] * a + uz[1] * b];
    const name = String(u.name ?? o.name ?? "piece");
    const type = pieceType(name);
    out.push({
      obj: o, name, type, at, w, d, h: wb.max.y - wb.min.y, y0: wb.min.y, y1: wb.max.y,
      ux, uz, rot: Math.round((Math.atan2(uz[0], uz[1]) * 180) / Math.PI),
      corners: [corner(-w / 2, -d / 2), corner(w / 2, -d / 2), corner(w / 2, d / 2), corner(-w / 2, d / 2)],
      hang: !!u.hang || type === "lamp", flat: !!u.flat || type === "rug", wallHung: WALL_HUNG.has(type),
    });
  });
  return out;
}

// --------------------------------------------------------------------------
// Plane geometry
// --------------------------------------------------------------------------

const segDist = ([x, z], [ax, az], [bx, bz]) => {
  const dx = bx - ax, dz = bz - az, L2 = dx * dx + dz * dz || 1;
  const t = Math.max(0, Math.min(1, ((x - ax) * dx + (z - az) * dz) / L2));
  return Math.hypot(x - ax - t * dx, z - az - t * dz);
};
const polyDist = (p, q) => { let d = Infinity; for (let i = 0; i < p.length; i++) d = Math.min(d, segDist(q, p[i], p[(i + 1) % p.length])); return d; };

/** Two rooms with no wall between them: their outlines run along each other over 50 cm or more. */
function touching(A, B) {
  let L = 0;
  for (let i = 0; i < A.length; i++) {
    const a = A[i], b = A[(i + 1) % A.length], len = Math.hypot(b[0] - a[0], b[1] - a[1]);
    for (let k = 0; k < 10; k++) {
      const t = (k + 0.5) / 10;
      if (polyDist(B, [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]) < 0.01) L += len / 10;
    }
  }
  return L >= 0.5;
}

/** Overlap depth of two convex polygons (separating axes), 0 when apart. */
function overlapDepth(A, B) {
  let depth = Infinity;
  for (const P of [A, B]) {
    for (let i = 0; i < P.length; i++) {
      const a = P[i], b = P[(i + 1) % P.length], L = Math.hypot(b[0] - a[0], b[1] - a[1]);
      if (L < 1e-9) continue;
      const n = [-(b[1] - a[1]) / L, (b[0] - a[0]) / L];
      const pa = A.map((q) => q[0] * n[0] + q[1] * n[1]), pb = B.map((q) => q[0] * n[0] + q[1] * n[1]);
      const o = Math.min(Math.max(...pa), Math.max(...pb)) - Math.max(Math.min(...pa), Math.min(...pb));
      if (o <= 0) return 0;
      depth = Math.min(depth, o);
    }
  }
  return depth;
}

/** Distance along a ray from `o` in direction `u` to the first edge of the polygons (Infinity if none). */
function rayHit(o, u, polys, max = 5) {
  let best = max;
  for (const p of polys) {
    for (let i = 0; i < p.length; i++) {
      const a = p[i], b = p[(i + 1) % p.length];
      const ex = b[0] - a[0], ez = b[1] - a[1];
      const den = u[0] * ez - u[1] * ex;
      if (Math.abs(den) < 1e-12) continue;
      const t = ((a[0] - o[0]) * ez - (a[1] - o[1]) * ex) / den;
      const s = ((a[0] - o[0]) * u[1] - (a[1] - o[1]) * u[0]) / den;
      if (t > 1e-6 && t < best && s >= 0 && s <= 1) best = t;
    }
  }
  return best;
}

/** The free distance in front of one face of a piece: rays from points along the face, a low percentile. */
function faceClearance(p, side, obstacles, roomPoly, { skipHead = 0 } = {}) {
  // side: "front" (+z), "left" (-x), "right" (+x), "foot" (+z for a bed)
  const dir = side === "left" ? [-p.ux[0], -p.ux[1]] : side === "right" ? p.ux : p.uz;
  const along = side === "left" || side === "right" ? p.uz : p.ux;
  const half = side === "left" || side === "right" ? p.d / 2 : p.w / 2;
  const off = side === "left" || side === "right" ? p.w / 2 : p.d / 2;
  const from = side === "left" || side === "right" ? -half + skipHead : -half;
  const out = [];
  for (let k = 0; k <= 8; k++) {
    const s = from + ((half - from) * (k + 0.5)) / 9;
    const o = [p.at[0] + dir[0] * (off + 0.005) + along[0] * s, p.at[1] + dir[1] * (off + 0.005) + along[1] * s];
    out.push(rayHit(o, dir, [roomPoly, ...obstacles.filter((q) => q !== p).map((q) => q.corners)]));
  }
  out.sort((a, b) => a - b);
  return out[2]; // the third shortest of nine: a single leg or a corner does not decide
}

// --------------------------------------------------------------------------
// The layout
// --------------------------------------------------------------------------

/** Every piece measured, and the findings (see the module's head). */
export function layout(root, { rules = RULES } = {}) {
  root.updateMatrixWorld(true);
  const plans = readFloorPlans(root);
  const rooms = plans.flatMap((P, si) => P.rooms.map((r, i) => ({
    ...r, storey: si, y: P.y, height: P.height, ceiling: P.ceiling, no: `${si}.${i + 1}`,
    sn: polyArea(r.polygon), plan: r.planArea ?? null, sloped: !P.ceiling,
  })));
  const pieces = readPieces(root);
  const findings = [];
  const add = (kind, text, extra = {}) => findings.push({ kind, text, ...extra });

  // ---- each piece's room: the storey below its base, the room its footprint centre stands in
  for (const p of pieces) {
    // the storey it stands on: the highest floor under its base that covers where it stands (a garage's
    // raised floor plan beside the house is not the floor under a plate on the dining table)
    const covers = (P) => P.rooms.some((r) => polyDist(r.polygon, p.at) < 0.6 || inside(r.polygon, p.at[0], p.at[1]));
    const P = plans.filter((x) => x.y <= p.y0 + 0.25 && covers(x)).sort((a, b) => b.y - a.y)[0];
    if (!P) continue;
    p.storeyY = P.y;
    p.room = rooms.find((x) => x.y === P.y && inside(x.polygon, p.at[0], p.at[1]))
      ?? (p.wallHung || p.hang ? rooms.find((x) => x.y === P.y && polyDist(x.polygon, p.at) < 0.15) : null) ?? null;
    if (!p.room && !p.hang) {
      const near = rooms.filter((x) => x.y === P.y).sort((a, b) => polyDist(a.polygon, p.at) - polyDist(b.polygon, p.at))[0];
      add("support", `"${p.name}" at ${pt(p.at)} stands in no room (in a wall or outside${near ? `, ${cm(polyDist(near.polygon, p.at))} from "${near.name}"` : ""}): move it into its room`, { room: near?.name, piece: p.name });
    }
    if (p.room) {
      p.bottom = p.y0 - (p.room.y + 0.015);
      let best = null;
      const poly = p.room.polygon;
      for (let i = 0; i < poly.length; i++) {
        const a = poly[i], b = poly[(i + 1) % poly.length];
        const dmin = Math.min(...p.corners.map((c) => segDist(c, a, b)));
        if (!best || dmin < best.d) best = { d: dmin, edge: i };
      }
      p.wall = best;
      // through a wall: how far its footprint reaches past its room's outline (not into a room open to it)
      if (!p.flat && !p.hang && !p.wallHung && p.type !== "curtain") {
        const open = rooms.filter((x) => x !== p.room && x.y === p.room.y && touching(x.polygon, poly));
        const past = Math.max(0, ...p.corners.filter((c) => !inside(poly, c[0], c[1]) && !open.some((x) => inside(x.polygon, c[0], c[1]))).map((c) => polyDist(poly, c)));
        if (past > 0.03) add("support", `"${p.name}" in "${p.room.name}" goes ${cm(past)} through its wall or partition: move it into the room`, { room: p.room.name, piece: p.name });
      }
    }
  }
  const byRoom = new Map(rooms.map((r) => [r, pieces.filter((p) => p.room === r)]));

  // ---- the plan check (#70): the rooms off the areas printed on the plan, the rooms without one
  for (const g of planGaps(rooms, { pct: rules.gapShare, abs: rules.gapArea })) {
    const sign = g.gap > 0 ? "+" : "−";
    const why = g.cause === "slope"
      ? "it lies under the roof: the plan likely stops at a low knee wall (mur de pied) the model leaves out. Look at the sheet; if it draws one, model it as a partition and end the room's polygon there"
      : g.gap < 0 ? "its outline is likely wrong: re-read the plan's dimensions and redraw the room's polygon (and the partitions around it)"
        : "re-read the plan's dimensions: its outline is likely too large, or a wall the plan draws is missing";
    add("plan", `room area off the plan: "${g.name}" measures ${f2(g.sn)} m², the plan prints ${f2(g.plan)} m² (${sign}${(Math.abs(g.share) * 100).toFixed(1)} %, ${sign}${f2(Math.abs(g.gap))} m²): ${why}`, { room: g.name });
  }
  const unmarked = rooms.filter((r) => !r.plan && r.use !== "stair");
  if (unmarked.length && rooms.some((r) => r.plan)) {
    add("plan", `no area from the plan recorded for ${unmarked.length} room${unmarked.length > 1 ? "s" : ""} (${unmarked.slice(0, 6).map((r) => `"${r.name}"`).join(", ")}${unmarked.length > 6 ? "…" : ""}): give each room the area the plan prints (area: m²), where it prints one, so its gap can be checked`);
  }

  // ---- windows and doors: what stands in front of them
  const walls = readWalls(root), units = readUnits(root);
  for (const un of units) {
    const e = un.obj.matrixWorld.elements, sz = Math.hypot(e[8], e[9], e[10]) || 1;
    const out = [e[8] / sz, e[10] / sz], along = [-out[1], out[0]];
    const wall = walls.reduce((best, w) => {
      const d = segDist([un.x, un.z], w.from, w.to);
      return d < (best?.d ?? 0.6) ? { w, d } : best;
    }, null);
    const thick = wall?.w.thickness ?? 0.3;
    const inner = thick - 0.12; // units sit 12 cm behind the outer face
    const span = (s0, s1, o0, o1) => [
      [un.x + along[0] * s0 + out[0] * o0, un.z + along[1] * s0 + out[1] * o0],
      [un.x + along[0] * s1 + out[0] * o0, un.z + along[1] * s1 + out[1] * o0],
      [un.x + along[0] * s1 + out[0] * o1, un.z + along[1] * s1 + out[1] * o1],
      [un.x + along[0] * s0 + out[0] * o1, un.z + along[1] * s0 + out[1] * o1],
    ];
    const hw = un.w / 2;
    // inside: within 30 cm of the inner face, over the window's width
    const strip = span(-hw, hw, -inner - rules.window, -inner + 0.02);
    const room = rooms.find((r) => inside(r.polygon, un.x - out[0] * (inner + 0.2), un.z - out[1] * (inner + 0.2)));
    if (room) {
      for (const p of byRoom.get(room) ?? []) {
        if (p.hang || p.flat || p.type === "curtain" || p.type === "radiator" || p.wallHung) continue;
        if (p.y1 <= un.y + 0.02) continue; // lower than the sill
        if (p.y0 >= un.y + un.h) continue; // above the window
        const cover = coverAlong(p.corners, strip, along, [un.x, un.z]);
        if (cover > un.w / 3) {
          add("window", `"${p.name}" in "${room.name}" stands in front of the ${un.kind === "door" ? "door" : "window"} at ${pt([un.x, un.z])} (${f2(un.w)} m wide, sill ${f2(un.y - room.y)} m): it covers ${cm(cover)} of it within ${cm(rules.window)} of the wall, ${f2(p.y1 - room.y)} m high: move it along the wall`, { room: room.name, piece: p.name });
        }
      }
    }
  }
  // outside: plants and props within 1 m of a window below its head, or of a door
  const outsiders = [];
  root.traverse((o) => {
    const k = o.userData?.kind;
    if (!["tree", "bush", "hedge", "planter", "car", "bench", "bicycle", "prop", "swingSet"].includes(k)) return;
    for (let a = o.parent; a; a = a.parent) if (["tree", "bush", "hedge", "planter", "car", "prop"].includes(a.userData?.kind)) return;
    const e = o.matrixWorld.elements;
    let poly;
    if (k === "tree") { const r = 0.3; poly = [[e[12] - r, e[14] - r], [e[12] + r, e[14] - r], [e[12] + r, e[14] + r], [e[12] - r, e[14] + r]]; }
    else if (k === "bush") { const r = (o.userData.radius ?? 0.8) * Math.hypot(e[0], e[1], e[2]); poly = [[e[12] - r, e[14] - r], [e[12] + r, e[14] - r], [e[12] + r, e[14] + r], [e[12] - r, e[14] + r]]; }
    else { const b = new THREE.Box3().setFromObject(o); poly = [[b.min.x, b.min.z], [b.max.x, b.min.z], [b.max.x, b.max.z], [b.min.x, b.max.z]]; }
    const top = new THREE.Box3().setFromObject(o).max.y;
    outsiders.push({ name: o.userData.kind === "prop" ? (o.name || "prop") : (o.name || k), kind: k, poly, y0: e[13], top });
  });
  for (const un of units) {
    if (!outsiders.length) break;
    const e = un.obj.matrixWorld.elements, sz = Math.hypot(e[8], e[9], e[10]) || 1;
    const out = [e[8] / sz, e[10] / sz], along = [-out[1], out[0]];
    const strip = [[-un.w / 2, 0.12], [un.w / 2, 0.12], [un.w / 2, 0.12 + rules.outside], [-un.w / 2, 0.12 + rules.outside]]
      .map(([s, o]) => [un.x + along[0] * s + out[0] * o, un.z + along[1] * s + out[1] * o]);
    for (const o of outsiders) {
      // in front of the opening: over a quarter of its width, and up into it (not a low bed under a sill)
      if (un.kind !== "door" && (o.y0 >= un.y + un.h || o.top < un.y + 0.2)) continue;
      if (un.kind === "door" && o.top < un.y + 0.3) continue;
      if (coverAlong(o.poly, strip, along, [un.x, un.z]) >= Math.max(0.25, un.w / 4)) {
        add("window", `outside: the ${o.kind} "${o.name}" at ${pt(o.poly[0])} stands within ${cm(rules.outside)} of the ${un.kind === "door" ? "entrance door" : "window"} at ${pt([un.x, un.z])}: move it clear of it`, { piece: o.name });
      }
    }
  }

  // ---- sizes: usual for its kind, and for its room
  for (const p of pieces) {
    const S = SIZES[p.type];
    if (S && !/-l\b|\bl-|corner|angle|modular-l/.test(p.name.toLowerCase())) {
      const bad = [];
      if (S.w && (p.w < S.w[0] || p.w > S.w[1])) bad.push(`${f2(p.w)} m wide`);
      if (S.d && (p.d < S.d[0] || p.d > S.d[1])) bad.push(`${f2(p.d)} m deep`);
      if (S.h && !p.room?.sloped && (p.h < S.h[0] || p.h > S.h[1])) bad.push(`${f2(p.h)} m high`); // under a slope a low piece is chosen
      if (bad.length) add("size", `"${p.name}"${p.room ? ` in "${p.room.name}"` : ""} is ${bad.join(", ")}: a ${p.type} is usually ${S.say}`, { room: p.room?.name, piece: p.name });
    }
    if (!p.room) continue;
    if (p.type === "bed") {
      // the room's length along the bed: bed + 60 cm at least
      const proj = p.room.polygon.map(([x, z]) => x * p.uz[0] + z * p.uz[1]);
      const len = Math.max(...proj) - Math.min(...proj);
      if (len < p.d + rules.bedSide) add("size", `"${p.name}" (${f2(p.d)} m long) does not fit "${p.room.name}" (${f2(len)} m that way): leave ${cm(rules.bedSide)} at its foot or turn it`, { room: p.room.name, piece: p.name });
    }
    if (p.type === "sofa" && p.w >= 1.9 && p.room.use === "bedroom" && p.room.sn < 12) {
      add("size", `a ${f2(p.w)} m sofa ("${p.name}") in a ${f2(p.room.sn)} m² bedroom ("${p.room.name}"): too big for the room`, { room: p.room.name, piece: p.name });
    }
    if (!p.flat && !p.hang && p.w * p.d > 0.5 * p.room.sn) {
      add("size", `"${p.name}" covers ${Math.round((100 * p.w * p.d) / p.room.sn)} % of "${p.room.name}" (${f2(p.room.sn)} m²): too big for the room`, { room: p.room.name, piece: p.name });
    }
  }

  // ---- passages: 80 cm between a room's doors, room to use a bed, a sofa, a wardrobe
  const openings = doorsOf(plans, units, walls);
  for (const [room, ps] of byRoom) {
    const obstacles = ps.filter((p) => !p.flat && !p.hang && p.y0 < room.y + 0.4 && p.y1 > room.y + 0.1);
    for (const p of ps) {
      if (p.type === "bed") {
        const sides = ["left", "right"].map((s) => faceClearance(p, s, obstacles, room.polygon, { skipHead: 0.6 }));
        const need = p.w >= 1.2 ? sides : [Math.max(...sides)];
        const narrow = need.filter((c) => c < rules.bedSide - 0.01);
        if (narrow.length) add("passage", `"${p.name}" in "${room.name}": ${narrow.map(cm).join(" and ")} beside it (keep ${cm(rules.bedSide)} ${p.w >= 1.2 ? "on both sides of a double bed" : "on one side"})`, { room: room.name, piece: p.name });
        const foot = faceClearance(p, "foot", obstacles, room.polygon);
        if (p.w >= 1.2 && foot < rules.bedSide - 0.01) add("passage", `"${p.name}" in "${room.name}": ${cm(foot)} at its foot (keep ${cm(rules.bedSide)})`, { room: room.name, piece: p.name });
      }
      if (p.type === "sofa" || p.type === "armchair") {
        const c = faceClearance(p, "front", obstacles.filter((q) => !LOW.has(q.type) && q.h > 0.5), room.polygon);
        if (c < rules.sofaFront - 0.01) add("passage", `"${p.name}" in "${room.name}": ${cm(c)} in front of it (keep ${cm(rules.sofaFront)})`, { room: room.name, piece: p.name });
      }
      if (p.type === "wardrobe" || p.type === "kitchen" || p.type === "appliance") {
        const c = faceClearance(p, "front", obstacles, room.polygon);
        if (c < rules.storageFront - 0.01) add("passage", `"${p.name}" in "${room.name}": ${cm(c)} in front of it (keep ${cm(rules.storageFront)} to open it and stand there)`, { room: room.name, piece: p.name });
      }
    }
    // the doors of the room joined by an 80 cm passage (the narrowing furniture makes, not the walls)
    const doors = openings.filter((o) => o.rooms(rooms).includes(room)).map((o) => o.inside(room));
    if (doors.length >= 2 && obstacles.length) {
      const empty = passage(room, [], doors), full = passage(room, obstacles, doors);
      for (const g of full) {
        const e = empty.find((x) => x.a === g.a && x.b === g.b);
        if (g.width < rules.passage - 0.03 && (!e || e.width >= g.width + 0.1)) {
          add("passage", `"${room.name}": the way between the doors at ${pt(doors[g.a])} and ${pt(doors[g.b])} narrows to ${cm(g.width)} at ${pt(g.at)}${g.by ? ` by "${g.by}"` : ""} (keep a ${cm(rules.passage)} passage)`, { room: room.name, piece: g.by });
        }
      }
    }
  }
  // door swings: the leaf's quarter circle on its side
  for (const sw of swings(plans)) {
    for (const p of pieces) {
      if (p.flat || p.hang || p.wallHung || !p.room || p.room.y !== sw.y) continue;
      if (p.y0 > sw.y + 2.0 || p.y1 < sw.y + 0.05) continue;
      if (sw.tris.some((t) => overlapDepth(p.corners, t) > 0.03)) add("passage", `the door at ${pt(sw.at)} (${f2(sw.width)} m) swings into "${p.name}" in "${p.room.name}": move it out of the door's swing`, { room: p.room.name, piece: p.name });
    }
  }

  // ---- supports: what each piece rests on, measured with rays against the meshes
  supports(root, pieces, add, rules);

  return { rooms, pieces, findings };
}

/** How much of a window's width (along `along`, centred on `c`) a polygon covers inside `strip`. */
function coverAlong(poly, strip, along, c) {
  if (overlapDepth(poly, strip) <= 0.001) return 0;
  // clip the piece's polygon to the strip and measure its extent along the wall
  let q = poly;
  for (let i = 0; i < strip.length; i++) {
    const a = strip[i], b = strip[(i + 1) % strip.length];
    const n = [-(b[1] - a[1]), b[0] - a[0]]; // left of a → b
    const sgn = Math.sign((strip[(i + 2) % strip.length][0] - a[0]) * n[0] + (strip[(i + 2) % strip.length][1] - a[1]) * n[1]) || 1;
    const out = [];
    for (let k = 0; k < q.length; k++) {
      const P = q[k], Q = q[(k + 1) % q.length];
      const fp = sgn * ((P[0] - a[0]) * n[0] + (P[1] - a[1]) * n[1]), fq = sgn * ((Q[0] - a[0]) * n[0] + (Q[1] - a[1]) * n[1]);
      if (fp >= 0) out.push(P);
      if ((fp > 0 && fq < 0) || (fp < 0 && fq > 0)) { const t = fp / (fp - fq); out.push([P[0] + (Q[0] - P[0]) * t, P[1] + (Q[1] - P[1]) * t]); }
    }
    q = out;
    if (!q.length) return 0;
  }
  const s = q.map(([x, z]) => (x - c[0]) * along[0] + (z - c[1]) * along[1]);
  return Math.max(...s) - Math.min(...s);
}

/** The doors of the floor plans' partitions and the outside doors, with the point in front of each in a room. */
function doorsOf(plans, units, walls) {
  const out = [];
  for (const P of plans) {
    for (const p of P.partitions) {
      const L = Math.hypot(p.to[0] - p.from[0], p.to[1] - p.from[1]) || 1;
      const u = [(p.to[0] - p.from[0]) / L, (p.to[1] - p.from[1]) / L], n = [-u[1], u[0]];
      for (const o of p.openings ?? []) {
        const c = [p.from[0] + u[0] * (o.offset + o.width / 2), p.from[1] + u[1] * (o.offset + o.width / 2)];
        const pts = [1, -1].map((s) => [c[0] + n[0] * s * 0.5, c[1] + n[1] * s * 0.5]);
        out.push({ y: P.y, rooms: (rooms) => rooms.filter((r) => r.y === P.y && pts.some((q) => inside(r.polygon, q[0], q[1]))), inside: (r) => pts.find((q) => inside(r.polygon, q[0], q[1])) });
      }
    }
  }
  for (const un of units) {
    if (un.kind !== "door") continue;
    const e = un.obj.matrixWorld.elements, sz = Math.hypot(e[8], e[9], e[10]) || 1;
    const out_ = [e[8] / sz, e[10] / sz];
    const wall = walls.find((w) => segDist([un.x, un.z], w.from, w.to) < 0.6);
    const q = [un.x - out_[0] * ((wall?.thickness ?? 0.3) + 0.4), un.z - out_[1] * ((wall?.thickness ?? 0.3) + 0.4)];
    out.push({ y: un.y, rooms: (rooms) => rooms.filter((r) => Math.abs(r.y - un.y) < 0.3 && inside(r.polygon, q[0], q[1])), inside: () => q });
  }
  return out;
}

/** The leaves' swings: a door's quarter circle (8 segments) on the side it opens to. */
function swings(plans) {
  const out = [];
  for (const P of plans) {
    for (const p of P.partitions) {
      const L = Math.hypot(p.to[0] - p.from[0], p.to[1] - p.from[1]) || 1;
      const u = [(p.to[0] - p.from[0]) / L, (p.to[1] - p.from[1]) / L], r = [-u[1], u[0]]; // right of walking from → to
      for (const o of p.openings ?? []) {
        if (!o.door) continue;
        const side = o.door.swing === "right" ? 1 : -1;
        const start = o.door.hinge !== "end";
        const a = [p.from[0] + u[0] * o.offset, p.from[1] + u[1] * o.offset];
        const hinge = start ? a : [a[0] + u[0] * o.width, a[1] + u[1] * o.width];
        const toward = start ? u : [-u[0], -u[1]];
        const t = (p.thickness ?? 0.1) / 2;
        const h0 = [hinge[0] + r[0] * side * t, hinge[1] + r[1] * side * t];
        const poly = [h0];
        for (let k = 0; k <= 8; k++) {
          const ang = (k / 8) * (Math.PI / 2);
          const dir = [toward[0] * Math.cos(ang) + r[0] * side * Math.sin(ang), toward[1] * Math.cos(ang) + r[1] * side * Math.sin(ang)];
          poly.push([h0[0] + dir[0] * o.width * 0.95, h0[1] + dir[1] * o.width * 0.95]);
        }
        // a convex fan: its triangles for the overlap test
        const tris = [];
        for (let k = 1; k + 1 < poly.length; k++) tris.push([poly[0], poly[k], poly[k + 1]]);
        out.push({ y: P.y, at: [a[0] + u[0] * o.width / 2, a[1] + u[1] * o.width / 2], width: o.width, tris });
      }
    }
  }
  return out;
}

/**
 * The narrowest point of the best way between each pair of doors of a room (a 5 cm grid, the width a
 * body has there: twice the distance to the nearest wall or piece), and the piece that narrows it.
 */
function passage(room, obstacles, doors) {
  const c = 0.05, poly = room.polygon;
  const xs = poly.map((p) => p[0]), zs = poly.map((p) => p[1]);
  const x0 = Math.min(...xs), z0 = Math.min(...zs);
  const nx = Math.ceil((Math.max(...xs) - x0) / c), nz = Math.ceil((Math.max(...zs) - z0) / c);
  if (nx * nz > 160000) return [];
  const cen = (i, j) => [x0 + (i + 0.5) * c, z0 + (j + 0.5) * c];
  // the distance to the nearest wall (the room's edges) or obstacle, and which obstacle
  const W = new Float32Array(nx * nz), by = new Int16Array(nx * nz).fill(-1);
  for (let j = 0; j < nz; j++) for (let i = 0; i < nx; i++) {
    const q = cen(i, j);
    if (!inside(poly, q[0], q[1])) { W[j * nx + i] = 0; continue; }
    let d = polyDist(poly, q), who = -1;
    obstacles.forEach((p, k) => {
      const dd = inside(p.corners, q[0], q[1]) ? 0 : polyDist(p.corners, q);
      if (dd < d) { d = dd; who = k; }
    });
    W[j * nx + i] = 2 * d;
    by[j * nx + i] = who;
  }
  const cellOf = ([x, z]) => [Math.min(nx - 1, Math.max(0, Math.floor((x - x0) / c))), Math.min(nz - 1, Math.max(0, Math.floor((z - z0) / c)))];
  const out = [];
  for (let a = 0; a < doors.length; a++) for (let b = a + 1; b < doors.length; b++) {
    // the widest way: maximise the narrowest width along it (a max-heap Dijkstra)
    const [si, sj] = cellOf(doors[a]), [gi, gj] = cellOf(doors[b]);
    const best = new Float32Array(nx * nz).fill(-1), from = new Int32Array(nx * nz).fill(-1);
    const heap = [[W[sj * nx + si], sj * nx + si]];
    best[sj * nx + si] = W[sj * nx + si];
    while (heap.length) {
      let m = 0;
      for (let k = 1; k < heap.length; k++) if (heap[k][0] > heap[m][0]) m = k;
      const [v, k] = heap.splice(m, 1)[0];
      if (v < best[k]) continue;
      if (k === gj * nx + gi) break;
      const i = k % nx, j = (k - i) / nx;
      for (const [di, dj] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        const ii = i + di, jj = j + dj;
        if (ii < 0 || jj < 0 || ii >= nx || jj >= nz) continue;
        const kk = jj * nx + ii;
        const w = Math.min(v, W[kk]);
        if (w > best[kk]) { best[kk] = w; from[kk] = k; heap.push([w, kk]); }
      }
      if (heap.length > 4000) heap.sort((p, q) => q[0] - p[0]).length = 2000;
    }
    const goal = gj * nx + gi;
    if (best[goal] < 0) continue;
    // where it is narrowest, away from the doors themselves
    let at = null, width = Infinity, who = -1;
    for (let k = goal; k >= 0; k = from[k]) {
      const i = k % nx, j = (k - i) / nx, q = cen(i, j);
      if (Math.hypot(q[0] - doors[a][0], q[1] - doors[a][1]) < 0.45 || Math.hypot(q[0] - doors[b][0], q[1] - doors[b][1]) < 0.45) continue;
      if (W[k] < width) { width = W[k]; at = q; who = by[k]; }
    }
    if (at) out.push({ a, b, width, at, by: who >= 0 ? obstacles[who]?.name : null });
  }
  return out;
}

/** How far behind a piece's back the nearest surface is (rays from 3 × 3 points of its back face), or null. */
function backGap(p, meshes, owner) {
  const ray = new THREE.Raycaster();
  ray.layers.enableAll();
  const back = new THREE.Vector3(-p.uz[0], 0, -p.uz[1]), from = new THREE.Vector3();
  const xs = p.corners.map((q) => q[0]), zs = p.corners.map((q) => q[1]);
  const near = meshes.filter(({ o, box }) => owner.get(o) !== p && box.max.y >= p.y0 && box.min.y <= p.y1
    && box.max.x >= Math.min(...xs) - 0.6 && box.min.x <= Math.max(...xs) + 0.6 && box.max.z >= Math.min(...zs) - 0.6 && box.min.z <= Math.max(...zs) + 0.6).map((m) => m.o);
  const gaps = [];
  for (const a of [-0.3, 0, 0.3]) for (const b of [0.25, 0.5, 0.75]) {
    // from the middle of its depth (a model's box can reach into the wall behind it)
    const q = [p.at[0] + p.ux[0] * a * p.w, p.at[1] + p.ux[1] * a * p.w];
    ray.set(from.set(q[0], p.y0 + b * (p.y1 - p.y0), q[1]), back);
    ray.far = p.d / 2 + 0.6;
    const h = ray.intersectObjects(near, false)[0];
    if (h) gaps.push(h.distance - p.d / 2);
  }
  if (!gaps.length) return null;
  gaps.sort((x, y) => x - y);
  return Math.max(0, gaps[0]);
}

/** Supports: rays down against the meshes under each piece (not its own, not rugs). */
function supports(root, pieces, add, rules) {
  const meshes = [];
  root.traverse((o) => {
    if (!o.isMesh || o.isInstancedMesh || !o.geometry?.attributes?.position) return;
    const k = kindOf(o);
    if (["terrain", "leaves", "ceiling"].includes(k)) return;
    // a rug is no support (a piece stands through it on the floor)
    for (let a = o; a; a = a.parent) if (a.userData?.kind === "furniture" && (a.userData.flat || pieceType(a.userData.name) === "rug")) return;
    meshes.push({ o, box: new THREE.Box3().setFromObject(o) });
  });
  const owner = new Map();
  for (const p of pieces) p.obj.traverse((c) => owner.set(c, p));
  const ray = new THREE.Raycaster();
  ray.layers.enableAll();
  const down = new THREE.Vector3(0, -1, 0), from = new THREE.Vector3();
  const floats = new Map(); // grouped: the same gap above the same kind of support
  // colliding first: a piece in another one gets that line, not a "sunk" or "overhangs" one besides
  const collide = [];
  const seat = new Set(["chair", "armchair"]), under = new Set(["dining", "desk", "table", "kitchen", "coffee"]);
  for (let i = 0; i < pieces.length; i++) for (let j = i + 1; j < pieces.length; j++) {
    const A = pieces[i], B = pieces[j];
    if (!A.room || A.room !== B.room || A.flat || B.flat || A.hang || B.hang) continue;
    if ((seat.has(A.type) && under.has(B.type)) || (seat.has(B.type) && under.has(A.type))) continue; // a chair tucked under a table
    const dy = Math.min(A.y1, B.y1) - Math.max(A.y0, B.y0);
    if (dy <= rules.overlap) continue;
    const d = overlapDepth(A.corners, B.corners);
    if (d > rules.overlap) collide.push({ A, B, d, dy });
  }
  for (const p of pieces) {
    p.restsOn = new Set();
    if (p.hang || p.flat || p.type === "curtain" || !p.room) continue;
    const xs = p.corners.map((q) => q[0]), zs = p.corners.map((q) => q[1]);
    const near = meshes.filter(({ o, box }) => owner.get(o) !== p && box.max.x >= Math.min(...xs) - 0.01 && box.min.x <= Math.max(...xs) + 0.01
      && box.max.z >= Math.min(...zs) - 0.01 && box.min.z <= Math.max(...zs) + 0.01 && box.max.y <= p.y0 + 0.16 && box.max.y >= p.y0 - 1.5).map((m) => m.o);
    // 5 × 5 points over 80 % of the footprint, each its first surface below (from 15 cm over the base)
    const hits = [];
    for (let a = 0; a < 5; a++) for (let b = 0; b < 5; b++) {
      const s = (-0.4 + (0.8 * a) / 4) * p.w, t = (-0.4 + (0.8 * b) / 4) * p.d;
      const q = [p.at[0] + p.ux[0] * s + p.uz[0] * t, p.at[1] + p.ux[1] * s + p.uz[1] * t];
      ray.set(from.set(q[0], p.y0 + 0.15, q[1]), down);
      ray.far = 1.65;
      const h = ray.intersectObjects(near, false)[0];
      hits.push(h ? { y: h.point.y, by: owner.get(h.object) ?? null } : null);
    }
    for (const h of hits) if (h?.by && Math.abs(h.y - p.y0) < 0.05) p.restsOn.add(h.by);
    if (collide.some((c) => (c.A === p || c.B === p) && c.d > 0.05 && !hits.some((h) => h?.by && (h.by === c.A || h.by === c.B) && Math.abs(h.y - p.y0) < 0.05))) continue;
    const centre = hits[12];
    const on = hits.filter((h) => h && Math.abs(h.y - p.y0) <= rules.float);
    const below = hits.filter((h) => h && h.y < p.y0 - rules.float);
    const name = (h) => (h?.by ? `"${h.by.name}"` : "the floor");
    if (p.wallHung) {
      // a wall-hung piece: its back on a wall (rays backwards from its back to the walls, partitions,
      // tiles and boxes behind it), or resting on something
      const back = backGap(p, meshes, owner) ?? p.wall?.d ?? Infinity;
      if (back > rules.fixed && on.length < rules.supported * 25) {
        add("support", `"${p.name}" in "${p.room.name}" is fixed to nothing: its back is ${cm(back)} from the nearest wall${on.length ? "" : " and nothing is under it"} (put it against the wall)`, { room: p.room.name, piece: p.name });
      }
      continue;
    }
    // sunk: its base a little into what it stands on (more than 10 cm is two pieces overlapping, below)
    const into = hits.filter((h) => h && h.y > p.y0 + rules.sink && h.y <= p.y0 + 0.1);
    if (centre && into.includes(centre) && into.length >= 13) {
      add("support", `"${p.name}" in "${p.room.name}" is sunk ${cm(centre.y - p.y0)} into ${name(centre)} (its base at ${f2(p.y0 - p.room.y)} m, ${name(centre)}'s top at ${f2(centre.y - p.room.y)} m): raise it onto it`, { room: p.room.name, piece: p.name });
      continue;
    }
    if (!on.length && below.length) {
      // floating: the highest surface under it is more than 1 cm below its base
      const top = below.sort((a, b) => b.y - a.y)[0];
      const gap = p.y0 - top.y;
      const key = `${top.by?.name ?? "floor"}|${Math.round(gap * 200)}`; // grouped by support and gap (5 mm)
      const g = floats.get(key) ?? { gap, under: name(top), level: top.y - p.room.y, pieces: new Set(), rooms: new Set(), base: p.y0 - p.room.y };
      g.pieces.add(p.name);
      g.rooms.add(p.room.name);
      floats.set(key, g);
      continue;
    }
    if (!on.length && !below.length && p.bottom > rules.float) {
      add("support", `"${p.name}" in "${p.room.name}" floats with nothing under it (its base ${f2(p.bottom)} m above the floor)`, { room: p.room.name, piece: p.name });
    } else if (on.length && (on.length < rules.supported * 25 || !centre || Math.abs(centre.y - p.y0) > rules.float)) {
      const by = on.find((h) => h.by)?.by;
      add("support", `"${p.name}" in "${p.room.name}" overhangs: only ${Math.round((100 * on.length) / 25)} % of it rests on ${by ? `"${by.name}"` : "its support"}${centre && Math.abs(centre.y - p.y0) > rules.float ? ", its centre over nothing" : ""} (it would tip over): move it onto it`, { room: p.room.name, piece: p.name });
    }
  }
  for (const g of floats.values()) {
    const list = [...g.pieces].map((n) => `"${n}"`).join(", ");
    add("support", `${g.pieces.size > 1 ? `${list} float` : `${list} floats`} ${cm(g.gap)} above ${g.under} (base at ${f2(g.base)} m, ${g.under}'s top at ${f2(g.level)} m) in ${[...g.rooms].map((r) => `"${r}"`).join(", ")}: lower ${g.pieces.size > 1 ? "them" : "it"} onto it`, { room: [...g.rooms][0], piece: [...g.pieces][0] });
  }
  // two pieces overlapping by more than 1 cm in plan and in height (not one resting on the other)
  for (const { A, B, d } of collide) {
    if (A.restsOn.has(B) || B.restsOn.has(A)) continue;
    add("support", `"${A.name}" and "${B.name}" in "${A.room.name}" overlap by ${cm(d)}: move one of them`, { room: A.room.name, piece: A.name });
  }
}

/** The findings as audit lines: plan and support issues first, at most `max`, then how many more. */
export function auditLines(L, { max = 14 } = {}) {
  const order = ["plan", "support", "window", "passage", "size"];
  const seen = new Set();
  const lines = [];
  // within the supports: what floats, sinks or overlaps first (the numbers say what to change), then
  // what hangs off a wall, then what goes through one
  const rank = (f) => (/floats|sunk|overhangs/.test(f.text) ? 0 : /overlap/.test(f.text) ? 1 : /fixed to nothing/.test(f.text) ? 2 : 3);
  for (const k of order) for (const f of L.findings.filter((x) => x.kind === k).sort((a, b) => (k === "support" ? rank(a) - rank(b) : 0))) {
    const t = `${k === "plan" ? "" : `${k}: `}${f.text}`;
    if (seen.has(t)) continue;
    seen.add(t);
    lines.push(t);
  }
  if (lines.length <= max) return lines;
  return [...lines.slice(0, max), `… and ${lines.length - max} more layout findings: measure() lists them all with every piece`];
}

/** The table the builder's measure tool shows: every piece with its numbers, and every finding. */
export function layoutReport(L) {
  return {
    pieces: L.pieces.filter((p) => p.room).map((p) => ({
      name: p.name, type: p.type, room: p.room.name, storey: p.room.storey + 1, at: p.at.map((v) => Math.round(v * 100) / 100),
      w: Math.round(p.w * 100) / 100, d: Math.round(p.d * 100) / 100, h: Math.round(p.h * 100) / 100, rot: p.rot,
      bottom: Math.round((p.bottom ?? 0) * 100) / 100, wall: p.wall ? Math.round(p.wall.d * 100) / 100 : null,
    })),
    findings: L.findings.map((f) => ({ kind: f.kind, room: f.room ?? null, piece: f.piece ?? null, text: f.text })),
    rooms: L.rooms.map((r) => ({ name: r.name, storey: r.storey + 1, use: r.use, area: Math.round(r.sn * 100) / 100, plan: r.plan })),
  };
}

export default { layout, auditLines, layoutReport, readPieces, pieceType, RULES };
