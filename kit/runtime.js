// housekit runtime — boots a three.js scene around a user-provided buildScene()
// and exposes a small control surface for the app (postMessage) and for the
// headless renderer (window.__house).
//
// Query parameters:
//   ?view=north|south|east|west|aerial|<custom>   initial camera (also <side>-photo, <side>-elevation)
//   ?headless=1                                    no controls animation, deterministic
//   ?quality=low|medium|high                       low: no shadows/effects · medium: shadows, env light
//                                                  high: + ambient occlusion + anti-aliasing (final look)
//   ?w=1280&h=800                                  canvas size (headless)
//   ?eye_height=1.6&distance=1&azimuth=0&fov=50&target_height=3
//                                                  camera overrides applied to every side view of this page
//                                                  (render_views tool): eye/target in metres above the house
//                                                  base, distance as a factor of the auto-frame radius,
//                                                  azimuth in degrees (0 = looking at the north façade, clockwise)

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { Sky } from "three/addons/objects/Sky.js";
import { CSM } from "three/addons/csm/CSM.js";
import { SSRPass } from "three/addons/postprocessing/SSRPass.js";
// vendored by kit/scripts/vendor.mjs (npm postinstall); relative paths, so a scene's
// index.html needs no importmap entry for them (projects keep the template of their day)
import { EffectComposer, RenderPass, EffectPass, SMAAEffect, SMAAPreset, VignetteEffect, ToneMappingEffect, ToneMappingMode, Pass } from "./vendor/postprocessing/index.js";
import { N8AOPostPass } from "./vendor/n8ao/N8AO.js";
import * as house from "housekit";

const params = new URLSearchParams(location.search);
const HEADLESS = params.get("headless") === "1";
const QUALITY = params.get("quality") ?? (HEADLESS ? "high" : "medium");
// ambient occlusion (N8AO) + SMAA + a light vignette through the pmndrs postprocessing
// composer: the final look. Interactive pages fall back to plain rendering automatically when
// the machine cannot sustain it (see the frame-time guard).
const EFFECTS = QUALITY === "high";
// cascaded shadow maps (#17): interactive (GPU) pages only. The headless renderer keeps its
// single 4096 map computed once per page and reused across views: CSM re-renders three
// cascades for every camera, far more than the +20 % the software renderer can afford.
const USE_CSM = !HEADLESS && QUALITY !== "low";
// interactive-only extras (#20): instanced grass around the house and screen-space reflections
// on glass. GPU pages at quality=high only, dropped with the effects by the frame-time guard;
// the headless renderer (the critic's eyes) never sees them. ?extras=0 switches them off.
const EXTRAS = !HEADLESS && EFFECTS && params.get("extras") !== "0";
const TONE_MAPPING_EXPOSURE = 1.05;
const DEFAULT_FOV = 42; // the elevated auto-framed views
const PHOTO_FOV = 50; // the "-photo" views: a person with a phone
// the "-elevation" views: a straight-on camera far away with a narrow field of view, so the
// façade reads like an architect's elevation drawing (perspective under a few percent) without
// swapping the perspective camera the controls and the post-processing passes hold
const ELEVATION_FOV = 4;
// camera azimuth of the side views, degrees clockwise from north (camera north of the house, looking south)
const AZIMUTH = { north: 0, northeast: 45, east: 90, southeast: 135, south: 180, southwest: 225, west: 270, northwest: 315 };
const CAMERA_OVERRIDES = readCameraOverrides(params);

function readCameraOverrides(p) {
  const o = {};
  for (const k of ["eye_height", "distance", "azimuth", "fov", "target_height"]) {
    const v = p.get(k);
    if (v !== null && v !== "" && Number.isFinite(Number(v))) o[k] = Number(v);
  }
  return Object.keys(o).length ? o : null;
}

const PALETTE = {
  sky: "#d9e0e4", // fallback clear colour (quality=low, no sky dome)
  grass: "#8a9a68",
};

