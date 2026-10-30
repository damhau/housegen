// housekit lighting: table, floor and desk lamps (they stand on a surface or the floor). See lighting.js for the contract.
//
// Frame: the origin at the bottom centre of the base (y = 0 under it), the lamp facing +z: an arm, an
// arc or a head reaches toward +z, into the room when the lamp's back is to a wall. `footprint` is
// the base's (what stands on the floor or the table); an arc or an arm overhangs it by design.
// Lights stay inside the shades, at a modest flux (a table lamp stands near a wall): fabric shades
// throw a cone down and a cone up through their openings and glow a little, opal glows, metal
// shades throw a cone.

import * as THREE from "three";
import { metal, finishes, cyl, lit, shaded, addGlow, addCone, tagFixture, opal, bulbGlow, glowMaterial } from "./lighting-parts.js";
import { finishMaterial } from "./finishes.js";

// ---------------------------------------------------------------- this family's helpers

const _own = new Map();
const own = (key, make) => {
  if (!_own.has(key)) _own.set(key, make());
  return _own.get(key);
};
const UP = new THREE.Vector3(0, 1, 0);
const v3 = (p) => (p.isVector3 ? p.clone() : new THREE.Vector3(...p));
const DEG = Math.PI / 180;

/** Legs of light oak or walnut: the kit's oak veneer, tinted, when the finishes are loaded. */
function wood(kind = "oak") {
  const walnut = kind === "walnut";
  return finishMaterial("oak", { color: walnut ? "#7a583f" : "#dcb98c", roughness: 0.85 })
    ?? own(`wood:${kind}`, () => new THREE.MeshStandardMaterial({ name: `${walnut ? "walnut" : "oak"} wood`, color: walnut ? "#6a4a33" : "#d0ae84", roughness: 0.6 }));
}
/** A white marble base: polished light stone (the kit's marble texture reads beige under warm light). */
const marble = () => own("marble", () => new THREE.MeshStandardMaterial({ name: "white marble", color: "#e4e3df", roughness: 0.22 }));
/** Glazed ceramic in a named glaze or a colour. */
const GLAZES = { white: "#ece8df", sage: "#a4b09a", clay: "#c38d6b", charcoal: "#3d3d3b", sand: "#d8c7a8", blue: "#7f93a3" };
const ceramic = (glaze) => {
  const color = GLAZES[glaze] ?? glaze;
  return own(`ceramic:${color}`, () => new THREE.MeshStandardMaterial({ name: "glazed ceramic", color, roughness: 0.2 }));
};
/** Fabric shades in a named colour or a hex. */
const SHADES = { linen: "#e6dccb", white: "#f4f2ed", grey: "#a9a8a3", sand: "#d8c6a8", charcoal: "#4c4c4b", sage: "#a9b39d" };
/**
 * A lit fabric shade: the cloth's colour, glowing with the warm light through it (made per fixture:
 * the runtime dims it with the fixture). The inside is brighter than the outside.
 */
function fabric(shade, strength) {
  const c = new THREE.Color(SHADES[shade] ?? shade);
  const e = c.clone().multiply(new THREE.Color("#ffc890"));
  return glowMaterial({ color: `#${c.getHexString()}`, emissive: `#${e.getHexString()}`, strength, roughness: 0.95, name: "fabric shade" });
}
/** The lit inside of a metal shade: white, faintly glowing. */
const shadeInside = (strength = 0.5) => glowMaterial({ color: "#f4f2ee", emissive: "#ffe2bf", strength, roughness: 0.6, name: "shade inside" });

