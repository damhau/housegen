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
//   ?look=presentation                             the owner's look: physical sky, a sun placed and coloured by
//                                                  the runtime, environment light from that sky, ground to the
//                                                  horizon, multisampling, a light vignette. The builder and the
//                                                  critic never request it (their renders use `quality` alone),
//                                                  so it changes nothing they see.
//   ?look=ultra                                    presentation + progressive accumulation over `samples` frames:
//                                                  the sun jittered within a disc (soft shadows) and the camera
//                                                  jittered by a fraction of a pixel (supersampled anti-aliasing).
//                                                  Interactive pages converge while the camera rests; headless
//                                                  pages finish every sample before `ready`.
//   ?walk=1                                        first-person walk (kit/walk.js): click to look with the mouse
//                                                  (Esc releases) or drag, WASD, click/tap the
//                                                  floor to glide; starts from `view` at 1.6 m above its floor
//   ?samples=48                                    accumulation frames for look=ultra (headless default 16)
//   ?sun_el=42&sun_az=200                          presentation sun: elevation and azimuth in degrees, azimuth
//                                                  clockwise from north = the direction the light comes FROM
//   ?p_env=0.3&p_bg=0.34&p_sun=3&p_hemi=0.08&p_expo=1&p_fog=0.0012&p_rayleigh=1.8&p_turbidity=2.5
//                                                  calibration overrides of the presentation look (tuning only)
//   ?p_contrast=1.08&p_sat=1.06&p_vignette=0.28&p_meadow=0.78
//                                                  the grade (contrast about mid grey, saturation, vignette) and
//                                                  how much darker than the lawn the land beyond the plot is

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { GTAOPass } from "three/addons/postprocessing/GTAOPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { SMAAPass } from "three/addons/postprocessing/SMAAPass.js";
import { ShaderPass } from "three/addons/postprocessing/ShaderPass.js";
import { Pass, FullScreenQuad } from "three/addons/postprocessing/Pass.js";
import { CopyShader } from "three/addons/shaders/CopyShader.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { Sky } from "three/addons/objects/Sky.js";
import * as house from "housekit";
import { finishesReady, loadFinishes, metricUVs } from "./finishes.js";

const params = new URLSearchParams(location.search);
const HEADLESS = params.get("headless") === "1";
const WALK = params.get("walk") === "1";
// presentation looks (see the header): never on the builder's or the critic's pages
const LOOK = params.get("look");
const PRESENTATION = LOOK === "presentation" || LOOK === "ultra";
const ULTRA = LOOK === "ultra";
// a presentation page is a "high" page with more on top
const QUALITY = PRESENTATION ? "high" : (params.get("quality") ?? (HEADLESS ? "high" : "medium"));
// ambient occlusion + anti-aliasing: the final look. Interactive pages fall back to plain
// rendering automatically when the machine cannot sustain it (see the frame-time guard).
const EFFECTS = QUALITY === "high";
const SAMPLES = Math.max(1, Math.min(256, Number(params.get("samples")) || (HEADLESS ? 16 : 48)));
const CAMERA_FAR = PRESENTATION ? 2000 : 500; // presentation: the sky dome and the ground reach the horizon
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
  sky: "#d9e0e4",
  grass: "#8a9a68",
  sun: "#fff3e0",
};

// The presentation look. Calibrated against the quality=high renders of real projects
// (docs/presentation-look.md): the sunlit plaster level with its quality=high brightness, the
// shade a little deeper, a sky instead of a card. The light design pass of 2026-09-13: the first
// calibration came out washed out (lit walls darker than quality=high at the same shade level, a
// blue-grey sky fill desaturating the greens, the base ground's square around the plot), so: less
// sky fill, a warmer and stronger sun, a near-neutral hemisphere, the land beyond the plot darker
// and warmer than the lawn with no edge at the plot, a touch of contrast and saturation at the end.
const PRESENTATION_LOOK = {
  sun: { elevation: 42, azimuth: 200 }, // early afternoon, from a little west of south
  sunColor: "#ffe2b8",
  sunIntensity: 3.0,
  sunAngularRadius: 1.5, // degrees; the disc the ultra samples spread the sun over (soft daylight)
  hemi: { sky: "#d6dbe0", ground: "#6e6a4e", intensity: 0.08 }, // near neutral: the sky map is the blue fill
  // the sky map is bright (see setupPresentationSky): these scale it as light and as background.
  // As light it fills the shade: 0.4 put a shaded white wall level with quality=high, 0.3 a
  // little under it, which reads as depth once the sun is stronger
  environmentIntensity: 0.3,
  backgroundIntensity: 0.34,
  exposure: 1.0,
  meadow: 0.78, // the land beyond the plot: the lawn's colour times this, warmed (presentationGround)
  fogDensity: 0.0012,
  vignette: 0.28,
  contrast: 1.08, // about mid grey, after tone mapping (GradeShader)
  saturation: 1.06,
  sky: { turbidity: 2.5, rayleigh: 1.8, mieCoefficient: 0.005, mieDirectionalG: 0.85 },
};

// calibration overrides (see the header): numbers only, anything else keeps the default
for (const [key, path] of [
  ["p_env", ["environmentIntensity"]], ["p_bg", ["backgroundIntensity"]], ["p_sun", ["sunIntensity"]], ["p_hemi", ["hemi", "intensity"]],
  ["p_expo", ["exposure"]], ["p_fog", ["fogDensity"]], ["p_rayleigh", ["sky", "rayleigh"]], ["p_turbidity", ["sky", "turbidity"]],
  ["p_contrast", ["contrast"]], ["p_sat", ["saturation"]], ["p_vignette", ["vignette"]], ["p_meadow", ["meadow"]],
]) {
  const v = Number(params.get(key));
  if (params.get(key) !== null && Number.isFinite(v)) {
    let o = PRESENTATION_LOOK;
    for (const k of path.slice(0, -1)) o = o[k];
    o[path[path.length - 1]] = v;
  }
}

