// housekit decor (#102): the pieces the listing photos used to choose, drawn by the kit from its catalogue once the
// scene is built, so the photos show what the 3D shows (the photo service frames the same ids at full resolution).
//   - a decorative fixture (styles.js "lamps": a table, desk or floor lamp, a pendant) becomes a catalogue lamp at its
//     real size, standing or hanging where the fixture did, and keeps the fixture's lights, moved into the lamp's
//     shade (objects.js `light`); a pendant's cord is lengthened or shortened (`cord`) so the lamp hangs from the
//     ceiling to where the fixture's bottom was. A room's lamps of one family are one piece (a pair by a bed);
//   - the kit's parametric entrance bench becomes a catalogue bench, its back where the bench's was;
//   - "wall-art" (fx.model) frames a print of art.js: the house's rooms in the order a set photographs them, each print
//     once in the house while there are prints left, a print where its kind suits the room (the archviz spike's
//     artlib.py allocation and frames).
// The runtime calls catalogueDecor(houseGroup) after buildScene; the models load before the first picture (track).

import * as THREE from "three";
import { ART } from "./art.js";
import { track } from "./finishes.js";
import { catalogueModel, hash, pickObject, sceneKey, shuffled } from "./furnish.js";
import { look, styleName } from "./styles.js";

const ASSETS = new URL("/kit/assets/", import.meta.url);
const BROWSER = typeof window !== "undefined";

// ---------------------------------------------------------------- rooms

function roomsOf(root) {
  const plans = [];
  root.traverse((o) => { if (o.userData?.kind === "floorPlan") plans.push(o); });
  return plans;
}

function inside([x, z], poly) {
  let c = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, zi] = poly[i], [xj, zj] = poly[j];
    if (zi > z !== zj > z && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) c = !c;
  }
  return c;
}

function distance([x, z], poly) {
  let d = Infinity;
  for (let i = 0; i < poly.length; i++) {
    const [ax, az] = poly[i], [bx, bz] = poly[(i + 1) % poly.length];
    const dx = bx - ax, dz = bz - az, l = dx * dx + dz * dz;
    const t = l ? Math.max(0, Math.min(1, ((x - ax) * dx + (z - az) * dz) / l)) : 0;
    d = Math.min(d, Math.hypot(x - ax - t * dx, z - az - t * dz));
  }
  return d;
}

/** The room `o` stands in (a floor plan's room, within 0.4 m of it, on the storey under it), null outside. */
function roomOf(plans, o) {
  const p = o.getWorldPosition(new THREE.Vector3());
  let best = null;
  for (const plan of plans) {
    const q = plan.worldToLocal(p.clone()), y = plan.userData.y ?? 0;
    if (q.y < y - 0.3 || q.y > y + (plan.userData.height ?? 2.5) + 0.1) continue;
    for (const r of plan.userData.rooms ?? []) {
      const d = inside([q.x, q.z], r.polygon) ? 0 : distance([q.x, q.z], r.polygon);
      if (d < 0.4 && (!best || d < best.d)) best = { d, room: r };
    }
  }
  return best?.room ?? null;
}

// ---------------------------------------------------------------- lamps

/** The catalogue lamp role fixture family `family` is drawn as in `room` (styles.js "lamps"), null for its own shape. */
export function lampRole(family, room) {
  const role = look("lamps", { room, kind: family });
  return role && role !== "kit" ? role : null;
}

// a quantized attribute (meshopt) as plain floats, so a transform does not clamp it
function floats(attr) {
  const out = new Float32Array(attr.count * attr.itemSize);
  for (let i = 0; i < attr.count; i++) for (let k = 0; k < attr.itemSize; k++) out[i * attr.itemSize + k] = attr.getComponent(i, k);
  return new THREE.BufferAttribute(out, attr.itemSize);
}

/**
 * Bake the model's meshes into `wrap`'s frame, then give its cord (heights y0 to y1) the length `len`: what is above
 * moves with the canopy, what is below stays. The photo service makes the same edit (photo/blender/catalogue.py).
 */
