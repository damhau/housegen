// housekit/plan2d — a storey of the built scene as a simplified 2D plan (SVG): rooms filled by use,
// walls black, doors with their swing, windows as double lines, furniture as plan symbols, the
// name and area of every room, a north arrow, a scale bar and the total per flat. Nothing is
// asked of the scene code: everything is read from what the kit tagged (floorPlan rooms and
// partitions, exterior walls, window/door units, slabs and balconies, furniture pieces).
//
//   planStoreys(root)                        [{ index, y, label }] lowest first
//   planData(root, index)                    the storey's plan in world metres (+x east, +z south)
//   planSVG(data, { furnished = true })      the drawing; furnished: false keeps the fittings only
//
// The runtime answers the app's `house:plan2d` message with it (the viewer's Plan dialog).

import * as THREE from "three";

// colour by use: pale fills, one family per kind of space
const FILL = {
  living: "#f7e6bf", dining: "#f7e6bf", "kitchen-living": "#f7e6bf", kitchen: "#f5dcc2",
  bedroom: "#d6e2f1", office: "#e1dcef", bath: "#cbe8e4", wc: "#cbe8e4",
  hall: "#e7e5df", stair: "#e7e5df", storage: "#ecebe6", outdoor: "#efeee9",
};
const INK = "#262626";
const FURN = { stroke: "#8f8c85", fill: "#ffffff", w: 0.022 };
const LABEL = { name: "#23282b", area: "#4f565b" };
const FITTINGS = new Set(["kitchen", "wc", "basin", "bath", "shower"]);
const FONT = "'Archivo', 'Helvetica Neue', Arial, sans-serif";

const f = (v) => +v.toFixed(3);
const pts = (poly) => poly.map(([x, z]) => `${f(x)},${f(z)}`).join(" ");
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");

// --------------------------------------------------------------------------
// Reading the scene
// --------------------------------------------------------------------------

function floorPlans(root) {
  const out = [];
  root.traverse((o) => { if (o.userData?.kind === "floorPlan") out.push(o); });
  return out.sort((a, b) => a.userData.y - b.userData.y);
}

const FR = /chambre|séjour|salon|cuisine|sdb|salle|cave|circ|escalier|buanderie|bureau|rangement|combles/i;
function french(root) {
  return floorPlans(root).some((p) => p.userData.rooms.some((r) => FR.test(r.name)));
}

/** The storeys that have a floor plan, named from the one nearest the ground. */
export function planStoreys(root) {
  const plans = floorPlans(root);
  if (!plans.length) return [];
  const fr = french(root);
  let ground = 0;
  plans.forEach((p, i) => { if (Math.abs(p.userData.y) < Math.abs(plans[ground].userData.y)) ground = i; });
  return plans.map((p, i) => ({ index: i, y: p.userData.y, label: storeyName(i - ground, fr) }));
}

function storeyName(n, fr) {
  if (fr) return n < 0 ? (n === -1 ? "Sous-sol" : `Sous-sol ${-n}`) : n === 0 ? "Rez-de-chaussée" : n === 1 ? "1er étage" : `${n}e étage`;
  const ord = ["Ground floor", "First floor", "Second floor", "Third floor", "Fourth floor"];
  return n < 0 ? (n === -1 ? "Basement" : `Basement ${-n}`) : ord[n] ?? `Floor ${n}`;
}

const V = new THREE.Vector3();
const toWorld = (obj, x, y, z) => V.set(x, y, z).applyMatrix4(obj.matrixWorld).clone();
const parentWorld = (o, [x, z], y) => {
  const p = o.parent ? toWorld(o.parent, x, y, z) : new THREE.Vector3(x, y, z);
  return [p.x, p.z];
};

/** Rotation about +y and the horizontal scale of an object in world space. */
function frame(o) {
  const e = o.matrixWorld.elements;
  return {
    x: e[12], y: e[13], z: e[14],
    rot: Math.atan2(-e[2], e[0]),
    sx: Math.hypot(e[0], e[1], e[2]), sz: Math.hypot(e[8], e[9], e[10]),
  };
}

function insidePoly([x, z], poly) {
  let c = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, zi] = poly[i], [xj, zj] = poly[j];
    if ((zi > z) !== (zj > z) && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) c = !c;
  }
  return c;
}

function polyArea(poly) {
  let a = 0;
  for (let i = 0; i < poly.length; i++) {
    const [x1, z1] = poly[i], [x2, z2] = poly[(i + 1) % poly.length];
    a += x1 * z2 - x2 * z1;
  }
  return Math.abs(a) / 2;
}

