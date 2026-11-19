// housekit/lens: the photographer's frame of a room (photo-<n>) is a shift lens on the walk's camera; nothing else may
// lose it or keep it (#129): ultra's samples jitter on top of the shift, and a room view after a photo view draws
// through the walk's own lens again.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import { jitterFrame, photoLens, unshift, walkLens, WALK_LENS } from "../lens.js";

const SIZE = new THREE.Vector2(1280, 720), BUFFER = [2560, 1440]; // the canvas and its drawing buffer at 2x

// a walk standing in a room whose floor is at 2.74 m, at its own lens
function walking() {
  const walk = { floorY: 2.74, eye: WALK_LENS.eye };
  const camera = new THREE.PerspectiveCamera(WALK_LENS.fov, SIZE.x / SIZE.y, 0.05, 500);
  camera.position.set(1, walk.floorY + walk.eye, 2);
  camera.lookAt(4, walk.floorY + walk.eye, -1);
  camera.updateMatrixWorld(true);
  return { walk, camera };
}

// where points of the room fall on the drawing buffer, in pixels
function onScreen(camera) {
  camera.updateMatrixWorld(true);
  return [[4, 3.2, -1], [3, 5.1, 0.5], [2.5, 2.9, -2], [5, 4.4, 1]].map((p) => {
    const v = new THREE.Vector3(...p).project(camera);
    return [((v.x + 1) / 2) * BUFFER[0], ((1 - v.y) / 2) * BUFFER[1]];
  });
}

test("a sub-pixel jitter keeps the frame it is given, a photographer's shift lens or none, and its undo gives it back", () => {
  for (const photo of [true, false]) {
    const { walk, camera } = walking();
    if (photo) photoLens(walk, camera, SIZE);
    const framed = onScreen(camera), projection = camera.projectionMatrix.clone();
    // ultra's samples after the first: up to half a pixel each way
    for (const [jx, jy] of [[0.25, -0.17], [-0.5, 0.33], [0.125, 0.44]]) {
      const undo = jitterFrame(camera, jx, jy, ...BUFFER);
      onScreen(camera).forEach(([x, y], i) => {
        assert.ok(Math.abs(x - framed[i][0] + jx) < 1e-6 && Math.abs(y - framed[i][1] + jy) < 1e-6,
          `${photo ? "photo" : "walk"}: moved by (${(x - framed[i][0]).toFixed(2)}, ${(y - framed[i][1]).toFixed(2)}) px, not (${-jx}, ${-jy})`);
      });
      undo();
      assert.equal(!!camera.view?.enabled, photo, photo ? "the shift lens stays on" : "no view offset left");
      assert.ok(camera.projectionMatrix.equals(projection), "the frame back as it was");
    }
  }
});

test("a room view after a photo view draws through the walk's own lens: its eye, its field of view, no shift", () => {
  const { walk, camera } = walking();
  const own = { y: camera.position.y, projection: camera.projectionMatrix.clone() };
  photoLens(walk, camera, SIZE);
  assert.ok(Math.abs(camera.position.y - (walk.floorY + 1.3)) < 1e-9 && camera.view?.enabled && camera.fov < WALK_LENS.fov);
  walkLens(walk, camera);
  assert.equal(walk.eye, WALK_LENS.eye);
  assert.equal(camera.fov, WALK_LENS.fov);
  assert.ok(!camera.view?.enabled, "no shift lens");
  assert.ok(Math.abs(camera.position.y - own.y) < 1e-9, `the eye at ${(camera.position.y - walk.floorY).toFixed(2)} m`);
  assert.ok(Math.abs(camera.aspect - SIZE.x / SIZE.y) < 1e-12, `drawn at ${camera.aspect.toFixed(3)}, not the canvas's ${(SIZE.x / SIZE.y).toFixed(3)}`);
  assert.ok(camera.projectionMatrix.equals(own.projection));
});

test("any view after a photo view (an elevation, a plan section) is drawn at the canvas's proportions", () => {
  const { walk, camera } = walking();
  photoLens(walk, camera, SIZE);
  unshift(camera);
  assert.ok(!camera.view?.enabled);
  assert.ok(Math.abs(camera.aspect - SIZE.x / SIZE.y) < 1e-12, `drawn at ${camera.aspect.toFixed(3)}`);
});
