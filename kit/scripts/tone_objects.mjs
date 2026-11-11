// The `tone` of each decor piece in objects.js, measured on its fetched model (run after fetch_objects.mjs): the share
// of its seen surface that is vivid (CIELAB chroma against lightness; browns and tans count as neutral) and the main
// hue family of that share. Labels made
// by eye or by name missed whole pieces (a "neutral" rug printed with a deep blue trellis).
//   node scripts/tone_objects.mjs [id...]  prints the pieces whose tone changes (and why for the ids given)
//   node scripts/tone_objects.mjs --write  rewrites objects.js with the measured tones
import { access, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { NodeIO } from "@gltf-transform/core";
import { ALL_EXTENSIONS } from "@gltf-transform/extensions";
import { MeshoptDecoder } from "meshoptimizer";
import sharp from "sharp";
import { OBJECTS } from "../objects.js";

const KIT = join(dirname(fileURLToPath(import.meta.url)), "..");
await MeshoptDecoder.ready;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ "meshopt.decoder": MeshoptDecoder });

// sRGB (0..1) -> CIELAB [L, chroma, hue in degrees]
function lab(rgb) {
  const [r, g, b] = rgb.map((c) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)));
  const f = (t) => (t > 216 / 24389 ? Math.cbrt(t) : (24389 / 27 * t + 16) / 116);
  const x = f((0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.9505), y = f(0.2126 * r + 0.7152 * g + 0.0722 * b), z = f((0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.089);
  const A = 500 * (x - y), B = 200 * (y - z);
  return [116 * y - 16, Math.hypot(A, B), (Math.atan2(B, A) * 180 / Math.PI + 360) % 360];
}

// the colour under each triangle the eye can see (faces pointing down left out: a rug's back, an underside), with
// its area as weight: an atlas's padding and its hidden faces do not count
async function samples(doc) {
  const out = [];
  const images = new Map();
  for (const node of doc.getRoot().listNodes()) {
    const mesh = node.getMesh();
    if (!mesh) continue;
    const world = node.getWorldMatrix();
    for (const prim of mesh.listPrimitives()) {
      const mat = prim.getMaterial();
      const f = mat ? mat.getBaseColorFactor() : [1, 1, 1, 1];
      const tex = mat?.getBaseColorTexture();
      if (tex && !images.has(tex)) {
        images.set(tex, await sharp(Buffer.from(tex.getImage())).removeAlpha().resize(128, 128, { fit: "fill" }).raw().toBuffer());
      }
      const img = tex ? images.get(tex) : null;
      const pos = prim.getAttribute("POSITION"), uv = prim.getAttribute("TEXCOORD_0"), idx = prim.getIndices();
      const n = idx ? idx.getCount() : pos.getCount();
      const at = (i) => (idx ? idx.getScalar(i) : i);
      const P = (i) => {
        const [x, y, z] = pos.getElement(at(i), []);
        return [0, 1, 2].map((r) => world[r] * x + world[4 + r] * y + world[8 + r] * z + world[12 + r]);
      };
      for (let t = 0; t + 2 < n; t += 3) {
        const [a, b, c] = [P(t), P(t + 1), P(t + 2)];
        const u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]], v = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
        const nx = u[1] * v[2] - u[2] * v[1], ny = u[2] * v[0] - u[0] * v[2], nz = u[0] * v[1] - u[1] * v[0];
        const area = Math.hypot(nx, ny, nz) / 2;
        if (area === 0 || ny / (2 * area) < -0.5) continue;
        if (!img || !uv) {
          out.push([[1, 1, 1].map((c, i) => c * Math.pow(f[i], 1 / 2.2)), area]);
          continue;
        }
        // a grid of points over the triangle, as fine as the texels it covers (a big face is a few triangles)
        const w = [t, t + 1, t + 2].map((i) => uv.getElement(at(i), []));
        const texels = Math.abs((w[1][0] - w[0][0]) * (w[2][1] - w[0][1]) - (w[2][0] - w[0][0]) * (w[1][1] - w[0][1])) / 2 * 128 * 128;
        const k = Math.max(1, Math.min(24, Math.ceil(Math.sqrt(texels))));
        const pts = [];
        for (let i = 0; i < k; i++) for (let j = 0; i + j < k; j++) pts.push([(i + 1 / 3) / k, (j + 1 / 3) / k]);
        for (const [p, q] of pts) {
          const r = 1 - p - q;
          const x = ((r * w[0][0] + p * w[1][0] + q * w[2][0]) % 1 + 1) % 1, y = ((r * w[0][1] + p * w[1][1] + q * w[2][1]) % 1 + 1) % 1;
          const k3 = (Math.min(127, Math.floor(y * 128)) * 128 + Math.min(127, Math.floor(x * 128))) * 3;
          out.push([[img[k3], img[k3 + 1], img[k3 + 2]].map((c, i) => c / 255 * Math.pow(f[i], 1 / 2.2)), area / pts.length]);
        }
      }
    }
  }
  return out;
}

// wood, leather, rattan and brass are browns and tans (CIELAB hue 40–100°): they go anywhere, as a neutral does,
// unless as vivid as a saffron or a terracotta
// colourfulness against lightness: a dark muted teal (chroma 12 at L 32) reads as teal once lit
const vivid = ([l, c, h]) => (h >= 40 && h <= 100 ? c > 30 && c > 0.65 * l : c > 10 && c > 0.3 * l);
const family = (h) => (h < 100 || h >= 330 ? "warm" : h < 200 ? "green" : "cool");

export function toneOf(px, why = false) {
  const c = px.map(([rgb, w]) => [lab(rgb), w]);
  const total = c.reduce((a, [, w]) => a + w, 0);
  const colourful = c.filter(([p]) => p[0] > 12 && vivid(p));
  const share = colourful.reduce((a, [, w]) => a + w, 0) / total;
  if (why) console.log(`  ${(100 * share).toFixed(0)} % of the seen area vivid`);
  if (share < 0.12) return c.reduce((a, [[l], w]) => a + l * w, 0) / total < 30 ? "dark" : "neutral";
  const n = {};
  for (const [[, , h], w] of colourful) n[family(h)] = (n[family(h)] ?? 0) + w;
  return Object.entries(n).sort((a, b) => b[1] - a[1])[0][0];
}

const WHY = process.argv.slice(2).filter((a) => !a.startsWith("--"));
const tones = {};
for (const o of OBJECTS.filter((o) => o.role !== "foliage")) {
  const file = join(KIT, "assets", o.abo ? "objects" : "made", `${o.id}.glb`);
  if (!(await access(file).then(() => true, () => false))) continue;
  tones[o.id] = toneOf(await samples(await io.read(file)), WHY.includes(o.id));
  if (tones[o.id] !== o.tone) console.log(`${o.role.padEnd(15)} ${o.id.padEnd(16)} ${o.tone.padEnd(8)} -> ${tones[o.id].padEnd(8)} ${o.name.slice(0, 60)}`);
}
if (process.argv.includes("--write")) {
  const p = join(KIT, "objects.js");
  let src = await readFile(p, "utf8");
  for (const [id, tone] of Object.entries(tones)) {
    src = src.replace(new RegExp(`("id": "${id}", "role": "[^"]+", "tone": )"[^"]+"`), `$1"${tone}"`);
  }
  await writeFile(p, src);
}
