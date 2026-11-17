// housekit dressing (#103): what a stager adds to a furnished room, drawn by the kit once the scene is built, so the 3D
// tour shows what the listing photos show (the photo service used to add it from its camera: photo/blender/stage.py).
//   - a nightstand: its lamp to the back corner away from the bed, books (something small on them) or something
//     small alone at the front corner on the bed's side, now and then a carafe at the other front corner;
//   - a dining table: napkins on its plates, now and then a linen runner, on its centre line a candle, a carafe, a vase
//     or a bowl of fruit;
//   - a sofa the house style leaves bare: cushions at both ends against its back, now and then a throw over an arm;
//   - a sideboard: something tall at one end, a jar or a vase, books with something small on them at the other;
//   - the floor: a plant, a pouf, a basket or a floor vase in a free corner, more of them in a larger room;
//   - a bath: a towel over the tub's front rim, now and then a stool with folded towels, a towel on a bar when the room
//     has no towel rail.
// The pieces are the house style's catalogue (pickObject: cushions, candles, jars, bowls, vases, plants, poufs,
// baskets) and the kit's own stand-ins for the photo service's made props (books, a carafe, a napkin, a runner, a
// throw, towels, a stool, a towel bar), tagged `made` (and `arg`): the photo service draws its own in their place
// (photo/blender/catalogue.py). A surface, a sofa or a corner the scene dressed itself is left as it is.
// It is one group (kind "dressing"), the house group's last child: the export's node ids of the scene's own pieces stay
// what they were (the listing photos' choices name them). Nothing of the scene is hidden; a nightstand's lamp moves.

import * as THREE from "three";
import { roomOf, roomsOf } from "./decor.js";
import { track } from "./finishes.js";
import { catalogueModel, finish, hash, measureSofa, model, pickObject, sceneKey, shuffled } from "./furnish.js";
import { polygonArea, wallOpenings } from "./interior.js";
import { pieceType } from "./layout.js";
import { readWalls } from "./quantities.js";

const BROWSER = typeof window !== "undefined";
const DEG = Math.PI / 180;

// furnish.js hash (FNV-1a) mixed: its low bits alone follow the parity of the string's odd characters
function roll(s) {
  let h = hash(s);
  h = Math.imul(h ^ (h >>> 16), 0x85ebca6b);
  h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35);
  return (h ^ (h >>> 16)) >>> 0;
}

/** One of `list`, the same for the same scene and `salt`. */
const choose = (list, salt) => list[roll(`${sceneKey()}|dress|${salt}`) % list.length];

// ---------------------------------------------------------------- plane geometry

function segDist([x, z], [ax, az], [bx, bz]) {
  const dx = bx - ax, dz = bz - az, l = dx * dx + dz * dz;
  const t = l ? Math.max(0, Math.min(1, ((x - ax) * dx + (z - az) * dz) / l)) : 0;
  return { d: Math.hypot(x - ax - t * dx, z - az - t * dz), q: [ax + t * dx, az + t * dz] };
}

function inside([x, z], poly) {
  let c = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, zi] = poly[i], [xj, zj] = poly[j];
    if (zi > z !== zj > z && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) c = !c;
  }
  return c;
}

// a circle at p of radius r against a plan rectangle [x0, z0, x1, z1]
const hits = ([x, z], r, [x0, z0, x1, z1]) => Math.hypot(Math.max(x0 - x, 0, x - x1), Math.max(z0 - z, 0, z - z1)) < r;

/**
 * Free floor spots of a room for a piece `r` in radius and `h` tall, best first. A spot stands against a wall (the
 * room's edge r + 3 to r + 25 cm away, a wall of `walls` along it there: an open side of a kitchen-living is no wall),
 * clear of `boxes` (plan rectangles [x0, z0, x1, z1] with heights y0, y1 over the floor) by 3 cm, 0.9 m from the
 * `doors` (spans { a, b }), not before a window lower than the piece (`windows`: spans with their sill y0), 25 cm from
 * what is `taken` ([x, z, r]); off a corner, 0.6 m in front of it stays free. Corners first, then far from the doors.
 */
export function floorSpots({ polygon, walls = [], boxes = [], doors = [], windows = [], taken = [], r, h, step = 0.1 }) {
  const edges = polygon.map((p, i) => [p, polygon[(i + 1) % polygon.length]]);
  const xs = polygon.map((p) => p[0]), zs = polygon.map((p) => p[1]);
  const near = (rect, pad) => rect[2] > Math.min(...xs) - pad && rect[0] < Math.max(...xs) + pad && rect[3] > Math.min(...zs) - pad && rect[1] < Math.max(...zs) + pad;
  const here = boxes.filter((b) => b.y1 > 0.04 && near(b.rect, 0.1));
  const low = here.filter((b) => b.y0 < h), ahead = here.filter((b) => b.y0 < 1.0);
  walls = walls.filter((w) => near([Math.min(w.a[0], w.b[0]), Math.min(w.a[1], w.b[1]), Math.max(w.a[0], w.b[0]), Math.max(w.a[1], w.b[1])], w.t + 0.2));
  const out = [];
  for (let x = Math.min(...xs) + step / 2; x < Math.max(...xs); x += step) {
    for (let z = Math.min(...zs) + step / 2; z < Math.max(...zs); z += step) {
      const p = [x, z];
      if (!inside(p, polygon)) continue;
      let dmin = Infinity;
      const along = [];
      for (const [a, b] of edges) {
        const { d, q } = segDist(p, a, b);
        dmin = Math.min(dmin, d);
        if (d <= r + 0.25 && walls.some((w) => segDist(q, w.a, w.b).d <= w.t + 0.12)) along.push({ a, b, q });
      }
      if (dmin < r + 0.03 || !along.length) continue;
      const dirs = along.map(({ a, b }) => { const l = Math.hypot(b[0] - a[0], b[1] - a[1]); return [(b[0] - a[0]) / l, (b[1] - a[1]) / l]; });
      const corner = dirs.some((u) => dirs.some((v) => Math.abs(u[0] * v[1] - u[1] * v[0]) > 0.7));
      if (low.some((bx) => hits(p, r + 0.03, bx.rect))) continue;
      const fromDoor = Math.min(6, ...doors.map((dr) => segDist(p, dr.a, dr.b).d));
      if (fromDoor < 0.9 + r) continue;
      if (windows.some((w) => w.y0 < h + 0.05 && segDist(p, w.a, w.b).d < r + 0.3)) continue;
      if (taken.some(([tx, tz, tr]) => Math.hypot(x - tx, z - tz) < r + tr + 0.25)) continue;
      if (!corner) {
        const { q } = along[0], l = Math.hypot(x - q[0], z - q[1]) || 1, n = [(x - q[0]) / l, (z - q[1]) / l];
        if ([0.1, 0.3, 0.5, 0.6].some((k) => ahead.some((bx) => hits([x + n[0] * (r + k), z + n[1] * (r + k)], 0.001, bx.rect)))) continue;
      }
      out.push({ x, z, corner, score: (corner ? 10 : 0) + fromDoor });
    }
  }
  return out.sort((a, b) => b.score - a.score || a.x - b.x || a.z - b.z);
}

