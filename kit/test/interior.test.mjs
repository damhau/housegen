// housekit/interior: rooms, partitions and door leaves, no renderer needed.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import { floorPlan, partition, interiorDoor, polygonArea } from "../interior.js";

test("room areas are reported whatever the winding", () => {
  const sq = [[0, 0], [4, 0], [4, 3], [0, 3]];
  assert.equal(polygonArea(sq), 12);
  assert.equal(polygonArea([...sq].reverse()), 12);
  const plan = floorPlan({ y: 0.15, rooms: [{ name: "Chambre", use: "bedroom", polygon: sq }] });
  assert.deepEqual(plan.userData.rooms.map((r) => [r.name, r.area]), [["Chambre", 12]]);
});

test("a partition is centred on its line and spans it", () => {
  const w = partition({ from: [0, 1], to: [3, 1], thickness: 0.1, height: 2.5 });
  w.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(w);
  assert.ok(Math.abs(box.min.x - 0) < 1e-6 && Math.abs(box.max.x - 3) < 1e-6);
  assert.ok(Math.abs(box.min.z - 0.95) < 1e-6 && Math.abs(box.max.z - 1.05) < 1e-6);
  assert.ok(Math.abs(box.max.y - 2.5) < 1e-6);
});

test("a door swings into the side it is asked to", () => {
  // along +x, "right" (walking from → to) is +z: the open leaf must be on the +z side
  for (const [swing, sign] of [["right", 1], ["left", -1]]) {
    for (const hinge of ["start", "end"]) {
      const d = interiorDoor({ width: 0.8, thickness: 0.1, hinge, swing, open: 90 });
      d.updateMatrixWorld(true);
      const leaf = d.children.at(-1).children[0];
      const c = new THREE.Box3().setFromObject(leaf).getCenter(new THREE.Vector3());
      assert.ok(sign * c.z > 0.3, `${hinge}/${swing}: leaf centre z=${c.z.toFixed(2)}`);
      assert.ok(Math.abs(c.x - (hinge === "start" ? 0 : 0.8)) < 0.05, `${hinge}/${swing}: leaf at the hinge jamb`);
    }
  }
});

test("floorPlan keeps its partitions and puts a leaf in each door opening", () => {
  const plan = floorPlan({
    y: 0,
    rooms: [],
    partitions: [
      { from: [0, 0], to: [4, 0], openings: [{ offset: 1, width: 0.8 }, { offset: 2.5, width: 0.9, door: false }] },
    ],
  });
  assert.equal(plan.userData.partitions.length, 1);
  assert.deepEqual(plan.userData.partitions[0].openings.map((o) => o.door), [true, false]);
  let doors = 0;
  plan.traverse((o) => { if (o.userData?.kind === "interiorDoor") doors++; });
  assert.equal(doors, 1);
});
