// housekit/furnish — furniture for interiors: a few scanned models (Poly Haven, CC0) and parametric
// pieces built here (sofa, bed, dining set, wardrobe, kitchen run, bathroom), in one light
// Scandinavian style: light oak, white lacquer, grey and sand textiles.
//
// Every piece has its origin at the bottom centre and FACES +z (its back at z = -depth/2).
// `onWall(room, edge, at, piece)` puts a piece with its back against a wall of a room polygon,
// facing into the room; `place(piece, [x, z], rotationY)` puts it anywhere.
// Pieces are tagged userData.kind = "furniture" with their name and footprint (w × d).

import * as THREE from "three";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { finishMaterial, metricUV } from "./finishes.js";

// --------------------------------------------------------------------------
// Materials
// --------------------------------------------------------------------------

const _mats = new Map();
function m(key, make) {
  if (!_mats.has(key)) _mats.set(key, make());
  return _mats.get(key);
}
// textured when the finishes are loaded (finishes.loadFinishes), plain colours otherwise
export const finish = {
  // the veneer is nearly white: a honey tint gives the light oak of Scandinavian furniture
  oak: (tint = "#dcb98c") => finishMaterial("oak", { color: tint, roughness: 0.9 })
    ?? m(`oak:${tint}`, () => new THREE.MeshStandardMaterial({ color: "#d2b48a", roughness: 0.62 })),
  lacquer: (color = "#f2f1ed") => m(`lacquer:${color}`, () => new THREE.MeshStandardMaterial({ color, roughness: 0.42 })),
  // the mélange's own colour (a light grey) tinted by `color`
  fabric: (color = "#9a9c97") => finishMaterial("fabric-melange", { color: tintOf(color) })
    ?? m(`fabric:${color}`, () => new THREE.MeshStandardMaterial({ color, roughness: 1 })),
  wool: (tint) => finishMaterial("fabric-wool", { color: tint })
    ?? m(`wool:${tint}`, () => new THREE.MeshStandardMaterial({ color: tint ?? "#8f9a90", roughness: 1 })),
  linen: (tint = "#f5f3ee") => finishMaterial("linen", { color: tint, scale: 1.5 })
    ?? m(`linen:${tint}`, () => new THREE.MeshStandardMaterial({ color: tint ?? "#f3f1ea", roughness: 0.95 })),
  weave: (color = "#d8d2c5") => finishMaterial("cotton-weave", { color, scale: 2 })
    ?? m(`weave:${color}`, () => new THREE.MeshStandardMaterial({ color, roughness: 1 })),
  metal: (color = "#2d2f31") => m(`metal:${color}`, () => new THREE.MeshStandardMaterial({ color, roughness: 0.4, metalness: 0.7 })),
  steel: () => m("steel", () => new THREE.MeshStandardMaterial({ color: "#c9ccce", roughness: 0.25, metalness: 0.9 })),
  ceramic: () => m("ceramic", () => new THREE.MeshStandardMaterial({ color: "#fbfbf9", roughness: 0.15 })),
  stone: (color = "#e4e1db") => m(`stone:${color}`, () => new THREE.MeshStandardMaterial({ color, roughness: 0.35 })),
  glass: () => m("glass", () => new THREE.MeshStandardMaterial({ color: "#dfe9ec", roughness: 0.05, transparent: true, opacity: 0.25 })),
  black: () => m("black", () => new THREE.MeshStandardMaterial({ color: "#1d1e1f", roughness: 0.3 })),
};

// --------------------------------------------------------------------------
// Building blocks
// --------------------------------------------------------------------------

/** A tint that brings the light-grey mélange texture to about `color`. */
function tintOf(color) {
  const c = new THREE.Color(color);
  c.multiplyScalar(1.45);
  return `#${c.getHexString()}`;
}

