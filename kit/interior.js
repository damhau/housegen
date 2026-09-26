// housekit/interior — the inside of a floor: rooms, partitions and interior doors.
//
// Same conventions as house.js: metres, +x east, +z south, +y up, points are [x, z].
// A floor is described the way a floor plan is drawn:
//   * rooms: the clear floor area of each room (inside faces of its walls), with a name and
//     a use ("living", "kitchen", "bedroom", "bath", "wc", "hall", "stair", "storage"...).
//     Rooms that are open to each other (a kitchen-living) are separate rooms with no
//     partition between them.
//   * partitions: interior walls by their CENTRE LINE from → to, with their thickness
//     (10 cm light partitions, 20–25 cm masonry). Door openings are measured along the
//     partition from `from` to the near edge of the opening, like house.wall().
//   * exterior walls, windows and the entrance doors stay in the exterior scene.

import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import { finishMaterial, metricUV, tileMaterial } from "./finishes.js";

// plain fallbacks (no import of house.js: a page may load it under another URL, ?v=<tag>, and a
// second instance of it would carry its own caches)
const _plain = new Map();
const plain = (color, roughness = 0.9) => {
  const k = `${color}:${roughness}`;
  if (!_plain.has(k)) _plain.set(k, new THREE.MeshStandardMaterial({ color, roughness }));
  return _plain.get(k);
};

// textured when the finishes are loaded (finishes.loadFinishes), plain colours otherwise
const FLOORS = {
  oak: () => finishMaterial("oak-floor") ?? plain("#c9a57c", 0.7),
  "oak-light": () => finishMaterial("oak-floor", { color: "#fff6ea" }) ?? plain("#dcc3a0", 0.7),
  tile: () => finishMaterial("stone-tile") ?? plain("#d8d5ce", 0.5),
  "tile-dark": () => finishMaterial("stone-tile", { color: "#8d8b86" }) ?? plain("#8d8b86", 0.5),
  concrete: () => plain("#b9b6ae", 0.95),
};
const PLASTER = () => finishMaterial("plaster") ?? plain("#f1efea", 0.92);

