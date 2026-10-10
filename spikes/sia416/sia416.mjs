// Mock SIA 416 report for TestVillaGille v10: numbers computed from the scene's own data modules
// (interior-layouts.js + dimensions.js, pure data) and the opening lists transcribed from openings.js.
// usage: node sia416.mjs <scene src dir> <out.json>
import path from "node:path";
import fs from "node:fs";
import { pathToFileURL } from "node:url";

const [, , srcDir, outFile] = process.argv;
const L = await import(pathToFileURL(path.join(srcDir, "interior-layouts.js")).href);
const { D, footprint } = await import(pathToFileURL(path.join(srcDir, "dimensions.js")).href);

// ---- openings (openings.js / shell.js), offsets from the left end of the façade seen from outside
const win = (offset, width, height = 1.2, sill = 1) => ({ offset, width, height, sill, kind: "fenêtre" });
const entry = (offset, width = 1, height = 2.2) => ({ offset, width, height, sill: 0, kind: "porte" });
const small = (offset) => ({ offset, width: 1, height: 0.6, sill: 1.65, kind: "soupirail" });
const OPEN = {
  basement: { 0: [small(0.7)], 1: [small(2.82), small(8.3)], 3: [small(3)] },
  ground: { 0: [win(3.78, 0.8), entry(5.625), win(8.85, 1.2)], 1: [entry(4.3, 1, 2.1), win(6.45, 2.3)], 2: [win(1.385, 3, 2.2, 0), win(6.695, 3, 2.2, 0)], 3: [win(5.15, 3)] },
  first: { 0: [win(0.5, 1.4), win(2.88, 1.4), { ...entry(6.6, 0.9, 2.2), kind: "porte-fenêtre" }, win(9.3, 0.8)], 1: [win(4.4, 0.8), win(6.4, 1.4)], 2: [win(1.385, 3), win(6.695, 3, 2.2, 0)], 3: [win(2.1, 1.8, 2.2, 0), win(4.655, 1.4)] },
  attic: { 0: [win(2.27, 2.3), win(7.0, 1.4)], 2: [win(3.08, 1.4), win(6.59, 1.4)] },
};
// perimeterWalls flips this footprint (positive signed area): edge i runs from polygon[i+1] to polygon[i]
const EDGE = [
  { name: "NO", from: [5.5, -5], dir: [-1, 0], inward: [0, 1], len: 11 },
  { name: "NE", from: [5.5, 5], dir: [0, -1], inward: [-1, 0], len: 10 },
  { name: "SE", from: [-5.5, 5], dir: [1, 0], inward: [0, -1], len: 11 },
  { name: "SO", from: [-5.5, -5], dir: [0, 1], inward: [1, 0], len: 10 },
];
const WALL = D.wall; // 0.40
const roofUnderRidge = D.ridge - D.roofThickness; // 9.64
const roofUnderWall = roofUnderRidge - D.pitch * D.width / 2; // 6.637
const atticClear = (x) => Math.min(4.16, roofUnderRidge - D.pitch * Math.abs(x) - D.attic);

const STOREYS = [
  { key: "basement", label: "Sous-sol", prefix: "-1", plan: L.basement, top: D.ground, h: () => L.basement.height },
  { key: "ground", label: "Rez-de-chaussée", prefix: "0", plan: L.ground, top: D.first, h: () => L.ground.height },
  { key: "first", label: "1er étage", prefix: "1", plan: L.first, top: D.attic, h: () => L.first.height },
  { key: "attic", label: "Combles", prefix: "2", plan: L.attic, top: null, h: atticClear },
];

