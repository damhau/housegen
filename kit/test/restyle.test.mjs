// housekit: a Restyle redraws a house's catalogue beds and the dressing's floor accents from another style's pieces
// (#120). Every bed must take the same place in every style, and every accent keep to its spot, or the new piece closes
// the passages the scene left round the old one. The catalogue's models are boxes of their pieces' sizes (stub-models).
import { test } from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import { dressRooms } from "../dressing.js";
import * as house from "../house.js";
import { floorPlan } from "../interior.js";
import fx, { pickObject, resetObjects } from "../furnish.js";
import { STYLES, setHouseStyle } from "../styles.js";
import { layout } from "../layout.js";
import { OBJECTS } from "../objects.js";
import { stubCatalogue } from "./stub-models.mjs";

stubCatalogue();
const BY_ID = new Map(OBJECTS.map((o) => [o.id, o]));

const SEEDS = ["fa8bd00053e6", "a7c4e1", "b2d9f0", "c5e8a3", "d1f6b7", "e9a2c4"];
// the beds of a house, in the order its scene draws them: TestVillaGille's, then the other usual sizes
const BEDS = [[1.6, 2.0], [0.9, 2.0], [0.9, 2.0], [1.6, 2.0], [0.9, 2.0], [1.8, 2.0], [1.6, 2.0], [1.6, 2.0], [1.4, 2.0], [1.4, 1.9], [2.0, 2.0], [1.6, 2.1]];

async function drawBeds(style, seed, sizes = BEDS) {
  setHouseStyle({ style });
  globalThis.__housekitSeed = seed;
  resetObjects();
  const out = [];
  for (const [width, length] of sizes) out.push(await fx.model("bed", { width, length, room: `Chambre ${out.length + 1}` }));
  return out;
}

function done() {
  setHouseStyle({});
  delete globalThis.__housekitSeed;
  resetObjects();
}

test("a bed takes the same place in every style: its mattress in the kit's frame, a catalogue model or the kit's own", async () => {
  try {
    let swapped = 0, models = 0;
    for (const seed of SEEDS) {
      const drawn = {};
      for (const style of Object.keys(STYLES)) drawn[style] = await drawBeds(style, seed);
      const [a, b] = Object.values(drawn);
      BEDS.forEach(([width, length], i) => {
        const where = `seed ${seed}, bed ${i + 1} (${width} x ${length})`;
        for (const p of [a[i], b[i]]) {
          assert.deepEqual(p.userData.footprint.map((v) => +v.toFixed(3)), [+(width + 0.06).toFixed(3), +(length + 0.08).toFixed(3)],
            `${where}: ${p.userData.model ?? "the kit's bed"} takes ${p.userData.footprint.map((v) => v.toFixed(2)).join(" x ")}`);
          if (!p.userData.model) continue;
          models++;
          const [mw, ml] = p.userData.nominal;
          assert.ok(Math.abs(Math.log(mw / width)) <= Math.log(1.1) + 1e-9 && Math.abs(Math.log(ml / length)) <= Math.log(1.1) + 1e-9,
            `${where}: ${p.userData.model} draws a ${mw.toFixed(2)} x ${ml.toFixed(2)} mattress`);
        }
        if (a[i].userData.model !== b[i].userData.model) swapped++;
      });
    }
    assert.ok(models > SEEDS.length * 2 * 6, `only ${models} catalogue beds: the styles hardly draw their own`);
    assert.ok(swapped > SEEDS.length * 4, `a Restyle swapped only ${swapped} beds`);
  } finally {
    done();
  }
});

