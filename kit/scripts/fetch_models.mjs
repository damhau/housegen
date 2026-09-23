// Download the scanned furniture models listed in furnish.js (Poly Haven, CC0, glTF with 1k textures)
// into kit/assets/polyhaven/<id>/, and the surface textures listed in finishes.js (1k JPEG colour,
// normal, roughness + meta.json with their real size) into kit/assets/polyhaven/textures/<id>/.
// Run from kit/: node scripts/fetch_models.mjs
import { mkdir, writeFile, access } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { MODELS } from "../furnish.js";
import { TEXTURES } from "../finishes.js";

const OUT = join(dirname(fileURLToPath(import.meta.url)), "..", "assets", "polyhaven");
const UA = { "User-Agent": "housegen-kit" };
const get = async (url) => {
  const r = await fetch(url, { headers: UA });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return Buffer.from(await r.arrayBuffer());
};

for (const id of new Set(Object.values(MODELS).filter((m) => m.id).map((m) => m.id))) {
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

for (const id of new Set(Object.values(TEXTURES).map((t) => t.id))) {
  const dir = join(OUT, "textures", id);
  if (await access(join(dir, "meta.json")).then(() => true, () => false)) { console.log(`${id}: present`); continue; }
  const files = JSON.parse((await get(`https://api.polyhaven.com/files/${id}`)).toString());
  const info = JSON.parse((await get(`https://api.polyhaven.com/info/${id}`)).toString());
  await mkdir(dir, { recursive: true });
  for (const [key, suffix] of [["Diffuse", "diff"], ["nor_gl", "nor_gl"], ["Rough", "rough"]]) {
    await writeFile(join(dir, `${id}_${suffix}_1k.jpg`), await get(files[key]["1k"].jpg.url));
  }
  // real size of one tile of the texture, metres (the API gives millimetres)
  const size = (info.dimensions ?? [1000, 1000]).map((mm) => Math.round(mm) / 1000);
  await writeFile(join(dir, "meta.json"), JSON.stringify({ id, size, source: `https://polyhaven.com/a/${id}`, license: "CC0" }));
  console.log(`${id}: fetched, ${size.join(" × ")} m`);
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
}