// ---------------------------------------------------------------- reading the scene

const STRUCTURE = new Set(["wall", "perimeter", "slab", "roof", "roomFloor", "ceiling", "partition", "interiorDoor", "door", "window",
  "skirting", "windowSills", "curtainRails", "cornice", "wallTiles", "featureWall", "terrain", "dressing", "floorPlan"]);

function kindOf(o) {
  for (let a = o; a; a = a.parent) if (a.userData?.kind) return a.userData.kind;
  return null;
}

/** A piece's frame (its place and turn about the vertical) and its box in that frame. */
function frameOf(o) {
  const p = o.getWorldPosition(new THREE.Vector3());
  const yaw = new THREE.Euler().setFromQuaternion(o.getWorldQuaternion(new THREE.Quaternion()), "YXZ").y;
  const c = Math.cos(yaw), s = Math.sin(yaw);
  const f = { x: p.x, y: p.y, z: p.z, yaw, c, s };
  f.world = (u, v) => [f.x + u * c + v * s, f.z - u * s + v * c];
  f.local = (x, z) => { const dx = x - f.x, dz = z - f.z; return [dx * c - dz * s, dx * s + dz * c]; };
  return f;
}

function localBox(o, f) {
  const box = new THREE.Box3(), part = new THREE.Box3();
  const inv = new THREE.Matrix4().makeRotationY(-f.yaw).multiply(new THREE.Matrix4().makeTranslation(-f.x, -f.y, -f.z));
  o.traverse((m) => {
    if (!m.isMesh || !m.geometry?.attributes?.position) return;
    if (!m.geometry.boundingBox) m.geometry.computeBoundingBox();
    box.union(part.copy(m.geometry.boundingBox).applyMatrix4(inv.clone().multiply(m.matrixWorld)));
  });
  return box;
}

function meshesOf(o) {
  const out = [];
  o.traverse((m) => { if (m.isMesh && m.geometry?.attributes?.position) out.push(m); });
  return out;
}

const _ray = new THREE.Raycaster();
const DOWN = new THREE.Vector3(0, -1, 0);
/** The height (world) of what a ray down at plan point [x, z] meets first among `meshes`, null for nothing. */
function heightAt(meshes, [x, z], from) {
  _ray.set(new THREE.Vector3(x, from, z), DOWN);
  return _ray.intersectObjects(meshes, false)[0]?.point.y ?? null;
}

const SOFT = /cushion|pillow|throw|plaid|blanket/i;

function readScene(root) {
  root.updateMatrixWorld(true);
  const plans = roomsOf(root);
  const storeys = plans.map((plan) => {
    const y = plan.localToWorld(new THREE.Vector3(0, plan.userData.y ?? 0, 0)).y;
    const at = ([x, z]) => { const v = plan.localToWorld(new THREE.Vector3(x, plan.userData.y ?? 0, z)); return [v.x, v.z]; };
    const rooms = (plan.userData.rooms ?? []).map((r) => ({ room: r, poly: r.polygon.map(at), y, plan }));
    const walls = (plan.userData.partitions ?? []).map((p) => ({ a: at(p.from), b: at(p.to), t: p.thickness ?? 0.1 }));
    return { plan, y, height: plan.userData.height ?? 2.5, rooms, walls, doors: [], windows: [] };
  });
  const storeyAt = (y) => storeys.find((s) => y > s.y - 0.3 && y < s.y + s.height - 0.2);
  const pieces = [], loose = [], dressed = new Set(), soft = [];
  root.traverse((o) => {
    const u = o.userData ?? {};
    if (u.kind === "sofaDressing" && u.sofa) dressed.add(u.sofa);
    if (u.kind === "furniture") {
      for (let a = o.parent; a; a = a.parent) if (a.userData?.kind === "furniture") return;
      const name = String(u.name ?? o.name ?? "");
      const box = new THREE.Box3().setFromObject(o);
      if (box.isEmpty()) return;
      const f = frameOf(o), lb = localBox(o, f);
      pieces.push({ o, u, name, type: pieceType(name), box, f, w: lb.max.x - lb.min.x, d: lb.max.z - lb.min.z, lb,
        storey: storeyAt(box.min.y + 0.05), room: roomOf(plans, o) });
      if (SOFT.test(name)) soft.push(o);
      return;
    }
    if (SOFT.test(String(u.name ?? o.name ?? ""))) soft.push(o);
    if (o.isMesh && !STRUCTURE.has(kindOf(o)) && kindOf(o) !== "furniture" && kindOf(o) !== "sofaDressing") loose.push(o);
  });
  for (const w of readWalls(root)) {
    for (const s of storeys) if (w.y < s.y + 0.5 && w.y + w.height > s.y + 1.0) s.walls.push({ a: w.from, b: w.to, t: w.thickness });
  }
  // what a builder modelled without a kind (an open stair's treads, a fireplace): obstacles on the floor
  const obstacles = pieces.filter((p) => !p.u.hang && !p.u.flat && p.type !== "rug" && p.type !== "lamp" || p.u.fixture && !p.u.hang)
    .map((p) => ({ box: p.box }));
  for (const m of loose) {
    const box = new THREE.Box3().setFromObject(m);
    if (box.isEmpty() || (box.max.x - box.min.x) * (box.max.z - box.min.z) > 4) continue;
    obstacles.push({ box });
  }
  for (const op of wallOpenings(root)) {
    // a partition's opening counts from its plan's own floor height (wallOpenings), a unit's from the world
    const s = op.inner ? storeys.find((st) => Math.abs(op.a.y - st.y) < 0.05) : storeyAt(op.a.y + 0.05);
    if (!s) continue;
    const base = op.inner ? s.plan.userData.y ?? 0 : s.y;
    const span = { a: [op.a.x, op.a.z], b: [op.b.x, op.b.z], y0: op.y0 - base, y1: op.y1 - base };
    (span.y0 < 0.3 && span.y1 - span.y0 > 1.6 ? s.doors : s.windows).push(span);
  }
  return { plans, storeys, pieces, obstacles, dressed, soft };
}

/** The plan rectangles of the obstacles on storey `s`, with their heights over its floor (what the pass adds, too). */
function boxesOn(ctx, s) {
  if (!ctx.boxes.has(s)) {
    ctx.boxes.set(s, ctx.scene.obstacles.filter((o) => o.box.min.y < s.y + s.height - 0.2 && o.box.max.y > s.y)
      .map((o) => ({ rect: [o.box.min.x, o.box.min.z, o.box.max.x, o.box.max.z], y0: o.box.min.y - s.y, y1: o.box.max.y - s.y })));
  }
  return ctx.boxes.get(s);
}