// ---- geometry
const area = (p) => { let a = 0; for (let i = 0; i < p.length; i++) { const [x1, z1] = p[i], [x2, z2] = p[(i + 1) % p.length]; a += x1 * z2 - x2 * z1; } return Math.abs(a) / 2; };
const inside = (p, x, z) => { let c = false; for (let i = 0, j = p.length - 1; i < p.length; j = i++) { const [xi, zi] = p[i], [xj, zj] = p[j]; if ((zi > z) !== (zj > z) && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) c = !c; } return c; };
const segDist = (x, z, [ax, az], [bx, bz]) => { const dx = bx - ax, dz = bz - az, L2 = dx * dx + dz * dz; let t = L2 ? ((x - ax) * dx + (z - az) * dz) / L2 : 0; t = Math.max(0, Math.min(1, t)); return Math.hypot(x - ax - t * dx, z - az - t * dz); };
const isRect = (p) => p.length === 4 && new Set(p.map((q) => q[0])).size === 2 && new Set(p.map((q) => q[1])).size === 2;
// ∫ min(cap, h(x)) along a segment
const wallArea = (a, b, h, cap = Infinity) => { const n = 400, len = Math.hypot(b[0] - a[0], b[1] - a[1]); let s = 0; for (let k = 0; k < n; k++) { const t = (k + 0.5) / n; s += Math.min(cap, h(a[0] + (b[0] - a[0]) * t)); } return (s / n) * len; };
// a label point well inside a polygon (grid search for the point farthest from the edges)
function labelPoint(p) {
  const xs = p.map((q) => q[0]), zs = p.map((q) => q[1]);
  let best = [xs[0], zs[0]], bd = -1;
  for (let x = Math.min(...xs); x <= Math.max(...xs); x += 0.05) for (let z = Math.min(...zs); z <= Math.max(...zs); z += 0.05) {
    if (!inside(p, x, z)) continue;
    let d = Infinity; for (let i = 0; i < p.length; i++) d = Math.min(d, segDist(x, z, p[i], p[(i + 1) % p.length]));
    if (d > bd) { bd = d; best = [x, z]; }
  }
  return { at: best, room: bd };
}

// ---- SIA 416 class of a room: by use, then by name for the uses the kit lacks (#47)
function siaClass(r) {
  // the name decides only for "storage", where the builder files cellars, laundries and plant rooms
  if (r.use === "storage") return /local technique|chaufferie|technique|\bpac\b/i.test(r.name) ? "SI" : "SUS";
  if (r.use === "hall" || r.use === "stair") return "SD";
  return "SUP";
}
const flatOf = (name) => { const f = name.split(" — ")[1] ?? ""; return /appartement 1/i.test(f) ? "App. 1" : /appartement 2/i.test(f) ? "App. 2" : "Commun"; };
const shortName = (name) => name.split(" — ")[0];
const FLOOR = { "oak-light": "Parquet chêne clair", tile: "Carrelage grès cérame clair", concrete: "Béton brut, sans revêtement" };

// wet rooms tiled by bathDetails / t.tiled (furniture-*.js): storey, room index, full-height edges
const TILED = { ground: [[3, [0]], [2, []]], first: [[9, [1, 2, 3]], [3, []]], attic: [[4, [0, 1]], [5, [1]]] };

