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
import { MeshoptDecoder } from "three/addons/libs/meshopt_decoder.module.js";
import { finishMaterial, metricUV, tileMaterial } from "./finishes.js";

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
  // a silvered mirror: it shows the environment (the room's light), not the view through it
  mirror: () => m("mirror", () => new THREE.MeshStandardMaterial({ color: "#e9eeee", roughness: 0.02, metalness: 1 })),
  // shower screens: clear, a faint green edge tint, a little sheen
  showerGlass: () => m("showerGlass", () => new THREE.MeshStandardMaterial({ color: "#e8f2ee", roughness: 0.03, metalness: 0.1, transparent: true, opacity: 0.18, side: THREE.DoubleSide })),
  chrome: () => m("chrome", () => new THREE.MeshStandardMaterial({ color: "#e6e8ea", roughness: 0.08, metalness: 1 })),
  towel: (color = "#ece8e0") => finishMaterial("cotton-weave", { color, scale: 0.6 })
    ?? m(`towel:${color}`, () => new THREE.MeshStandardMaterial({ color, roughness: 1 })),
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
 * Kitchen run along a wall, `length` long, fronts `front`, worktop `worktop` ("oak" or a stone
 * colour): base units of ~60 cm (handleless: dark joints, a grip rail under the worktop, a recessed
 * plinth), tall units `tall: [{ at, width, oven }]` (centre from the left end; `oven` puts a
 * built-in oven at eye height), an undermount sink and an induction hob at `sink` / `hob` (centres
 * from the left end), wall units above (`upper`), a splashback between them (`splash`: tile options
 * { size, color }, a colour, or null) and a hood over the hob: built into the wall units, or with
 * `hood: "chimney"` a canopy and chimney up to `ceiling` (m above the floor), for a run with no
 * wall units or an island.
 */