function readSun(p) {
  const el = Number(p.get("sun_el")), az = Number(p.get("sun_az"));
  return {
    elevation: Number.isFinite(el) && p.get("sun_el") !== null ? Math.min(89, Math.max(3, el)) : PRESENTATION_LOOK.sun.elevation,
    azimuth: Number.isFinite(az) && p.get("sun_az") !== null ? az : PRESENTATION_LOOK.sun.azimuth,
  };
}

/** Unit vector pointing AT the sun. +x east, +z south: azimuth 0 = north (-z), 90 = east (+x). */
function sunDirection({ elevation, azimuth }) {
  const el = THREE.MathUtils.degToRad(elevation), az = THREE.MathUtils.degToRad(azimuth);
  return new THREE.Vector3(Math.sin(az) * Math.cos(el), Math.sin(el), -Math.cos(az) * Math.cos(el));
}

const state = {
  ready: false,
  errors: [],
  views: {},
  camera: null,
  controls: null,
  renderer: null,
  composer: null,
  accumulate: null,
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
  // debugging: what the last frame cost: draw calls and triangles (renderer.info) and how many
  // leaf cards the scene holds. A sensible ceiling is ~100k cards; a furnished garden is ~20k.
  get stats() {
    const r = state.renderer;
    if (!r) return null;
    let cards = 0;
    state.scene?.traverse((o) => { if (o.isInstancedMesh && o.userData?.kind === "leaves") cards += o.count; });
    return { calls: r.info.render.calls, triangles: r.info.render.triangles, cards };
  },
  // debugging: which GPU draws this page (WEBGL_debug_renderer_info), e.g. from the console of
  // the app: document.querySelector("iframe").contentWindow.__house.gl
  get gl() {
    const ctx = state.renderer?.getContext();
    if (!ctx) return null;
    const ext = ctx.getExtension("WEBGL_debug_renderer_info");
    return ext ? String(ctx.getParameter(ext.UNMASKED_RENDERER_WEBGL)) : String(ctx.getParameter(ctx.RENDERER));
  },
  // debugging: the presentation look in force on this page (after the calibration overrides)
  get look() { return PRESENTATION ? JSON.parse(JSON.stringify(PRESENTATION_LOOK)) : null; },
  // debugging: progress of the ultra accumulation, null on other pages
  get accumulate() {
    const a = state.accumulate;
    return a ? { count: a.count, total: a.samples, effects: state.composer !== null } : null;
  },
  // deterministic plausibility audit of the built scene (see house.audit): the renderer
  // appends it to the builder's tool results and gives it to the critic
  audit: async () => (state.houseGroup ? [...house.audit(state.houseGroup), ...(await interiorAudit())] : []),
  // what the backend reads with every render (the builder's plan check): the rooms of the floor
  // plans and, per storey, the framing in metres of its plan-section view
  report: () => ({
    rooms: sceneRooms().map(({ name, use, area, polygon, y }) => ({ name, use, area, polygon, y })),
    planSections: storeys().map((s, i) => ({ view: `plan-section-${i + 1}`, y: s.y, bbox: sectionFrame(i) })),
  }),
  // walk mode (?walk=1): the rooms of the scene's floor plans, glide / jump to a place
  get rooms() {
    const out = [];
    state.houseGroup?.traverse((o) => { if (o.userData?.kind === "floorPlan") out.push(...o.userData.rooms); });
    return out;
  },
  walkTo: (x, z) => state.walk?.goTo(x, z),
  get walkDebug() {
    const w = state.walk;
    const r = ([x, z]) => [Math.round(x * 100) / 100, Math.round(z * 100) / 100];
    return w ? { glide: w.glide && r(w.glide), path: w.path.map(r), doorways: w.doorways.map(r), blocked: w.blockedDoorways().map(r), reach: w.reach(), floorY: w.floorY } : null;
  },
  // tests: advance the walk by `seconds` in fixed 50 ms steps, without rendering
  walkTick: (seconds) => { for (let t = 0; t < seconds; t += 0.05) state.walk?.update(0.05); },
  jumpTo: (name) => {
    const room = window.__house.rooms.find((r) => r.name === name);
    if (room) startWalk(name);
    return !!room;
  },
  startWalk: (room) => startWalk(room),
  stopWalk: () => stopWalk(),
  get position() { return state.camera?.position.toArray().map((v) => Math.round(v * 100) / 100) ?? null; },
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

  // presentation keeps the canvas multisampled too: it is what a page falls back to when the
  // frame-time guard drops the composer
  // high-performance: on a dual-GPU laptop the browser otherwise runs WebGL on the integrated
  // GPU; the hint asks for the discrete one (Windows' per-app graphics setting still wins)
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: PRESENTATION || !EFFECTS,
    preserveDrawingBuffer: HEADLESS,
    powerPreference: HEADLESS ? "default" : "high-performance",
  });
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
  renderer.toneMappingExposure = PRESENTATION ? PRESENTATION_LOOK.exposure : 0.95;
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(PALETTE.sky);
  scene.fog = new THREE.Fog(PALETTE.sky, 55, 150);
  state.fog = scene.fog;

  const camera = new THREE.PerspectiveCamera(DEFAULT_FOV, W / H, 0.1, CAMERA_FAR);
  camera.position.set(18, 10, 18);

  // lights: sun + sky/ground hemisphere + a soft environment for the PBR materials
  const hemi = new THREE.HemisphereLight("#e8eef5", "#6f7a5a", 0.55);
  scene.add(hemi);
  const sun = new THREE.DirectionalLight(PALETTE.sun, 2.0);
  sun.position.set(-18, 28, 12);
  sun.castShadow = renderer.shadowMap.enabled;
  // ultra spreads the sun over many frames: the penumbra hides the map's resolution, and the
  // map is re-rendered for every sample, so a smaller one keeps the page converging quickly
  const shadowRes = ULTRA ? 2048 : QUALITY === "high" ? 4096 : 2048;
  sun.shadow.mapSize.set(shadowRes, shadowRes);
  sun.shadow.camera.left = -40; sun.shadow.camera.right = 40;
  sun.shadow.camera.top = 40; sun.shadow.camera.bottom = -40;
  sun.shadow.camera.near = 1; sun.shadow.camera.far = 120;
  sun.shadow.bias = -0.0004;
  sun.shadow.normalBias = 0.02;
  scene.add(sun);
  const sunSpec = readSun(params);
  if (PRESENTATION) {
    setupPresentationSky(scene, renderer, sun, sunSpec);
  } else if (QUALITY !== "low") {
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
    const drawing = renderer.getDrawingBufferSize(new THREE.Vector2());
    // presentation renders into a multisampled target (MSAA with the occlusion pass, which the
    // canvas cannot give); ultra accumulates its own samples instead
    const target = PRESENTATION
      ? new THREE.WebGLRenderTarget(drawing.x, drawing.y, { type: THREE.HalfFloatType, samples: ULTRA ? 0 : 4 })
      : undefined;
    const composer = new EffectComposer(renderer, target);
    if (ULTRA) {
      const accumulate = new AccumulatePass(scene, camera, sun, {
        samples: SAMPLES, radiusDeg: PRESENTATION_LOOK.sunAngularRadius, width: drawing.x, height: drawing.y,
      });
      composer.addPass(accumulate);
      state.accumulate = accumulate;
    } else {
      composer.addPass(new RenderPass(scene, camera));
    }
    const gtao = new GTAOPass(scene, camera, W, H);
    gtao.output = GTAOPass.OUTPUT.Default;
    gtao.blendIntensity = PRESENTATION ? 0.8 : 0.9;
    gtao.updateGtaoMaterial({ radius: 0.6, distanceExponent: 1, thickness: 1, scale: 1, samples: QUALITY === "high" ? 16 : 8, distanceFallOff: 1, screenSpaceRadius: false });
    composer.addPass(gtao);
    composer.addPass(new OutputPass());
    if (PRESENTATION) {
      const grade = new ShaderPass(GradeShader);
      grade.uniforms.strength.value = PRESENTATION_LOOK.vignette;
      grade.uniforms.contrast.value = PRESENTATION_LOOK.contrast;
      grade.uniforms.saturation.value = PRESENTATION_LOOK.saturation;
      composer.addPass(grade);
    } else {
      composer.addPass(new SMAAPass(W, H));
    }
    state.composer = composer;
    state.gtao = gtao;
  }

  // ---- user scene ----
  let result = null;
  try {
    // textured surfaces: loaded first so house.js mat.* hands out textured materials (plain without the files)
    await loadFinishes().catch((e) => console.warn(`housekit: finishes not loaded: ${e?.message ?? e}`));
    result = await buildScene({ THREE, scene, house, group: houseGroup, sun, ground, renderer, camera });
    house.texturePitchedRoofs(houseGroup);
    metricUVs(houseGroup);
    await finishesReady(); // only the texture sets the scene uses were fetched
  } catch (err) {
    recordError(`buildScene failed: ${err?.stack ?? err}`);
  }

  if (PRESENTATION) {
    applyPresentationLook(scene, sun, sunSpec, ground);
    state.accumulate?.rememberSun();
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
      state.views[name] = { pos: v.position, target: new THREE.Vector3(...(v.target ?? [c.x, c.y, c.z])), fov: v.fov };
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
  if (WALK) await startWalk();
  window.addEventListener("message", (e) => {
    // a page opened on its own is its own parent: never act on what it announced itself
    if (e.source === window) return;
    const d = e.data ?? {};
    if (d.type === "house:setView") { stopWalk(); setView(d.view); }
    if (d.type === "house:walk") (d.on ? startWalk(d.room) : Promise.resolve(stopWalk())).catch((err) => recordError(err?.message ?? err));
    if (d.type === "house:jumpTo") startWalk(d.room).catch((err) => recordError(err?.message ?? err));
  });
  state.ready = true;
  try {
    parent.postMessage({
      type: "house:ready",
      views: Object.keys(state.views),
      // walk mode: the rooms of the scene's floor plans; the credit lines of attribution-licensed models
      rooms: sceneRooms().map(({ name, use, area }) => ({ name, use, area })),
      credits: globalThis.__housekitCredits?.() ?? [],
    }, "*");
  } catch { /* noop */ }
  // Render on demand: only when the camera moves (or something asks for a frame). An idle page
  // costs nothing, and machines without a GPU stay responsive.
  state.needsRender = true;
  controls.addEventListener("change", () => { state.needsRender = true; state.accumulate?.reset(); });
  // frame-time guard: if the first frames with effects are slow (no GPU, weak laptop),
  // drop the post-processing rather than freeze the page. Ultra is an explicit choice and
  // keeps going: it only converges more slowly.
  let frames = 0, slowMs = 0;
  renderer.setAnimationLoop(() => {
    const moved = state.walk ? state.walk.update() : controls.update();
    if (moved) state.accumulate?.reset();
    const converging = state.composer && state.accumulate && !state.accumulate.done;
    if (!moved && !state.needsRender && !converging) return;
    state.needsRender = false;
    const t0 = performance.now();
    renderFrame();
    if (converging) {
      const a = state.accumulate;
      try { parent.postMessage({ type: "house:accumulate", count: a.count, total: a.samples }, "*"); } catch { /* noop */ }
    }
    if (state.composer && !ULTRA && frames < 8) {
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

// --------------------------------------------------------------------------
// Presentation look: sky, sun, environment, horizon, vignette, accumulation
// --------------------------------------------------------------------------

function applyPresentationSun(sun, spec) {
  sun.color.set(PRESENTATION_LOOK.sunColor);
  sun.intensity = PRESENTATION_LOOK.sunIntensity;
  sun.position.copy(sunDirection(spec)).multiplyScalar(60);
  sun.target.position.set(0, 0, 0);
  sun.target.updateMatrixWorld();
}

/**
 * Physical sky (three's Sky addon) for the given sun, the environment map generated from that
 * sky (with the sun's glare damped: the directional light already carries the sun), the fog
 * and clear colour read from the sky at the horizon so the ground fades seamlessly into it.
 */
function setupPresentationSky(scene, renderer, sun, spec) {
  const dir = sunDirection(spec);
  const look = PRESENTATION_LOOK;
  const sky = new Sky();
  sky.scale.setScalar(1500);
  const u = sky.material.uniforms;
  u.turbidity.value = look.sky.turbidity;
  u.rayleigh.value = look.sky.rayleigh;
  u.mieDirectionalG.value = look.sky.mieDirectionalG;
  u.sunPosition.value.copy(dir);

  // The Sky shader's radiance is written for an exposure of about one half: several times
  // brighter than this scene's lights. It is rendered once into an environment map, with almost
  // no forward scattering so the sun's disc does not light the scene twice (once from the map,
  // once from the directional light), and that one map, scaled down, is both the light from the
  // sky and the sky the camera sees: the two cannot drift apart.
  const staging = new THREE.Scene();
  staging.add(sky);
  u.mieCoefficient.value = 0.0004;
  const pmrem = new THREE.PMREMGenerator(renderer);
  const skyMap = pmrem.fromScene(staging, 0.02, 1, 4000).texture;
  pmrem.dispose();
  // fog: the sky just above the horizon, away from the sun, at the background's scale
  const horizon = sampleHorizon(renderer, staging, dir).multiplyScalar(look.backgroundIntensity);
  staging.remove(sky);
  sky.material.dispose();
  sky.geometry.dispose();
  // the map lights the scene from now on; the background and the fog are applied after
  // buildScene (see applyPresentationLook): scene code may set the colour of the plain
  // background it was written against, and a texture has no colour to set
  scene.environment = skyMap;
  scene.environmentIntensity = look.environmentIntensity;
  state.presentation = { skyMap, horizon };
}

/**
 * The look the runtime owns on a presentation page, applied after buildScene so that whatever
 * the scene did with ctx.scene's background, fog or environment, or with ctx.sun, the page
 * still shows the sky it was lit by.
 */
function applyPresentationLook(scene, sun, spec, ground) {
  const look = PRESENTATION_LOOK;
  const { skyMap, horizon } = state.presentation;
  scene.environment = skyMap;
  scene.environmentIntensity = look.environmentIntensity;
  scene.background = skyMap;
  scene.backgroundIntensity = look.backgroundIntensity;
  scene.backgroundBlurriness = 0;
  scene.fog = new THREE.FogExp2(horizon, look.fogDensity);
  state.fog = scene.fog;
  const hemi = scene.children.find((o) => o.isHemisphereLight);
  if (hemi) {
    hemi.color.set(look.hemi.sky);
    hemi.groundColor.set(look.hemi.ground);
    hemi.intensity = look.hemi.intensity;
  }
  applyPresentationSun(sun, spec);
  presentationGround(scene, ground);
}

/** Average linear radiance of the sky just above the horizon, opposite the sun. */
function sampleHorizon(renderer, skyScene, sunDir) {
  const n = 8;
  const rt = new THREE.WebGLRenderTarget(n, n, { type: THREE.FloatType, depthBuffer: false });
  const cam = new THREE.PerspectiveCamera(3, 1, 1, 4000);
  const away = new THREE.Vector3(-sunDir.x, 0, -sunDir.z).normalize();
  cam.lookAt(away.multiplyScalar(100).setY(1.2));
  const prev = renderer.getRenderTarget();
  renderer.setRenderTarget(rt);
  renderer.render(skyScene, cam);
  const px = new Float32Array(n * n * 4);
  renderer.readRenderTargetPixels(rt, 0, 0, n, n, px);
  renderer.setRenderTarget(prev);
  rt.dispose();
  let r = 0, g = 0, b = 0;
  for (let i = 0; i < px.length; i += 4) { r += px[i]; g += px[i + 1]; b += px[i + 2]; }
  const k = n * n;
  // linear values: say so, or the setter would apply the sRGB transfer curve
  return new THREE.Color().setRGB(r / k, g / k, b / k, THREE.LinearSRGBColorSpace);
}

/**
 * The land on a presentation page. The runtime's base ground (a 400 m sheet, 20 % darker than
 * the terrain under its grain: the square the aerial views showed around the plot) is hidden and
 * a sheet to the horizon takes its place, with a hole under every mesh the scene tagged as
 * terrain (the builder's plot, and a wider context ground when it made one): inside them only
 * the builder's ground and whatever is dug into it (a pool, a sunken terrace) are drawn. Outside,
 * the sheet FOLLOWS the scene's own ground height (`groundY`, the function the builder placed its
 * fences, trees and neighbours' walls on) for 40 m past the terrain, then eases over the next
 * 80 m to one far height, the mean of that ground at the end of the followed band: on a sloped
 * plot the land beyond keeps the slope where anything stands on it, and no flat plane cuts
 * through a house set lower than the terrain's rim. From the terrain's edge the colour darkens
 * and warms over 60 m into a meadow, one fine grain over all of it, fading into the haze. The
 * holes are in the geometry (every pass sees them, occlusion and depth included); the gradient
 * is per fragment from the world distance to the plot, so there is no seam and no triangle
 * pattern whatever the tessellation.
 */
function presentationGround(scene, baseGround) {
  const look = PRESENTATION_LOOK;
  const terrains = [];
  scene.traverse((o) => {
    if (o !== baseGround && o.isMesh && o.userData?.kind === "terrain") terrains.push(o);
  });
  const plot = terrains[0] ?? baseGround;
  const material = Array.isArray(plot.material) ? plot.material[0] : plot.material;
  // a textured lawn carries a tint, not its colour: use the colour it reads as (finishes.js baseColor)
  const lawn = material?.userData?.baseColor?.clone() ?? (material?.color ? material.color.clone() : new THREE.Color(PALETTE.grass));
  // the plot: the terrains' footprints (holes in the sheet), or the house and its site on flat
  // ground (no hole: the sheet is then the ground under the house)
  const inset = 0.1; // the meadow reaches 10 cm under the terrain's rim: no hairline at the edge
  const holes = terrains
    .map((t) => new THREE.Box3().setFromObject(t))
    .filter((b) => !b.isEmpty() && b.max.x - b.min.x > 2 * inset && b.max.z - b.min.z > 2 * inset)
    .map((b) => b.expandByVector(new THREE.Vector3(-inset, 0, -inset)));
  const box = holes.length ? holes.reduce((u, b) => u.union(b), new THREE.Box3()) : framingBounds(state.houseGroup).expandByScalar(12);
  if (box.isEmpty()) box.set(new THREE.Vector3(-45, 0, -45), new THREE.Vector3(45, 0, 45));
  const meadow = lawn.clone().multiply(new THREE.Color(look.meadow, look.meadow * 0.94, look.meadow * 0.8));
  const grainMean = new THREE.Color().setRGB(245 / 255, 245 / 255, 245 / 255, THREE.SRGBColorSpace).r;
  lawn.multiplyScalar(1 / grainMean);
  meadow.multiplyScalar(1 / grainMean);

  // the sheet: a grid whose lines include every hole's edges (a cell is then either inside a
  // hole or outside it), 2 m cells through the followed and eased bands, coarser to the edge
  const follow = 40, ease = 80, step = 2, tile = 6;
  const halfSize = Math.max(1400, Math.max(-box.min.x, box.max.x, -box.min.z, box.max.z) + follow + ease + 100);
  const lines = (lo, hi) => {
    const s = new Set([lo, hi]);
    for (let k = 1; k * step <= follow + ease; k++) { s.add(lo - k * step); s.add(hi + k * step); }
    for (const d of [30, 80, 180, 350, 600, 900]) { s.add(lo - follow - ease - d); s.add(hi + follow + ease + d); }
    s.add(-halfSize); s.add(halfSize);
    return [...s].filter((v) => Math.abs(v) <= halfSize).sort((a, b) => a - b);
  };
  const xs = lines(box.min.x, box.max.x), zs = lines(box.min.z, box.max.z);
  // the far height: the mean of the scene's ground at the end of the followed band, so the
  // easing starts from where the land already is
  const heightAt = (x, z) => { const y = house.groundY(x, z); return Number.isFinite(y) ? y : 0; };
  const outsidePlot = (x, z) => Math.hypot(Math.max(box.min.x - x, x - box.max.x, 0), Math.max(box.min.z - z, z - box.max.z, 0));
  let farSum = 0, farCount = 0;
  for (let i = 0; i <= 8; i++) {
    const t = i / 8;
    const x = THREE.MathUtils.lerp(box.min.x, box.max.x, t), z = THREE.MathUtils.lerp(box.min.z, box.max.z, t);
    for (const [px, pz] of [[x, box.min.z - follow], [x, box.max.z + follow], [box.min.x - follow, z], [box.max.x + follow, z]]) {
      farSum += heightAt(px, pz); farCount += 1;
    }
  }
  const far = farSum / farCount;
  const positions = [], uvs = [];
  for (const z of zs) for (const x of xs) {
    const t = THREE.MathUtils.smoothstep(outsidePlot(x, z), follow, follow + ease);
    positions.push(x, THREE.MathUtils.lerp(heightAt(x, z), far, t) - 0.02, z);
    uvs.push(x / tile, -z / tile);
  }
  const inHole = (x0, x1, z0, z1) => holes.some((h) => x0 >= h.min.x - 1e-6 && x1 <= h.max.x + 1e-6 && z0 >= h.min.z - 1e-6 && z1 <= h.max.z + 1e-6);
  const indices = [];
  for (let j = 0; j < zs.length - 1; j++) for (let i = 0; i < xs.length - 1; i++) {
    if (inHole(xs[i], xs[i + 1], zs[j], zs[j + 1])) continue;
    const a = j * xs.length + i, b = a + 1, c = a + xs.length, d = c + 1;
    indices.push(a, c, b, b, c, d);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  const map = noiseTexture(256, 245, 20, 3, 1);
  map.colorSpace = THREE.SRGBColorSpace;
  const mat = new THREE.MeshStandardMaterial({ map, roughness: 1 });
  mat.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, {
      uPlotMin: { value: new THREE.Vector2(box.min.x, box.min.z) },
      uPlotMax: { value: new THREE.Vector2(box.max.x, box.max.z) },
      uLawn: { value: lawn },
      uMeadow: { value: meadow },
      uFade: { value: 60 },
    });
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nvarying vec2 vLandXZ;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvLandXZ = (modelMatrix * vec4(transformed, 1.0)).xz;");
    shader.fragmentShader = shader.fragmentShader
      .replace(
        "#include <common>",
        "#include <common>\nvarying vec2 vLandXZ;\nuniform vec2 uPlotMin;\nuniform vec2 uPlotMax;\nuniform vec3 uLawn;\nuniform vec3 uMeadow;\nuniform float uFade;",
      )
      .replace(
        "#include <color_fragment>",
        [
          "#include <color_fragment>",
          "vec2 outsidePlot = max(max(uPlotMin - vLandXZ, vLandXZ - uPlotMax), vec2(0.0));",
          "diffuseColor.rgb *= mix(uLawn, uMeadow, smoothstep(0.0, uFade, length(outsidePlot)));",
        ].join("\n"),
      );
  };
  const land = new THREE.Mesh(geo, mat);
  land.receiveShadow = true;
  land.userData = { kind: "terrain", excludeFromBounds: true };
  baseGround.visible = false;
  scene.add(land);
}

/**
 * The grade at the end of a presentation page, after tone mapping and the output colour space:
 * a touch of contrast about mid grey, a little saturation, a light vignette.
 */
const GradeShader = {
  uniforms: { tDiffuse: { value: null }, strength: { value: 0.28 }, contrast: { value: 1.0 }, saturation: { value: 1.0 } },
  vertexShader: /* glsl */ `
    varying vec2 vUv;
    void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
  fragmentShader: /* glsl */ `
    uniform sampler2D tDiffuse;
    uniform float strength;
    uniform float contrast;
    uniform float saturation;
    varying vec2 vUv;
    void main() {
      vec4 c = texture2D(tDiffuse, vUv);
      vec3 col = (c.rgb - 0.5) * contrast + 0.5;
      float l = dot(col, vec3(0.2126, 0.7152, 0.0722));
      col = mix(vec3(l), col, saturation);
      float r = length((vUv - 0.5) * vec2(1.0, 0.8));
      col *= 1.0 - strength * smoothstep(0.35, 0.85, r);
      gl_FragColor = vec4(clamp(col, 0.0, 1.0), c.a);
    }`,
};

/** Van der Corput radical inverse: the i-th point of a low-discrepancy sequence in base b. */
function halton(i, b) {
  let f = 1, r = 0;
  while (i > 0) { f /= b; r += f * (i % b); i = Math.floor(i / b); }
  return r;
}

/**
 * Progressive accumulation (look=ultra): each render adds one frame with the sun moved to a
 * point of a small disc around its position (an area light: soft, distance-dependent penumbrae)
 * and the camera shifted by a fraction of a pixel (supersampled anti-aliasing), and blends it
 * into the running average, which the following passes (occlusion, output, vignette) then read.
 * Deterministic: the sample points come from Halton sequences.
 */
class AccumulatePass extends Pass {
  constructor(scene, camera, sun, { samples, radiusDeg, width, height }) {
    super();
    this.scene = scene;
    this.camera = camera;
    this.sun = sun;
    this.samples = samples;
    this.count = 0;
    this.radius = THREE.MathUtils.degToRad(radiusDeg);
    this.rememberSun();
    const opts = { type: THREE.HalfFloatType };
    this.sample = new THREE.WebGLRenderTarget(width, height, { ...opts, depthBuffer: true });
    this.average = [
      new THREE.WebGLRenderTarget(width, height, { ...opts, depthBuffer: false }),
      new THREE.WebGLRenderTarget(width, height, { ...opts, depthBuffer: false }),
    ];
    this.result = null;
    this.syncPixel = new Uint16Array(4); // one half-float pixel, read back headless as a GPU sync
    this.blend = new THREE.ShaderMaterial({
      uniforms: { tAverage: { value: null }, tSample: { value: null }, weight: { value: 1 } },
      vertexShader: /* glsl */ `
        varying vec2 vUv;
        void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
      fragmentShader: /* glsl */ `
        uniform sampler2D tAverage;
        uniform sampler2D tSample;
        uniform float weight;
        varying vec2 vUv;
        void main() { gl_FragColor = mix(texture2D(tAverage, vUv), texture2D(tSample, vUv), weight); }`,
      depthTest: false,
      depthWrite: false,
    });
    this.blendQuad = new FullScreenQuad(this.blend);
    this.copy = new THREE.ShaderMaterial({
      uniforms: THREE.UniformsUtils.clone(CopyShader.uniforms),
      vertexShader: CopyShader.vertexShader,
      fragmentShader: CopyShader.fragmentShader,
      depthTest: false,
      depthWrite: false,
    });
    this.copyQuad = new FullScreenQuad(this.copy);
    this.needsSwap = true;
  }

  /** The sun the samples jitter around: call again if the sun moved. */
  rememberSun() {
    this.sunBase = this.sun.position.clone();
  }

  get done() { return this.count >= this.samples; }

  reset() { this.count = 0; }

  setSize(width, height) {
    this.sample.setSize(width, height);
    for (const t of this.average) t.setSize(width, height);
    this.reset();
  }

  addSample(renderer) {
    const i = this.count;
    const { width, height } = this.sample;
    if (i === 0) {
      this.sun.position.copy(this.sunBase);
    } else {
      // camera: sub-pixel offset of the whole frustum
      this.camera.setViewOffset(width, height, halton(i, 2) - 0.5, halton(i, 3) - 0.5, width, height);
      // sun: a point of the disc, area-weighted
      const r = Math.tan(this.radius * Math.sqrt(halton(i, 5)));
      const phi = 2 * Math.PI * halton(i, 7);
      const dir = this.sunBase.clone().normalize();
      const helper = Math.abs(dir.y) < 0.99 ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(1, 0, 0);
      const t1 = new THREE.Vector3().crossVectors(dir, helper).normalize();
      const t2 = new THREE.Vector3().crossVectors(dir, t1);
      dir.addScaledVector(t1, r * Math.cos(phi)).addScaledVector(t2, r * Math.sin(phi)).normalize();
      this.sun.position.copy(dir.multiplyScalar(this.sunBase.length()));
    }
    renderer.shadowMap.needsUpdate = true;
    renderer.setRenderTarget(this.sample);
    renderer.render(this.scene, this.camera);
    this.camera.clearViewOffset();
    // headless: keep the script in step with the GPU (a readback cannot return before every
    // queued command has run), or the screenshot waits behind samples not yet drawn
    const from = this.average[i % 2], to = this.average[(i + 1) % 2];
    this.blend.uniforms.tAverage.value = from.texture;
    this.blend.uniforms.tSample.value = this.sample.texture;
    this.blend.uniforms.weight.value = 1 / (i + 1);
    renderer.setRenderTarget(to);
    this.blendQuad.render(renderer);
    if (HEADLESS) renderer.readRenderTargetPixels(to, 0, 0, 1, 1, this.syncPixel);
    this.result = to.texture;
    this.count = i + 1;
  }

  render(renderer, writeBuffer) {
    if (!this.done) this.addSample(renderer);
    if (!this.result) return;
    this.copy.uniforms.tDiffuse.value = this.result;
    renderer.setRenderTarget(this.renderToScreen ? null : writeBuffer);
    this.copyQuad.render(renderer);
  }

  dispose() {
    this.sample.dispose();
    for (const t of this.average) t.dispose();
    this.blend.dispose();
    this.copy.dispose();
    this.blendQuad.dispose();
    this.copyQuad.dispose();
  }
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
  return { pos: pos.toArray(), target, fov: ELEVATION_FOV, fog: false, fixed: true, near: dist * 0.5, far: dist + 200 };
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

/** The floor plans of the scene (one per storey), lowest first. */
function storeys() {
  const out = [];
  state.houseGroup?.traverse((o) => { if (o.userData?.kind === "floorPlan") out.push({ y: o.userData.y, plan: o }); });
  return out.sort((a, b) => a.y - b.y);
}

/** The framing [x0, z0, x1, z1] (metres) of storey i's plan section: its rooms + 1 m, at the canvas's aspect. */
function sectionFrame(i) {
  const s = storeys()[i];
  if (!s) return null;
  let [x0, z0, x1, z1] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const r of s.plan.userData.rooms) for (const [x, z] of r.polygon) {
    x0 = Math.min(x0, x); x1 = Math.max(x1, x); z0 = Math.min(z0, z); z1 = Math.max(z1, z);
  }
  x0 -= 1; z0 -= 1; x1 += 1; z1 += 1;
  const size = state.renderer.getSize(new THREE.Vector2());
  const aspect = size.x / size.y;
  const w = x1 - x0, d = z1 - z0;
  if (w / d < aspect) { const e = (d * aspect - w) / 2; x0 -= e; x1 += e; } else { const e = (w / aspect - d) / 2; z0 -= e; z1 += e; }
  return [x0, z0, x1, z1];
}

