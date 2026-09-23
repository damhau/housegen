// housekit/finishes — textured surface finishes for interiors (Poly Haven textures, CC0, 1k JPEG:
// colour, OpenGL normal, roughness), laid at their real size: a texture repeats every
// `size` metres, so geometry must carry UVs in metres (`metricUV`; ExtrudeGeometry caps already do).
//
//   await loadFinishes()          once, before building (the page is ready only when they are loaded)
//   finishMaterial(name, opts)    the material, or null when that finish is not available
//
// Textures are fetched with kit/scripts/fetch_models.mjs into kit/assets/polyhaven/textures/<id>/.
// The runtime loads them before buildScene (house.js mat.* then return textured materials) and runs
// metricUVs over the built scene.

import * as THREE from "three";

// colour: false keeps the caller's colour and takes only the relief (normal + roughness) of the texture
export const TEXTURES = {
  "oak-floor": { id: "laminate_floor_02" },
  "stone-tile": { id: "marble_01" },
  plaster: { id: "white_stucco", normalScale: 0.35, tint: "#ffffff" },
  oak: { id: "oak_veneer_03" },
  "fabric-melange": { id: "jogging_melange" },
  "fabric-wool": { id: "poly_wool_herringbone" },
  linen: { id: "terlenka", colour: false },
  "cotton-weave": { id: "cotton_jersey", colour: false },
  // exterior (house.js mat.*): match = tinted so the texture's average colour is the colour asked for
  render: { id: "white_stucco", match: true, normalScale: 0.5 },
  concrete: { id: "plaster_grey_04", match: true },
  "roof-tiles": { id: "roof_tiles", match: true },
  grass: { id: "leafy_grass", match: true },
  gravel: { id: "gravel_floor_02", match: true },
  asphalt: { id: "clean_asphalt", match: true },
  "wood-planks": { id: "wood_planks", match: true },
};

// absolute: renderer snapshots (kit/versions/<name>/) share the working copy's assets
const BASE = new URL("/kit/assets/polyhaven/textures/", import.meta.url);
const _sets = new Map(); // name -> { map, normalMap, roughnessMap, size }
const _mats = new Map();

async function loadSet(name) {
  const spec = TEXTURES[name];
  const dir = new URL(`${spec.id}/`, BASE);
  const meta = await fetch(new URL("meta.json", dir)).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  if (!meta) return null; // not fetched: callers fall back to plain materials
  const loader = new THREE.TextureLoader();
  const get = async (suffix, srgb) => {
    const t = await loader.loadAsync(new URL(`${spec.id}_${suffix}_1k.jpg`, dir).href);
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    t.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
    t.anisotropy = 8;
    return t;
  };
  const [map, normalMap, roughnessMap] = await Promise.all([
    spec.colour === false ? null : get("diff", true), get("nor_gl", false), get("rough", false),
  ]);
  return { map, normalMap, roughnessMap, size: meta.size, mean: map ? meanColour(map.image) : null };
}

/** Average colour of an image, linear RGB (to tint a texture to a given average colour). */
function meanColour(image) {
  if (typeof document === "undefined" || !image) return null;
  const c = document.createElement("canvas");
  c.width = c.height = 32;
  const ctx = c.getContext("2d");
  ctx.drawImage(image, 0, 0, 32, 32);
  const px = ctx.getImageData(0, 0, 32, 32).data;
  const sum = [0, 0, 0];
  const lin = new THREE.Color();
  for (let i = 0; i < px.length; i += 4) {
    lin.setRGB(px[i] / 255, px[i + 1] / 255, px[i + 2] / 255, THREE.SRGBColorSpace);
    sum[0] += lin.r; sum[1] += lin.g; sum[2] += lin.b;
  }
  const n = px.length / 4;
  return new THREE.Color(sum[0] / n, sum[1] / n, sum[2] / n);
}

/** Load the texture sets (all, or the names given). Missing ones are skipped silently. */
export async function loadFinishes(names = Object.keys(TEXTURES)) {
  await Promise.all(names.map(async (n) => {
    if (!_sets.has(n)) _sets.set(n, await loadSet(n));
  }));
}

