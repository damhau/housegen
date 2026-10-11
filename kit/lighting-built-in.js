// housekit lighting: built-in and architectural light (downlights, tracks, strips). See lighting.js for the contract.
//
// The lights Swiss new-build flats have: downlights in halls, kitchens and baths, a track over a
// worktop or a picture wall, the flush opal light of the old ceiling disc, an LED cove, a strip under
// the wall units, step lights in a stair. Ceiling fixtures hang from y = 0 (ceilingUnderside), the
// step light's plate is on the wall (facing +z).
//
// Directions: an aimed head (a gimbal downlight, a track's spots) tilts toward -z at yaw 0, yaw turns
// it about the vertical. A strip that runs along a wall (coveStrip, underCabinet) runs along x with
// the wall on its -z side (coveStrip's back is at z = 0): rotate it with fx.place's rot to its wall.
//
// Light: cones only aimed at surfaces from a few centimetres (they scallop), never a point light just
// under the ceiling (the bright smear); the flush light throws a wide cone down and keeps its glow
// 0.35 m under the ceiling and faint.

import * as THREE from "three";
import { metal, lit, addGlow, addCone, tagFixture, opal, glowMaterial, WARM, NEUTRAL } from "./lighting-parts.js";

// ---------------------------------------------------------------- materials of this family
const _mats = new Map();
const std = (key, o) => {
  if (!_mats.has(key)) _mats.set(key, new THREE.MeshStandardMaterial(o));
  return _mats.get(key);
};
/** A lamp's inside: a satin reflector (seen from inside its cone, so both sides). */
const reflector = () => std("bi-reflector", { name: "reflector", color: "#d9d9d5", roughness: 0.3, metalness: 1, side: THREE.DoubleSide });
/** Anodised aluminium: the "alu" finish of profiles and channels, next to the FINISHES. */
const alu = () => std("bi-alu", { name: "anodised aluminium", color: "#c6c8ca", roughness: 0.38, metalness: 1 });
/** Painted gypsum board, the ceiling's white (floorPlan's ceiling colour). */
const gypsum = () => std("bi-gypsum", { name: "gypsum board", color: "#f6f5f1", roughness: 0.95 });
/** A dark recess: a track's slot, a trim's depth, end caps. */
const recess = () => std("bi-recess", { name: "dark recess", color: "#1e1e1e", roughness: 0.85 });
const surface = (finish) => (finish === "alu" ? alu() : metal(finish));
const lightColor = (tone) => (tone === "neutral" ? NEUTRAL : WARM);

// ---------------------------------------------------------------- geometry helpers
const mesh = (geo, material, g) => {
  const m = lit(new THREE.Mesh(geo, material));
  g.add(m);
  return m;
};
function box(g, [sx, sy, sz], [x, y, z], material) {
  const m = mesh(new THREE.BoxGeometry(sx, sy, sz), material, g);
  m.position.set(x, y, z);
  return m;
}
/** A plan outline (a Shape or a hole's Path, in x and z): a circle of radius `r`, or a square of half side `r`. */
function outline(shape, r, path = new THREE.Shape()) {
  if (shape !== "square") {
    path.absarc(0, 0, r, 0, Math.PI * 2, false);
    return path;
  }
  const c = Math.min(r * 0.12, 0.005); // a small rounded corner
  path.moveTo(-r + c, -r);
  path.lineTo(r - c, -r);
  path.quadraticCurveTo(r, -r, r, -r + c);
  path.lineTo(r, r - c);
  path.quadraticCurveTo(r, r, r - c, r);
  path.lineTo(-r + c, r);
  path.quadraticCurveTo(-r, r, -r, r - c);
  path.lineTo(-r, -r + c);
  path.quadraticCurveTo(-r, -r, -r + c, -r);
  return path;
}
/**
 * An outline extruded down from y = `top` by `h` (a body, a trim ring with a `hole`, a canopy),
 * `bevel` rounding its edges (not with a hole).
 */
