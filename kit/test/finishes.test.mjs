// housekit/finishes: UVs in metres, and the plain fallback when textures are not loaded (node has none).
import { test } from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import { metricUV, finishMaterial } from "../finishes.js";
import fx from "../furnish.js";

test("metricUV gives each face of a box its size in metres", () => {
  const g = metricUV(new THREE.BoxGeometry(2, 0.5, 1));
  const uv = g.attributes.uv, nor = g.attributes.normal;
  const span = { x: [Infinity, -Infinity], y: [Infinity, -Infinity], z: [Infinity, -Infinity] };
  for (let i = 0; i < uv.count; i++) {
    const axis = Math.abs(nor.getY(i)) > 0.5 ? "y" : Math.abs(nor.getX(i)) > 0.5 ? "x" : "z";
    span[axis][0] = Math.min(span[axis][0], uv.getX(i));
    span[axis][1] = Math.max(span[axis][1], uv.getX(i));
  }
  assert.equal(span.y[1] - span.y[0], 2); // top: u along x
  assert.equal(span.x[1] - span.x[0], 1); // side: u along z
  assert.equal(span.z[1] - span.z[0], 2); // front: u along x
});

test("without loaded textures the finishes fall back to plain materials", () => {
  assert.equal(finishMaterial("oak-floor"), null);
  const m = fx.finish.oak();
  assert.ok(m.isMeshStandardMaterial && !m.map);
});
