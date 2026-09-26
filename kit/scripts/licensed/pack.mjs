// Split a bought furniture pack (one GLB holding every object, as Fab / Sketchfab convert a pack)
// into one small file per object for the data volume: meshopt geometry, WebP textures at 1024 px,
// heavy meshes simplified, then masked (furnish.js maskLicensed) so that what the server hands out
// is not a model anyone can open. Output: kit/assets/licensed/<pack>, versioned. The license allows
// that only while the repository and the image stay private; the source pack itself is not versioned.
//
//   node scripts/licensed/pack.mjs <pack.glb> --list [--scale 0.01]        objects, sizes (m), triangles
//   node scripts/licensed/pack.mjs <pack.glb> <out_dir> [--scale 0.01] [object ...]
//                                                                          <out_dir>/<slug>.glbx + catalog.json
//
// --scale: to metres, for a pack modelled in centimetres (0.01).
//
// Run from kit/ (the glTF tools are dev dependencies: not in the image).
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { Document, NodeIO, getBounds } from "@gltf-transform/core";
import { ALL_EXTENSIONS } from "@gltf-transform/extensions";
import { copyToDocument, dedup, meshopt, prune, simplify, textureCompress, weld } from "@gltf-transform/functions";
import { MeshoptDecoder, MeshoptEncoder, MeshoptSimplifier } from "meshoptimizer";
import sharp from "sharp";
import { maskLicensed } from "../../furnish.js";

const MAX_TRIANGLES = 60000; // above this an object is simplified down to it
const TEXTURE_SIZE = 1024;

const argv = process.argv.slice(2);
const at = argv.indexOf("--scale");
const SCALE = at >= 0 ? Number(argv.splice(at, 2)[1]) : 1;
const [src, out, ...only] = argv;
if (!src || !out) {
  console.error("usage: node scripts/licensed/pack.mjs <pack.glb> (--list | <out_dir> [object ...])");
  process.exit(2);
}

await MeshoptDecoder.ready;
await MeshoptEncoder.ready;
await MeshoptSimplifier.ready;
const io = new NodeIO()
  .registerExtensions(ALL_EXTENSIONS)
  .registerDependencies({ "meshopt.decoder": MeshoptDecoder, "meshopt.encoder": MeshoptEncoder });
const doc = await io.read(src);

/** The pack's objects: below the chain of single-child wrappers a converter puts on top. */
function objects(document) {
  let level = document.getRoot().getDefaultScene()?.listChildren() ?? document.getRoot().listScenes()[0].listChildren();
  while (level.length === 1 && !level[0].getMesh() && level[0].listChildren().length > 0) level = level[0].listChildren();
  return level;
}

function triangles(node) {
  let n = 0;
  node.traverse((o) => {
    for (const p of o.getMesh()?.listPrimitives() ?? []) {
      const count = p.getIndices()?.getCount() ?? p.getAttribute("POSITION")?.getCount() ?? 0;
      n += p.getMode() === 4 ? count / 3 : 0;
    }
  });
  return Math.round(n);
}

const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "object";
const r2 = (v) => Math.round(v * 100) / 100;
/** The object's world matrix, in metres (column-major: the scale applies to rows 0-2). */
function toMetres(m) {
  return m.map((v, i) => (i % 4 === 3 ? v : v * SCALE));
}

const found = objects(doc).map((node) => {
  const { min, max } = getBounds(node);
  return { node, name: node.getName(), size: [0, 1, 2].map((i) => r2((max[i] - min[i]) * SCALE)), triangles: triangles(node) };
});
// same names in a pack: numbered
const seen = new Map();
for (const o of found) {
  const s = slug(o.name);
  seen.set(s, (seen.get(s) ?? 0) + 1);
  o.slug = seen.get(s) > 1 ? `${s}-${seen.get(s)}` : s;
}

if (out === "--list") {
  for (const o of found) console.log(`${o.slug.padEnd(40)} ${o.size.join(" × ").padEnd(22)} m  ${String(o.triangles).padStart(8)} tris  (${o.name})`);
  console.log(`${found.length} objects`);
  process.exit(0);
}

await mkdir(out, { recursive: true });
const catalog = {};
for (const o of found.filter((x) => only.length === 0 || only.includes(x.slug))) {
  const part = new Document();
  part.createBuffer();
  const copied = copyToDocument(part, doc, [o.node]).get(o.node);
  // where the pack laid the object out does not matter (model() centres it), its scale does
  copied.setMatrix(toMetres(o.node.getWorldMatrix()));
  part.createScene(o.slug).addChild(copied);
  const steps = [prune(), dedup()];
  if (o.triangles > MAX_TRIANGLES) {
    steps.push(weld(), simplify({ simplifier: MeshoptSimplifier, ratio: MAX_TRIANGLES / o.triangles, error: 0.002 }));
  }
  steps.push(
    textureCompress({ encoder: sharp, targetFormat: "webp", resize: [TEXTURE_SIZE, TEXTURE_SIZE] }),
    meshopt({ encoder: MeshoptEncoder, level: "medium" }),
    prune(),
  );
  await part.transform(...steps);
  const glb = await io.writeBinary(part);
  await writeFile(join(out, `${o.slug}.glbx`), maskLicensed(glb, false));
  catalog[o.slug] = { source: o.name, size: o.size, triangles: triangles(part.getRoot().listScenes()[0].listChildren()[0]), bytes: glb.byteLength };
  console.log(`${o.slug}: ${(glb.byteLength / 1e6).toFixed(2)} MB, ${catalog[o.slug].triangles} tris`);
}
await writeFile(join(out, "catalog.json"), JSON.stringify(catalog, null, 1));