function shadow(mesh) {
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

function shape(points) {
  const s = new THREE.Shape();
  points.forEach(([x, z], i) => (i === 0 ? s.moveTo(x, z) : s.lineTo(x, z)));
  s.closePath();
  return s;
}

/** Polygon area, any winding. */
export function polygonArea(points) {
  let a = 0;
  for (let i = 0; i < points.length; i++) {
    const [x1, z1] = points[i], [x2, z2] = points[(i + 1) % points.length];
    a += x1 * z2 - x2 * z1;
  }
  return Math.abs(a) / 2;
}

function horizontal(points, y, thickness, material) {
  const geo = new THREE.ExtrudeGeometry(shape(points), { depth: thickness, bevelEnabled: false });
  const m = shadow(new THREE.Mesh(geo, material));
  m.rotation.x = Math.PI / 2; // polygon in XZ, extruded downwards from y
  m.position.y = y;
  return m;
}

/**
 * Interior wall on its centre line. openings: [{ offset, width, height = 2.05, sill = 0 }].
 * Returns a Group tagged kind "partition" with the wall's frame in userData.
 */
export function partition({ from, to, height = 2.5, thickness = 0.1, y = 0, openings = [], material = PLASTER() }) {
  const dx = to[0] - from[0], dz = to[1] - from[1];
  const length = Math.hypot(dx, dz);
  const angle = Math.atan2(dz, dx);
  // solid pieces between the openings, a lintel over each and a breast under a raised one: no hole
  // cut through one shape (a hole touching the floor leaves the shape's bottom face across the doorway)
  const parts = [];
  const piece = (x0, x1, y0, y1) => {
    if (x1 - x0 < 1e-4 || y1 - y0 < 1e-4) return;
    const b = new THREE.BoxGeometry(x1 - x0, y1 - y0, thickness);
    b.translate((x0 + x1) / 2, (y0 + y1) / 2, 0);
    parts.push(b);
  };
  let at = 0;
  for (const o of [...openings].sort((a, b) => a.offset - b.offset)) {
    const sill = o.sill ?? 0, top = Math.min(height, sill + (o.height ?? 2.05));
    piece(at, o.offset, 0, height);
    piece(o.offset, o.offset + o.width, top, height);
    piece(o.offset, o.offset + o.width, 0, sill);
    at = o.offset + o.width;
  }
  piece(at, length, 0, height);
  const mesh = shadow(new THREE.Mesh(metricUV(mergeGeometries(parts)), material));
  const g = new THREE.Group();
  g.add(mesh);
  g.position.set(from[0], y, from[1]);
  g.rotation.y = -angle;
  g.userData = { kind: "partition", from, to, length, thickness, height, y, angle };
  return g;
}

/**
 * Interior door leaf with its casing, for an opening of `width` × `height` in a wall of
 * `thickness`. Local frame: origin at the opening's start edge on the wall's centre line,
 * +x along the wall, +z = the side on your RIGHT walking from → to (house.js's "exterior" side).
 *   hinge: "start" | "end"   which jamb carries the hinges
 *   swing: "left" | "right"  which side of the wall the leaf opens into, walking from → to
 *   open:  degrees, 0 = closed
 */
export function interiorDoor({ width = 0.8, height = 2.05, thickness = 0.1, hinge = "start", swing = "left", open = 90, color = "#f4f2ee" }) {
  const g = new THREE.Group();
  const paint = plain(color, 0.6);
  const casing = 0.06, leafT = 0.04;
  const steel = plain("#b8b8b8", 0.35);
  // casing: two jambs and a head on both faces
  for (const side of [-1, 1]) {
    const z = side * (thickness / 2 + 0.005);
    for (const x of [-casing / 2, width + casing / 2]) {
      const j = new THREE.Mesh(new THREE.BoxGeometry(casing, height + casing, 0.012), paint);
      j.position.set(x, (height + casing) / 2, z);
      g.add(j);
    }
    const head = new THREE.Mesh(new THREE.BoxGeometry(width + 2 * casing, casing, 0.012), paint);
    head.position.set(width / 2, height + casing / 2, z);
    g.add(head);
  }
  const pivot = new THREE.Group();
  const hx = hinge === "start" ? 0 : width;
  const dir = hinge === "start" ? 1 : -1; // leaf runs from the hinge towards the other jamb
  const side = swing === "right" ? 1 : -1;
  pivot.position.set(hx, 0, side * thickness / 2);
  const leaf = shadow(new THREE.Mesh(new THREE.BoxGeometry(width - 0.01, height - 0.01, leafT), paint));
  leaf.position.set(dir * (width - 0.01) / 2, height / 2, side * leafT / 2);
  pivot.add(leaf);
  const handle = new THREE.Mesh(new THREE.BoxGeometry(0.12, 0.02, 0.02), steel);
  handle.position.set(dir * (width - 0.08), 1.02, side * (leafT + 0.03));
  pivot.add(handle);
  pivot.rotation.y = -dir * side * THREE.MathUtils.degToRad(open);
  g.add(pivot);
  g.userData = { kind: "interiorDoor", width, height };
  return g;
}

/**
 * One storey of rooms, partitions and doors.
 *   y:        finished floor level of this storey (the top of the slab)
 *   height:   clear height to the ceiling
 *   rooms:    [{ name, use, polygon, area (as printed on the plan, optional), floor = "oak" | "oak-light" | "tile" | "tile-dark" | "concrete", wallColor }]
 *   partitions: [{ from, to, thickness = 0.1, openings: [{ offset, width, height,
 *               door: { hinge, swing, open } | false }] }]   door defaults to a leaf; false = open passage
 * Returns a Group (kind "floorPlan"); userData.rooms lists each room with its area, userData.partitions
 * the partitions as given (walkable area, room views and the plan check read them).
 */
export function floorPlan({ y = 0, height = 2.5, rooms = [], partitions = [], ceiling = true, ceilingColor = "#f6f5f1" }) {
  const g = new THREE.Group();
  const listed = [];
  for (const r of rooms) {
    const floorMat = (FLOORS[r.floor ?? "oak"] ?? FLOORS.oak)();
    const f = horizontal(r.polygon, y + 0.015, 0.015, floorMat);
    f.userData = { kind: "roomFloor", room: r.name };
    g.add(f);
    if (ceiling) {
      // just under the clear height: level with it, the ceiling would share its plane with the
      // underside of the slab above (flicker in the browser, black patches in a path tracer)
      const c = horizontal(r.polygon, y + height - 0.005, 0.02, plain(ceilingColor, 0.95));
      c.userData = { kind: "ceiling", room: r.name };
      g.add(c);
    }
    listed.push({ name: r.name, use: r.use, polygon: r.polygon, area: Math.round(polygonArea(r.polygon) * 100) / 100,
      ...(r.area ? { planArea: r.area } : {}) });
  }
  for (const p of partitions) {
    const w = partition({ ...p, y, height: p.height ?? height });
    g.add(w);
    for (const o of p.openings ?? []) {
      if (o.door === false) continue;
      const d = interiorDoor({ width: o.width, height: o.height ?? 2.05, thickness: p.thickness ?? 0.1, ...(o.door ?? {}) });
      d.position.set(o.offset, 0, 0);
      w.add(d);
    }
  }
  const walls = partitions.map((p) => ({ from: p.from, to: p.to, thickness: p.thickness ?? 0.1, height: p.height ?? height,
    // door: the leaf's hinge and swing (the 2D plan draws them), false for an open passage
    openings: (p.openings ?? []).map((o) => ({ offset: o.offset, width: o.width, height: o.height ?? 2.05,
      door: o.door === false ? false : { hinge: o.door?.hinge ?? "start", swing: o.door?.swing ?? "left" } })) }));
  g.userData = { kind: "floorPlan", y, height, rooms: listed, partitions: walls };
  return g;
}

/**
 * The openings in the walls of `root`, as spans at the centre line of their wall: the doors and
 * passages of every floorPlan's partitions, and the window and door units of the shell (tagged
 * kind "window" / "door", origin at their bottom centre, x along the wall).
 */
function wallOpenings(root) {
  root.updateMatrixWorld(true);
  const out = [];
  const P = (m, x, y, z) => new THREE.Vector3(x, y, z).applyMatrix4(m);
  root.traverse((o) => {
    const u = o.userData ?? {};
    if (u.kind === "floorPlan") {
      for (const p of u.partitions ?? []) {
        const d = Math.hypot(p.to[0] - p.from[0], p.to[1] - p.from[1]);
        const dir = [(p.to[0] - p.from[0]) / d, (p.to[1] - p.from[1]) / d];
        for (const op of p.openings ?? []) {
          const at = (s) => P(o.matrixWorld, p.from[0] + dir[0] * s, u.y, p.from[1] + dir[1] * s);
          out.push({ a: at(op.offset), b: at(op.offset + op.width), y0: u.y + (op.sill ?? 0), y1: u.y + (op.sill ?? 0) + (op.height ?? 2.05) });
        }
      }
    } else if ((u.kind === "window" || u.kind === "door") && u.width) {
      const w = u.width / 2;
      const a = P(o.matrixWorld, -w, 0, 0), b = P(o.matrixWorld, w, 0, 0);
      out.push({ a, b, y0: a.y, y1: a.y + (u.height ?? 2.1) });
    }
  });
  return out;
}

/**
 * Ceramic tiles on the walls of a room, cut around its doors and windows: `height` metres up all
 * round, `fullHeight` on the edges listed in `full` (a shower, the wall behind a bath). Edge i runs
 * from room.polygon[i] to the next point (the clear inside faces, as in floorPlan). `root` is the
 * scene holding the walls: the openings are found in it (wallOpenings). `tiles` are tileMaterial's
 * options ({ size: [0.3, 0.6], color, jointColor, joint }). Add the result to the scene.
 */
export function tileWalls(root, room, { y = 0, height = 1.2, full = [], fullHeight = 2.4, edges, tiles = {} } = {}) {
  const poly = room.polygon ?? room;
  const material = tileMaterial(tiles);
  const openings = wallOpenings(root);
  const T = 0.008; // tile + adhesive
  let area = 0;
  for (let i = 0; i < poly.length; i++) {
    const [x1, z1] = poly[i], [x2, z2] = poly[(i + 1) % poly.length];
    area += x1 * z2 - x2 * z1;
  }
  const inward = area > 0 ? 1 : -1; // as in furnish.onWall
  const g = new THREE.Group();
  g.userData = { kind: "wallTiles", room: room.name, polygon: poly, y };
  for (let i = 0; i < poly.length; i++) {
    if (edges && !edges.includes(i)) continue;
    const [ax, az] = poly[i], [bx, bz] = poly[(i + 1) % poly.length];
    const L = Math.hypot(bx - ax, bz - az);
    if (L < 0.05) continue;
    const ux = (bx - ax) / L, uz = (bz - az) / L;
    const nx = -uz * inward, nz = ux * inward;
    const H = full.includes(i) ? fullHeight : height;
    // the openings in this wall: parallel to it, their centre line within half a wall's thickness behind it
    const holes = [];
    for (const o of openings) {
      const along = (p) => (p.x - ax) * ux + (p.z - az) * uz;
      const across = (p) => (p.x - ax) * nx + (p.z - az) * nz;
      const dx = o.b.x - o.a.x, dz = o.b.z - o.a.z, len = Math.hypot(dx, dz);
      if (len < 1e-3 || Math.abs((dx * ux + dz * uz) / len) < 0.95) continue;
      const c = (across(o.a) + across(o.b)) / 2;
      if (c > 0.05 || c < -0.45) continue;
      const s0 = Math.max(0, Math.min(along(o.a), along(o.b))), s1 = Math.min(L, Math.max(along(o.a), along(o.b)));
      const h0 = Math.max(0, o.y0 - y), h1 = Math.min(H, o.y1 - y);
      if (s1 - s0 > 1e-3 && h1 - h0 > 1e-3) holes.push([s0, s1, h0, h1]);
    }
    // the wall cut into columns at the openings' sides; in each, the heights no opening covers
    const xs = [...new Set([0, L, ...holes.flatMap(([s0, s1]) => [s0, s1])])].sort((p, q) => p - q);
    const parts = [];
    for (let k = 0; k + 1 < xs.length; k++) {
      const x0 = xs[k], x1 = xs[k + 1];
      if (x1 - x0 < 1e-3) continue;
      const cut = holes.filter(([s0, s1]) => s0 <= x0 + 1e-6 && s1 >= x1 - 1e-6).map(([, , h0, h1]) => [h0, h1]).sort((p, q) => p[0] - q[0]);
      let at = 0;
      for (const [h0, h1] of [...cut, [H, H]]) {
        if (h0 - at > 1e-3) {
          // in the wall's own frame (x along it from its start, y up, z into the room): the UVs in
          // metres run on across the pieces, so the joints line up
          const b = new THREE.BoxGeometry(x1 - x0, h0 - at, T);
          b.translate((x0 + x1) / 2, (at + h0) / 2, T / 2);
          parts.push(b);
        }
        at = Math.max(at, h1);
      }
    }
    if (!parts.length) continue;
    const mesh = new THREE.Mesh(metricUV(mergeGeometries(parts)), material);
    mesh.receiveShadow = true;
    // local x → the edge, local z → into the room
    mesh.rotation.y = Math.atan2(-uz, ux);
    if (inward < 0) mesh.scale.z = -1;
    mesh.position.set(ax, y, az);
    g.add(mesh);
  }
  return g;
}

/** Is point [x, z] inside polygon (even-odd), or within `margin` metres of its edges? */
function nearPolygon([x, z], poly, margin = 0) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, zi] = poly[i], [xj, zj] = poly[j];
    if ((zi > z) !== (zj > z) && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) inside = !inside;
  }
  if (inside || margin <= 0) return inside;
  for (let i = 0; i < poly.length; i++) {
    const [ax, az] = poly[i], [bx, bz] = poly[(i + 1) % poly.length];
    const dx = bx - ax, dz = bz - az, L2 = dx * dx + dz * dz || 1;
    const t = Math.max(0, Math.min(1, ((x - ax) * dx + (z - az) * dz) / L2));
    if (Math.hypot(x - (ax + t * dx), z - (az + t * dz)) <= margin) return true;
  }
  return false;
}