// a 3.05 x 2.72 m bedroom inside 30 cm walls: a double bed against its north wall, 2.0 m with the kit's frame, leaves
// 60 cm at its foot and on both sides; a frame 18 cm longer or 14 cm wider than that does not
function bedroom(bed) {
  const root = new THREE.Group();
  root.add(house.perimeterWalls({ polygon: [[-0.3, -0.3], [3.35, -0.3], [3.35, 3.02], [-0.3, 3.02]], y: 0, height: 2.6, thickness: 0.3 }));
  const polygon = [[0, 0], [3.05, 0], [3.05, 2.72], [0, 2.72]];
  root.add(floorPlan({ y: 0, height: 2.5, rooms: [{ name: "Chambre", use: "bedroom", polygon, area: 8.3 }] }));
  root.add(fx.onWall(polygon, 0, 1.525, bed, { y: 0.015 }));
  root.updateMatrixWorld(true);
  return layout(root);
}

test("a Restyle keeps the passages round a bed in a tight bedroom, in every style, whichever frame the style offers", async () => {
  try {
    for (const seed of SEEDS) {
      for (const style of Object.keys(STYLES)) {
        const [bed] = await drawBeds(style, seed, [[1.6, 2.0]]);
        const L = bedroom(bed);
        const found = L.findings.filter((f) => f.piece === "bed" && (f.kind === "passage" || /does not fit/.test(f.text))).map((f) => f.text);
        assert.deepEqual(found, [], `seed ${seed}, ${style}: ${bed.userData.model ?? "the kit's bed"} (${bed.userData.footprint.map((v) => v.toFixed(2)).join(" x ")})`);
      }
    }
  } finally {
    done();
  }
});

// the plan box of `p` turned by `yaw`: its larger side
function across(p, yaw) {
  const g = new THREE.Group();
  g.add(p);
  g.rotation.y = yaw;
  g.updateMatrixWorld(true);
  const s = new THREE.Box3().setFromObject(g).getSize(new THREE.Vector3());
  g.remove(p);
  return Math.max(s.x, s.z);
}

// the dressing's floor vases and floor plants (dressing.js floors), at the diameter the spot leaves them
const FLOOR = [
  ["vase", { height: 0.46, stems: true, spread: 0.5 }], ["vase", { height: 0.55, stems: true, spread: 0.5 }],
  ["floor-plant", { height: 1.05, spread: 0.9 }], ["floor-plant", { height: 1.15, spread: 0.9 }], ["floor-plant", { height: 1.5, spread: 0.9 }],
];

test("a floor vase or plant keeps to the spot it was given in every style, its stems and leaves too, however it is turned", async () => {
  try {
    const widest = {};
    for (const seed of SEEDS) {
      for (const style of Object.keys(STYLES)) {
        setHouseStyle({ style });
        globalThis.__housekitSeed = seed;
        resetObjects();
        for (const [role, opts] of FLOOR) {
          const p = await fx.model(role, { ...opts, room: "Chambre 4 — App. 1" });
          assert.match(p.userData.model ?? "", /\+/, `seed ${seed}, ${style}: a ${role} of two pieces`);
          for (const yaw of [0, 0.4, Math.PI / 4, 1.2, Math.PI / 2]) {
            const w = across(p, yaw);
            assert.ok(w <= opts.spread + 1e-6, `seed ${seed}, ${style}: ${p.userData.model} ${w.toFixed(3)} m across turned ${yaw.toFixed(2)}`);
            widest[role] = Math.max(widest[role] ?? 0, w / opts.spread);
          }
        }
      }
    }
    assert.ok(Object.values(widest).every((w) => w > 0.8), `the stems and leaves hardly spread: ${JSON.stringify(widest)}`);
  } finally {
    done();
  }
});

// TestVillaGille's Chambre 4 — App. 1 (first floor): an L, its door in the north wall, the bathroom's corner jutting
// into it, a double bed against the west wall, a wardrobe against the east one, the scene's own small plant by the south
// wall. The dressing adds one floor accent; at the bathroom's corner a pouf 52 cm wide, turned, left less than the
// walk's 50 cm to the plant: the room was cut in two in one style and not in the other.
const L_ROOM = [[0.125, 1.59], [3.6, 1.59], [3.6, 2.78], [5.1, 2.78], [5.1, 4.6], [0.125, 4.6]];

