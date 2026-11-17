// Planar mirrors (#41): a mirror shows the room in front of it, drawn again from the mirrored
// camera, instead of the room's light probe (a blurred capture from the room's centre).
//
// A mirror keeps its own material: the reflection takes the place of the environment's in its
// specular term, where the surface is polished. A bought mirror whose frame and glass share one
// material (a roughness texture tells them apart) keeps a matt frame, and the tint, the Fresnel and
// the fallback when the mirror is not drawn this frame all stay the material's.
//
// Cost: one extra render of the scene, only over the part of the screen the mirror covers, at the
// screen's resolution (half on a phone), for the one mirror most in view. The caller hides what the
// mirror cannot show (other rooms) for that render: a bathroom mirror draws the bathroom and the
// hall outside its door, a few dozen draw calls, not the house.

import * as THREE from "three";

/** A mesh that should reflect: the kit's mirror finish or a bought mirror (a material named "mirror"), or any polished metal plate. */
export function isMirror(o) {
  if (!o.isMesh || Array.isArray(o.material)) return false;
  const m = o.material;
  if (!m?.isMeshStandardMaterial) return false;
  const named = m.name === "mirror" && m.metalness >= 0.9;
  const polished = m.metalness >= 0.9 && m.roughness <= 0.03 && !m.roughnessMap;
  if (!named && !polished) return false;
  // flat: a plate, not a polished tap or rail
  const { thickness, width, height } = frame(o);
  return thickness <= 0.15 * Math.min(width, height) && width * height >= 0.03;
}

/** The plate's own axes: its thin axis (local), the thickness and the two other sides in metres. */
function frame(o) {
  o.geometry.computeBoundingBox();
  const size = o.geometry.boundingBox.getSize(new THREE.Vector3());
  const scale = new THREE.Vector3().setFromMatrixScale(o.matrixWorld);
  const dims = [size.x * scale.x, size.y * scale.y, size.z * scale.z];
  const thin = dims.indexOf(Math.min(...dims));
  const [width, height] = dims.filter((_, i) => i !== thin);
  return { thin, thickness: dims[thin], width, height };
}

const BIAS = new THREE.Matrix4().set(0.5, 0, 0, 0.5, 0, 0.5, 0, 0.5, 0, 0, 0.5, 0.5, 0, 0, 0, 1);

/**
 * The mirror's material, patched: the planar reflection (`tMirror`, projected with `mirrorMatrix`)
 * replaces the environment's radiance while `mirrorOn` is 1, fully on polished parts, not at all
 * on rough ones. A clone: each mirror has its own reflection.
 */
function mirrorMaterial(original) {
  const m = original.clone();
  const uniforms = { tMirror: { value: null }, mirrorMatrix: { value: new THREE.Matrix4() }, mirrorOn: { value: 0 } };
  const base = original.onBeforeCompile;
  const baseKey = original.customProgramCacheKey();
  m.onBeforeCompile = (shader, renderer) => {
    base.call(m, shader, renderer);
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nuniform mat4 mirrorMatrix;\nvarying vec4 vMirrorUv;")
      .replace("#include <project_vertex>", "#include <project_vertex>\n\tvMirrorUv = mirrorMatrix * modelMatrix * vec4( transformed, 1.0 );");
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", "#include <common>\nuniform sampler2D tMirror;\nuniform float mirrorOn;\nvarying vec4 vMirrorUv;")
      .replace("#include <lights_fragment_maps>", `#include <lights_fragment_maps>
	#if defined( RE_IndirectSpecular )
		radiance = mix( radiance, texture2DProj( tMirror, vMirrorUv ).rgb, mirrorOn * ( 1.0 - smoothstep( 0.08, 0.3, material.roughness ) ) );
	#endif`);
  };
  m.customProgramCacheKey = () => `${baseKey}|planar-mirror`;
  m.userData.mirror = uniforms;
  return m;
}