function prism(g, shape, r, h, material, { top = 0, hole = 0, bevel = 0, seg } = {}) {
  const s = outline(shape, r - bevel);
  if (hole > 0) s.holes.push(outline(shape, hole, new THREE.Path()));
  const geo = new THREE.ExtrudeGeometry(s, {
    depth: Math.max(1e-4, h - 2 * bevel), curveSegments: seg ?? (shape === "square" ? 3 : 20),
    bevelEnabled: bevel > 0, bevelThickness: bevel, bevelSize: bevel, bevelSegments: 3,
  });
  geo.rotateX(Math.PI / 2); // the outline in x-z, extruded downwards
  geo.translate(0, top - bevel, 0);
  return mesh(geo, material, g);
}
/** A flat disc (or square) facing down at height `y`: a lens, the back of a recess. */
function disc(g, shape, r, y, material) {
  const geo = shape === "square" ? new THREE.PlaneGeometry(2 * r, 2 * r) : new THREE.CircleGeometry(r, 32);
  geo.rotateX(Math.PI / 2);
  geo.translate(0, y, 0);
  return mesh(geo, material, g);
}
/** An open cone (a reflector) from radius `r0` at y = `top` down to `r1`, `h` tall: round or square. */
function tube(g, shape, r0, r1, h, top, material) {
  const sq = shape === "square", k = sq ? Math.SQRT2 : 1;
  const geo = new THREE.CylinderGeometry(r0 * k, r1 * k, h, sq ? 4 : 32, 1, true);
  if (sq) geo.rotateY(Math.PI / 4);
  geo.translate(0, top - h / 2, 0);
  return mesh(geo, material, g);
}
/**
 * A lamp body (a can) along -y from its top at y = 0: `shape` round or square, `d` across, `len`
 * long, its lens `inset` into the bottom behind a conical reflector (inset = len: a trim ring only).
 * Returns the group and the lens's local y.
 */
function can(shape, d, len, material, { inset = 0.016, aperture = 0.36, glow = 1.6 } = {}) {
  const g = new THREE.Group();
  const ri = d * aperture, solid = len - inset;
  if (solid > 1e-4) prism(g, shape, d / 2, solid, material);
  prism(g, shape, d / 2, inset, material, { top: -solid, hole: ri });
  tube(g, shape, ri * 0.72, ri - 0.0004, inset, -solid, reflector());
  disc(g, shape, ri * 0.72, -solid - 0.0005, glowMaterial({ strength: glow, name: "lens" }));
  return { group: g, lensY: -solid - 0.0005 };
}
/**
 * Aim a head: `head` (its top at its y = 0) pivots at `pivot`, its top `rise` above the pivot,
 * tilted by `tilt` toward -z, turned by `yaw` about the vertical.
 * Returns where its cone starts (`ahead` past `lensY` along its axis) and the axis.
 */
function aim(g, head, { pivot, tilt, yaw, rise = 0, lensY, ahead = 0.02 }) {
  const turn = new THREE.Group(), tip = new THREE.Group();
  turn.position.set(...pivot);
  turn.rotation.y = yaw;
  tip.rotation.x = tilt;
  head.position.y = rise;
  tip.add(head);
  turn.add(tip);
  g.add(turn);
  g.updateMatrixWorld(true);
  const from = head.localToWorld(new THREE.Vector3(0, lensY, 0));
  const to = head.localToWorld(new THREE.Vector3(0, lensY - 1, 0));
  const dir = to.sub(from).normalize();
  return { at: from.addScaledVector(dir, ahead), dir };
}
/** How far a fixture reaches below its fixing (its height in the tag). */
function reachDown(g) {
  g.updateMatrixWorld(true);
  return Math.max(0.002, -new THREE.Box3().setFromObject(g).min.y);
}
/** One value per item: a number for all, or an array (or a "0.3,0.6" list) taken in turn. */
const pick = (v, i) => {
  const a = typeof v === "string" ? v.split(",").map(Number) : v;
  return Number(Array.isArray(a) ? a[i % a.length] : a) || 0;
};

// ---------------------------------------------------------------- downlight
/**
 * Downlight: `mount` "surface" (a can on the ceiling, as in the hall of the benchmark) or "recessed"
 * (a flush trim ring), `shape` round or square, `d` across. A cone straight down, `beam` its
 * half-angle; `gimbal: true` tilts the head by `tilt` (toward -z, turned by `yaw`) and its cone with
 * it (a ball-jointed can under a canopy, or a cup tilting out of the recessed trim). Role "ceiling".
 */