function lRoom() {
  const root = new THREE.Group();
  root.add(house.perimeterWalls({ polygon: [[-0.175, 1.29], [5.4, 1.29], [5.4, 4.9], [-0.175, 4.9]], height: 2.46, thickness: 0.3 }));
  root.add(floorPlan({ y: 0, height: 2.46, rooms: [{ name: "Chambre 4 — App. 1", use: "bedroom", polygon: L_ROOM, area: 13.05 }],
    partitions: [
      { from: [0.125, 1.54], to: [3.65, 1.54], thickness: 0.1, openings: [{ offset: 2.59, width: 0.88, height: 2.04, door: { hinge: "end", swing: "right", open: 90 } }] },
      { from: [3.65, 1.54], to: [3.65, 2.72], thickness: 0.1 }, { from: [3.65, 2.72], to: [5.1, 2.72], thickness: 0.12 },
    ] }));
  const y = 0.015, bed = fx.bed({ width: 1.6, length: 2 });
  root.add(fx.onWall(L_ROOM, 5, 1.5, bed, { y, gap: 0.02 }));
  const [bx, bz] = [bed.position.x, bed.position.z], [bw, bl] = bed.userData.footprint, th = bed.rotation.y;
  for (const side of [-1, 1]) {
    const [u, v] = [side * (bw / 2 + 0.24), -bl / 2 + 0.28];
    root.add(fx.place(fx.nightstand({ width: 0.35, length: 0.33 }), [bx + u * Math.cos(th) + v * Math.sin(th), bz - u * Math.sin(th) + v * Math.cos(th)], th, y));
  }
  root.add(fx.onWall(L_ROOM, 3, 0.9, fx.wardrobe({ width: 1.6, depth: 0.58, height: 2.3 }), { y, gap: 0.02 }));
  const plant = new THREE.Group();
  plant.add(new THREE.Mesh(new THREE.CylinderGeometry(0.2, 0.2, 0.42).translate(0, 0.21, 0)));
  plant.userData = { kind: "furniture", name: "floor-plant", footprint: [0.4, 0.4] };
  root.add(fx.place(plant, [3.0, 4.12], 0, y));
  root.updateMatrixWorld(true);
  return root;
}

// the areas one can walk to in a room as the walk counts them (walk.js reach): 5 cm cells 25 cm from its edges and from
// the furniture standing 0.2 to 1.9 m over the floor, a patch under 0.2 m² not counted; `discs` stand on it too
function walkAreas(polygon, boxes, discs) {
  const C = 0.05, R = 0.25, xs = polygon.map((p) => p[0]), zs = polygon.map((p) => p[1]);
  const [x0, z0] = [Math.min(...xs), Math.min(...zs)];
  const nx = Math.ceil((Math.max(...xs) - x0) / C), nz = Math.ceil((Math.max(...zs) - z0) / C);
  const inside = (x, z) => polygon.reduce((c, [xi, zi], i) => {
    const [xj, zj] = polygon[(i + polygon.length - 1) % polygon.length];
    return zi > z !== zj > z && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi ? !c : c;
  }, false);
  const toSeg = (x, z, [ax, az], [bx, bz]) => {
    const dx = bx - ax, dz = bz - az, t = Math.max(0, Math.min(1, ((x - ax) * dx + (z - az) * dz) / (dx * dx + dz * dz)));
    return Math.hypot(x - ax - t * dx, z - az - t * dz);
  };
  const ok = (x, z) => inside(x, z) && polygon.every((p, i) => toSeg(x, z, p, polygon[(i + 1) % polygon.length]) >= R)
    && !boxes.some((b) => Math.hypot(Math.max(b.min.x - x, 0, x - b.max.x), Math.max(b.min.z - z, 0, z - b.max.z)) < R)
    && !discs.some(([dx, dz, r]) => Math.hypot(x - dx, z - dz) < r + R);
  const free = Array.from({ length: nx * nz }, (_, k) => ok(x0 + (k % nx + 0.5) * C, z0 + (Math.floor(k / nx) + 0.5) * C));
  const seen = new Set();
  let n = 0;
  for (let k0 = 0; k0 < free.length; k0++) {
    if (!free[k0] || seen.has(k0)) continue;
    const stack = [k0];
    let cells = 0;
    seen.add(k0);
    while (stack.length) {
      const k = stack.pop();
      cells++;
      for (const m of [k % nx < nx - 1 ? k + 1 : -1, k % nx ? k - 1 : -1, k + nx, k - nx]) {
        if (m >= 0 && m < free.length && free[m] && !seen.has(m)) (seen.add(m), stack.push(m));
      }
    }
    if (cells * C * C >= 0.2) n++;
  }
  return n;
}