// Sun presets (#16): elevation / azimuth in degrees, azimuth clockwise from north = where the
// sun IS (180 = due south, 225 = south-west: a pleasant late afternoon, the default).
// buildScene may return { sun: { elevation, azimuth } } or { time: "morning"|"noon"|"evening" }.
const SUN_PRESETS = {
  morning: { elevation: 28, azimuth: 120 },
  noon: { elevation: 58, azimuth: 180 },
  afternoon: { elevation: 36, azimuth: 225 },
  evening: { elevation: 16, azimuth: 262 },
};
const SKY_RADIUS = 300; // inside every camera's far plane (default 500; elevation views raise it)

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
  // debugging: what the fixed views frame (building = tagged walls/openings, or the site when none)
  get frame() {
    const f = state.frame;
    if (!f) return null;
    const b = (box) => ({ min: box.min.toArray(), max: box.max.toArray() });
    return { building: b(f.building), site: b(f.bounds), aspect: state.camera?.aspect };
  },
  renderOnce: () => renderFrame(),
  // deterministic plausibility audit of the built scene (see house.audit): the renderer
  // appends it to the builder's tool results and gives it to the critic
  audit: () => (state.houseGroup ? house.audit(state.houseGroup) : []),
};

function recordError(msg) {
  state.errors.push(String(msg));
  try { parent.postMessage({ type: "house:error", message: String(msg) }, "*"); } catch { /* noop */ }
}
window.addEventListener("error", (e) => recordError(e.message || e.error));
window.addEventListener("unhandledrejection", (e) => recordError(e.reason?.message ?? e.reason));

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
  // AgX: soft highlights, no plastic whites on plaster. The composer's OutputPass applies the
  // renderer's tone mapping too, so both paths (effects on / off) look the same.
  renderer.toneMapping = THREE.AgXToneMapping;
  renderer.toneMappingExposure = TONE_MAPPING_EXPOSURE;
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(PALETTE.sky);
  scene.fog = new THREE.Fog(PALETTE.sky, 55, 150);
  state.fog = scene.fog;

  const camera = new THREE.PerspectiveCamera(DEFAULT_FOV, W / H, 0.1, 500);
  camera.position.set(18, 10, 18);

  // lights: the sun (set from the sky below, once buildScene has said where it wants it) and
  // a sky/ground hemisphere; the environment map comes from the physical sky
  const hemi = new THREE.HemisphereLight("#e8eef5", "#6f7a5a", 0.35);
  scene.add(hemi);
  const sun = new THREE.DirectionalLight("#fff3e0", 2.0);
  sun.castShadow = renderer.shadowMap.enabled;
  const shadowRes = QUALITY === "high" ? 4096 : 2048;
  sun.shadow.mapSize.set(shadowRes, shadowRes);
  sun.shadow.camera.left = -40; sun.shadow.camera.right = 40;
  sun.shadow.camera.top = 40; sun.shadow.camera.bottom = -40;
  sun.shadow.camera.near = 1; sun.shadow.camera.far = 120;
  sun.shadow.bias = -0.0004;
  sun.shadow.normalBias = 0.02;
  scene.add(sun);
  if (QUALITY === "low") scene.add(new THREE.AmbientLight("#ffffff", 0.25));

  // base ground: a tiled lawn (kit texture) that the terrain component replaces when used
  const grass = house.mat.grass({ texture: "lawn", scale: 3 });
  const ground = new THREE.Mesh(house.uvsInMetres(new THREE.PlaneGeometry(400, 400), 400, 400), grass);
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

  // post-processing (final look): N8AO ambient occlusion, SMAA, a light vignette, AgX. The
  // composer owns tone mapping (its ToneMappingEffect, same exposure), so the renderer's is
  // switched off while it runs and restored if the frame-time guard drops the effects.
  if (EFFECTS) {
    renderer.toneMapping = THREE.NoToneMapping;
    const composer = new EffectComposer(renderer, { frameBufferType: THREE.HalfFloatType });
    composer.addPass(new RenderPass(scene, camera));
    // screen-space reflections on glass, half resolution, before the AO so it shades them too
    // (the pass re-renders the scene for its beauty/normal buffers: GPU pages only)
    if (EXTRAS) state.ssrSlot = composer.passes.length; // filled in after buildScene (needs the glass)
    const ao = new N8AOPostPass(scene, camera, W, H);
    ao.configuration.aoRadius = 1.2;
    ao.configuration.distanceFalloff = 0.6;
    ao.configuration.intensity = 2.5;
    ao.configuration.aoSamples = HEADLESS ? 8 : 16;
    ao.configuration.denoiseSamples = HEADLESS ? 4 : 8;
    ao.configuration.denoiseRadius = 6;
    ao.configuration.halfRes = HEADLESS; // software GL: a quarter of the AO fragments
    ao.configuration.screenSpaceRadius = false;
    composer.addPass(ao);
    composer.addPass(
      new EffectPass(
        camera,
        new SMAAEffect({ preset: SMAAPreset.HIGH }),
        new VignetteEffect({ offset: 0.35, darkness: 0.22 }),
        new ToneMappingEffect({ mode: ToneMappingMode.AGX }),
      ),
    );
    state.composer = composer;
    state.ao = ao;
  }

  // ---- user scene ----
  // trees: full detail for the saved version (quality=high), lighter for in-loop renders (#15)
  house.setVegetationDetail(QUALITY === "high" ? "high" : "low");
  let result = null;
  try {
    result = await buildScene({ THREE, scene, house, group: houseGroup, sun, ground, renderer, camera });
  } catch (err) {
    recordError(`buildScene failed: ${err?.stack ?? err}`);
  }
  // sky, sun and environment: deterministic, from the scene's choice (or the default afternoon)
  setupSky(scene, renderer, sun, sunOptions(result));
  if (USE_CSM) setupCSM(scene, camera, sun);
  if (EXTRAS) setupExtras(scene, renderer, camera, houseGroup, W, H);
  // textures load asynchronously: the first (headless) frame must not race them
  try { await house.texturesReady(); } catch { /* a missing map is only a look problem */ }

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
  const building = buildingBounds(houseGroup);
  state.frame = { c, size, r, bounds, building: building.isEmpty() ? bounds : building, aspect: W / H };
  state.views = {
    // the view is named after the façade the camera LOOKS AT. These are elevated wide shots
    // (eye ~4 m for two storeys, whole building in frame): right for massing and roofs, wrong
    // for judging heights against a photo — that is what the "-photo" views below are for.
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
  // photo-like views: a person standing at 1.6 m in front of the façade, looking at its mid
  // height, the façade filling ~85 % of the frame width, 50° fov — the viewpoint of the photos.
  // Elevation views: straight on, near-orthographic, the counterpart of the elevation sheets.
  // Both depend on the frame's aspect, so they are computed when selected (a resized
  // interactive window still frames the façade).
  for (const side of ["north", "south", "east", "west"]) {
    state.views[`${side}-photo`] = () => photoView(side);
    state.views[`${side}-elevation`] = () => elevationView(side);
  }
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
    state.csm?.updateFrustums();
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
        renderer.toneMapping = THREE.AgXToneMapping; // the composer applied it until now
        if (state.grass) state.grass.visible = false; // no GPU: the extras go with the effects
        state.needsRender = true; // redraw once without effects
        console.warn(`housekit: effects disabled, ${Math.round(dt)} ms frame on this machine`);
        try { parent.postMessage({ type: "house:effects", enabled: false }, "*"); } catch { /* noop */ }
      }
    }
  });
}