function bboxOf(points, box = [Infinity, Infinity, -Infinity, -Infinity]) {
  for (const [x, z] of points) {
    box[0] = Math.min(box[0], x); box[1] = Math.min(box[1], z);
    box[2] = Math.max(box[2], x); box[3] = Math.max(box[3], z);
  }
  return box;
}

const overlaps = (a, b, pad = 0) => a[0] < b[2] + pad && a[2] > b[0] - pad && a[1] < b[3] + pad && a[3] > b[1] - pad;

/** What a furniture piece is drawn as, from its catalogue or scene name. */
function symbolOf(name = "") {
  const n = name.toLowerCase();
  if (/lamp|light|pendant|lustre|luminaire|chandelier|sconce/.test(n)) return null;
  if (/rug|carpet|tapis/.test(n)) return "rug";
  if (/^bed|bed-|\bbed\b|lit\b/.test(n) && !/bedside|nightstand/.test(n)) return "bed";
  if (/sofa|couch|canap/.test(n)) return "sofa";
  if (/armchair|fauteuil/.test(n)) return "armchair";
  if (/diningset/.test(n)) return "dining";
  if (/kitchen|island|cuisine|îlot/.test(n)) return "kitchen";
  if (/\bwc\b|^wc|toilet/.test(n)) return "wc";
  if (/basin|vanity|washbasin|lavabo|sink/.test(n)) return "basin";
  if (/bathtub|\bbath\b|baignoire/.test(n)) return "bath";
  if (/shower|douche/.test(n)) return "shower";
  if (/plant|tree|ficus|palm/.test(n)) return "plant";
  if (/chair|stool|chaise|tabouret/.test(n)) return "chair";
  if (/coffee|side-table|side table|low table/.test(n)) return "coffee";
  if (/wardrobe|closet|dressing|cupboard|armoire|placard|cabinet|storage|shelf|shelv|bookcase/.test(n)) return "cabinet";
  if (/nightstand|bedside|chevet/.test(n)) return "box";
  if (/desk|table|bureau/.test(n)) return "table";
  return "box";
}

/** The kit's exterior walls standing on the floor at `y` (1 per wall, however it was grouped). */
function wallsAt(root, y, near) {
  const walls = [], seen = new Set();
  root.traverse((o) => {
    const u = o.userData;
    if (u?.kind !== "wall" || !u.from || !u.to) return;
    // standing on this floor (an attic's knee walls are low), not the storey below's
    if (!(u.y <= y + 0.3 && u.y + u.height >= y + 0.5)) return;
    const from = parentWorld(o, u.from, u.y), to = parentWorld(o, u.to, u.y);
    const key = [...from, ...to, u.thickness].map((v) => v.toFixed(2)).join(",");
    if (seen.has(key) || !overlaps(bboxOf([from, to]), near)) return;
    seen.add(key);
    walls.push({ from, to, thickness: u.thickness, openings: [] });
  });
  return walls;
}

/**
 * Storey `index` of the scene, in world metres: rooms, partitions (with their doors), exterior walls
 * (with their windows and doors), outdoor slabs and balconies at this level, furniture.
 */
