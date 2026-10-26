// housekit lighting: wall lights (their back plate on the wall, facing +z into the room). See lighting.js for the contract.
//
// Frame: the origin is the centre of the back plate on the wall, +z into the room, nothing behind z = 0.
// Light: a wall light washes the wall with cones grazing it (the scallop up and/or down); a point light
// only inside an opal globe held at least 15 cm off the wall. Every family takes `finish` (any of
// FINISHES), `light` ('warm' | 'neutral'), `lm` (its flux) and `role` (default "accent").

import * as THREE from "three";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
import { metal, cyl, lit, shaded, addGlow, addCone, tagFixture, opal, bulbGlow, glowMaterial, WARM, NEUTRAL } from "./lighting-parts.js";

// ---------------------------------------------------------------- helpers (this family's own)

const UP = new THREE.Vector3(0, 1, 0);
const tint = (light) => (light === "neutral" ? NEUTRAL : WARM);
const unit = (v) => { const l = Math.hypot(...v); return v.map((c) => c / l); };

// brushed aluminium: the mirror bar's profile (not one of the shared FINISHES, so kept here)
let _alu = null;
const aluminium = () => (_alu ??= new THREE.MeshStandardMaterial({ name: "brushed aluminium", color: "#c4c7ca", roughness: 0.38, metalness: 1 }));
/** A finish by name: the shared ones, plus "aluminium". */
const finishOf = (name) => (name === "aluminium" ? aluminium() : metal(name));

/** A mesh added to `g` at `at`, casting and receiving shadows. */
function put(g, geometry, material, at = [0, 0, 0]) {
  const m = shaded(new THREE.Mesh(geometry, material));
  m.position.set(...at);
  g.add(m);
  return m;
}
/** A box with softened edges, `size` [w, h, d], centred at `at`. */
const softBox = (g, size, at, material, radius = 0.003) =>
  put(g, new RoundedBoxGeometry(size[0], size[1], size[2], 2, Math.min(radius, ...size.map((s) => s / 2.01))), material, at);