/** The sun the scene asked for: { sun: { elevation, azimuth } } wins over { time: "…" }. */
function sunOptions(result) {
  const preset = SUN_PRESETS[result?.time] ?? SUN_PRESETS.afternoon;
  const elevation = Number(result?.sun?.elevation);
  const azimuth = Number(result?.sun?.azimuth);
  return {
    elevation: Number.isFinite(elevation) ? Math.min(89, Math.max(2, elevation)) : preset.elevation,
    azimuth: Number.isFinite(azimuth) ? azimuth : preset.azimuth,
  };
}

/**
 * Physical sky (three's Sky addon) driven by the sun's elevation and azimuth; the environment
 * map is generated from it so surfaces get the warm sun and the blue sky bounce; the
 * directional light is aligned with the sky's sun and coloured by its height; the fog and
 * the clear colour follow the horizon. Skipped at quality=low (flat colour, no env map).
 */
function setupSky(scene, renderer, sun, { elevation, azimuth }) {
  const el = THREE.MathUtils.degToRad(elevation);
  const az = THREE.MathUtils.degToRad(azimuth);
  // +x east, +z south: azimuth 0 = north (-z), 90 = east (+x), clockwise
  const dir = new THREE.Vector3(Math.sin(az) * Math.cos(el), Math.sin(el), -Math.cos(az) * Math.cos(el));
  sun.position.copy(dir).multiplyScalar(60);
  sun.target.position.set(0, 0, 0);
  scene.add(sun.target);
  const t = Math.sin(el); // 0 at the horizon, 1 overhead
  sun.color.set("#ffd2a3").lerp(new THREE.Color("#fff7ec"), Math.min(1, t * 1.6));
  sun.intensity = 0.6 + 2.2 * Math.pow(t, 0.6);
  // horizon: warm haze when the sun is low, pale blue when it is high
  const horizon = new THREE.Color("#e9d9c4").lerp(new THREE.Color("#d5e1ea"), Math.min(1, t * 1.4));
  renderer.setClearColor(horizon, 1);
  if (state.fog) state.fog.color.copy(horizon);
  state.horizon = horizon;
  if (QUALITY === "low") {
    scene.background = horizon;
    return;
  }
  const sky = new Sky();
  sky.scale.setScalar(SKY_RADIUS);
  const u = sky.material.uniforms;
  u.turbidity.value = 4;
  u.rayleigh.value = 1.8;
  u.mieCoefficient.value = 0.006;
  u.mieDirectionalG.value = 0.8;
  u.sunPosition.value.copy(dir);
  sky.userData = { kind: "sky", excludeFromBounds: true };
  // the sky is the background: no fog on it, always behind everything
  sky.material.depthWrite = false;
  sky.renderOrder = -1;
  // the environment map is the sky itself (rendered alone, then the dome joins the scene)
  const pmrem = new THREE.PMREMGenerator(renderer);
  const skyScene = new THREE.Scene();
  skyScene.add(sky);
  scene.environment = pmrem.fromScene(skyScene, 0.04).texture;
  scene.environmentIntensity = 0.45;
  pmrem.dispose();
  scene.add(sky);
  scene.background = null;
  state.sky = sky;
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
  state.csm?.update();
  if (composer) composer.render();
  else renderer.render(scene, camera);
}