export function kitchenRun({ length = 3.6, depth = 0.62, tall = [], sink = null, hob = null, upper = true, front = "#f2f1ed",
  worktop = "#d9d5cc", splash = { size: [0.3, 0.1], color: "#f4f3ef" }, hood = true, ceiling = 2.4 } = {}) {
  const g = new THREE.Group();
  const white = finish.lacquer(front), gap = finish.lacquer("#8f8b84");
  const top = worktop === "oak" ? finish.oak() : finish.stone(worktop);
  const x0 = -length / 2;
  const inTall = (x) => tall.some((t) => Math.abs(x - t.at) < t.width / 2 - 1e-6);
  // plinth, 5 cm back and dark: the units seem to float over their own shadow
  block(g, [length, 0.1, depth - 0.1], [0, 0, -0.05], finish.lacquer("#6f6c66"));
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
    for (let i = 1; i < n; i++) block(g, [0.006, 0.74, 0.004], [x0 + a + ((b - a) / n) * i, 0.11, depth / 2 - 0.01], gap);
    block(g, [b - a, 0.004, 0.004], [x0 + (a + b) / 2, 0.66, depth / 2 - 0.01], gap);
    // the grip rail: a dark recess under the worktop
    block(g, [b - a - 0.01, 0.025, 0.006], [x0 + (a + b) / 2, 0.83, depth / 2 - 0.012], gap);
  }
  for (const t of tall) {
    block(g, [t.width - 0.004, 2.14, depth], [x0 + t.at, 0.1, 0], white, 0.003);
    block(g, [t.width - 0.02, 0.004, 0.004], [x0 + t.at, 0.95, depth / 2], gap);
    if (t.oven) {
      // a built-in oven: black glass, a steel handle, the control strip above
      const w = Math.min(0.56, t.width - 0.04);
      block(g, [w, 0.56, 0.01], [x0 + t.at, 0.96, depth / 2 + 0.002], finish.black(), 0.004);
      block(g, [w, 0.07, 0.01], [x0 + t.at, 1.53, depth / 2 + 0.002], finish.black(), 0.003);
      block(g, [w - 0.1, 0.018, 0.025], [x0 + t.at, 1.43, depth / 2 + 0.02], finish.steel(), 0.006);
    }
  }
  if (sink !== null && !inTall(sink)) {
    block(g, [0.6, 0.012, 0.44], [x0 + sink, 0.9, 0.02], finish.steel(), 0.01);
    const tap = shade(new THREE.Mesh(new THREE.TorusGeometry(0.1, 0.012, 8, 16, Math.PI), finish.steel()));
    tap.position.set(x0 + sink, 0.98, -depth / 2 + 0.1);
    tap.rotation.y = Math.PI / 2;
    g.add(tap);
    leg(g, x0 + sink, -depth / 2 + 0.1, 0.98, finish.steel(), 0.012);
  }
  if (hob !== null && !inTall(hob)) {
    block(g, [0.6, 0.008, 0.52], [x0 + hob, 0.9, 0.02], finish.black());
    // induction zones: faint rings in the glass
    const ring = m("hobRing", () => new THREE.MeshStandardMaterial({ color: "#55585b", roughness: 0.3 }));
    for (const [dx, dz, r] of [[-0.15, -0.11, 0.09], [0.15, -0.11, 0.07], [-0.15, 0.14, 0.07], [0.15, 0.14, 0.09]]) {
      const t = new THREE.Mesh(new THREE.TorusGeometry(r, 0.0025, 4, 40), ring);
      t.rotation.x = Math.PI / 2;
      t.position.set(x0 + hob + dx, 0.9085, 0.02 + dz);
      g.add(t);
    }
  }
  if (upper) {
    for (const [a, b] of spans) {
      block(g, [b - a, 0.7, 0.35], [x0 + (a + b) / 2, 1.5, -depth / 2 + 0.175], white, 0.003);
      const n = Math.max(1, Math.round((b - a) / 0.6));
      for (let i = 1; i < n; i++) block(g, [0.006, 0.68, 0.004], [x0 + a + ((b - a) / n) * i, 1.51, -depth / 2 + 0.35], gap);
    }
    if (hob !== null && hood && hood !== "chimney" && !inTall(hob)) {
      // a flat hood built into the wall unit: a steel lip under it
      block(g, [0.6, 0.03, 0.3], [x0 + hob, 1.47, -depth / 2 + 0.17], finish.metal("#b7babc"), 0.004);
    }
  }
  if (hob !== null && hood === "chimney" && !inTall(hob)) {
    const steel = finish.metal("#b7babc");
    block(g, [0.9, 0.06, 0.5], [x0 + hob, 1.55, -depth / 2 + 0.25], steel, 0.006);
    block(g, [0.28, Math.max(0.1, ceiling - 1.61), 0.24], [x0 + hob, 1.61, -depth / 2 + 0.14], steel, 0.004);
  }
  if (splash && upper) {
    const mat = typeof splash === "object" ? tileMaterial(splash) : finish.stone(splash);
    for (const [a, b] of spans) {
      const sp = block(g, [b - a, 0.6, 0.008], [x0 + (a + b) / 2, 0.9, -depth / 2 + 0.004], mat);
      if (typeof splash === "object") metricUV(sp.geometry);
    }
  }
  return piece(g, "kitchenRun", length, depth, { sink, hob });
}

/** A plan outline with round corners (rx along x, rz along z): for bowls, basins, trays. */
function roundedOutline(w, d, r) {
  const sh = new THREE.Shape(), x = w / 2, z = d / 2;
  r = Math.min(r, x, z);
  sh.moveTo(-x + r, -z);
  sh.lineTo(x - r, -z);
  sh.quadraticCurveTo(x, -z, x, -z + r);
  sh.lineTo(x, z - r);
  sh.quadraticCurveTo(x, z, x - r, z);
  sh.lineTo(-x + r, z);
  sh.quadraticCurveTo(-x, z, -x, z - r);
  sh.lineTo(-x, -z + r);
  sh.quadraticCurveTo(-x, -z, -x + r, -z);
  return sh;
}

/** A solid of that outline, `h` high, its bottom at `y`, bevelled all round. */
function roundedSolid(g, w, d, h, r, y, z, material, bevel = 0.012) {
  const geo = new THREE.ExtrudeGeometry(roundedOutline(w - 2 * bevel, d - 2 * bevel, r), {
    depth: Math.max(0.001, h - 2 * bevel), bevelEnabled: true, bevelThickness: bevel, bevelSize: bevel, bevelSegments: 3, curveSegments: 10 });
  geo.rotateX(-Math.PI / 2); // outline in x/z, extruded up
  geo.translate(0, bevel, 0);
  const mesh = shade(new THREE.Mesh(geo, material));
  mesh.position.set(0, y, z);
  g.add(mesh);
  return mesh;
}

