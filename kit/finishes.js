// housekit/finishes — textured surface finishes for interiors (Poly Haven textures, CC0, 1k JPEG:
// colour, OpenGL normal, roughness), laid at their real size: a texture repeats every
// `size` metres, so geometry must carry UVs in metres (`metricUV`; ExtrudeGeometry caps already do).
//
//   await loadFinishes()          once, before building: which sets exist (their images load on first use)
//   finishMaterial(name, opts)    the material, or null when that finish is not available
//   await finishesReady()         after building: every material used has its images (and what track() was given)
//
// Textures are fetched with kit/scripts/fetch_models.mjs into kit/assets/polyhaven/textures/<id>/, the
// marble photographs of the marble tiles into kit/assets/ambientcg/<id>/.
// The runtime loads them before buildScene (house.js mat.* then return textured materials) and runs
// metricUVs over the built scene.

import * as THREE from "three";
import { entry } from "./styles.js";

// colour: false keeps the caller's colour and takes only the relief (normal + roughness) of the texture
// grade: a correction of the colour map (#61) so the finishes sit together without new files:
// { saturation, contrast, brightness, hue (degrees) }, 1/1/1/0 = as photographed
export const TEXTURES = {
  "oak-floor": { id: "laminate_floor_02" },
  "stone-tile": { id: "marble_01" },
  // interior floors (#61): the oak no longer orange and uniform at walking distance
  "oak-natural": { id: "laminate_floor_03", grade: { saturation: 0.72, brightness: 1.08, hue: 4 } },
  "oak-washed": { id: "laminate_floor_03", grade: { saturation: 0.42, brightness: 1.3, contrast: 0.92 } },
  "oak-smoked": { id: "wood_floor", grade: { saturation: 0.62, brightness: 0.92, contrast: 1.05 } },
  "oak-herringbone": { id: "herringbone_parquet", grade: { saturation: 0.6, brightness: 1.12 } },
  "porcelain-dark": { id: "granite_tile", grade: { saturation: 0.5, brightness: 1.05 } },
  terrazzo: { id: "terrazzo_tiles", grade: { saturation: 0.3, brightness: 1.65, contrast: 0.85 } },
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
const AMBIENT = new URL("/kit/assets/ambientcg/", import.meta.url);

/**
 * Marble photographs (ambientCG, CC0, the 1k colour map) for tiles, graded to a grey as in the interior pilot:
 * each pixel's value = lift + 0.38 × its luminance, plus `tint` (r, g, b).
 */
export const MARBLES = {
  // near black with dense white veins: lifted to a warm mid grey, the veins softened
  Marble017: { lift: 104, tint: [2, 0, -3] },
  Marble012: { lift: 150, tint: [0, 0, -2] },
};
const MARBLE_GAIN = 0.38;

/** The tiles of tileWalls and of the tiled fronts (wc, bathtub, shower), by name: styles.js "wallTile". */
export const WALL_TILES = {
  white: {}, // tileMaterial's defaults: 30 x 60 glazed white, pale joints
  "marble-grey": { marble: "Marble017", size: [0.6, 1.2], joint: 0.002, jointColor: "#8c8a86", roughness: 0.28 },
};
/** The "tile" floor of a bath or a WC, by name: styles.js "bathFloor". */
export const FLOOR_TILES = {
  tile: { size: [0.6, 0.6], joint: 0.002, color: "#e6e4df", jointColor: "#d2cfc8", roughness: 0.4 }, // light porcelain
  "marble-light": { marble: "Marble012", size: [0.6, 0.6], joint: 0.002, jointColor: "#c9c7c2", roughness: 0.22 },
};

const _mats = new Map();

const _meta = new Map(); // name -> { size } when the set was fetched, null when it was not
const _loading = new Map(); // name -> Promise of the set's textures (started on first use)
const _pending = new Set(); // materials still waiting for their textures

/**
 * A texture from an image URL, decoded off the main thread while it downloads (an ImageBitmap): with
 * an <img>, the browser decodes the JPEG on the main thread when the texture is first drawn (#60: 4.8 s
 * of TestVillaGille's first frame). Flipped at decode time, so it reads as three's TextureLoader's
 * (normal maps included). TextureLoader where createImageBitmap is missing.
 */
export async function loadTexture(url) {
  if (typeof createImageBitmap === "undefined") return new THREE.TextureLoader().loadAsync(url);
  const loader = new THREE.ImageBitmapLoader();
  loader.setOptions({ imageOrientation: "flipY", premultiplyAlpha: "none", colorSpaceConversion: "none" });
  const t = new THREE.Texture(await loader.loadAsync(url));
  t.flipY = false;
  t.needsUpdate = true;
  return t;
}

async function loadTextures(name) {
  const spec = TEXTURES[name];
  const dir = new URL(`${spec.id}/`, BASE);
  const get = async (suffix, srgb) => {
    const t = await loadTexture(new URL(`${spec.id}_${suffix}_1k.jpg`, dir).href);
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    t.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
    t.anisotropy = 8;
    return t;
  };
  const [map, normalMap, roughnessMap] = await Promise.all([
    spec.colour === false ? null : get("diff", true), get("nor_gl", false), get("rough", false),
  ]);
  if (map && spec.grade) await gradeTexture(map, spec.grade);
  return { map, normalMap, roughnessMap, mean: map ? meanColour(map.image) : null };
}

/**
 * Correct a colour texture in place (#61): saturation and hue around each pixel's own luminance,
 * contrast around mid-grey, brightness as a gain, in sRGB values as an image editor does. Once per
 * finish, on the CPU (a 1k map: ~15 ms); the shaders stay the stock ones.
 */
async function gradeTexture(t, { saturation = 1, contrast = 1, brightness = 1, hue = 0 }) {
  if (typeof OffscreenCanvas === "undefined" || !t.image) return;
  const { width: w, height: h } = t.image;
  const c = new OffscreenCanvas(w, h);
  const g = c.getContext("2d", { willReadFrequently: true });
  g.drawImage(t.image, 0, 0);
  const img = g.getImageData(0, 0, w, h), px = img.data;
  // hue: a rotation about the grey axis (the usual RGB hue-rotate matrix)
  const a = THREE.MathUtils.degToRad(hue), cos = Math.cos(a), sin = Math.sin(a);
  const m = [
    0.213 + cos * 0.787 - sin * 0.213, 0.715 - cos * 0.715 - sin * 0.715, 0.072 - cos * 0.072 + sin * 0.928,
    0.213 - cos * 0.213 + sin * 0.143, 0.715 + cos * 0.285 + sin * 0.14, 0.072 - cos * 0.072 - sin * 0.283,
    0.213 - cos * 0.213 - sin * 0.787, 0.715 - cos * 0.715 + sin * 0.715, 0.072 + cos * 0.928 + sin * 0.072,
  ];
  for (let i = 0; i < px.length; i += 4) {
    let r = px[i], gg = px[i + 1], b = px[i + 2];
    if (hue) [r, gg, b] = [m[0] * r + m[1] * gg + m[2] * b, m[3] * r + m[4] * gg + m[5] * b, m[6] * r + m[7] * gg + m[8] * b];
    const y = 0.2126 * r + 0.7152 * gg + 0.0722 * b;
    r = y + (r - y) * saturation; gg = y + (gg - y) * saturation; b = y + (b - y) * saturation;
    px[i] = ((r - 128) * contrast + 128) * brightness;
    px[i + 1] = ((gg - 128) * contrast + 128) * brightness;
    px[i + 2] = ((b - 128) * contrast + 128) * brightness;
  }
  g.putImageData(img, 0, 0);
  t.image = await createImageBitmap(c);
  t.needsUpdate = true;
}

function texturesOf(name) {
  if (!_loading.has(name)) _loading.set(name, loadTextures(name).catch(() => null));
  return _loading.get(name);
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

/**
 * Learn which texture sets are there (their small meta.json: size). The images themselves are
 * fetched only for the finishes a scene uses, on first use; `finishesReady()` waits for them.
 */
export async function loadFinishes(names = Object.keys(TEXTURES)) {
  await Promise.all(names.map(async (n) => {
    if (_meta.has(n)) return;
    const dir = new URL(`${TEXTURES[n].id}/`, BASE);
    _meta.set(n, await fetch(new URL("meta.json", dir)).then((r) => (r.ok ? r.json() : null)).catch(() => null));
  }));
}

/** Resolves when every textured material handed out so far has its images (and every piece its model, see track). */
export async function finishesReady() {
  while (_pending.size) await Promise.all([..._pending]);
}

/**
 * Something a piece still has to load before the first picture (furnish.js: the real sanitary ware
 * that replaces a parametric WC or basin): finishesReady() waits for it too. A failure is the
 * caller's (the piece keeps its parametric shape); here it only stops being pending.
 */
export function track(promise) {
  const p = Promise.resolve(promise).catch(() => {}).finally(() => _pending.delete(p));
  _pending.add(p);
  return promise;
}

/**
 * The textured material of a finish. `color` tints it (or is the colour itself for relief-only
 * finishes), `scale` multiplies the texture's real size, `rotate` turns the pattern (radians).
 * Returns null when the finish is not loaded.
 */
export function finishMaterial(name, { color, scale = 1, rotate = 0, roughness = 1 } = {}) {
  const meta = _meta.get(name);
  if (!meta) return null;
  const spec = TEXTURES[name];
  const key = `${name}:${color}:${scale}:${rotate}:${roughness}`;
  if (_mats.has(key)) return _mats.get(key);
  const [sx, sy] = meta.size;
  const wanted = new THREE.Color(color ?? spec.tint ?? "#ffffff");
  // plain in the colour asked for until the images arrive (the runtime waits for them)
  const m = new THREE.MeshStandardMaterial({
    color: wanted.clone(),
    normalScale: new THREE.Vector2(spec.normalScale ?? 1, spec.normalScale ?? 1),
    roughness,
  });
  m.userData.finish = name;
  // the colour the surface should read as (with a texture the colour below is a tint): what code
  // that derives colours from a material (the presentation look's meadow from the lawn) must use
  m.userData.baseColor = wanted.clone();
  _mats.set(key, m);
  const tex = (t) => {
    if (!t) return null;
    const c = t.clone();
    c.repeat.set(1 / (sx * scale), 1 / (sy * scale));
    c.rotation = rotate;
    c.needsUpdate = true;
    return c;
  };
  const ready = texturesOf(name).then((set) => {
    if (!set) return;
    Object.assign(m, { map: tex(set.map), normalMap: tex(set.normalMap), roughnessMap: tex(set.roughnessMap) });
    if (spec.match && set.mean && color) {
      // colour-matched: texture × tint averages to `color` (in linear light)
      m.color.setRGB(wanted.r / Math.max(set.mean.r, 1e-3), wanted.g / Math.max(set.mean.g, 1e-3), wanted.b / Math.max(set.mean.b, 1e-3));
    }
    m.needsUpdate = true;
  }).finally(() => _pending.delete(ready));
  _pending.add(ready);
  return m;
}

const _tiles = new Map();

/**
 * Ceramic tiles drawn on a canvas (nothing to fetch): one tile of `size` [w, h] metres per
 * texture repeat, `joint` metres of grout between them, glossy tiles and matt grout (roughness
 * map), a groove at every joint (normal map). Needs UVs in metres like the other finishes.
 * Without a canvas (Node), the plain tile colour. `marble` (a MARBLES name): marble tiles instead (marbleTiles).
 */
export function tileMaterial({ size = [0.3, 0.6], joint = 0.003, color = "#f3f2ee", jointColor = "#c9c3b9", roughness = 0.2, marble } = {}) {
  if (marble) return marbleTiles({ marble, size, joint, jointColor, roughness });
  const key = JSON.stringify([size, joint, color, jointColor, roughness]);
  if (_tiles.has(key)) return _tiles.get(key);
  const m = new THREE.MeshStandardMaterial({ color, roughness });
  m.userData.finish = "tiles";
  m.userData.baseColor = new THREE.Color(color);
  _tiles.set(key, m);
  if (typeof document === "undefined") return m;
  // ~1000 px per metre, at most 1024 px a side
  const k = Math.min(1000, 1024 / Math.max(...size));
  const W = Math.max(8, Math.round(size[0] * k)), H = Math.max(8, Math.round(size[1] * k));
  const j = Math.max(1, Math.round(joint * k / 2)); // half a joint on each side of the tile
  const bevel = Math.max(1, Math.round(0.0015 * k));
  const canvas = (draw) => {
    const c = document.createElement("canvas");
    c.width = W;
    c.height = H;
    draw(c.getContext("2d"));
    const t = new THREE.CanvasTexture(c);
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    t.repeat.set(1 / size[0], 1 / size[1]);
    t.anisotropy = 4;
    return t;
  };
  const map = canvas((g) => {
    g.fillStyle = jointColor;
    g.fillRect(0, 0, W, H);
    // a glazed tile is not a flat colour: a faint glaze cloud over the tile colour
    g.fillStyle = color;
    g.fillRect(j, j, W - 2 * j, H - 2 * j);
    for (let i = 0; i < 24; i++) {
      g.fillStyle = `rgba(${Math.random() < 0.5 ? "0,0,0" : "255,255,255"},0.005)`;
      g.beginPath();
      g.ellipse(Math.random() * W, Math.random() * H, W * (0.1 + Math.random() * 0.3), H * (0.1 + Math.random() * 0.3), 0, 0, Math.PI * 2);
      g.fill();
    }
  });
  map.colorSpace = THREE.SRGBColorSpace;
  // roughness in the green channel (× material.roughness = 1): matt grout, the glaze `roughness`
  const roughnessMap = canvas((g) => {
    g.fillStyle = "rgb(0,255,0)";
    g.fillRect(0, 0, W, H);
    g.fillStyle = `rgb(0,${Math.round(255 * roughness)},0)`;
    g.fillRect(j, j, W - 2 * j, H - 2 * j);
  });
  // tangent-space normals (OpenGL, +y up): the glaze rounds off into the joint
  const normalMap = canvas((g) => {
    g.fillStyle = "rgb(128,128,255)";
    g.fillRect(0, 0, W, H);
    const edge = (r, gr, x, y, w, h) => { g.fillStyle = `rgb(${r},${gr},225)`; g.fillRect(x, y, w, h); };
    edge(40, 128, j, j, bevel, H - 2 * j); // left edge leans left
    edge(216, 128, W - j - bevel, j, bevel, H - 2 * j);
    edge(128, 40, j, j, W - 2 * j, bevel); // canvas top = v 0 (flipY off): the tile's bottom edge
    edge(128, 216, j, H - j - bevel, W - 2 * j, bevel);
  });
  map.flipY = roughnessMap.flipY = normalMap.flipY = false;
  Object.assign(m, { map, roughnessMap, normalMap, color: new THREE.Color("#ffffff"), roughness: 1 });
  m.userData.tile = { size, joint };
  m.needsUpdate = true;
  return m;
}

/**
 * Marble tiles: a 2 x 2 tile canvas cut from the marble photograph (each tile its own window of the slab, two of
 * them turned) so that neighbours do not repeat, graded (MARBLES), the joints drawn: the interior pilot's. One tile
 * of `size` [w, h] metres; UVs in metres. Plain grey without a canvas (Node) or until the photograph is in (tracked:
 * finishesReady waits for it).
 */
function marbleTiles({ marble, size, joint, jointColor, roughness }) {
  const key = JSON.stringify([marble, size, joint, jointColor, roughness]);
  if (_tiles.has(key)) return _tiles.get(key);
  const spec = MARBLES[marble];
  const grade = (y) => { const v = spec.lift + y * MARBLE_GAIN; return [v + spec.tint[0], v + spec.tint[1], v + spec.tint[2]]; };
  const plain = new THREE.Color().setRGB(...grade(140).map((c) => c / 255), THREE.SRGBColorSpace);
  const m = new THREE.MeshStandardMaterial({ name: "marble tiles", color: plain, roughness });
  m.userData.finish = "tiles";
  m.userData.baseColor = plain.clone();
  m.userData.tile = { size, joint, marble };
  _tiles.set(key, m);
  if (typeof document === "undefined") return m;
  const url = new URL(`${marble}/${marble}_1K-JPG_Color.jpg`, AMBIENT).href;
  track(fetch(url)
    .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.blob(); })
    .then((blob) => createImageBitmap(blob))
    .then((img) => {
      const k = 512 / size[0], W = Math.round(2 * size[0] * k), H = Math.round(2 * size[1] * k);
      const canvas = document.createElement("canvas");
      canvas.width = W;
      canvas.height = H;
      const c = canvas.getContext("2d", { willReadFrequently: true });
      const tw = W / 2, th = H / 2, j = Math.max(1, Math.round(joint * k / 2));
      c.fillStyle = jointColor;
      c.fillRect(0, 0, W, H);
      const sw = img.width * 0.45, sh = img.height * 0.9;
      [[0, 0, false], [0.55, 0.1, true], [0.3, 0.05, true], [0.5, 0, false]].forEach(([u, v, turn], i) => {
        const x = (i % 2) * tw, y = Math.floor(i / 2) * th;
        c.save();
        c.beginPath();
        c.rect(x + j, y + j, tw - 2 * j, th - 2 * j);
        c.clip();
        if (turn) {
          c.translate(x + tw, y + th);
          c.rotate(Math.PI);
          c.drawImage(img, u * img.width, v * img.height, sw, sh, 0, 0, tw, th);
        } else c.drawImage(img, u * img.width, v * img.height, sw, sh, x, y, tw, th);
        c.restore();
      });
      const px = c.getImageData(0, 0, W, H);
      const d = px.data;
      for (let i = 0; i < d.length; i += 4) {
        [d[i], d[i + 1], d[i + 2]] = grade(0.3 * d[i] + 0.59 * d[i + 1] + 0.11 * d[i + 2]);
      }
      c.putImageData(px, 0, 0);
      const map = new THREE.CanvasTexture(canvas);
      map.colorSpace = THREE.SRGBColorSpace;
      map.wrapS = map.wrapT = THREE.RepeatWrapping;
      map.repeat.set(1 / (2 * size[0]), 1 / (2 * size[1]));
      map.anisotropy = 8;
      Object.assign(m, { map, color: new THREE.Color("#ffffff") });
      m.needsUpdate = true;
    })
    .catch((e) => console.warn(`housekit: ${marble} not loaded (${e?.message ?? e}): plain marble tiles`)));
  return m;
}

/**
 * Wall tiles as a piece asks for them: an object is the tiles themselves (tileMaterial's options), a name one of
 * WALL_TILES, anything else (true, nothing) the house style's "wallTile" (for `room`, a room's name).
 */
export function wallTileMaterial(tiles, { room } = {}) {
  if (tiles && typeof tiles === "object") return tileMaterial(tiles);
  return tileMaterial(entry(WALL_TILES, "wallTile", { piece: typeof tiles === "string" ? tiles : undefined, room }));
}

/** The floor tiles of a bath or a WC whose floor is "tile": the house style's "bathFloor" for `room`. */
export const bathFloorMaterial = (room) => tileMaterial(entry(FLOOR_TILES, "bathFloor", { room }));

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

export default { TEXTURES, MARBLES, WALL_TILES, FLOOR_TILES, loadFinishes, finishesReady, track, finishMaterial, tileMaterial,
  wallTileMaterial, metricUV, metricUVs };