/** A rod (a cylinder) from a to b ([x, y, z] or Vector3), radius r at a and r1 at b. */
function rod(g, a, b, r, material, { r1 = r, seg = 12 } = {}) {
  const A = v3(a), B = v3(b), d = B.clone().sub(A), len = d.length();
  const m = shaded(new THREE.Mesh(new THREE.CylinderGeometry(r1, r, len, seg), material));
  m.position.copy(A).addScaledVector(d, 0.5);
  m.quaternion.setFromUnitVectors(UP, d.normalize());
  g.add(m);
  return m;
}
/** A ball (a joint, a finial) at `at`. */
function ball(g, at, r, material, seg = 16) {
  const m = shaded(new THREE.Mesh(new THREE.SphereGeometry(r, seg, Math.max(6, Math.round(seg * 0.6))), material));
  m.position.copy(v3(at));
  g.add(m);
  return m;
}
/** A vertical cylinder whose centre is at [x, y, z]. */
function cylAt(g, r0, r1, h, [x, y, z], material, seg = 16) {
  const m = cyl(g, r0, r1, h, y, material, seg);
  m.position.x = x;
  m.position.z = z;
  return m;
}
/**
 * A solid of revolution about y from a profile [[r, y], ...]: listed bottom to top it faces out (an
 * outside), top to bottom it faces the axis (an inside). `crisp` keeps every corner sharp (machined
 * bases), else the profile shades smooth (spun shades, ceramic).
 */
function lathe(g, profile, material, { seg = 48, crisp = false } = {}) {
  let pts = profile.map(([r, y]) => new THREE.Vector2(Math.max(r, 0), y));
  if (crisp) {
    // a point a hair along the next segment: each corner takes the normals of its own two faces
    pts = pts.flatMap((p, i) => (i > 0 && i < pts.length - 1 ? [p, p.clone().lerp(pts[i + 1], 0.002)] : [p]));
  }
  const m = shaded(new THREE.Mesh(new THREE.LatheGeometry(pts, seg), material));
  g.add(m);
  return m;
}
/** A flat ring at height y facing up (or down): a shade's rolled edge, a rim. */
function ring(g, r0, r1, y, material, up = true, seg = 64) {
  const m = new THREE.Mesh(new THREE.RingGeometry(r0, r1, seg, 1), material);
  m.rotation.x = up ? -Math.PI / 2 : Math.PI / 2;
  m.position.y = y;
  g.add(m);
  return m;
}
/** A fabric shade between yb and yt, radius rb at the bottom and rt at the top (a drum: rb = rt), open both ends. */
function fabricShade(g, { rb, rt, yb, yt, shade, outside = 0.18, inside = 0.7, t = 0.003 }) {
  const out = fabric(shade, outside), inn = fabric(shade, inside);
  lit(lathe(g, [[rb, yb], [rt, yt]], out, { seg: 64 }));
  lit(lathe(g, [[rt - t, yt], [rb - t, yb]], inn, { seg: 64 }));
  lit(ring(g, rt - t, rt + 0.001, yt, out, true));
  lit(ring(g, rb - t, rb + 0.001, yb, out, false));
}
/** The base's footprint, the height from the built mesh. */
function tagStanding(g, { name, family, finish, footprint }) {
  const height = new THREE.Box3().setFromObject(g).max.y;
  return tagFixture(g, { name, family, role: "accent", finish, footprint, height: Math.round(height * 1000) / 1000 });
}

// ---------------------------------------------------------------- table lamps

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

/**
 * Mushroom table lamp (the Scandinavian classic): a dome shade on a slim trumpet foot. `shade` "opal"
 * glows (a cone down and a modest point under the dome), "metal" (in `finish`, white inside) throws a
 * cone down. Role "accent".
 */
