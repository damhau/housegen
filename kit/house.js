// housekit — parametric building blocks for exterior house scenes.
//
// Conventions (shared with runtime.js):
//   * units are metres, +x = east, +z = south, +y = up (north is -z)
//   * footprints / polygons are arrays of [x, z] points, any winding
//   * walls are described by a start point and an end point on the ground.
//     Walking from `from` to `to`, the EXTERIOR is on your RIGHT. On a north-up map
//     that means going COUNTER-CLOCKWISE around the footprint: north wall east→west,
//     west wall north→south, south wall west→east, east wall south→north.
//     `perimeterWalls` does this automatically for a whole footprint.
//     Openings (windows, doors) are cut through the wall by the wall itself;
//     use `placeOnWall(wall, obj, offset, sill)` to put a window unit in a hole.
//     Opening offsets are measured from `from`, which is the LEFT end of the façade
//     as seen from outside.
//
// Every builder returns a THREE.Object3D that the caller adds to the scene.

import * as THREE from "three";

// --------------------------------------------------------------------------
// Materials
// --------------------------------------------------------------------------

const _cache = new Map();

function cached(key, make) {
  if (!_cache.has(key)) _cache.set(key, make());
  return _cache.get(key);
}

export const mat = {
  plaster: (color = "#e9e6dd", roughness = 0.9) =>
    cached(`plaster:${color}:${roughness}`, () => new THREE.MeshStandardMaterial({ color, roughness, metalness: 0 })),
  concrete: (color = "#b9b7b0") =>
    cached(`concrete:${color}`, () => new THREE.MeshStandardMaterial({ color, roughness: 0.95 })),
  wood: (color = "#8b6a42") =>
    cached(`wood:${color}`, () => new THREE.MeshStandardMaterial({ color, roughness: 0.7 })),
  metal: (color = "#3a3d40") =>
    cached(`metal:${color}`, () => new THREE.MeshStandardMaterial({ color, roughness: 0.35, metalness: 0.8 })),
  roof: (color = "#4a4a4a") =>
    cached(`roof:${color}`, () => new THREE.MeshStandardMaterial({ color, roughness: 0.85 })),
  tile: (color = "#a0523d") =>
    cached(`tile:${color}`, () => new THREE.MeshStandardMaterial({ color, roughness: 0.8 })),
  glass: (tint = "#9fb8c8", opacity = 0.55) =>
    cached(`glass:${tint}:${opacity}`, () =>
      new THREE.MeshPhysicalMaterial({
        color: tint,
        roughness: 0.05,
        metalness: 0,
        transmission: 0.35,
        transparent: true,
        opacity,
        reflectivity: 0.9,
      })),
  grass: (color = "#7f9a52") =>
    cached(`grass:${color}`, () => new THREE.MeshStandardMaterial({ color, roughness: 1 })),
  gravel: (color = "#a6a39a") =>
    cached(`gravel:${color}`, () => new THREE.MeshStandardMaterial({ color, roughness: 1 })),
  asphalt: (color = "#5b5b5b") =>
    cached(`asphalt:${color}`, () => new THREE.MeshStandardMaterial({ color, roughness: 1 })),
  foliage: (color = "#5f8a3f") =>
    cached(`foliage:${color}`, () => new THREE.MeshStandardMaterial({ color, roughness: 0.9, side: THREE.DoubleSide })),
  paint: (color) =>
    cached(`paint:${color}`, () => new THREE.MeshStandardMaterial({ color, roughness: 0.6 })),
};

function shadow(mesh, cast = true, receive = true) {
  mesh.castShadow = cast;
  mesh.receiveShadow = receive;
  return mesh;
}

function shapeFromPolygon(points) {
  const s = new THREE.Shape();
  points.forEach(([x, z], i) => (i === 0 ? s.moveTo(x, z) : s.lineTo(x, z)));
  s.closePath();
  return s;
}

// --------------------------------------------------------------------------
// Primitive helpers
// --------------------------------------------------------------------------

/** Axis-aligned box. size=[w,h,d], position = centre of the box. */
export function box({ size, position = [0, 0, 0], material = mat.plaster(), rotationY = 0 }) {
  const [w, h, d] = size;
  const m = shadow(new THREE.Mesh(new THREE.BoxGeometry(w, h, d), material));
  m.position.set(...position);
  m.rotation.y = rotationY;
  return m;
}

/**
 * Horizontal slab extruded from a footprint polygon.
 * `y` is the TOP surface height. Use it for floor plates, terraces, plinths.
 */
export function slab({ polygon, y = 0, thickness = 0.25, material = mat.concrete() }) {
  const geo = new THREE.ExtrudeGeometry(shapeFromPolygon(polygon), { depth: thickness, bevelEnabled: false });
  const m = shadow(new THREE.Mesh(geo, material));
  // shape is in XY plane extruded along +Z; rotate so the polygon lies in XZ
  m.rotation.x = Math.PI / 2;
  m.position.y = y;
  return m;
}

/**
 * Vertical prism from a footprint polygon: a whole floor volume without
 * openings. Fast way to block out a shell; use `wall` where you need windows.
 */
export function volume({ polygon, y = 0, height = 3, material = mat.plaster() }) {
  const geo = new THREE.ExtrudeGeometry(shapeFromPolygon(polygon), { depth: height, bevelEnabled: false });
  const m = shadow(new THREE.Mesh(geo, material));
  m.rotation.x = -Math.PI / 2;
  m.position.y = y;
  return m;
}

/**
 * A wall segment with rectangular openings cut through it.
 *   from/to: [x, z] ground points. Exterior is on the right when walking from→to.
 *   openings: [{ offset, sill, width, height }] — offset measured along the wall from `from`,
 *             sill measured from the wall base (y).
 * Returns a Group with .userData = { from, to, length, normal, y } for placeOnWall.
 */
export function wall({ from, to, height = 3, thickness = 0.3, y = 0, openings = [], material = mat.plaster() }) {
  const dx = to[0] - from[0];
  const dz = to[1] - from[1];
  const length = Math.hypot(dx, dz);
  const angle = Math.atan2(dz, dx);

  const shape = new THREE.Shape();
  shape.moveTo(0, 0);
  shape.lineTo(length, 0);
  shape.lineTo(length, height);
  shape.lineTo(0, height);
  shape.closePath();
  for (const o of openings) {
    const hole = new THREE.Path();
    const x0 = o.offset, x1 = o.offset + o.width, y0 = o.sill, y1 = o.sill + o.height;
    hole.moveTo(x0, y0);
    hole.lineTo(x1, y0);
    hole.lineTo(x1, y1);
    hole.lineTo(x0, y1);
    hole.closePath();
    shape.holes.push(hole);
  }
  const geo = new THREE.ExtrudeGeometry(shape, { depth: thickness, bevelEnabled: false });
  const mesh = shadow(new THREE.Mesh(geo, material));
  // local: wall runs along +x, extruded toward +z (which after rotation is the interior side)
  mesh.position.z = -thickness; // extrude from -thickness..0 so the exterior face is at local z=0
  const g = new THREE.Group();
  g.add(mesh);
  g.position.set(from[0], y, from[1]);
  g.rotation.y = -angle;
  // exterior normal: right-hand side of the walking direction (in XZ, right of (dx,dz) is (-dz, dx)... we want +local z after rotation)
  const n = new THREE.Vector3(-dz, 0, dx).normalize();
  // The exterior face is local z=0 with interior toward local -z; local +z maps to world (-sin, cos) of angle... see placeOnWall.
  g.userData = { kind: "wall", from, to, length, height, thickness, y, angle, normal: [n.x, n.z] };
  return g;
}

/**
 * Place an object (window/door unit built by this kit) into a wall.
 * The unit's local +z faces outward, local origin at its bottom-centre.
 */
/** How deep window/door units sit behind the exterior face of a wall (the reveal). */
export const UNIT_INSET = 0.12;

export function placeOnWall(wallGroup, unit, offset, sill = 0, inset = UNIT_INSET) {
  const { angle, y, from } = wallGroup.userData;
  const alongX = Math.cos(angle), alongZ = Math.sin(angle);
  const nx = -alongZ, nz = alongX; // exterior normal (right of walking direction)
  // `offset` is the centre of the unit measured along the wall from `from`;
  // the unit is recessed `inset` metres into the wall from the exterior face.
  unit.position.set(from[0] + alongX * offset - nx * inset, y + sill, from[1] + alongZ * offset - nz * inset);
  unit.rotation.y = Math.atan2(nx, nz); // local +z → exterior normal
  return unit;
}

