// housekit/furnish: parametric pieces and wall placement, no renderer needed (scanned models need a browser).
import { test } from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import fx from "../furnish.js";

const room = [[0, 0], [4, 0], [4, 3], [0, 3]]; // x 0..4, z 0..3

function box(p) {
  p.updateMatrixWorld(true);
  return new THREE.Box3().setFromObject(p);
}

test("every parametric piece sits on the floor, tagged with its footprint", () => {
  for (const p of [fx.sofa(), fx.bed(), fx.nightstand(), fx.chair(), fx.diningSet(), fx.wardrobe(), fx.kitchenRun(), fx.wc(), fx.basin(), fx.bathtub(), fx.rug()]) {
    const b = box(p);
    assert.equal(p.userData.kind, "furniture");
    assert.ok(Math.abs(b.min.y) < 0.02, `${p.userData.name} bottom at ${b.min.y.toFixed(3)}`);
    const [w, d] = p.userData.footprint;
    const size = b.getSize(new THREE.Vector3());
    assert.ok(size.x <= w + 0.12 && size.z <= d + 0.12, `${p.userData.name} ${size.x.toFixed(2)}×${size.z.toFixed(2)} in ${w}×${d}`);
  }
});

test("onWall puts the back against the wall and the front into the room, on every wall and winding", () => {
  for (const poly of [room, [...room].reverse()]) {
    for (let edge = 0; edge < 4; edge++) {
      const a = poly[edge], b = poly[(edge + 1) % 4];
      const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
      const w = fx.wardrobe({ width: 1.0, depth: 0.6 });
      fx.onWall(poly, edge, len / 2, w, { gap: 0 });
      const bb = box(w);
      // inside the room
      assert.ok(bb.min.x > -0.01 && bb.max.x < 4.01 && bb.min.z > -0.01 && bb.max.z < 3.01, `edge ${edge}: out of the room`);
      // its back touches that wall
      const onWallLine = edge % 2 === 0
        ? Math.min(Math.abs(bb.min.z - a[1]), Math.abs(bb.max.z - a[1])) < 0.02
        : Math.min(Math.abs(bb.min.x - a[0]), Math.abs(bb.max.x - a[0])) < 0.02;
      assert.ok(onWallLine, `edge ${edge}: not against its wall`);
      // it faces into the room: its local +z points towards the room's centre
      const f = new THREE.Vector3(0, 0, 1).applyQuaternion(w.quaternion);
      const toCentre = new THREE.Vector3(2 - w.position.x, 0, 1.5 - w.position.z);
      assert.ok(f.dot(toCentre) > 0, `edge ${edge}: faces the wall`);
    }
  }
});

test("a kitchen run keeps its tall units out of the worktop and the sink out of a tall unit", () => {
  const k = fx.kitchenRun({ length: 3.6, tall: [{ at: 0.3, width: 0.6 }], sink: 0.3, hob: 2.0 });
  const b = box(k);
  assert.ok(b.max.y > 2.2 && b.max.y < 2.3, "tall unit to ~2.24 m");
  let steel = 0;
  k.traverse((o) => { if (o.isMesh && o.material === fx.finish.steel()) steel++; });
  assert.equal(steel, 0, "no sink inside the tall unit");
});