function downlight({ d = 0.09, mount = "surface", shape = "round", finish = "black", beam = 0.55, lm = 450,
  gimbal = false, tilt = 0.35, yaw = 0, tone = "warm" } = {}) {
  const g = new THREE.Group();
  const m = metal(finish);
  const recessed = mount === "recessed";
  let at = [0, 0, 0], dir = [0, -1, 0];
  if (!gimbal) {
    const len = recessed ? 0.008 : 0.1;
    const { group, lensY } = can(shape, d, len, m, { inset: recessed ? len : 0.016, aperture: recessed ? 0.37 : 0.36 });
    g.add(group);
    at = [0, lensY - 0.02, 0];
  } else if (recessed) {
    // the trim, the dark recess in it, a round cup tilting out of it
    const ri = d * 0.38, rc = d * 0.29, len = 0.026;
    prism(g, shape, d / 2, 0.006, m, { hole: ri });
    disc(g, shape, ri + 0.0015, -0.0004, recess()); // its edge hidden in the trim
    const { group, lensY } = can("round", 2 * rc, len, m, { inset: 0.009, aperture: 0.38 });
    const a = aim(g, group, { pivot: [0, -rc * Math.sin(tilt) - 0.0015, 0], tilt, yaw, lensY });
    ({ at, dir } = { at: a.at.toArray(), dir: a.dir.toArray() });
  } else {
    // a canopy, a short stem and a ball joint, the can hanging from it
    const canopy = shape === "square" ? d * 0.36 : d * 0.4, len = 0.085;
    prism(g, shape, canopy, 0.014, m, { bevel: 0.002 });
    const py = -0.026 - (d / 2) * Math.sin(Math.abs(tilt));
    const stem = mesh(new THREE.CylinderGeometry(0.0055, 0.0055, -py - 0.014, 12), m, g);
    stem.position.y = (py - 0.014) / 2;
    const ball = mesh(new THREE.SphereGeometry(0.0085, 14, 10), m, g);
    ball.position.y = py;
    const { group, lensY } = can(shape, d, len, m);
    const a = aim(g, group, { pivot: [0, py, 0], tilt, yaw, rise: -0.004, lensY });
    ({ at, dir } = { at: a.at.toArray(), dir: a.dir.toArray() });
  }
  addCone(g, at, dir, lm, { angle: beam, penumbra: 0.7, color: lightColor(tone) });
  const name = `${gimbal ? "adjustable " : ""}${mount} downlight`;
  return tagFixture(g, { name, family: "downlight", role: "ceiling", finish, footprint: [d, d], height: reachDown(g), hang: true });
}

// ---------------------------------------------------------------- track
/**
 * Surface track: a bar `length` long (along x) on the ceiling with `heads` spots (2-4) on adapters
 * and stems, each tilted by `tilt` toward -z and turned by `yaw` (a number, or one per head:
 * tilt: [0.5, 0.3, 0.5]); `fan` turns the outer heads that much outward (0: all parallel). One cone
 * per head, heads grouped two by two past three (3 lights at most).
 */