/**
 * All exterior walls of a footprint at once, correctly oriented whatever the polygon winding.
 *   polygon: [[x,z], ...]  edges are numbered i = 0..n-1 from polygon[i] to polygon[i+1]
 *   openings: { [edgeIndex]: [{ offset, sill, width, height, ...anything for makeUnit }] }
 *             offset = distance from the LEFT end of the façade AS SEEN FROM OUTSIDE.
 *   makeUnit(opening, edgeIndex) → window/door unit (optional)
 * Returns a Group; group.userData.walls[i] is the wall group of edge i.
 */
export function perimeterWalls({ polygon, height = 3, y = 0, thickness = 0.3, material = mat.plaster(), openings = {}, makeUnit }) {
  const g = new THREE.Group();
  // signed area in the XZ plane; with +x east and +z south, walking with the exterior on the
  // right means going counter-clockwise on a north-up map, i.e. NEGATIVE signed area here.
  let area = 0;
  for (let i = 0; i < polygon.length; i++) {
    const [x1, z1] = polygon[i], [x2, z2] = polygon[(i + 1) % polygon.length];
    area += x1 * z2 - x2 * z1;
  }
  const flip = area > 0;
  const walls = [];
  for (let i = 0; i < polygon.length; i++) {
    const a = polygon[i], b = polygon[(i + 1) % polygon.length];
    const [from, to] = flip ? [b, a] : [a, b];
    const ops = openings[i] ?? [];
    const w = makeUnit
      ? wallWithUnits({ from, to, height, y, thickness, material, openings: ops }, (o) => makeUnit(o, i))
      : wall({ from, to, height, y, thickness, material, openings: ops });
    walls.push(w);
    g.add(w);
  }
  g.userData = { kind: "perimeter", walls };
  return g;
}

/** Builds a wall AND automatically fills each opening with the given unit factory. */
export function wallWithUnits(opts, makeUnit) {
  const g = new THREE.Group();
  const w = wall(opts);
  g.add(w);
  for (const o of opts.openings ?? []) {
    const unit = makeUnit(o);
    if (unit) g.add(placeOnWall(w, unit, o.offset + o.width / 2, o.sill));
  }
  g.userData = w.userData;
  return g;
}

// --------------------------------------------------------------------------
// Openings
// --------------------------------------------------------------------------

/**
 * Window unit: frame + glazing + optional roller/louvered shutter.
 * Local origin: bottom-centre, faces +z. Fits an opening of the same width/height.
 */
export function windowUnit({
  width = 1.2,
  height = 1.3,
  frameColor = "#4b4f52",
  frameDepth = 0.08,
  mullions = 1,
  transoms = 0,
  glassTint = "#9fb8c8",
  shutter = "none", // "none" | "roller" | "louvered"
  shutterOpen = 0.35, // 0 = fully closed, 1 = fully open (roller only)
  shutterColor = "#d9d9d9",
  sillDepth = 0.12,
}) {
  const g = new THREE.Group();
  const frame = mat.paint(frameColor);
  const t = 0.06;
  // outer frame
  const parts = [
    [width, t, frameDepth, 0, t / 2, 0],
    [width, t, frameDepth, 0, height - t / 2, 0],
    [t, height, frameDepth, -width / 2 + t / 2, height / 2, 0],
    [t, height, frameDepth, width / 2 - t / 2, height / 2, 0],
  ];
  for (let i = 1; i <= mullions; i++) {
    const x = -width / 2 + (width / (mullions + 1)) * i;
    parts.push([t * 0.7, height, frameDepth * 0.8, x, height / 2, 0]);
  }
  for (let i = 1; i <= transoms; i++) {
    const yy = (height / (transoms + 1)) * i;
    parts.push([width, t * 0.7, frameDepth * 0.8, 0, yy, 0]);
  }
  for (const [w, h, d, x, y, z] of parts) {
    g.add(box({ size: [w, h, d], position: [x, y, z], material: frame }));
  }
  const glass = new THREE.Mesh(new THREE.PlaneGeometry(width - 2 * t, height - 2 * t), mat.glass(glassTint));
  glass.position.set(0, height / 2, -0.01);
  g.add(glass);
  // The unit is recessed UNIT_INSET into the wall by placeOnWall, so anything that must
  // sit on or in front of the façade lives at z >= UNIT_INSET.
  const face = UNIT_INSET;
  // sill: flush with the façade, slightly proud
  g.add(box({ size: [width + 0.1, 0.04, sillDepth], position: [0, -0.02, face - sillDepth / 2 + 0.05], material: mat.concrete("#cfcbc2") }));
  if (shutter === "roller") {
    const boxH = 0.22;
    g.add(box({ size: [width + 0.08, boxH, 0.16], position: [0, height + boxH / 2, face + 0.04], material: mat.paint(shutterColor) }));
    const closed = Math.max(0, 1 - shutterOpen);
    if (closed > 0.02) {
      const h = height * closed;
      const slat = box({ size: [width - 0.02, h, 0.02], position: [0, height - h / 2, face + 0.02], material: mat.paint(shutterColor) });
      g.add(slat);
      // slat lines
      const lines = Math.max(1, Math.floor(h / 0.08));
      const lineMat = mat.paint("#bdbdbd");
      for (let i = 0; i < lines; i++) {
        g.add(box({ size: [width - 0.02, 0.01, 0.005], position: [0, height - (i + 0.5) * (h / lines), face + 0.035], material: lineMat }));
      }
    }
  } else if (shutter === "louvered") {
    const leaf = width / 2;
    for (const s of [-1, 1]) {
      const l = box({ size: [leaf, height, 0.04], position: [s * (width / 2 + leaf / 2 + 0.02), height / 2, face + 0.03], material: mat.paint(shutterColor) });
      g.add(l);
    }
  }
  g.userData = { kind: "window", width, height };
  return g;
}

/** Door unit (solid or glazed). Local origin bottom-centre, faces +z. */
export function door({ width = 1.0, height = 2.1, color = "#3b3f42", glass = false, frameColor = "#4b4f52" }) {
  const g = new THREE.Group();
  const t = 0.07;
  g.add(box({ size: [width, height, 0.1], position: [0, height / 2, 0], material: mat.paint(frameColor) }));
  const leaf = glass
    ? new THREE.Mesh(new THREE.PlaneGeometry(width - 2 * t, height - 2 * t), mat.glass("#8fa9bb", 0.7))
    : box({ size: [width - 2 * t, height - 2 * t, 0.05], position: [0, 0, 0], material: mat.paint(color) });
  leaf.position.set(0, height / 2, 0.03);
  g.add(leaf);
  const handle = box({ size: [0.03, 0.25, 0.03], position: [width / 2 - 0.15, height / 2, 0.08], material: mat.metal("#c9c9c9") });
  g.add(handle);
  g.userData = { kind: "door", width, height };
  return g;
}

/** Large sliding glass door / glazed bay. */
export function slidingDoor({ width = 2.4, height = 2.2, panels = 2, frameColor = "#4b4f52" }) {
  return windowUnit({ width, height, frameColor, mullions: panels - 1, transoms: 0, glassTint: "#8fa9bb", sillDepth: 0.04 });
}

// --------------------------------------------------------------------------
// Roofs
// --------------------------------------------------------------------------

/** Flat roof with parapet + gravel/membrane. `y` = top of the slab. */
export function flatRoof({ polygon, y, thickness = 0.3, parapet = 0.35, parapetThickness = 0.25, material = mat.roof("#3f4144"), edgeMaterial = mat.plaster() }) {
  const g = new THREE.Group();
  g.add(slab({ polygon, y, thickness, material }));
  if (parapet > 0) {
    // parapet along polygon edges; it starts below the slab so the slab edge is hidden
    for (let i = 0; i < polygon.length; i++) {
      const a = polygon[i], b = polygon[(i + 1) % polygon.length];
      g.add(wall({ from: a, to: b, height: parapet + thickness, thickness: parapetThickness, y: y - thickness, material: edgeMaterial }));
    }
  }
  return g;
}

/**
 * Gable roof over a rectangle: ridge runs along local x.
 *   width = along ridge, depth = across, ridgeHeight above eave line (y).
 */