/**
 * Storey i as a floor plan, into the canvas: seen from above, cut at 1.2 m above its floor, the cut
 * solids filled (stencil: more back faces than front faces below the cut). Exterior walls dark
 * blue, partitions red, door leaves green; furniture as orange footprints.
 */
function drawPlanSection(i) {
  const s = storeys()[i];
  const frame = sectionFrame(i);
  if (!s || !frame) return false;
  const [x0, z0, x1, z1] = frame;
  const r = state.renderer;
  const cut = s.y + 1.2;
  const cam = new THREE.OrthographicCamera(x0, x1, -z0, -z1, 0.1, 500);
  cam.up.set(0, 0, -1);
  cam.position.set(0, cut + 100, 0);
  cam.lookAt(0, 0, 0);
  state.houseGroup.updateMatrixWorld(true);
  const kindOf = (o) => { for (let a = o; a; a = a.parent) if (a.userData?.kind) return a.userData.kind; return null; };
  const CATS = [["#1f3a93", ["wall", "perimeter"]], ["#d62728", ["partition"]], ["#2ca02c", ["interiorDoor"]]];
  const out = new THREE.Scene();
  const clip = [new THREE.Plane(new THREE.Vector3(0, -1, 0), cut)];
  let order = 0;
  // furniture first, as footprints under everything
  state.houseGroup.traverse((o) => {
    if (o.userData?.kind !== "furniture" || o.userData.flat) return;
    const [w, d] = o.userData.footprint ?? [0, 0];
    if (!(w > 0 && d > 0) || Math.abs(o.getWorldPosition(new THREE.Vector3()).y - s.y) > 0.6) return;
    const q = new THREE.Mesh(new THREE.PlaneGeometry(w, d), new THREE.MeshBasicMaterial({ color: "#ff9900", toneMapped: false, transparent: true, opacity: 0.55, depthTest: false }));
    q.rotation.x = -Math.PI / 2;
    const p = o.getWorldPosition(new THREE.Vector3());
    const yaw = new THREE.Euler().setFromQuaternion(o.getWorldQuaternion(new THREE.Quaternion()), "YXZ").y;
    q.position.set(p.x, s.y, p.z);
    q.rotation.z = yaw;
    q.renderOrder = order;
    out.add(q);
  });
  order++;
  for (const [color, kinds] of CATS) {
    const meshes = [];
    state.houseGroup.traverse((o) => {
      if (!o.isMesh) return;
      const k = kindOf(o);
      if (kinds.includes(k)) meshes.push(o);
    });
    for (const [side, op] of [[THREE.BackSide, THREE.IncrementWrapStencilOp], [THREE.FrontSide, THREE.DecrementWrapStencilOp]]) {
      const m = new THREE.MeshBasicMaterial({ side, colorWrite: false, depthWrite: false, depthTest: false, clippingPlanes: clip,
        stencilWrite: true, stencilFunc: THREE.AlwaysStencilFunc, stencilFail: op, stencilZFail: op, stencilZPass: op });
      for (const o of meshes) {
        const c = new THREE.Mesh(o.geometry, m);
        c.matrixAutoUpdate = false;
        c.matrix.copy(o.matrixWorld);
        c.renderOrder = order;
        out.add(c);
      }
    }
    const cap = new THREE.Mesh(new THREE.PlaneGeometry(4000, 4000), new THREE.MeshBasicMaterial({ color, toneMapped: false,
      depthTest: false, depthWrite: false, stencilWrite: true, stencilRef: 0, stencilFunc: THREE.NotEqualStencilFunc,
      stencilFail: THREE.ReplaceStencilOp, stencilZFail: THREE.ReplaceStencilOp, stencilZPass: THREE.ReplaceStencilOp }));
    cap.rotation.x = -Math.PI / 2;
    cap.position.y = cut;
    cap.renderOrder = order + 1;
    out.add(cap);
    order += 2;
  }
  out.background = new THREE.Color("#ffffff");
  // the page's context has no stencil buffer: draw into a target that has one, then onto the canvas
  const size = r.getDrawingBufferSize(new THREE.Vector2());
  const target = new THREE.WebGLRenderTarget(size.x, size.y, { stencilBuffer: true, depthBuffer: true });
  const saved = { local: r.localClippingEnabled, target: r.getRenderTarget(), autoClear: r.autoClear };
  r.localClippingEnabled = true;
  r.autoClear = true;
  r.setRenderTarget(target);
  r.clear(true, true, true);
  r.render(out, cam);
  r.setRenderTarget(null);
  const blit = new FullScreenQuad(new THREE.MeshBasicMaterial({ map: target.texture, toneMapped: false }));
  blit.render(r);
  blit.dispose();
  target.dispose();
  r.localClippingEnabled = saved.local;
  r.autoClear = saved.autoClear;
  r.setRenderTarget(saved.target);
  state.needsRender = false; // keep the section on screen until something moves
  return true;
}