/** three's SSRPass inside the pmndrs composer: same render(renderer, write, read) shape. */
class ThreePassAdapter extends Pass {
  constructor(pass, scale = 0.5) {
    super("ThreePassAdapter");
    this.pass = pass;
    this.scale = scale;
    this.needsSwap = true;
  }
  render(renderer, inputBuffer, outputBuffer, deltaTime, stencilTest) {
    this.pass.render(renderer, outputBuffer, inputBuffer, deltaTime, stencilTest);
  }
  setSize(width, height) {
    this.pass.setSize(Math.max(2, Math.floor(width * this.scale)), Math.max(2, Math.floor(height * this.scale)));
  }
}

/**
 * The interactive-only extras (#20): grass blades in a ring around the building, and
 * screen-space reflections restricted to the glass (panes, conservatory, glass railings).
 * Either one failing to set up only logs a warning: the scene must never depend on them.
 */
function setupExtras(scene, renderer, camera, houseGroup, W, H) {
  try {
    const building = buildingBounds(houseGroup);
    const around = building.isEmpty() ? framingBounds(houseGroup) : building;
    if (!around.isEmpty()) {
      const grass = house.grassField({ around, inner: 0.4, outer: 6, count: 30000, seed: 1 });
      scene.add(grass);
      state.grass = grass;
    }
  } catch (err) {
    console.warn(`housekit: grass skipped (${err?.message ?? err})`);
  }
  if (!state.composer || state.ssrSlot === undefined) return;
  try {
    const glass = [];
    scene.traverse((o) => {
      const m = o.material;
      if (o.isMesh && m && (m.userData?.interior || (m.isMeshPhysicalMaterial && m.transparent))) glass.push(o);
    });
    if (glass.length === 0) return;
    const ssr = new SSRPass({ renderer, scene, camera, width: Math.floor(W / 2), height: Math.floor(H / 2), selects: glass });
    ssr.opacity = 0.35;
    ssr.maxDistance = 14;
    ssr.thickness = 0.06;
    ssr.blur = true;
    ssr.fresnel = true;
    ssr.distanceAttenuation = true;
    const adapter = new ThreePassAdapter(ssr, 0.5);
    adapter.initialize?.(renderer, false, THREE.HalfFloatType);
    state.composer.addPass(adapter, state.ssrSlot);
    state.ssr = adapter;
  } catch (err) {
    console.warn(`housekit: screen-space reflections skipped (${err?.message ?? err})`);
  }
}