/**
 * A ceramic bowl `w` x `d`, its rim at `top`, `depth` deep: walls `rim` thick all the way down, a
 * floor and a drain. The inside is open: whatever carries it must stay below top - depth.
 */
function sunkenBowl(g, w, d, top, depth, rim, material, z = 0) {
  const y = top - depth;
  block(g, [w, depth, rim], [0, y, z - d / 2 + rim / 2], material, 0.01);
  block(g, [w, depth, rim], [0, y, z + d / 2 - rim / 2], material, 0.01);
  block(g, [rim, depth, d - 2 * rim], [-w / 2 + rim / 2, y, z], material, 0.01);
  block(g, [rim, depth, d - 2 * rim], [w / 2 - rim / 2, y, z], material, 0.01);
  block(g, [w - 2 * rim, 0.02, d - 2 * rim], [0, y, z], material, 0.008);
  const drain = shade(new THREE.Mesh(new THREE.CylinderGeometry(0.022, 0.022, 0.004, 24), finish.chrome()));
  drain.position.set(0, y + 0.021, z);
  g.add(drain);
}

/** A mixer tap on the wall or the deck: body, spout reaching `reach` forward. */
function tap(g, x, y, z, reach = 0.14) {
  const c = finish.chrome();
  block(g, [0.05, 0.05, 0.05], [x, y, z], c, 0.01);
  block(g, [0.022, 0.022, reach], [x, y + 0.03, z + reach / 2], c, 0.008);
  block(g, [0.03, 0.012, 0.012], [x, y + 0.055, z], c, 0.004); // lever
}

/**
 * Wall-hung WC: the bowl (rounded, tapering to the front) with seat and lid, on a tiled or lacquered
 * box that hides the cistern (`boxWidth` wide: 0 for none, e.g. when the wall is already built out),
 * the flush plate on it.
 */
export function wc({ boxWidth = 0.5, boxHeight = 1.1, tiles } = {}) {
  const g = new THREE.Group();
  const c = finish.ceramic();
  if (boxWidth > 0) {
    const box = block(g, [boxWidth, boxHeight, 0.18], [0, 0, -0.26], tiles ? tileMaterial(tiles) : finish.lacquer("#f4f3f0"), 0.004);
    if (tiles) metricUV(box.geometry);
    block(g, [0.24, 0.16, 0.012], [0, boxHeight - 0.28, -0.166], finish.chrome(), 0.004);
  }
  // bowl: 36 x 54, hung 40 cm above the floor, narrower at the front
  const bowl = roundedSolid(g, 0.36, 0.52, 0.32, 0.16, 0.08, 0.05, c, 0.02);
  bowl.scale.set(1, 1, 1);
  roundedSolid(g, 0.37, 0.5, 0.025, 0.17, 0.4, 0.06, c, 0.008); // seat
  roundedSolid(g, 0.36, 0.48, 0.02, 0.17, 0.425, 0.055, finish.ceramic(), 0.008); // lid
  return piece(g, "wc", Math.max(0.37, boxWidth), 0.7);
}

/**
 * Washbasin: a ceramic top with a real bowl on an oak vanity (or wall-hung without), a tap, and
 * a mirror above (silvered, `mirrorHeight` high, its bottom at 1.05 m).
 */
export function basin({ width = 0.6, depth = 0.46, vanity = true, mirror = true, mirrorHeight = 0.7 } = {}) {
  const g = new THREE.Group();
  if (vanity) {
    // under the bowl (its floor at 0.74)
    block(g, [width - 0.01, 0.28, depth - 0.03], [0, 0.46, -0.015], finish.oak(), 0.006);
    block(g, [width - 0.06, 0.004, 0.004], [0, 0.62, depth / 2 - 0.03], finish.black()); // grip line
  }
  sunkenBowl(g, width, depth, 0.86, 0.12, 0.05, finish.ceramic());
  tap(g, 0, 0.86, -depth / 2 + 0.06, Math.min(0.13, depth * 0.3));
  if (mirror) {
    block(g, [width, mirrorHeight, 0.012], [0, 1.05, -depth / 2 - 0.006], finish.mirror(), 0.004);
  }
  return piece(g, "basin", width, depth);
}