const out = { storeys: [], partitions: [], facades: [], basementWalls: null };
for (const s of STOREYS) {
  const rooms = s.plan.rooms.map((r, i) => {
    const sn = area(r.polygon);
    const xs = r.polygon.map((q) => q[0]), zs = r.polygon.map((q) => q[1]);
    const formula = isRect(r.polygon) ? `${(Math.max(...xs) - Math.min(...xs)).toFixed(2)} × ${(Math.max(...zs) - Math.min(...zs)).toFixed(2)}` : `polygone, ${r.polygon.length} sommets`;
    return { no: `${s.prefix}.${String(i + 1).padStart(2, "0")}`, name: shortName(r.name), full: r.name, flat: flatOf(r.name), use: r.use, sia: siaClass(r), sn, plan: r.area ?? null, formula, polygon: r.polygon, floor: r.floor, label: labelPoint(r.polygon), openings: [], wallsGross: 0, tiles: 0 };
  });
  const roomAt = (x, z) => rooms.find((r) => inside(r.polygon, x, z));
  const h = s.h;
  // gross wall faces per room (clear height, sloped under the roof in the attic)
  for (const r of rooms) for (let i = 0; i < r.polygon.length; i++) r.wallsGross += wallArea(r.polygon[i], r.polygon[(i + 1) % r.polygon.length], h);
  // doors in partitions: both faces
  const parts = [];
  for (const p of s.plan.partitions) {
    const dx = p.to[0] - p.from[0], dz = p.to[1] - p.from[1], len = Math.hypot(dx, dz), ux = dx / len, uz = dz / len, nx = -uz, nz = ux;
    const height = p.height ?? (s.key === "attic" ? null : s.plan.height);
    const gross = height ? len * height : wallArea(p.from, p.to, h);
    let holes = 0;
    for (const o of p.openings ?? []) {
      const oh = o.height ?? 2.04, a = o.width * oh;
      if (a >= 1) holes += a;
      const cx = p.from[0] + ux * (o.offset + o.width / 2), cz = p.from[1] + uz * (o.offset + o.width / 2);
      for (const sgn of [1, -1]) {
        const r = roomAt(cx + sgn * nx * (p.thickness / 2 + 0.06), cz + sgn * nz * (p.thickness / 2 + 0.06));
        if (r) r.openings.push({ kind: o.door === false ? "passage" : "porte", w: o.width, h: oh, sill: 0, at: [cx, cz] });
      }
    }
    parts.push({ len, t: p.thickness, gross, holes, n: (p.openings ?? []).length, from: p.from, to: p.to, openings: p.openings ?? [] });
  }
  // windows and doors in the exterior walls
  const ext = [];
  for (const [ei, ops] of Object.entries(OPEN[s.key] ?? {})) {
    const e = EDGE[ei];
    for (const o of ops) {
      const along = o.offset + o.width / 2;
      const cx = e.from[0] + e.dir[0] * along, cz = e.from[1] + e.dir[1] * along;
      const r = roomAt(cx + e.inward[0] * (WALL + 0.08), cz + e.inward[1] * (WALL + 0.08));
      const op = { kind: o.kind, w: o.width, h: o.height, sill: o.sill, at: [cx, cz], edge: +ei };
      ext.push(op);
      if (r) r.openings.push(op);
    }
  }
  // wall tiles (faïence): 1.20 m, 2.40 m on the listed edges, under the roof in the attic
  for (const [idx, full] of TILED[s.key] ?? []) {
    const r = rooms[idx];
    let t = 0;
    for (let i = 0; i < r.polygon.length; i++) t += wallArea(r.polygon[i], r.polygon[(i + 1) % r.polygon.length], h, full.includes(i) ? 2.4 : 1.2);
    let holes = 0;
    for (const o of r.openings) {
      // the band this opening cuts: the edge it sits on
      let ei = 0, bd = Infinity;
      for (let i = 0; i < r.polygon.length; i++) { const d = segDist(o.at[0], o.at[1], r.polygon[i], r.polygon[(i + 1) % r.polygon.length]); if (d < bd) { bd = d; ei = i; } }
      const top = full.includes(ei) ? 2.4 : 1.2;
      const a = Math.max(0, Math.min(top, o.sill + o.h) - Math.max(0, o.sill)) * o.w;
      if (a >= 1) holes += a;
    }
    r.tiles = t - holes;
    r.tileFull = full.length;
  }
  for (const r of rooms) {
    r.holes = r.openings.reduce((a, o) => a + (o.w * o.h >= 1 ? o.w * o.h : 0), 0);
    r.small = r.openings.filter((o) => o.w * o.h < 1).length;
    r.paint = r.wallsGross - r.holes - r.tiles;
  }
  const sp = 11 * 10;
  const sn = rooms.reduce((a, r) => a + r.sn, 0);
  const by = (k) => rooms.filter((r) => r.sia === k).reduce((a, r) => a + r.sn, 0);
  out.storeys.push({ key: s.key, label: s.label, prefix: s.prefix, y: s.plan.y, clear: s.plan.height, sp, sn, sc: sp - sn, SUP: by("SUP"), SUS: by("SUS"), SD: by("SD"), SI: by("SI"), rooms, parts, ext });
}

// ---- façades (E02) and walls against the ground (E01)
const gable = 11 * (roofUnderWall - D.attic) + 0.5 * 11 * (roofUnderRidge - roofUnderWall);
const knee = 10 * (roofUnderWall - D.attic);
for (let ei = 0; ei < 4; ei++) {
  const e = EDGE[ei];
  const base = e.len * D.attic; // ground + first storeys, 0.00 → 5.48
  const top = ei % 2 === 0 ? gable : knee;
  const ops = ["ground", "first", "attic"].flatMap((k) => OPEN[k][ei] ?? []);
  const ded = ops.filter((o) => o.width * o.height >= 1), keep = ops.filter((o) => o.width * o.height < 1);
  out.facades.push({ name: e.name, len: e.len, base, top, topKind: ei % 2 === 0 ? "pignon" : "mur sous pente", gross: base + top, holes: ded.reduce((a, o) => a + o.width * o.height, 0), nHoles: ded.length, nKeep: keep.length, net: base + top - ded.reduce((a, o) => a + o.width * o.height, 0) });
}
out.basementWalls = { len: 42, h: 2.8, gross: 42 * 2.8, small: 4 };