// ---------------------------------------------------------------- the kit's stand-ins for the photo's made props

const _mats = new Map();
const mat = (key, make) => (_mats.has(key) ? _mats.get(key) : (_mats.set(key, make()), _mats.get(key)));
const linear = (r, g, b) => new THREE.Color().setRGB(r, g, b, THREE.LinearSRGBColorSpace);
const plain = (name, color, roughness = 0.8, extra = {}) =>
  mat(name, () => new THREE.MeshStandardMaterial({ name, color: Array.isArray(color) ? linear(...color) : color, roughness, ...extra }));
const doubleSided = (m) => mat(`2s:${m.uuid}`, () => { const c = m.clone(); c.side = THREE.DoubleSide; return c; });
// the photo service's made props' colours (photo/blender/made/bathset.py), linear
const COVERS = [[0.30, 0.34, 0.27], [0.55, 0.47, 0.37], [0.74, 0.71, 0.64], [0.09, 0.09, 0.09], [0.45, 0.26, 0.19], [0.10, 0.13, 0.18]];
const TOWEL = [0.85, 0.84, 0.81], SAND = [0.62, 0.55, 0.46];

function mesh(g, geo, material, [x, y, z] = [0, 0, 0]) {
  const m = new THREE.Mesh(geo, material);
  m.position.set(x, y, z);
  m.castShadow = m.receiveShadow = true;
  g.add(m);
  return m;
}
const slab = (g, [w, h, d], [x, y, z], material) => mesh(g, new THREE.BoxGeometry(w, h, d), material, [x, y + h / 2, z]);
const lathe = (g, profile, material, at) => mesh(g, new THREE.LatheGeometry(profile.map(([r, y]) => new THREE.Vector2(r, y)), 32), material, at);

/** A cloth band `width` wide (along z) following `profile` ([x, y] points, across), both faces drawn. */
function band(g, profile, width, material) {
  const pos = [], idx = [], n = profile.length, rows = 8;
  for (let j = 0; j <= rows; j++) {
    const z = -width / 2 + (width * j) / rows;
    for (const [x, y] of profile) pos.push(x, y + 0.003 * Math.sin(x * 23 + z * 17), z);
  }
  for (let j = 0; j < rows; j++) for (let i = 0; i < n - 1; i++) {
    const a = j * n + i, b = a + n;
    idx.push(a, b, a + 1, b, b + 1, a + 1);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  geo.setIndex(idx);
  geo.computeVertexNormals();
  return mesh(g, geo, doubleSided(material));
}

// a sharp corner of a cloth's path rounded off
function rounded(points, k = 0.25) {
  const out = [points[0]];
  for (let i = 1; i < points.length - 1; i++) {
    const [p, q, r] = [points[i - 1], points[i], points[i + 1]];
    out.push([q[0] + (p[0] - q[0]) * k, q[1] + (p[1] - q[1]) * k], [q[0] + (r[0] - q[0]) * k, q[1] + (r[1] - q[1]) * k]);
  }
  out.push(points.at(-1));
  return out;
}

const MADE = {
  // three cloth-bound books, largest at the bottom; `arg` the covers ("i-j-k", COVERS)
  books(g, arg) {
    const covers = String(arg).split("-").map(Number);
    let y = 0;
    [[0.26, 0.19, 0.034], [0.235, 0.165, 0.026], [0.21, 0.145, 0.021]].forEach(([w, d, h], k) => {
      const turn = ((roll(`${arg}|${k}`) % 18) - 9) * DEG;
      const b = new THREE.Group();
      b.rotation.y = turn;
      b.position.y = y;
      slab(b, [w, h, d], [0, 0, 0], plain(`cover-${covers[k]}`, COVERS[covers[k]] ?? COVERS[0], 0.8));
      slab(b, [w - 0.005, h - 0.0056, d + 0.001], [0.0025, 0.0028, 0], plain("pages", [0.80, 0.77, 0.70], 0.85));
      g.add(b);
      y += h;
    });
    return { h: y, r: 0.15 };
  },
  carafe(g, _arg, tumbler = true) {
    const glass = finish.glass();
    lathe(g, [[0, 0], [0.040, 0], [0.044, 0.004], [0.046, 0.10], [0.041, 0.155], [0.027, 0.19], [0.024, 0.222], [0.0255, 0.228]], glass);
    lathe(g, [[0, 0.0045], [0.0395, 0.0045], [0.041, 0.12], [0, 0.12]], plain("water", [0.9, 0.95, 0.97], 0.05, { transparent: true, opacity: 0.35 }));
    if (tumbler) lathe(g, [[0, 0], [0.031, 0], [0.033, 0.004], [0.035, 0.088]], glass, [0.088, 0, -0.018]);
    return { h: 0.228, r: tumbler ? 0.13 : 0.05 };
  },
  carafe1: (g) => MADE.carafe(g, null, false),
  candles(g) {
    lathe(g, [[0, 0], [0.115, 0], [0.12, 0.004], [0.12, 0.013], [0.113, 0.014], [0, 0.006]], plain("stone", [0.70, 0.66, 0.60], 0.7));
    for (const [x, y, r, h] of [[-0.045, 0.02, 0.036, 0.16], [0.04, 0.035, 0.036, 0.11], [0.012, -0.055, 0.03, 0.075]]) {
      mesh(g, new THREE.CylinderGeometry(r, r, h, 24).translate(0, h / 2, 0), plain("wax", [0.90, 0.87, 0.80], 0.45), [x, 0.006, -y]);
    }
    return { h: 0.17, r: 0.12 };
  },
  napkin(g) {
    slab(g, [0.13, 0.007, 0.24], [0, 0, 0], plain("napkin", [0.78, 0.75, 0.69], 0.9));
    slab(g, [0.122, 0.007, 0.222], [0.003, 0.006, -0.008], plain("napkin", [0.78, 0.75, 0.69], 0.9));
    return { h: 0.013, r: 0.13 };
  },
  // a linen runner `arg` = "width-length" (cm) along z
  runner(g, arg) {
    const [w, l] = String(arg).split("-").map((v) => Number(v) / 100);
    slab(g, [w, 0.002, l], [0, 0, 0], finish.linen("#d5cfc4"));
    return { h: 0.002, r: Math.max(w, l) / 2 };
  },
  // a wool throw over a sofa's arm (the photo's sofathrow frame: the arm's inner face at x = 0, the arm on +x, the
  // floor at y = 0, z = 0 the seat's middle); `arg` "arm width-arm height-seat height" (cm), `side` -1 mirrors it
  sofathrow(g, arg, side = 1) {
    const [aw, ah, sh] = String(arg).split("-").map((v) => Number(v) / 100);
    const seat = Math.max(0.05, Math.min(0.4, 1.25 - 0.4 - aw - (ah - sh)));
    const path = rounded([[-seat, sh + 0.012], [-0.02, sh + 0.012], [0.008, ah - 0.03], [aw / 2, ah + 0.014], [aw + 0.014, ah - 0.04],
      [aw + 0.02, ah - 0.4]]);
    band(g, path.map(([x, y]) => [side * x, y]), 0.45, finish.wool("#c4bfb7"));
    return { h: ah, r: 0.4 };
  },
  // a bath towel folded in three over a tub's rim `arg` m wide (the rim's top centre at the origin, along x; the room
  // on +z), more of it outside
  drape(g, arg) {
    const rim = Number(arg) || 0.1;
    const path = rounded([[-rim / 2 - 0.2, -0.2], [-rim / 2 - 0.006, -0.03], [-rim / 2 + 0.01, 0.008], [rim / 2 - 0.01, 0.008],
      [rim / 2 + 0.008, -0.03], [rim / 2 + 0.014, -0.3]]);
    const m = band(g, path, 0.30, finish.towel("#ece8e0"));
    m.rotation.y = -Math.PI / 2; // the band runs across the rim (z), 0.30 along it (x)
    return { h: 0.01, r: 0.3 };
  },
  // a round oak stool 0.45 m high, three folded towels on it
  stooltowels(g) {
    const oak = finish.oak();
    mesh(g, new THREE.CylinderGeometry(0.17, 0.165, 0.032, 32).translate(0, 0.434, 0), oak);
    for (let k = 0; k < 3; k++) {
      const a = (90 + 120 * k) * DEG, foot = new THREE.Vector3(Math.cos(a) * 0.19, 0, -Math.sin(a) * 0.19);
      const head = new THREE.Vector3(Math.cos(a) * 0.105, 0.42, -Math.sin(a) * 0.105);
      const axis = head.clone().sub(foot);
      const leg = mesh(g, new THREE.CylinderGeometry(0.017, 0.013, axis.length(), 12), oak);
      leg.position.copy(foot.clone().add(head).multiplyScalar(0.5));
      leg.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), axis.normalize());
    }
    let y = 0.45;
    for (const [w, d, h, c] of [[0.285, 0.228, 0.05, TOWEL], [0.285, 0.228, 0.05, TOWEL], [0.257, 0.2, 0.042, SAND]]) {
      mesh(g, new THREE.BoxGeometry(w, h, d).translate(0, h / 2, 0), finish.towel(`#${linear(...c).getHexString()}`), [0, y, 0]);
      y += h - 0.004;
    }
    return { h: y, r: 0.2 };
  },
  // a black towel bar on two stand-offs, a towel folded in three over it (the bar's centre at the origin, along x; the
  // wall at z = -0.075, the room on +z)
  rail(g) {
    const black = plain("bar-black", [0.02, 0.02, 0.02], 0.45);
    mesh(g, new THREE.CylinderGeometry(0.011, 0.011, 0.6, 16).rotateZ(Math.PI / 2), black);
    for (const x of [-0.27, 0.27]) mesh(g, new THREE.CylinderGeometry(0.008, 0.008, 0.075, 12).rotateX(Math.PI / 2), black, [x, 0, -0.0375]);
    const path = rounded([[-0.022, -0.36], [-0.016, 0.0], [0, 0.016], [0.016, 0.0], [0.024, -0.44]], 0.4);
    const m = band(g, path, 0.45, finish.towel(`#${linear(...SAND).getHexString()}`));
    m.rotation.y = Math.PI / 2; // across the bar (z), 0.45 along it (x)
    return { h: 0.02, r: 0.3 };
  },
};