export function gableRoof({ width, depth, ridgeHeight = 2, y = 0, overhang = 0.4, position = [0, 0, 0], rotationY = 0, material = mat.tile(), underside = mat.wood("#d8cdb5") }) {
  const g = new THREE.Group();
  const w = width + 2 * overhang, d = depth + 2 * overhang;
  const shape = new THREE.Shape();
  shape.moveTo(-d / 2, 0);
  shape.lineTo(d / 2, 0);
  shape.lineTo(0, ridgeHeight);
  shape.closePath();
  const geo = new THREE.ExtrudeGeometry(shape, { depth: w, bevelEnabled: false });
  const m = shadow(new THREE.Mesh(geo, material));
  m.rotation.y = Math.PI / 2;
  m.position.set(-w / 2, 0, 0);
  g.add(m);
  // thin underside plate to hide interior
  const plate = box({ size: [w, 0.05, d], position: [0, 0.02, 0], material: underside });
  g.add(plate);
  g.position.set(position[0], y + position[1], position[2]);
  g.rotation.y = rotationY;
  return g;
}

/** Mono-pitch (shed) roof: high edge at local -z, low edge at +z. */
export function shedRoof({ width, depth, rise = 1, y = 0, overhang = 0.3, position = [0, 0, 0], rotationY = 0, material = mat.roof("#3f4144") }) {
  const g = new THREE.Group();
  const w = width + 2 * overhang, d = depth + 2 * overhang;
  const shape = new THREE.Shape();
  shape.moveTo(-d / 2, rise);
  shape.lineTo(d / 2, 0);
  shape.lineTo(d / 2, -0.12);
  shape.lineTo(-d / 2, rise - 0.12);
  shape.closePath();
  const geo = new THREE.ExtrudeGeometry(shape, { depth: w, bevelEnabled: false });
  const m = shadow(new THREE.Mesh(geo, material));
  m.rotation.y = Math.PI / 2;
  m.position.set(-w / 2, 0, 0);
  g.add(m);
  g.position.set(position[0], y + position[1], position[2]);
  g.rotation.y = rotationY;
  return g;
}

/** Simple chimney stack. position = [x, y(base), z]. */
export function chimney({ position, size = [0.6, 1.2, 0.5], material = mat.plaster("#d7d3c8") }) {
  const g = new THREE.Group();
  const [w, h, d] = size;
  g.add(box({ size: [w, h, d], position: [0, h / 2, 0], material }));
  g.add(box({ size: [w + 0.1, 0.08, d + 0.1], position: [0, h, 0], material: mat.concrete("#8f8c85") }));
  g.add(box({ size: [0.18, 0.35, 0.18], position: [0, h + 0.2, 0], material: mat.metal("#2c2c2c") }));
  g.position.set(...position);
  return g;
}

// --------------------------------------------------------------------------
// Exterior fittings
// --------------------------------------------------------------------------

/**
 * Railing along a line. from/to are [x, z]; y = base height.
 * style: "bars" | "glass" | "cable" | "solid"
 */
export function railing({ from, to, y = 0, height = 1.0, style = "bars", color = "#3a3d40", spacing = 0.12 }) {
  const g = new THREE.Group();
  const dx = to[0] - from[0], dz = to[1] - from[1];
  const length = Math.hypot(dx, dz);
  const angle = Math.atan2(dz, dx);
  const m = mat.metal(color);
  const rail = box({ size: [length, 0.05, 0.05], position: [length / 2, height, 0], material: m });
  g.add(rail);
  const posts = Math.max(2, Math.round(length / 1.5) + 1);
  for (let i = 0; i < posts; i++) {
    g.add(box({ size: [0.05, height, 0.05], position: [(length / (posts - 1)) * i, height / 2, 0], material: m }));
  }
  if (style === "bars") {
    const n = Math.floor(length / spacing);
    for (let i = 1; i < n; i++) g.add(box({ size: [0.015, height - 0.1, 0.015], position: [i * spacing, (height - 0.1) / 2, 0], material: m }));
  } else if (style === "cable") {
    for (let k = 1; k < 6; k++) g.add(box({ size: [length, 0.008, 0.008], position: [length / 2, (height / 6) * k, 0], material: m }));
  } else if (style === "glass") {
    const p = new THREE.Mesh(new THREE.PlaneGeometry(length, height - 0.1), mat.glass("#cfe0ea", 0.35));
    p.position.set(length / 2, (height - 0.1) / 2 + 0.05, 0);
    g.add(p);
  } else if (style === "solid") {
    g.add(box({ size: [length, height, 0.15], position: [length / 2, height / 2, 0], material: mat.plaster() }));
  }
  g.position.set(from[0], y, from[1]);
  g.rotation.y = -angle;
  return g;
}

/**
 * Exterior stair. Starts at `position` (bottom-centre of first step), climbs toward local +z... no:
 * climbs toward local -z (away from the viewer) by default; rotate with rotationY.
 */
export function stairs({ steps = 6, width = 1.2, rise = 0.17, run = 0.28, position = [0, 0, 0], rotationY = 0, material = mat.concrete("#c9c6be"), sideWalls = true }) {
  const g = new THREE.Group();
  for (let i = 0; i < steps; i++) {
    const h = rise * (i + 1);
    g.add(box({ size: [width, h, run], position: [0, h / 2, -(i * run + run / 2)], material }));
  }
  if (sideWalls) {
    const total = steps * run, hTop = steps * rise;
    for (const s of [-1, 1]) {
      const shape = new THREE.Shape();
      shape.moveTo(0, 0); shape.lineTo(total, 0); shape.lineTo(total, hTop + 0.9); shape.lineTo(0, 0.9); shape.closePath();
      const geo = new THREE.ExtrudeGeometry(shape, { depth: 0.12, bevelEnabled: false });
      const m = shadow(new THREE.Mesh(geo, mat.plaster()));
      m.rotation.y = Math.PI / 2;
      m.position.set(s * (width / 2 + 0.06), 0, 0);
      g.add(m);
    }
  }
  g.position.set(...position);
  g.rotation.y = rotationY;
  return g;
}

/** Balcony: slab + railing on the three free sides. Attached edge is local -z. */
export function balcony({ width = 3, depth = 1.5, thickness = 0.2, position = [0, 0, 0], rotationY = 0, railingStyle = "bars", railingColor = "#3a3d40", material = mat.concrete("#d0ccc3") }) {
  const g = new THREE.Group();
  g.add(box({ size: [width, thickness, depth], position: [0, -thickness / 2, depth / 2], material }));
  const y = 0;
  g.add(railing({ from: [-width / 2, 0], to: [-width / 2, depth], y, style: railingStyle, color: railingColor }));
  g.add(railing({ from: [-width / 2, depth], to: [width / 2, depth], y, style: railingStyle, color: railingColor }));
  g.add(railing({ from: [width / 2, depth], to: [width / 2, 0], y, style: railingStyle, color: railingColor }));
  g.position.set(...position);
  g.rotation.y = rotationY;
  return g;
}

/** Glass canopy / conservatory-style roof on slim posts. Local origin at ground, centre. */
export function canopy({ width = 4, depth = 2.5, height = 2.6, position = [0, 0, 0], rotationY = 0, frameColor = "#2e3133", posts = true }) {
  const g = new THREE.Group();
  const f = mat.metal(frameColor);
  const beams = Math.max(2, Math.round(width / 0.9) + 1);
  for (let i = 0; i < beams; i++) {
    g.add(box({ size: [0.06, 0.1, depth], position: [-width / 2 + (width / (beams - 1)) * i, height, 0], material: f }));
  }
  g.add(box({ size: [width, 0.1, 0.08], position: [0, height, depth / 2], material: f }));
  g.add(box({ size: [width, 0.1, 0.08], position: [0, height, -depth / 2], material: f }));
  const glass = new THREE.Mesh(new THREE.PlaneGeometry(width, depth), mat.glass("#d5e6ee", 0.4));
  glass.rotation.x = -Math.PI / 2;
  glass.position.y = height + 0.06;
  g.add(glass);
  if (posts) {
    for (const s of [-1, 1]) g.add(box({ size: [0.08, height, 0.08], position: [s * (width / 2 - 0.05), height / 2, depth / 2 - 0.05], material: f }));
  }
  g.position.set(...position);
  g.rotation.y = rotationY;
  return g;
}

