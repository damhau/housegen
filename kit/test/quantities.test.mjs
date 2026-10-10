// housekit/quantities: SIA 416 surfaces and volumes of a built scene (#47, #48), with a fake height
// measure in place of the GPU (the roof is a gable, given as a function).
import { test } from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import * as house from "../house.js";
import { floorPlan, tileWalls } from "../interior.js";
import { quantities, siaClass, flatOf, planGaps, _internals } from "../quantities.js";

const near = (a, b, tol = 1e-3, msg = "") => assert.ok(Math.abs(a - b) <= tol, `${msg} ${a} ≠ ${b}`);

/** A height measure over the requested grid from functions of the world point (NaN = nothing hit). */
function fakeMeasure({ top, under }) {
  return async ({ mode, frame, cell, nx, nz, y0 }) => {
    const out = new Float32Array(nx * nz);
    for (let j = 0; j < nz; j++) for (let i = 0; i < nx; i++) {
      const x = frame.o[0] + frame.u[0] * (i + 0.5) * cell + frame.v[0] * (j + 0.5) * cell;
      const z = frame.o[1] + frame.u[1] * (i + 0.5) * cell + frame.v[1] * (j + 0.5) * cell;
      out[j * nx + i] = mode === "top" ? top(x, z) : under(x, z, y0);
    }
    return out;
  };
}

// a 10 × 8 m two-flat house: a basement whose walls lost their kind (as a scene keeping it out of the
// framing does), a ground floor and an attic under a gable roof (ridge along z at x = 0, 0.5 per metre)
const W = 10, D = 8, T = 0.3;
const footprint = [[-W / 2, -D / 2], [W / 2, -D / 2], [W / 2, D / 2], [-W / 2, D / 2]];
const ridge = 9.0, pitch = 0.5;
function scene({ tiles = true } = {}) {
  const root = new THREE.Group();
  const win = (offset, width = 1.2, height = 1.2, sill = 1) => ({ offset, width, height, sill });
  const unit = (o) => (o.door ? house.door({ width: o.width, height: o.height }) : house.windowUnit({ width: o.width, height: o.height }));
  const basement = house.perimeterWalls({ polygon: footprint, y: -2.6, height: 2.6, thickness: T, openings: { 0: [win(2, 1, 0.5, 1.8)] }, makeUnit: unit });
  basement.traverse((o) => { delete o.userData.kind; });
  root.add(basement);
  root.add(house.perimeterWalls({ polygon: footprint, y: 0, height: 2.8, thickness: T, makeUnit: unit,
    openings: { 0: [win(1), win(6, 2.4, 2.2, 0)], 2: [{ ...win(4, 1, 2.1, 0), door: true }, win(7, 0.8, 1.0, 1.2)] } }));
  // attic: knee walls along the eaves (x = ±5) only
  for (const x of [-W / 2, W / 2]) root.add(house.wall({ from: [x, x > 0 ? D / 2 : -D / 2], to: [x, x > 0 ? -D / 2 : D / 2], y: 2.8, height: 1, thickness: T }));
  const rooms = (y) => (y < 0
    ? [{ name: "Cave — Appartement 1", use: "storage", polygon: [[-4.7, -3.7], [-0.05, -3.7], [-0.05, 3.7], [-4.7, 3.7]], floor: "concrete" },
      { name: "Local technique — Commun", use: "storage", polygon: [[0.05, -3.7], [4.7, -3.7], [4.7, 3.7], [0.05, 3.7]], floor: "concrete" }]
    : y < 1
      ? [{ name: "Séjour — Appartement 1", use: "living", polygon: [[-4.7, -3.7], [-0.05, -3.7], [-0.05, 3.7], [-4.7, 3.7]], area: 30.0 },
        { name: "Bain — Appartement 1", use: "bath", polygon: [[0.05, -3.7], [4.7, -3.7], [4.7, 0], [0.05, 0]], floor: "tile", area: 17.0 },
        { name: "Hall — Appartement 1", use: "hall", polygon: [[0.05, 0], [4.7, 0], [4.7, 3.7], [0.05, 3.7]] }]
      : [{ name: "Chambre — Appartement 2", use: "bedroom", polygon: [[-4.7, -3.7], [4.7, -3.7], [4.7, 3.7], [-4.7, 3.7]], area: 30.0 }]);
  const parts = [{ from: [0, -3.7], to: [0, 3.7], thickness: 0.1, openings: [{ offset: 5, width: 0.9 }] }];
  root.add(floorPlan({ y: -2.6, height: 2.4, rooms: rooms(-2.6), partitions: parts }));
  const ground = floorPlan({ y: 0, height: 2.5, rooms: rooms(0), partitions: [...parts, { from: [0.05, 0], to: [4.7, 0], thickness: 0.1, openings: [{ offset: 1, width: 0.8 }] }] });
  root.add(ground);
  root.add(floorPlan({ y: 2.8, height: 2.4, ceiling: false, rooms: rooms(2.8), partitions: [] }));
  if (tiles) root.add(tileWalls(root, ground.userData.rooms[1], { y: 0, height: 1.2, full: [0] }));
  // a balcony off the attic? no: a terrace slab on the ground to the south, a balcony on the first level
  root.add(house.slab({ polygon: [[-3, 4], [3, 4], [3, 7], [-3, 7]], y: 0.02, thickness: 0.12 }));
  root.add(house.slab({ polygon: [[-W / 2 + T, -D / 2 + T], [W / 2 - T, -D / 2 + T], [W / 2 - T, D / 2 - T], [-W / 2 + T, D / 2 - T]], y: 2.8, thickness: 0.3 }));
  root.add(house.balcony({ width: 3, depth: 1.5, position: [0, 2.8, D / 2] }));
  root.add(house.hedge({ from: [-8, 9], to: [8, 9] }));
  root.updateMatrixWorld(true);
  return root;
}
const roofTop = (x, z) => (Math.abs(x) <= W / 2 + 0.5 && Math.abs(z) <= D / 2 + 0.5 ? ridge - pitch * Math.abs(x) : NaN);
const clearUnder = (x, z) => ridge - 0.3 - pitch * Math.abs(x); // the roof's underside, 0.3 under its top