function track({ length = 1.2, heads = 3, finish = "black", tilt = 0.6, yaw = 0, fan = 0.3, beam = 0.32, lm = 180, tone = "warm" } = {}) {
  const g = new THREE.Group();
  const m = metal(finish);
  const n = Math.max(1, Math.min(4, Math.round(heads)));
  const W = 0.034, H = 0.032;
  box(g, [length, H, W], [0, -H / 2, 0], m);
  box(g, [length - 0.012, 0.001, 0.01], [0, -H - 0.0004, 0], recess()); // the slot
  for (const s of [-1, 1]) box(g, [0.004, H + 0.001, W + 0.001], [s * (length / 2 - 0.002), -H / 2, 0], m); // end caps
  const spots = [];
  for (let i = 0; i < n; i++) {
    const x = ((i + 0.5) / n - 0.5) * length;
    box(g, [0.05, 0.022, 0.028], [x, -H - 0.011, 0], m); // the adapter in the track
    const t = pick(tilt, i), py = -H - 0.022 - 0.05;
    const stem = mesh(new THREE.CylinderGeometry(0.0055, 0.0055, 0.05, 12), m, g);
    stem.position.set(x, py + 0.025, 0);
    const { group, lensY } = can("round", 0.056, 0.105, m, { inset: 0.022, aperture: 0.38 });
    const out = n > 1 ? (2 * i) / (n - 1) - 1 : 0; // -1 at the first head, 1 at the last
    spots.push(aim(g, group, { pivot: [x, py, 0], tilt: t, yaw: pick(yaw, i) - fan * out, rise: 0.03, lensY }));
  }
  // at most three cones: neighbouring heads share one (the mean of their spots, wider)
  const per = Math.ceil(n / 3), color = lightColor(tone);
  for (let i = 0; i < n; i += per) {
    const grp = spots.slice(i, i + per);
    const at = grp.reduce((s, p) => s.add(p.at), new THREE.Vector3()).divideScalar(grp.length);
    const dir = grp.reduce((s, p) => s.add(p.dir), new THREE.Vector3()).normalize();
    const spread = Math.max(...grp.map((p) => p.dir.angleTo(dir)));
    addCone(g, at.toArray(), dir.toArray(), lm * grp.length, { angle: beam + spread + (grp.length > 1 ? 0.08 : 0), penumbra: 0.55, color });
  }
  return tagFixture(g, { name: "track light", family: "track", role: "ceiling", finish, footprint: [length, 0.06], height: reachDown(g), hang: true });
}

// ---------------------------------------------------------------- flush ceiling light
/**
 * Flush ceiling light: an opal diffuser (round or square, `d` 0.3-0.5) on a thin base in `finish`,
 * 6 cm deep: the modern ceiling disc. Its light goes down in a wide cone (`beam` half-angle) and a
 * faint glow 0.35 m under the ceiling lifts the walls (never a point just under the ceiling: a smear).
 * Role "ceiling".
 */
function flushCeiling({ d = 0.35, shape = "round", finish = "white", lm, beam = 1.3, tone = "warm" } = {}) {
  const g = new THREE.Group();
  const flux = lm ?? Math.round(1400 * (d / 0.35) ** 2);
  const seg = shape === "square" ? 3 : 32;
  prism(g, shape, d / 2, 0.014, metal(finish), { seg });
  prism(g, shape, d / 2 - 0.0015, 0.046, opal(1.1), { top: -0.014, bevel: 0.006, seg }); // nearly flush with the base
  const color = lightColor(tone);
  addCone(g, [0, -0.08, 0], [0, -1, 0], flux * 0.95, { angle: beam, penumbra: 1, color });
  addGlow(g, [0, -0.35, 0], flux * 0.05, { color, reach: 4 });
  return tagFixture(g, { name: `${shape} flush ceiling light`, family: "flushCeiling", role: "ceiling", finish, footprint: [d, d], height: 0.06, hang: true });
}

// ---------------------------------------------------------------- cove strip
/**
 * LED cove: a strip hidden in a gypsum trough along a wall, near the ceiling (the bedroom of the
 * benchmark). An L-shaped lip (white board): a shelf `drop` under the ceiling, `depth` deep from the
 * wall, its upright stopping `gap` under the ceiling; the strip lies on the shelf behind the upright
 * and its light washes the ceiling and the top of the wall. Origin: on the ceiling at the middle of
 * the wall's edge, x along the wall, the back (z = 0) against it, the lip at z = depth: place it at
 * ceilingUnderside(y, height) on the wall line, rotated so -z faces the wall. Role "accent".
 */