/** Planter box with bushy plants (for terraces/balconies). */
export function planter({ length = 1.5, position = [0, 0, 0], rotationY = 0, color = "#5d6b62", seed = 1 }) {
  const rnd = seeded(seed);
  const g = new THREE.Group();
  g.add(box({ size: [length, 0.45, 0.4], position: [0, 0.225, 0], material: mat.paint(color) }));
  const n = Math.max(2, Math.round(length / 0.35));
  for (let i = 0; i < n; i++) {
    const s = new THREE.Mesh(new THREE.SphereGeometry(0.22 + rnd() * 0.08, 8, 6), mat.foliage("#6a9a48"));
    s.position.set(-length / 2 + (i + 0.5) * (length / n), 0.6, (rnd() - 0.5) * 0.15);
    s.scale.y = 1.3;
    shadow(s, true, false);
    g.add(s);
  }
  g.position.set(...position);
  g.rotation.y = rotationY;
  g.userData = { kind: "planter" };
  return g;
}

// --------------------------------------------------------------------------
// Landscape
// --------------------------------------------------------------------------

function seeded(seed) {
  let s = seed >>> 0 || 1;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
}

/** Hedge along a line. */
export function hedge({ from, to, height = 1.2, thickness = 0.6, y = 0, color = "#4f7a3a", seed = 1 }) {
  const rnd = seeded(seed);
  const dx = to[0] - from[0], dz = to[1] - from[1];
  const length = Math.hypot(dx, dz);
  const g = new THREE.Group();
  const m = mat.foliage(color);
  const segs = Math.max(1, Math.round(length / 0.8));
  for (let i = 0; i < segs; i++) {
    const b = shadow(new THREE.Mesh(new THREE.BoxGeometry(length / segs + 0.05, height, thickness), m));
    b.position.set((i + 0.5) * (length / segs), height / 2, 0);
    b.rotation.z = (rnd() - 0.5) * 0.04;
    g.add(b);
  }
  g.position.set(from[0], y, from[1]);
  g.rotation.y = -Math.atan2(dz, dx);
  g.userData = { kind: "hedge" };
  return g;
}

/** Flat pathway / driveway along a polyline of [x,z] points. */
export function pathway({ points, width = 1.2, y = 0.02, material = mat.gravel() }) {
  const g = new THREE.Group();
  for (let i = 0; i < points.length - 1; i++) {
    const a = points[i], b = points[i + 1];
    const dx = b[0] - a[0], dz = b[1] - a[1];
    const len = Math.hypot(dx, dz);
    const m = new THREE.Mesh(new THREE.BoxGeometry(len + width * 0.5, 0.03, width), material);
    m.receiveShadow = true;
    m.position.set((a[0] + b[0]) / 2, y, (a[1] + b[1]) / 2);
    m.rotation.y = -Math.atan2(dz, dx);
    g.add(m);
  }
  return g;
}

/** Ground patch (lawn, gravel area, terrace) from a polygon; sits slightly above the base ground. */
export function groundPatch({ polygon, y = 0.01, material = mat.grass() }) {
  const geo = new THREE.ShapeGeometry(shapeFromPolygon(polygon));
  const m = new THREE.Mesh(geo, material);
  m.rotation.x = Math.PI / 2;
  m.position.y = y;
  m.receiveShadow = true;
  return m;
}

/** Retaining wall / low garden wall. */
export function gardenWall(opts) {
  return wall({ height: 0.6, thickness: 0.25, material: mat.concrete("#b5b1a6"), ...opts });
}

/** Simple fence: posts + horizontal boards. */
export function fence({ from, to, height = 1.2, y = 0, color = "#7a6a52" }) {
  const dx = to[0] - from[0], dz = to[1] - from[1];
  const length = Math.hypot(dx, dz);
  const g = new THREE.Group();
  const m = mat.wood(color);
  const posts = Math.max(2, Math.round(length / 2) + 1);
  for (let i = 0; i < posts; i++) g.add(box({ size: [0.08, height, 0.08], position: [(length / (posts - 1)) * i, height / 2, 0], material: m }));
  for (const f of [0.35, 0.65, 0.95]) g.add(box({ size: [length, 0.1, 0.03], position: [length / 2, height * f, 0.05], material: m }));
  g.position.set(from[0], y, from[1]);
  g.rotation.y = -Math.atan2(dz, dx);
  return g;
}

/** Car silhouette for scale (optional). */
export function car({ position = [0, 0, 0], rotationY = 0, color = "#2f3a48" }) {
  const g = new THREE.Group();
  g.add(box({ size: [4.3, 0.6, 1.8], position: [0, 0.5, 0], material: mat.paint(color) }));
  g.add(box({ size: [2.4, 0.55, 1.6], position: [-0.2, 1.05, 0], material: mat.glass("#8fa9bb", 0.8) }));
  for (const [x, z] of [[-1.4, 0.8], [1.4, 0.8], [-1.4, -0.8], [1.4, -0.8]]) {
    const w = new THREE.Mesh(new THREE.CylinderGeometry(0.32, 0.32, 0.22, 12), mat.metal("#1a1a1a"));
    w.rotation.x = Math.PI / 2; w.position.set(x, 0.32, z); g.add(w);
  }
  g.position.set(...position);
  g.rotation.y = rotationY;
  g.userData = { kind: "car" };
  return g;
}

// --------------------------------------------------------------------------
// Terrain (registered globally so every later placement can ask groundY)
// --------------------------------------------------------------------------

let _heightAt = () => 0;

/** Ground height at [x, z]: the registered terrain's, or 0 on flat ground. */
export function groundY(x, z) {
  return _heightAt(x, z);
}

/**
 * Sloped / shaped ground. Give EITHER heightAt(x, z) → y, OR `points: [[x, z, y], ...]`
 * (spot heights: the surface is interpolated between them and flattens to `edgeHeight`
 * far away). The mesh is centred on `center` and covers `size` metres. Registers itself
 * so groundY(), ribbon(), pebbleStrip(), leafTree()… follow it. Hide ctx.ground when you
 * use it: `ctx.ground.visible = false`.
 */
export function terrain({ size = [90, 90], center = [0, 0], resolution = 0.5, heightAt, points, edgeHeight = 0, material = mat.grass("#8a9a68") }) {
  let fn = heightAt;
  if (!fn && points) {
    fn = (x, z) => {
      let num = 0, den = 0;
      for (const [px, pz, py] of points) {
        const d2 = (x - px) ** 2 + (z - pz) ** 2;
        if (d2 < 1e-6) return py;
        const w = 1 / (d2 * d2);
        num += w * py; den += w;
      }
      const far = Math.min(...points.map(([px, pz]) => Math.hypot(x - px, z - pz)));
      const t = Math.min(1, Math.max(0, (far - 6) / 14)); // blend to edgeHeight beyond ~6 m from the nearest point
      return (num / den) * (1 - t) + edgeHeight * t;
    };
  }
  if (!fn) fn = () => 0;
  _heightAt = fn;
  const [w, d] = size;
  const nx = Math.max(2, Math.round(w / resolution)), nz = Math.max(2, Math.round(d / resolution));
  const geo = new THREE.PlaneGeometry(w, d, nx, nz);
  geo.rotateX(-Math.PI / 2);
  const pos = geo.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i) + center[0], z = pos.getZ(i) + center[1];
    pos.setY(i, fn(x, z));
  }
  geo.computeVertexNormals();
  const m = new THREE.Mesh(geo, material);
  m.position.set(center[0], 0.0, center[1]);
  m.receiveShadow = true;
  m.userData = { kind: "terrain", heightAt: fn };
  return m;
}

/** Thin cylinder between two [x,y,z] points (branches, rails, posts…). */
export function rod(from, to, radius = 0.03, material = mat.metal()) {
  const a = new THREE.Vector3(...from), b = new THREE.Vector3(...to);
  const len = a.distanceTo(b);
  const geo = new THREE.CylinderGeometry(radius, radius, len, 7);
  const m = shadow(new THREE.Mesh(geo, material), true, false);
  m.position.copy(a).lerp(b, 0.5);
  m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), b.clone().sub(a).normalize());
  return m;
}

