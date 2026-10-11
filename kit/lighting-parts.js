// housekit lighting parts: what every fixture family is made of (lighting.js explains the contract):
// the light units, the metal finishes, the glowing materials, the suspension, the two kinds of light
// and the tag. Family modules (lighting-*.js) import from here; lighting.js gathers the families.

import * as THREE from "three";

// ---------------------------------------------------------------- the ceiling
/**
 * Where a ceiling fixture is fixed: the underside of a floor plan's ceiling, which floorPlan draws
 * as a 2 cm board just under the clear height (interior.js), so 2.5 cm below `y + height`.
 * fx.place(fixture, [x, z], rot, ceilingUnderside(storeyY, height)).
 */
export const ceilingUnderside = (y, height) => y + height - 0.025;

// ---------------------------------------------------------------- light units
// A lamp's flux in lumens to three's intensity, calibrated on the lamps the builders placed by hand
// (a PointLight of about 2.5 for a 60 W bulb's 800 lm), so fixtures sit with the scenes we have.
const K = 2.5 / 800;
/** A glow of `lm` lumens: a point light's intensity. */
export const glowIntensity = (lm) => lm * K;
/** A cone of `lm` lumens spread over a half-angle `angle`: brighter as it narrows (the same flux). */
export const coneIntensity = (lm, angle) => lm * K * (2 / (1 - Math.cos(angle)));

// warm white 2700 K, neutral 3000 K: the light colours of homes
export const WARM = "#ffd2a1", NEUTRAL = "#ffe3c4";

// ---------------------------------------------------------------- finishes
const _m = new Map();
const mat = (key, make) => {
  if (!_m.has(key)) _m.set(key, make());
  return _m.get(key);
};
/** Metal and painted finishes of fixtures: brass, black, white, chrome, bronze. */
export const finishes = {
  brass: () => mat("fx-brass", () => new THREE.MeshStandardMaterial({ name: "brass", color: "#c9a464", roughness: 0.32, metalness: 1 })),
  black: () => mat("fx-black", () => new THREE.MeshStandardMaterial({ name: "matte black", color: "#1c1c1c", roughness: 0.55, metalness: 0.3 })),
  white: () => mat("fx-white", () => new THREE.MeshStandardMaterial({ name: "white lacquer", color: "#f1f0ec", roughness: 0.4, metalness: 0 })),
  chrome: () => mat("fx-chrome", () => new THREE.MeshStandardMaterial({ name: "chrome", color: "#e3e5e7", roughness: 0.08, metalness: 1 })),
  bronze: () => mat("fx-bronze", () => new THREE.MeshStandardMaterial({ name: "bronze", color: "#5a4632", roughness: 0.4, metalness: 0.9 })),
};
export const FINISHES = Object.keys(finishes);
export const metal = (name) => (finishes[name] ?? finishes.black)();

/**
 * A material that shines while its fixture is on: `emissive` at `strength` (the runtime sets it to 0
 * when the fixture is off). Opal glass, a fabric or paper shade's inside, a bulb, an LED's diffuser.
 */
export function glowMaterial({ color = "#ffffff", emissive = "#ffe7c8", strength = 1, roughness = 0.35, side = THREE.FrontSide, transparent = false, opacity = 1, name = "lamp glow" } = {}) {
  const m = new THREE.MeshStandardMaterial({ name, color, emissive, emissiveIntensity: strength, roughness, side, transparent, opacity });
  m.userData.glow = strength;
  return m;
}
/** Opal (frosted) glass: white, glowing through when on. */
export const opal = (strength = 1.2) => glowMaterial({ color: "#f7f6f2", emissive: "#ffe9cf", strength, roughness: 0.3, name: "opal glass" });
/** Clear glass: a faint reflection, nearly invisible (no transmission: too costly in the walk). */
export const clearGlass = () => mat("fx-clear", () => new THREE.MeshStandardMaterial({
  name: "clear glass", color: "#ffffff", roughness: 0.04, metalness: 0, transparent: true, opacity: 0.12, depthWrite: false }));
/** A bulb's glass when lit (bare bulbs, filaments behind clear glass). */
export const bulbGlow = (strength = 2.2) => glowMaterial({ color: "#fff4e0", emissive: "#ffd9a6", strength, roughness: 0.2, name: "bulb" });

// ---------------------------------------------------------------- building blocks
export const shaded = (m) => { m.castShadow = true; m.receiveShadow = true; return m; };
export const lit = (m) => { m.castShadow = false; m.receiveShadow = false; return m; };
export function cyl(g, r0, r1, h, y, material, seg = 32) {
  const m = shaded(new THREE.Mesh(new THREE.CylinderGeometry(r0, r1, h, seg), material));
  m.position.y = y;
  g.add(m);
  return m;
}
/** The canopy on the ceiling and the cable down to `drop` (a pendant's suspension); returns the cable's bottom y. */
export function suspension(g, drop, material, { canopy = 0.11, cable = 0.003 } = {}) {
  cyl(g, canopy / 2, canopy / 2, 0.025, -0.0125, material);
  const c = new THREE.Mesh(new THREE.CylinderGeometry(cable, cable, drop, 6), finishes.black());
  c.position.y = -drop / 2;
  c.castShadow = false;
  g.add(c);
  return -drop;
}

/** A glow light (point) of `lm` lumens at `at`, `reach` metres at most. */
export function addGlow(g, at, lm, { color = WARM, reach = 6 } = {}) {
  const l = new THREE.PointLight(color, glowIntensity(lm), reach, 2);
  l.position.set(...at);
  l.castShadow = false;
  l.userData.lm = lm;
  g.add(l);
  return l;
}
/** A cone light (spot) of `lm` lumens from `at` toward `dir`, half-angle `angle`, soft edge `penumbra`. */
export function addCone(g, at, dir, lm, { angle = 0.6, penumbra = 0.6, color = WARM, reach = 8 } = {}) {
  const l = new THREE.SpotLight(color, coneIntensity(lm, angle), reach, angle, penumbra, 2);
  l.position.set(...at);
  l.target.position.set(at[0] + dir[0], at[1] + dir[1], at[2] + dir[2]);
  g.add(l, l.target); // the target in the fixture: it moves with it
  l.castShadow = false;
  l.userData.lm = lm;
  return l;
}

/**
 * Tag a fixture: a piece the audit knows (a "lamp" by its name) with its family, role and finish.
 * `wall`: its origin is on its back, fixed to a wall (fx.onWall puts it flat against it).
 */
export function tagFixture(g, { name, family, role, finish, footprint, height, hang = false, wall = false }) {
  g.userData = { kind: "furniture", name, footprint, height, hang, fixture: { family, role, finish, wall } };
  return g;
}

