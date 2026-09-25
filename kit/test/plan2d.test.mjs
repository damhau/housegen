// housekit/plan2d: a storey of a built scene as a 2D plan (SVG), no renderer needed.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import * as house from "../house.js";
import { floorPlan } from "../interior.js";
import fx from "../furnish.js";
import { planData, planStoreys, planSVG } from "../plan2d.js";

// a 8 × 6 m house: a living room and a bedroom split by a partition with a door, a window on the
// south façade, a bed and a WC, a balcony on the first floor
function scene() {
  const root = new THREE.Group();
  const footprint = [[-4, -3], [-4, 3], [4, 3], [4, -3]];
  for (const y of [0, 2.8]) root.add(house.perimeterWalls({ polygon: footprint, y, height: 2.8, thickness: 0.3,
    openings: { 1: [{ offset: 2, sill: 0.9, width: 1.4, height: 1.3 }] }, makeUnit: (o) => house.windowUnit({ width: o.width, height: o.height }) }));
  const rooms = (y) => [
    { name: "Séjour — App. 1", use: "living", polygon: [[-3.7, -2.7], [0.95, -2.7], [0.95, 2.7], [-3.7, 2.7]], area: 25.1 },
    { name: "Chambre — App. 1", use: "bedroom", polygon: [[1.05, -2.7], [3.7, -2.7], [3.7, 2.7], [1.05, 2.7]] },
  ];
  const partitions = [{ from: [1, -2.7], to: [1, 2.7], openings: [{ offset: 3.8, width: 0.8, door: { hinge: "start", swing: "right" } }] }];
  root.add(floorPlan({ y: 0, rooms: rooms(0), partitions }));
  root.add(floorPlan({ y: 2.8, rooms: rooms(2.8), partitions }));
  root.add(fx.place(fx.bed({ width: 1.4 }), [2.4, -1.5], 0, 0.015));
  root.add(fx.place(fx.wc(), [-3.2, -2.3], 0, 0.015));
  root.add(house.balcony({ width: 3, depth: 1.5, position: [0, 2.8, 3] }));
  return root;
}

test("storeys are named from the one on the ground, in the plans' language", () => {
  assert.deepEqual(planStoreys(scene()).map((s) => s.label), ["Rez-de-chaussée", "1er étage"]);
});

test("a storey's plan has its rooms, walls, window, door and furniture", () => {
  const d = planData(scene(), 0);
  assert.equal(d.rooms.length, 2);
  assert.equal(d.walls.length, 4);
  assert.equal(d.walls.reduce((n, w) => n + w.openings.length, 0), 1);
  assert.deepEqual(d.partitions[0].openings[0].door, { hinge: "start", swing: "right" });
  assert.deepEqual(d.furniture.map((p) => p.type).sort(), ["bed", "wc"]);
  assert.equal(d.outdoor.length, 0);
});

test("the plan labels each room with its area: the plan's when given, else measured (≈)", () => {
  const svg = planSVG(planData(scene(), 0));
  assert.match(svg, /^<svg [^>]*viewBox=/);
  assert.match(svg, />Séjour</);
  assert.match(svg, />25\.10 m²</);
  assert.match(svg, />≈ 14\.31 m²</); // 2.65 × 5.4
  assert.match(svg, /App\. 1 · ≈ 39\.41 m²/);
  assert.match(svg, /Rez-de-chaussée/);
});

test("fittings only keeps the WC and leaves the bed out", () => {
  const d = planData(scene(), 0);
  const all = planSVG(d), fittings = planSVG(d, { furnished: false });
  const count = (s) => (s.match(/<g transform="translate\([^)]*\) rotate/g) ?? []).length; // one per piece
  assert.equal(count(all), 2);
  assert.equal(count(fittings), 1);
});

test("the balcony at the upper floor's level is drawn and named there", () => {
  const d = planData(scene(), 1);
  assert.equal(d.outdoor.length, 1);
  assert.ok(Math.abs(d.outdoor[0].area - 4.5) < 1e-6);
  assert.match(planSVG(d), />Balcon</);
  assert.equal(planData(scene(), 0).outdoor.length, 0);
});