/** Path / drive draped on the ground along a polyline of [x, z] points. */
export function ribbon({ points, width = 1.2, lift = 0.03, material = mat.gravel() }) {
  const left = [], right = [];
  for (let i = 0; i < points.length; i++) {
    const p = points[i], q = points[Math.min(i + 1, points.length - 1)], o = points[Math.max(i - 1, 0)];
    let dx = q[0] - o[0], dz = q[1] - o[1];
    const l = Math.hypot(dx, dz) || 1; dx /= l; dz /= l;
    const nx = -dz, nz = dx;
    left.push([p[0] + nx * width / 2, p[1] + nz * width / 2]);
    right.push([p[0] - nx * width / 2, p[1] - nz * width / 2]);
  }
  const verts = [], idx = [];
  for (let i = 0; i < points.length; i++) {
    const [lx, lz] = left[i], [rx, rz] = right[i];
    verts.push(lx, groundY(lx, lz) + lift, lz, rx, groundY(rx, rz) + lift, rz);
    if (i > 0) { const a = 2 * (i - 1); idx.push(a, a + 2, a + 1, a + 1, a + 2, a + 3); }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(verts, 3));
  geo.setIndex(idx);
  geo.computeVertexNormals();
  const m = new THREE.Mesh(geo, material);
  m.receiveShadow = true;
  return m;
}

/** River stones along a line (the classic strip against a foundation). */
export function pebbleStrip({ from, to, width = 0.45, density = 12, seed = 5 }) {
  const rnd = seeded(seed);
  const dx = to[0] - from[0], dz = to[1] - from[1];
  const len = Math.hypot(dx, dz);
  const n = Math.max(4, Math.round(len * density));
  const geo = new THREE.IcosahedronGeometry(1, 1);
  const m = new THREE.MeshStandardMaterial({ color: "#b9b4a8", roughness: 0.95 });
  const inst = new THREE.InstancedMesh(geo, m, n);
  const d = new THREE.Object3D();
  for (let i = 0; i < n; i++) {
    const t = rnd(), s = 0.05 + rnd() * 0.07;
    const x = from[0] + dx * t + (rnd() - 0.5) * width, z = from[1] + dz * t + (rnd() - 0.5) * width;
    d.position.set(x, groundY(x, z) + s * 0.4, z);
    d.scale.set(s, s * 0.6, s * 0.8);
    d.rotation.set(rnd() * 3, rnd() * 3, rnd() * 3);
    d.updateMatrix();
    inst.setMatrixAt(i, d.matrix);
    inst.setColorAt(i, new THREE.Color().setHSL(0.1, 0.05 + rnd() * 0.05, 0.55 + rnd() * 0.25));
  }
  inst.castShadow = true;
  return inst;
}

// --------------------------------------------------------------------------
// Vegetation. Wood is a continuous, tapering, gently bending tube per trunk and branch (all
// of a plant's wood merged into one mesh); foliage is one instanced mesh of leaf cards with a
// leaf shape cut by an alpha texture drawn on a canvas at start (plain quads where there is
// no document, e.g. the Node tests). Deterministic by seed.
// --------------------------------------------------------------------------