/**
 * The textured material of a finish. `color` tints it (or is the colour itself for relief-only
 * finishes), `scale` multiplies the texture's real size, `rotate` turns the pattern (radians).
 * Returns null when the finish is not loaded.
 */
export function finishMaterial(name, { color, scale = 1, rotate = 0, roughness = 1 } = {}) {
  const set = _sets.get(name);
  if (!set) return null;
  const spec = TEXTURES[name];
  const key = `${name}:${color}:${scale}:${rotate}:${roughness}`;
  if (_mats.has(key)) return _mats.get(key);
  const [sx, sy] = set.size;
  const tex = (t) => {
    if (!t) return null;
    const c = t.clone();
    c.repeat.set(1 / (sx * scale), 1 / (sy * scale));
    c.rotation = rotate;
    c.needsUpdate = true;
    return c;
  };
  let tint = new THREE.Color(color ?? spec.tint ?? "#ffffff");
  if (spec.match && set.mean && color) {
    // colour-matched: texture × tint averages to `color` (in linear light)
    tint = new THREE.Color(tint.r / Math.max(set.mean.r, 1e-3), tint.g / Math.max(set.mean.g, 1e-3), tint.b / Math.max(set.mean.b, 1e-3));
  }
  const m = new THREE.MeshStandardMaterial({
    color: tint,
    map: tex(set.map),
    normalMap: tex(set.normalMap),
    normalScale: new THREE.Vector2(spec.normalScale ?? 1, spec.normalScale ?? 1),
    roughnessMap: tex(set.roughnessMap),
    roughness,
  });
  m.userData.finish = name;
  // the colour the surface should read as (the tint above is colour ÷ texture average): what code
  // that derives colours from a material (the presentation look's meadow from the lawn) must use
  m.userData.baseColor = new THREE.Color(color ?? spec.tint ?? "#ffffff");
  _mats.set(key, m);
  return m;
}

/**
 * After a scene is built: give every mesh that wears a textured finish UVs in metres, except shapes
 * that already have them (extruded and flat shapes: walls, slabs, patches).
 */
export function metricUVs(root) {
  root.traverse((o) => {
    if (!o.isMesh || o.isInstancedMesh) return;
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    if (!mats.some((m) => m?.userData?.finish)) return;
    const g = o.geometry;
    // extruded and flat shapes already have UVs in metres, except for roof tiles, which must follow the slope
    const tiles = mats.some((m) => m?.userData?.finish === "roof-tiles");
    if (g.userData.metricUV || (!tiles && (g.type === "ExtrudeGeometry" || g.type === "ShapeGeometry"))) return;
    metricUV(g);
  });
}

/**
 * Replace a geometry's UVs by a projection in metres (local space) on each face's own plane: u runs
 * level along the face, v up its slope (rows of roof tiles follow the eaves, wall textures stand
 * upright); a floor or a top takes x and z. For boxes, rounded boxes, custom roofs, terrain, paths.
 */
export function metricUV(geo) {
  const pos = geo.attributes.position, nor = geo.attributes.normal;
  if (!nor) geo.computeVertexNormals();
  const n = geo.attributes.normal;
  const uv = new Float32Array(pos.count * 2);
  const N = new THREE.Vector3(), T = new THREE.Vector3(), B = new THREE.Vector3(), P = new THREE.Vector3();
  for (let i = 0; i < pos.count; i++) {
    N.set(n.getX(i), n.getY(i), n.getZ(i));
    P.set(pos.getX(i), pos.getY(i), pos.getZ(i));
    if (Math.abs(N.y) > 0.97) {
      uv[i * 2] = P.x;
      uv[i * 2 + 1] = P.z;
      continue;
    }
    T.set(N.z, 0, -N.x).normalize(); // level, along the face
    B.crossVectors(N, T).normalize(); // up the face
    uv[i * 2] = P.dot(T);
    uv[i * 2 + 1] = P.dot(B);
  }
  geo.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
  geo.userData.metricUV = true;
  return geo;
}

export default { TEXTURES, loadFinishes, finishMaterial, metricUV, metricUVs };