export function setCord(wrap, [y0, y1], len) {
  wrap.updateMatrixWorld(true);
  const inv = wrap.matrixWorld.clone().invert();
  const meshes = [];
  wrap.traverse((m) => { if (m.isMesh) meshes.push(m); });
  const k = len / (y1 - y0), d = len - (y1 - y0);
  for (const m of meshes) {
    const g = m.geometry.clone();
    for (const name of ["position", "normal"]) if (g.attributes[name]) g.setAttribute(name, floats(g.attributes[name]));
    g.applyMatrix4(new THREE.Matrix4().multiplyMatrices(inv, m.matrixWorld));
    const p = g.attributes.position;
    for (let i = 0; i < p.count; i++) {
      const y = p.getY(i);
      p.setY(i, y >= y1 ? y + d : y > y0 ? y0 + (y - y0) * k : y);
    }
    g.computeBoundingBox();
    g.computeBoundingSphere();
    m.geometry = g;
    wrap.add(m);
    m.position.set(0, 0, 0);
    m.quaternion.identity();
    m.scale.set(1, 1, 1);
  }
  wrap.userData.cord = [y0, y1, Math.round(len * 1000) / 1000];
}

const LAMP_NAMES = { "table-lamp": "table lamp", "desk-lamp": "desk lamp", "floor-lamp": "floor lamp", pendant: "pendant lamp" };

/** The fixture's lights and their spot targets, in the fixture's frame, out of it; the rest of its parts removed. */
function strip(f) {
  f.updateMatrixWorld(true);
  const inv = f.matrixWorld.clone().invert(), lights = [];
  f.traverse((o) => { if (o.isLight) lights.push(o); });
  const at = (o) => o.getWorldPosition(new THREE.Vector3()).applyMatrix4(inv);
  const kept = lights.map((l) => ({ l, p: at(l), t: l.target ? at(l.target) : null }));
  const box = new THREE.Box3();
  f.traverse((o) => { if (o.isMesh) box.union(new THREE.Box3().setFromObject(o).applyMatrix4(inv)); });
  for (const c of [...f.children]) f.remove(c);
  return { kept, box };
}

function relight(f, kept, at, spread) {
  const c = kept.reduce((s, { p }) => s.add(p), new THREE.Vector3()).divideScalar(Math.max(kept.length, 1));
  for (const { l, p, t } of kept) {
    const q = at.clone().add(p.clone().sub(c).multiplyScalar(spread));
    l.position.copy(q);
    f.add(l);
    if (t) {
      l.target.position.copy(t.sub(p).add(q));
      f.add(l.target);
    }
  }
}

function lampInto(f, o, [wrap, size]) {
  const u = f.userData, hang = !!u.hang;
  const { kept, box } = strip(f);
  const light = new THREE.Vector3(...(o.light ?? [0, size.y * 0.7, 0]));
  let top = size.y;
  if (hang && o.cord) {
    // from the ceiling (the fixture's origin) to the fixture's bottom
    const want = box.isEmpty() ? u.height ?? size.y : -box.min.y;
    const len = Math.max(0.05, want - (size.y - (o.cord[1] - o.cord[0])));
    setCord(wrap, o.cord, len);
    top = size.y + len - (o.cord[1] - o.cord[0]);
  }
  if (hang) {
    wrap.position.y = -top;
    light.y -= top;
  }
  f.add(wrap);
  const kitWide = box.isEmpty() ? 0 : Math.max(box.max.x - box.min.x, box.max.z - box.min.z);
  relight(f, kept, light, kitWide > 0 ? Math.min(1, Math.max(size.x, size.z) / kitWide) : 1);
  u.name = LAMP_NAMES[o.role] ?? u.name;
  u.footprint = [Math.round(size.x * 1000) / 1000, Math.round(size.z * 1000) / 1000];
  u.height = Math.round(top * 1000) / 1000;
  u.model = o.id;
}

// ---------------------------------------------------------------- benches

function benchInto(b, o, [wrap, size]) {
  const [w, d] = b.userData.footprint;
  for (const c of [...b.children]) b.remove(c);
  const sx = Math.min(1.25, Math.max(1 / 1.25, w / size.x));
  wrap.scale.x = sx;
  wrap.position.z = (size.z - d) / 2; // its back where the kit bench's was
  b.add(wrap);
  Object.assign(b.userData, { footprint: [Math.round(size.x * sx * 1000) / 1000, Math.round(size.z * 1000) / 1000], height: size.y, model: o.id });
}

// ---------------------------------------------------------------- wall art