/** Ring-swept tube along a curve, radius shrinking from r0 at the start to r1 at the end. */
function taperedTube(points, r0, r1, { sides = 7, segments } = {}) {
  const curve = new THREE.CatmullRomCurve3(points.map((p) => new THREE.Vector3(...p)), false, "centripetal");
  const n = segments ?? Math.max(4, Math.round(curve.getLength() / 0.3));
  const pts = curve.getSpacedPoints(n);
  const frames = curve.computeFrenetFrames(n, false);
  const rAt = (t) => r0 + (r1 - r0) * Math.pow(t, 0.85);
  const pos = [], idx = [];
  for (let i = 0; i <= n; i++) {
    const r = rAt(i / n), N = frames.normals[i], B = frames.binormals[i], p = pts[i];
    for (let j = 0; j < sides; j++) {
      const a = (j / sides) * Math.PI * 2, c = Math.cos(a), s = Math.sin(a);
      pos.push(p.x + (N.x * c + B.x * s) * r, p.y + (N.y * c + B.y * s) * r, p.z + (N.z * c + B.z * s) * r);
      if (i < n) {
        const k = i * sides + j, l = i * sides + ((j + 1) % sides);
        idx.push(k, l, k + sides, l, l + sides, k + sides); // outward-facing
      }
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  geo.setIndex(idx);
  geo.computeVertexNormals();
  return { geo, curve, rAt };
}

/** One geometry from several (position + normal + index), for one draw call per plant. */
function mergeGeometries(geos) {
  const pos = [], nor = [], idx = [];
  let offset = 0;
  for (const g of geos) {
    const p = g.attributes.position.array, n = g.attributes.normal.array, ix = g.index.array;
    for (let i = 0; i < p.length; i++) pos.push(p[i]);
    for (let i = 0; i < n.length; i++) nor.push(n[i]);
    for (let i = 0; i < ix.length; i++) idx.push(ix[i] + offset);
    offset += p.length / 3;
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  geo.setAttribute("normal", new THREE.Float32BufferAttribute(nor, 3));
  geo.setIndex(idx);
  return geo;
}

/** The control points of a branch: from `start` along `dir` for `len`, sagging a little in the middle and lifting at the tip, with some wobble. */
function bendPoints(start, dir, len, rnd, { droop = 0.12, lift = 0.22, wobble = 0.1, steps = 3 } = {}) {
  const pts = [];
  for (let i = 0; i <= steps; i++) {
    const t = i / steps, s = len * t;
    const dy = -droop * len * Math.sin(t * Math.PI) + lift * len * t * t;
    const w = i === 0 ? 0 : (rnd() - 0.5) * wobble * len;
    const w2 = i === 0 ? 0 : (rnd() - 0.5) * wobble * len;
    pts.push([start[0] + dir[0] * s + w, start[1] + dir[1] * s + dy, start[2] + dir[2] * s + w2]);
  }
  return pts;
}

const _leafTextures = new Map();

/** The alpha-cut leaf shape (a pointed leaf with a midrib, or a tuft of needles); null without a document. */
function leafTexture(kind) {
  if (typeof document === "undefined") return null;
  if (_leafTextures.has(kind)) return _leafTextures.get(kind);
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const ctx = c.getContext("2d");
  ctx.clearRect(0, 0, 64, 64);
  ctx.fillStyle = "#ffffff";
  ctx.strokeStyle = "#ffffff";
  ctx.lineCap = "round";
  if (kind === "needle") {
    ctx.lineWidth = 3.2;
    for (let i = 0; i < 9; i++) {
      const a = -Math.PI / 2 + (i - 4) * 0.26;
      ctx.beginPath();
      ctx.moveTo(32, 62);
      ctx.lineTo(32 + Math.cos(a) * 34, 62 + Math.sin(a) * 34);
      ctx.stroke();
    }
  } else {
    ctx.beginPath();
    ctx.moveTo(32, 2);
    ctx.quadraticCurveTo(64, 24, 32, 62);
    ctx.quadraticCurveTo(0, 24, 32, 2);
    ctx.fill();
    ctx.strokeStyle = "rgba(0,0,0,0.3)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(32, 6);
    ctx.lineTo(32, 58);
    ctx.stroke();
  }
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  _leafTextures.set(kind, tex);
  return tex;
}

function leafMaterial(kind) {
  return cached(`leaf:${kind}`, () => {
    const map = leafTexture(kind);
    const m = new THREE.MeshStandardMaterial({
      color: "#ffffff", // the hue is per instance
      roughness: 0.85,
      metalness: 0,
      side: THREE.DoubleSide,
      map: map ?? null,
      alphaTest: map ? 0.5 : 0,
    });
    m.emissive = new THREE.Color("#1a2a10"); // a little light through the leaf
    m.emissiveIntensity = 0.35;
    return m;
  });
}

/** Bark: rough, with a noise bump where a document exists (canvas), plain otherwise. */
function barkMaterial(color) {
  return cached(`bark:${color}`, () => {
    const m = new THREE.MeshStandardMaterial({ color, roughness: 1, metalness: 0 });
    if (typeof document !== "undefined") {
      const c = document.createElement("canvas");
      c.width = c.height = 128;
      const ctx = c.getContext("2d");
      const img = ctx.createImageData(128, 128);
      const rnd = seeded(11);
      for (let y = 0; y < 128; y++) {
        for (let x = 0; x < 128; x++) {
          const i = (y * 128 + x) * 4;
          // vertical streaks with grain
          const v = 128 + Math.sin(x * 0.9 + rnd() * 0.6) * 40 + (rnd() - 0.5) * 60;
          img.data[i] = img.data[i + 1] = img.data[i + 2] = Math.max(0, Math.min(255, v));
          img.data[i + 3] = 255;
        }
      }
      ctx.putImageData(img, 0, 0);
      const tex = new THREE.CanvasTexture(c);
      tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
      tex.repeat.set(2, 2);
      m.bumpMap = tex;
      m.bumpScale = 0.015;
    }
    return m;
  });
}

/** Leaf cards along a curve between `from` and `to` (0..1), `perMetre` of them, scattered within `radius`. */
function leavesAlong(list, curve, from, to, perMetre, radius, rnd) {
  const len = curve.getLength() * (to - from);
  const n = Math.max(0, Math.round(len * perMetre));
  for (let i = 0; i < n; i++) {
    const p = curve.getPointAt(from + (to - from) * rnd());
    const u = rnd() * Math.PI * 2, v = Math.acos(2 * rnd() - 1), r = radius * Math.cbrt(rnd());
    list.push([p.x + Math.cos(u) * Math.sin(v) * r, p.y + Math.cos(v) * r * 0.7, p.z + Math.sin(u) * Math.sin(v) * r]);
  }
}

/** One instanced mesh of leaf cards: random orientation, size and hue per leaf. */
function leafMesh(leaves, color, rnd, { kind = "leaf", width = 0.11, height = 0.075 } = {}) {
  const inst = new THREE.InstancedMesh(new THREE.PlaneGeometry(1, 1), leafMaterial(kind), leaves.length);
  const d = new THREE.Object3D();
  const base = new THREE.Color(color);
  const hsl = { h: 0, s: 0, l: 0 };
  base.getHSL(hsl);
  const tint = new THREE.Color();
  leaves.forEach((p, i) => {
    d.position.set(...p);
    const s = 0.75 + rnd() * 0.6;
    d.scale.set(width * s, height * s, 1);
    d.rotation.set((rnd() - 0.5) * 2.2, rnd() * Math.PI * 2, (rnd() - 0.5) * 1.2);
    d.updateMatrix();
    inst.setMatrixAt(i, d.matrix);
    tint.setHSL(hsl.h + (rnd() - 0.5) * 0.04, hsl.s * (0.85 + rnd() * 0.3), hsl.l * (0.7 + rnd() * 0.55));
    inst.setColorAt(i, tint);
  });
  inst.castShadow = true;
  inst.receiveShadow = true;
  inst.userData = { kind: "leaves" };
  return inst;
}

function broadleafWood(height, spread, kind, rnd, wood, leaves) {
  const columnar = kind === "columnar";
  const trunkH = height * (columnar ? 0.28 : 0.38);
  const rBase = 0.022 * height + 0.02, rTrunkTop = rBase * 0.55;
  const wob = 0.06 * height;
  const trunkPts = [[0, 0, 0], [(rnd() - 0.5) * wob * 0.5, trunkH * 0.5, (rnd() - 0.5) * wob * 0.5], [(rnd() - 0.5) * wob, trunkH, (rnd() - 0.5) * wob]];
  const trunk = taperedTube(trunkPts, rBase, rTrunkTop, { sides: 9 });
  wood.push(trunk.geo);
  // the leader: the trunk continuing into the crown
  const top = trunkPts[2];
  const leaderPts = [top, [top[0] + (rnd() - 0.5) * wob, (trunkH + height * 0.95) / 2, top[2] + (rnd() - 0.5) * wob], [top[0] + (rnd() - 0.5) * wob * 0.6, height * 0.95, top[2] + (rnd() - 0.5) * wob * 0.6]];
  const leader = taperedTube(leaderPts, rTrunkTop, 0.025, { sides: 8 });
  wood.push(leader.geo);
  const primaries = (columnar ? 9 : 6) + Math.floor(rnd() * 3);
  const reach = spread * 0.5;
  for (let k = 0; k < primaries; k++) {
    const t = 0.04 + (k / primaries) * 0.82 + rnd() * 0.05;
    const at = leader.curve.getPointAt(t);
    const az = k * 2.399 + (rnd() - 0.5) * 0.6;
    const el = (columnar ? 58 : 24 + rnd() * 30) * (Math.PI / 180);
    const dir = [Math.cos(az) * Math.cos(el), Math.sin(el), Math.sin(az) * Math.cos(el)];
    const len = Math.max(0.7, reach * (columnar ? 0.7 : 1) * (0.65 + rnd() * 0.5) * (1 - 0.35 * t));
    const r0 = Math.max(0.02, leader.rAt(t) * 0.55);
    const branch = taperedTube(bendPoints([at.x, at.y, at.z], dir, len, rnd), r0, 0.018, { sides: 6 });
    wood.push(branch.geo);
    leavesAlong(leaves, branch.curve, 0.45, 1, 40, 0.35, rnd);
    const twigs = 2 + Math.floor(rnd() * 2);
    for (let j = 0; j < twigs; j++) {
      const tt = 0.35 + rnd() * 0.45;
      const p = branch.curve.getPointAt(tt), tan = branch.curve.getTangentAt(tt);
      const side = (rnd() < 0.5 ? -1 : 1) * (0.6 + rnd() * 0.5);
      // rotate the tangent about y and tilt it up a little
      const c = Math.cos(side), s = Math.sin(side);
      const dx = tan.x * c - tan.z * s, dz = tan.x * s + tan.z * c;
      const tdir = [dx, Math.max(0.15, tan.y) + 0.25, dz];
      const tl = Math.hypot(...tdir);
      const twigLen = Math.max(0.4, len * 0.5 * (0.7 + rnd() * 0.5));
      const twig = taperedTube(bendPoints([p.x, p.y, p.z], [tdir[0] / tl, tdir[1] / tl, tdir[2] / tl], twigLen, rnd, { steps: 2 }), Math.max(0.012, branch.rAt(tt) * 0.6), 0.008, { sides: 5 });
      wood.push(twig.geo);
      leavesAlong(leaves, twig.curve, 0.15, 1, 85, 0.3, rnd);
      const tip = twig.curve.getPointAt(1);
      for (let q = 0; q < 26; q++) {
        const u = rnd() * Math.PI * 2, v = Math.acos(2 * rnd() - 1), r = 0.45 * Math.cbrt(rnd());
        leaves.push([tip.x + Math.cos(u) * Math.sin(v) * r, tip.y + Math.cos(v) * r * 0.8, tip.z + Math.sin(u) * Math.sin(v) * r]);
      }
    }
  }
  leavesAlong(leaves, leader.curve, 0.55, 1, 70, 0.55, rnd);
}

function pineWood(height, spread, rnd, wood, leaves) {
  const rBase = 0.02 * height + 0.03;
  const lean = 0.03 * height;
  const trunkPts = [[0, 0, 0], [(rnd() - 0.5) * lean, height * 0.35, (rnd() - 0.5) * lean], [(rnd() - 0.5) * lean, height * 0.7, (rnd() - 0.5) * lean], [(rnd() - 0.5) * lean * 0.5, height, (rnd() - 0.5) * lean * 0.5]];
  const trunk = taperedTube(trunkPts, rBase, 0.02, { sides: 9 });
  wood.push(trunk.geo);
  const y0 = height * 0.28, y1 = height * 0.92;
  const spacing = Math.max(0.45, height / 14);
  let whorl = 0;
  for (let y = y0; y <= y1; y += spacing, whorl++) {
    const t = y / height;
    const at = trunk.curve.getPointAt(t);
    const u = (y - y0) / (y1 - y0);
    const count = 5 + Math.floor(rnd() * 2);
    for (let k = 0; k < count; k++) {
      const az = (k / count) * Math.PI * 2 + whorl * 0.7 + (rnd() - 0.5) * 0.3;
      const el = (-8 + rnd() * 12) * (Math.PI / 180);
      const dir = [Math.cos(az) * Math.cos(el), Math.sin(el), Math.sin(az) * Math.cos(el)];
      const len = spread * 0.55 * (1 - 0.85 * u) + 0.25;
      const branch = taperedTube(bendPoints([at.x, at.y, at.z], dir, len, rnd, { droop: 0.08, lift: 0.1, wobble: 0.06, steps: 2 }), Math.max(0.015, trunk.rAt(t) * 0.45), 0.01, { sides: 5 });
      wood.push(branch.geo);
      leavesAlong(leaves, branch.curve, 0.15, 1, 75, 0.14, rnd);
    }
  }
  const tip = trunk.curve.getPointAt(1);
  for (let q = 0; q < 30; q++) leaves.push([tip.x + (rnd() - 0.5) * 0.3, tip.y - rnd() * 0.5, tip.z + (rnd() - 0.5) * 0.3]);
}

/**
 * A tree: a tapering, bending trunk and branches (one mesh) and instanced leaf cards (one
 * mesh). position=[x, z] (sits on the ground) or [x, y, z]. kind: "broadleaf" | "pine" |
 * "columnar". Deterministic by seed.
 */
export function leafTree({ position, height = 7, spread = 3.2, kind = "broadleaf", seed = 3, foliageColor, trunkColor = "#5b5142" }) {
  const rnd = seeded(seed);
  const [x, z] = position.length === 3 ? [position[0], position[2]] : position;
  const y = position.length === 3 ? position[1] : groundY(x, z);
  const g = new THREE.Group();
  g.position.set(x, y, z);
  const wood = [], leaves = [];
  if (kind === "pine") pineWood(height, spread, rnd, wood, leaves);
  else broadleafWood(height, spread, kind, rnd, wood, leaves);
  const bark = shadow(new THREE.Mesh(mergeGeometries(wood), barkMaterial(trunkColor)));
  bark.userData = { kind: "wood" };
  g.add(bark);
  if (kind === "pine") g.add(leafMesh(leaves, foliageColor ?? "#3f6238", rnd, { kind: "needle", width: 0.24, height: 0.16 }));
  else g.add(leafMesh(leaves, foliageColor ?? "#5a7d3c", rnd, { width: kind === "columnar" ? 0.09 : 0.11, height: kind === "columnar" ? 0.06 : 0.075 }));
  g.userData = { kind: "tree", height, spread };
  return g;
}

/** Bush: a dome of leaf cards over a few twigs; position=[x, z] or [x, y, z]. */
export function leafBush({ position, radius = 0.8, seed = 2, color = "#6a8a4a", stems = true }) {
  const rnd = seeded(seed);
  const [x, z] = position.length === 3 ? [position[0], position[2]] : position;
  const y = position.length === 3 ? position[1] : groundY(x, z);
  const g = new THREE.Group();
  g.position.set(x, y, z);
  const leaves = [];
  const n = Math.max(220, Math.round(1100 * radius * radius));
  for (let i = 0; i < n; i++) {
    const a = rnd() * Math.PI * 2, cy = 2 * rnd() - 1, rr = radius * (0.5 + 0.5 * Math.cbrt(rnd())), ss = Math.sqrt(1 - cy * cy);
    leaves.push([Math.cos(a) * ss * rr, radius * 0.6 + cy * rr * 0.65, Math.sin(a) * ss * rr]);
  }
  if (stems) {
    const twigs = [];
    for (let i = 0; i < 5; i++) {
      const a = i * 2.4 + rnd() * 0.5;
      twigs.push(taperedTube([[0, 0, 0], [Math.cos(a) * radius * 0.25, radius * 0.45, Math.sin(a) * radius * 0.25], [Math.cos(a) * radius * 0.55, radius * 0.85, Math.sin(a) * radius * 0.55]], 0.014, 0.006, { sides: 5, segments: 4 }).geo);
    }
    const stem = shadow(new THREE.Mesh(mergeGeometries(twigs), barkMaterial("#6a6455")), true, false);
    stem.userData = { kind: "wood" };
    g.add(stem);
  }
  g.add(leafMesh(leaves, color, rnd, { width: 0.075 * (0.7 + radius * 0.4), height: 0.05 * (0.7 + radius * 0.4) }));
  g.userData = { kind: "bush", radius };
  return g;
}

// --------------------------------------------------------------------------
// Props
// --------------------------------------------------------------------------

/** Garden swing set (A-frames + beam + one seat). position=[x, z] (on the ground) or [x, y, z]. */
export function swingSet({ position, rotationY = 0, width = 2.6, height = 2.3, color = "#7a6045" }) {
  const g = new THREE.Group();
  const [x, z] = position.length === 3 ? [position[0], position[2]] : position;
  g.position.set(x, position.length === 3 ? position[1] : groundY(x, z), z);
  g.rotation.y = rotationY;
  const wood = mat.wood(color);
  for (const s of [-1, 1]) {
    g.add(rod([s * width / 2, 0, -0.7], [s * width / 2, height, 0], 0.05, wood));
    g.add(rod([s * width / 2, 0, 0.7], [s * width / 2, height, 0], 0.05, wood));
  }
  g.add(rod([-width / 2 - 0.1, height, 0], [width / 2 + 0.1, height, 0], 0.055, wood));
  const rope = mat.metal("#3a3a3a");
  g.add(rod([-0.25, height, 0], [-0.25, 0.5, 0], 0.008, rope));
  g.add(rod([0.25, height, 0], [0.25, 0.5, 0], 0.008, rope));
  g.add(box({ size: [0.55, 0.04, 0.2], position: [0, 0.5, 0], material: mat.paint("#c0392b") }));
  g.userData = { kind: "swingSet" };
  return g;
}

/** Garden bench. position=[x, z] (on the ground) or [x, y, z]. */
export function bench({ position, rotationY = 0, color = "#7a6a52" }) {
  const g = new THREE.Group();
  const [x, z] = position.length === 3 ? [position[0], position[2]] : position;
  g.position.set(x, position.length === 3 ? position[1] : groundY(x, z), z);
  g.rotation.y = rotationY;
  const wood = mat.wood(color), metal = mat.metal("#3a3d40");
  g.add(box({ size: [1.6, 0.05, 0.45], position: [0, 0.45, 0], material: wood }));
  g.add(box({ size: [1.6, 0.4, 0.05], position: [0, 0.75, -0.2], material: wood }));
  for (const s of [-0.7, 0.7]) {
    g.add(box({ size: [0.05, 0.45, 0.05], position: [s, 0.225, 0.18], material: metal }));
    g.add(box({ size: [0.05, 0.95, 0.05], position: [s, 0.475, -0.18], material: metal }));
  }
  g.userData = { kind: "bench" };
  return g;
}

/** Bicycle leaning at position=[x, z] (on the ground) or [x, y, z]. */
export function bicycle({ position, rotationY = 0, color = "#323a36" }) {
  const g = new THREE.Group();
  const [x, z] = position.length === 3 ? [position[0], position[2]] : position;
  g.position.set(x, position.length === 3 ? position[1] : groundY(x, z), z);
  g.rotation.y = rotationY;
  const frame = mat.metal(color), tire = mat.paint("#272c28");
  for (const wx of [-0.52, 0.52]) {
    const wheel = new THREE.Mesh(new THREE.TorusGeometry(0.33, 0.025, 8, 32), tire);
    wheel.position.set(wx, 0.35, 0);
    g.add(wheel);
    for (let i = 0; i < 12; i++) { const a = i * Math.PI / 6; g.add(rod([wx, 0.35, 0], [wx + Math.cos(a) * 0.31, 0.35 + Math.sin(a) * 0.31, 0], 0.003, frame)); }
  }
  for (const [a, b] of [[[-0.52, 0.35, 0], [-0.12, 0.43, 0]], [[-0.12, 0.43, 0], [-0.28, 0.86, 0]], [[-0.28, 0.86, 0], [-0.52, 0.35, 0]], [[-0.28, 0.86, 0], [0.34, 0.88, 0]], [[0.34, 0.88, 0], [-0.12, 0.43, 0]], [[0.34, 0.88, 0], [0.52, 0.35, 0]]]) g.add(rod(a, b, 0.02, frame));
  g.add(rod([0.34, 0.88, 0], [0.28, 1.01, 0], 0.019, frame));
  g.add(rod([0.28, 1.01, -0.2], [0.28, 1.01, 0.2], 0.018, frame));
  g.add(box({ size: [0.25, 0.06, 0.14], position: [-0.28, 0.9, 0], material: tire }));
  g.userData = { kind: "bicycle" };
  return g;
}

/** Convenience: bounding box of an object (world space). */
export function boundsOf(obj) {
  obj.updateMatrixWorld(true);
  return new THREE.Box3().setFromObject(obj);
}

// --------------------------------------------------------------------------
// Plausibility audit (deterministic, from bounding boxes; no rendering, no model)
// --------------------------------------------------------------------------

// what the audit treats as a movable/planted object: the kit's props and vegetation, plus
// anything the scene tags itself (userData.kind = "prop", or a name like "trampoline")
const AUDIT_PROP_KINDS = new Set(["tree", "bush", "hedge", "planter", "swingSet", "bench", "bicycle", "car", "prop"]);
const AUDIT_WALL_KINDS = new Set(["wall", "building"]);

function fmt(n) { return (Math.round(n * 10) / 10).toString(); }

/** The solid part of an object for clash tests: a tree is its trunk column, not its canopy. */
function auditSolid(o, box) {
  const kind = o.userData.kind;
  if (kind === "tree") {
    const p = new THREE.Vector3();
    o.getWorldPosition(p);
    return new THREE.Box3(new THREE.Vector3(p.x - 0.35, box.min.y, p.z - 0.35), new THREE.Vector3(p.x + 0.35, box.max.y, p.z + 0.35));
  }
  if (kind === "bush") {
    const c = box.getCenter(new THREE.Vector3()), sz = box.getSize(new THREE.Vector3());
    return new THREE.Box3().setFromCenterAndSize(c, new THREE.Vector3(sz.x * 0.6, sz.y, sz.z * 0.6));
  }
  return box;
}

function auditLabel(o, box) {
  const c = box.getCenter(new THREE.Vector3());
  const kind = o.userData.kind === "prop" ? (o.name || "prop") : o.userData.kind;
  return `${kind} at (${fmt(c.x)}, ${fmt(c.z)})`;
}

/**
 * Walk a scene group and report what a glance at the aerial view would call impossible:
 * props/vegetation intersecting each other or a wall, props floating above or sunk into the
 * ground, ground-storey walls floating above the terrain, objects far from the house, and
 * openings of impossible size. Returns a list of short strings (empty = nothing found).
 * Objects are recognised by `userData.kind` (the kit sets it; tag raw three.js props with
 * userData.kind = "prop" and a name). `heightAt(x, z)` defaults to the registered terrain.
 */
export function audit(root, { heightAt = groundY, maxLines = 20 } = {}) {
  root.updateMatrixWorld(true);
  const props = [], walls = [], openings = [];
  root.traverse((o) => {
    const kind = o.userData?.kind;
    if (!kind) return;
    if (AUDIT_PROP_KINDS.has(kind)) {
      // a tagged object inside a tagged object (a bench in a "prop" group) counts once: the outer
      let p = o.parent, nested = false;
      while (p && p !== root) { if (AUDIT_PROP_KINDS.has(p.userData?.kind)) { nested = true; break; } p = p.parent; }
      if (!nested) props.push(o);
    } else if (AUDIT_WALL_KINDS.has(kind)) walls.push(o);
    else if (kind === "window" || kind === "door") openings.push(o);
  });
  const lines = [];
  const boxOf = (o) => new THREE.Box3().setFromObject(o);
  const items = props.map((o) => { const box = boxOf(o); return { o, box, solid: auditSolid(o, box), label: auditLabel(o, box) }; })
    .filter((it) => !it.box.isEmpty());
  const wallItems = walls.map((o) => { const box = boxOf(o); return { o, box, solid: box, label: `${o.userData.kind} at (${fmt((box.min.x + box.max.x) / 2)}, ${fmt((box.min.z + box.max.z) / 2)})` }; })
    .filter((it) => !it.box.isEmpty());

  // 1. clashes: the horizontal overlap covers most of the smaller footprint and the heights
  //    overlap; for a thin (axis-aligned) wall: the object passes through its thickness
  const overlap = (a, b) => ({
    ix: Math.min(a.max.x, b.max.x) - Math.max(a.min.x, b.min.x),
    iz: Math.min(a.max.z, b.max.z) - Math.max(a.min.z, b.min.z),
    iy: Math.min(a.max.y, b.max.y) - Math.max(a.min.y, b.min.y),
  });
  const clash = (a, b) => {
    const { ix, iz, iy } = overlap(a, b);
    if (ix <= 0 || iz <= 0 || iy < 0.2) return false;
    const areaA = (a.max.x - a.min.x) * (a.max.z - a.min.z), areaB = (b.max.x - b.min.x) * (b.max.z - b.min.z);
    return ix * iz >= 0.5 * Math.min(areaA, areaB);
  };
  const wallClash = (prop, wall) => {
    const sx = wall.max.x - wall.min.x, sz = wall.max.z - wall.min.z;
    const thickness = Math.min(sx, sz);
    if (thickness > 1.0) return clash(prop, wall); // a mass or a diagonal wall: footprint rule
    const { ix, iz, iy } = overlap(prop, wall);
    if (ix <= 0 || iz <= 0 || iy < 0.2) return false;
    const across = sx < sz ? ix : iz, along = sx < sz ? iz : ix;
    return across >= 0.8 * thickness && along >= 0.5;
  };
  for (let i = 0; i < items.length; i++) {
    for (let j = i + 1; j < items.length; j++) {
      const a = items[i], b = items[j];
      const ka = a.o.userData.kind, kb = b.o.userData.kind;
      const veg = (k) => k === "tree" || k === "bush" || k === "hedge";
      if (veg(ka) && veg(kb)) continue; // planting overlaps naturally
      if (clash(a.solid, b.solid)) lines.push(`${a.label} intersects ${b.label}`);
    }
    for (const w of wallItems) {
      if (wallClash(items[i].solid, w.solid)) lines.push(`${items[i].label} intersects the ${w.label}`);
    }
  }

  // 2. floating / sunk props (their base against the ground under their centre)
  for (const it of items) {
    const c = it.box.getCenter(new THREE.Vector3());
    const g = heightAt(c.x, c.z);
    const gap = it.box.min.y - g;
    if (gap > 0.15) lines.push(`${it.label} floats ${fmt(gap)} m above the ground`);
    else if (gap < -0.5) lines.push(`${it.label} is sunk ${fmt(-gap)} m into the ground`);
  }

  // 3. ground-storey walls floating above the terrain (no plinth / basement under them)
  if (wallItems.length) {
    const lowest = Math.min(...wallItems.map((w) => w.box.min.y));
    for (const w of wallItems) {
      if (w.box.min.y > lowest + 0.5) continue; // upper storeys sit on the storey below
      const cx = (w.box.min.x + w.box.max.x) / 2, cz = (w.box.min.z + w.box.max.z) / 2;
      const gap = w.box.min.y - heightAt(cx, cz);
      if (gap > 0.3) lines.push(`${w.label} floats ${fmt(gap)} m above the terrain: add a plinth or lower the terrain`);
    }
  }

  // 4. far from the house (a typo in a coordinate)
  if (wallItems.length) {
    const site = new THREE.Box3();
    for (const w of wallItems) site.union(w.box);
    const c = site.getCenter(new THREE.Vector3());
    for (const it of items) {
      const p = it.box.getCenter(new THREE.Vector3());
      const d = Math.hypot(p.x - c.x, p.z - c.z);
      if (d > 60) lines.push(`${it.label} is ${fmt(d)} m from the house: outside the plot?`);
    }
  }

  // 5. impossible scale
  for (const o of openings) {
    const { kind, width, height } = o.userData;
    if (kind === "door" && (height < 1.7 || height > 3.2)) lines.push(`a door is ${fmt(height)} m tall`);
    if (kind === "window" && (height > 4 || width > 8)) lines.push(`a window is ${fmt(width)} x ${fmt(height)} m`);
  }
  for (const it of items) {
    const sz = it.box.getSize(new THREE.Vector3());
    const k = it.o.userData.kind;
    if (k === "tree" && sz.y > 30) lines.push(`${it.label} is ${fmt(sz.y)} m tall`);
    if (k === "car" && Math.max(sz.x, sz.z) > 7) lines.push(`${it.label} is ${fmt(Math.max(sz.x, sz.z))} m long`);
  }
  return lines.slice(0, maxLines);
}

export default {
  mat, box, slab, volume, wall, wallWithUnits, perimeterWalls, placeOnWall, UNIT_INSET, windowUnit, door, slidingDoor,
  flatRoof, gableRoof, shedRoof, chimney, railing, stairs, balcony, canopy, planter,
  hedge, pathway, groundPatch, gardenWall, fence, car, boundsOf, audit,
  terrain, groundY, rod, ribbon, pebbleStrip, leafTree, leafBush, swingSet, bench, bicycle,
};