/** Built-in bathtub: a white bath (rim, sunken inside) in a tiled or white front panel, a tap on the wall. */
export function bathtub({ length = 1.7, width = 0.75, tiles } = {}) {
  const g = new THREE.Group();
  const front = tiles ? tileMaterial(tiles) : finish.lacquer("#eceae5");
  // the tub on a base under its floor, its front clad (tiles or a white panel) up to the rim
  block(g, [length, 0.14, width - 0.02], [0, 0, -0.01], front, 0.004);
  sunkenBowl(g, length, width - 0.02, 0.56, 0.42, 0.06, finish.ceramic(), -0.01);
  const apron = block(g, [length, 0.51, 0.012], [0, 0, width / 2 - 0.006], front, 0.002);
  if (tiles) metricUV(apron.geometry);
  tap(g, -length / 2 + 0.3, 0.72, -width / 2 + 0.02, 0.16);
  return piece(g, "bathtub", length, width);
}

/**
 * Walk-in shower `width` x `depth` (its back on the wall): a flush tray in `floor` (tile material
 * options or a colour) with a linear drain, a fixed glass panel `panel` metres wide on the front,
 * 2 m high with a steadying bar to the wall, the way in beside it; `side` "left" / "right" closes
 * that side with glass too (a shower in a corner has a wall on the other side). A rain head on an
 * arm, a hand shower on a rail and a thermostatic mixer on the back wall.
 */
export function shower({ width = 1.2, depth = 0.9, panel = 0.8, side = null, floor } = {}) {
  const g = new THREE.Group();
  const trayMat = floor && typeof floor === "object" ? tileMaterial(floor) : finish.stone(floor ?? "#8e8b85");
  const tray = block(g, [width, 0.012, depth], [0, 0, 0], trayMat);
  if (floor && typeof floor === "object") metricUV(tray.geometry);
  block(g, [width - 0.1, 0.004, 0.05], [0, 0.012, -depth / 2 + 0.08], finish.steel()); // drain
  const glass = finish.showerGlass(), black = finish.black();
  const p = Math.min(panel, width);
  // the front panel starts at the closed side (the glass side, or the left when both are walls)
  const s = side === "right" ? 1 : -1;
  const edge = s * (width / 2);
  block(g, [p, 2.0, 0.008], [edge - s * (p / 2), 0.012, depth / 2 - 0.02], glass);
  block(g, [0.02, 2.0, 0.02], [edge - s * 0.01, 0.012, depth / 2 - 0.02], black); // profile
  if (side) {
    block(g, [0.008, 2.0, depth - 0.04], [edge - s * 0.02, 0.012, 0], glass);
  } else {
    block(g, [0.012, 0.012, depth - 0.02], [edge - s * (p - 0.02), 1.95, 0], black); // bar to the back wall
  }
  const c = finish.chrome();
  // rain head on an arm from the back wall
  block(g, [0.02, 0.02, 0.35], [0, 2.1, -depth / 2 + 0.175], c, 0.006);
  const head = shade(new THREE.Mesh(new THREE.CylinderGeometry(0.13, 0.13, 0.012, 40), c));
  head.position.set(0, 2.08, -depth / 2 + 0.33);
  g.add(head);
  // mixer and hand shower on a rail
  block(g, [0.16, 0.08, 0.05], [width / 2 - 0.3, 1.05, -depth / 2 + 0.025], c, 0.01);
  block(g, [0.02, 0.8, 0.02], [width / 2 - 0.14, 1.1, -depth / 2 + 0.03], c, 0.008);
  block(g, [0.05, 0.2, 0.05], [width / 2 - 0.14, 1.6, -depth / 2 + 0.07], c, 0.02);
  return piece(g, "shower", width, depth);
}

/**
 * Heated towel rail (a ladder of bars on the wall) with `towels` hung over it (their colours),
 * `width` x `height`, its bottom at its origin: place it with y = floor + 0.2.
 */
