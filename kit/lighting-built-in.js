// housekit lighting: built-in and architectural light (downlights, tracks, strips). See lighting.js for the contract.

import * as THREE from "three";
import { metal, cyl, lit, addGlow, addCone, tagFixture, opal, glowMaterial } from "./lighting-parts.js";

/**
 * Downlight: `mount` "surface" (a cylinder on the ceiling, `finish` black or white) or "recessed" (a
 * flush ring), `d` its diameter. A cone straight down, `beam` its half-angle. Role "ceiling".
 */
function downlight({ d = 0.09, mount = "surface", finish = "black", beam = 0.55, lm = 450 } = {}) {
  const g = new THREE.Group();
  const m = metal(finish);
  const h = mount === "surface" ? 0.1 : 0.012;
  const body = cyl(g, d / 2, d / 2, h, -h / 2, m, 40);
  body.castShadow = false;
  const lens = new THREE.Mesh(new THREE.CircleGeometry(d * 0.32, 32), glowMaterial({ strength: 1.6, name: "downlight lens" }));
  lens.rotation.x = Math.PI / 2;
  lens.position.y = -h - 0.001;
  g.add(lit(lens));
  addCone(g, [0, -h - 0.02, 0], [0, -1, 0], lm, { angle: beam, penumbra: 0.7 });
  return tagFixture(g, { name: `${mount} downlight`, family: "downlight", role: "ceiling", finish, footprint: [d, d], height: h, hang: true });
}

/** The family's entries for lighting.js FIXTURES: { make, kind, doc }. */
export const BUILT_IN = {
  downlight: { make: downlight, kind: "ceiling",
    doc: "downlight({ d=0.09, mount:'surface'|'recessed', finish:'black'|'white', beam=0.55 }) a cone straight down: halls, kitchens, baths" },
};