// What a piece counts as, from the words of its name: the kit's pieces ("towelRail"), the catalogue's
// models ("bed-oak-linen") and the builder's own tagged pieces ("Oak vanity and recessed washbasin").
const KINDS = {
  bed: (w) => w.includes("bed") || w.includes("bunk"),
  wc: (w) => w.includes("wc") || w.includes("toilet"),
  basin: (w) => ["basin", "washbasin", "vanity", "lavabo", "sink"].some((k) => w.includes(k)),
  bathtub: (w) => w.includes("bathtub") || w.includes("tub"), // not "bath" alone: a bath mat is not a bath
  shower: (w) => w.includes("shower"),
  towels: (w) => w.includes("towelrail") || w.includes("towel") || w.includes("towels"),
  kitchen: (w) => w.includes("kitchenrun") || w.includes("kitchen"),
  seat: (w) => ["sofa", "armchair", "couch"].some((k) => w.includes(k)),
  diningTable: (w) => w.includes("diningset") || w.includes("dining"),
  desk: (w) => w.includes("desk"),
};
// What each room use needs (the builder's "What each room gets", the parts that can be checked)
const ESSENTIALS = {
  bath: [["tiles", "walls not tiled (tileWalls)"], [["basin"], "no basin (fx.basin)"], [["bathtub", "shower"], "no bath or shower (fx.bathtub, fx.shower)"],
    [["towels"], "no towel rail with towels (fx.towelRail)"]],
  wc: [["tiles", "walls not tiled (tileWalls)"], [["wc"], "no WC (fx.wc)"], [["basin"], "no hand basin (fx.basin)"]],
  bedroom: [[["bed"], "no bed (\"bed-oak-linen\")"]],
  kitchen: [[["kitchen"], "no kitchen run (fx.kitchenRun)"]],
  "kitchen-living": [[["kitchen"], "no kitchen run (fx.kitchenRun)"]],
  living: [[["seat"], "no sofa or armchair"]],
  dining: [[["diningTable"], "no dining table"]],
  office: [[["desk"], "no desk"]],
};