test("a Restyle never lets the dressing's floor accent cut a bedroom in two: TestVillaGille's L-shaped Chambre 4", () => {
  try {
    // how far each accent reaches from its spot: a pouf or a basket turned any way, its catalogue size; the floor vase
    // and the floor plant the kit bounds (dressing.js FLOOR_VASE, FLOOR_PLANT)
    const reach = (u) => (u.dressing === "vase" ? 0.25 : u.dressing === "floor-plant" ? 0.45 : Math.hypot(...BY_ID.get(u.model).size.slice(0, 2)) / 2);
    let accents = 0;
    for (const seed of SEEDS) {
      for (const style of Object.keys(STYLES)) {
        setHouseStyle({ style });
        globalThis.__housekitSeed = seed;
        resetObjects();
        const root = lRoom();
        const boxes = [];
        root.traverse((o) => {
          if (o.userData?.kind !== "furniture" || o.parent?.userData?.kind === "furniture") return;
          const b = new THREE.Box3().setFromObject(o);
          if (b.min.y < 1.9 && b.max.y > 0.2) boxes.push(b);
        });
        const before = walkAreas(L_ROOM, boxes, []);
        dressRooms(root);
        const discs = [];
        root.traverse((o) => {
          const u = o.userData;
          if (u?.kind !== "dressing" || !["floor-plant", "vase", "pouf", "basket"].includes(u.dressing)) return;
          const p = o.getWorldPosition(new THREE.Vector3());
          if (p.y < 0.1) discs.push([p.x, p.z, reach(u), `${u.dressing} ${u.model ?? ""}`]);
        });
        accents += discs.length;
        assert.ok(walkAreas(L_ROOM, boxes, discs) <= before,
          `seed ${seed}, ${style}: ${discs.map(([x, z, r, w]) => `${w} at ${x.toFixed(2)}, ${z.toFixed(2)} (${r.toFixed(2)} m)`).join(", ")} cuts the room`);
      }
    }
    assert.ok(accents >= SEEDS.length, `only ${accents} floor accents`);
  } finally {
    done();
  }
});

test("a bed's pick: its mattress near the size asked in the kit's frame, never a frame stretched more than a quarter", () => {
  try {
    for (const style of Object.keys(STYLES)) {
      setHouseStyle({ style });
      for (const [width, length] of [[1.4, 2.0], [1.6, 2.0], [1.8, 2.0], [1.6, 2.1]]) {
        resetObjects();
        const o = pickObject("bed", { width, length }).piece;
        const k = [(width + 0.06) / o.size[0], (length + 0.08) / o.size[1]];
        assert.ok(k.every((v) => Math.abs(Math.log(v)) <= Math.log(1.25)), `${style} ${width} x ${length}: ${o.id} stretched ${k.map((v) => v.toFixed(2)).join(" x ")}`);
        assert.ok(Math.abs(Math.log((o.nominal[0] * k[0]) / width)) <= Math.log(1.1) && Math.abs(Math.log((o.nominal[1] * k[1]) / length)) <= Math.log(1.1),
          `${style} ${width} x ${length}: ${o.id}'s mattress ${o.nominal.join(" x ")} drawn ${(o.nominal[0] * k[0]).toFixed(2)} x ${(o.nominal[1] * k[1]).toFixed(2)}`);
      }
    }
  } finally {
    done();
  }
});
