// housekit runtime — boots a three.js scene around a user-provided buildScene()
// and exposes a small control surface for the app (postMessage) and for the
// headless renderer (window.__house).
//
// Query parameters:
//   ?view=north|south|east|west|aerial|<custom>   initial camera
//   ?headless=1                                    no controls animation, deterministic
//   ?quality=low|medium|high                       low: no shadows/effects · medium: shadows, env light
//                                                  high: + ambient occlusion + anti-aliasing (final look)
//   ?w=1280&h=800                                  canvas size (headless)

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { GTAOPass } from "three/addons/postprocessing/GTAOPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { SMAAPass } from "three/addons/postprocessing/SMAAPass.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import * as house from "housekit";

const params = new URLSearchParams(location.search);
const HEADLESS = params.get("headless") === "1";
const QUALITY = params.get("quality") ?? (HEADLESS ? "high" : "medium");
// ambient occlusion + anti-aliasing: the final look. Interactive pages fall back to plain
// rendering automatically when the machine cannot sustain it (see the frame-time guard).
const EFFECTS = QUALITY === "high";

const PALETTE = {
  sky: "#d9e0e4",
  grass: "#8a9a68",
  sun: "#fff3e0",
};

const state = {
  ready: false,
  errors: [],
  views: {},
  camera: null,
  controls: null,
  renderer: null,
  composer: null,
  scene: null,
  houseGroup: null,
  bounds: null,
};

window.__house = {
  get ready() { return state.ready; },
  get errors() { return state.errors; },
  listViews: () => Object.keys(state.views),
  setView: (name) => setView(name),
  renderOnce: () => renderFrame(),
};

function recordError(msg) {
  state.errors.push(String(msg));
  try { parent.postMessage({ type: "house:error", message: String(msg) }, "*"); } catch { /* noop */ }
}
window.addEventListener("error", (e) => recordError(e.message || e.error));
window.addEventListener("unhandledrejection", (e) => recordError(e.reason?.message ?? e.reason));

