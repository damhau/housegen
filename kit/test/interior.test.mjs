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
  // a door keeps its hinge and swing (the 2D plan draws them), an open passage is false
  assert.deepEqual(plan.userData.partitions[0].openings.map((o) => o.door), [{ hinge: "start", swing: "left" }, false]);
  let doors = 0;
  plan.traverse((o) => { if (o.userData?.kind === "interiorDoor") doors++; });
  assert.equal(doors, 1);
});

test("tileWalls tiles the walls to the height asked for, around the doors and windows", async () => {
  const { tileWalls, floorPlan } = await import("../interior.js");
  const house = await import("../house.js");
  const root = new THREE.Group();
  // a 3 x 2 bathroom: a door in the partition on its north side, a window in its south wall
  const bath = { name: "bath", use: "bath", polygon: [[0, 0], [3, 0], [3, 2], [0, 2]] };
  root.add(floorPlan({ y: 0, height: 2.4, rooms: [bath],
    partitions: [{ from: [0, -0.05], to: [3, -0.05], thickness: 0.1, openings: [{ offset: 1, width: 0.8 }] }] }));
  const win = house.windowUnit({ width: 1.0, height: 1.0 });
  win.position.set(1.5, 1.0, 2.15); // in the south wall, sill at 1.0: its bottom 20 cm under the tiles' top
  root.add(win);
  const tiled = (g) => {
    let a = 0;
    g.traverse((o) => {
      if (!o.isMesh) return;
      const pos = o.geometry.attributes.position, idx = o.geometry.index;
      const P = [0, 1, 2].map(() => new THREE.Vector3());
      for (let t = 0; t < idx.count; t += 3) {
        [0, 1, 2].forEach((k) => P[k].fromBufferAttribute(pos, idx.getX(t + k)));
        const n = new THREE.Vector3().crossVectors(P[1].clone().sub(P[0]), P[2].clone().sub(P[0]));
        if (n.z / n.length() > 0.99) a += n.length() / 2; // the faces towards the room
      }
    });
    return a;
  };
  const g = tileWalls(root, bath, { y: 0, height: 1.2 });
  // perimeter 10 m x 1.2, less the door (0.8 x 1.2) and the window's bottom 0.2 m (1.0 x 0.2)
  assert.ok(Math.abs(tiled(g) - (12 - 0.96 - 0.2)) < 0.02, `tiled ${tiled(g).toFixed(3)} m²`);
  // full height behind the bath (edge 1, the east wall, 2 m): + 2 x 1.2
  const full = tileWalls(root, bath, { y: 0, height: 1.2, full: [1] });
  assert.ok(Math.abs(tiled(full) - (12 - 0.96 - 0.2 + 2 * 1.2)) < 0.02, `tiled ${tiled(full).toFixed(3)} m²`);
});

test("roomEssentials names what each room's use needs and does not have", async () => {
  const { roomEssentials, tileWalls } = await import("../interior.js");
  const piece = (name, x, z, y = 0.015) => {
    const g = new THREE.Group();
    g.userData = { kind: "furniture", name };
    g.position.set(x, y, z);
    return g;
  };
  const bath = { name: "SDB", use: "bath", polygon: [[0, 0], [3, 0], [3, 2], [0, 2]] };
  const bedroom = { name: "Chambre", use: "bedroom", polygon: [[3.1, 0], [7, 0], [7, 4], [3.1, 4]] };
  const hall = { name: "Hall", use: "hall", polygon: [[0, 2.1], [3, 2.1], [3, 4], [0, 4]] };
  const root = new THREE.Group();
  root.add(floorPlan({ y: 0, height: 2.4, rooms: [bath, bedroom, hall] }));
  // a builder's own basin, the kit's bathtub, and a bath mat (not a bath); a bedside lamp is not a bed
  root.add(piece("Oak vanity and recessed washbasin", 0.4, 1.0), piece("bathMat", 1.5, 1.5), piece("bedside lamp", 5, 1));
  const lines = roomEssentials(root);
  assert.deepEqual(lines.slice(0, 2), [
    '"SDB" (bath): walls not tiled (tileWalls), no bath or shower (fx.bathtub, fx.shower), no towel rail with towels (fx.towelRail)',
    '"Chambre" (bedroom): no bed ("bed-oak-linen")',
  ]);
  assert.match(lines[2], /tag the ones you build yourself/);
  // completed: tiles, a bath, a towel rail on the wall (its origin on the wall line), a Martel bed
  root.add(tileWalls(root, bath, { y: 0 }), piece("bathtub", 1.5, 0.4), piece("towelRail", 2.99, 1.2, 0.25), piece("bed-oak-linen", 5, 2));
  assert.deepEqual(roomEssentials(root), []);
});
