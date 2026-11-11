// Download the decor models listed in objects.js from Amazon Berkeley Objects (CC BY 4.0, a public bucket, no
// login) into kit/assets/objects/<id>.glb, compressed for the web: meshopt geometry, heavy meshes simplified within
// 0.1 % of their size, WebP textures at 512 px, 1024 for furniture (the originals carry 4k maps: 0.5–4 MB each, about
// 0.15 MB after, 0.3 MB for furniture).
// The plants the pots take (Poly Haven, `ph`) are compressed the same way from what fetch_models.mjs fetched.
// Run from kit/, after fetch_models.mjs: node scripts/fetch_objects.mjs
//   node scripts/fetch_objects.mjs --made <dir>   compresses the pieces made in the repo (<dir>/<id>.glb, the archviz
//                                                 spike's made/stems) into kit/assets/made/, which is versioned
import { access, mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { NodeIO } from "@gltf-transform/core";
import { ALL_EXTENSIONS } from "@gltf-transform/extensions";
import { dedup, flatten, instance, join as joinMeshes, meshopt, prune, resample, simplify, textureCompress, weld } from "@gltf-transform/functions";
import { MeshoptEncoder, MeshoptSimplifier } from "meshoptimizer";
import sharp from "sharp";
import { OBJECTS } from "../objects.js";

const ASSETS = join(dirname(fileURLToPath(import.meta.url)), "..", "assets");
const ABO = "https://amazon-berkeley-objects.s3.amazonaws.com/3dmodels/original/";
const PARALLEL = 6;

await MeshoptEncoder.ready;
await MeshoptSimplifier.ready;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ "meshopt.encoder": MeshoptEncoder });

// keepNodes: the plants' node names say which part is a leaf and which variant (furnish.js reads them), so their
// nodes are neither flattened nor joined
// furniture fills a listing photo: its textures at 1024 px
const LARGE = new Set(["sofa", "armchair", "dining-chair", "dining-table", "coffee-table", "bed", "nightstand", "desk"]);

async function squeeze(bytes, dst, { keepNodes = false, px = 512 } = {}) {
  const doc = await io.readBinary(new Uint8Array(bytes));
  await doc.transform(
    dedup(), ...(keepNodes ? [] : [instance(), flatten(), joinMeshes()]), weld(),
    simplify({ simplifier: MeshoptSimplifier, ratio: 0.5, error: 0.001 }),
    resample(), prune(),
    textureCompress({ encoder: sharp, targetFormat: "webp", resize: [px, px] }),
    meshopt({ encoder: MeshoptEncoder, level: "medium" }),
  );
  await mkdir(dirname(dst), { recursive: true });
  await writeFile(dst, await io.writeBinary(doc));
}

const exists = (f) => access(f).then(() => true, () => false);

async function pool(items, work) {
  const queue = [...items];
  await Promise.all(Array.from({ length: PARALLEL }, async () => {
    for (let it = queue.shift(); it; it = queue.shift()) await work(it);
  }));
}

const made = process.argv.indexOf("--made");
if (made > 0) {
  const src = process.argv[made + 1];
  await pool(OBJECTS.filter((o) => !o.abo), async (o) => {
    await squeeze(await readFile(join(src, `${o.id}.glb`)), join(ASSETS, "made", `${o.id}.glb`));
    console.log(`made/${o.id}: compressed`);
  });
} else {
  let failed = 0;
  await pool(OBJECTS.filter((o) => o.abo), async (o) => {
    const dst = join(ASSETS, "objects", `${o.id}.glb`);
    if (await exists(dst)) return;
    try {
      const r = await fetch(ABO + o.abo);
      if (!r.ok) throw new Error(`${r.status}`);
      await squeeze(await r.arrayBuffer(), dst, { px: LARGE.has(o.role) ? 1024 : 512 });
      console.log(`objects/${o.id}: fetched`);
    } catch (e) {
      // a scene asking for this role takes the next model of it
      failed++;
      console.log(`objects/${o.id}: not fetched (${String(e.message).slice(0, 120)})`);
    }
  });
  console.log(`objects: ${OBJECTS.filter((o) => o.abo).length - failed} of ${OBJECTS.filter((o) => o.abo).length} present`);
  // the pots' plants: Poly Haven glTF at 1k (fetch_models.mjs, run first), 1–6 MB each, squeezed the same way
  for (const o of OBJECTS.filter((o) => o.ph)) {
    const dst = join(ASSETS, "objects", `${o.id}.glb`);
    const src = join(ASSETS, "polyhaven", o.id, `${o.id}.gltf`);
    if (await exists(dst) || !(await exists(src))) continue;
    const doc = await io.read(src);
    await squeeze(await io.writeBinary(doc), dst, { keepNodes: true });
    console.log(`objects/${o.id}: compressed from polyhaven/`);
  }
}