// ---- volumes (VB, SIA 416): outer footprint × storey heights, the roof from the attic floor to its outer surface
const RADIER = 0.25; // not in the model: assumed
const roofVol = 10 * (11 * (D.ridge - D.attic) - D.pitch * (D.width / 2) ** 2);
out.volumes = {
  footprint: 110, radier: RADIER,
  storeys: [
    { label: "Sous-sol", from: D.basement - RADIER, to: D.ground, h: D.ground - D.basement + RADIER, v: 110 * (D.ground - D.basement + RADIER), formula: `11.00 × 10.00 × (2.80 + 0.25 radier)`, under: true },
    { label: "Rez-de-chaussée", from: D.ground, to: D.first, h: D.first - D.ground, v: 110 * (D.first - D.ground), formula: `11.00 × 10.00 × 2.74` },
    { label: "1er étage", from: D.first, to: D.attic, h: D.attic - D.first, v: 110 * (D.attic - D.first), formula: `11.00 × 10.00 × 2.74` },
    { label: "Combles et toiture", from: D.attic, to: D.ridge, h: null, v: roofVol, formula: `10.00 × (11.00 × 4.52 − 0.546 × 5.50²)` },
  ],
  garage: { sb: 33, v: 33 * (2.95 - 0.5) + 5.5 * (6 * (D.garageRidge - 2.95) - 0.3 * 9), formula: `6.00 × 5.50 × 2.45 + 5.50 × (6.00 × 1.08 − 0.30 × 3.00²)` },
  balconies: [
    { name: "Balcon en L, nord-ouest et sud-ouest (1er étage)", area: area([[-8, -6.8], [0.1, -6.8], [0.1, -5], [-5.5, -5], [-5.5, -0.8], [-8, -0.8]]), flat: "App. 2" },
    { name: "Balcon sud-est (1er étage, Chambre 4)", area: D.southBalconyWidth * D.southBalconyDepth, flat: "App. 1" },
  ],
  roofEave: D.ridge - D.pitch * D.width / 2,
};
out.attic = { minClear: atticClear(5.1), kneeOuter: roofUnderWall - D.attic };
fs.writeFileSync(outFile, JSON.stringify(out, null, 1));

// summary on stdout
for (const s of out.storeys) {
  console.log(`${s.label}: SP ${s.sp} SN ${s.sn.toFixed(2)} SC ${s.sc.toFixed(2)} SUP ${s.SUP.toFixed(2)} SUS ${s.SUS.toFixed(2)} SD ${s.SD.toFixed(2)} SI ${s.SI.toFixed(2)}`);
  for (const r of s.rooms) console.log(`  ${r.no} ${r.name.padEnd(42)} ${r.flat.padEnd(7)} ${r.sia.padEnd(4)} ${r.sn.toFixed(2).padStart(6)} plan ${r.plan ?? "-"}  walls ${r.wallsGross.toFixed(2)} holes ${r.holes.toFixed(2)} small ${r.small} tiles ${r.tiles.toFixed(2)} paint ${r.paint.toFixed(2)} label ${r.label.room.toFixed(2)}`);
  for (const p of s.parts) if (0) console.log(p);
  const c02 = s.parts.filter((p) => p.t >= 0.2), g01 = s.parts.filter((p) => p.t < 0.2);
  console.log(`  partitions C02 ${c02.reduce((a, p) => a + p.gross - p.holes, 0).toFixed(2)} (${c02.length}), G01 ${g01.reduce((a, p) => a + p.gross - p.holes, 0).toFixed(2)} (${g01.length})`);
  console.log(`  exterior openings ${s.ext.length}, unmapped ${s.ext.filter((o) => !s.rooms.some((r) => r.openings.includes(o))).length}`);
}
for (const f of out.facades) console.log(`façade ${f.name}: gross ${f.gross.toFixed(2)} holes ${f.holes.toFixed(2)} (${f.nHoles}, kept ${f.nKeep}) net ${f.net.toFixed(2)}`);
const vb = out.volumes.storeys.reduce((a, s) => a + s.v, 0);
console.log(`VB ${vb.toFixed(2)} (${out.volumes.storeys.map((s) => s.v.toFixed(2)).join(" + ")}), garage ${out.volumes.garage.v.toFixed(2)}, attic min clear ${out.attic.minClear.toFixed(3)}`);
console.log(`balconies ${out.volumes.balconies.map((b) => b.area.toFixed(2)).join(", ")}`);