class Mirror {
  constructor(mesh, room, inside) {
    this.mesh = mesh;
    this.room = room;
    mesh.updateMatrixWorld(true);
    const { thin, thickness } = frame(mesh);
    const box = mesh.geometry.boundingBox;
    const local = new THREE.Vector3().setComponent(thin, 1);
    const normalMatrix = new THREE.Matrix3().getNormalMatrix(mesh.matrixWorld);
    this.normal = local.clone().applyMatrix3(normalMatrix).normalize();
    this.center = box.getCenter(new THREE.Vector3()).applyMatrix4(mesh.matrixWorld);
    this.thickness = thickness;
    this.corners = [];
    for (let i = 0; i < 8; i++) {
      this.corners.push(new THREE.Vector3(i & 1 ? box.max.x : box.min.x, i & 2 ? box.max.y : box.min.y, i & 4 ? box.max.z : box.min.z)
        .applyMatrix4(mesh.matrixWorld));
    }
    this.worldBox = new THREE.Box3().setFromPoints(this.corners);
    this.faceRoom(inside);
    this.fallback = mesh.material;
    mesh.material = mirrorMaterial(mesh.material);
    this.uniforms = mesh.material.userData.mirror;
  }

  /** Its front: the side facing into its room (a mirror hangs with its back to a wall). */
  faceRoom(inside) {
    const ahead = this.center.clone().addScaledVector(this.normal, 0.1);
    if (inside && !inside([ahead.x, ahead.z])) this.normal.negate();
    this.point = this.center.clone().addScaledVector(this.normal, this.thickness / 2);
  }
}

/**
 * The mirrors of a scene and the one render target they share (one mirror is drawn per frame):
 * `mirrors` = [{ mesh, room, inside([x, z]) }] (the room it hangs in, whether a point is in it).
 * `update` before the frame's render, `done` after it.
 */
export class Mirrors {
  constructor(renderer, mirrors, { scale = 1, samples = 4, reach = 10 } = {}) {
    this.renderer = renderer;
    this.list = mirrors.map(({ mesh, room, inside }) => new Mirror(mesh, room, inside));
    this.scale = scale;
    this.samples = samples;
    this.reach = reach;
    this.camera = new THREE.PerspectiveCamera();
    this.target = null;
    this.active = null;
    this.drawn = null;
    this.frustum = new THREE.Frustum();
    this.tmp = { m: new THREE.Matrix4(), v: new THREE.Vector3(), v4: new THREE.Vector4(), plane: new THREE.Plane(), q: new THREE.Vector4(),
      clip: new THREE.Vector4(), rot: new THREE.Matrix4(), look: new THREE.Vector3(), cam: new THREE.Vector3() };
  }

  get count() { return this.list.length; }

