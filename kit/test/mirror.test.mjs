// housekit/mirror: which meshes reflect the room while walking, and where the reflection is drawn
// (the drawing itself needs a browser).
import { test } from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import fx from "../furnish.js";
import { isMirror, Mirrors } from "../mirror.js";

function mirrorsIn(p) {
  p.updateMatrixWorld(true);
  const found = [];
  p.traverse((o) => { if (isMirror(o)) found.push(o); });
  return found;
}

test("the basin's mirror reflects; chrome taps, rails and the shower glass do not", () => {
  assert.equal(mirrorsIn(fx.basin()).length, 1);
  assert.equal(mirrorsIn(fx.basin({ mirror: false })).length, 0);
  for (const p of [fx.towelRail(), fx.shower(), fx.bathAccessories(), fx.wc(), fx.kitchenRun()]) {
    assert.deepEqual(mirrorsIn(p), [], p.userData.name);
  }
});

test("a mirror is a plate: the mirror finish on a thin panel, stretched or not; not on a ball or a sliver", () => {
  const plate = new THREE.Mesh(new THREE.BoxGeometry(0.6, 0.9, 0.01), fx.finish.mirror());
  assert.ok(mirrorsIn(plate).length === 1);
  const stretched = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), fx.finish.mirror());
  stretched.scale.set(0.5, 1.2, 0.012);
  assert.ok(mirrorsIn(stretched).length === 1);
  assert.equal(mirrorsIn(new THREE.Mesh(new THREE.SphereGeometry(0.3), fx.finish.mirror())).length, 0);
  assert.equal(mirrorsIn(new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.1, 0.005), fx.finish.mirror())).length, 0);
  // a polished metal plate of the builder's own reflects too
  const own = new THREE.Mesh(new THREE.PlaneGeometry(0.5, 0.7), new THREE.MeshStandardMaterial({ metalness: 1, roughness: 0 }));
  assert.equal(mirrorsIn(own).length, 1);
});

const W = 1280, H = 720;
const fakeRenderer = {
  getDrawingBufferSize: (v) => v.set(W, H),
  getRenderTarget: () => null,
  setRenderTarget() {},
  clear() {},
  render() {},
  state: { buffers: { depth: { setMask() {} } } },
};

/** The share of the glass the camera sees whose reflection falls outside the rectangle drawn for it (it would draw black). */
function glassOutsideReflection(camera) {
  const glass = new THREE.Mesh(new THREE.BoxGeometry(0.6, 0.7, 0.012), fx.finish.mirror());
  glass.position.set(1.685, 6.895, 1.236); // TestVillaGille's attic bath (#115): on a wall at z 1.24, the room north of it
  glass.updateMatrixWorld(true);
  const mirrors = new Mirrors(fakeRenderer, [{ mesh: glass, room: { name: "bath" }, inside: ([, z]) => z < 1.23 }]);
  camera.updateMatrixWorld(true);
  const drawn = mirrors.update(new THREE.Scene(), camera);
  if (!drawn) return null;
  const [vw, vh] = mirrors.drawn.size, umax = vw / mirrors.target.width, vmax = vh / mirrors.target.height;
  const toScreen = new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
  const toMirror = drawn.uniforms.mirrorMatrix.value, eps = 1e-3;
  let seen = 0, outside = 0;
  for (let i = 0; i <= 20; i++) {
    for (let j = 0; j <= 20; j++) {
      const p = new THREE.Vector4(1.385 + 0.6 * (i / 20), 6.545 + 0.7 * (j / 20), 1.23, 1);
      const s = p.clone().applyMatrix4(toScreen);
      if (s.w <= 0 || Math.abs(s.x / s.w) > 1 || Math.abs(s.y / s.w) > 1) continue;
      seen++;
      const m = p.clone().applyMatrix4(toMirror), u = m.x / m.w, v = m.y / m.w;
      if (u < -eps || u > umax + eps || v < -eps || v > vmax + eps) outside++;
    }
  }
  return seen ? outside / seen : null;
}

test("the reflection covers all the glass the camera sees, through a photo's shift lens too (#115)", () => {
  let checked = 0;
  for (const lens of ["walk", "photo", "jitter"]) {
    for (let yaw = -80; yaw <= 80; yaw += 10) {
      const camera = new THREE.PerspectiveCamera(70, W / H, 0.1, 2000);
      camera.position.set(1.085, 6.795, -0.09);
      camera.rotation.set(0, THREE.MathUtils.degToRad(-90 + yaw), 0, "YXZ"); // yaw 0: looking east, the mirror to the right
      if (lens === "photo") {
        // runtime.js photoFrame: a 53° frame shifted down 12 % inside a taller one
        camera.fov = THREE.MathUtils.radToDeg(2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(53) / 2) * 1.24));
        camera.setViewOffset(W, H * 1.24, 0, H * 0.24, W, H);
      } else if (lens === "jitter") {
        camera.setViewOffset(W, H, 0.4, -0.3, W, H);
      }
      camera.updateProjectionMatrix();
      const share = glassOutsideReflection(camera);
      if (share === null) continue;
      checked++;
      assert.equal(share, 0, `${lens}, yaw ${yaw}°: ${Math.round(share * 100)} % of the glass outside its reflection`);
    }
  }
  assert.ok(checked >= 12, `only ${checked} views saw the mirror`);
});