export function planData(root, index) {
  root.updateMatrixWorld(true);
  const plans = floorPlans(root);
  const plan = plans[index];
  if (!plan) return null;
  const { y, height = 2.5 } = plan.userData;
  const storey = planStoreys(root)[index];
  const fr = french(root);
  const rooms = plan.userData.rooms.map((r) => ({ ...r, polygon: r.polygon.map((p) => parentWorld(plan, p, y)) }));
  const partitions = plan.userData.partitions.map((p) => ({ ...p, from: parentWorld(plan, p.from, y), to: parentWorld(plan, p.to, y) }));
  const roomBox = bboxOf(rooms.flatMap((r) => r.polygon));
  const near = [roomBox[0] - 1.2, roomBox[1] - 1.2, roomBox[2] + 1.2, roomBox[3] + 1.2];

  // exterior walls standing on this storey, near its rooms; a storey whose shell the scene built
  // without the kit's walls (an attic of gables and boxes) takes the outline of the one below
  let walls = wallsAt(root, y, near);
  for (let below = index - 1; !walls.length && below >= 0; below--) {
    walls = wallsAt(root, plans[below].userData.y, near);
  }
  const ownWalls = wallsAt(root, y, near).length > 0;

  // windows and doors of this storey, on the wall they sit in
  if (ownWalls) root.traverse((o) => {
    const k = o.userData?.kind;
    if (k !== "window" && k !== "door") return;
    const fr0 = frame(o);
    const sill = fr0.y - y;
    if (sill < -0.1 || sill > 2.0) return;
    let best = null;
    for (const w of walls) {
      const [ax, az] = w.from, [bx, bz] = w.to, len = Math.hypot(bx - ax, bz - az);
      const ux = (bx - ax) / len, uz = (bz - az) / len;
      const s = (fr0.x - ax) * ux + (fr0.z - az) * uz;
      const d = Math.abs((fr0.x - ax) * -uz + (fr0.z - az) * ux);
      if (s < -0.05 || s > len + 0.05 || d > w.thickness + 0.3) continue;
      if (!best || d < best.d) best = { w, s, d };
    }
    if (best) best.w.openings.push({ at: best.s, width: o.userData.width ?? 0.9, door: k === "door" || sill < 0.3 });
  });

  // terraces and balconies at this level: slabs and balconies outside the rooms
  const footprint = walls.length ? bboxOf(walls.flatMap((w) => [w.from, w.to])) : roomBox;
  const outdoor = [];
  const addOutdoor = (poly) => {
    const box = bboxOf(poly);
    const c = [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2];
    const area = polyArea(poly);
    if (area < 1 || area > 150 || !overlaps(box, footprint, 3)) return;
    // the storey's own floor slab (inside the walls) is not a terrace
    if (box[0] >= footprint[0] - 0.3 && box[2] <= footprint[2] + 0.3 && box[1] >= footprint[1] - 0.3 && box[3] <= footprint[3] + 0.3) return;
    outdoor.push({ polygon: poly, area });
  };
  root.traverse((o) => {
    const u = o.userData;
    // flags rather than kind: scene code sometimes retags its slabs and balconies
    if ((u?.slab || u?.kind === "slab") && Math.abs(u.y - y) < 0.4 && Array.isArray(u.polygon)) {
      addOutdoor(u.polygon.map((p) => parentWorld(o, p, u.y)));
    } else if (u?.balcony || u?.kind === "balcony") {
      const fr0 = frame(o);
      if (Math.abs(fr0.y - y) > 0.4) return;
      const hw = u.width / 2;
      addOutdoor([[-hw, 0], [hw, 0], [hw, u.depth], [-hw, u.depth]].map(([x, z]) => { const p = toWorld(o, x, 0, z); return [p.x, p.z]; }));
    }
  });

  // furniture standing on this storey (not the pieces inside another piece, not hanging lamps)
  const furniture = [];
  root.traverse((o) => {
    const u = o.userData;
    if (u?.kind !== "furniture" || u.hang) return;
    for (let p = o.parent; p; p = p.parent) if (p.userData?.kind === "furniture") return;
    const fr0 = frame(o);
    if (fr0.y < y - 0.2 || fr0.y > y + Math.min(1.5, height)) return;
    const type = symbolOf(u.name);
    const [w, d] = u.footprint ?? [0, 0];
    if (!type || w * fr0.sx < 0.05 || d * fr0.sz < 0.05) return;
    const inRoom = rooms.some((r) => insidePoly([fr0.x, fr0.z], r.polygon));
    if (!inRoom && !overlaps([fr0.x, fr0.z, fr0.x, fr0.z], footprint, 0.5)) return;
    furniture.push({ type, name: u.name, x: fr0.x, z: fr0.z, rot: fr0.rot, w: w * fr0.sx, d: d * fr0.sz, sink: u.sink, hob: u.hob });
  });

  return { index, y, label: storey.label, french: fr, rooms, partitions, walls, outdoor, furniture };
}

// --------------------------------------------------------------------------
// Drawing
// --------------------------------------------------------------------------