function mushroomLamp({ d = 0.32, height = 0.42, shade = "opal", finish = "brass", lm = 250 } = {}) {
  const g = new THREE.Group();
  const m = metal(finish);
  const R = d / 2, sh = d * 0.42, rimY = height - sh, t = 0.004;
  // the foot: wide on the table, flaring up into a slim stem that enters the dome
  const rb = d * 0.3, rs = 0.011, top = rimY + 0.03;
  const foot = [[0, 0], [rb, 0], [rb, 0.004]];
  for (let i = 1; i <= 24; i++) {
    const k = i / 24;
    foot.push([rs + (rb - rs) * Math.pow(1 - k, 3.2), 0.004 + k * (top - 0.004)]);
  }
  foot.push([0, top]);
  lathe(g, foot, m, { seg: 48 });
  // the dome: a half ellipsoid with a wall `t` thick, opal glass or spun metal
  const dome = (r, h) => Array.from({ length: 17 }, (_, k) => {
    const p = (k / 16) * (Math.PI / 2);
    return [r * Math.cos(p), rimY + h * Math.sin(p)];
  });
  const glass = shade === "opal" ? opal(0.75) : null;
  const outM = glass ?? m, inM = glass ?? shadeInside(0.55);
  const o = lathe(g, dome(R, sh), outM, { seg: 56 });
  const i = lathe(g, dome(R - t, sh - t).reverse(), inM, { seg: 56 });
  const r = ring(g, R - t, R, rimY, outM, false);
  if (glass) [o, i, r].forEach(lit);
  // the socket on the stem, a frosted bulb under a metal dome
  cyl(g, 0.019, 0.019, 0.035, top + 0.0175, m, 20);
  if (glass) {
    // the dome sends most of the light down, its glass glows: a modest point (it stands near a wall)
    addCone(g, [0, rimY + 0.06, 0], [0, -1, 0], lm * 0.78, { angle: 1.0, penumbra: 0.85, reach: 4 });
    addGlow(g, [0, rimY + 0.06, 0], lm * 0.22, { reach: 3 });
  } else {
    const bulb = new THREE.Mesh(new THREE.SphereGeometry(0.03, 20, 12), bulbGlow(1.6));
    bulb.position.y = top + 0.055;
    g.add(lit(bulb));
    addCone(g, [0, top + 0.055, 0], [0, -1, 0], lm, { angle: 1.1, penumbra: 0.8, reach: 4 });
  }
  return tagStanding(g, { name: `${shade === "opal" ? "opal" : "metal"} mushroom table lamp`, family: "mushroomLamp", finish, footprint: [d, d] });
}

/**
 * Table lamp with a fabric shade: a glazed ceramic gourd (`base` "ceramic", `glaze` a name or a
 * colour) or a slim metal column (`base` "metal", in `finish`), a drum or cone shade (`shape`) in
 * `shade` (linen, white, grey, sand, charcoal, sage or a colour). Cones up and down through the
 * shade's openings, a weak glow through the cloth. Role "accent".
 */
function drumTableLamp({ shape = "drum", shadeD = 0.38, shade = "linen", base = "ceramic", glaze = "white", finish = "brass", lm = 350 } = {}) {
  const g = new THREE.Group();
  const m = metal(finish);
  const bodyH = 0.3;
  if (base === "metal") {
    lathe(g, [[0, 0], [0.075, 0], [0.075, 0.012], [0.069, 0.018], [0.015, 0.02], [0.015, bodyH], [0, bodyH]], m, { seg: 48, crisp: true });
  } else {
    // a foot ring, a full belly, a narrow neck with a lip
    const ctrl = [[0.052, 0], [0.06, 0.012], [0.086, 0.06], [0.096, 0.12], [0.087, 0.185], [0.055, 0.245], [0.027, 0.276], [0.029, 0.292], [0.026, bodyH]];
    const pts = new THREE.SplineCurve(ctrl.map(([r, y]) => new THREE.Vector2(r, y))).getPoints(36);
    lathe(g, [[0, 0], ...pts.map((p) => [p.x, p.y]), [0, bodyH]], ceramic(glaze), { seg: 48 });
  }
  // the neck, the socket and the bulb, inside the shade
  cyl(g, 0.011, 0.011, 0.035, bodyH + 0.0175, m, 16);
  cyl(g, 0.017, 0.017, 0.05, bodyH + 0.06, m, 20);
  const yBulb = bodyH + 0.115;
  const bulb = new THREE.Mesh(new THREE.SphereGeometry(0.03, 20, 12), bulbGlow(1.4));
  bulb.position.y = yBulb;
  g.add(lit(bulb));
  // the shade, its bottom level with the socket's
  const rb = shadeD / 2, rt = shape === "cone" ? rb * 0.68 : rb, hh = shape === "cone" ? 0.24 : 0.25;
  const yb = bodyH + 0.04, yt = yb + hh;
  fabricShade(g, { rb, rt, yb, yt, shade });
  // the fitter seen from above: a ring on the socket, three spokes up to the top edge
  const ringY = bodyH + 0.085;
  for (let k = 0; k < 3; k++) {
    const a = (k * 2 * Math.PI) / 3 + Math.PI / 2;
    rod(g, [0.018 * Math.cos(a), ringY, 0.018 * Math.sin(a)], [(rt - 0.005) * Math.cos(a), yt - 0.004, (rt - 0.005) * Math.sin(a)], 0.0018, m, { seg: 6 });
  }
  const fit = shaded(new THREE.Mesh(new THREE.TorusGeometry(0.019, 0.0025, 6, 24), m));
  fit.rotation.x = Math.PI / 2;
  fit.position.y = ringY;
  g.add(fit);
  // light: down through the bottom opening, up through the top, a little through the cloth
  const at = [0, yBulb, 0];
  addCone(g, at, [0, -1, 0], lm * 0.45, { angle: Math.min(1.05, Math.atan2(rb, yBulb - yb)), penumbra: 0.9, reach: 4 });
  addCone(g, at, [0, 1, 0], lm * 0.43, { angle: Math.atan2(rt, yt - yBulb) * 0.9, penumbra: 0.9, reach: 4 });
  addGlow(g, at, lm * 0.12, { reach: 3 });
  return tagStanding(g, { name: `${shape} shade table lamp`, family: "drumTableLamp", finish, footprint: [shadeD, shadeD] });
}