const ORDER = ["living", "dining", "bedroom", "office", "kitchen", "bath", "hall", "wc", "laundry"];
const ALL = new Set(ORDER);
// museum kinds by room use; the prints made in the spike suit any room
const SUITS = {
  japanese: ["living", "dining", "bedroom", "office", "hall"],
  botanical: ["bedroom", "dining", "kitchen", "bath", "hall", "wc", "living", "laundry"],
  abstract: ["living", "dining", "office", "bedroom", "hall"],
  drawing: ["bedroom", "living", "office", "hall"],
  landscape: ["living", "dining", "bedroom", "office", "hall"],
  interior: ["living", "bedroom", "hall", "office"],
  photo: ["bath", "hall", "office", "kitchen", "living", "wc", "laundry"],
  stilllife: ["dining", "kitchen", "living"],
};
// frame: colour, roughness, metalness, face width (m), depth (m)
export const FRAMES = {
  oak: ["#c3a98a", 0.55, 0, 0.022, 0.03],
  ash: ["#dfd0bb", 0.6, 0, 0.02, 0.03],
  black: ["#2f2f2f", 0.45, 0, 0.015, 0.025],
  white: ["#eeeeec", 0.4, 0, 0.02, 0.03],
  brass: ["#e7cfa0", 0.3, 1, 0.01, 0.022],
};

const suits = (e, use) => (e.style.startsWith("gen-") ? ALL : new Set(SUITS[e.style] ?? ORDER)).has(use);

/** The framed print's outer width and height (m) for an outer long side `long`. */
export function artSize(e, long) {
  const face = FRAMES[e.frame][3], a = e.w / e.h, m = e.mount * long, inner = long - 2 * face - 2 * m;
  const [pw, ph] = a < 1 ? [inner * a, inner] : [inner, inner / a];
  return [pw + 2 * m + 2 * face, ph + 2 * m + 2 * face];
}

/**
 * Prints for `pieces` (each { use }), in the order given: one the house has not hung yet that suits its room, else one
 * not hung yet, else the least hung; the house style's prints only, in the house's own order.
 */
export function allocateArt(pieces, style = styleName()) {
  const pool = shuffled(ART.filter((e) => e.styles.includes(style)), hash(`${sceneKey()}|art`));
  const used = new Map(pool.map((e) => [e.id, 0]));
  return pieces.map(({ use }) => {
    const e = pool.find((e) => !used.get(e.id) && suits(e, use)) ?? pool.find((e) => !used.get(e.id))
      ?? [...pool].sort((a, b) => used.get(a.id) - used.get(b.id))[0];
    if (e) used.set(e.id, used.get(e.id) + 1);
    return e ?? null;
  });
}

const _mats = new Map();
const mat = (key, make) => (_mats.has(key) ? _mats.get(key) : (_mats.set(key, make()), _mats.get(key)));

function slab(g, [x0, y0, z0], [x1, y1, z1], material, name) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(x1 - x0, y1 - y0, z1 - z0), material);
  m.position.set((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2);
  m.name = name;
  m.castShadow = m.receiveShadow = true;
  g.add(m);
  return m;
}

/** Print `e` framed, its outer long side `long`, bottom-centred on the origin, its back on z = -depth / 2, facing +z. */
export function framedPrint(e, long) {
  const g = new THREE.Group();
  const [fw, fh] = artSize(e, long);
  const [color, roughness, metalness, face, depth] = FRAMES[e.frame];
  const fm = mat(`frame:${e.frame}`, () => new THREE.MeshStandardMaterial({ name: `art frame ${e.frame}`, color, roughness, metalness }));
  const z0 = -depth / 2, z1 = depth / 2;
  slab(g, [-fw / 2, fh - face, z0], [fw / 2, fh, z1], fm, "art-frame");
  slab(g, [-fw / 2, 0, z0], [fw / 2, face, z1], fm, "art-frame");
  slab(g, [-fw / 2, face, z0], [-fw / 2 + face, fh - face, z1], fm, "art-frame");
  slab(g, [fw / 2 - face, face, z0], [fw / 2, fh - face, z1], fm, "art-frame");
  const m = e.mount * long;
  const paper = mat("mount", () => new THREE.MeshStandardMaterial({ name: "art mount", color: "#f2f0ea", roughness: 0.9 }));
  // a mount, else a backing board: nothing shows through
  if (m > 0) slab(g, [-fw / 2 + face, face, z1 - 0.012], [fw / 2 - face, fh - face, z1 - 0.008], paper, "art-mount");
  else slab(g, [-fw / 2 + face, face, z0], [fw / 2 - face, fh - face, z0 + 0.004], paper, "art-back");
  const pm = new THREE.MeshStandardMaterial({ name: `art print ${e.id}`, color: "#ffffff", roughness: 0.85 });
  if (BROWSER) {
    track(new THREE.TextureLoader().loadAsync(new URL(`art/${e.id}.webp`, ASSETS).href).then((t) => {
      t.colorSpace = THREE.SRGBColorSpace;
      t.anisotropy = 4;
      pm.map = t;
      pm.needsUpdate = true;
    }, () => console.warn(`housekit: print ${e.id} not loaded`)));
  }
  const pw = fw - 2 * face - 2 * m, ph = fh - 2 * face - 2 * m;
  const print = new THREE.Mesh(new THREE.PlaneGeometry(pw, ph), pm);
  print.position.set(0, fh / 2, z1 - (m > 0 ? 0.0075 : 0.006));
  print.name = "art-print";
  print.receiveShadow = true;
  g.add(print);
  return { g, size: [fw, fh], depth };
}