/** A plan symbol in the piece's own frame: centred on the origin, facing +z. */
function symbol(p) {
  const { type, w, d } = p;
  const s = `stroke="${FURN.stroke}" stroke-width="${FURN.w}" fill="${FURN.fill}"`;
  const l = `stroke="${FURN.stroke}" stroke-width="${FURN.w}" fill="none"`;
  const hw = w / 2, hd = d / 2;
  const box = (x, z, bw, bd, rx = 0.02, a = s) => `<rect x="${f(x)}" y="${f(z)}" width="${f(Math.max(bw, 0.01))}" height="${f(Math.max(bd, 0.01))}" rx="${rx}" ${a}/>`;
  switch (type) {
    case "bed": {
      const two = w > 1.3, pw = two ? w / 2 - 0.13 : Math.max(0.3, w - 0.2);
      const pillows = two
        ? box(-hw + 0.09, -hd + 0.11, pw, 0.34, 0.07) + box(0.04, -hd + 0.11, pw, 0.34, 0.07)
        : box(-pw / 2, -hd + 0.11, pw, 0.34, 0.07);
      const fold = -hd + Math.min(0.6, d * 0.3);
      return box(-hw, -hd, w, d, 0.04) + box(-hw, -hd, w, 0.07, 0.01) + pillows
        + `<path d="M${f(-hw)} ${f(fold)}H${f(hw)}M${f(-hw)} ${f(fold + 0.14)}H${f(hw)}" ${l}/>`
        + `<path d="M${f(hw - 0.35)} ${f(hd)}L${f(hw)} ${f(hd - 0.35)}" ${l}/>`;
    }
    case "sofa":
    case "armchair": {
      const arm = Math.min(type === "sofa" ? 0.17 : 0.13, w / 5), back = Math.min(0.2, d / 3);
      let out = box(-hw, -hd, w, d, 0.07) + box(-hw, -hd, w, back, 0.05) + box(-hw, -hd, arm, d, 0.05) + box(hw - arm, -hd, arm, d, 0.05);
      if (type === "sofa" && d < 1.3) {
        const inner = w - 2 * arm, n = inner > 1.5 ? 3 : 2;
        for (let i = 1; i < n; i++) out += `<path d="M${f(-hw + arm + (inner * i) / n)} ${f(-hd + back)}V${f(hd - 0.04)}" ${l}/>`;
      }
      return out;
    }
    case "chair": {
      const cw = Math.min(w, 0.46), cd = Math.min(d, 0.5);
      return box(-cw / 2 + 0.03, -cd / 2 + 0.06, cw - 0.06, cd - 0.08, 0.05) + box(-cw / 2 + 0.03, -cd / 2, cw - 0.06, 0.07, 0.02);
    }
    case "dining": {
      // the set: a table (footprint minus the chairs around it) and chairs down both long sides
      const tw = Math.max(0.7, w - 1.0), tl = Math.max(0.8, d - 0.2);
      let out = "";
      const n = Math.max(1, Math.round(tl / 0.62));
      for (let i = 0; i < n; i++) {
        const z = -tl / 2 + (tl / n) * (i + 0.5);
        for (const side of [-1, 1]) {
          const cx = side * (tw / 2 + 0.16);
          out += `<g transform="translate(${f(cx)} ${f(z)}) rotate(${side * 90})">${symbol({ type: "chair", w: 0.46, d: 0.5 })}</g>`;
        }
      }
      return out + box(-tw / 2, -tl / 2, tw, tl, 0.03);
    }
    case "table": return box(-hw, -hd, w, d, 0.03);
    case "coffee": return box(-hw, -hd, w, d, 0.05) + (w > 0.3 && d > 0.3 ? box(-hw + 0.06, -hd + 0.06, w - 0.12, d - 0.12, 0.03, l) : "");
    case "cabinet": return box(-hw, -hd, w, d, 0.01)
      + (d > 0.45 ? `<path d="M${f(-hw + 0.05)} ${f(-0.02)}H${f(hw - 0.05)}" ${l} stroke-dasharray="0.06 0.05"/>` : "")
      + (w > 0.9 ? `<path d="M0 ${f(-hd)}V${f(hd)}" ${l}/>` : "");
    case "kitchen": {
      let out = box(-hw, -hd, w, d, 0.01) + `<path d="M${f(-hw)} ${f(hd - 0.04)}H${f(hw)}" ${l}/>`;
      // sink and hob: where the scene put them (centre from the left end), else a sink right of centre
      const sink = p.sink != null ? -hw + p.sink : (p.hob == null && w > 1.2 ? Math.min(hw - 0.45, 0.4) : null);
      const hob = p.hob != null ? -hw + p.hob : (p.sink == null && w > 2.2 ? -Math.min(hw - 0.45, 0.7) : null);
      if (sink != null && d > 0.45) out += box(sink - 0.27, -0.2, 0.54, 0.38, 0.06) + `<circle cx="${f(sink)}" cy="-0.01" r="0.035" ${l}/>`;
      if (hob != null && d > 0.45) for (const [dx, dz] of [[-0.15, -0.13], [0.15, -0.13], [-0.15, 0.13], [0.15, 0.13]]) {
        out += `<circle cx="${f(hob + dx)}" cy="${f(dz)}" r="${dx < 0 ? 0.09 : 0.07}" ${l}/>`;
      }
      return out;
    }
    case "wc": {
      const tw = Math.min(w, 0.42);
      return box(-tw / 2, -hd, tw, 0.2, 0.03) + `<ellipse cx="0" cy="${f(-hd + 0.2 + Math.min(0.24, (d - 0.2) / 2))}" rx="0.18" ry="${f(Math.min(0.25, (d - 0.2) / 2))}" ${s}/>`;
    }
    case "basin": return box(-hw, -hd, w, d, 0.03) + `<ellipse cx="0" cy="0.02" rx="${f(Math.max(0.08, Math.min(0.2, hw - 0.06)))}" ry="${f(Math.max(0.06, hd - 0.07))}" ${l}/>`;
    case "bath": return box(-hw, -hd, w, d, 0.04) + box(-hw + 0.07, -hd + 0.07, w - 0.14, d - 0.14, Math.min(w, d) / 3, l);
    case "shower": return box(-hw, -hd, w, d, 0.01)
      + `<path d="M${f(-hw)} ${f(-hd)}L${f(hw)} ${f(hd)}M${f(hw)} ${f(-hd)}L${f(-hw)} ${f(hd)}" ${l}/><circle cx="0" cy="0" r="0.05" ${s}/>`;
    case "plant": {
      const r = Math.min(0.3, Math.max(0.15, Math.min(w, d) / 2));
      return `<circle cx="0" cy="0" r="${f(r)}" ${s}/>` + [0, 72, 144, 216, 288].map((a) => {
        const q = (a * Math.PI) / 180, cx = f(Math.cos(q) * r * 0.55), cz = f(Math.sin(q) * r * 0.55);
        return `<ellipse cx="${cx}" cy="${cz}" rx="${f(r * 0.45)}" ry="${f(r * 0.22)}" transform="rotate(${a} ${cx} ${cz})" ${l}/>`;
      }).join("");
    }
    case "rug": return `<rect x="${f(-hw)}" y="${f(-hd)}" width="${f(w)}" height="${f(d)}" rx="0.04" fill="none" stroke="#b9b5ac" stroke-width="0.015" stroke-dasharray="0.08 0.06"/>`;
  }
  return box(-hw, -hd, w, d, 0.02);
}