test("room classes: SIA 416 by use, the name deciding for storage rooms only", () => {
  assert.equal(siaClass({ use: "bedroom" }), "SUP");
  assert.equal(siaClass({ use: "bath" }), "SUP");
  assert.equal(siaClass({ use: "hall" }), "SD");
  assert.equal(siaClass({ use: "stair" }), "SD");
  for (const use of ["cellar", "laundry", "garage", "attic"]) assert.equal(siaClass({ use }), "SUS");
  assert.equal(siaClass({ use: "technical" }), "SI");
  assert.equal(siaClass({ use: "storage", name: "Local technique PAC" }), "SI");
  assert.equal(siaClass({ use: "storage", name: "Chaufferie" }), "SI");
  assert.equal(siaClass({ use: "storage", name: "Cave" }), "SUS");
  assert.equal(siaClass({ use: "living", name: "Local technique" }), "SUP"); // the name decides for storage only
});

test("the flat a room's name gives", () => {
  assert.equal(flatOf("Chambre 2 — Appartement 1, étage"), "App. 1");
  assert.equal(flatOf("Cuisine — App. 2"), "App. 2");
  assert.equal(flatOf("Local technique — Commun sous-sol"), "Commun");
  assert.equal(flatOf("Séjour"), null);
});

test("plan gaps: over the share AND over the area, with the likely cause", () => {
  const rooms = [
    { no: "a", sn: 15.3, plan: 14.05, sloped: true }, // +8.9 %, +1.25 m²: under the roof
    { no: "b", sn: 4.39, plan: 5.25 }, // −16 %: the outline
    { no: "c", sn: 2.23, plan: 2.05 }, // +8.8 % but 0.18 m²: a small room
    { no: "d", sn: 10.23, plan: 10.25 }, // fine
    { no: "e", sn: 8 }, // no printed area
  ];
  assert.deepEqual(planGaps(rooms).map((r) => [r.no, r.cause]), [["a", "slope"], ["b", "outline"], ["c", "small"]]);
  assert.deepEqual(planGaps(rooms, { pct: 0.05, abs: 0.5 }).map((r) => r.no), ["a", "b"]);
});