/** Subtle tileable noise so large flat surfaces (lawn) do not read as plastic. */
function noiseTexture(size = 256, base = 200, spread = 26, seed = 1, repeat = 40) {
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const ctx = c.getContext("2d");
  const img = ctx.createImageData(size, size);
  let s = seed >>> 0 || 1;
  const rnd = () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
  for (let i = 0; i < img.data.length; i += 4) {
    const v = base + (rnd() - 0.5) * spread;
    img.data[i] = img.data[i + 1] = img.data[i + 2] = v;
    img.data[i + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  const tex = new THREE.CanvasTexture(c);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(repeat, repeat);
  return tex;
}

export async function boot(buildScene) {
  const canvas = document.getElementById("view") ?? Object.assign(document.createElement("canvas"), { id: "view" });
  if (!canvas.isConnected) document.body.appendChild(canvas);

  const W = Number(params.get("w")) || window.innerWidth;
  const H = Number(params.get("h")) || window.innerHeight;

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: !EFFECTS, preserveDrawingBuffer: HEADLESS });
  renderer.setPixelRatio(QUALITY === "low" ? 1 : Math.min(window.devicePixelRatio, 2));
  renderer.setSize(W, H, false);
  renderer.shadowMap.enabled = QUALITY !== "low";
  renderer.shadowMap.type = QUALITY === "high" ? THREE.PCFSoftShadowMap : THREE.PCFShadowMap;
  if (HEADLESS) {
    // Shadows depend on the sun and the geometry, not on the camera: with software GL they are
    // the expensive part, so compute them once for the first frame and reuse them for every view.
    renderer.shadowMap.autoUpdate = false;
    renderer.shadowMap.needsUpdate = true;
  }
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 0.95;
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(PALETTE.sky);
  scene.fog = new THREE.Fog(PALETTE.sky, 55, 150);

  const camera = new THREE.PerspectiveCamera(42, W / H, 0.1, 500);
  camera.position.set(18, 10, 18);

  // lights: sun + sky/ground hemisphere + a soft environment for the PBR materials
  const hemi = new THREE.HemisphereLight("#e8eef5", "#6f7a5a", 0.55);
  scene.add(hemi);
  const sun = new THREE.DirectionalLight(PALETTE.sun, 2.0);
  sun.position.set(-18, 28, 12);
  sun.castShadow = renderer.shadowMap.enabled;
  const shadowRes = QUALITY === "high" ? 4096 : 2048;
  sun.shadow.mapSize.set(shadowRes, shadowRes);
  sun.shadow.camera.left = -40; sun.shadow.camera.right = 40;
  sun.shadow.camera.top = 40; sun.shadow.camera.bottom = -40;
  sun.shadow.camera.near = 1; sun.shadow.camera.far = 120;
  sun.shadow.bias = -0.0004;
  sun.shadow.normalBias = 0.02;
  scene.add(sun);
  if (QUALITY !== "low") {
    const pmrem = new THREE.PMREMGenerator(renderer);
    scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    scene.environmentIntensity = 0.35;
    pmrem.dispose();
  } else {
    scene.add(new THREE.AmbientLight("#ffffff", 0.2));
  }

  // base ground: lawn with a little grain
  const grass = house.mat.grass(PALETTE.grass);
  grass.map = noiseTexture(256, 205, 30, 3, 60);
  grass.map.colorSpace = THREE.SRGBColorSpace;
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(400, 400), grass);
  ground.rotation.x = -Math.PI / 2;
  ground.receiveShadow = true;
  ground.position.y = -0.01;
  scene.add(ground);

  const houseGroup = new THREE.Group();
  houseGroup.name = "house";
  scene.add(houseGroup);

  Object.assign(state, { renderer, scene, camera, houseGroup });

  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = !HEADLESS;
  controls.maxPolarAngle = Math.PI / 2 - 0.02;
  controls.target.set(0, 2, 0);
  state.controls = controls;

  // post-processing (final look): ambient occlusion + anti-aliasing
  if (EFFECTS) {
    const composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    const gtao = new GTAOPass(scene, camera, W, H);
    gtao.output = GTAOPass.OUTPUT.Default;
    gtao.blendIntensity = 0.9;
    gtao.updateGtaoMaterial({ radius: 0.6, distanceExponent: 1, thickness: 1, scale: 1, samples: QUALITY === "high" ? 16 : 8, distanceFallOff: 1, screenSpaceRadius: false });
    composer.addPass(gtao);
    composer.addPass(new OutputPass());
    composer.addPass(new SMAAPass(W, H));
    state.composer = composer;
    state.gtao = gtao;
  }

  // ---- user scene ----
  let result = null;
  try {
    result = await buildScene({ THREE, scene, house, group: houseGroup, sun, ground, renderer, camera });
  } catch (err) {
    recordError(`buildScene failed: ${err?.stack ?? err}`);
  }

  // ---- views ----
  houseGroup.updateMatrixWorld(true);
  const bounds = framingBounds(houseGroup);
  if (bounds.isEmpty()) bounds.set(new THREE.Vector3(-5, 0, -5), new THREE.Vector3(5, 6, 5));
  state.bounds = bounds;
  const center = bounds.getCenter(new THREE.Vector3());
  const size = bounds.getSize(new THREE.Vector3());
  const r = Math.max(size.x, size.z, size.y * 1.2) * 1.15 + 4;
  const eyeY = center.y + size.y * 0.35 + 1.2;
  const c = center;
  const look = new THREE.Vector3(c.x, c.y * 0.8, c.z);
  state.views = {
    // the view is named after the façade the camera LOOKS AT
    north: { pos: [c.x, eyeY, c.z - r], target: look },
    south: { pos: [c.x, eyeY, c.z + r], target: look },
    east: { pos: [c.x + r, eyeY, c.z], target: look },
    west: { pos: [c.x - r, eyeY, c.z], target: look },
    northeast: { pos: [c.x + r * 0.75, eyeY, c.z - r * 0.75], target: look },
    northwest: { pos: [c.x - r * 0.75, eyeY, c.z - r * 0.75], target: look },
    southeast: { pos: [c.x + r * 0.75, eyeY, c.z + r * 0.75], target: look },
    southwest: { pos: [c.x - r * 0.75, eyeY, c.z + r * 0.75], target: look },
    aerial: { pos: [c.x + r * 0.9, c.y + r * 1.1, c.z + r * 0.9], target: c },
    top: { pos: [c.x + 0.01, c.y + r * 1.8, c.z], target: c },
  };
  if (result?.views) {
    for (const [name, v] of Object.entries(result.views)) {
      state.views[name] = { pos: v.position, target: new THREE.Vector3(...(v.target ?? [c.x, c.y, c.z])) };
    }
  }

  await setView(params.get("view") ?? "southeast");

  if (HEADLESS) {
    renderFrame();
    state.ready = true;
    try { parent.postMessage({ type: "house:ready" }, "*"); } catch { /* noop */ }
    return;
  }

  window.addEventListener("resize", () => {
    const w = window.innerWidth, h = window.innerHeight;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h, false);
    state.composer?.setSize(w, h);
    state.needsRender = true;
  });
  window.addEventListener("message", (e) => {
    const d = e.data ?? {};
    if (d.type === "house:setView") setView(d.view);
  });
  state.ready = true;
  try { parent.postMessage({ type: "house:ready", views: Object.keys(state.views) }, "*"); } catch { /* noop */ }
  // Render on demand: only when the camera moves (or something asks for a frame). An idle page
  // costs nothing, and machines without a GPU stay responsive.
  state.needsRender = true;
  controls.addEventListener("change", () => { state.needsRender = true; });
  // frame-time guard: if the first frames with effects are slow (no GPU, weak laptop),
  // drop the post-processing rather than freeze the page.
  let frames = 0, slowMs = 0;
  renderer.setAnimationLoop(() => {
    const moved = controls.update();
    if (!moved && !state.needsRender) return;
    state.needsRender = false;
    const t0 = performance.now();
    renderFrame();
    if (state.composer && frames < 8) {
      const dt = performance.now() - t0;
      slowMs += dt;
      frames += 1;
      // one frame over 250 ms (< 4 fps) or 8 frames averaging over 90 ms: not sustainable
      if (dt > 250 || (frames === 8 && slowMs / frames > 90)) {
        state.composer = null;
        state.needsRender = true; // redraw once without effects
        console.warn(`housekit: effects disabled, ${Math.round(dt)} ms frame on this machine`);
        try { parent.postMessage({ type: "house:effects", enabled: false }, "*"); } catch { /* noop */ }
      }
    }
  });
}