const place = (p) => `<g transform="translate(${f(p.x)} ${f(p.z)}) rotate(${f((-p.rot * 180) / Math.PI)})">${symbol(p)}</g>`;

/** The world box of a piece (its rotated footprint), to keep labels off it. */
function pieceBox(p) {
  const c = Math.abs(Math.cos(p.rot)), s = Math.abs(Math.sin(p.rot));
  const hx = (p.w * c + p.d * s) / 2, hz = (p.w * s + p.d * c) / 2;
  return [p.x - hx, p.z - hz, p.x + hx, p.z + hz];
}

function segDist([px, pz], [ax, az], [bx, bz]) {
  const dx = bx - ax, dz = bz - az, l2 = dx * dx + dz * dz || 1;
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (pz - az) * dz) / l2));
  return Math.hypot(px - ax - t * dx, pz - az - t * dz);
}

/**
 * Where a label of half-size [hw, hh] fits in a polygon, clear of the obstacles: the point of a
 * 10 cm grid that stays furthest from both, nudged toward the middle. Null when it does not fit.
 */
function labelSpot(poly, hw, hh, obstacles) {
  const box = bboxOf(poly);
  const cx = (box[0] + box[2]) / 2, cz = (box[1] + box[3]) / 2;
  let best = null;
  for (let x = box[0] + hw; x <= box[2] - hw + 1e-6; x += 0.1) {
    for (let z = box[1] + hh; z <= box[3] - hh + 1e-6; z += 0.1) {
      const corners = [[x - hw, z - hh], [x + hw, z - hh], [x + hw, z + hh], [x - hw, z + hh], [x, z]];
      if (!corners.every((c) => insidePoly(c, poly))) continue;
      const lb = [x - hw, z - hh, x + hw, z + hh];
      if (obstacles.some((o) => overlaps(lb, o))) continue;
      let edge = Infinity;
      for (let i = 0; i < poly.length; i++) edge = Math.min(edge, segDist([x, z], poly[i], poly[(i + 1) % poly.length]));
      let clear = Infinity;
      for (const o of obstacles) {
        const dx = Math.max(o[0] - (x + hw), x - hw - o[2], 0), dz = Math.max(o[1] - (z + hh), z - hh - o[3], 0);
        clear = Math.min(clear, Math.hypot(dx, dz));
      }
      const score = Math.min(edge - Math.min(hw, hh), clear, 0.8) - 0.08 * Math.hypot(x - cx, z - cz);
      if (!best || score > best.score) best = { x, z, score };
    }
  }
  return best;
}

/** A room's display name and its flat: "Chambre 2 — App. 1, étage" → ["Chambre 2", "App. 1"]. */
function splitName(name) {
  const [base, rest] = String(name).split(/\s+[—–]\s+/);
  return [base, rest ? rest.split(",")[0].trim() : null];
}

const fmtArea = (a) => `${a.toFixed(2)} m²`;