/**
 * Articulated desk lamp: a round weighted foot, two arms of twin rods with hinged joints, a conical
 * metal shade (white inside) aimed `tilt` degrees forward of straight down, all in `finish`; the arms
 * swivel `turn` degrees from +z toward +x on the foot. A cone from the shade. Role "accent".
 */
function deskLamp({ finish = "black", arm = 0.36, shadeD = 0.15, tilt = 12, turn = 30, lm = 200 } = {}) {
  const g = new THREE.Group();
  const m = metal(finish);
  lathe(g, [[0, 0], [0.085, 0], [0.085, 0.016], [0.079, 0.022], [0.022, 0.024], [0.022, 0.04], [0, 0.04]], m, { seg: 48, crisp: true });
  // everything above the foot swivels on it
  const swing = new THREE.Group();
  swing.rotation.y = turn * DEG;
  g.add(swing);
  const knuckle = (J) => {
    rod(swing, [J.x - 0.019, J.y, J.z], [J.x + 0.019, J.y, J.z], 0.011, m, { seg: 16 });
    ball(swing, [J.x - 0.019, J.y, J.z], 0.006, m, 10);
    ball(swing, [J.x + 0.019, J.y, J.z], 0.006, m, 10);
  };
  const twin = (A, B, r = 0.0042) => {
    for (const s of [-1, 1]) rod(swing, [A.x + s * 0.012, A.y, A.z], [B.x + s * 0.012, B.y, B.z], r, m, { seg: 8 });
  };
  // the post on the foot, the lower arm leaning back, the upper arm reaching forward and a little down
  const J0 = new THREE.Vector3(0, 0.08, 0);
  rod(swing, [0, 0.04, 0], J0, 0.008, m);
  const e1 = 66 * DEG, e2 = -6 * DEG;
  const J1 = J0.clone().add(new THREE.Vector3(0, Math.sin(e1), -Math.cos(e1)).multiplyScalar(arm));
  const J2 = J1.clone().add(new THREE.Vector3(0, Math.sin(e2), Math.cos(e2)).multiplyScalar(arm * 0.95));
  twin(J0, J1);
  twin(J1, J2);
  // a tension spring from the foot to the lower arm (the articulated lamp's mark)
  const s0 = new THREE.Vector3(0, 0.024, -0.05), s1 = J0.clone().lerp(J1, 0.45);
  rod(swing, s0, s1, 0.005, finishes.chrome(), { seg: 8 });
  rod(swing, [-0.012, s1.y, s1.z], [0.012, s1.y, s1.z], 0.003, m, { seg: 6 }); // the pin it hooks on
  [J0, J1, J2].forEach(knuckle);
  // the head: a short neck down from the joint, the shade on it, all turned by `tilt`
  const head = new THREE.Group();
  head.position.copy(J2);
  head.rotation.x = -tilt * DEG;
  swing.add(head);
  rod(head, [0, 0, 0], [0, -0.03, 0], 0.006, m, { seg: 10 });
  const R = shadeD / 2, L = shadeD * 0.95, yB = -0.03, yM = yB - L, t = 0.0025;
  const outer = [[R, yM], [R * 0.93, yM + 0.022], [0.046, yB - 0.012], [0.036, yB - 0.002], [0.022, yB + 0.007], [0, yB + 0.01]];
  lathe(head, outer, m, { seg: 48 });
  lit(lathe(head, [[0, yB + 0.006], [0.02, yB + 0.003], [0.034, yB - 0.005], [0.044, yB - 0.014], [R * 0.93 - t, yM + 0.022], [R - t, yM]], shadeInside(0.6), { seg: 48 }));
  ring(head, R - t, R, yM, m, false, 48);
  // a handle at the back of the shade, a frosted bulb inside
  rod(head, [0, yB - 0.03, -0.042], [0, yB - 0.012, -0.075], 0.0035, m, { seg: 8 });
  ball(head, [0, yB - 0.012, -0.075], 0.008, m, 12);
  const bulb = new THREE.Mesh(new THREE.SphereGeometry(0.026, 20, 12), opal(1.4));
  bulb.position.y = yB - 0.045;
  head.add(lit(bulb));
  addCone(head, [0, yB - 0.05, 0], [0, -1, 0], lm, { angle: 0.65, penumbra: 0.65, reach: 5 });
  return tagStanding(g, { name: "articulated desk lamp", family: "deskLamp", finish, footprint: [0.17, 0.17] });
}