// ---------------------------------------------------------------- holders

/** A holder of the dressing at world point `at`, turned by `yaw`: its userData says what it is. */
function holder(ctx, what, room, at, yaw, extra = {}) {
  const g = new THREE.Group();
  g.position.copy(ctx.toGroup(at));
  g.rotation.y = yaw - ctx.yaw0;
  g.userData = { kind: "dressing", dressing: what, ...(room ? { room: room.name } : {}), ...extra };
  ctx.group.add(g);
  ctx.count++;
  return g;
}

/** A made prop (MADE) at `at`, turned by `yaw`: the photo service draws its own (`made`, `arg`) in its place. */
function made(ctx, what, room, at, yaw, arg = null, side = 1) {
  const g = holder(ctx, what, room, at, yaw, { made: what, ...(arg != null ? { arg: String(arg) } : {}), ...(side !== 1 ? { side } : {}) });
  const size = MADE[what](g, arg, side);
  return { g, ...size };
}

/** A role's piece of the catalogue, scaled by `k(size)` (its [width, depth, height]); null when the role has none. */
function catalogue(ctx, role, room, at, yaw, { want = {}, k = () => 1, tilt = 0, fit } = {}) {
  let o = null;
  for (let i = 0; i < 3 && !o; i++) {
    const p = pickObject(role, { ...want, room: room?.name }).piece;
    if (!fit || fit(p.size)) o = p;
  }
  if (!o) return null;
  const s = k(o.size);
  const g = holder(ctx, role, room, at, yaw, { model: o.id });
  if (tilt) g.rotation.set(tilt, g.rotation.y, 0, "YXZ");
  if (BROWSER) {
    track(catalogueModel(o).then((m) => {
      if (!m) return;
      m[0].scale.setScalar(s);
      g.add(m[0]);
    }));
  }
  return { g, w: o.size[0] * s, d: o.size[1] * s, h: o.size[2] * s, r: Math.max(o.size[0], o.size[1]) * s / 2 };
}

/** A role drawn by model() (a potted plant, a vase holding stems), as a holder's content: no piece of its own. */
function modelled(ctx, role, room, at, yaw, opts) {
  const g = holder(ctx, role, room, at, yaw);
  if (BROWSER) {
    track(model(role, { ...opts, room: room?.name }).then((p) => {
      // not a piece of furniture of the scene's (the layout audit, the walk's plan): what draws it stays tagged
      const { model: id, catalogue: cid } = p.userData;
      p.userData = { ...(id ? { model: id } : {}), ...(cid ? { catalogue: cid } : {}) };
      g.add(p);
    }));
  }
  return { g, h: opts.height ?? 0.3 };
}

// something small standing at `at` (world), at most `r` in radius: a little plant, a vase (with stems now and then), a
// candle or a jar, by the room (the photo service's small_on)
function small(ctx, room, at, r, salt) {
  const what = choose(["pot", "vase", "candle", "jar", "vase+stems"], `${room?.name}|small|${salt}`);
  const yaw = ((roll(`${salt}|turn`) % 60) - 30) * DEG;
  const fits = (h) => (s) => Math.max(s[0], s[1]) <= 2 * r * 1.6 && s[2] <= h * 1.6;
  const scaled = (h) => (s) => Math.min(1, (2 * r) / Math.max(s[0], s[1]), h / s[2]);
  if (what === "pot") return modelled(ctx, "pot-plant", room, at, yaw, { height: 0.26 });
  if (what === "vase+stems") return modelled(ctx, "vase", room, at, yaw, { height: 0.17, stems: true });
  if (what === "vase") return catalogue(ctx, "vase", room, at, yaw, { want: { height: 0.2 }, k: scaled(0.24), fit: fits(0.24) });
  return catalogue(ctx, what, room, at, yaw, { k: scaled(0.3), fit: fits(0.3) });
}