export function towelRail({ width = 0.5, height = 0.9, towels = ["#ece8e0", "#c9c3b6"], color } = {}) {
  const g = new THREE.Group();
  const bar = color ? finish.metal(color) : finish.lacquer("#f2f2f0");
  const z = -0.04;
  for (const s of [-1, 1]) leg(g, s * (width / 2 - 0.015), z, height, bar, 0.013); // uprights, bottom at 0
  const rungs = Math.max(3, Math.round(height / 0.12));
  for (let i = 0; i < rungs; i++) {
    const r = shade(new THREE.Mesh(new THREE.CylinderGeometry(0.009, 0.009, width - 0.03, 10), bar));
    r.rotation.z = Math.PI / 2;
    r.position.set(0, 0.06 + (i * (height - 0.12)) / (rungs - 1), z);
    g.add(r);
  }
  // each towel folded over a rung near the top: a front and a back fall, a round fold on the bar
  towels.slice(0, 2).forEach((col, k) => {
    const t = finish.towel(col);
    const barY = height - 0.08 - k * 0.2, tw = width - 0.1, fall = 0.42 - k * 0.1;
    block(g, [tw, fall, 0.012], [0, barY - fall, z + 0.018], t, 0.006);
    block(g, [tw, fall * 0.8, 0.012], [0, barY - fall * 0.8, z - 0.018], t, 0.006);
    const fold = shade(new THREE.Mesh(new THREE.CylinderGeometry(0.024, 0.024, tw, 16, 1, false, 0, Math.PI), t));
    fold.rotation.z = Math.PI / 2;
    fold.position.set(0, barY, z);
    g.add(fold);
  });
  return piece(g, "towelRail", width, 0.1, { height });
}

/**
 * Coat hooks on the wall: an oak rail `width` wide at 1.7 m with black hooks and a shelf above it;
 * `coats` (their colours) hang from the first hooks. Origin: bottom centre on the floor, back on the wall.
 */
export function coatHooks({ width = 0.8, hooks = 5, coats = ["#4a4f55", "#b59f82"] } = {}) {
  const g = new THREE.Group();
  const oak = finish.oak(), black = finish.black();
  block(g, [width, 0.1, 0.022], [0, 1.65, -0.02], oak, 0.004); // rail
  block(g, [width, 0.022, 0.24], [0, 1.9, 0.08], oak, 0.004); // shelf
  const step = width / hooks;
  for (let i = 0; i < hooks; i++) {
    const x = -width / 2 + step * (i + 0.5);
    block(g, [0.016, 0.016, 0.07], [x, 1.69, 0.025], black, 0.006);
  }
  // coats on every other hook from the second, so they stay within the rail
  coats.slice(0, Math.floor((hooks - 1) / 2)).forEach((col, i) => {
    const x = -width / 2 + step * (1 + 2 * i + 0.5);
    const fab = finish.fabric(col);
    // a coat on a hook: shoulders, a body that widens a little, a fold where it hangs
    block(g, [0.34, 0.2, 0.12], [x, 1.5, 0.08], fab, 0.05);
    block(g, [0.36, 0.62, 0.1], [x, 0.9, 0.075], fab, 0.04);
  });
  return piece(g, "coatHooks", width, 0.24, { height: 1.92 });
}

/** Entrance bench: an oak seat on two panel legs, a shoe shelf under it. */
export function bench({ width = 1.0, depth = 0.34, height = 0.45 } = {}) {
  const g = new THREE.Group();
  const oak = finish.oak();
  block(g, [width, 0.035, depth], [0, height - 0.035, 0], oak, 0.006);
  for (const s of [-1, 1]) block(g, [0.03, height - 0.035, depth - 0.02], [s * (width / 2 - 0.04), 0, 0], oak, 0.004);
  block(g, [width - 0.1, 0.02, depth - 0.04], [0, 0.1, 0], oak, 0.004);
  return piece(g, "bench", width, depth);
}

/**
 * Front-loading washing machine (60 x 60 x 85): white, a round door with a chrome ring and dark
 * glass, a control strip with a dial. `dryer: true` stacks a dryer on it (on a stacking frame).
 */
export function washer({ dryer = false } = {}) {
  const g = new THREE.Group();
  const white = finish.lacquer("#f3f3f1"), chrome = finish.chrome(), black = finish.black();
  const unit = (y0) => {
    block(g, [0.6, 0.85, 0.58], [0, y0, 0], white, 0.02);
    block(g, [0.56, 0.1, 0.006], [0, y0 + 0.72, 0.293], finish.lacquer("#e2e2e0"), 0.003); // control strip
    const dial = shade(new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, 0.02, 24), chrome));
    dial.rotation.x = Math.PI / 2;
    dial.position.set(0.18, y0 + 0.77, 0.3);
    g.add(dial);
    const ring = shade(new THREE.Mesh(new THREE.TorusGeometry(0.17, 0.022, 12, 40), chrome));
    ring.position.set(0, y0 + 0.4, 0.3);
    g.add(ring);
    const glass = shade(new THREE.Mesh(new THREE.CircleGeometry(0.155, 40), black));
    glass.position.set(0, y0 + 0.4, 0.302);
    g.add(glass);
  };
  unit(0);
  if (dryer) {
    block(g, [0.6, 0.04, 0.58], [0, 0.85, 0], finish.lacquer("#dcdcda"), 0.004); // stacking frame
    unit(0.89);
  }
  return piece(g, "washer", 0.6, 0.6, { height: dryer ? 1.74 : 0.85 });
}