// ---------------------------------------------------------------- floor lamps

/**
 * Floor lamp: a slim stem on a round plate, an opal globe on a short arm swung `turn` degrees from +z
 * toward +x, all in `finish`; `height` to the stem's top, `d` the globe, `arm` its reach. A glow in
 * the globe. Role "accent".
 */
function globeFloorLamp({ height = 1.5, d = 0.2, arm = 0.2, turn = 35, finish = "brass", lm = 450 } = {}) {
  const g = new THREE.Group();
  const m = metal(finish);
  const R = 0.13;
  lathe(g, [[0, 0], [R, 0], [R, 0.014], [R - 0.006, 0.02], [0.022, 0.021], [0.022, 0.04], [0, 0.04]], m, { seg: 56, crisp: true });
  rod(g, [0, 0.04, 0], [0, height - 0.018, 0], 0.008, m, { seg: 16 });
  ball(g, [0, height - 0.011, 0], 0.011, m);
  // the arm, turning on the stem: a sleeve, a rod, a cup holding the globe by its neck
  const gy = height - 0.13;
  const swing = new THREE.Group();
  swing.rotation.y = turn * DEG;
  g.add(swing);
  cyl(swing, 0.012, 0.012, 0.04, gy, m, 16);
  rod(swing, [0, gy, 0], [0, gy, arm], 0.006, m, { seg: 12 });
  rod(swing, [0, gy, arm - 0.005], [0, gy, arm + 0.045], 0.015, m, { r1: 0.024, seg: 24 });
  const gz = arm + 0.045 + d / 2 - 0.012;
  const globe = new THREE.Mesh(new THREE.SphereGeometry(d / 2, 40, 24), opal(1.0));
  globe.position.set(0, gy, gz);
  swing.add(lit(globe));
  addGlow(swing, [0, gy, gz], lm, { reach: 5 });
  return tagStanding(g, { name: "opal globe floor lamp", family: "globeFloorLamp", finish, footprint: [2 * R, 2 * R] });
}

/**
 * Arc floor lamp: a heavy round base (`base` "marble" or "metal"), a tube rising and arching
 * `reach` metres toward +z, `height` at its top, a dome shade (`shadeD`, white inside) hanging at its
 * end; the tube and the shade in `finish`. A cone down from the shade. Role "accent".
 */