// ---------------------------------------------------------------- surfaces

/** The pieces standing on a top at height `top` (world) of piece `p`, each with its box in `p`'s frame. */
function onTop(ctx, p, top) {
  const out = [];
  for (const q of ctx.scene.pieces) {
    if (q === p || q.box.min.y < top - 0.06 || q.box.min.y > top + 0.08) continue;
    const c = q.box.getCenter(new THREE.Vector3()), [u, v] = p.f.local(c.x, c.z);
    if (u < p.lb.min.x - 0.02 || u > p.lb.max.x + 0.02 || v < p.lb.min.z - 0.02 || v > p.lb.max.z + 0.02) continue;
    const corners = [[q.box.min.x, q.box.min.z], [q.box.max.x, q.box.min.z], [q.box.max.x, q.box.max.z], [q.box.min.x, q.box.max.z]]
      .map(([x, z]) => p.f.local(x, z));
    const us = corners.map((k) => k[0]), vs = corners.map((k) => k[1]);
    out.push({ q, rect: [Math.min(...us), Math.min(...vs), Math.max(...us), Math.max(...vs)] });
  }
  return out;
}

// a top's height: the median of rays down at a few points of the piece's own meshes, within the piece's box
function topOf(p, pts) {
  const meshes = meshesOf(p.o), from = p.box.max.y + 0.2;
  const ys = pts.map(([u, v]) => heightAt(meshes, p.f.world(u, v), from)).filter((y) => y != null).sort((a, b) => a - b);
  return ys.length ? ys[Math.floor(ys.length / 2)] : null;
}

const free = (items, [u, v], r) => !items.some((i) => hits([u, v], r, i.rect));

// ---------------------------------------------------------------- the rules

function nightstands(ctx) {
  let n = 0;
  for (const p of ctx.scene.pieces) {
    if (p.type !== "nightstand" || !p.room) continue;
    const { w, d } = p, cu = (p.lb.min.x + p.lb.max.x) / 2, cv = (p.lb.min.z + p.lb.max.z) / 2;
    const top = topOf(p, [[cu, cv], [cu - w / 4, cv], [cu + w / 4, cv]]);
    if (top == null || top - p.box.min.y < 0.3) continue;
    const items = onTop(ctx, p, top), lamps = items.filter((i) => i.q.u.fixture);
    if (items.length > lamps.length) continue; // the scene dressed it
    const beds = ctx.scene.pieces.filter((b) => b.type === "bed" && b.room === p.room);
    const bed = beds.sort((a, b) => Math.hypot(a.f.x - p.f.x, a.f.z - p.f.z) - Math.hypot(b.f.x - p.f.x, b.f.z - p.f.z))[0];
    const s = bed ? Math.sign(p.f.local(bed.f.x, bed.f.z)[0]) || 1 : 1;
    const taken = [];
    // its base 8 cm from the edges (the photo's rule): what the shade overhangs stays usable below it
    for (const l of lamps) {
      const lr = Math.min(0.08, w / 2, d / 2);
      const [u, v] = [cu - s * (w / 2 - lr), cv - (d / 2 - lr)];
      const [x, z] = p.f.world(u, v), lo = l.q.o;
      const at = lo.getWorldPosition(new THREE.Vector3());
      at.x = x;
      at.z = z;
      lo.position.copy(lo.parent.worldToLocal(at));
      lo.updateMatrixWorld(true);
      taken.push({ rect: [u - lr, v - lr, u + lr, v + lr] });
    }
    const slot = `${p.room.name}|${n}`;
    const scale = Math.min(1, (w * 0.62) / 0.26, (d * 0.55) / 0.19);
    const bu = cu + s * (w / 2 - 0.13 * scale - 0.015), bv = cv + (d / 2 - 0.095 * scale - 0.015);
    const how = choose(["books+small", "books", "small"], `${slot}|bedside`);
    if (free(taken, [bu, bv], 0.1 * scale)) {
      const at = (u, v, y) => { const [x, z] = p.f.world(u, v); return new THREE.Vector3(x, y, z); };
      if (how.startsWith("books")) {
        const b = made(ctx, "books", p.room, at(bu, bv, top + 0.001), p.f.yaw - 8 * s * DEG, covers(slot));
        b.g.scale.setScalar(scale);
        if (how === "books+small") small(ctx, p.room, at(bu + 0.02 * s, bv, top + 0.001 + b.h * scale), 0.09, `${slot}|bed`);
      } else small(ctx, p.room, at(bu, bv, top + 0.001), 0.09, `${slot}|bed`);
      const cx = cu - s * (w / 2 - 0.06), cz = cv + (d / 2 - 0.06);
      if (w >= 0.445 && d >= 0.355 && choose([true, false], `${slot}|carafe`) && free(taken, [cx - s * 0.04, cz], 0.07)) {
        made(ctx, "carafe", p.room, at(cx, cz, top + 0.001), p.f.yaw + (s > 0 ? 0 : Math.PI) + 12 * DEG);
      }
    }
    n++;
  }
  return n;
}

// three cover colours of the books, the same for the same place in the scene
function covers(salt) {
  return shuffled([0, 1, 2, 3, 4, 5], hash(`${sceneKey()}|books|${salt}`)).slice(0, 3).join("-");
}

const FRUIT = [["lemon", "#e8c547"], ["food_apple_01", "#a8332b"], ["food_pears_asian_01", "#c9b46a"]];

// a bowl of fruit standing at `at`: the bowl the catalogue's, the fruit stand-ins tagged with the photo's model
function fruitBowl(ctx, room, at, salt) {
  const b = catalogue(ctx, "bowl", room, at, (roll(`${salt}|bowl`) % 90) * DEG, { k: (s) => Math.min(1, 0.36 / Math.max(s[0], s[1])) });
  if (!b) return null;
  const pairs = FRUIT.flatMap((p) => FRUIT.map((q) => [p, q]));
  const [a, c] = choose(pairs, `${salt}|fruit`), r = Math.min(b.w, b.d) * 0.5;
  [[-0.05, 0, a], [0.05, 0.03, c], [0, -0.05, c], [0.02, 0.06, a], [-0.01, 0.01, c]].forEach(([dx, dz, [name, color]], k) => {
    const f = new THREE.Group();
    f.position.set((dx * r) / 0.12, b.h * 0.55 + (k >= 3 ? 0.03 : 0), (dz * r) / 0.12);
    f.rotation.y = -b.g.rotation.y + 70 * k * DEG;
    f.userData = { fruit: name, height: 0.07 };
    mesh(f, new THREE.SphereGeometry(0.035, 16, 12).scale(1, name === "lemon" ? 0.82 : 1, 1), plain(`fruit-${name}`, color, 0.5), [0, 0.035, 0]);
    b.g.add(f);
  });
  return b;
}