/**
 * Plausibility of the interiors (added to audit): on each storey with a floor plan, the doors that
 * cannot be reached from one side, the rooms furniture splits in two or fills, and the rooms that
 * are not connected to each other (fine between two flats, a mistake inside one).
 */
async function interiorAudit() {
  const floors = storeys();
  if (!floors.length) return [];
  const { Walk } = await import("./walk.js");
  const lines = [];
  const f = (v) => (Math.round(v * 100) / 100).toString();
  for (const s of floors) {
    const rooms = s.plan.userData.rooms;
    const big = [...rooms].sort((a, b) => b.area - a.area)[0];
    if (!big) continue;
    let cx = 0, cz = 0;
    for (const [x, z] of big.polygon) { cx += x / big.polygon.length; cz += z / big.polygon.length; }
    const cam = new THREE.PerspectiveCamera(70, 1, 0.05, 100);
    cam.position.set(cx, s.y + 1.62, cz);
    const w = new Walk({ camera: cam, canvas: document.createElement("canvas"), root: state.houseGroup, renderer: state.renderer });
    try {
      for (const d of w.blockedDoors()) {
        lines.push(`the door ${f(d.offset)}–${f(d.offset + d.width)} m along the partition [${d.from}]→[${d.to}] cannot be reached from one side: something stands at [${f(d.at[0])}, ${f(d.at[1])}] (keep 50 cm clear in front of a door)`);
      }
      const groups = w.reach();
      for (const r of rooms) {
        if (r.use === "storage" || r.area < 1.5) continue;
        const n = groups.filter((g) => g.includes(r.name)).length;
        if (n === 0) lines.push(`nobody can stand in "${r.name}": furniture fills it or leaves less than 50 cm anywhere`);
        if (n > 1) lines.push(`"${r.name}" is split in parts one cannot walk between: move furniture to leave a 60 cm passage`);
      }
      if (groups.length > 1) {
        lines.push(`storey at y=${f(s.y)}: ${groups.length} areas not connected to each other (fine between two flats, a mistake inside one): ${groups.map((g) => g.join(", ")).join(" | ")}`);
      }
    } finally {
      w.dispose();
    }
  }
  return lines;
}