/**
 * The rooms of every floorPlan in `root` that miss something their use needs (a bath without tiles
 * or towel rail, a bedroom without a bed...). One line per room; empty when all are complete. A
 * piece belongs to a room when its origin (bottom centre) stands in the room's floor polygon (or
 * within 15 cm of its walls: wall-hung pieces) at the room's storey.
 */
export function roomEssentials(root) {
  root.updateMatrixWorld(true);
  const rooms = [], pieces = [], tiles = [];
  const at = new THREE.Vector3();
  root.traverse((o) => {
    const u = o.userData ?? {};
    if (u.kind === "floorPlan") {
      o.getWorldPosition(at);
      for (const r of u.rooms ?? []) rooms.push({ ...r, y: u.y + at.y });
    } else if (u.kind === "furniture") {
      o.getWorldPosition(at);
      const words = String(u.name ?? "").replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase().split(/[^a-z]+/).filter(Boolean);
      // camelCase names count as one word too ("towelRail" → "towel", "rail", "towelrail")
      words.push(String(u.name ?? "").toLowerCase().replace(/[^a-z]/g, ""));
      pieces.push({ words, x: at.x, y: at.y, z: at.z });
    } else if (u.kind === "wallTiles") {
      tiles.push(u);
    }
  });
  const lines = [];
  for (const r of rooms) {
    const need = ESSENTIALS[r.use];
    if (!need) continue;
    const here = pieces.filter((p) => p.y > r.y - 0.3 && p.y < r.y + 2.2 && nearPolygon([p.x, p.z], r.polygon, 0.15));
    const has = (kinds) => here.some((p) => kinds.some((k) => KINDS[k](p.words)));
    const tiled = tiles.some((t) => t.room === r.name || (t.polygon && Math.abs((t.y ?? r.y) - r.y) < 0.3 &&
      nearPolygon(t.polygon.reduce(([sx, sz], [x, z]) => [sx + x / t.polygon.length, sz + z / t.polygon.length], [0, 0]), r.polygon)));
    const missing = need.filter(([what]) => (what === "tiles" ? !tiled : !has(what))).map(([, say]) => say);
    if (missing.length) lines.push(`"${r.name}" (${r.use}): ${missing.join(", ")}`);
  }
  if (lines.length) {
    lines.push("(pieces are recognised by userData = { kind: \"furniture\", name }: tag the ones you build yourself with a name that says what they are)");
  }
  return lines;
}

export default { floorPlan, partition, interiorDoor, polygonArea, tileWalls, roomEssentials };