function coveStrip({ length = 2, depth = 0.18, drop = 0.12, gap = 0.05, finish = "alu", lmPerM = 300, tone = "warm" } = {}) {
  const g = new THREE.Group();
  const t = 0.0125, up = drop - gap, gy = gypsum();
  const inner = length - 2 * t;
  box(g, [length, t, depth], [0, -drop + t / 2, depth / 2], gy); // the shelf
  box(g, [length, up, t], [0, -gap - up / 2, depth - t / 2], gy); // the upright
  for (const s of [-1, 1]) box(g, [t, up - t, depth - t], [s * (length / 2 - t / 2), -drop + t + (up - t) / 2, (depth - t) / 2], gy);
  // the strip in its channel, on the shelf behind the upright
  const sy = -drop + t, sz = depth - t - 0.022;
  box(g, [inner - 0.01, 0.008, 0.016], [0, sy + 0.004, sz], surface(finish));
  box(g, [inner - 0.02, 0.002, 0.009], [0, sy + 0.009, sz], glowMaterial({ strength: 2.4, name: "LED strip" }));
  // inside the trough, the wall and the ceiling it lights from a few centimetres: an even glow (a
  // line of light that near is no job for point sources), seen through the slit over the upright
  const glowing = glowMaterial({ color: "#f6f5f1", emissive: "#ffe2bf", strength: 1.6, roughness: 0.95, name: "cove glow" });
  const wall = mesh(new THREE.PlaneGeometry(inner, drop - t - 0.003), glowing, g);
  wall.position.set(0, (sy - 0.003) / 2, 0.003);
  const ceil = mesh(new THREE.PlaneGeometry(inner, depth - t - 0.003), glowing, g);
  ceil.rotation.x = Math.PI / 2;
  ceil.position.set(0, -0.003, (depth - t) / 2);
  // the wash over the ceiling past the lip: cones from just inside the wall's plane, low enough to
  // spread evenly along the length; from there they see neither the lip's face, nor the shelf's
  // underside, nor the wall (all turned away), nor the next room (they all point into this one)
  const n = length <= 1.2 ? 2 : 3, color = lightColor(tone);
  const hs = Math.min(1.4, Math.max(0.55, (1.4 * length) / n)); // the cones' depth: more than their spacing
  const back = Math.atan2(hs - drop, depth + 0.01) - 0.01; // the steepest: just over the lip, past the shelf
  const axis = Math.atan2(hs, 0.01 + Math.max(0.9, hs * 1.6)); // aimed well into the room: wide, even cones
  const lm = (0.8 * lmPerM * length) / n; // the trough keeps a fifth of the strip's light
  for (let i = 0; i < n; i++) {
    const x = ((i + 0.5) / n - 0.5) * length;
    addCone(g, [x, -hs, -0.01], [0, Math.sin(axis), Math.cos(axis)], lm, { angle: back - axis, penumbra: 1, color, reach: 5 });
  }
  return tagFixture(g, { name: "cove light strip", family: "coveStrip", role: "accent", finish, footprint: [length, depth], height: drop, hang: true });
}

// ---------------------------------------------------------------- under-cabinet strip
/**
 * Under-cabinet strip: a slim aluminium profile with an opal face, `length` long (along x), fixed
 * under the kitchen's wall units (its origin at its top, the wall on its -z side): cones down onto
 * the worktop, tilted by `tilt` toward the splashback. Role "accent".
 */
function underCabinet({ length = 0.9, finish = "alu", tilt = 0.2, lmPerM = 350, tone = "warm" } = {}) {
  const g = new THREE.Group();
  const W = 0.018, H = 0.0085, face = 0.0016;
  box(g, [length, H - face, W], [0, -(H - face) / 2, 0], surface(finish));
  box(g, [length - 0.008, face, W - 0.004], [0, -H + face / 2, 0], opal(1.4));
  for (const s of [-1, 1]) box(g, [0.004, H, W + 0.001], [s * (length / 2 - 0.002), -H / 2, 0], recess());
  const n = length <= 0.6 ? 2 : 3, color = lightColor(tone), lm = (lmPerM * length) / n;
  for (let i = 0; i < n; i++) {
    const x = ((i + 0.5) / n - 0.5) * length;
    addCone(g, [x, -H - 0.02, 0], [0, -Math.cos(tilt), -Math.sin(tilt)], lm, { angle: 1.0, penumbra: 0.9, color, reach: 4 });
  }
  return tagFixture(g, { name: "under-cabinet light", family: "underCabinet", role: "accent", finish, footprint: [length, W], height: H, hang: true });
}

// ---------------------------------------------------------------- step light
/**
 * Step light: a small plate set in the wall (`w` × `h`, 4 mm proud) for stairs and corridors, with a
 * `style` "louvre" (slats over the light) or "slot" (a slit under a little hood). Its cone goes down
 * and out onto the steps or the floor; mount it about 0.3 m up. Role "accent".
 */