const DINING = /dining.?(table|set)|table à manger/i;

function diningTables(ctx) {
  let n = 0;
  for (const p of ctx.scene.pieces) {
    if (!(p.type === "dining" || DINING.test(p.name)) || !p.room) continue;
    const meshes = meshesOf(p.o), from = p.box.max.y + 0.2;
    const cu = (p.lb.min.x + p.lb.max.x) / 2, cv = (p.lb.min.z + p.lb.max.z) / 2;
    const at0 = heightAt(meshes, p.f.world(cu, cv), from);
    if (at0 == null || at0 - p.box.min.y < 0.6 || at0 - p.box.min.y > 0.85) continue;
    // the top's extent, out from its centre along each axis to its edge (a set's chairs are lower)
    const reach = (du, dv) => {
      let k = 0;
      for (let t = 0.05; t < 2; t += 0.05) {
        const y = heightAt(meshes, p.f.world(cu + du * t, cv + dv * t), from);
        if (y == null || Math.abs(y - at0) > 0.02) break;
        k = t;
      }
      return k;
    };
    const hu = Math.min(reach(1, 0), reach(-1, 0)), hv = Math.min(reach(0, 1), reach(0, -1));
    if (hu < 0.3 || hv < 0.3) continue;
    const top = at0, alongZ = hv > hu, L = 2 * Math.max(hu, hv);
    const items = onTop(ctx, p, top), slot = `${p.room.name}|table${n}`;
    const at = (u, v, y) => { const [x, z] = p.f.world(u, v); return new THREE.Vector3(x, y, z); };
    // napkins on every plate (the photo skipped them on a third of its tables; plates alone read as a table half laid)
    const plates = items.filter((i) => /^plate$/i.test(i.q.name) || i.q.type === "decor" && /plate/i.test(i.q.name));
    plates.forEach((i, k) => {
      const c = i.q.box.getCenter(new THREE.Vector3());
      made(ctx, "napkin", p.room, new THREE.Vector3(c.x, i.q.box.max.y + 0.0005, c.z), p.f.yaw + (alongZ ? Math.PI / 2 : 0) + (((k * 37) % 11) - 5) * DEG);
    });
    let lift = 0;
    if (choose([true, false], `${slot}|runner`)) {
      made(ctx, "runner", p.room, at(cu, cv, top + 0.001), p.f.yaw + (alongZ ? 0 : Math.PI / 2), `30-${Math.round((L - 0.3) * 100)}`);
      lift = 0.002;
    }
    // on the centre line: what is there already counts
    const centre = items.filter((i) => !plates.includes(i) && !/glass|cutlery|fork|knife|spoon|napkin/i.test(i.q.name));
    const want = centre.length === 0 ? 3 : centre.length === 1 ? 1 : 0;
    const axis = alongZ ? [0, 1] : [1, 0];
    const spots = [];
    for (let k = -8; k <= 8; k++) {
      const t = (k * L) / 18, uv = [cu + axis[0] * t, cv + axis[1] * t];
      if (Math.abs(t) <= L / 2 - 0.13 && free(items, uv, 0.125)) spots.push({ t, uv });
    }
    const order = choose([["candle", "carafe", "fruit"], ["candle", "vase", "fruit"], ["vase", "candle", "bowl"], ["carafe", "candle", "vase"]], `${slot}|order`);
    const placed = [];
    [[order[0], 0.125, 0.25, 0.27], [order[1], 0.08, 0.25, 0.27], [order[2], 0.125, 0.05, 0]].slice(0, want).forEach(([what, r, gap, frac], i) => {
      const best = spots.filter((s) => !placed.some((q) => Math.abs(s.t - q.t) < r + q.r + gap))
        .sort((a, b) => Math.abs(Math.abs(a.t) - L * frac) - Math.abs(Math.abs(b.t) - L * frac))[0];
      if (!best) return;
      const p3 = at(best.uv[0], best.uv[1], top + lift + 0.001), salt = `${slot}|${i}`;
      let done = null;
      if (what === "fruit") done = fruitBowl(ctx, p.room, p3, salt);
      else if (what === "carafe") done = made(ctx, "carafe1", p.room, p3, p.f.yaw + 0.4);
      else if (what === "candle") done = catalogue(ctx, "candle", p.room, p3, 20 * DEG, { k: (s) => Math.min(1, 0.3 / Math.max(s[0], s[1]), 0.35 / s[2]) });
      else if (what === "bowl") done = catalogue(ctx, "bowl", p.room, p3, (roll(salt) % 90) * DEG, { k: (s) => Math.min(1, 0.36 / Math.max(s[0], s[1])) });
      else if (choose([true, false], `${salt}|stems`)) done = modelled(ctx, "vase", p.room, p3, 0, { height: 0.24, stems: true });
      else done = catalogue(ctx, "vase", p.room, p3, 0, { want: { height: 0.26 }, k: (s) => Math.min(1, 0.3 / s[2], 0.2 / Math.max(s[0], s[1])) });
      if (done) placed.push({ t: best.t, r });
    });
    n++;
  }
  return n;
}

// the photo service's cushion plans: per end (near, far), [width, inset from the arm, forward from the back, twist°]
const CUSHIONS = [
  [[[0.46, 0.24, 0.0, 6], [0.40, 0.46, 0.07, 14]], [[0.44, 0.28, 0.02, 4]]],
  [[[0.44, 0.24, 0.0, 8]], [[0.46, 0.24, 0.0, 5], [0.38, 0.46, 0.08, 12]]],
  [[[0.46, 0.24, 0.0, 6], [0.40, 0.46, 0.07, 14]], [[0.46, 0.24, 0.0, 5], [0.40, 0.46, 0.07, 14]]],
];
const PILLOW_TILT = 16 * DEG;