/**
 * Things on a bathroom ledge (the top of the WC's box, a shelf, a vanity): a tray with a soap
 * dispenser and a toothbrush cup with two brushes. `width` x 0.12, origin at its bottom centre:
 * place it at the ledge's height.
 */
export function bathAccessories({ width = 0.3, tray = "#2a2b2c" } = {}) {
  const g = new THREE.Group();
  block(g, [width, 0.012, 0.12], [0, 0, 0], finish.metal(tray), 0.004);
  const cyl = (r, h, x, y, mat, seg = 24) => {
    const c = shade(new THREE.Mesh(new THREE.CylinderGeometry(r, r, h, seg), mat));
    c.position.set(x, y + h / 2, 0);
    g.add(c);
    return c;
  };
  // soap dispenser: an amber glass bottle, a chrome pump
  const amber = m("amberGlass", () => new THREE.MeshStandardMaterial({ color: "#8a5a2b", roughness: 0.15, transparent: true, opacity: 0.85 }));
  cyl(0.033, 0.13, -width / 4, 0.012, amber);
  cyl(0.012, 0.03, -width / 4, 0.142, finish.chrome(), 12);
  block(g, [0.008, 0.008, 0.04], [-width / 4, 0.165, 0.015], finish.chrome());
  // toothbrush cup, two brushes leaning in it
  cyl(0.032, 0.1, width / 4, 0.012, finish.ceramic());
  for (const [dx, col] of [[-0.01, "#7fa7b5"], [0.012, "#e9e3d6"]]) {
    const b = shade(new THREE.Mesh(new THREE.BoxGeometry(0.008, 0.18, 0.012), finish.lacquer(col)));
    b.position.set(width / 4 + dx, 0.14, 0);
    b.rotation.z = dx * 12;
    g.add(b);
  }
  return piece(g, "bathAccessories", width, 0.12, { height: 0.22 });
}

/** Folded towels stacked (their colours from the bottom), `width` x `depth`. */
export function towelStack({ colors = ["#e7e2d8", "#cfc7b8", "#f2efe9"], width = 0.36, depth = 0.26 } = {}) {
  const g = new THREE.Group();
  colors.forEach((col, i) => {
    const t = block(g, [width - i * 0.01, 0.055, depth - i * 0.008], [(i % 2 ? 0.006 : -0.004), i * 0.055, 0], finish.towel(col), 0.022);
    t.rotation.y = (i - 1) * 0.03;
  });
  return piece(g, "towelStack", width, depth, { height: colors.length * 0.055 });
}