test("storeys: SP from the walls (also those whose kind was removed), SN and the classes from the rooms", async () => {
  const q = await quantities(scene(), { measure: fakeMeasure({ top: roofTop, under: clearUnder }) });
  assert.equal(q.storeys.length, 3);
  assert.deepEqual(q.storeys.map((s) => s.label), ["Sous-sol", "Rez-de-chaussée", "Combles"]);
  for (const s of q.storeys) near(s.sp, 80, 1e-6, s.label);
  near(q.storeys[0].SUS, 4.65 * 7.4); // the cellar
  near(q.storeys[0].SI, 4.65 * 7.4); // the plant room filed as storage
  near(q.storeys[1].SD, 4.65 * 3.7);
  assert.deepEqual(q.storeys[1].rooms.map((r) => r.no), ["0.01", "0.02", "0.03"]);
  assert.equal(q.storeys[1].rooms[0].formula, "4.65 × 7.40");
  assert.equal(q.flats.length, 2);
});

test("openings: on their façade and storey, behind them the room; basement windows are soupiraux", async () => {
  const q = await quantities(scene(), { measure: fakeMeasure({ top: roofTop, under: clearUnder }) });
  const [b, g] = q.storeys;
  assert.deepEqual(b.ext.map((o) => o.kind), ["soupirail"]);
  assert.deepEqual(g.ext.map((o) => o.kind).sort(), ["fenêtre", "fenêtre", "porte", "porte-fenêtre"]);
  const sejour = g.rooms[0];
  // the partition's door on both faces, the two windows of the north façade (x < 0 side)
  assert.ok(sejour.openings.some((o) => o.kind === "porte" && !("edge" in o)));
  assert.equal(q.groundWalls.small, 1);
  near(q.groundWalls.gross, 36 * 2.6);
});

test("façades: the faces in their plane above the ground floor, gross with the openings, openings under 1 m² kept", async () => {
  const q = await quantities(scene(), { measure: fakeMeasure({ top: roofTop, under: clearUnder }) });
  const north = q.facades.find((f) => f.n[1] < -0.9);
  // 10 m × 2.8 (ground) + the knee walls are on the east and west only
  near(north.gross, 10 * 2.8, 1e-6);
  near(north.holes, 1.2 * 1.2 + 2.4 * 2.2, 1e-6);
  const south = q.facades.find((f) => f.n[1] > 0.9);
  assert.equal(south.nKeep, 1); // the 0.8 × 1.0 window
  near(south.net, 10 * 2.8 - 1 * 2.1, 1e-6);
  const east = q.facades.find((f) => f.n[0] > 0.9);
  near(east.gross, 8 * 2.8 + 8 * 1, 1e-6); // with its knee wall
  near(east.top, 8, 1e-6);
});

test("volumes: storey bands, the roof measured from above; the slab under the basement when the model has one", async () => {
  const q = await quantities(scene(), { measure: fakeMeasure({ top: roofTop, under: clearUnder }) });
  const B = q.buildings[0];
  assert.equal(B.slab, null); // no tagged slab under the basement: the report assumes one (setting)
  near(B.bands[0].v, 80 * 2.6, 1e-3);
  near(B.bands[1].v, 80 * 2.8, 1e-3);
  // the gable from 2.8: ∫ (9 − 0.5|x| − 2.8) over 10 × 8 = 8 × (10 × 6.2 − 0.5 × 2 × 12.5)
  near(B.bands[2].v, 8 * (10 * 6.2 - 12.5), 1e-3);
  assert.equal(B.bands[2].label, "Combles et toiture");
  near(B.sb, 80, 1e-9);
});

test("the attic: clear height under the roof, its walls and the RLATC check", async () => {
  const q = await quantities(scene(), { measure: fakeMeasure({ top: roofTop, under: clearUnder }) });
  const attic = q.storeys[2];
  near(q.attic.minClear, 8.7 - 0.5 * 4.69 - 2.8, 0.01);
  const room = attic.rooms[0];
  // the room's four walls: 2 × 9.4 along x under the slope, 2 × 7.4 at x = ±4.7
  const along = 2 * (9.4 * 5.9 - 0.5 * 4.7 ** 2); // ∫ (5.9 − 0.5|x|) dx over ±4.7, both long walls
  const ends = 2 * 7.4 * (5.9 - 0.5 * 4.7);
  near(room.wallsGross, along + ends, 0.2); // sampled 2 cm inside the room
  const r = q.rlatc.find((x) => x.no === room.no);
  assert.ok(r.attic);
  // 3.55 m at its lowest: all of it counts (≥ 1.30) and is ≥ 2.40 (art. 27)
  near(r.use, 9.4 * 7.4, 0.05);
  assert.equal(r.high, 1);
  assert.ok(r.ok27 && r.ok25);
});

