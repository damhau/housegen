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
import { mat } from "./house.js";
import { finishMaterial, metricUV } from "./finishes.js";

// textured when the finishes are loaded (finishes.loadFinishes), plain colours otherwise
const FLOORS = {
  oak: () => finishMaterial("oak-floor") ?? mat.wood("#c9a57c"),
  "oak-light": () => finishMaterial("oak-floor", { color: "#fff6ea" }) ?? mat.wood("#dcc3a0"),
  tile: () => finishMaterial("stone-tile") ?? mat.plaster("#d8d5ce", 0.5),
  "tile-dark": () => finishMaterial("stone-tile", { color: "#8d8b86" }) ?? mat.plaster("#8d8b86", 0.5),
  concrete: () => mat.concrete("#b9b6ae"),
};
const PLASTER = () => finishMaterial("plaster") ?? mat.plaster("#f1efea", 0.92);

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
  const paint = mat.paint(color);
  const casing = 0.06, leafT = 0.04;
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
  const handle = new THREE.Mesh(new THREE.BoxGeometry(0.12, 0.02, 0.02), mat.metal("#b8b8b8"));
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
 *   rooms:    [{ name, use, polygon, floor = "oak" | "oak-light" | "tile" | "tile-dark" | "concrete", wallColor }]
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
      const c = horizontal(r.polygon, y + height + 0.02, 0.02, mat.plaster(ceilingColor, 0.95));
      c.userData = { kind: "ceiling", room: r.name };
      g.add(c);
    }
    listed.push({ name: r.name, use: r.use, polygon: r.polygon, area: Math.round(polygonArea(r.polygon) * 100) / 100 });
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
    openings: (p.openings ?? []).map((o) => ({ offset: o.offset, width: o.width, height: o.height ?? 2.05, door: o.door !== false })) }));
  g.userData = { kind: "floorPlan", y, height, rooms: listed, partitions: walls };
  return g;
}

export default { floorPlan, partition, interiorDoor, polygonArea };