function doorSwing(p, o) {
  const [ax, az] = p.from, [bx, bz] = p.to, len = Math.hypot(bx - ax, bz - az);
  const ux = (bx - ax) / len, uz = (bz - az) / len;
  const rx = -uz, rz = ux; // right of walking from → to
  const spec = typeof o.door === "object" ? o.door : {};
  const side = spec.swing === "right" ? 1 : -1;
  const startAtHinge = spec.hinge !== "end";
  const a = [ax + ux * o.offset, az + uz * o.offset], b = [a[0] + ux * o.width, a[1] + uz * o.width];
  const hinge = startAtHinge ? a : b, free = startAtHinge ? b : a;
  const tip = [hinge[0] + rx * side * o.width, hinge[1] + rz * side * o.width];
  const e = [free[0] - hinge[0], free[1] - hinge[1]], t = [tip[0] - hinge[0], tip[1] - hinge[1]];
  const sweep = e[0] * t[1] - e[1] * t[0] > 0 ? 1 : 0;
  return `<path d="M${f(hinge[0])} ${f(hinge[1])}L${f(tip[0])} ${f(tip[1])}" stroke="${INK}" stroke-width="0.03" fill="none"/>`
    + `<path d="M${f(free[0])} ${f(free[1])}A${f(o.width)} ${f(o.width)} 0 0 ${sweep} ${f(tip[0])} ${f(tip[1])}" stroke="${INK}" stroke-width="0.012" fill="none"/>`;
}

function band(from, to, t, s0, s1, towards = 0) {
  // the quad of a wall segment [s0, s1] along from → to; towards: 0 centred, -1 to the left of the line
  const [ax, az] = from, [bx, bz] = to, len = Math.hypot(bx - ax, bz - az);
  const ux = (bx - ax) / len, uz = (bz - az) / len, rx = -uz, rz = ux;
  const o0 = towards === 0 ? -t / 2 : -t, o1 = towards === 0 ? t / 2 : 0;
  return [[s0, o0], [s1, o0], [s1, o1], [s0, o1]].map(([s, o]) => [ax + ux * s + rx * o, az + uz * s + rz * o]);
}

function stairTreads(room, id) {
  const box = bboxOf(room.polygon);
  const alongX = box[2] - box[0] >= box[3] - box[1];
  let lines = "";
  if (alongX) for (let x = box[0] + 0.26; x < box[2] - 0.05; x += 0.26) lines += `M${f(x)} ${f(box[1])}V${f(box[3])}`;
  else for (let z = box[1] + 0.26; z < box[3] - 0.05; z += 0.26) lines += `M${f(box[0])} ${f(z)}H${f(box[2])}`;
  const mid = alongX ? `M${f(box[0] + 0.15)} ${f((box[1] + box[3]) / 2)}H${f(box[2] - 0.2)}` : `M${f((box[0] + box[2]) / 2)} ${f(box[3] - 0.15)}V${f(box[1] + 0.2)}`;
  return `<clipPath id="${id}"><polygon points="${pts(room.polygon)}"/></clipPath>`
    + `<g clip-path="url(#${id})" stroke="${INK}" fill="none"><path d="${lines}" stroke-width="0.014"/><path d="${mid}" stroke-width="0.012"/></g>`;
}

/**
 * The storey as an SVG document (units: metres; north = up, the model's -z). `furnished: false`
 * keeps the fixed fittings (kitchen, bathroom) and leaves the rest of the furniture out.
 */
