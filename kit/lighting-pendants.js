// housekit lighting: pendants (ceiling fixtures on a cable or a rod). See lighting.js for the contract.

import * as THREE from "three";
import { metal, cyl, lit, suspension, addGlow, tagFixture, opal, clearGlass, bulbGlow } from "./lighting-parts.js";

/**
 * Globe pendant: an opal (or clear, with a bare bulb) glass globe on a cable. `d` its diameter, `drop`
 * from the ceiling to the globe's bottom, `finish` of the canopy and the cap. Glows every way.
 * Role "accent" when it hangs over a table or an island (`over: true`), else "ceiling".
 */
function globePendant({ d = 0.3, drop = 0.7, glass = "opal", finish = "brass", over = false, lm = 600 } = {}) {
  const g = new THREE.Group();
  const m = metal(finish);
  const bottom = suspension(g, drop - d, m);
  cyl(g, 0.035, 0.045, 0.05, bottom - 0.025, m); // the cap
  const cy = bottom - 0.05 - d / 2 + 0.02;
  const globe = new THREE.Mesh(new THREE.SphereGeometry(d / 2, 40, 24), glass === "clear" ? clearGlass() : opal());
  globe.position.y = cy;
  g.add(lit(globe));
  if (glass === "clear") {
    const bulb = new THREE.Mesh(new THREE.SphereGeometry(0.03, 20, 12), bulbGlow());
    bulb.position.y = cy;
    g.add(lit(bulb));
  }
  addGlow(g, [0, cy, 0], lm);
  return tagFixture(g, { name: `${glass} globe pendant`, family: "globePendant", role: over ? "accent" : "ceiling", finish,
    footprint: [d, d], height: drop + 0.025, hang: true });
}

/** The family's entries for lighting.js FIXTURES: { make, kind, doc }. */
export const PENDANTS = {
  globePendant: { make: globePendant, kind: "ceiling",
    doc: "globePendant({ d=0.3, drop=0.7, glass:'opal'|'clear', finish, over=false }) opal or clear glass globe on a cable; over: true above a table or island" },
};