  /**
   * Draw the reflection of the mirror most in view, among those `accept` allows, with `hidden(mirror)`
   * hidden for that render. Returns the mirror drawn, or null.
   */
  update(scene, camera, { accept = () => true, hidden = () => [] } = {}) {
    this.done();
    this.drawn = null;
    const t = this.tmp;
    camera.updateMatrixWorld();
    const eye = t.cam.setFromMatrixPosition(camera.matrixWorld);
    this.frustum.setFromProjectionMatrix(t.m.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse));
    let best = null;
    for (const m of this.list) {
      if (!m.mesh.visible || !accept(m)) continue;
      const toEye = t.v.subVectors(eye, m.point);
      const facing = toEye.dot(m.normal);
      const distance = toEye.length();
      if (facing <= 0.01 || distance > this.reach || !this.frustum.intersectsBox(m.worldBox)) continue;
      // most in view: big, near, seen square on
      const score = (facing / distance) / (distance * distance);
      if (!best || score > best.score) best = { m, score };
    }
    if (!best) return null;
    return this.draw(best.m, scene, camera, hidden(best.m)) ? best.m : null;
  }

  draw(mirror, scene, camera, hide) {
    const { renderer, tmp: t } = this;
    const vc = this.camera;
    const size = renderer.getDrawingBufferSize(new THREE.Vector2());
    const W = size.x, H = size.y;
    // the mirrored camera (three's Reflector)
    const n = mirror.normal, p = mirror.point;
    const eye = t.cam.setFromMatrixPosition(camera.matrixWorld);
    t.rot.extractRotation(camera.matrixWorld);
    const view = t.v.subVectors(p, eye).reflect(n).negate().add(p);
    t.look.set(0, 0, -1).applyMatrix4(t.rot).add(eye);
    const target = new THREE.Vector3().subVectors(p, t.look).reflect(n).negate().add(p);
    vc.position.copy(view);
    vc.up.set(0, 1, 0).applyMatrix4(t.rot).reflect(n);
    vc.lookAt(target);
    vc.fov = camera.fov; vc.aspect = camera.aspect; vc.near = camera.near; vc.far = camera.far; vc.zoom = camera.zoom;
    vc.clearViewOffset();
    vc.updateMatrixWorld();
    vc.updateProjectionMatrix();
    // the part of the frame the mirror covers, seen from the mirrored camera: only that is drawn
    let x0 = W, y0 = H, x1 = 0, y1 = 0, behind = false;
    t.m.multiplyMatrices(vc.projectionMatrix, vc.matrixWorldInverse);
    for (const c of mirror.corners) {
      const h = t.v4.set(c.x, c.y, c.z, 1).applyMatrix4(t.m);
      if (h.w <= vc.near) { behind = true; break; }
      const sx = ((h.x / h.w + 1) / 2) * W, sy = ((1 - h.y / h.w) / 2) * H;
      x0 = Math.min(x0, sx); x1 = Math.max(x1, sx); y0 = Math.min(y0, sy); y1 = Math.max(y1, sy);
    }
    if (behind) { x0 = 0; y0 = 0; x1 = W; y1 = H; } // the camera beside the glass: the whole frame
    x0 = Math.max(0, Math.floor(x0) - 2); y0 = Math.max(0, Math.floor(y0) - 2);
    x1 = Math.min(W, Math.ceil(x1) + 2); y1 = Math.min(H, Math.ceil(y1) + 2);
    if (x1 - x0 < 2 || y1 - y0 < 2) return false;
    vc.setViewOffset(W, H, x0, y0, x1 - x0, y1 - y0);
    vc.updateProjectionMatrix();
    // one target at the frame's size (scaled), the rectangle drawn into its corner
    const RW = Math.max(1, Math.ceil(W * this.scale)), RH = Math.max(1, Math.ceil(H * this.scale));
    if (!this.target) {
      this.target = new THREE.WebGLRenderTarget(RW, RH, { type: THREE.HalfFloatType, samples: this.samples });
    } else if (this.target.width !== RW || this.target.height !== RH) {
      this.target.setSize(RW, RH);
    }
    const vw = Math.max(1, Math.round((x1 - x0) * this.scale)), vh = Math.max(1, Math.round((y1 - y0) * this.scale));
    this.target.viewport.set(0, 0, vw, vh);
    // where a point of the glass falls in that rectangle
    const u = mirror.uniforms;
    u.mirrorMatrix.value.makeScale(vw / RW, vh / RH, 1).multiply(BIAS).multiply(vc.projectionMatrix).multiply(vc.matrixWorldInverse);
    // clip at the glass: nothing behind the mirror (its wall, the next room) is drawn in it
    const plane = t.plane.setFromNormalAndCoplanarPoint(n, p).applyMatrix4(vc.matrixWorldInverse);
    const clip = t.clip.set(plane.normal.x, plane.normal.y, plane.normal.z, plane.constant);
    const e = vc.projectionMatrix.elements;
    t.q.set((Math.sign(clip.x) + e[8]) / e[0], (Math.sign(clip.y) + e[9]) / e[5], -1, (1 + e[10]) / e[14]);
    clip.multiplyScalar(2 / clip.dot(t.q));
    e[2] = clip.x; e[6] = clip.y; e[10] = clip.z + 1; e[14] = clip.w;
    vc.projectionMatrixInverse.copy(vc.projectionMatrix).invert();
    // draw
    const hidden = [mirror.mesh, ...hide].filter((o) => o.visible);
    for (const o of hidden) o.visible = false;
    const previous = renderer.getRenderTarget();
    renderer.setRenderTarget(this.target);
    renderer.state.buffers.depth.setMask(true);
    renderer.clear();
    renderer.render(scene, vc);
    renderer.setRenderTarget(previous);
    for (const o of hidden) o.visible = true;
    u.tMirror.value = this.target.texture;
    u.mirrorOn.value = 1;
    this.active = mirror;
    this.drawn = { room: mirror.room?.name, size: [vw, vh] }; // debugging: what the last frame drew
    return true;
  }

  /** After the frame: the mirror falls back to its material's own reflection (light probes, other cameras). */
  done() {
    if (this.active) this.active.uniforms.mirrorOn.value = 0;
    this.active = null;
  }

  dispose() {
    this.done();
    for (const m of this.list) {
      m.mesh.material.dispose();
      m.mesh.material = m.fallback;
    }
    this.target?.dispose();
  }
}
