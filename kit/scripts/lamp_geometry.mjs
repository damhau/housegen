// Where the light sits in each catalogue lamp, and the run of cord a hanging one can lengthen or shorten (#102):
// measured on the full-resolution models (the archviz spike's assets/objects/<id>.glb, the photo service's volume),
// bottom-centred like furnish.js grounded(). Prints { id: { light, cord } } for objects.js.
//   node scripts/lamp_geometry.mjs <objects dir> <id> [<id> ...] [--hang <id> ...]
// light: [x, y, z] (m), the densest cluster of the shade (its bulb, its glass): where the kit puts the fixture's lights.
// cord: [y0, y1] (m), a hanging lamp's thin part between its body and its canopy (null: none found).
import { NodeIO } from "@gltf-transform/core";
import { ALL_EXTENSIONS } from "@gltf-transform/extensions";
import { MeshoptDecoder } from "meshoptimizer";
import { join } from "node:path";

await MeshoptDecoder.ready;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ "meshopt.decoder": MeshoptDecoder });

function mul(m, [x, y, z]) {
  return [m[0] * x + m[4] * y + m[8] * z + m[12], m[1] * x + m[5] * y + m[9] * z + m[13], m[2] * x + m[6] * y + m[10] * z + m[14]];
}

async function points(file) {
  const doc = await io.read(file);
  const out = [];
  for (const node of doc.getRoot().listNodes()) {
    const mesh = node.getMesh();
    if (!mesh) continue;
    const m = node.getWorldMatrix();
    for (const prim of mesh.listPrimitives()) {
      const pos = prim.getAttribute("POSITION");
      const v = [0, 0, 0];
      for (let i = 0; i < pos.getCount(); i++) out.push(mul(m, pos.getElement(i, v)));
    }
  }
  const lo = [0, 1, 2].map((k) => Math.min(...out.map((p) => p[k]))), hi = [0, 1, 2].map((k) => Math.max(...out.map((p) => p[k])));
  const c = [(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2];
  return { ps: out.map((p) => [p[0] - c[0], p[1] - c[1], p[2] - c[2]]), h: hi[1] - lo[1] };
}

// the 6 cm cell whose 3 x 3 x 3 neighbourhood holds the most points among `ps`: their centroid
function cluster(ps) {
  const cell = 0.06, key = (p) => p.map((v) => Math.floor(v / cell)).join(",");
  const count = new Map();
  for (const p of ps) count.set(key(p), (count.get(key(p)) ?? 0) + 1);
  let best = null, most = -1;
  for (const k of count.keys()) {
    const [i, j, l] = k.split(",").map(Number);
    let n = 0;
    for (let a = -1; a <= 1; a++) for (let b = -1; b <= 1; b++) for (let d = -1; d <= 1; d++) n += count.get(`${i + a},${j + b},${l + d}`) ?? 0;
    if (n > most) [best, most] = [[i, j, l], n];
  }
  const near = ps.filter((p) => p.every((v, k) => Math.abs(Math.floor(v / cell) - best[k]) <= 1));
  return [0, 1, 2].map((k) => Math.round((near.reduce((s, p) => s + p[k], 0) / near.length) * 1000) / 1000);
}

// the longest run of 1 cm slices that are empty or narrower than `thin` about the canopy's centre (the top 3 cm's),
// starting within 15 cm of the top: one cord, a rod, or several cords (only their ends have vertices)
function cord(ps, h, thin = 0.035) {
  const top = ps.filter(([, y]) => y > h - 0.03);
  const ax = top.reduce((s, p) => s + p[0], 0) / top.length, az = top.reduce((s, p) => s + p[2], 0) / top.length;
  const bins = Math.ceil(h / 0.01), wide = new Array(bins).fill(0);
  for (const [x, y, z] of ps) {
    const b = Math.min(bins - 1, Math.floor(y / 0.01));
    wide[b] = Math.max(wide[b], Math.hypot(x - ax, z - az));
  }
  let best = null;
  for (let b = bins - 1; b >= bins - 16 && b >= 0; b--) {
    if (wide[b] >= thin || (b < bins - 1 && wide[b + 1] < thin)) continue;
    let e = b;
    while (e > 0 && wide[e - 1] < thin) e--;
    if (!best || b - e > best[1] - best[0]) best = [e, b + 1];
  }
  if (!best) return null;
  const [y0, y1] = [best[0] * 0.01, Math.min(h, best[1] * 0.01)];
  return y1 - y0 >= 0.1 ? [Math.round(y0 * 1000) / 1000, Math.round(y1 * 1000) / 1000] : null;
}

const args = process.argv.slice(2), dir = args.shift(), split = args.indexOf("--hang");
const standing = split < 0 ? args : args.slice(0, split), hanging = split < 0 ? [] : args.slice(split + 1);
const out = {};
for (const id of [...standing, ...hanging]) {
  const { ps, h } = await points(join(dir, `${id}.glb`));
  if (hanging.includes(id)) {
    const c = cord(ps, h);
    out[id] = { light: cluster(ps.filter(([, y]) => y < (c ? c[0] : h * 0.6))), cord: c, h: Math.round(h * 1000) / 1000 };
  } else {
    out[id] = { light: cluster(ps.filter(([, y]) => y > h * 0.5)), h: Math.round(h * 1000) / 1000 };
  }
}
console.log(JSON.stringify(out, null, 1));