/** A disc or a short cylinder whose axis is z, from z0 to z0 + t, centred on (x, y). */
function discZ(g, r, t, z0, material, { x = 0, y = 0, seg = 32, r1 = r } = {}) {
  const m = put(g, new THREE.CylinderGeometry(r1, r, t, seg), material, [x, y, z0 + t / 2]);
  m.rotation.x = Math.PI / 2; // the cylinder's top (r1) faces +z
  return m;
}
/** A rod of radius `r` from `a` to `b`. */
function rod(g, a, b, r, material, seg = 12) {
  const A = new THREE.Vector3(...a), d = new THREE.Vector3(...b).sub(A);
  const m = put(g, new THREE.CylinderGeometry(r, r, d.length(), seg), material);
  m.position.copy(A).addScaledVector(d, 0.5);
  m.quaternion.setFromUnitVectors(UP, d.normalize());
  return m;
}
const ball = (g, r, at, material) => put(g, new THREE.SphereGeometry(r, 16, 10), material, at);
/** The same surface seen from the other side (an inside lining): winding and normals flipped. */
function inverted(geo) {
  const g = geo.index ? geo.toNonIndexed() : geo.clone();
  const p = g.attributes.position, n = g.attributes.normal;
  for (let i = 0; i < p.count; i += 3) {
    for (const a of [p, n]) {
      const t = [a.getX(i + 1), a.getY(i + 1), a.getZ(i + 1)];
      a.setXYZ(i + 1, a.getX(i + 2), a.getY(i + 2), a.getZ(i + 2));
      a.setXYZ(i + 2, ...t);
    }
  }
  for (let i = 0; i < n.count; i++) n.setXYZ(i, -n.getX(i), -n.getY(i), -n.getZ(i));
  return g;
}
/** Flat polygons (lists of THREE.Vector3, convex) as one geometry, each wound to face `out` of its own. */
function facets(polys, offset = 0) {
  const pos = [];
  for (const { pts, out } of polys) {
    const q = pts.map((p) => p.clone().addScaledVector(out, -offset));
    const n = new THREE.Vector3().subVectors(q[1], q[0]).cross(new THREE.Vector3().subVectors(q[2], q[0]));
    if (n.dot(out) < 0) q.reverse();
    for (let i = 1; i < q.length - 1; i++) pos.push(...q[0].toArray(), ...q[i].toArray(), ...q[i + 1].toArray());
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.computeVertexNormals();
  return g;
}
const tag = (g, family, name, role, finish, box) =>
  tagFixture(g, { name, family, role, finish, footprint: [box.w, box.d], height: box.h, hang: true, wall: true });

// ---------------------------------------------------------------- the families

/**
 * Hexagonal sconce: a cube stood on its corner, its back corner cut off by the wall, so it reads as a
 * hexagon from the room (three facets meeting at the front). `finish` outside (black), `inner` inside
 * (brass). Open at the bottom front facet (the brass inside and the bulb show) and at the top facet
 * against the wall. `d` the hexagon's height point to point. A cone up and a cone down along the wall.
 */
function hexSconce({ d = 0.17, finish = "black", inner = "brass", lm = 360, light = "warm", role = "accent" } = {}) {
  const g = new THREE.Group();
  const a = d / (4 * Math.sqrt(2 / 3)); // the cube's half side: the hexagon's circumradius is 2a·√(2/3)
  const s = Math.sqrt(2 / 3), c = 1 / Math.sqrt(3);
  // the cube's axes, symmetric about z: e1 points down (and out), e2 up-right, e3 up-left
  const u = [[0, -1], [Math.cos(Math.PI / 6), 0.5], [-Math.cos(Math.PI / 6), 0.5]];
  const e = u.map(([x, y]) => new THREE.Vector3(s * x, s * y, c));
  const C = new THREE.Vector3(0, 0, a * c + 0.0015); // its centre: the back corner (−,−,−) is cut by the wall (the rims clear it)
  const v = (s1, s2, s3) => C.clone().addScaledVector(e[0], a * s1).addScaledVector(e[1], a * s2).addScaledVector(e[2], a * s3);
  const neg = (x) => x.clone().negate();
  const closed = [
    { pts: [v(1, 1, 1), v(-1, 1, 1), v(-1, 1, -1), v(1, 1, -1)], out: e[1] }, // the right front facet
    { pts: [v(1, 1, 1), v(1, -1, 1), v(-1, -1, 1), v(-1, 1, 1)], out: e[2] }, // the left front facet
    { pts: [v(1, -1, 1), v(-1, -1, 1), v(1, -1, -1)], out: neg(e[1]) }, // lower left, back to the wall
    { pts: [v(1, 1, -1), v(1, -1, -1), v(-1, 1, -1)], out: neg(e[2]) }, // lower right, back to the wall
  ];
  // open: e1 (the lower front facet) and −e1 (the top facet, leaning back to the wall)
  const sheet = 0.0012;
  put(g, facets(closed), metal(finish));
  // the lining: the inner finish, lit by the bulb while the sconce is on (a glow material: dark when off)
  const base = metal(inner), lining = glowMaterial({ color: `#${base.color.getHexString()}`, strength: 0.55, roughness: base.roughness,
    emissive: `#${base.color.clone().lerp(new THREE.Color("#fff3e0"), 0.35).getHexString()}`, name: "sconce lining" });
  lining.metalness = base.metalness;
  put(g, inverted(facets(closed, sheet)), lining);
  // the folded edges round the openings: a thin rim so the sheet reads as metal, not paper
  const rimAt = (p, q) => rod(g, p.toArray(), q.toArray(), sheet * 1.1, metal(finish), 6);
  rimAt(v(1, 1, 1), v(1, 1, -1));
  rimAt(v(1, 1, -1), v(1, -1, -1));
  rimAt(v(1, -1, -1), v(1, -1, 1));
  rimAt(v(1, -1, 1), v(1, 1, 1));
  rimAt(v(-1, 1, 1), v(-1, 1, -1));
  rimAt(v(-1, 1, 1), v(-1, -1, 1));
  // the mount inside: a round plate on the wall, a socket, a bulb seen through the bottom
  discZ(g, 0.028, 0.006, 0, metal(finish));
  discZ(g, 0.011, 0.03, 0.006, metal(finish), { seg: 16 });
  const bulb = put(g, new THREE.SphereGeometry(0.019, 20, 12), bulbGlow(1.8), [0, -0.004, 0.054]);
  lit(bulb);
  // the scallops: up through the top opening, down through the bottom one, both grazing the wall
  addCone(g, [0, 0.02, 0.034], [0, 1, 0], lm * 0.45, { angle: 0.68, penumbra: 0.85, color: tint(light), reach: 4 });
  addCone(g, [0, -0.03, 0.048], unit([0, -1, 0.12]), lm * 0.55, { angle: 0.72, penumbra: 0.85, color: tint(light), reach: 4 });
  const w = 4 * a * s * Math.cos(Math.PI / 6);
  return tag(g, "hexSconce", "hexagonal wall sconce", role, finish, { w, d: a * (c + Math.sqrt(3)), h: d });
}

/**
 * Up-and-down wall light: a cylinder held off the wall by a short bracket, open at both ends behind a
 * lens. `d` its diameter, `h` its height, `beam` the cones' half-angle (narrow: a crisp scallop).
 * Two cones grazing the wall, up and down.
 */
function upDown({ d = 0.07, h = 0.16, beam = 0.36, finish = "black", lm = 320, light = "warm", role = "accent" } = {}) {
  const g = new THREE.Group();
  const m = metal(finish), r = d / 2, zc = 0.012 + 0.018 + r; // the plate, the bracket's gap, the tube
  softBox(g, [Math.min(0.05, d * 0.7), Math.min(0.11, h * 0.68), 0.012], [0, 0, 0.006], m, 0.004);
  softBox(g, [0.022, 0.03, zc - 0.01], [0, 0, 0.01 + (zc - 0.01) / 2], m, 0.003);
  const tube = new THREE.CylinderGeometry(r, r, h, 40, 1, true);
  put(g, tube, m, [0, 0, zc]);
  put(g, inverted(new THREE.CylinderGeometry(r - 0.0015, r - 0.0015, h, 40, 1, true)), m, [0, 0, zc]);
  for (const sgn of [1, -1]) {
    const rim = put(g, new THREE.RingGeometry(r - 0.0015, r, 40, 1), m, [0, sgn * h / 2, zc]);
    rim.rotation.x = -sgn * Math.PI / 2;
    // the lens, a little inside the tube's mouth
    const lens = put(g, new THREE.CircleGeometry(r - 0.004, 32), glowMaterial({ strength: 1.6, name: "wall light lens" }), [0, sgn * (h / 2 - 0.014), zc]);
    lens.rotation.x = -sgn * Math.PI / 2;
    lit(lens);
    addCone(g, [0, sgn * (h / 2 - 0.02), zc], [0, sgn, 0], lm / 2, { angle: beam, penumbra: 0.65, color: tint(light), reach: 4 });
  }
  return tag(g, "upDown", "up-down wall light", role, finish, { w: d, d: zc + r, h });
}

/**
 * Globe sconce: an opal globe sitting in a cup at the end of a short arm, on a round back plate
 * (`finish` brass or black). `d` the globe's diameter, `arm` its reach (at least 14 cm, so the glow
 * sits 15 cm off the wall). A modest glow inside the globe.
 */
function globeSconce({ d = 0.15, arm = 0.2, finish = "brass", lm = 140, light = "warm", role = "accent" } = {}) {
  const g = new THREE.Group();
  const m = metal(finish), R = d / 2, plateT = 0.014;
  const reach = Math.max(arm, 0.15 - plateT); // the glow at least 15 cm off the wall
  discZ(g, 0.05, plateT, 0, m, { seg: 40, r1: 0.047 });
  const end = plateT + reach;
  rod(g, [0, 0, plateT], [0, 0, end], 0.0065, m);
  ball(g, 0.011, [0, 0, end], m);
  rod(g, [0, 0, end], [0, 0.022, end], 0.0065, m);
  cyl(g, 0.026, 0.017, 0.032, 0.022 + 0.016, m, 32).position.z = end; // the cup the globe sits in
  const cy = 0.022 + 0.032 + R * 0.9;
  const globe = put(g, new THREE.SphereGeometry(R, 40, 24), opal(3.2), [0, cy, end]);
  lit(globe);
  addGlow(g, [0, cy, end + R * 0.6], lm, { color: tint(light), reach: 4 }); // toward the globe's front: a soft halo on the wall
  return tag(g, "globeSconce", "opal globe wall sconce", role, finish, { w: Math.max(d, 0.1), d: end + R, h: cy + R + 0.05 });
}

/**
 * Swing-arm reading light: a slim back plate with a pivot, two arm segments joined by a hinge (folded
 * in plan, like a swung-out reading arm), a conical metal shade aimed down and forward. `arm` the two
 * segments' length (0.25–0.7), `aim` the shade's tilt from straight down toward the room (radians).
 * A cone from the shade.
 */
function swingArm({ arm = 0.5, aim = 0.45, finish = "black", lm = 350, light = "warm", role = "accent" } = {}) {
  const g = new THREE.Group();
  const m = metal(finish), A = Math.min(0.7, Math.max(0.25, arm));
  softBox(g, [0.05, 0.13, 0.012], [0, 0, 0.006], m, 0.005);
  // the pivot on the plate: two tabs and a knuckle
  const z0 = 0.03;
  for (const y of [0.026, -0.026]) softBox(g, [0.018, 0.01, z0 - 0.012], [0, y, 0.012 + (z0 - 0.012) / 2], m, 0.002);
  cyl(g, 0.011, 0.011, 0.06, 0, m, 20).position.z = z0;
  // the arm, folded in plan: out to the right, then back across toward the middle
  const s1 = A * 0.55, s2 = A * 0.45, y1 = 0.35, y2 = -1.2;
  const P0 = [0, 0.012, z0], P1 = [s1 * Math.sin(y1), 0.012, z0 + s1 * Math.cos(y1)];
  const P2 = [P1[0] + s2 * Math.sin(y2), 0.012, P1[2] + s2 * Math.cos(y2)];
  rod(g, P0, P1, 0.0055, m);
  cyl(g, 0.01, 0.01, 0.045, 0, m, 20).position.set(P1[0], 0, P1[2]); // the hinge
  for (const y of [0.0235, -0.0235]) cyl(g, 0.0115, 0.0115, 0.002, y, m, 20).position.set(P1[0], y, P1[2]);
  rod(g, [P1[0], -0.012, P1[2]], [P2[0], -0.012, P2[2]], 0.0055, m);
  ball(g, 0.012, [P2[0], -0.012, P2[2]], m);
  // the shade, hung from a short stem and tilted toward the room
  const top = [P2[0], -0.052, P2[2]];
  rod(g, [P2[0], -0.012, P2[2]], top, 0.005, m);
  const dir = new THREE.Vector3(0, -Math.cos(aim), Math.sin(aim));
  const shade = new THREE.Group();
  shade.position.set(...top);
  shade.quaternion.setFromUnitVectors(new THREE.Vector3(0, -1, 0), dir);
  const H = 0.13, rt = 0.022, rb = 0.068;
  cyl(shade, rt + 0.003, rt + 0.003, 0.026, -0.008, m, 32); // the cap with the socket
  const cone = new THREE.CylinderGeometry(rt, rb, H, 40, 1, true);
  put(shade, cone, m, [0, -0.021 - H / 2, 0]);
  const lining = put(shade, inverted(new THREE.CylinderGeometry(rt - 0.0012, rb - 0.0012, H, 40, 1, true)),
    glowMaterial({ color: "#f3f1ec", emissive: "#ffe2bd", strength: 0.35, name: "shade lining" }), [0, -0.021 - H / 2, 0]);
  lit(lining);
  const lip = put(shade, new THREE.TorusGeometry(rb, 0.0018, 6, 48), m, [0, -0.021 - H, 0]);
  lip.rotation.x = Math.PI / 2;
  const b = put(shade, new THREE.SphereGeometry(0.018, 16, 10), bulbGlow(1.6), [0, -0.05, 0]);
  lit(b);
  g.add(shade);
  shade.updateMatrix();
  const from = new THREE.Vector3(0, -0.06, 0).applyMatrix4(shade.matrix);
  addCone(g, from.toArray(), dir.toArray(), lm, { angle: 0.58, penumbra: 0.5, color: tint(light), reach: 5 });
  const xs = [0.025, P1[0] + 0.012, P2[0] + rb, P2[0] - rb];
  return tag(g, "swingArm", "swing-arm wall lamp", role, finish,
    { w: Math.max(...xs) - Math.min(...xs, -0.025), d: P2[2] + rb + Math.sin(aim) * H, h: 0.052 + Math.cos(aim) * H + rb });
}

/**
 * Picture light: a slim bar on a curved arm from a small plate, held out over a picture below it.
 * `w` the bar's width (0.3–1), `out` how far the bar stands off the wall, `finish` brass or black.
 * One cone (two from 45 cm wide) down and back onto the wall below.
 */
function pictureLight({ w = 0.5, out = 0.17, finish = "brass", lm = 260, light = "warm", role = "accent" } = {}) {
  const g = new THREE.Group();
  const m = metal(finish), W = Math.min(1, Math.max(0.3, w));
  discZ(g, 0.024, 0.01, 0, m, { seg: 32, r1: 0.022 });
  // the arm: up and out from the plate, curving down to the bar
  const curve = new THREE.CubicBezierCurve3(
    new THREE.Vector3(0, 0, 0.008), new THREE.Vector3(0, 0.07, 0.03),
    new THREE.Vector3(0, 0.1, out - 0.05), new THREE.Vector3(0, 0.05, out));
  put(g, new THREE.TubeGeometry(curve, 24, 0.0048, 10, false), m);
  ball(g, 0.0075, [0, 0.05, out], m);
  // the bar: a slim tube with closed ends, its diffuser underneath (turned a little to the wall)
  const by = 0.034, br = 0.0145;
  const bar = put(g, new THREE.CylinderGeometry(br, br, W, 28), m, [0, by, out]);
  bar.rotation.z = Math.PI / 2;
  rod(g, [0, 0.05, out], [0, by + br * 0.6, out], 0.004, m, 8);
  for (const x of [W / 2, -W / 2]) {
    const cap = put(g, new THREE.CylinderGeometry(br * 1.08, br * 1.08, 0.004, 28), m, [x, by, out]);
    cap.rotation.z = Math.PI / 2;
  }
  const strip = softBox(g, [W - 0.03, 0.005, 0.014], [0, by - br + 0.0012, out - 0.003],
    glowMaterial({ strength: 1.5, name: "picture light diffuser" }), 0.002);
  strip.rotation.x = 0.35;
  lit(strip);
  const n = W >= 0.45 ? 2 : 1, dir = unit([0, -1, -0.4]);
  for (let i = 0; i < n; i++) {
    const x = n === 1 ? 0 : (i ? 1 : -1) * W / 4;
    addCone(g, [x, by - br - 0.006, out - 0.004], dir, lm / n, { angle: n === 1 ? 0.62 : 0.55, penumbra: 0.75, color: tint(light), reach: 4 });
  }
  return tag(g, "pictureLight", "brass picture light", role, finish, { w: W, d: out + br, h: 0.11 });
}

/**
 * Mirror light bar: a slim profile over a bathroom mirror, an opal diffuser underneath, like the WC's
 * in the benchmark. `w` its width (0.4–1.2), `finish` 'aluminium' (default) or any finish, `light`
 * 'neutral' (default) or 'warm'. Two cones down and slightly forward (one under 50 cm).
 */
function mirrorBar({ w = 0.6, finish = "aluminium", light = "neutral", lm, role = "accent" } = {}) {
  const g = new THREE.Group();
  const W = Math.min(1.2, Math.max(0.4, w)), m = finishOf(finish), flux = lm ?? Math.round(800 * W);
  const depth = 0.09, hh = 0.02, z0 = 0.014;
  softBox(g, [0.11, 0.045, z0], [0, 0, z0 / 2], m, 0.003); // the wall mount
  softBox(g, [W, hh, depth], [0, 0, z0 + depth / 2], m, 0.0035); // the profile
  const diffuser = softBox(g, [W - 0.012, 0.004, depth - 0.014], [0, -hh / 2 - 0.0012, z0 + depth / 2],
    glowMaterial({ color: "#f7f6f2", emissive: light === "neutral" ? "#fff1e2" : "#ffe7c8", strength: 1.8, name: "opal diffuser" }), 0.0015);
  lit(diffuser);
  const n = W >= 0.5 ? 2 : 1, dir = unit([0, -1, 0.35]);
  for (let i = 0; i < n; i++) {
    const x = n === 1 ? 0 : (i ? 1 : -1) * W / 4;
    addCone(g, [x, -hh / 2 - 0.01, z0 + depth * 0.55], dir, flux / n, { angle: 0.95, penumbra: 0.9, color: tint(light), reach: 5 });
  }
  return tag(g, "mirrorBar", "LED mirror light bar", role, finish, { w: W, d: z0 + depth, h: 0.045 });
}

/** The family's entries for lighting.js FIXTURES: { make, kind, doc }. */
export const WALL = {
  hexSconce: { make: hexSconce, kind: "wall",
    doc: "hexSconce({ d=0.17, finish='black', inner='brass' }) faceted hexagonal sconce, open top and bottom, brass inside: a scallop up and down the wall; bedside, hall" },
  upDown: { make: upDown, kind: "wall",
    doc: "upDown({ d=0.07, h=0.16, beam=0.36, finish='black' }) cylinder washing the wall up and down (beam: the cones' half-angle); halls, stairs, beside a door, outside a bedroom" },
  globeSconce: { make: globeSconce, kind: "wall",
    doc: "globeSconce({ d=0.15, arm=0.2, finish='brass' }) opal globe in a cup on a short arm, round back plate: a soft glow; bedside, hall, bathroom" },
  swingArm: { make: swingArm, kind: "wall",
    doc: "swingArm({ arm=0.5, aim=0.45, finish='black' }) articulated reading light, conical shade aimed down/forward: beside a bed or an armchair, at about 1.1-1.3 m" },
  pictureLight: { make: pictureLight, kind: "wall",
    doc: "pictureLight({ w=0.5, out=0.17, finish='brass' }) slim bar on a curved arm, lights the wall below it: fix it 5-10 cm above a picture's top" },
  mirrorBar: { make: mirrorBar, kind: "wall",
    doc: "mirrorBar({ w=0.6, finish='aluminium', light='neutral' }) slim LED bar with an opal underside over a bathroom or WC mirror (w 0.4-1.2): its top edge at the mirror's" },
};
// every family also takes lm (flux), light: 'warm'|'neutral' and role ('accent' by default)
