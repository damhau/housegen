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
export function planter({ length = 1.5, position = [0, 0, 0], rotationY = 0, color = "#5d6b62" }) {
  const g = new THREE.Group();
  g.add(box({ size: [length, 0.45, 0.4], position: [0, 0.225, 0], material: mat.paint(color) }));
  const n = Math.max(2, Math.round(length / 0.35));
  for (let i = 0; i < n; i++) {
    const s = new THREE.Mesh(new THREE.SphereGeometry(0.22 + Math.random() * 0.08, 8, 6), mat.foliage("#6a9a48"));
    s.position.set(-length / 2 + (i + 0.5) * (length / n), 0.6, (Math.random() - 0.5) * 0.15);
    s.scale.y = 1.3;
    shadow(s, true, false);
    g.add(s);
  }
  g.position.set(...position);
  g.rotation.y = rotationY;
  return g;
}

// --------------------------------------------------------------------------
// Landscape
// --------------------------------------------------------------------------

function seeded(seed) {
  let s = seed >>> 0 || 1;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
}

/** Tree. kind: "round" | "conifer" | "willow". position=[x,y,z] at trunk base. */
export function tree({ position = [0, 0, 0], height = 6, kind = "round", seed = 1, foliageColor }) {
  const g = new THREE.Group();
  const rnd = seeded(seed);
  const trunkH = height * (kind === "conifer" ? 0.25 : 0.4);
  const trunk = shadow(new THREE.Mesh(new THREE.CylinderGeometry(height * 0.03, height * 0.05, trunkH, 7), mat.wood("#6b4f34")));
  trunk.position.y = trunkH / 2;
  g.add(trunk);
  const leaf = mat.foliage(foliageColor ?? (kind === "conifer" ? "#3f6b3a" : "#5f8a3f"));
  if (kind === "conifer") {
    const tiers = 4;
    for (let i = 0; i < tiers; i++) {
      const r = height * 0.28 * (1 - i / tiers) + 0.3;
      const h = height * 0.3;
      const cone = shadow(new THREE.Mesh(new THREE.ConeGeometry(r, h, 8), leaf));
      cone.position.y = trunkH + i * h * 0.55 + h / 2;
      g.add(cone);
    }
  } else {
    const blobs = kind === "willow" ? 9 : 6;
    for (let i = 0; i < blobs; i++) {
      const r = height * (0.16 + rnd() * 0.12);
      const s = shadow(new THREE.Mesh(new THREE.IcosahedronGeometry(r, 1), leaf));
      s.position.set((rnd() - 0.5) * height * 0.45, trunkH + height * 0.25 + (rnd() - 0.2) * height * 0.35, (rnd() - 0.5) * height * 0.45);
      if (kind === "willow") s.scale.y = 1.6;
      g.add(s);
    }
  }
  g.position.set(...position);
  return g;
}

/** Hedge along a line. */
export function hedge({ from, to, height = 1.2, thickness = 0.6, y = 0, color = "#4f7a3a" }) {
  const dx = to[0] - from[0], dz = to[1] - from[1];
  const length = Math.hypot(dx, dz);
  const g = new THREE.Group();
  const m = mat.foliage(color);
  const segs = Math.max(1, Math.round(length / 0.8));
  for (let i = 0; i < segs; i++) {
    const b = shadow(new THREE.Mesh(new THREE.BoxGeometry(length / segs + 0.05, height, thickness), m));
    b.position.set((i + 0.5) * (length / segs), height / 2, 0);
    b.rotation.z = (Math.random() - 0.5) * 0.04;
    g.add(b);
  }
  g.position.set(from[0], y, from[1]);
  g.rotation.y = -Math.atan2(dz, dx);
  return g;
}

/** A single bush. */
export function bush({ position = [0, 0, 0], radius = 0.6, color = "#5b8a45", seed = 3 }) {
  const g = new THREE.Group();
  const rnd = seeded(seed);
  for (let i = 0; i < 4; i++) {
    const s = shadow(new THREE.Mesh(new THREE.IcosahedronGeometry(radius * (0.6 + rnd() * 0.5), 1), mat.foliage(color)));
    s.position.set((rnd() - 0.5) * radius, radius * 0.6 + (rnd() - 0.5) * radius * 0.3, (rnd() - 0.5) * radius);
    g.add(s);
  }
  g.position.set(...position);
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
  return g;
}

/** Convenience: bounding box of an object (world space). */
export function boundsOf(obj) {
  obj.updateMatrixWorld(true);
  return new THREE.Box3().setFromObject(obj);
}

export default {
  mat, box, slab, volume, wall, wallWithUnits, perimeterWalls, placeOnWall, UNIT_INSET, windowUnit, door, slidingDoor,
  flatRoof, gableRoof, shedRoof, chimney, railing, stairs, balcony, canopy, planter,
  tree, hedge, bush, pathway, groundPatch, gardenWall, fence, car, boundsOf,
};