/**
 * Bounding box used to frame the named views: the house and its immediate site, not the
 * terrain sheet or anything flagged `userData.excludeFromBounds`, which would push the
 * camera to the horizon.
 */
function framingBounds(root) {
  const box = new THREE.Box3();
  const tmp = new THREE.Box3();
  root.traverse((o) => {
    if (o.userData?.kind === "terrain" || o.userData?.excludeFromBounds) return;
    if (!o.isMesh) return;
    let p = o.parent;
    while (p && p !== root) { if (p.userData?.excludeFromBounds) return; p = p.parent; }
    tmp.setFromObject(o);
    if (!tmp.isEmpty()) box.union(tmp);
  });
  return box;
}

function renderFrame() {
  const { renderer, scene, camera, composer } = state;
  if (!renderer) return;
  if (composer) composer.render();
  else renderer.render(scene, camera);
}

async function setView(name) {
  const v = state.views[name];
  if (!v) {
    recordError(`unknown view: ${name}. Known: ${Object.keys(state.views).join(", ")}`);
    return false;
  }
  state.camera.position.set(...v.pos);
  state.controls.target.copy(v.target);
  state.controls.update();
  state.camera.lookAt(v.target);
  renderFrame();
  state.needsRender = true; // the interactive loop draws the settled frame
  return true;
}
