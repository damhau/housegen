// housekit/finishes — textured surface finishes for interiors (Poly Haven textures, CC0, 1k JPEG:
// colour, OpenGL normal, roughness), laid at their real size: a texture repeats every
// `size` metres, so geometry must carry UVs in metres (`metricUV`; ExtrudeGeometry caps already do).
//
//   await loadFinishes()          once, before building (the page is ready only when they are loaded)
//   finishMaterial(name, opts)    the material, or null when that finish is not available
//
// Textures are fetched with kit/scripts/fetch_models.mjs into kit/assets/polyhaven/textures/<id>/.

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
};

const BASE = new URL("./assets/polyhaven/textures/", import.meta.url);
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
  return { map, normalMap, roughnessMap, size: meta.size };
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
  const m = new THREE.MeshStandardMaterial({
    color: color ?? spec.tint ?? "#ffffff",
    map: tex(set.map),
    normalMap: tex(set.normalMap),
    normalScale: new THREE.Vector2(spec.normalScale ?? 1, spec.normalScale ?? 1),
    roughnessMap: tex(set.roughnessMap),
    roughness,
  });
  _mats.set(key, m);
  return m;
}

/**
 * Replace a geometry's UVs by a box projection in metres (local space): each face takes the two
 * axes it lies along. For boxes, rounded boxes and merged boxes built by the kit.
 */
export function metricUV(geo) {
  const pos = geo.attributes.position, nor = geo.attributes.normal;
  const uv = new Float32Array(pos.count * 2);
  for (let i = 0; i < pos.count; i++) {
    const nx = Math.abs(nor.getX(i)), ny = Math.abs(nor.getY(i)), nz = Math.abs(nor.getZ(i));
    const x = pos.getX(i), y = pos.getY(i), z = pos.getZ(i);
    const [u, v] = ny >= nx && ny >= nz ? [x, z] : nx >= nz ? [z, y] : [x, y];
    uv[i * 2] = u;
    uv[i * 2 + 1] = v;
  }
  geo.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
  return geo;
}

export default { TEXTURES, loadFinishes, finishMaterial, metricUV };