/** A woven laundry basket with its lid, `diameter` x `height`. */
export function laundryBasket({ diameter = 0.38, height = 0.55, color = "#b9a47f" } = {}) {
  const g = new THREE.Group();
  const weave = finish.weave(color);
  const body = shade(new THREE.Mesh(metricUV(new THREE.CylinderGeometry(diameter / 2, diameter / 2 - 0.03, height - 0.03, 32)), weave));
  body.position.y = (height - 0.03) / 2;
  g.add(body);
  const lid = shade(new THREE.Mesh(metricUV(new THREE.CylinderGeometry(diameter / 2 + 0.01, diameter / 2 + 0.01, 0.03, 32)), weave));
  lid.position.y = height - 0.015;
  g.add(lid);
  return piece(g, "laundryBasket", diameter + 0.02, diameter + 0.02, { height });
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

const VISTHETIQUE = "“Modern Apartment” by visthetique, CC BY 4.0";

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
  // Bought: Aurélien Martel's "PBR Archviz Asset Pack" (Fab, Standard license), one light Scandinavian
  // flat, in kit/assets/licensed/martel (kit/scripts/licensed/pack.mjs). The license allows them in a
  // private repository and image only; where one cannot be loaded, the parametric piece of the same
  // kind (or nothing) stands in.
  // `nominal`: the size the piece stands for when it differs from its bounding box (a bed: its
  // mattress, the duvet hangs over), what model(name, { width, length }) scales from.
  "bed-oak-linen": { licensed: "martel/bed.glbx", fallback: "bed", rotate: 0, nominal: [1.4, 2.0] },
  "nightstand-round-black": { licensed: "martel/nightstand.glbx", fallback: "nightstand", rotate: 0 },
  "sideboard-teak": { licensed: "martel/dresser.glbx", rotate: 0 },
  "sofa-modular-grey": { licensed: "martel/couch.glbx", fallback: "sofa", rotate: 0 },
  "pouf-knit": { licensed: "martel/pouffe.glbx", rotate: 0 },
  "coffee-table-oval-white": { licensed: "martel/lowtable.glbx", rotate: Math.PI / 2 },
  "coffee-table-oval-black": { licensed: "martel/lowtable2.glbx", rotate: Math.PI / 2 },
  "dining-table-white": { licensed: "martel/dinnertable.glbx", rotate: 0 },
  "dining-chair-grey": { licensed: "martel/chair.glbx", fallback: "chair", rotate: 0 },
  "desk-trestle-white": { licensed: "martel/desk.glbx", rotate: 0 },
  "desk-chair-leather": { licensed: "martel/deskchair.glbx", rotate: 0 },
  "step-stool-black": { licensed: "martel/footboard.glbx", rotate: 0 },
  "rug-grey-pattern": { licensed: "martel/carpet.glbx", fallback: "rug", rotate: 0 },
  "curtain-grey": { licensed: "martel/curtain1.glbx", rotate: Math.PI / 2 },
  "curtain-grey-wide": { licensed: "martel/curtain4.glbx", rotate: Math.PI / 2 },
  "radiator-white": { licensed: "martel/heater.glbx", rotate: Math.PI / 2 },
  "wall-art-gallery": { licensed: "martel/posters.glbx", rotate: 0 },
  "mirror-round": { licensed: "martel/mirror.glbx", rotate: 0 },
  "floor-lamp-black": { licensed: "martel/floorlamp.glbx", rotate: 0 },
  "pendant-cluster": { licensed: "martel/ceilinglight.glbx", rotate: 0, hang: true },
  "pendant-drum": { licensed: "martel/ceilinglight2.glbx", rotate: 0, hang: true },
  "plant-ficus": { licensed: "martel/ficus.glbx", rotate: 0 },
  "plant-leafy-white-pot": { licensed: "martel/interiorplant.glbx", rotate: 0 },
  "plant-ivy": { licensed: "martel/ivypot.glbx", rotate: 0 },
  "planter-herbs": { licensed: "martel/mintplanter-003.glbx", rotate: 0 },
  "vase-dry-branches": { licensed: "martel/modernvase.glbx", rotate: 0 },
  "candle-holder-brass": { licensed: "martel/candleholder.glbx", rotate: 0 },
  "clock-black": { licensed: "martel/clock.glbx", rotate: 0 },
  "photo-frame": { licensed: "martel/photoframe.glbx", rotate: 0 },
  "book-open": { licensed: "martel/openbook.glbx", rotate: 0 },
  "teapot": { licensed: "martel/teapot.glbx", rotate: 0 },
  "plate": { licensed: "martel/plate1.glbx", rotate: 0 },
  "wine-glass": { licensed: "martel/wineglass.glbx", rotate: 0 },
  "cup": { licensed: "martel/cup2.glbx", rotate: 0 },
  "bowls-black": { licensed: "martel/bowls.glbx", rotate: 0 },
  "toaster": { licensed: "martel/toaster.glbx", rotate: 0 },
  "bottle-oil": { licensed: "martel/oilbottle.glbx", rotate: 0 },
  // CC Attribution (credited in the viewer): pieces of visthetique's "Modern Apartment" (Sketchfab),
  // split and masked like the bought ones (kit/scripts/licensed/pack.mjs)
  "coffee-machine-black": { licensed: "visthetique/coffee-maker.glbx", rotate: 0, credit: VISTHETIQUE },
  "fridge-black-glass": { licensed: "visthetique/refrigator-001.glbx", rotate: 0, credit: VISTHETIQUE },
  "hood-angled-black": { licensed: "visthetique/hood.glbx", rotate: 0, credit: VISTHETIQUE },
  "plant-hanging": { licensed: "visthetique/flower.glbx", rotate: 0, hang: true, credit: VISTHETIQUE },
  "shoe-cabinet-white": { licensed: "visthetique/shoerack.glbx", rotate: -Math.PI / 2, credit: VISTHETIQUE },
};
// absolute: renderer snapshots (kit/versions/<name>/) share the working copy's assets
const ASSETS = new URL("/kit/assets/", import.meta.url);

