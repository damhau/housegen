// Download the scanned furniture models listed in furnish.js (Poly Haven, CC0, glTF with 1k textures)
// into kit/assets/polyhaven/<id>/, and the surface textures listed in finishes.js (1k JPEG colour,
// normal, roughness + meta.json with their real size) into kit/assets/polyhaven/textures/<id>/, and the
// marbles of the styles' tiles (finishes.js MARBLES, ambientCG, CC0: the 1K colour map) and the ambientCG texture sets
// of finishes.js (`acg`: the neighbours' roof covers) into kit/assets/ambientcg/<id>/.
// Run from kit/: node scripts/fetch_models.mjs
import { mkdir, writeFile, access } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { unzipSync } from "three/addons/libs/fflate.module.js";
import { MODELS } from "../furnish.js";
import { MARBLES, TEXTURES } from "../finishes.js";
import { OBJECTS } from "../objects.js";

const OUT = join(dirname(fileURLToPath(import.meta.url)), "..", "assets", "polyhaven");
const UA = { "User-Agent": "housegen-kit" };
const get = async (url) => {
  const r = await fetch(url, { headers: UA });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return Buffer.from(await r.arrayBuffer());
};

// the catalogue's models and the plants the decor's pots take (objects.js, `ph`)
for (const id of new Set([...Object.values(MODELS).filter((m) => m.id).map((m) => m.id), ...OBJECTS.filter((o) => o.ph).map((o) => o.id)])) {
  const main = join(OUT, id, `${id}.gltf`);
  if (await access(main).then(() => true, () => false)) { console.log(`${id}: present`); continue; }
  const files = JSON.parse((await get(`https://api.polyhaven.com/files/${id}`)).toString());
  const g = files.gltf["1k"].gltf;
  for (const [path, inc] of Object.entries(g.include ?? {})) {
    await mkdir(dirname(join(OUT, id, path)), { recursive: true });
    await writeFile(join(OUT, id, path), await get(inc.url));
  }
  await mkdir(dirname(main), { recursive: true });
  await writeFile(main, await get(g.url));
  console.log(`${id}: fetched`);
}

// a JPEG re-encoded at `quality` (finishes.js `jpeg`) when sharp is there (the kit's dev dependency), else as it came
const sharp = await import("sharp").then((m) => m.default, () => null);
const lighter = async (buf, quality) => (quality && sharp ? sharp(buf).jpeg({ quality, mozjpeg: true }).toBuffer() : buf);
const specsOf = (key, id) => Object.values(TEXTURES).filter((t) => t[key] === id);

for (const id of new Set(Object.values(TEXTURES).filter((t) => t.id).map((t) => t.id))) {
  const dir = join(OUT, "textures", id);
  if (await access(join(dir, "meta.json")).then(() => true, () => false)) { console.log(`${id}: present`); continue; }
  const files = JSON.parse((await get(`https://api.polyhaven.com/files/${id}`)).toString());
  const info = JSON.parse((await get(`https://api.polyhaven.com/info/${id}`)).toString());
  await mkdir(dir, { recursive: true });
  const specs = specsOf("id", id), rough = specs.some((t) => t.rough !== false), quality = Math.max(0, ...specs.map((t) => t.jpeg ?? 0));
  for (const [key, suffix] of [["Diffuse", "diff"], ["nor_gl", "nor_gl"], ...(rough ? [["Rough", "rough"]] : [])]) {
    await writeFile(join(dir, `${id}_${suffix}_1k.jpg`), await lighter(await get(files[key]["1k"].jpg.url), quality));
  }
  // real size of one tile of the texture, metres (the API gives millimetres)
  const size = (info.dimensions ?? [1000, 1000]).map((mm) => Math.round(mm) / 1000);
  await writeFile(join(dir, "meta.json"), JSON.stringify({ id, size, source: `https://polyhaven.com/a/${id}`, license: "CC0" }));
  console.log(`${id}: fetched, ${size.join(" × ")} m`);
}

