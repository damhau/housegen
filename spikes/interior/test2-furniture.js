// Ground-floor furniture, following the furniture drawn on sheet 3.
import * as THREE from "three";
import fx from "housekit/furnish";
import { LEVEL } from "./dimensions.js";

const Y = LEVEL.ground + 0.015; // on the finished floor
const CEIL = LEVEL.ground + 2.5;
const R = {
  salon: [[-1.275, 1.61], [5.1, 1.61], [5.1, 4.6], [-1.275, 4.6]],
  kitchen: [[-5.1, -2.45], [-1.275, -2.45], [-1.275, 4.6], [-5.1, 4.6]],
  chambre: [[2.105, -4.6], [5.1, -4.6], [5.1, -1.1], [3.81, -1.1], [3.81, -0.5], [0.125, -0.5], [0.125, -2.11], [2.105, -2.11]],
  sdb: [[0.125, -4.6], [2.005, -4.6], [2.005, -2.21], [0.125, -2.21]],
  wc: [[-1.025, -0.98], [-0.125, -0.98], [-0.125, 1.51], [-1.025, 1.51]],
  armoireChambre: [[3.91, -1.0], [5.1, -1.0], [5.1, -0.5], [3.91, -0.5]],
  armoireHall: [[3.91, 0.95], [5.1, 0.95], [5.1, 1.51], [3.91, 1.51]],
};

// A light fitting on the ceiling at (x, z): the globe pendant hangs its full length (0.95 m), else a
// flush disc. Each gets a warm point light where the lamp glows.
async function light(group, x, z, kind = "flush") {
  const p = kind === "pendant" ? await fx.model("pendant-globe") : fx.ceilingLight();
  // the pendant's cord runs 0.28 m up into the slab (hidden, the floor above is at +0.30) so the
  // globe clears heads at ~2.0 m
  const lift = kind === "pendant" ? 0.28 : 0;
  p.position.set(x, CEIL + lift, z);
  group.add(p);
  const bulb = new THREE.PointLight("#ffd9a8", kind === "pendant" ? 2.0 : 1.4, 7, 2);
  bulb.position.set(x, CEIL + lift - (kind === "pendant" ? p.userData.height - 0.12 : 0.12), z);
  group.add(bulb);
}

export async function buildFurniture({ group }) {
  const g = new THREE.Group();
  group.add(g);
  const add = (p) => { p.position.y += Y; g.add(p); return p; };

  // Salon: sofa facing east, rug and coffee table, two armchairs, the sideboard on the east wall
  add(fx.place(await fx.model("sofa-grey-cushions"), [0.5, 3.35], Math.PI / 2)); // 2.0 m: 0.7 m passage at its north end
  add(fx.place(fx.rug({ width: 2.6, depth: 2.0 }), [2.15, 3.1], Math.PI / 2));
  add(fx.place(await fx.model("coffee-table-oak"), [1.85, 3.1], Math.PI / 2));
  // armchairs south of the hall door's approach (the door is x 2.69–3.57 on z 1.56)
  add(fx.place(await fx.model("armchair-oak-leather"), [3.55, 2.95], -Math.PI / 2 - 0.25));
  add(fx.place(await fx.model("armchair-oak-leather"), [3.55, 4.0], -Math.PI / 2 + 0.25));
  add(fx.onWall(R.salon, 1, 1.5, await fx.model("sideboard-walnut")));
  add(fx.place(await fx.model("side-table-oak"), [0.45, 4.3], Math.PI / 2));
  add(fx.onWall(R.salon, 0, 3.375, await fx.model("cube-shelf-oak"))); // x 1.56–2.64, clear of the passage past the sofa
  add(fx.place(await fx.model("plant-large"), [4.7, 4.25]));
  add(fx.place(await fx.model("vase-white"), [4.75, 2.2], 0)).position.y += 0.68; // on the sideboard
  await light(g, 2.0, 3.1, "pendant");

  // Kitchen along the north wall (87 tall | 210 run | 86 tall), dining table for eight
  add(fx.onWall(R.kitchen, 0, 3.825 / 2, fx.kitchenRun({
    length: 3.825, tall: [{ at: 0.435, width: 0.87 }, { at: 3.395, width: 0.86 }], hob: 1.55, sink: 2.55, worktop: "oak",
  })));
  add(fx.place(fx.diningSet({ length: 2.4, width: 1.0, seats: 8 }), [-3.4, 2.6]));
  await light(g, -3.4, 2.0, "pendant");
  await light(g, -3.4, 3.2, "pendant");
  add(fx.place(await fx.model("plant-large"), [-4.7, 4.2]));

  // Bedroom: bed against the north wall, nightstands, the wardrobe in the dressing bay
  // the scanned bed has its own wall-mounted headboard with bedside shelves (2.74 m wide)
  add(fx.onWall(R.chambre, 0, 1.5, await fx.model("bed-messy-grey")));
  add(fx.onWall(R.chambre, 5, 3.81 - 1.065, fx.wardrobe({ width: 1.86, depth: 0.55 })));
  await light(g, 3.6, -2.9);

  // Closets
  add(fx.onWall(R.armoireChambre, 0, 0.595, fx.wardrobe({ width: 1.17, depth: 0.48 })));
  add(fx.onWall(R.armoireHall, 2, 0.595, fx.wardrobe({ width: 1.17, depth: 0.5 })));

  // Bathroom: bath on the north wall, WC and basin on the west wall
  add(fx.onWall(R.sdb, 0, 0.94, fx.bathtub({ length: 1.84, width: 0.75 })));
  add(fx.onWall(R.sdb, 3, 0.55, fx.wc()));
  add(fx.onWall(R.sdb, 3, 1.3, fx.basin({ width: 0.7 })));
  await light(g, 1.06, -3.2);

  // WC: pan on the north wall, a small basin on the west wall
  add(fx.onWall(R.wc, 0, 0.45, fx.wc()));
  add(fx.onWall(R.wc, 3, 1.25, fx.basin({ width: 0.45, depth: 0.3, vanity: false })));

  // halls
  await light(g, 3.0, 0.2);
  await light(g, -1.9, -3.6);
  await light(g, -0.575, 0.3);
}