function arcFloorLamp({ reach = 1.6, height = 2.1, shadeD = 0.4, finish = "black", base = "marble", lm = 600 } = {}) {
  const g = new THREE.Group();
  const m = metal(finish);
  const BR = 0.18, bh = base === "marble" ? 0.075 : 0.045;
  lathe(g, [[0, 0], [BR, 0], [BR, bh - 0.008], [BR - 0.008, bh], [0, bh]], base === "marble" ? marble() : m, { seg: 64, crisp: true });
  cyl(g, 0.022, 0.027, 0.05, bh + 0.025, m, 20);
  // the arc: straight up, then part of an ellipse over its top and down to the shade (its end ~50° below level)
  const te = 0.8, drop = 0.3;
  const b = drop / (1 - Math.sin(te)), a = reach / (1 + Math.cos(te)), y1 = Math.max(bh + 0.3, height - b);
  const y0 = bh + 0.04;
  const straight = y1 - y0;
  const ellipse = (u) => {
    const th = Math.PI - u * (Math.PI - te);
    return new THREE.Vector3(0, y1 + b * Math.sin(th), a + a * Math.cos(th));
  };
  let curved = 0;
  for (let k = 1, p = ellipse(0); k <= 64; k++) { const q = ellipse(k / 64); curved += q.distanceTo(p); p = q; }
  const split = straight / (straight + curved);
  const path = new THREE.Curve();
  path.getPoint = (u, out = new THREE.Vector3()) => (u < split
    ? out.set(0, y0 + (u / split) * straight, 0)
    : out.copy(ellipse((u - split) / (1 - split))));
  g.add(shaded(new THREE.Mesh(new THREE.TubeGeometry(path, 120, 0.012, 12, false), m)));
  // the shade hangs from the end on a swivel
  const end = ellipse(1);
  ball(g, end, 0.017, m);
  const top = end.y - 0.04;
  rod(g, end, [0, top, end.z], 0.01, m);
  const Rs = shadeD / 2, hs = shadeD * 0.45, neck = 0.03, t = 0.003;
  const pe = Math.acos(neck / Rs);
  const rim = top - hs * Math.sin(pe);
  const dome = (r, h) => Array.from({ length: 15 }, (_, k) => {
    const p = (k / 14) * pe;
    return [r * Math.cos(p), rim + h * Math.sin(p)];
  });
  const shade = new THREE.Group();
  shade.position.z = end.z;
  g.add(shade);
  lathe(shade, dome(Rs, hs), m, { seg: 56 });
  lit(lathe(shade, dome(Rs - t, hs - t).reverse(), shadeInside(0.5), { seg: 56 }));
  ring(shade, Rs - t, Rs, rim, m, false);
  cyl(shade, neck + 0.002, neck + 0.002, 0.03, top - 0.012, m, 24);
  const bulb = new THREE.Mesh(new THREE.SphereGeometry(0.032, 20, 12), bulbGlow(1.6));
  bulb.position.y = rim + 0.08;
  shade.add(lit(bulb));
  addCone(shade, [0, rim + 0.06, 0], [0, -1, 0], lm, { angle: 0.7, penumbra: 0.75, reach: 6 });
  return tagStanding(g, { name: "arc floor lamp", family: "arcFloorLamp", finish, footprint: [2 * BR, 2 * BR] });
}

/**
 * Tripod floor lamp: three slim splayed legs (`legs` "oak", "walnut" or "metal" in `finish`) meeting
 * in a collar, a fabric drum shade (`shade` linen, white, grey, sand, charcoal, sage or a colour).
 * A cone down, a cone up, a weak glow through the cloth. Role "accent".
 */