function stepLight({ w = 0.12, h = 0.075, style = "louvre", finish = "white", lm = 30, tone = "warm" } = {}) {
  const g = new THREE.Group();
  const m = metal(finish), T = 0.004;
  const slot = style === "slot";
  const ow = w - 0.026, oh = slot ? 0.011 : h * 0.44, oy = slot ? -h * 0.18 : -h * 0.1;
  const plate = new THREE.Shape();
  plate.moveTo(-w / 2, -h / 2).lineTo(w / 2, -h / 2).lineTo(w / 2, h / 2).lineTo(-w / 2, h / 2).lineTo(-w / 2, -h / 2);
  const hole = new THREE.Path();
  hole.moveTo(-ow / 2, oy - oh / 2).lineTo(ow / 2, oy - oh / 2).lineTo(ow / 2, oy + oh / 2).lineTo(-ow / 2, oy + oh / 2).lineTo(-ow / 2, oy - oh / 2);
  plate.holes.push(hole);
  mesh(new THREE.ExtrudeGeometry(plate, { depth: T, bevelEnabled: false }), m, g); // from the wall (z = 0) out
  const back = mesh(new THREE.PlaneGeometry(ow, oh), glowMaterial({ strength: 1.1, name: "step light diffuser" }), g);
  back.position.set(0, oy, 0.0008);
  if (slot) {
    box(g, [ow + 0.012, 0.0028, 0.012], [0, oy + oh / 2 + 0.0014, T + 0.006], m); // the hood over the slit
  } else {
    for (let k = 0; k < 3; k++) {
      const slat = box(g, [ow, 0.0022, 0.006], [0, oy - oh / 2 + (oh * (k + 0.5)) / 3, 0.0036], m);
      slat.rotation.x = 0.6; // tipped down: the light goes down, the eye sees the slats
    }
  }
  addCone(g, [0, oy, 0.03], [0, -Math.cos(0.95), Math.sin(0.95)], lm, { angle: 0.5, penumbra: 0.8, color: lightColor(tone), reach: 4 });
  return tagFixture(g, { name: `${style} step light`, family: "stepLight", role: "accent", finish, footprint: [w, 0.016], height: h, wall: true });
}

/** The family's entries for lighting.js FIXTURES: { make, kind, doc }. */
export const BUILT_IN = {
  downlight: { make: downlight, kind: "ceiling",
    doc: "downlight({ d=0.09, mount:'surface'|'recessed', shape:'round'|'square', finish:'black'|'white'|…, beam=0.55, lm=450, gimbal=false, tilt=0.35, yaw=0, tone:'warm'|'neutral' }) a cone straight down (gimbal: an adjustable head tilted toward -z): halls, kitchens, baths" },
  track: { make: track, kind: "ceiling",
    doc: "track({ length=1.2, heads=3 (2-4), finish:'black'|'white'|…, tilt=0.6, yaw=0 (or one per head), fan=0.3, beam=0.32, lm=180 per head, tone }) surface track along x with spots tilted toward -z: over a worktop, a picture wall" },
  flushCeiling: { make: flushCeiling, kind: "ceiling",
    doc: "flushCeiling({ d=0.35 (0.3-0.5), shape:'round'|'square', finish='white', lm≈1400·(d/0.35)², beam=1.3, tone }) flush opal ceiling light, light down in a wide cone: halls, baths, small rooms (replaces fx.ceilingLight)" },
  coveStrip: { make: coveStrip, kind: "ceiling",
    doc: "coveStrip({ length=2, depth=0.18, drop=0.12, gap=0.05, lmPerM=300, tone }) accent: LED strip in a gypsum trough along a wall (x along it, back z=0 against it, -z to the wall), washes the ceiling and the wall's top: bedrooms, living rooms" },
  underCabinet: { make: underCabinet, kind: "ceiling",
    doc: "underCabinet({ length=0.9, finish='alu'|…, tilt=0.2, lmPerM=350, tone }) accent: LED profile under kitchen wall units (origin at its top, x along the run, -z to the wall), light down onto the worktop" },
  stepLight: { make: stepLight, kind: "wall",
    doc: "stepLight({ w=0.12, h=0.075, style:'louvre'|'slot', finish='white'|…, lm=30, tone }) accent: small plate in the wall about 0.3 m up, light down onto steps or a corridor floor" },
};