for (const id of Object.keys(MARBLES)) {
  const dir = join(OUT, "..", "ambientcg", id), name = `${id}_1K-JPG_Color.jpg`;
  if (await access(join(dir, "meta.json")).then(() => true, () => false)) { console.log(`${id}: present`); continue; }
  const zip = unzipSync(new Uint8Array(await get(`https://ambientcg.com/get?file=${id}_1K-JPG.zip`)), { filter: (f) => f.name === name });
  if (!zip[name]) throw new Error(`${id}: no ${name} in its archive`);
  await mkdir(dir, { recursive: true });
  await writeFile(join(dir, name), zip[name]);
  await writeFile(join(dir, "meta.json"), JSON.stringify({ id, source: `https://ambientcg.com/view?id=${id}`, license: "CC0" }));
  console.log(`${id}: fetched`);
}

// the ambientCG texture sets (finishes.js `acg`): colour, OpenGL normal and, unless rough: false, roughness
for (const id of new Set(Object.values(TEXTURES).filter((t) => t.acg).map((t) => t.acg))) {
  const dir = join(OUT, "..", "ambientcg", id);
  if (await access(join(dir, "meta.json")).then(() => true, () => false)) { console.log(`${id}: present`); continue; }
  const specs = specsOf("acg", id), quality = Math.max(0, ...specs.map((t) => t.jpeg ?? 0));
  const maps = ["Color", "NormalGL", ...(specs.some((t) => t.rough !== false) ? ["Roughness"] : [])].map((m) => `${id}_1K-JPG_${m}.jpg`);
  const zip = unzipSync(new Uint8Array(await get(`https://ambientcg.com/get?file=${id}_1K-JPG.zip`)), { filter: (f) => maps.includes(f.name) });
  await mkdir(dir, { recursive: true });
  for (const name of maps) {
    if (!zip[name]) throw new Error(`${id}: no ${name} in its archive`);
    await writeFile(join(dir, name), await lighter(Buffer.from(zip[name]), quality));
  }
  await writeFile(join(dir, "meta.json"), JSON.stringify({ id, size: specs[0].size, source: `https://ambientcg.com/view?id=${id}`, license: "CC0" }));
  console.log(`${id}: fetched`);
}

/**
 * Sketchfab models come with 2k–4k textures and heavy meshes (a sofa: 11 MB): compress them for the
 * web (meshopt geometry, WebP textures at 1024 px; furnish.js loads meshopt) — about 9x lighter with
 * no visible difference. Needs Node 22+ for the CLI; when it cannot run, the original is kept.
 */
function compress(file) {
  try {
    execFileSync("npx", ["--yes", "@gltf-transform/cli@4", "optimize", file, file,
      "--texture-compress", "webp", "--texture-size", "1024", "--compress", "meshopt"], { stdio: "pipe" });
    console.log(`${file}: compressed`);
  } catch (e) {
    console.log(`${file}: kept uncompressed (${String(e.message).split("\n")[0].slice(0, 120)})`);
  }
}

// Sketchfab models (CC Attribution): the download API needs an account token
const token = process.env.SKETCHFAB_TOKEN;
for (const m of Object.values(MODELS).filter((m) => m.uid)) {
  const file = join(OUT, "..", m.file);
  if (await access(file).then(() => true, () => false)) { console.log(`${m.file}: present`); continue; }
  if (!token) { console.log(`${m.file}: missing (set SKETCHFAB_TOKEN to fetch it)`); continue; }
  const r = await fetch(`https://api.sketchfab.com/v3/models/${m.uid}/download`, { headers: { Authorization: `Token ${token}` } });
  const links = await r.json();
  if (!links.glb) { console.log(`${m.file}: no GLB offered`); continue; }
  await mkdir(dirname(file), { recursive: true });
  await writeFile(file, await get(links.glb.url));
  console.log(`${m.file}: fetched`);
  compress(file);
}