function sofas(ctx) {
  let n = 0;
  for (const p of ctx.scene.pieces) {
    if (p.type !== "sofa" || !p.room || p.u.dressed || p.w < 1.2 || ctx.scene.dressed.has(p.o.uuid)) continue;
    const near = p.box.clone().expandByScalar(0.05);
    if (ctx.scene.soft.some((o) => o !== p.o && near.containsPoint(o.getWorldPosition(new THREE.Vector3())))) continue;
    const m = measureSofa(p.o);
    if (!m) continue;
    const slot = `${p.room.name}|sofa${n}`, far = choose([1, -1], `${slot}|far`);
    const world = (x, y, z) => new THREE.Vector3(x, y, z).applyMatrix4(m.toWorld);
    const plan = choose(CUSHIONS, `${slot}|cushions`);
    for (const [side, row] of [[-far, plan[0]], [far, plan[1]]]) {
      const edge = side > 0 ? m.right : m.left;
      for (const [width, inset, fwd, twist] of row) {
        const x = edge - side * inset, z = m.backZ + 0.09 + fwd;
        if (m.taken(x, m.backZ + 0.12, 0.12)) continue;
        catalogue(ctx, "cushion", p.room, world(x, m.seatAt(x, z) + 0.003, z), p.f.yaw - side * twist * DEG,
          { want: { width }, k: (s) => width / s[0], tilt: -PILLOW_TILT });
      }
    }
    const arm = m.arms[far], sh = m.seatY - m.floorY;
    if (arm && choose([true, true, false], `${slot}|throw`)) {
      const aw = arm.width, ah = arm.top - m.floorY;
      if (aw >= 0.08 && aw <= 0.45 && ah >= sh + 0.08 && ah <= 0.85) {
        const arg = [aw, ah, sh].map((v) => Math.round(v * 100)).join("-");
        made(ctx, "sofathrow", p.room, world(arm.inner, m.floorY, m.mid), p.f.yaw, arg, far);
      }
    }
    n++;
  }
  return n;
}

const SIDEBOARD = /sideboard|buffet|credenza|commode|dresser|console|media|tv/i;

function sideboards(ctx) {
  let n = 0;
  for (const p of ctx.scene.pieces) {
    if (!p.room || !SIDEBOARD.test(p.name) || p.w < 0.9 || p.d > 0.65) continue;
    const hgt = p.box.max.y - p.box.min.y;
    if (hgt < 0.45 || hgt > 1.1) continue;
    const cu = (p.lb.min.x + p.lb.max.x) / 2, cv = (p.lb.min.z + p.lb.max.z) / 2, L = p.w;
    const top = topOf(p, [[cu, cv], [cu - L / 4, cv], [cu + L / 4, cv]]);
    if (top == null) continue;
    const items = onTop(ctx, p, top);
    if (items.filter((i) => !i.q.u.fixture).length >= 2) continue;
    const slot = `${p.room.name}|board${n}`, e = choose([1, -1], `${slot}|end`);
    const at = (u, v, y = top + 0.001) => { const [x, z] = p.f.world(u, v); return new THREE.Vector3(x, y, z); };
    const taken = [...items];
    const room = (u, v, r) => free(taken, [u, v], r) && Math.abs(u - cu) <= L / 2 - r * 0.6;
    const tall = [cu + e * (L / 2 - 0.3), cv - 0.04], books = [cu - e * (L / 2 - 0.36), cv + 0.06], mid = [cu - e * (L / 2 - 0.66), cv - 0.05];
    if (room(...tall, 0.14)) {
      if (choose(["plant", "branches", "branches"], `${slot}|tall`) === "plant") modelled(ctx, "pot-plant", p.room, at(...tall), 20 * DEG, { height: 0.55 });
      else modelled(ctx, "vase", p.room, at(...tall), 0, { height: 0.3, stems: true });
      taken.push({ rect: [tall[0] - 0.14, tall[1] - 0.14, tall[0] + 0.14, tall[1] + 0.14] });
    }
    if (room(...books, 0.15)) {
      const b = made(ctx, "books", p.room, at(...books), p.f.yaw - 12 * e * DEG, covers(slot));
      small(ctx, p.room, at(books[0], books[1], top + 0.001 + b.h), 0.08, `${slot}|board`);
      taken.push({ rect: [books[0] - 0.15, books[1] - 0.12, books[0] + 0.15, books[1] + 0.12] });
    }
    if (L >= 1.3 && room(...mid, 0.12)) {
      const role = choose(["jar", "vase"], `${slot}|mid`);
      catalogue(ctx, role, p.room, at(...mid), -35 * DEG, { k: (s) => Math.min(1, 0.24 / Math.max(s[0], s[1]), 0.32 / s[2]) });
    }
    n++;
  }
  return n;
}

const NO_FLOOR = new Set(["stair", "storage", "cellar", "garage", "attic", "technical"]);
const FLOOR_PLANS = {
  wet: [["basket"], ["plant"], ["basket"]],
  kitchen: [["plant"], ["vase"], []],
  other: [["plant", "pouf"], ["basket", "plant"], ["plant", "pouf", "vase"], ["vase", "basket"], ["plant", "basket"], ["pouf", "plant"]],
};
const FLOOR_ACCENT = /plant|ficus|palm|pouf|ottoman|basket|vase/i;

function floors(ctx) {
  let n = 0;
  for (const s of ctx.scene.storeys) {
    const boxes = boxesOn(ctx, s);
    for (const { room, poly } of s.rooms) {
      if (NO_FLOOR.has(room.use)) continue;
      const area = polygonArea(poly);
      const mine = ctx.scene.pieces.filter((p) => p.room === room && FLOOR_ACCENT.test(p.name) && p.box.min.y < s.y + 0.1);
      const budget = (area < 11 ? 1 : area < 22 ? 2 : 3) - mine.length;
      if (budget <= 0) continue;
      const plans = ["bath", "wc", "laundry"].includes(room.use) ? FLOOR_PLANS.wet : room.use === "kitchen" ? FLOOR_PLANS.kitchen : FLOOR_PLANS.other;
      let plant = mine.some((p) => p.type === "plant"), placed = 0;
      for (let what of choose(plans, `${room.name}|floor`)) {
        if (placed >= budget) break;
        if (what === "plant" && plant) what = "vase";
        let pick = null, r = 0.3, h = 1.3;
        if (what === "plant") h = choose([1.5, 1.05, 1.15], `${room.name}|floor-plant`);
        else if (what === "vase") [r, h] = [0.16, 1.3];
        else {
          for (let i = 0; i < 3 && !pick; i++) {
            const o = pickObject(what, { room: room.name }).piece;
            if (o.size[0] <= 0.7 && o.size[1] <= 0.7 && o.size[2] <= 0.55) pick = o;
          }
          if (!pick) continue;
          [r, h] = [Math.max(pick.size[0], pick.size[1]) / 2, pick.size[2]];
        }
        const spots = floorSpots({ polygon: poly, walls: s.walls, boxes, doors: s.doors, windows: s.windows, taken: ctx.taken, r, h });
        const spot = what === "plant" ? spots.find((q) => q.corner) ?? spots[0] : spots[0];
        if (!spot) continue;
        const at = new THREE.Vector3(spot.x, s.y, spot.z), turn = (roll(`${room.name}|${what}|${placed}`) % 360) * DEG;
        if (what === "plant") modelled(ctx, "floor-plant", room, at, turn, { height: h });
        else if (what === "vase") modelled(ctx, "vase", room, at, turn, { height: choose([0.46, 0.55], `${room.name}|floor-vase`), stems: true });
        else {
          const g = holder(ctx, what, room, at, turn, { model: pick.id });
          if (BROWSER) track(catalogueModel(pick).then((m) => m && g.add(m[0])));
        }
        ctx.taken.push([spot.x, spot.z, r]);
        boxes.push({ rect: [spot.x - r, spot.z - r, spot.x + r, spot.z + r], y0: 0, y1: h });
        if (what === "plant") plant = true;
        placed++;
        n++;
      }
    }
  }
  return n;
}