/**
 * Cascaded shadow maps for the sun (#17): three cascades over the plot, crisp next to the
 * house and soft far away. The CSM's own lights replace the single sun light (which keeps
 * its position for the sky); every lit material is set up once, including the builder's raw
 * ones (traversed after buildScene).
 */
function setupCSM(scene, camera, sun) {
  const dir = sun.position.clone().normalize().negate();
  const csm = new CSM({
    camera,
    parent: scene,
    cascades: 3,
    mode: "practical",
    maxFar: 140,
    shadowMapSize: QUALITY === "high" ? 2048 : 1024,
    lightDirection: dir,
    lightIntensity: sun.intensity,
    lightNear: 1,
    lightFar: 400,
    lightMargin: 80,
    shadowBias: -0.0002,
  });
  csm.fade = true;
  for (const l of csm.lights) l.color.copy(sun.color);
  sun.castShadow = false;
  sun.intensity = 0;
  const done = new Set();
  scene.traverse((o) => {
    const mats = Array.isArray(o.material) ? o.material : o.material ? [o.material] : [];
    for (const m of mats) {
      if (done.has(m) || !(m.isMeshStandardMaterial || m.isMeshPhysicalMaterial || m.isMeshPhongMaterial || m.isMeshLambertMaterial)) continue;
      csm.setupMaterial(m);
      done.add(m);
    }
  });
  state.csm = csm;
}

/**
 * Bounds of the building itself (walls, openings and anything tagged `userData.kind =
 * "building"`), without the garden: what the "-photo" views frame.
 */
function buildingBounds(root) {
  const box = new THREE.Box3();
  const tmp = new THREE.Box3();
  const kinds = new Set(["wall", "perimeter", "window", "door", "building"]);
  root.traverse((o) => {
    if (!kinds.has(o.userData?.kind)) return;
    tmp.setFromObject(o);
    if (!tmp.isEmpty()) box.union(tmp);
  });
  return box;
}

function photoView(side) {
  const { building } = state.frame;
  const aspect = state.camera.aspect;
  const c = building.getCenter(new THREE.Vector3());
  const size = building.getSize(new THREE.Vector3());
  const az = THREE.MathUtils.degToRad(AZIMUTH[side]);
  const dir = new THREE.Vector3(Math.sin(az), 0, -Math.cos(az));
  const alongX = side === "north" || side === "south";
  const facadeWidth = alongX ? size.x : size.z;
  const toFacade = (alongX ? size.z : size.x) / 2; // centre → façade plane
  const hfov = 2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(PHOTO_FOV / 2)) * aspect);
  const standOff = Math.max(3, (facadeWidth / 0.85 / 2) / Math.tan(hfov / 2));
  const base = building.min.y;
  const pos = new THREE.Vector3(c.x, base + 1.6, c.z).addScaledVector(dir, toFacade + standOff);
  const target = new THREE.Vector3(c.x, base + size.y / 2, c.z).addScaledVector(dir, toFacade);
  return { pos: pos.toArray(), target, fov: PHOTO_FOV };
}