export function planSVG(data, { furnished = true, title } = {}) {
  const fr = data.french;
  const T = fr
    ? { balcony: "Balcon", terrace: "Terrasse", total: "Surface", note: "Plan indicatif, non contractuel. Surfaces ≈ mesurées sur la maquette 3D, sauf celles lues sur les plans; mobilier suggéré." }
    : { balcony: "Balcony", terrace: "Terrace", total: "Area", note: "For illustration only. Areas ≈ measured on the 3D model unless read from the plans; furniture shown as a suggestion." };
  const furniture = data.furniture.filter((p) => furnished || FITTINGS.has(p.type));
  const shapes = [...data.rooms.flatMap((r) => r.polygon), ...data.walls.flatMap((w) => [w.from, w.to]), ...data.outdoor.flatMap((o) => o.polygon)];
  const box = bboxOf(shapes);
  const size = Math.max(box[2] - box[0], box[3] - box[1]);
  const u = Math.max(1, size / 12); // text and margins grow a little with the drawing
  const m = 0.9 * u;
  const head = 1.1 * u, foot = 1.9 * u;
  const vx = box[0] - m, vz = box[1] - m - head, vw = box[2] - box[0] + 2 * m, vh = box[3] - box[1] + 2 * m + head + foot;
  let id = 0;

  // outdoor slabs, then rooms
  let out = data.outdoor.map((o) => `<polygon points="${pts(o.polygon)}" fill="${FILL.outdoor}" stroke="${INK}" stroke-width="0.04"/>`).join("");
  out += data.rooms.map((r) => `<polygon points="${pts(r.polygon)}" fill="${FILL[r.use] ?? FILL.storage}"/>`).join("");
  out += data.rooms.filter((r) => r.use === "stair").map((r) => stairTreads(r, `st-${data.index}-${id++}`)).join("");

  // furniture: rugs under the rest
  out += furniture.filter((p) => p.type === "rug").map(place).join("");
  out += furniture.filter((p) => p.type !== "rug").map(place).join("");

  // partitions with their doors
  let doors = "";
  for (const p of data.partitions) {
    const len = Math.hypot(p.to[0] - p.from[0], p.to[1] - p.from[1]);
    const cuts = [0, ...[...p.openings].sort((a, b) => a.offset - b.offset).flatMap((o) => [o.offset, o.offset + o.width]), len];
    for (let i = 0; i < cuts.length; i += 2) {
      if (cuts[i + 1] - cuts[i] > 0.005) out += `<polygon points="${pts(band(p.from, p.to, p.thickness, cuts[i], cuts[i + 1]))}" fill="${INK}"/>`;
    }
    for (const o of p.openings) if (o.door) doors += doorSwing(p, o);
  }
  out += doors;

  // exterior walls: solid, the openings cut out, a window as three lines, a door as two sliding leaves
  for (const w of data.walls) {
    const len = Math.hypot(w.to[0] - w.from[0], w.to[1] - w.from[1]);
    const ops = w.openings.map((o) => ({ ...o, s0: Math.max(0, o.at - o.width / 2), s1: Math.min(len, o.at + o.width / 2) })).sort((a, b) => a.s0 - b.s0);
    const cuts = [0, ...ops.flatMap((o) => [o.s0, o.s1]), len];
    for (let i = 0; i < cuts.length; i += 2) {
      if (cuts[i + 1] - cuts[i] > 0.005) out += `<polygon points="${pts(band(w.from, w.to, w.thickness, cuts[i], cuts[i + 1], -1))}" fill="${INK}"/>`;
    }
    for (const o of ops) {
      const q = band(w.from, w.to, w.thickness, o.s0, o.s1, -1);
      out += `<polygon points="${pts(q)}" fill="#ffffff" stroke="${INK}" stroke-width="0.016"/>`;
      const line = (t, s0 = o.s0, s1 = o.s1) => {
        const [a, b] = [band(w.from, w.to, w.thickness * t, s0, s1, -1)[0], band(w.from, w.to, w.thickness * t, s0, s1, -1)[1]];
        return `M${f(a[0])} ${f(a[1])}L${f(b[0])} ${f(b[1])}`;
      };
      const mid = (o.s0 + o.s1) / 2;
      const d = o.door ? line(0.42, o.s0, mid + 0.05) + line(0.58, mid - 0.05, o.s1) : line(0.3) + line(0.5) + line(0.7);
      out += `<path d="${d}" stroke="${INK}" stroke-width="${o.door ? 0.03 : 0.016}" fill="none"/>`;
    }
  }

  // labels: the rooms (not the stairs), then the terraces and balconies
  // labels stay off the furniture, the partitions and the doors' swing
  const obstacles = furniture.filter((p) => p.type !== "rug").map(pieceBox);
  for (const p of data.partitions) {
    const len = Math.hypot(p.to[0] - p.from[0], p.to[1] - p.from[1]);
    obstacles.push(bboxOf(band(p.from, p.to, p.thickness + 0.04, 0, len)));
    for (const o of p.openings) if (o.door) obstacles.push(bboxOf([...band(p.from, p.to, 2 * o.width, o.offset, o.offset + o.width)]));
  }
  const groups = new Map();
  for (const r of data.rooms) {
    const [base, group] = splitName(r.name);
    const area = r.planArea ?? r.area, measured = r.planArea == null;
    if (group) {
      const g = groups.get(group) ?? { area: 0, measured: false };
      g.area += area; g.measured ||= measured;
      groups.set(group, g);
    }
    if (r.use === "stair") continue;
    const text = `${measured ? "≈ " : ""}${fmtArea(area)}`;
    for (const [ns, as] of [[0.36, 0.27], [0.27, 0.21]]) {
      const hw = Math.max(base.length * ns * 0.56, text.length * as * 0.55) / 2 + 0.06;
      const hh = (ns + as + 0.1) / 2;
      const spot = labelSpot(r.polygon, hw, hh, obstacles) ?? (ns < 0.3 ? labelSpot(r.polygon, hw, hh, []) : null);
      if (!spot) continue;
      out += `<text x="${f(spot.x)}" y="${f(spot.z - hh + ns * 0.85)}" font-size="${ns}" font-weight="600" fill="${LABEL.name}" text-anchor="middle">${esc(base)}</text>`;
      out += `<text x="${f(spot.x)}" y="${f(spot.z + hh - 0.04)}" font-size="${as}" fill="${LABEL.area}" text-anchor="middle">${esc(text)}</text>`;
      break;
    }
  }
  for (const o of data.outdoor) {
    const name = data.y > 1 ? T.balcony : T.terrace, text = `≈ ${fmtArea(o.area)}`;
    const hw = Math.max(name.length * 0.3 * 0.56, text.length * 0.22 * 0.55) / 2 + 0.06, hh = 0.33;
    const inside = data.rooms.map((r) => bboxOf(r.polygon));
    const spot = labelSpot(o.polygon, hw, hh, [...inside, ...data.walls.map((w) => bboxOf([w.from, w.to]))]);
    if (!spot) continue;
    out += `<text x="${f(spot.x)}" y="${f(spot.z - 0.05)}" font-size="0.3" font-weight="600" fill="${LABEL.name}" text-anchor="middle">${esc(name)}</text>`;
    out += `<text x="${f(spot.x)}" y="${f(spot.z + 0.25)}" font-size="0.22" fill="${LABEL.area}" text-anchor="middle">${esc(text)}</text>`;
  }

  // title, north arrow, scale bar, totals, note
  const total = data.rooms.reduce((s, r) => s + (r.planArea ?? r.area), 0);
  const anyMeasured = data.rooms.some((r) => r.planArea == null);
  const top = box[1] - m - head;
  let chrome = `<text x="${f(box[0] - m + 0.1 * u)}" y="${f(top + 0.62 * u)}" font-size="${f(0.5 * u)}" font-weight="700" fill="${LABEL.name}">${esc(title ?? data.label)}</text>`;
  chrome += `<text x="${f(box[2] + m - 0.1 * u)}" y="${f(top + 0.62 * u)}" font-size="${f(0.3 * u)}" fill="${LABEL.area}" text-anchor="end">${esc(`${T.total} ${anyMeasured ? "≈ " : ""}${fmtArea(total)}`)}</text>`;
  const fy = box[3] + m;
  const ax = box[0] - m + 0.55 * u, ay = fy + 0.75 * u;
  chrome += `<g transform="translate(${f(ax)} ${f(ay)}) scale(${f(u)})"><circle r="0.42" fill="none" stroke="${INK}" stroke-width="0.02"/>`
    + `<path d="M0 -0.34L0.14 0.2L0 0.1L-0.14 0.2Z" fill="${INK}"/><text y="-0.5" font-size="0.24" font-weight="600" text-anchor="middle" fill="${INK}">N</text></g>`;
  const sx = ax + 0.8 * u, sy = ay + 0.1 * u, steps = size > 20 ? [0, 2, 4, 6, 10] : [0, 1, 2, 3, 5];
  chrome += `<g font-size="${f(0.2 * u)}" fill="${INK}">`;
  for (let i = 0; i < steps.length - 1; i++) {
    chrome += `<rect x="${f(sx + steps[i])}" y="${f(sy)}" width="${steps[i + 1] - steps[i]}" height="${f(0.1 * u)}" fill="${i % 2 ? "#ffffff" : INK}" stroke="${INK}" stroke-width="0.015"/>`;
  }
  for (const s of steps) chrome += `<text x="${f(sx + s)}" y="${f(sy + 0.38 * u)}" text-anchor="middle">${s}</text>`;
  chrome += `<text x="${f(sx + steps.at(-1) + 0.2 * u)}" y="${f(sy + 0.1 * u)}">m</text></g>`;
  const list = [...groups.entries()];
  list.forEach(([name, g], i) => {
    const x = box[2] + m - 0.1 * u, yy = fy + (0.55 + i * 0.42) * u;
    chrome += `<text x="${f(x)}" y="${f(yy)}" font-size="${f(0.26 * u)}" font-weight="600" fill="${LABEL.name}" text-anchor="end">${esc(`${name} · ${g.measured ? "≈ " : ""}${fmtArea(g.area)}`)}</text>`;
  });
  chrome += `<text x="${f(box[0] - m + 0.1 * u)}" y="${f(vz + vh - 0.2 * u)}" font-size="${f(0.16 * u)}" fill="${LABEL.area}">${esc(T.note)}</text>`;

  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${f(vx)} ${f(vz)} ${f(vw)} ${f(vh)}" width="${Math.round(vw * 60)}" height="${Math.round(vh * 60)}" font-family="${FONT}">`
    + `<rect x="${f(vx)}" y="${f(vz)}" width="${f(vw)}" height="${f(vh)}" fill="#ffffff"/>${out}${chrome}</svg>`;
}

export default { planStoreys, planData, planSVG };