function artInto(p, e) {
  const [bw, bh] = p.userData.art.box;
  const [fw1, fh1] = artSize(e, 1);
  const long = Math.min(bw / fw1, bh / fh1, 1.2);
  const { g, size: [fw, fh], depth } = framedPrint(e, long);
  g.position.y = (bh - fh) / 2;
  p.add(g);
  Object.assign(p.userData, { footprint: [Math.round(fw * 1000) / 1000, depth], height: Math.round(((bh + fh) / 2) * 1000) / 1000,
    art: e.id, model: e.id });
}

// ---------------------------------------------------------------- the pass

/**
 * What the scene's decor draws from the catalogue, in scene order: { lamps: [[fixture, piece, room]], benches:
 * [[bench, piece, room]], art: [[placeholder, print, room]] } (room: its floor plan's room, null outside one).
 */
export function decorPlan(root) {
  root.updateMatrixWorld(true);
  const plans = roomsOf(root);
  const found = { lamps: [], benches: [], art: [] };
  root.traverse((o) => {
    const u = o.userData ?? {};
    if (u.fixture && !u.model) found.lamps.push(o);
    else if (u.kind === "furniture" && u.name === "bench" && !u.model) found.benches.push(o);
    else if (u.kind === "furniture" && u.art && typeof u.art === "object") found.art.push(o);
  });
  const out = { lamps: [], benches: [], art: [] };
  const matched = new Map(); // family -> room -> piece
  for (const f of found.lamps) {
    const room = roomOf(plans, f), family = f.userData.fixture.family;
    const role = lampRole(family, room?.name);
    if (!role) continue;
    if (!matched.has(family)) matched.set(family, new Map());
    let piece = matched.get(family).get(room);
    if (!piece) {
      const want = f.userData.hang ? { width: f.userData.footprint?.[0] } : { height: f.userData.height };
      piece = pickObject(role, { ...want, room: room?.name }).piece;
      if (room) matched.get(family).set(room, piece);
    }
    out.lamps.push([f, piece, room]);
  }
  for (const b of found.benches) {
    const room = roomOf(plans, b);
    out.benches.push([b, pickObject("bench", { width: b.userData.footprint?.[0], room: room?.name }).piece, room]);
  }
  const named = new Map(plans.flatMap((pl) => pl.userData.rooms ?? []).map((r) => [r.name, r]));
  const hung = found.art.map((p, i) => {
    const room = (p.userData.art.room && named.get(p.userData.art.room)) || roomOf(plans, p);
    return { p, i, room, use: room?.use ?? null, name: room?.name ?? p.userData.art.room ?? "" };
  });
  const rank = (u) => (ORDER.includes(u) ? ORDER.indexOf(u) : ORDER.length);
  hung.sort((a, b) => rank(a.use) - rank(b.use) || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0) || a.i - b.i);
  allocateArt(hung).forEach((e, k) => { if (e) out.art.push([hung[k].p, e, hung[k].room]); });
  return out;
}

/**
 * Draw the scene's decor from the catalogue (see the top of this file). Returns how many pieces it takes on: the
 * prints at once, the lamps and benches once their models load (in Node, the kit's tests, they keep their shapes).
 */
export function catalogueDecor(root) {
  const plan = decorPlan(root);
  for (const [p, e] of plan.art) artInto(p, e);
  if (BROWSER) {
    for (const [f, o] of plan.lamps) track(catalogueModel(o).then((m) => m && lampInto(f, o, m)));
    for (const [b, o] of plan.benches) track(catalogueModel(o).then((m) => m && benchInto(b, o, m)));
  }
  return plan.lamps.length + plan.benches.length + plan.art.length;
}

export default { catalogueDecor, decorPlan, lampRole, allocateArt, artSize, framedPrint, setCord, FRAMES };