/**
 * Straight-on view of one façade with a very narrow field of view from far away: the building
 * fills ~90 % of the frame and the perspective is nearly orthographic, like an elevation
 * drawing. Fog is switched off for it (the camera sits beyond the fog's far distance).
 */
function elevationView(side) {
  const { building } = state.frame;
  const aspect = state.camera.aspect;
  const c = building.getCenter(new THREE.Vector3());
  const size = building.getSize(new THREE.Vector3());
  const az = THREE.MathUtils.degToRad(AZIMUTH[side]);
  const dir = new THREE.Vector3(Math.sin(az), 0, -Math.cos(az));
  const alongX = side === "north" || side === "south";
  const facadeWidth = alongX ? size.x : size.z;
  const depth = alongX ? size.z : size.x; // the whole building must fit, not only the front plane
  const vfov = THREE.MathUtils.degToRad(ELEVATION_FOV);
  const hfov = 2 * Math.atan(Math.tan(vfov / 2) * aspect);
  const fill = 0.9;
  const dist = Math.max((facadeWidth / fill / 2) / Math.tan(hfov / 2), (size.y / fill / 2) / Math.tan(vfov / 2)) + depth / 2;
  const midY = building.min.y + size.y / 2;
  const target = new THREE.Vector3(c.x, midY, c.z);
  const pos = target.clone().addScaledVector(dir, dist);
  // the camera is far away: move the near plane up so the depth buffer (and the ambient
  // occlusion that reads it) keeps its precision around the building
  return { pos: pos.toArray(), target, fov: ELEVATION_FOV, fog: false, fixed: true, near: dist * 0.5, far: dist + SKY_RADIUS * 2 };
}

/**
 * Apply the page's camera overrides (query string) to a side view: azimuth/distance move the
 * eye around the house centre, eye_height/target_height are metres above the house base, fov
 * in degrees. Aerial, top, elevation and custom views are left alone.
 */
function withOverrides(name, v) {
  const o = CAMERA_OVERRIDES;
  if (!o || !state.frame || v.fixed) return v;
  const baseName = name.replace(/-photo$/, "");
  const azDeg = o.azimuth ?? AZIMUTH[baseName];
  if (azDeg === undefined) return v;
  const { c, r, bounds } = state.frame;
  const pos = new THREE.Vector3(...v.pos);
  const target = v.target.clone();
  if (o.azimuth !== undefined || o.distance !== undefined) {
    const az = THREE.MathUtils.degToRad(azDeg);
    const dist = o.distance !== undefined ? o.distance * r : Math.hypot(pos.x - c.x, pos.z - c.z);
    pos.x = c.x + Math.sin(az) * dist;
    pos.z = c.z - Math.cos(az) * dist;
    if (o.azimuth !== undefined) { target.x = c.x; target.z = c.z; }
  }
  if (o.eye_height !== undefined) pos.y = bounds.min.y + o.eye_height;
  if (o.target_height !== undefined) target.y = bounds.min.y + o.target_height;
  return { pos: pos.toArray(), target, fov: o.fov ?? v.fov };
}

async function setView(name) {
  let preset = state.views[name];
  if (typeof preset === "function") preset = preset();
  if (!preset) {
    recordError(`unknown view: ${name}. Known: ${Object.keys(state.views).join(", ")}`);
    return false;
  }
  const v = withOverrides(name, preset);
  if (state.scene) state.scene.fog = v.fog === false ? null : state.fog ?? null;
  // headless only: an interactive page keeps the default planes so orbiting away from an
  // elevation view never clips the scene
  state.camera.near = HEADLESS ? v.near ?? 0.1 : 0.1;
  state.camera.far = Math.max(500, v.far ?? 0);
  state.camera.fov = v.fov ?? DEFAULT_FOV;
  state.camera.updateProjectionMatrix();
  state.csm?.updateFrustums();
  state.camera.position.set(...v.pos);
  state.controls.target.copy(v.target);
  state.controls.update();
  state.camera.lookAt(v.target);
  renderFrame();
  state.needsRender = true; // the interactive loop draws the settled frame
  return true;
}