/** The rooms of the floor plans in the scene, each with its storey's floor level `y`. */
function sceneRooms() {
  const out = [];
  state.houseGroup?.traverse((o) => {
    if (o.userData?.kind === "floorPlan") out.push(...o.userData.rooms.map((r) => ({ ...r, y: o.userData.y })));
  });
  return out;
}

/**
 * First-person walk (kit/walk.js): into `roomName`, else a hall, else the largest room; with no floor
 * plan, from where the camera stands. Already walking: jump to that room.
 */
async function startWalk(roomName) {
  const rooms = sceneRooms();
  const room = rooms.find((r) => r.name === roomName)
    ?? rooms.find((r) => r.use === "hall")
    ?? [...rooms].sort((a, b) => b.area - a.area)[0];
  if (state.walk) {
    if (room) state.walk.jumpTo(room);
    return;
  }
  const { Walk } = await import("./walk.js");
  const camera = state.camera;
  if (room) {
    // stand in the room, at eye height above its floor, looking along it
    let cx = 0, cz = 0;
    for (const [x, z] of room.polygon) { cx += x / room.polygon.length; cz += z / room.polygon.length; }
    camera.position.set(cx, room.y + 0.02 + 1.6, cz);
    camera.lookAt(cx + 1, room.y + 1.5, cz);
  }
  state.savedFov = camera.fov;
  camera.fov = 70;
  camera.updateProjectionMatrix();
  state.controls.enabled = false;
  state.walk = new Walk({ camera, canvas: state.renderer.domElement, root: state.houseGroup, renderer: state.renderer,
    onChange: () => { state.needsRender = true; } });
  if (room) state.walk.jumpTo(room);
  window.__walk = state.walk; // debugging
  state.needsRender = true;
  try { parent.postMessage({ type: "house:walking", on: true }, "*"); } catch { /* noop */ }
}