/**
 * Bought models (`licensed: "<pack>/<name>.glbx"`, in kit/assets/licensed/): their license forbids
 * handing out the files and asks to keep the people who view a scene from extracting them, so
 * they are stored and served masked: a GLB XORed with this key, behind a 4-byte tag. Not a
 * secret, just not a file anyone can open as a model. XOR is its own inverse: the same function
 * masks (kit/scripts/licensed/pack.mjs) and unmasks (model()).
 */
const MASK_TAG = "HGX1";
const MASK_KEY = new TextEncoder().encode("housegen:licensed-furniture:do-not-redistribute");
export function maskLicensed(bytes, masked) {
  const tag = new TextEncoder().encode(MASK_TAG);
  const body = masked ? bytes.subarray(tag.length) : bytes;
  if (masked && !tag.every((b, i) => bytes[i] === b)) throw new Error("not a masked licensed model");
  const out = new Uint8Array((masked ? 0 : tag.length) + body.length);
  if (!masked) out.set(tag);
  const at = masked ? 0 : tag.length;
  for (let i = 0; i < body.length; i++) out[at + i] = body[i] ^ MASK_KEY[i % MASK_KEY.length];
  return out;
}

async function loadGLTF(spec, file) {
  const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
  if (!spec.licensed) return loader.loadAsync(new URL(file, ASSETS).href);
  const r = await fetch(new URL(file, ASSETS).href);
  if (!r.ok) throw new Error(`${file}: ${r.status}`);
  return loader.parseAsync(maskLicensed(new Uint8Array(await r.arrayBuffer()), true).buffer, "");
}
const _used = new Set();
// shared with the runtime (it sends the page's credits to the app) without importing this module
globalThis.__housekitCredits = () => credits();

/** Credit lines of the attribution-licensed models this page has loaded (for the page's credits). */
export function credits() {
  return [..._used].map((n) => MODELS[n].credit).filter(Boolean);
}
const _loaded = new Map();

/**
 * A scanned or bought model by its catalogue name, bottom-centred and facing +z (async: it is
 * loaded once). `width` / `length` / `height` (m) stretch it along x / z / y, each on its own: a bed
 * given the plan's mattress size, a curtain the room's height. Width and length count from the
 * piece's `nominal` size when the catalogue gives one (a bed: its mattress), else from its box.
 */
export async function model(name, { width, length, height } = {}) {
  const spec = MODELS[name];
  if (!spec) throw new Error(`unknown model ${name}. Known: ${Object.keys(MODELS).join(", ")}`);
  const file = spec.licensed ? `licensed/${spec.licensed}` : spec.file ?? `polyhaven/${spec.id}/${spec.id}.gltf`;
  // models are fetched compressed (meshopt geometry, WebP textures at 1k: scripts/fetch_models.mjs)
  if (!_loaded.has(file)) _loaded.set(file, loadGLTF(spec, file).catch(() => null));
  const gltf = await _loaded.get(file);
  if (!gltf) {
    // not fetched (a build without the Sketchfab token) or not loadable: the parametric piece of
    // the same kind
    console.warn(`housekit: model ${name} (${file}) not available, using ${spec.fallback ?? "nothing"}`);
    const make = { sofa, bed, nightstand, wardrobe, rug, chair }[spec.fallback];
    // at the size asked for (each parametric piece takes the dimensions it knows)
    return make ? make({ width, length, depth: length, height }) : piece(new THREE.Group(), name, 0, 0);
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
  const [nw, nl] = spec.nominal ?? [size.x, size.z];
  const s = [width ? width / nw : 1, height ? height / size.y : 1, length ? length / nl : 1];
  wrap.scale.set(...s);
  const extra = { hang: !!spec.hang, height: size.y * s[1] };
  if (spec.nominal) extra.nominal = [nw * s[0], nl * s[2]];
  return piece(wrap, name, size.x * s[0], size.z * s[2], extra);
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
  finish, sofa, bed, nightstand, chair, diningSet, wardrobe, kitchenRun, wc, basin, bathtub, shower, towelRail,
  coatHooks, bench, washer, bathAccessories, towelStack, laundryBasket, rug, ceilingLight,
  MODELS, model, credits, place, onWall,
};