test("wall tiles: the band and the full-height edges the kit recorded, openings of 1 m² or more deducted", async () => {
  const q = await quantities(scene(), { measure: fakeMeasure({ top: roofTop, under: clearUnder }) });
  const bath = q.storeys[1].rooms[1];
  // 4.65 × 3.7 room: edge 0 (4.65 m, the north façade) at 2.40, the others at 1.20; its window (1.2 × 1.2 from
  // 1.0) deducted, its door into the hall (0.8 wide, 0.96 m² in the 1.20 band) kept: under 1 m²
  const gross = 4.65 * 2.4 + (3.7 + 4.65 + 3.7) * 1.2;
  near(bath.tiles, gross - 1.2 * 1.2, 1e-3);
  assert.equal(bath.tileFull, 1);
});

test("balconies to the flat of the rooms they run along, terraces on the ground, hedges", async () => {
  const q = await quantities(scene(), { measure: fakeMeasure({ top: roofTop, under: clearUnder }) });
  assert.equal(q.balconies.length, 1);
  near(q.balconies[0].area, 4.5);
  assert.equal(q.balconies[0].flat, "App. 2");
  assert.deepEqual(q.terraces.map((t) => t.area), [18]);
  near(q.exterior.hedges.length, 16);
});

test("pits: a hole in a roof plane is closed by the plane, a ridge joint by its two planes, a valley is left", () => {
  const { fillPits } = _internals;
  const nx = 200, nz = 100, c = 0.05;
  const plane = (i, j) => 3 + 0.02 * i + 0.01 * j;
  const a = new Float32Array(nx * nz);
  for (let j = 0; j < nz; j++) for (let i = 0; i < nx; i++) a[j * nx + i] = plane(i, j);
  for (let j = 40; j < 60; j++) for (let i = 50; i < 70; i++) a[j * nx + i] = j < 50 ? NaN : 1; // a window: the sky, a floor
  const pits = fillPits(a, nx, nz, c);
  assert.equal(pits.length, 1);
  assert.ok(pits[0].empty > 0);
  near(a[45 * nx + 60], plane(60, 45), 1e-4);
  // a gable with a 10 cm joint at its ridge, and a valley
  const tent = (i) => 5 - 0.5 * Math.abs((i - 100) * c);
  const b = new Float32Array(nx * nz);
  for (let j = 0; j < nz; j++) for (let i = 0; i < nx; i++) b[j * nx + i] = Math.abs(i - 99.5) < 1.5 ? tent(i) - 0.08 : tent(i);
  const mask = new Uint8Array(nx * nz);
  for (let j = 10; j < 90; j++) for (let i = 10; i < 190; i++) mask[j * nx + i] = 1;
  fillPits(b, nx, nz, c, { mask });
  near(b[50 * nx + 100], tent(100), 1e-3);
  const v = new Float32Array(nx * nz);
  for (let j = 0; j < nz; j++) for (let i = 0; i < nx; i++) v[j * nx + i] = 3 + 0.5 * Math.abs((i - 100) * c);
  assert.equal(fillPits(v, nx, nz, c, { mask }).length, 0);
  near(v[50 * nx + 100], 3, 1e-6);
});

test("hedges of bushes: a row, and a double row counted once", () => {
  const { bushRows } = _internals;
  const row = Array.from({ length: 10 }, (_, i) => ({ at: [i * 0.94, 0], r: 0.59 }));
  const rows = bushRows(row);
  assert.equal(rows.length, 1);
  near(rows[0].length, 9.4, 0.01);
  const dbl = [...row, ...row.map((b) => ({ at: [b.at[0] + 0.19, 0.53], r: 0.59 }))];
  const d = bushRows(dbl);
  assert.equal(d.length, 1);
  near(d[0].length, 9.4, 0.05);
  near(d[0].double, d[0].length, 1e-9);
});