function stopWalk() {
  if (!state.walk) return;
  state.walk.dispose();
  if (document.pointerLockElement) document.exitPointerLock();
  state.walk = null;
  window.__walk = null;
  state.camera.fov = state.savedFov ?? DEFAULT_FOV;
  state.camera.updateProjectionMatrix();
  // orbit on around what the walker was looking at
  const ahead = state.camera.getWorldDirection(new THREE.Vector3()).multiplyScalar(3).add(state.camera.position);
  state.controls.target.copy(ahead);
  state.controls.enabled = true;
  state.controls.update();
  state.needsRender = true;
  try { parent.postMessage({ type: "house:walking", on: false }, "*"); } catch { /* noop */ }
}

async function setView(name) {
  // interiors: an eye-height view of a room (room-<n>, 1-based in the order of the rooms), or the
  // plan section of a storey (plan-section-<n>) drawn at the framing report() gives
  const roomView = /^room-(\d+)$/.exec(name);
  const sectionView = /^plan-section-(\d+)$/.exec(name);
  if (roomView) {
    const room = sceneRooms()[Number(roomView[1]) - 1];
    if (!room) { recordError(`unknown view: ${name} (${sceneRooms().length} rooms)`); return false; }
    await startWalk(room.name);
    renderFrame();
    return true;
  }
  stopWalk();
  if (sectionView) {
    const ok = drawPlanSection(Number(sectionView[1]) - 1);
    if (!ok) recordError(`unknown view: ${name} (${storeys().length} storeys with a floor plan)`);
    return ok;
  }
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
  state.camera.far = Math.max(CAMERA_FAR, v.far ?? 0);
  state.camera.fov = v.fov ?? DEFAULT_FOV;
  state.camera.updateProjectionMatrix();
  state.camera.position.set(...v.pos);
  state.controls.target.copy(v.target);
  state.controls.update();
  state.camera.lookAt(v.target);
  state.accumulate?.reset();
  if (HEADLESS && state.composer && state.accumulate) {
    // every sample before the screenshot
    while (!state.accumulate.done) renderFrame();
  } else {
    renderFrame();
  }
  state.needsRender = true; // the interactive loop draws the settled frame
  return true;
}