const RAIL_H = 1.2; // a towel bar's height over the floor (style.py RAIL_H)

function baths(ctx) {
  let n = 0;
  for (const s of ctx.scene.storeys) {
    const boxes = boxesOn(ctx, s);
    for (const { room, poly } of s.rooms) {
      if (room.use !== "bath") continue;
      const here = ctx.scene.pieces.filter((p) => p.room === room);
      for (const tub of here.filter((p) => p.type === "bathtub")) n += drapeOn(ctx, tub, room) ? 1 : 0;
      if (choose([true, false], `${room.name}|stool`)) {
        const spot = floorSpots({ polygon: poly, walls: s.walls, boxes, doors: s.doors, windows: s.windows, taken: ctx.taken, r: 0.2, h: 0.6 })[0];
        if (spot) {
          made(ctx, "stooltowels", room, new THREE.Vector3(spot.x, s.y, spot.z), 17 * DEG);
          ctx.taken.push([spot.x, spot.z, 0.2]);
          boxes.push({ rect: [spot.x - 0.2, spot.z - 0.2, spot.x + 0.2, spot.z + 0.2], y0: 0, y1: 0.6 });
          n++;
        }
      }
      if (!here.some((p) => p.type === "towels") && towelBar(ctx, s, room, poly, boxes)) n++;
    }
  }
  return n;
}

// a towel folded over the tub's front rim: the rim from rays down the tub's depth, from its front to where it drops
function drapeOn(ctx, tub, room) {
  const meshes = meshesOf(tub.o), from = tub.box.max.y + 0.2, { lb } = tub;
  const d = lb.max.z - lb.min.z, u = (lb.min.x + lb.max.x) / 2 - (lb.max.x - lb.min.x) * 0.22;
  const vs = [];
  for (let v = lb.max.z - 0.005; v > lb.min.z; v -= 0.01) vs.push(v);
  const hs = vs.map((v) => heightAt(meshes, tub.f.world(u, v), from));
  const front = hs.slice(0, 15).filter((h) => h != null);
  if (!front.length) return false;
  const topF = Math.max(...front), i0 = hs.findIndex((h) => h != null && h >= topF - 0.005);
  const k = hs.findIndex((h, i) => i > i0 && h != null && h < topF - 0.03);
  if (k < 0) return false;
  const rim = lb.max.z - vs[k];
  if (rim < 0.03 || rim > 0.2 || d < 0.5) return false;
  const [x, z] = tub.f.world(u, lb.max.z - rim / 2);
  made(ctx, "drape", room, new THREE.Vector3(x, topF, z), tub.f.yaw, Math.round(rim * 1000) / 1000);
  return true;
}

// a towel on a bar on a free wall of the bath: no door or window behind it, nothing standing in front of it above
// 0.65 m (a tub is fine), as far from the door as the room allows
function towelBar(ctx, s, room, poly, boxes) {
  let best = null;
  const tall = boxes.filter((b) => b.y1 > RAIL_H - 0.55 && b.y0 < RAIL_H + 0.15);
  const openings = [...s.doors, ...s.windows].filter((o) => o.y0 < RAIL_H + 0.1 && o.y1 > RAIL_H - 0.5);
  poly.forEach((a, i) => {
    const b = poly[(i + 1) % poly.length], len = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (len < 0.7) return;
    const t = [(b[0] - a[0]) / len, (b[1] - a[1]) / len];
    let nrm = [-t[1], t[0]];
    const m = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    if (!inside([m[0] + nrm[0] * 0.1, m[1] + nrm[1] * 0.1], poly)) nrm = [t[1], -t[0]];
    for (let k = 0.35; k <= len - 0.35 + 1e-6; k += 0.1) {
      const c = [a[0] + t[0] * k, a[1] + t[1] * k];
      if (!s.walls.some((w) => segDist(c, w.a, w.b).d <= w.t + 0.12)) continue;
      if (openings.some((o) => segDist(c, o.a, o.b).d < 0.45)) continue;
      const probe = [-0.3, 0, 0.3].flatMap((e) => [0.08, 0.25].map((q) => [c[0] + t[0] * e + nrm[0] * q, c[1] + t[1] * e + nrm[1] * q]));
      if (probe.some((q) => tall.some((bx) => hits(q, 0.04, bx.rect)))) continue;
      const score = Math.min(6, ...s.doors.map((dr) => segDist(c, dr.a, dr.b).d));
      if (!best || score > best.score) best = { score, c, nrm };
    }
  });
  if (!best) return false;
  const { c, nrm } = best;
  made(ctx, "rail", room, new THREE.Vector3(c[0] + nrm[0] * 0.075, s.y + RAIL_H, c[1] + nrm[1] * 0.075), Math.atan2(nrm[0], nrm[1]));
  boxes.push({ rect: [c[0] - 0.3, c[1] - 0.3, c[0] + 0.3, c[1] + 0.3], y0: RAIL_H - 0.45, y1: RAIL_H + 0.05 });
  return true;
}

// ---------------------------------------------------------------- the pass

/**
 * Dress the rooms of the scene under `root` (see the top of this file): one group added as `root`'s last child, its
 * catalogue pieces loading before the first picture (track; in Node, the kit's tests, the holders stay empty).
 * Returns { pieces, nightstands, tables, sofas, sideboards, floors, baths }.
 */
export function dressRooms(root) {
  const scene = readScene(root);
  const group = new THREE.Group();
  group.name = "dressing";
  group.userData = { kind: "dressing", name: "the kit's dressing" };
  root.updateMatrixWorld(true);
  const inv = root.matrixWorld.clone().invert();
  const ctx = { scene, group, count: 0, taken: [], boxes: new Map(), yaw0: frameOf(root).yaw, toGroup: (v) => v.clone().applyMatrix4(inv) };
  const done = {
    nightstands: nightstands(ctx), tables: diningTables(ctx), sofas: sofas(ctx), sideboards: sideboards(ctx), baths: baths(ctx),
    floors: floors(ctx),
  };
  root.add(group);
  root.userData.kitDressing = 1; // the photo service leaves these rules to the kit (photo/blender/catalogue.py)
  return { pieces: ctx.count, ...done };
}

export default { dressRooms, floorSpots, MADE };