function shade(mesh) {
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

/** Box by its size and the position of its bottom centre; `r` rounds the edges (m). */
function block(g, [w, h, d], [x, y, z], material, r = 0) {
  const geo = metricUV(r > 0 ? new RoundedBoxGeometry(w, h, d, 2, Math.min(r, w / 2, h / 2, d / 2)) : new THREE.BoxGeometry(w, h, d));
  const mesh = shade(new THREE.Mesh(geo, material));
  mesh.position.set(x, y + h / 2, z);
  g.add(mesh);
  return mesh;
}

function leg(g, x, z, h, material, r = 0.02) {
  const mesh = shade(new THREE.Mesh(metricUV(new THREE.CylinderGeometry(r * 0.8, r, h, 12)), material));
  mesh.position.set(x, h / 2, z);
  g.add(mesh);
}

function piece(g, name, w, d, extra = {}) {
  g.userData = { kind: "furniture", name, footprint: [w, d], ...extra };
  return g;
}

// --------------------------------------------------------------------------
// Parametric pieces
// --------------------------------------------------------------------------

/** Three-seat (or two-seat below 1.9 m) sofa, fabric on light oak legs. */
export function sofa({ width = 2.2, depth = 0.92, color = "#a3a49e" } = {}) {
  const g = new THREE.Group();
  const fab = finish.fabric(color), oak = finish.oak();
  const arm = 0.16, legH = 0.12, baseH = 0.2, seatH = 0.16, backD = 0.2;
  for (const x of [-width / 2 + 0.08, width / 2 - 0.08]) for (const z of [-depth / 2 + 0.08, depth / 2 - 0.08]) leg(g, x, z, legH, oak, 0.018);
  block(g, [width, baseH, depth], [0, legH, 0], fab, 0.03);
  for (const s of [-1, 1]) block(g, [arm, 0.36, depth], [s * (width / 2 - arm / 2), legH, 0], fab, 0.05);
  const inner = width - 2 * arm, n = width < 1.9 ? 2 : 3, cw = inner / n;
  for (let i = 0; i < n; i++) {
    const x = -inner / 2 + cw * (i + 0.5);
    block(g, [cw - 0.01, seatH, depth - backD - 0.02], [x, legH + baseH, backD / 2 + 0.01], fab, 0.06);
    const back = block(g, [cw - 0.02, 0.44, 0.17], [x, legH + baseH, -depth / 2 + backD / 2 + 0.01], fab, 0.07);
    back.rotation.x = -0.12;
  }
  block(g, [width, 0.3, backD * 0.6], [0, legH + baseH, -depth / 2 + backD * 0.3], fab, 0.04);
  return piece(g, "sofa", width, depth);
}

/** Double bed: oak frame and headboard, mattress, duvet folded back, two pillows, a throw. */
export function bed({ width = 1.6, length = 2.05, throwColor } = {}) {
  const g = new THREE.Group();
  const oak = finish.oak(), linen = finish.linen();
  const frameH = 0.3, mattH = 0.2;
  block(g, [width + 0.06, frameH, length + 0.04], [0, 0, 0.02], oak, 0.015);
  block(g, [width + 0.06, 0.95, 0.06], [0, 0, -length / 2 - 0.01], oak, 0.015);
  block(g, [width - 0.02, mattH, length - 0.04], [0, frameH, 0.02], linen, 0.05);
  // duvet over the foot two thirds, hanging a little over the sides and the foot
  const duvetL = length * 0.72, duvetZ = length / 2 - duvetL / 2 + 0.04, top = frameH + mattH;
  block(g, [width + 0.1, 0.07, duvetL], [0, top - 0.02, duvetZ], linen, 0.035);
  for (const s of [-1, 1]) block(g, [0.03, 0.22, duvetL], [s * (width / 2 + 0.05), top - 0.2, duvetZ], linen, 0.012);
  block(g, [width + 0.1, 0.22, 0.03], [0, top - 0.2, duvetZ + duvetL / 2], linen, 0.012);
  // a folded throw across the foot
  block(g, [width + 0.12, 0.05, 0.45], [0, top + 0.045, length / 2 - 0.3], finish.wool(throwColor), 0.02);
  // two pillows leaning on the headboard
  for (const s of [-1, 1]) {
    const p = block(g, [width / 2 - 0.08, 0.13, 0.5], [s * width / 4, top - 0.03, -length / 2 + 0.24], linen, 0.045);
    p.rotation.x = -0.4;
  }
  return piece(g, "bed", width + 0.06, length + 0.08);
}

/** Bedside table: lacquered box with a drawer line, oak legs. */
export function nightstand({ width = 0.45, depth = 0.38 } = {}) {
  const g = new THREE.Group();
  for (const x of [-width / 2 + 0.04, width / 2 - 0.04]) for (const z of [-depth / 2 + 0.04, depth / 2 - 0.04]) leg(g, x, z, 0.2, finish.oak(), 0.015);
  block(g, [width, 0.32, depth], [0, 0.2, 0], finish.lacquer(), 0.01);
  block(g, [width - 0.04, 0.004, 0.005], [0, 0.4, depth / 2], finish.black());
  return piece(g, "nightstand", width, depth);
}

/** Scandinavian dining chair: oak legs and frame, a curved back rail. */
export function chair({ color } = {}) {
  const g = new THREE.Group();
  const oak = finish.oak(color);
  const w = 0.46, d = 0.5, seat = 0.45;
  for (const x of [-w / 2 + 0.03, w / 2 - 0.03]) {
    leg(g, x, d / 2 - 0.03, seat, oak, 0.017);
    leg(g, x, -d / 2 + 0.03, 0.8, oak, 0.017);
  }
  block(g, [w, 0.035, d], [0, seat - 0.035, 0], oak, 0.012);
  block(g, [w - 0.02, 0.13, 0.022], [0, 0.64, -d / 2 + 0.03], oak, 0.008);
  block(g, [w - 0.06, 0.03, 0.02], [0, 0.52, -d / 2 + 0.03], oak, 0.006);
  return piece(g, "chair", w, d);
}

/** Dining table in light oak with chairs on its long sides (and ends when `ends`). */
export function diningSet({ length = 1.8, width = 0.9, seats = 6, ends = false } = {}) {
  const g = new THREE.Group();
  const oak = finish.oak();
  block(g, [width, 0.035, length], [0, 0.715, 0], oak, 0.008);
  for (const x of [-width / 2 + 0.06, width / 2 - 0.06]) for (const z of [-length / 2 + 0.06, length / 2 - 0.06]) leg(g, x, z, 0.715, oak, 0.025);
  const perSide = Math.floor((seats - (ends ? 2 : 0)) / 2);
  for (const s of [-1, 1]) {
    for (let i = 0; i < perSide; i++) {
      const c = chair();
      c.position.set(s * (width / 2 + 0.12), 0, -length / 2 + (length / perSide) * (i + 0.5));
      c.rotation.y = s * Math.PI / 2 * -1; // facing the table
      g.add(c);
    }
    if (ends) {
      const c = chair();
      c.position.set(0, 0, s * (length / 2 + 0.12));
      c.rotation.y = s > 0 ? Math.PI : 0;
      g.add(c);
    }
  }
  return piece(g, "diningSet", width + 1.0, length + (ends ? 1.0 : 0.2));
}

/** Built-in wardrobe: white doors of ~50 cm with oak pulls, full height. */
export function wardrobe({ width = 1.2, depth = 0.6, height = 2.3 } = {}) {
  const g = new THREE.Group();
  const white = finish.lacquer();
  block(g, [width, 0.08, depth - 0.05], [0, 0, -0.025], finish.lacquer("#e6e4de"));
  block(g, [width, height - 0.08, depth], [0, 0.08, 0], white, 0.004);
  const n = Math.max(1, Math.round(width / 0.5)), dw = width / n;
  for (let i = 1; i < n; i++) block(g, [0.004, height - 0.1, 0.004], [-width / 2 + dw * i, 0.09, depth / 2], finish.lacquer("#cfccc5"));
  for (let i = 0; i < n; i++) {
    const x = -width / 2 + dw * (i + 0.5) + (i % 2 ? -1 : 1) * (dw / 2 - 0.06);
    block(g, [0.02, 0.3, 0.03], [x, 0.95, depth / 2 + 0.012], finish.oak(), 0.005);
  }
  return piece(g, "wardrobe", width, depth);
}

/**
 * Kitchen run against a wall: base units with a worktop, plinth, optional wall units and tall units.
 *   tall: [{ at, width }]   tall units (fridge / oven column), `at` = centre measured from the left end
 *   sink / hob: centre positions from the left end (m), or null
 *   worktop: "oak" or a stone colour
 */
export function kitchenRun({ length = 3.6, depth = 0.62, tall = [], sink = null, hob = null, upper = true, front = "#f2f1ed", worktop = "#d9d5cc" } = {}) {
  const g = new THREE.Group();
  const white = finish.lacquer(front), gap = finish.lacquer("#cdcac3");
  const top = worktop === "oak" ? finish.oak() : finish.stone(worktop);
  const x0 = -length / 2;
  const inTall = (x) => tall.some((t) => Math.abs(x - t.at) < t.width / 2 - 1e-6);
  block(g, [length, 0.1, depth - 0.06], [0, 0, -0.03], finish.lacquer("#dcd9d2"));
  // base units between the tall ones, doors of ~60 cm
  const spans = [];
  let s = 0;
  for (const t of [...tall].sort((a, b) => a.at - b.at)) {
    if (t.at - t.width / 2 > s + 0.05) spans.push([s, t.at - t.width / 2]);
    s = t.at + t.width / 2;
  }
  if (length > s + 0.05) spans.push([s, length]);
  for (const [a, b] of spans) {
    block(g, [b - a, 0.76, depth - 0.02], [x0 + (a + b) / 2, 0.1, -0.01], white, 0.003);
    block(g, [b - a + 0.004, 0.04, depth + 0.02], [x0 + (a + b) / 2, 0.86, 0.01], top, 0.004);
    const n = Math.max(1, Math.round((b - a) / 0.6));
    for (let i = 1; i < n; i++) block(g, [0.004, 0.74, 0.004], [x0 + a + ((b - a) / n) * i, 0.11, depth / 2 - 0.01], gap);
    block(g, [b - a, 0.004, 0.004], [x0 + (a + b) / 2, 0.66, depth / 2 - 0.01], gap);
  }
  for (const t of tall) {
    block(g, [t.width - 0.004, 2.14, depth], [x0 + t.at, 0.1, 0], white, 0.003);
    block(g, [t.width - 0.02, 0.004, 0.004], [x0 + t.at, 0.95, depth / 2], gap);
  }
  if (sink !== null && !inTall(sink)) {
    block(g, [0.6, 0.012, 0.44], [x0 + sink, 0.9, 0.02], finish.steel(), 0.01);
    const tap = shade(new THREE.Mesh(new THREE.TorusGeometry(0.1, 0.012, 8, 16, Math.PI), finish.steel()));
    tap.position.set(x0 + sink, 0.98, -depth / 2 + 0.1);
    tap.rotation.y = Math.PI / 2;
    g.add(tap);
    leg(g, x0 + sink, -depth / 2 + 0.1, 0.98, finish.steel(), 0.012);
  }
  if (hob !== null && !inTall(hob)) block(g, [0.6, 0.008, 0.52], [x0 + hob, 0.9, 0.02], finish.black());
  if (upper) {
    for (const [a, b] of spans) {
      block(g, [b - a, 0.7, 0.35], [x0 + (a + b) / 2, 1.5, -depth / 2 + 0.175], white, 0.003);
      const n = Math.max(1, Math.round((b - a) / 0.6));
      for (let i = 1; i < n; i++) block(g, [0.004, 0.68, 0.004], [x0 + a + ((b - a) / n) * i, 1.51, -depth / 2 + 0.35], gap);
    }
  }
  return piece(g, "kitchenRun", length, depth);
}

/** Wall-hung WC with a concealed cistern box. */
export function wc() {
  const g = new THREE.Group();
  const c = finish.ceramic();
  block(g, [0.5, 1.1, 0.16], [0, 0, -0.27], finish.lacquer("#f4f3f0"), 0.01);
  block(g, [0.36, 0.3, 0.5], [0, 0.18, 0.02], c, 0.12);
  block(g, [0.37, 0.025, 0.46], [0, 0.48, 0.03], c, 0.012);
  block(g, [0.18, 0.012, 0.08], [0, 1.1, -0.27], finish.steel(), 0.005);
  return piece(g, "wc", 0.5, 0.7);
}

/** Washbasin on an oak vanity with a mirror above. */
export function basin({ width = 0.6, depth = 0.46, vanity = true, mirror = true } = {}) {
  const g = new THREE.Group();
  if (vanity) block(g, [width, 0.4, depth - 0.02], [0, 0.45, -0.01], finish.oak(), 0.01);
  block(g, [width, 0.12, depth], [0, 0.85, 0], finish.ceramic(), 0.04);
  leg(g, 0, -depth / 2 + 0.06, 1.15, finish.steel(), 0.012);
  if (mirror) block(g, [width, 0.7, 0.02], [0, 1.2, -depth / 2], finish.glass(), 0.01);
  return piece(g, "basin", width, depth);
}

/** Built-in bathtub with a tiled front. */
export function bathtub({ length = 1.7, width = 0.75 } = {}) {
  const g = new THREE.Group();
  block(g, [length, 0.56, width], [0, 0, 0], finish.lacquer("#eceae5"), 0.01);
  block(g, [length - 0.12, 0.02, width - 0.12], [0, 0.55, 0], finish.ceramic(), 0.01);
  return piece(g, "bathtub", length, width);
}

/** Flush ceiling light (opal disc), for halls, bathrooms and low rooms. Its origin is on the ceiling. */
export function ceilingLight({ diameter = 0.32 } = {}) {
  const g = new THREE.Group();
  const disc = new THREE.Mesh(new THREE.CylinderGeometry(diameter / 2, diameter / 2 - 0.02, 0.06, 32),
    m("opal", () => new THREE.MeshStandardMaterial({ color: "#ffffff", emissive: "#fff3e0", emissiveIntensity: 0.9, roughness: 0.4 })));
  disc.position.y = -0.03;
  g.add(disc);
  return piece(g, "ceilingLight", diameter, diameter, { hang: true, height: 0.06 });
}

/** Flat woven rug. */
export function rug({ width = 2.0, depth = 1.4, color = "#d8d2c5" } = {}) {
  const g = new THREE.Group();
  const r = block(g, [width, 0.012, depth], [0, 0, 0], finish.weave(color));
  r.castShadow = false;
  return piece(g, "rug", width, depth, { flat: true });
}

// --------------------------------------------------------------------------
// Scanned models (Poly Haven, CC0), served from kit/assets/polyhaven/<id>/<id>.gltf
// --------------------------------------------------------------------------

export const MODELS = {
  "armchair-oak-leather": { id: "modern_arm_chair_01", rotate: 0 },
  "side-table-oak": { id: "side_table_01", rotate: 0 },
  "cube-shelf-oak": { id: "wooden_display_shelves_01", rotate: -Math.PI / 2 }, // faces +x in the file
  "pendant-globe": { id: "modern_ceiling_lamp_01", rotate: 0, hang: true },
  "plant-large": { id: "potted_plant_02", rotate: 0 },
  "plant-small": { id: "potted_plant_04", rotate: 0 },
  "vase-white": { id: "ceramic_vase_01", rotate: 0 },
  "coffee-table-oak": { id: "modern_coffee_table_01", rotate: Math.PI / 2 }, // long side along x
  "sideboard-walnut": { id: "modern_wooden_cabinet", rotate: 0 },
  // Sketchfab, CC Attribution: the author must be credited where the scene is shown (credits()).
  // Fetched with a SKETCHFAB_TOKEN by kit/scripts/fetch_models.mjs into kit/assets/sketchfab/<name>.glb.
  "bed-messy-grey": { fallback: "bed", file: "sketchfab/messy-bed.glb", uid: "a2b2645701c94fa49e65661806219c6b", rotate: 0,
    credit: "“Messy bed 2.0” by thethieme, CC BY 4.0" },
  "bed-soho-white": { fallback: "bed", file: "sketchfab/soho-bed.glb", uid: "97e361e8beda4112ac5b1b5bcd388cdf", rotate: 0,
    credit: "“Soho bed” by BertO, CC BY 4.0" },
  "sofa-grey-cushions": { fallback: "sofa", file: "sketchfab/modern-sofa.glb", uid: "ac92f6e97eaa43c4ad6cb8f7c65ac43f", rotate: 0,
    credit: "“Modern Sofa” by 3dimentionalben, CC BY 4.0" },
  "sofa-modular-l": { fallback: "sofa", file: "sketchfab/modular-sofa.glb", uid: "c7a0c35f4f0b49f8b4fea273f9014001", rotate: 0, scale: 0.01,
    credit: "“Sofa” by GreenG, CC BY 4.0" },
};
// absolute: renderer snapshots (kit/versions/<name>/) share the working copy's assets
const ASSETS = new URL("/kit/assets/", import.meta.url);
const _used = new Set();
// shared with the runtime (it sends the page's credits to the app) without importing this module
globalThis.__housekitCredits = () => credits();

/** Credit lines of the attribution-licensed models this page has loaded (for the page's credits). */
export function credits() {
  return [..._used].map((n) => MODELS[n].credit).filter(Boolean);
}
const _loaded = new Map();

/** A scanned model by its catalogue name, bottom-centred and facing +z (async: it is loaded once). */
export async function model(name) {
  const spec = MODELS[name];
  if (!spec) throw new Error(`unknown model ${name}. Known: ${Object.keys(MODELS).join(", ")}`);
  const file = spec.file ?? `polyhaven/${spec.id}/${spec.id}.gltf`;
  if (!_loaded.has(file)) _loaded.set(file, new GLTFLoader().loadAsync(new URL(file, ASSETS).href).catch(() => null));
  const gltf = await _loaded.get(file);
  if (!gltf) {
    // not fetched (a build without the Sketchfab token): the parametric piece of the same kind
    console.warn(`housekit: model ${name} (${file}) not available, using ${spec.fallback ?? "nothing"}`);
    const make = { sofa, bed }[spec.fallback];
    return make ? make() : piece(new THREE.Group(), name, 0, 0);
  }
  _used.add(name);
  const inner = gltf.scene.clone(true);
  inner.rotation.y = spec.rotate;
  if (spec.scale) inner.scale.setScalar(spec.scale); // a model made in centimetres
  inner.traverse((o) => { if (o.isMesh) shade(o); });
  const wrap = new THREE.Group();
  wrap.add(inner);
  wrap.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(inner);
  const c = box.getCenter(new THREE.Vector3());
  // bottom centre on the origin; a hanging lamp has its top on the origin instead
  inner.position.set(-c.x, spec.hang ? -box.max.y : -box.min.y, -c.z);
  const size = box.getSize(new THREE.Vector3());
  return piece(wrap, name, size.x, size.z, { hang: !!spec.hang, height: size.y });
}

// --------------------------------------------------------------------------
// Placement
// --------------------------------------------------------------------------

/** Put a piece at [x, z] on a floor at `y`, turned by `rotationY` (0 = facing +z / south). */
export function place(p, [x, z], rotationY = 0, y = 0) {
  p.position.set(x, y, z);
  p.rotation.y = rotationY;
  return p;
}

/**
 * Put a piece with its back against edge `edge` of a room polygon (from polygon[edge] to the next
 * point), its centre `at` metres along the edge from its first point, facing into the room.
 * `gap` leaves room behind it (skirting), `out` pushes it further into the room.
 */
export function onWall(room, edge, at, p, { y = 0, gap = 0.02, out = 0 } = {}) {
  const poly = room.polygon ?? room;
  const a = poly[edge], b = poly[(edge + 1) % poly.length];
  const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
  const ux = (b[0] - a[0]) / len, uz = (b[1] - a[1]) / len;
  let area = 0;
  for (let i = 0; i < poly.length; i++) {
    const [x1, z1] = poly[i], [x2, z2] = poly[(i + 1) % poly.length];
    area += x1 * z2 - x2 * z1;
  }
  // inward normal: left of the edge direction when the polygon turns that way
  const s = area > 0 ? 1 : -1;
  const nx = -uz * s, nz = ux * s;
  const d = (p.userData.footprint?.[1] ?? 0) / 2 + gap + out;
  p.position.set(a[0] + ux * at + nx * d, y, a[1] + uz * at + nz * d);
  p.rotation.y = Math.atan2(nx, nz);
  return p;
}

export default {
  finish, sofa, bed, nightstand, chair, diningSet, wardrobe, kitchenRun, wc, basin, bathtub, rug, ceilingLight,
  MODELS, model, credits, place, onWall,
};
