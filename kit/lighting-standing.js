// housekit lighting: table, floor and desk lamps (they stand on a surface or the floor). See lighting.js for the contract.

import * as THREE from "three";
import { metal, cyl, lit, addGlow, addCone, tagFixture, opal, glowMaterial } from "./lighting-parts.js";

/** Opal globe table lamp on a small base (`finish`): bedside, sideboard, desk. A soft glow. Role "accent". */
function globeTableLamp({ d = 0.2, finish = "black", lm = 250 } = {}) {
  const g = new THREE.Group();
  const m = metal(finish);
  cyl(g, 0.06, 0.065, 0.025, 0.0125, m, 32);
  cyl(g, 0.01, 0.01, 0.08, 0.065, m, 12);
  const globe = new THREE.Mesh(new THREE.SphereGeometry(d / 2, 40, 24), opal(1.1));
  globe.position.y = 0.105 + d / 2;
  g.add(lit(globe));
  addGlow(g, [0, 0.105 + d / 2, 0], lm, { reach: 4 });
  return tagFixture(g, { name: "opal globe table lamp", family: "globeTableLamp", role: "accent", finish, footprint: [d, d], height: 0.105 + d });
}

/** The family's entries for lighting.js FIXTURES: { make, kind, doc }. */
export const STANDING = {
  globeTableLamp: { make: globeTableLamp, kind: "standing",
    doc: "globeTableLamp({ d=0.2, finish }) opal globe on a small base: bedside, sideboard" },
};