function tripodFloorLamp({ height = 1.55, shadeD = 0.45, shadeH = 0.3, shade = "linen", legs = "oak", finish = "black", lm = 600 } = {}) {
  const g = new THREE.Group();
  const m = metal(finish);
  const legM = legs === "metal" ? m : wood(legs);
  const R = shadeD / 2, yb = height - shadeH, hubY = yb - 0.07, spread = 0.3;
  // one leg behind, two in front; a rubber pad under each
  for (const a of [Math.PI, Math.PI / 3, -Math.PI / 3]) {
    const x = Math.sin(a) * spread, z = Math.cos(a) * spread;
    rod(g, [x, 0.01, z], [x * 0.05, hubY - 0.02, z * 0.05], 0.014, legM, { r1: 0.01, seg: 12 });
    cylAt(g, 0.014, 0.016, 0.012, [x, 0.006, z], finishes.black(), 12);
  }
  cyl(g, 0.024, 0.024, 0.06, hubY, m, 20);
  // the stem from the collar to the socket, the bulb at the shade's lower third
  const yBulb = yb + 0.12;
  rod(g, [0, hubY + 0.03, 0], [0, yBulb - 0.05, 0], 0.007, m);
  cyl(g, 0.018, 0.018, 0.05, yBulb - 0.05, m, 20);
  const bulb = new THREE.Mesh(new THREE.SphereGeometry(0.03, 20, 12), bulbGlow(1.4));
  bulb.position.y = yBulb;
  g.add(lit(bulb));
  fabricShade(g, { rb: R, rt: R, yb, yt: height, shade });
  // the fitter: a ring on the socket, three spokes up to the top edge
  for (let k = 0; k < 3; k++) {
    const a = (k * 2 * Math.PI) / 3 + Math.PI / 2;
    rod(g, [0.02 * Math.cos(a), yBulb - 0.025, 0.02 * Math.sin(a)], [(R - 0.005) * Math.cos(a), height - 0.004, (R - 0.005) * Math.sin(a)], 0.002, m, { seg: 6 });
  }
  const at = [0, yBulb, 0];
  addCone(g, at, [0, -1, 0], lm * 0.45, { angle: Math.min(1.05, Math.atan2(R, yBulb - yb)), penumbra: 0.85, reach: 6 });
  addCone(g, at, [0, 1, 0], lm * 0.4, { angle: Math.atan2(R, height - yBulb) * 0.9, penumbra: 0.85, reach: 5 });
  addGlow(g, at, lm * 0.15, { reach: 4 });
  return tagStanding(g, { name: "tripod floor lamp", family: "tripodFloorLamp", finish, footprint: [2 * spread * Math.sin(Math.PI / 3) + 0.03, 1.5 * spread + 0.03] });
}

/** The family's entries for lighting.js FIXTURES: { make, kind, doc }. */
export const STANDING = {
  globeTableLamp: { make: globeTableLamp, kind: "standing",
    doc: "globeTableLamp({ d=0.2, finish }) opal globe on a small base: bedside, sideboard" },
  mushroomLamp: { make: mushroomLamp, kind: "standing",
    doc: "mushroomLamp({ d=0.32, height=0.42, shade:'opal'|'metal', finish='brass' }) Scandinavian dome shade on a trumpet foot (opal glows, metal throws light down): sideboard, bedside" },
  drumTableLamp: { make: drumTableLamp, kind: "standing",
    doc: "drumTableLamp({ shape:'drum'|'cone', shadeD=0.38, shade='linen'|'white'|'grey'|'sand'|'charcoal'|'sage'|'#hex', base:'ceramic'|'metal', glaze='white'|'sage'|'clay'|'charcoal'|'sand'|'blue'|'#hex', finish='brass' }) fabric shade on a ceramic gourd or a metal column, 0.59 m: living room, bedside" },
  deskLamp: { make: deskLamp, kind: "standing",
    doc: "deskLamp({ finish='black', arm=0.36, shadeD=0.15, tilt=12, turn=30 }) articulated twin-rod arms swivelled `turn` degrees from +z toward +x, conical metal shade aimed down: desk, bedside" },
  globeFloorLamp: { make: globeFloorLamp, kind: "standing",
    doc: "globeFloorLamp({ height=1.5, d=0.2, arm=0.2, turn=35, finish='brass' }) slim stem, opal globe on a short arm swung `turn` degrees from +z toward +x: reading corner, beside a sofa or an armchair" },
  arcFloorLamp: { make: arcFloorLamp, kind: "standing",
    doc: "arcFloorLamp({ reach=1.6, height=2.1, shadeD=0.4, base:'marble'|'metal', finish='black' }) arched tube from a heavy base, dome shade `reach` m toward +z, ~1.6 m up: over a sofa or a coffee table" },
  tripodFloorLamp: { make: tripodFloorLamp, kind: "standing",
    doc: "tripodFloorLamp({ height=1.55, shadeD=0.45, shade='linen'|'white'|'grey'|'sand'|'charcoal'|'sage'|'#hex', legs:'oak'|'walnut'|'metal', finish='black' }) three splayed legs, fabric drum shade: living-room corner, beside a sofa" },
};
