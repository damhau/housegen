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
//   ?tour=1 (or #autoplay)                         the guided tour through the rooms once loaded (#57)
//   ?walk=1                                        first-person walk (kit/walk.js): click to look with the mouse
//                                                  (Esc releases) or drag, WASD, click/tap the
//                                                  floor to glide; starts from `view` at 1.6 m above its floor
//   ?samples=48                                    accumulation frames for look=ultra (headless default 16)
//   ?sun_el=42&sun_az=200                          presentation sun: elevation and azimuth in degrees, azimuth
//                                                  clockwise from north = the direction the light comes FROM
//   ?p_env=0.3&p_bg=0.34&p_sun=3&p_hemi=0.08&p_expo=1&p_fog=0.0012&p_rayleigh=1.8&p_turbidity=2.5
//   ?in_meter=0.5&in_expo=1&in_fill=0.45&in_lamps=0.35&in_ao=0.35&in_bounces=3&in_probe=1   the indoor light (#41), camera in a room
//   ?live_lamps=6                                  how many lamps light at once (the scene's point lights above it share stand-ins)
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
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { Sky } from "three/addons/objects/Sky.js";
import { HDRLoader } from "three/addons/loaders/HDRLoader.js";
import * as house from "housekit";
import { finishesReady, loadFinishes, loadTexture, metricUVs } from "./finishes.js";
import { planData, planStoreys, planSVG } from "./plan2d.js";
import { loadContext } from "./context.js";
import { isMirror, Mirrors } from "./mirror.js";

const params = new URLSearchParams(location.search);
const HEADLESS = params.get("headless") === "1";
const WALK = params.get("walk") === "1";
// the real surroundings (#39): a folder the viewer names, drawn on presentation pages only
const CONTEXT = ["presentation", "ultra"].includes(params.get("look")) ? params.get("context") : null;
// presentation looks (see the header): never on the builder's or the critic's pages
const LOOK = params.get("look");
const PRESENTATION = LOOK === "presentation" || LOOK === "ultra";
const ULTRA = LOOK === "ultra";
// a presentation page is a "high" page with more on top
const QUALITY = PRESENTATION ? "high" : (params.get("quality") ?? (HEADLESS ? "high" : "medium"));
// ambient occlusion + anti-aliasing: the final look. Interactive pages fall back to plain
// rendering automatically when the machine cannot sustain it (see the frame-time guard).
const EFFECTS = QUALITY === "high" && params.get("fx") !== "0"; // fx=0: without the post-processing (diagnostics)
// mirrors reflect the room while walking (kit/mirror.js); mirrors=0: the light probe's reflection instead
const MIRRORS = QUALITY !== "low" && params.get("mirrors") !== "0";
const CULL = params.get("cull") !== "0"; // walk frames draw only the rooms in view (#56)
const MERGE = params.get("merge") !== "0"; // the walk draws the house's static meshes merged (#56)
const MERGED_LAYER = 30; // where the meshes drawn as part of a merged one go: no camera draws it
const AO_FROM_SCENE = params.get("ao_gbuffer") !== "1"; // the occlusion reads the scene pass's depth (#56); 1: its own pass
const BOX = params.get("box") !== "0"; // glossy reflections projected on the room's box (#59)
const TILE = Number(params.get("merge_tile")) || 16; // metres: the shell and the outside are merged by tiles this size
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
  farHaze: 100000, // metres: the far landscape keeps a third of its colour at this distance (a Swiss summer day)
  vignette: 0.28,
  contrast: 1.08, // about mid grey, after tone mapping (GradeShader)
  saturation: 1.06,
  sky: { turbidity: 2.5, rayleigh: 1.8, mieCoefficient: 0.005, mieDirectionalG: 0.85 },
  // the photographed sky (setupPhotographedSky): its fill, relative to the analytic sky's at the same
  // light on a level surface (walls see mostly the horizon, darker and bluer in a photographed sky),
  // and the saturation of the sky the camera sees (tone mapping greys a blue sky)
  photoSky: { env: 2.4, envSaturation: 0.4, zenith: "#5180d6", horizon: "#9bbeee" }, // 2026-09-25: shaded plaster matched (TestVillaGille); the blue measured on a daylight photo (123, 164, 231)
};

// calibration overrides (see the header): numbers only, anything else keeps the default
for (const [key, path] of [
  ["p_env", ["environmentIntensity"]], ["p_bg", ["backgroundIntensity"]], ["p_sun", ["sunIntensity"]], ["p_hemi", ["hemi", "intensity"]],
  ["p_expo", ["exposure"]], ["p_fog", ["fogDensity"]], ["p_rayleigh", ["sky", "rayleigh"]], ["p_turbidity", ["sky", "turbidity"]],
  ["p_contrast", ["contrast"]], ["p_sat", ["saturation"]], ["p_vignette", ["vignette"]], ["p_meadow", ["meadow"]],
  ["p_skyenv", ["photoSky", "env"]], ["p_skyenvsat", ["photoSky", "envSaturation"]],
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
  rest: null, // the frame at rest converging (RestPass, #56)
  scene: null,
  houseGroup: null,
  bounds: null,
};

// ?debug=1: the page's state in the console (window.__housekitState), for experiments
if (params.get("debug") === "1") window.__housekitState = state;

window.__house = {
  get ready() { return state.ready; },
  get errors() { return state.errors; },
  listViews: () => Object.keys(state.views),
  setView: (name) => setView(name),
  // any viewpoint (scene metres): for checks and look sheets that need a view the scene did not name
  setCamera: (position, target) => {
    state.views.custom = { pos: position, target: new THREE.Vector3(...target) };
    return setView("custom");
  },
  // the 2D plan of storey `index` (SVG), as the viewer's Plan dialog shows it
  plan2d: (index = 0, opts = {}) => {
    const data = state.houseGroup && planData(state.houseGroup, index);
    return data ? planSVG(data, opts) : null;
  },
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
  // debugging: the room whose own light the presentation look uses (null outdoors)
  get indoor() {
    if (!state.indoor) return null;
    const { renderer, scene, hemi } = state;
    return { ...state.indoor, exposure: renderer.toneMappingExposure, envIntensity: scene.environmentIntensity,
      hemi: hemi?.intensity, probes: state.probes?.size ?? 0, outdoor: state.outdoor && { exposure: state.outdoor.exposure,
        envIntensity: state.outdoor.envIntensity, hemi: state.outdoor.hemi } };
  },
  // debugging: the lamps lighting the last frame (null when every point light of the scene does):
  // the room each lights, where it hangs, how far up its fade is
  get lamps() {
    const L = state.lamps;
    const r = (v) => Math.round(v * 100) / 100;
    return L && { total: L.lamps.length, live: L.slots.map((s) => s.spot && { room: s.spot.key, at: s.spot.p.toArray().map(r), w: r(s.w), intensity: r(s.light.intensity) }) };
  },
  // debugging: the presentation look in force on this page (after the calibration overrides)
  get look() { return PRESENTATION ? JSON.parse(JSON.stringify(PRESENTATION_LOOK)) : null; },
  // debugging: progress of the ultra accumulation, null on other pages
  get accumulate() {
    const a = state.accumulate;
    return a ? { count: a.count, total: a.samples, effects: state.composer !== null } : null;
  },
  // the SIA 416 surfaces and volumes, the finishes take-off and the habitability figures (#47, #48):
  // kit/quantities.js on the built scene; the viewer's "Surfaces et volumes" dialog asks for them
  quantities: () => sceneQuantities(),
  // deterministic plausibility audit of the built scene (see house.audit): the renderer
  // appends it to the builder's tool results and gives it to the critic
  // (an interior's own lines first: the furnishing job reads them, and the renderer keeps the first 20)
  audit: async () => {
    if (!state.houseGroup) return [];
    const inner = await interiorAudit();
    return storeys().length ? [...inner, ...house.audit(state.houseGroup)] : [...house.audit(state.houseGroup), ...inner];
  },
  // what the backend reads with every render (the builder's plan check): the rooms of the floor
  // plans and, per storey, the framing in metres of its plan-section view
  // and the furniture measured piece by piece (kit/layout.js: the builder's measure tool, #68)
  report: async () => ({
    rooms: sceneRooms().map(({ name, use, area, polygon, y }) => ({ name, use, area, polygon, y })),
    planSections: storeys().map((s, i) => ({ view: `plan-section-${i + 1}`, y: s.y, bbox: sectionFrame(i) })),
    layout: storeys().length ? (await sceneLayout()).report : null,
  }),
  // walk mode (?walk=1): the rooms of the scene's floor plans, glide / jump to a place
  get rooms() {
    const out = [];
    state.houseGroup?.traverse((o) => { if (o.userData?.kind === "floorPlan") out.push(...o.userData.rooms); });
    return out;
  },
  // glide to (x, z), arriving with { yaw, pitch } (radians) when given
  walkTo: (x, z, view) => state.walk?.goTo(x, z, view),
  // the viewpoint the walk arrived at in the last room (#54): where, the heading, the shares of the
  // view that are near (< 1 m), the room's own, a window, the share of the room's floor in view
  get viewpoint() { return state.walk?.lastView ?? null; },
  // debugging: what the walk's precompile found (#55): the environment's size, indoors or not, programs before/after
  get precompile() { return state.precompile ?? null; },
  // debugging: the walk's merge of the house (#56): meshes merged, into how many, milliseconds
  get merge() { return state.merge ?? null; },
  // debugging and test fixtures: the house as the scene built it
  get houseGroup() { return state.houseGroup ?? null; },
  // debugging: the glossy materials whose reflections are projected on the room's box (#59), and the box now
  get envBox() { return { patched: state.boxPatched ?? 0, on: ENV_BOX.on.value, min: ENV_BOX.min.value.toArray(), max: ENV_BOX.max.value.toArray(), at: ENV_BOX.at.value.toArray() }; },
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
  // the viewer's "Go to a room" (#55): a glide to the room's viewpoint, or a fade when it is far, on
  // another storey or in another flat; resolves once there ("glide" or "fade")
  goToRoom: (room) => goToRoom(room),
  // the guided tour (#57): the rooms in order, a glide or a fade to each, a pause; any key, click or
  // wheel stops it
  startTour: () => startTour(),
  // the dollhouse (#62): storey `index` (in the order of the floor plans' levels) from above, cut at its ceiling
  dollhouse: (index) => showDollhouse(index),
  get details() { return state.details ?? null; },
  get frameLog() { return FRAME_LOG; },
  // the last walk frame's room culling: the camera's room, the rooms drawn, where each mesh counts
  get culling() {
    const byMesh = new Map();
    for (const [k, { meshes }] of state.meshesByRoom ?? []) for (const o of meshes) byMesh.set(o, k);
    return { ...state.lastCull, roomOf: (o) => byMesh.get(o) ?? null, doorways: (y) => storeyDoorways(y).map((d) => [d.minus.key, d.plus.key, d.c]) };
  },
  get rest() { return state.rest ? { count: state.rest.count, samples: state.rest.samples } : null; },
  get dollhousing() { return state.dollhouse ? { index: state.dollhouse.index, hidden: state.dollhouse.hidden.length } : null; },
  stopTour: () => stopTour(),
  get tour() {
    const t = state.tour;
    return t ? { running: t.running, index: t.i, total: t.stops.length, room: t.stops[t.i]?.name ?? null, stops: t.stops.map((r) => r.name) } : null;
  },
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
  // the sun's shadow is drawn when the sun moves, not on every frame: see syncShadows
  renderer.shadowMap.autoUpdate = false;
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
  state.hemi = hemi;
  const sun = new THREE.DirectionalLight(PALETTE.sun, 2.0);
  sun.position.set(-18, 28, 12);
  sun.castShadow = renderer.shadowMap.enabled;
  state.sun = sun;
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
    await setupPresentationSky(scene, renderer, sun, sunSpec);
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
    // the ambient occlusion reads the scene pass's own depth (its normals rebuilt from it) instead of
    // drawing the whole scene a second time for its normals (#56): each of the composer's two
    // targets gets a depth texture (the second is a clone of the first)
    const sharedDepth = PRESENTATION && !ULTRA && AO_FROM_SCENE;
    if (sharedDepth) target.depthTexture = new THREE.DepthTexture(drawing.x, drawing.y);
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
    gtao.updateGtaoMaterial({ radius: 0.6, distanceExponent: 1, thickness: 1, scale: 1, samples: Number(params.get("ao_samples")) || (QUALITY === "high" ? 16 : 8), distanceFallOff: 1, screenSpaceRadius: false });
    if (sharedDepth) gtao.setGBuffer(target.depthTexture); // pointed at the right target before each frame (renderFrame)
    composer.addPass(gtao);
    if (PRESENTATION) {
      // the indoor white balance (#58): linear gains before the tone mapping, neutral outdoors
      const balance = new ShaderPass(BalanceShader);
      composer.addPass(balance);
      state.balancePass = balance;
      state.balanceTarget = new THREE.Vector3(1, 1, 1);
    }
    composer.addPass(new OutputPass());
    if (PRESENTATION) {
      const grade = new ShaderPass(GradeShader);
      grade.uniforms.strength.value = PRESENTATION_LOOK.vignette;
      grade.uniforms.contrast.value = PRESENTATION_LOOK.contrast;
      grade.uniforms.saturation.value = PRESENTATION_LOOK.saturation;
      composer.addPass(grade);
      if (!ULTRA && !HEADLESS && REST.on) {
        state.rest = new RestPass(drawing.x, drawing.y, REST.samples);
        composer.addPass(state.rest);
      }
    } else {
      composer.addPass(new SMAAPass(W, H));
    }
    state.composer = composer;
    state.gtao = gtao;
  }

  // progress while the scene loads (#60): the viewer shows the version's picture and this until the
  // first frame ("loading": the files three's loaders fetch; "drawing": the first frame, its shaders
  // compiled and its textures uploaded, most of the wait on a slow GPU path)
  const progress = (msg) => { try { parent.postMessage({ type: "house:progress", ...msg }, "*"); } catch { /* noop */ } };
  let progressAt = 0;
  if (!HEADLESS) {
    THREE.DefaultLoadingManager.onProgress = (_url, loaded, total) => {
      const now = performance.now();
      if (now - progressAt < 100 && loaded < total) return;
      progressAt = now;
      progress({ phase: "loading", loaded, total });
    };
  }

  // ---- user scene ----
  let result = null;
  try {
    // textured surfaces: loaded first so house.js mat.* hands out textured materials (plain without the files)
    await loadFinishes().catch((e) => console.warn(`housekit: finishes not loaded: ${e?.message ?? e}`));
    result = await buildScene({ THREE, scene, house, group: houseGroup, sun, ground, renderer, camera });
    house.texturePitchedRoofs(houseGroup);
    // the details a builder leaves out of its rooms (#43): skirting, inner window sills
    let plans = false;
    houseGroup.traverse((o) => { if (o.userData?.kind === "floorPlan") plans = true; });
    if (plans) {
      const t0 = performance.now();
      const added = (await import("./interior.js")).roomDetails(houseGroup);
      state.details = { added, ms: Math.round(performance.now() - t0) };
    }
    metricUVs(houseGroup);
    await finishesReady(); // only the texture sets the scene uses were fetched
    capPointLights(houseGroup, scene);
  } catch (err) {
    recordError(`buildScene failed: ${err?.stack ?? err}`);
  }

  if (CONTEXT) {
    try {
      state.context = await loadContext(CONTEXT, { houseGroup, heightAt: house.groundY });
      scene.add(state.context.group);
      // the builder's ground is clipped to its site there (context.js): the real ground beyond it
      if (state.context.clipped) renderer.localClippingEnabled = true;
    } catch (err) {
      console.warn(`housekit: surroundings not loaded: ${err?.message ?? err}`);
    }
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

  if (!HEADLESS) progress({ phase: "drawing" });
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
  // ?tour=1 or #autoplay: the guided tour starts once the page is ready (#57)
  if (!HEADLESS && (params.get("tour") === "1" || location.hash.includes("autoplay"))) setTimeout(() => startTour().catch((err) => recordError(err?.message ?? err)), 0);
  window.addEventListener("message", (e) => {
    // a page opened on its own is its own parent: never act on what it announced itself
    if (e.source === window) return;
    const d = e.data ?? {};
    if (d.type === "house:setView") { stopWalk(); setView(d.view); }
    if (d.type === "house:walk") (d.on ? startWalk(d.room) : Promise.resolve(stopWalk())).catch((err) => recordError(err?.message ?? err));
    if (d.type === "house:jumpTo") goToRoom(d.room).catch((err) => recordError(err?.message ?? err));
    if (d.type === "house:tour") (d.on ? startTour() : Promise.resolve(stopTour())).catch((err) => recordError(err?.message ?? err));
    if (d.type === "house:dollhouse") (typeof d.index === "number" && d.index >= 0 ? showDollhouse(d.index) : endDollhouse());
    if (d.type === "house:outline") {
      // the alignment editor: the scene seen from above, in scene metres (x east, z south)
      e.source?.postMessage({ type: "house:outline", id: d.id, ...sceneOutline() }, "*");
    }
    if (d.type === "house:quantities") {
      // the viewer's "Surfaces et volumes" dialog (#47, #48)
      sceneQuantities().then((quantities) => e.source?.postMessage({ type: "house:quantities", id: d.id, quantities, error: null }, "*"))
        .catch((err) => e.source?.postMessage({ type: "house:quantities", id: d.id, quantities: null, error: String(err?.message ?? err) }, "*"));
    }
    if (d.type === "house:plan2d") {
      // the viewer's Plan dialog: storey `index` as an SVG plan (furnished or fittings only)
      const storeys = planStoreys(state.houseGroup);
      const index = Math.max(0, Math.min(storeys.length - 1, d.index ?? 0));
      let svg = null, error = null;
      try {
        const data = storeys.length ? planData(state.houseGroup, index) : null;
        svg = data ? planSVG(data, { furnished: d.furnished !== false }) : null;
      } catch (err) { error = String(err?.message ?? err); }
      e.source?.postMessage({ type: "house:plan2d", id: d.id, storeys, index, svg, error }, "*");
    }
  });
  state.ready = true;
  try {
    parent.postMessage({
      type: "house:ready",
      views: Object.keys(state.views),
      // walk mode: the rooms of the scene's floor plans; the credit lines of attribution-licensed models
      rooms: sceneRooms().map(({ name, use, area }) => ({ name, use, area })),
      credits: [...(globalThis.__housekitCredits?.() ?? []), ...(state.context?.credits ?? [])],
      tour: true, // this renderer has the guided tour (house:tour)
      quantities: true, // and the SIA 416 quantities (house:quantities)
      // the storeys with a floor plan, for the dollhouse view (house:dollhouse { index })
      storeys: storeys().map((st, index) => ({ index, y: st.y, rooms: st.plan.userData.rooms.length })),
    }, "*");
  } catch { /* noop */ }
  // Render on demand: only when the camera moves (or something asks for a frame). An idle page
  // costs nothing, and machines without a GPU stay responsive.
  state.needsRender = true;
  controls.addEventListener("change", () => { state.needsRender = true; state.accumulate?.reset(); });
  // frame-time guard: if the first frames with effects are slow (no GPU, weak laptop),
  // drop the post-processing rather than freeze the page. Ultra is an explicit choice and
  // keeps going: it only converges more slowly.
  const counted = [];
  let slowRun = 0;
  renderer.setAnimationLoop((now) => {
    const u0 = FRAME_LOG ? performance.now() : 0;
    const moved = state.walk ? state.walk.update() : controls.update();
    const updateMs = FRAME_LOG ? performance.now() - u0 : 0;
    if (moved) state.accumulate?.reset();
    // anything new on screen starts the frame at rest again from the frame itself
    if (moved || state.needsRender) state.rest?.reset();
    const converging = state.composer && state.accumulate && !state.accumulate.done;
    const resting = state.composer && state.rest && state.rest.count > 0 && !state.rest.done;
    if (!moved && !state.needsRender && !converging && !resting) return;
    state.needsRender = false;
    const t0 = performance.now();
    const made = (renderer.info.programs?.length ?? 0) + renderer.info.memory.textures;
    renderFrame();
    if (converging) {
      const a = state.accumulate;
      try { parent.postMessage({ type: "house:accumulate", count: a.count, total: a.samples }, "*"); } catch { /* noop */ }
    }
    const probeMs = state.probeMs ?? 0;
    state.probeMs = 0;
    if (FRAME_LOG) {
      // ?framelog=1: what each drawn frame cost and changed, to find the slow ones (#56)
      const info = renderer.info;
      FRAME_LOG.push({ t: Math.round(t0), raf: Math.round(now), updateMs: Math.round(updateMs * 10) / 10, ms: Math.round((performance.now() - t0) * 10) / 10, probeMs: Math.round(probeMs), calls: info.render.calls,
        programs: info.programs?.length ?? 0, textures: info.memory.textures, geometries: info.memory.geometries, moved: !!moved,
        at: [Math.round(state.camera.position.x * 100) / 100, Math.round(state.camera.position.z * 100) / 100], yaw: Math.round((state.walk?.yaw ?? 0) * 100) / 100,
        room: state.indoor?.name ?? null, doorway: state.indoor?.doorway ? `${state.indoor.doorway.from}>${state.indoor.doorway.to}:${state.indoor.doorway.w}` : null,
        drawn: state.lastCull?.seen?.length ?? null });
      if (FRAME_LOG.length > 2000) FRAME_LOG.splice(0, 1000);
    }
    // a frame that compiled a shader or uploaded a texture, or one behind a fade, is a one-off cost,
    // not how fast this machine draws: not counted (one such frame used to switch the effects off
    // for the whole visit, the first glide of a walk at the latest: flat, darker pictures)
    const oneOff = (renderer.info.programs?.length ?? 0) + renderer.info.memory.textures !== made
      || (state.veil && state.veil.style.opacity !== "0");
    if (state.frameCheckSkip > 0) state.frameCheckSkip -= 1;
    else if (state.composer && !ULTRA && counted.length < 8 && !oneOff) {
      const dt = performance.now() - t0 - probeMs;
      counted.push(dt);
      slowRun = dt > 250 ? slowRun + 1 : 0;
      // three frames in a row over 250 ms (< 4 fps), or the median of 8 over 90 ms: not sustainable
      // (a slow frame here and there is the GPU driver's first draw of something, not the machine)
      const median = counted.length === 8 ? [...counted].sort((a, b) => a - b)[4] : 0;
      if (slowRun >= 3 || median > 90) {
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
async function setupPresentationSky(scene, renderer, sun, spec) {
  // a photographed sky (blue with scattered cumulus) unless the page asks for the analytic one
  if (params.get("sky") !== "analytic") {
    try {
      await setupPhotographedSky(scene, renderer, spec);
      return;
    } catch (err) {
      console.warn(`housekit: photographed sky not loaded (${err?.message ?? err}), analytic sky instead`);
    }
  }
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

// The analytic sky's light, measured once: its upper hemisphere's cosine-weighted mean radiance for
// the default sun (42°, 200°). A photographed sky is scaled to shine as much, so the calibrated
// environment and background intensities keep their meaning.
const ANALYTIC_SKY_IRRADIANCE = 1.1266;
const SKY_DIR = new URL("/kit/sky/v6/", import.meta.url); // this snapshot's sky (make_sky.py)

/**
 * A photographed sky (kit/sky/, made by scripts/make_sky.py from a Poly Haven HDRI, CC0): its 1k
 * radiance with the sun taken out lights the scene, its 4k picture is the sky the camera sees, both
 * turned so the photographed sun stands at the azimuth of the scene's sun. The fog is the sky's
 * colour just above the horizon, away from the sun.
 */
async function setupPhotographedSky(scene, renderer, spec) {
  const look = PRESENTATION_LOOK;
  const meta = await fetch(new URL("sky.json", SKY_DIR)).then((r) => { if (!r.ok) throw new Error(`sky.json: HTTP ${r.status}`); return r.json(); });
  const [env, clouds, cloudsMask] = await Promise.all([
    new HDRLoader().setDataType(THREE.FloatType).loadAsync(new URL(meta.env, SKY_DIR).href),
    loadTexture(new URL(meta.clouds, SKY_DIR).href),
    loadTexture(new URL(meta.cloudsMask, SKY_DIR).href),
  ]);
  // turn: the photographed sun's azimuth onto the scene's (columns run with azimuth, see make_sky.py)
  const shift = (((spec.azimuth - meta.sun.azimuth) / 360) % 1 + 1) % 1;
  const k = ANALYTIC_SKY_IRRADIANCE / meta.irradiance;
  const { width: w, height: h, data } = env.image;
  const rolled = new Float32Array(data.length);
  const cols = Math.round(shift * w), ch = data.length / (w * h);
  // as light, the blue of a real sky is toned down (look.photoSky.envSaturation): the shade stays
  // the near-neutral fill the look was calibrated with, the sky the camera sees keeps its colour
  const es = look.photoSky.envSaturation;
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const from = (y * w + x) * ch, to = (y * w + ((x + cols) % w)) * ch;
    const r = data[from], g = data[from + 1], b = data[from + 2];
    const l = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    rolled[to] = (l + (r - l) * es) * k;
    rolled[to + 1] = (l + (g - l) * es) * k;
    rolled[to + 2] = (l + (b - l) * es) * k;
    if (ch > 3) rolled[to + 3] = data[from + 3];
  }
  env.image.data = rolled;
  env.mapping = THREE.EquirectangularReflectionMapping;
  env.needsUpdate = true;
  const pmrem = new THREE.PMREMGenerator(renderer);
  const skyMap = pmrem.fromEquirectangular(env).texture;
  pmrem.dispose();

  // the fog: the rows 0.5° to 4° above the horizon, the half of the sky turned away from the sun
  const horizon = new THREE.Color(0, 0, 0);
  let n = 0;
  const sunCol = ((0.5 + (spec.azimuth - 90) / 360) % 1 + 1) % 1;
  for (let y = Math.floor(h * (86 / 180)); y < Math.floor(h * (89.5 / 180)); y++) for (let x = 0; x < w; x++) {
    const du = Math.abs(((x + 0.5) / w - sunCol + 1.5) % 1 - 0.5);
    if (du < 0.25) continue;
    const i = (y * w + x) * ch;
    horizon.r += rolled[i]; horizon.g += rolled[i + 1]; horizon.b += rolled[i + 2]; n++;
  }
  horizon.multiplyScalar(look.backgroundIntensity / Math.max(1, n));
  env.dispose();

  // the sky the camera sees: a dome at the far plane. Its blue is designed (the gradient of a
  // daylight photograph of a blue sky, zenith to horizon) and the photographed clouds are laid over
  // it, both drawn so that the frame's tone mapping shows exactly these colours: a real sky's blue,
  // measured and then tone-mapped, comes out grey and mauve. ?skymode=clear leaves the clouds out.
  for (const t of [clouds, cloudsMask]) {
    t.colorSpace = THREE.SRGBColorSpace;
    t.wrapS = THREE.RepeatWrapping;
    t.anisotropy = 8;
  }
  const hex = (name, fallback) => new THREE.Color(params.get(name) ? `#${params.get(name)}` : fallback);
  const dome = new THREE.Mesh(
    new THREE.SphereGeometry(1, 96, 48),
    new THREE.ShaderMaterial({
      side: THREE.BackSide,
      depthWrite: false,
      uniforms: {
        uClouds: { value: clouds },
        uCloudsMask: { value: cloudsMask },
        uShift: { value: shift },
        uSpan: { value: THREE.MathUtils.degToRad(90 + meta.skyBelow) },
        uWithClouds: { value: params.get("skymode") === "clear" ? 0 : 1 },
        uExposure: { value: look.exposure },
        uZenith: { value: hex("p_zenith", look.photoSky.zenith) },
        uHorizon: { value: hex("p_horizon", look.photoSky.horizon) },
      },
      vertexShader: /* glsl */ `
        precision highp float;
        varying vec3 vDir;
        void main() {
          vDir = position;
          vec4 p = projectionMatrix * vec4(mat3(viewMatrix) * position, 1.0);
          gl_Position = p.xyww; // on the far plane, whatever the camera's distance
        }`,
      fragmentShader: /* glsl */ `
        // full precision: in half precision the panorama's coordinate (atan) takes ~2000 values over a
        // turn, and an 8k picture is then read in steps of four pixels (blocks in the clouds)
        precision highp float;
        precision highp sampler2D;
        #include <common>
        uniform sampler2D uClouds, uCloudsMask;
        uniform float uShift, uSpan, uWithClouds, uExposure;
        uniform vec3 uZenith, uHorizon;
        varying vec3 vDir;
        // three's ACESFilmicToneMapping, undone: the colour to draw so the tone mapping shows 'display'
        vec3 inverseACES(vec3 display) {
          const mat3 inMat = mat3(vec3(0.59719, 0.07600, 0.02840), vec3(0.35458, 0.90834, 0.13383), vec3(0.04823, 0.01566, 0.83777));
          const mat3 outMat = mat3(vec3(1.60475, -0.10208, -0.00327), vec3(-0.53108, 1.10813, -0.07276), vec3(-0.07367, -0.00605, 1.07602));
          vec3 y = clamp(inverse(outMat) * clamp(display, 0.0, 0.96), 0.0, 0.99);
          // RRTAndODTFit: (v² + 0.0245786 v - 0.000090537) / (0.983729 v² + 0.4329510 v + 0.238081) = y
          vec3 a = 1.0 - 0.983729 * y, b = 0.0245786 - 0.4329510 * y, c = -0.000090537 - 0.238081 * y;
          vec3 v = (-b + sqrt(max(b * b - 4.0 * a * c, 0.0))) / (2.0 * a);
          return max(inverse(inMat) * v, 0.0) * 0.6 / uExposure;
        }
        void main() {
          vec3 d = normalize(vDir);
          float t = pow(clamp(d.y, 0.0, 1.0), 0.45);
          vec3 display = mix(uHorizon, uZenith, t);
          if (uWithClouds > 0.5) {
            float u = atan(d.z, d.x) * RECIPROCAL_PI2 + 0.5 - uShift;
            float v = 1.0 - clamp((0.5 * PI - asin(clamp(d.y, -1.0, 1.0))) / uSpan, 0.0, 1.0);
            // the panorama wraps where u jumps from 1 to 0: its derivatives are taken modulo 1 (a jump of
            // almost a whole turn between neighbouring pixels is a small step the other way), or the
            // mipmap chain draws a seam
            float dux = dFdx(u), duy = dFdy(u);
            dux -= floor(dux + 0.5);
            duy -= floor(duy + 0.5);
            vec2 gx = vec2(dux, dFdx(v)), gy = vec2(duy, dFdy(v));
            // the clouds, found at build time (make_sky.py): their mask, and their brightness times it
            vec2 uv = vec2(fract(u), v);
            float cloud = textureGrad(uCloudsMask, uv, gx, gy).r;
            float lit = textureGrad(uClouds, uv, gx, gy).r;
            // they fade out toward the horizon, where the photograph's haze would read as a grey band
            float fade = smoothstep(0.04, 0.16, d.y);
            display = display * (1.0 - cloud * fade) + vec3(lit * fade) * vec3(1.0, 0.99, 0.97);
          }
          gl_FragColor = vec4(inverseACES(display), 1.0);
          #include <tonemapping_fragment>
          #include <colorspace_fragment>
        }`,
    }),
  );
  dome.name = "Sky";
  dome.frustumCulled = false;
  dome.renderOrder = -1;
  dome.userData = { kind: "sky", excludeFromBounds: true };
  scene.environment = skyMap;
  scene.environmentIntensity = look.environmentIntensity * look.photoSky.env;
  state.presentation = { skyMap, horizon, dome };
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
  scene.environmentIntensity = look.environmentIntensity * (state.presentation.dome ? look.photoSky.env : 1);
  if (state.presentation.dome) {
    // the photographed sky: its dome, and its horizon as the clear colour behind it
    scene.background = horizon.clone();
    if (!state.presentation.dome.parent) scene.add(state.presentation.dome);
  } else {
    scene.background = skyMap;
    scene.backgroundIntensity = look.backgroundIntensity;
    scene.backgroundBlurriness = 0;
  }
  // the haze: toward the colour the sky shows at the horizon (the photographed sky's: exactly what the
  // dome draws there, so the land fades into it without a seam)
  const dome = state.presentation.dome;
  const haze = dome ? inverseACES(dome.material.uniforms.uHorizon.value, look.exposure) : horizon;
  if (dome) scene.background = haze.clone();
  if (state.context?.far) {
    // the real landscape to the horizon: the far terrain hazes by its own distance (tens of km); the
    // scene itself (a few hundred metres) barely
    scene.fog = new THREE.FogExp2(haze, 0.00001);
    // the far landscape mixes its haze as the screen shows it: the horizon's display colour
    const display = dome ? dome.material.uniforms.uHorizon.value : new THREE.Color(0.62, 0.74, 0.9);
    state.context.setHaze(display, look.farHaze, look.exposure);
  } else {
    scene.fog = new THREE.FogExp2(haze, look.fogDensity);
  }
  state.fog = scene.fog;
  const hemi = scene.children.find((o) => o.isHemisphereLight);
  if (hemi) {
    hemi.color.set(look.hemi.sky);
    hemi.groundColor.set(look.hemi.ground);
    hemi.intensity = look.hemi.intensity;
  }
  applyPresentationSun(sun, spec);
  if (state.context?.far) ground.visible = false; // the real land reaches the horizon: no meadow
  else presentationGround(scene, ground);
}

/**
 * three's ACESFilmicToneMapping undone for one colour: the linear radiance that the tone mapping shows
 * as `display` (linear, before the output transfer). Same as the sky dome's shader.
 */
function inverseACES(display, exposure = 1) {
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const outInv = new THREE.Matrix3().set(1.60475, -0.53108, -0.07367, -0.10208, 1.10813, -0.00605, -0.00327, -0.07276, 1.07602).invert();
  const inInv = new THREE.Matrix3().set(0.59719, 0.35458, 0.04823, 0.07600, 0.90834, 0.01566, 0.02840, 0.13383, 0.83777).invert();
  const y = new THREE.Vector3(clamp(display.r, 0, 0.96), clamp(display.g, 0, 0.96), clamp(display.b, 0, 0.96)).applyMatrix3(outInv);
  const v = ["x", "y", "z"].map((k) => {
    const yy = clamp(y[k], 0, 0.99);
    const a = 1 - 0.983729 * yy, b = 0.0245786 - 0.4329510 * yy, c = -0.000090537 - 0.238081 * yy;
    return (-b + Math.sqrt(Math.max(b * b - 4 * a * c, 0))) / (2 * a);
  });
  const out = new THREE.Vector3(...v).applyMatrix3(inInv).multiplyScalar(0.6 / exposure);
  return new THREE.Color(Math.max(out.x, 0), Math.max(out.y, 0), Math.max(out.z, 0));
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
  let lawn = material?.userData?.baseColor?.clone() ?? (material?.color ? material.color.clone() : new THREE.Color(PALETTE.grass));
  // a ground painted with vertex colours on a white material: the colour it reads as is their mean
  const colors = plot.geometry?.attributes?.color;
  if (material?.vertexColors && colors?.count) {
    const mean = new THREE.Color(0, 0, 0);
    for (let i = 0; i < colors.count; i++) { mean.r += colors.getX(i); mean.g += colors.getY(i); mean.b += colors.getZ(i); }
    lawn = mean.multiplyScalar(1 / colors.count).multiply(material.color ?? new THREE.Color(1, 1, 1));
  }
  // the plot: the terrains' footprints (holes in the sheet), or the house and its site on flat
  // ground (no hole: the sheet is then the ground under the house)
  const inset = 0.1; // the meadow reaches 10 cm under the terrain's rim: no hairline at the edge
  const holes = terrains
    .map((t) => new THREE.Box3().setFromObject(t))
    .filter((b) => !b.isEmpty() && b.max.x - b.min.x > 2 * inset && b.max.z - b.min.z > 2 * inset)
    .map((b) => b.expandByVector(new THREE.Vector3(-inset, 0, -inset)));
  let box = holes.length ? holes.reduce((u, b) => u.union(b), new THREE.Box3()) : framingBounds(state.houseGroup).expandByScalar(12);
  if (box.isEmpty()) box.set(new THREE.Vector3(-45, 0, -45), new THREE.Vector3(45, 0, 45));
  // with the real surroundings (#39) the meadow starts at the rim of their disc and lies under it
  const ctx = state.context;
  if (ctx) {
    const { x, z, radius } = ctx.disc;
    box = new THREE.Box3(new THREE.Vector3(x - radius, 0, z - radius), new THREE.Vector3(x + radius, 0, z + radius));
    holes.length = 0;
  }
  const meadow = lawn.clone().multiply(new THREE.Color(look.meadow, look.meadow * 0.94, look.meadow * 0.8));
  const grainMean = new THREE.Color().setRGB(245 / 255, 245 / 255, 245 / 255, THREE.SRGBColorSpace).r;
  lawn.multiplyScalar(1 / grainMean);
  meadow.multiplyScalar(1 / grainMean);

  // the sheet: a grid whose lines include every hole's edges (a cell is then either inside a
  // hole or outside it), 2 m cells through the followed and eased bands, coarser to the edge
  const follow = ctx ? 0 : 40, ease = ctx ? 150 : 80, step = ctx ? 4 : 2, tile = 6;
  const halfSize = Math.max(1400, Math.max(-box.min.x, box.max.x, -box.min.z, box.max.z) + follow + ease + 100);
  const lines = (lo, hi) => {
    const s = new Set([lo, hi]);
    if (ctx) for (let v = lo + step; v < hi; v += step) s.add(v); // under the disc too: its rim is round
    for (let k = 1; k * step <= follow + ease; k++) { s.add(lo - k * step); s.add(hi + k * step); }
    for (const d of [30, 80, 180, 350, 600, 900]) { s.add(lo - follow - ease - d); s.add(hi + follow + ease + d); }
    s.add(-halfSize); s.add(halfSize);
    return [...s].filter((v) => Math.abs(v) <= halfSize).sort((a, b) => a - b);
  };
  const xs = lines(box.min.x, box.max.x), zs = lines(box.min.z, box.max.z);
  // the far height: the mean of the scene's ground at the end of the followed band, so the
  // easing starts from where the land already is
  const ctxEdge = (x, z) => {
    // the real ground just inside the rim of the disc, on the way from its centre to (x, z)
    const { x: cx, z: cz, radius } = ctx.disc;
    const d = Math.hypot(x - cx, z - cz) || 1, r = Math.min(d, radius - 2);
    const y = ctx.heightAt(cx + ((x - cx) * r) / d, cz + ((z - cz) * r) / d);
    return Number.isFinite(y) ? y : 0;
  };
  const heightAt = ctx
    ? (x, z) => ctxEdge(x, z) - 0.6 // under the real ground, never through it
    : (x, z) => { const y = house.groundY(x, z); return Number.isFinite(y) ? y : 0; };
  const outsidePlot = ctx
    ? (x, z) => Math.max(0, Math.hypot(x - ctx.disc.x, z - ctx.disc.z) - ctx.disc.radius)
    : (x, z) => Math.hypot(Math.max(box.min.x - x, x - box.max.x, 0), Math.max(box.min.z - z, z - box.max.z, 0));
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
  const underDisc = (x0, x1, z0, z1) => ctx && [[x0, z0], [x1, z0], [x0, z1], [x1, z1]]
    .every(([x, z]) => Math.hypot(x - ctx.disc.x, z - ctx.disc.z) < ctx.disc.radius - 30);
  const inHole = (x0, x1, z0, z1) => underDisc(x0, x1, z0, z1) || holes.some((h) => x0 >= h.min.x - 1e-6 && x1 <= h.max.x + 1e-6 && z0 >= h.min.z - 1e-6 && z1 <= h.max.z + 1e-6);
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
  // the photo fades into this meadow at the rim of the disc (the grain's mean put back)
  ctx?.setMeadow(meadow.clone().multiplyScalar(grainMean));
  land.userData = { kind: "terrain", excludeFromBounds: true };
  baseGround.visible = false;
  scene.add(land);
}

/**
 * The grade at the end of a presentation page, after tone mapping and the output colour space:
 * a touch of contrast about mid grey, a little saturation, a light vignette.
 */
const NEUTRAL = new THREE.Vector3(1, 1, 1);

/** Linear gains per channel (the indoor white balance, #58), before the tone mapping. */
const BalanceShader = {
  uniforms: { tDiffuse: { value: null }, gain: { value: new THREE.Vector3(1, 1, 1) } },
  vertexShader: /* glsl */ `
    varying vec2 vUv;
    void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
  fragmentShader: /* glsl */ `
    uniform sampler2D tDiffuse;
    uniform vec3 gain;
    varying vec2 vUv;
    void main() { vec4 c = texture2D(tDiffuse, vUv); gl_FragColor = vec4(c.rgb * gain, c.a); }`,
};

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
/**
 * A clean frame at rest (#56): the frame on screen is the average of the frames drawn since the
 * camera last moved, each after the first with the camera jittered by a fraction of a pixel (a
 * Halton sequence, on top of any lens shift): the anti-aliasing and the occlusion's noise converge
 * over `samples` frames (~8 x a frame's cost, then the loop is idle again). Last in the chain, on
 * the graded picture; reset() by anything new on screen (a move, a fade, the exposure gliding).
 */
class RestPass extends Pass {
  constructor(width, height, samples) {
    super();
    this.samples = samples;
    this.count = 0;
    const opts = { type: THREE.HalfFloatType, depthBuffer: false };
    this.average = [new THREE.WebGLRenderTarget(width, height, opts), new THREE.WebGLRenderTarget(width, height, opts)];
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
    this.size = new THREE.Vector2();
  }

  get done() { return this.count >= this.samples; }

  reset() { this.count = 0; }

  setSize(width, height) {
    for (const t of this.average) t.setSize(width, height);
    this.reset();
  }

  /**
   * Before each frame, whoever draws it (the loop, a view change, a fade): a camera that moved, a new
   * projection or exposure starts over from the frame itself; a frame after the first moves the
   * camera by a sub-pixel offset. Returns the undo of that offset.
   */
  jitter(camera, renderer) {
    camera.updateMatrixWorld();
    const key = [...camera.matrixWorld.elements, ...camera.projectionMatrix.elements, renderer.toneMappingExposure];
    if (!this.key || key.some((v, i) => Math.abs(v - this.key[i]) > 1e-6)) this.reset();
    this.key = key;
    if (this.count === 0 || this.done) return null;
    const halton = (i, b) => { let f = 1, r = 0; for (let k = i; k > 0; k = Math.floor(k / b)) { f /= b; r += f * (k % b); } return r; };
    const jx = halton(this.count, 2) - 0.5, jy = halton(this.count, 3) - 0.5;
    const v = camera.view?.enabled ? { ...camera.view } : null;
    renderer.getDrawingBufferSize(this.size);
    if (v) camera.setViewOffset(v.fullWidth, v.fullHeight, v.offsetX + (jx * v.width) / this.size.x, v.offsetY + (jy * v.height) / this.size.y, v.width, v.height);
    else camera.setViewOffset(this.size.x, this.size.y, jx, jy, this.size.x, this.size.y);
    return () => {
      if (v) camera.setViewOffset(v.fullWidth, v.fullHeight, v.offsetX, v.offsetY, v.width, v.height);
      else camera.clearViewOffset();
    };
  }

  render(renderer, writeBuffer, readBuffer) {
    const [prev, next] = this.average;
    this.blend.uniforms.tAverage.value = prev.texture;
    this.blend.uniforms.tSample.value = readBuffer.texture;
    this.blend.uniforms.weight.value = 1 / (this.count + 1); // the first frame: itself
    renderer.setRenderTarget(next);
    this.blendQuad.render(renderer);
    this.average.reverse();
    this.copy.uniforms.tDiffuse.value = this.average[0].texture;
    renderer.setRenderTarget(this.renderToScreen ? null : writeBuffer);
    this.copyQuad.render(renderer);
    this.count = Math.min(this.count + 1, this.samples);
  }
}

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
    syncShadows();
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

/**
 * Lamps: three.js writes every light into every material's shader, so a furnished house with a
 * point light per room (47 on one project) makes every shader huge (slow to compile, slow per
 * pixel, even outdoors). Above LIGHT_BUDGET point lights, the scene's own are hidden and that many
 * stand-ins take the place of the lamps that light what the camera sees: the shaders always see the
 * same number of lights (a lamp switched on or off would recompile every material), the lamps'
 * glowing shades stay as they are.
 * Which lamps: indoors, those of the rooms in view (the camera's room and the rooms open to it, less
 * for a room seen through a door: a lamp behind a wall or on another storey lights nothing in view,
 * however close), ranked by intensity, by distance and by being in front of the camera; outdoors,
 * every lamp that way. A lamp fades in or out as it gains or loses its stand-in (a dimmer, not a
 * switch) and keeps it until another one is clearly ahead (no flicker between two alike).
 * The others are not dark: the room probes are captured with each room's own lamps on (aimLamps),
 * so their light off the walls is in the room's environment; a stand-in adds the direct light (the
 * pool on the table, the glow on the ceiling) of the lamps that matter for the view.
 */
const LIGHT_BUDGET = Math.max(1, Math.round(Number(params.get("live_lamps")) || 6)); // ?live_lamps=6
const FRAME_LOG = params.get("framelog") === "1" ? [] : null; // ?framelog=1: __house.frameLog
// a clean frame at rest (#56): once the camera stops, this many frames averaged (?rest=0: off)
const REST = { samples: Math.max(2, Number(params.get("rest_samples")) || 8), on: params.get("rest") !== "0" };
// fadeIn/fadeOut: seconds; throughDoor: the weight of a room a door opens onto (1: a room open to the
// camera's); roomless: of a lamp in no room (a stair, a porch) from indoors; keep: the lead a lamp
// with a stand-in has over one without; reach: metres at which a lamp counts half (distance)
const LAMPS = { fadeIn: 0.4, fadeOut: 0.25, throughDoor: 0.35, roomless: 0.2, keep: 1.3, reach: 4 };

function capPointLights(root, scene) {
  const lamps = [];
  root.traverse((o) => { if (o.isPointLight && o.visible && !o.castShadow) lamps.push(o); });
  if (lamps.length <= LIGHT_BUDGET) return;
  for (const l of lamps) l.visible = false;
  const slots = [];
  for (let i = 0; i < LIGHT_BUDGET; i++) {
    const light = new THREE.PointLight("#ffffff", 0, 1, 2);
    light.name = "lamp-stand-in";
    scene.add(light);
    slots.push({ light, spot: null, w: 0, target: 0 });
  }
  state.lamps = { lamps, slots, spots: null, at: null, from: new THREE.Vector3(), frustum: new THREE.Frustum(),
    m: new THREE.Matrix4(), sphere: new THREE.Sphere() };
}

/** The lamps where they hang and the room each lights (worked out at the first frame, the scene being complete). */
function lampSpots(L) {
  if (L.spots) return L.spots;
  state.houseGroup.updateMatrixWorld(true);
  const rooms = sceneRooms();
  return (L.spots = L.lamps.map((l) => {
    const p = l.getWorldPosition(new THREE.Vector3());
    const room = roomOf([p.x, p.z], p.y, rooms);
    return { l, p, key: room ? `${room.name}@${room.y}` : null, slot: null };
  }));
}

/** The room a point at height `y` is in: on the storey whose floor is the highest below it, inside or within 0.4 m of the room. */
function roomOf(at, y, rooms = sceneRooms()) {
  const below = rooms.filter((r) => r.y <= y + 0.05 && y - r.y < 6);
  const floor = Math.max(...below.map((r) => r.y));
  return below
    .filter((r) => r.y === floor)
    .map((r) => ({ r, d: insidePolygon(at, r.polygon) ? 0 : distanceToPolygon(at, r.polygon) }))
    .filter(({ d }) => d < 0.4)
    .sort((a, b) => a.d - b.d)[0]?.r ?? null;
}

/**
 * The rooms in view from `room`, as `name@y` keys with a weight: 1 for the room and those open to it
 * (open passages, chained: an open plan), LAMPS.throughDoor for those a door opens onto from them.
 */
function roomsInView(room) {
  const key = `${room.name}@${room.y}`;
  state.roomsInView ??= new Map();
  if (state.roomsInView.has(key)) return state.roomsInView.get(key);
  const ways = storeyDoorways(room.y);
  const open = new Set([key]);
  for (let grew = true; grew;) {
    grew = false;
    for (const d of ways) {
      if (d.passage && open.has(d.plus.key) !== open.has(d.minus.key)) {
        open.add(d.plus.key);
        open.add(d.minus.key);
        grew = true;
      }
    }
  }
  const out = new Map([...open].map((k) => [k, 1]));
  for (const d of ways) {
    if (d.passage) continue;
    if (open.has(d.plus.key) && !out.has(d.minus.key)) out.set(d.minus.key, LAMPS.throughDoor);
    if (open.has(d.minus.key) && !out.has(d.plus.key)) out.set(d.plus.key, LAMPS.throughDoor);
  }
  state.roomsInView.set(key, out);
  return out;
}

/** How much a lamp matters seen from `from`: 0 when it lights no room in view (`inView` null: outdoors, all count). */
function lampScore(spot, from, inView, frustum, sphere) {
  const k = !inView ? 1 : spot.key ? (inView.get(spot.key) ?? 0) : LAMPS.roomless;
  if (!k) return 0;
  const ahead = !frustum || frustum.intersectsSphere(sphere.set(spot.p, 1.5)) ? 1 : 0.3;
  const d = spot.p.distanceTo(from) / LAMPS.reach;
  return (k * ahead * spot.l.intensity) / (1 + d * d);
}

/** A stand-in on a lamp at `w` of its fade, `scale` of the lamp's own strength (setLampScale's by default). */
function setStandIn(light, spot, w, scale = state.lampScale ?? 1) {
  if (!spot || w <= 0) { light.intensity = 0; return; }
  const l = spot.l;
  light.position.copy(spot.p);
  light.color.copy(l.color);
  light.intensity = (l.userData.baseIntensity ?? l.intensity) * scale * w * w * (3 - 2 * w); // smoothstep: a dimmer's ease
  light.distance = l.distance;
  light.decay = l.decay;
}

/** Before each frame: the stand-ins on the lamps that light the view, fading toward their new lamp. */
function placeLampStandIns(camera) {
  const L = state.lamps;
  if (!L) return;
  const spots = lampSpots(L);
  camera.updateMatrixWorld();
  camera.getWorldPosition(L.from);
  L.frustum.setFromProjectionMatrix(L.m.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse));
  const room = cameraRoom(camera);
  const inView = room ? roomsInView(room) : null;
  for (const s of spots) s.score = lampScore(s, L.from, inView, L.frustum, L.sphere) * (s.slot ? LAMPS.keep : 1);
  const wanted = spots.filter((s) => s.score > 0).sort((a, b) => b.score - a.score).slice(0, L.slots.length);
  const chosen = new Set(wanted);
  for (const slot of L.slots) slot.target = slot.spot && chosen.has(slot.spot) ? 1 : 0;
  // the first frame (and a headless page) lights its lamps at once; later a stand-in fades out
  // before it moves to another lamp (taken over when nearly dark)
  const instant = HEADLESS || L.at === null;
  for (const s of wanted) {
    if (s.slot) continue;
    const free = L.slots
      .filter((x) => !x.spot || (x.target === 0 && (instant || x.w < 0.1)))
      .sort((a, b) => a.w - b.w)[0];
    if (!free) continue; // waits for one to fade out
    if (free.spot) free.spot.slot = null;
    free.spot = s;
    s.slot = free;
    free.w = 0;
    free.target = 1;
  }
  const now = performance.now(), dt = Math.min(0.1, (now - (L.at ?? now)) / 1000);
  L.at = now;
  let fading = false;
  for (const slot of L.slots) {
    if (instant) slot.w = slot.target;
    else if (slot.w < slot.target) slot.w = Math.min(slot.target, slot.w + dt / LAMPS.fadeIn);
    else if (slot.w > slot.target) slot.w = Math.max(slot.target, slot.w - dt / LAMPS.fadeOut);
    if (slot.w !== slot.target) fading = true;
    else if (slot.w === 0 && slot.spot) { slot.spot.slot = null; slot.spot = null; }
    setStandIn(slot.light, slot.spot, slot.w);
  }
  if (fading) {
    state.needsRender = true; // keep drawing until the lamps have settled
    state.accumulate?.reset();
  }
}

/**
 * A room probe's capture: the stand-ins at full strength on the lamps that light `room` most, seen
 * from `at` (its own and those of the rooms open to it). The next frame puts them back.
 */
function aimLamps(room, at, scale = state.lampScale ?? 1) {
  const L = state.lamps;
  if (!L) return;
  const inView = roomsInView(room);
  const best = lampSpots(L)
    .map((s) => ({ s, v: lampScore(s, at, inView, null, L.sphere) }))
    .filter(({ v }) => v > 0)
    .sort((a, b) => b.v - a.v);
  L.slots.forEach((slot, i) => setStandIn(slot.light, best[i]?.s, 1, scale));
}

// ---- indoors (#41): a room lit by its own light, not by the outdoor sky ----
//
// Outdoors the presentation look lights everything with the sky (its environment map) and a faint
// hemisphere: fine for façades, but inside it reaches every wall of every room, uncovered, and the
// rooms come out flat and washed out. With the camera in a room of a floor plan, the room is lit by
// what surrounds that room: a cube camera at its centre sees the sun patches, the lamps and the sky
// through its windows (a second capture adds one bounce off its walls), and that becomes the
// environment, so light falls off away from the windows and a mirror shows the room. The hemisphere
// goes, the occlusion tightens to corners and contact, the lamps (a daytime photo) are dimmed.
// Everything comes back when the camera leaves.
// exposure: a photographer opens up indoors (the windows blow out, as in any interior photo);
// bounces: white walls pass the light on several times, what fills a ceiling no window sees
// fill: the daylight the windows let in, bounced round the room and off the floor, that a probe at
// the room's centre undercounts (a ceiling sees only the floor): an even, near-white hemisphere
// exposure: a correction on the metered one; meter: the luminance (as light) that gets the outdoor
// exposure. A storey is exposed as a whole, for its brighter rooms (a photographer sets the
// exposure once for the flat, for the rooms the light comes into, not per room: exposed for the
// average, a sunlit bedroom washes out); local: the share of a room's own difference from that
// the exposure follows (an eye adapts a little: a dark bathroom stays a little darker than the
// living room). Within 0.4x..3x. meter was 0.29 up to v8, calibrated on probes lit by whichever
// lamps the camera stood nearest (other storeys' through the slabs included, at the outdoor strength);
// each room's own lamps at the indoor strength need 0.5 for the same picture.
// doorway: metres from a room within which the camera keeps that room's light; blend: metres each
// side of a door over which the light of the two rooms mixes (walking through, the light changes
// with each step, not at the threshold); adapt: seconds the exposure takes to follow a jump (from
// outdoors, to another storey): an eye, not a cut
const INDOOR = { doorway: 0.4, blend: 0.7, adapt: 1.2, local: 0.33, probe: 1, lamps: 0.35, probeLamps: 1, wb: 0.5, aoThickness: 0.3, aoRadius: 0.35, aoBlend: 1.0, probeSize: 128, bounces: 3, exposure: 1, meter: 0.5,
  fill: { sky: "#fbfaf7", ground: "#e9e4dc", intensity: 0.45 } };
// aoThickness: metres in front of a point within which something occludes it (the ambient
// occlusion's thickness; 1 outdoors): at 1, a door leaf 0.6 m from the camera drew a large bright arc
// over the room behind it (the WC of TestVillaGille's App. 1, seen from its doorway)
// probeLamps: the lamps' share in the room captures (their bounced light); wb: the share of a room's
// colour cast its white balance takes out (#58; 0: none, 1: grey; 0.5 matched the reference tour's
// neutral whites, median saturation 0.15 -> 0.08 on TestVillaGille, the brightness unchanged)
// calibration by eye, like the p_* knobs: ?in_meter=0.5&in_expo=1 (a correction on the metered exposure)&in_local=0.33&in_blend=0.7&in_adapt=1.2&in_fill=0.45&in_lamps=0.35&in_ao=0.35&in_bounces=3&in_probe=0 (no room capture)&in_probe_lamps=1&in_wb=0.5&in_ao_thickness=0.3
for (const [key, set] of [["in_expo", (v) => (INDOOR.exposure = v)], ["in_fill", (v) => (INDOOR.fill.intensity = v)],
  ["in_local", (v) => (INDOOR.local = v)], ["in_blend", (v) => (INDOOR.blend = Math.max(0.05, v))], ["in_adapt", (v) => (INDOOR.adapt = Math.max(0.01, v))],
  ["in_lamps", (v) => (INDOOR.lamps = v)], ["in_probe_lamps", (v) => (INDOOR.probeLamps = v)], ["in_wb", (v) => (INDOOR.wb = Math.max(0, Math.min(1, v)))], ["in_ao_thickness", (v) => (INDOOR.aoThickness = Math.max(0.01, v))], ["in_ao", (v) => (INDOOR.aoRadius = v)], ["in_bounces", (v) => (INDOOR.bounces = Math.max(1, Math.round(v)))], ["in_probe", (v) => (INDOOR.probe = v)], ["in_meter", (v) => (INDOOR.meter = v)]]) {
  const v = Number(params.get(key));
  if (params.get(key) !== null && Number.isFinite(v)) set(v);
}

function insidePolygon([x, z], poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, zi] = poly[i], [xj, zj] = poly[j];
    if ((zi > z) !== (zj > z) && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) inside = !inside;
  }
  return inside;
}

/** The room of a floor plan the camera stands in (at head height above its floor), or null. */
function cameraRoom(camera) {
  const p = camera.position;
  const storey = sceneRooms().filter((r) => p.y > r.y && p.y < r.y + 3.2);
  const inside = storey.find((r) => insidePolygon([p.x, p.z], r.polygon));
  if (inside) return inside;
  // in a doorway (the thickness of a wall belongs to no room): the room one comes from, or the
  // nearest one, keeps its light; only well away from every room is it outdoors
  const near = storey
    .map((r) => ({ r, d: distanceToPolygon([p.x, p.z], r.polygon) }))
    .filter(({ d }) => d < INDOOR.doorway)
    .sort((a, b) => a.d - b.d);
  const current = state.indoor && near.find(({ r }) => `${r.name}@${r.y}` === state.indoor.key);
  return (current ?? near[0])?.r ?? null;
}

function distanceToPolygon([x, z], poly) {
  let best = Infinity;
  for (let i = 0; i < poly.length; i++) {
    const [ax, az] = poly[i], [bx, bz] = poly[(i + 1) % poly.length];
    const dx = bx - ax, dz = bz - az, L2 = dx * dx + dz * dz || 1;
    const t = Math.max(0, Math.min(1, ((x - ax) * dx + (z - az) * dz) / L2));
    best = Math.min(best, Math.hypot(x - (ax + t * dx), z - (az + t * dz)));
  }
  return best;
}

/** The light of a room as an environment map: cube captures from its centre, the first lit directly, each next one by the previous (a bounce more). */
function roomProbe(room) {
  const { renderer, scene } = state;
  const t0 = performance.now();
  const [cx, cz] = innerPoint(room.polygon);
  const target = new THREE.WebGLCubeRenderTarget(INDOOR.probeSize, { type: THREE.HalfFloatType });
  const cube = new THREE.CubeCamera(0.05, CAMERA_FAR, target);
  cube.position.set(cx, room.y + 1.4, cz);
  scene.add(cube);
  const pmrem = new THREE.PMREMGenerator(renderer);
  if (renderer.shadowMap.needsUpdate) {
    // a shadow map that is due is drawn now, with the whole scene (the neighbours shade the house),
    // not by the first capture below, which hides the surroundings
    const tiny = new THREE.WebGLRenderTarget(1, 1), prev = renderer.getRenderTarget();
    renderer.setRenderTarget(tiny);
    renderer.render(scene, state.camera);
    renderer.setRenderTarget(prev);
    tiny.dispose();
  }
  // its own lamps, not those the camera stood nearest at the last frame; at probeLamps of their
  // strength: bounced three times their warm light tinted the whole room (#58), while their direct
  // light (the pool under a lamp) stays as it is in the frame
  const lampScale = state.lampScale ?? 1;
  aimLamps(room, cube.position, lampScale * INDOOR.probeLamps);
  if (!state.lamps) setLampScale(lampScale * INDOOR.probeLamps); // few lamps: the scene's own
  // The photographed sky's dome, the real surroundings, the clear colour and the fog are drawn in
  // colours pre-compensated for the tone mapping (right on screen, many times too bright as light):
  // in the capture the windows see the sky light that lights the façades instead, the outdoor
  // environment map, and nothing hazes.
  // The rooms the capture cannot see (all but this one and those its doors open onto) are hidden:
  // they cost nothing, and a storey's rooms are all captured on arrival.
  const hide = [state.presentation?.dome, state.context?.group, ...hiddenAround(room)].filter((o) => o?.visible);
  for (const o of hide) o.visible = false;
  const saved = { background: scene.background, backgroundIntensity: scene.backgroundIntensity, fog: scene.fog };
  scene.background = state.outdoor?.env ?? null;
  scene.backgroundIntensity = state.outdoor?.envIntensity ?? 1;
  scene.fog = null;
  scene.environment = null;
  // the bounces carry the sun, the sky and the lamps; the fill is left out (bounced three times it
  // would multiply itself) and comes back for the metering, as in the final frame
  const hemi = state.hemi, fill = hemi?.intensity ?? 0;
  if (hemi) hemi.intensity = 0;
  let env = null;
  for (let i = 0; i < INDOOR.bounces; i++) {
    cube.update(renderer, scene);
    const next = pmrem.fromCubemap(target.texture).texture;
    env?.dispose();
    env = next;
    scene.environment = env;
    scene.environmentIntensity = 1;
  }
  if (hemi) hemi.intensity = fill;
  if (!state.lamps) setLampScale(lampScale);
  const { luminance, balance } = meterRoom(cube.position);
  // the room's box and where it was captured from: glossy surfaces project their reflections on it (#59)
  const xs = room.polygon.map((q) => q[0]), zs = room.polygon.map((q) => q[1]);
  const box = { min: new THREE.Vector3(Math.min(...xs), room.y, Math.min(...zs)),
    max: new THREE.Vector3(Math.max(...xs), room.y + (room.height ?? 2.6), Math.max(...zs)), at: cube.position.clone() };
  pmrem.dispose();
  target.dispose();
  scene.remove(cube);
  Object.assign(scene, saved);
  for (const o of hide) o.visible = true;
  // a one-off cost, not the frame rate: the slow-frame check leaves it out (a capture inside the
  // first frames after opening a project must not switch the effects off for the whole visit)
  state.probeMs = (state.probeMs ?? 0) + (performance.now() - t0);
  return { env, luminance, balance, box };
}

/**
 * The median luminance (linear, as light) seen from `position`: a small float cube capture, read
 * back. The median is the room's walls, floor and ceiling: a photographer exposes for them and lets
 * the windows blow out, where a mean would be pulled up by a sunlit balcony seen through the glass.
 */
function meterRoom(position) {
  const { renderer, scene } = state;
  const size = 16;
  const target = new THREE.WebGLCubeRenderTarget(size, { type: THREE.FloatType });
  const cube = new THREE.CubeCamera(0.05, CAMERA_FAR, target);
  cube.position.copy(position);
  scene.add(cube);
  cube.update(renderer, scene);
  scene.remove(cube);
  const px = new Float32Array(size * size * 4);
  const all = [];
  for (let face = 0; face < 6; face++) {
    renderer.readRenderTargetPixels(target, 0, 0, size, size, px, face);
    for (let i = 0; i < px.length; i += 4) all.push([0.2126 * px[i] + 0.7152 * px[i + 1] + 0.0722 * px[i + 2], px[i], px[i + 1], px[i + 2]]);
  }
  target.dispose();
  all.sort((a, b) => a[0] - b[0]);
  // the room's colour cast (#58): the mean colour of its middle tones (walls, floor, furniture; not
  // the windows or the lamps), and the gains that take a share (INDOOR.wb) of it out, as a camera's
  // white balance does, at the same luminance
  const mid = all.slice(Math.floor(all.length * 0.2), Math.ceil(all.length * 0.8));
  const mean = [1, 2, 3].map((c) => mid.reduce((sum, p) => sum + p[c], 0) / Math.max(1, mid.length));
  const grey = 0.2126 * mean[0] + 0.7152 * mean[1] + 0.0722 * mean[2];
  let gain = mean.map((m) => Math.pow(grey / Math.max(m, 1e-6), INDOOR.wb));
  const norm = 0.2126 * gain[0] + 0.7152 * gain[1] + 0.0722 * gain[2];
  gain = gain.map((g) => Math.min(1.4, Math.max(0.7, g / norm)));
  return { luminance: all[all.length >> 1][0], balance: new THREE.Vector3(...gain) };
}

function setLampScale(k) {
  state.lampScale = k;
  state.houseGroup?.traverse((o) => {
    if (!o.isPointLight || o.name === "lamp-stand-in") return;
    o.userData.baseIntensity ??= o.intensity;
    o.intensity = o.userData.baseIntensity * k;
  });
}

/**
 * The sky's dome, the clear colour, the fog and the far landscape are drawn pre-compensated for
 * the tone mapping at one exposure: when the exposure opens up indoors they follow, so the view
 * through the windows keeps its colours instead of washing out.
 */
function setSkyExposure(exposure) {
  const dome = state.presentation?.dome;
  if (!dome) return;
  const display = dome.material.uniforms.uHorizon.value;
  dome.material.uniforms.uExposure.value = exposure;
  const haze = inverseACES(display, exposure);
  if (state.scene.background?.isColor) state.scene.background.copy(haze);
  if (state.scene.fog) state.scene.fog.color.copy(haze);
  state.context?.setHaze?.(display, PRESENTATION_LOOK.farHaze, exposure);
}

/** The exposure to reach: at once in a headless render (one frame per view), gliding otherwise. */
function setExposureTarget(exposure) {
  state.exposureTarget = exposure;
  if (HEADLESS || state.exposureAt === undefined) applyExposure(exposure);
}

function applyExposure(exposure) {
  state.renderer.toneMappingExposure = exposure;
  setSkyExposure(exposure);
}

/** Each frame: the white balance (#58) one step closer to the room's, at the exposure's pace. */
function adaptBalance(k) {
  const b = state.balancePass?.uniforms.gain.value, t = state.balanceTarget;
  if (!b || !t || b.distanceTo(t) < 1e-3) { if (b && t) b.copy(t); return; }
  b.lerp(t, k);
  state.needsRender = true;
}

/** Each frame: the exposure one step closer to its target (in log space, so up and down feel alike). */
function adaptExposure() {
  const now = performance.now();
  const last = state.exposureAt ?? now;
  state.exposureAt = now;
  const target = state.exposureTarget;
  const cur = state.renderer.toneMappingExposure;
  if (target === undefined || Math.abs(Math.log(cur / target)) < 0.01) {
    if (target !== undefined && cur !== target) applyExposure(target);
    return;
  }
  const k = 1 - Math.exp(-Math.max(0, now - last) / 1000 / INDOOR.adapt);
  adaptBalance(k);
  applyExposure(Math.exp(Math.log(cur) + (Math.log(target) - Math.log(cur)) * k));
  state.needsRender = true; // keep drawing until it has settled
}

/** Before a frame of the presentation look: the indoor light of the room the camera is in. */
function updateIndoorLight() {
  if (!PRESENTATION || !state.scene) return;
  const room = cameraRoom(state.camera);
  const key = room ? `${room.name}@${room.y}` : null;
  if (key !== (state.indoor?.key ?? null)) enterIndoorLight(room, key);
  if (room && INDOOR.probe) roomLightAt(room);
}

/** Into a room (or out of the house): the light that is the same in every room of the house. */
function enterIndoorLight(room, key) {
  const { scene, hemi, gtao } = state;
  if (!state.outdoor) {
    state.outdoor = { env: scene.environment, envIntensity: scene.environmentIntensity, hemi: hemi?.intensity ?? 0,
      hemiSky: hemi?.color.clone(), hemiGround: hemi?.groundColor.clone(),
      exposure: state.renderer.toneMappingExposure,
      aoRadius: gtao?.gtaoMaterial?.uniforms?.radius?.value ?? 0.6, aoBlend: gtao?.blendIntensity ?? 0.8 };
  }
  const out = state.outdoor;
  state.indoor = room ? { key, name: room.name } : null;
  if (room) {
    // the room's own light (fill, dimmed lamps) is on before the capture: what a mirror or a
    // chrome tap then reflects is the room as it is lit, not its unlit ceiling
    if (hemi) {
      hemi.color.set(INDOOR.fill.sky);
      hemi.groundColor.set(INDOOR.fill.ground);
      hemi.intensity = INDOOR.fill.intensity;
    }
    setLampScale(INDOOR.lamps);
    if (INDOOR.probe) {
      captureStorey(room.y);
      scene.environmentIntensity = 1;
      state.indoor.luminance = state.probes.get(key)?.luminance;
    } else {
      setExposureTarget(out.exposure * INDOOR.exposure);
    }
    if (gtao) {
      gtao.updateGtaoMaterial({ radius: INDOOR.aoRadius, thickness: INDOOR.aoThickness });
      gtao.blendIntensity = INDOOR.aoBlend;
    }
  } else {
    scene.environment = out.env;
    scene.environmentIntensity = out.envIntensity;
    if (hemi) {
      hemi.intensity = out.hemi;
      if (out.hemiSky) hemi.color.copy(out.hemiSky);
      if (out.hemiGround) hemi.groundColor.copy(out.hemiGround);
    }
    setExposureTarget(out.exposure);
    state.balanceTarget?.copy(NEUTRAL);
    setEnvBox(null);
    setLampScale(1);
    if (gtao) {
      gtao.updateGtaoMaterial({ radius: out.aoRadius, thickness: 1 });
      gtao.blendIntensity = out.aoBlend;
    }
  }
  state.accumulate?.reset();
  // the next frames recompile the materials for the new light (a second on a laptop GPU): not a
  // sign that the effects are too heavy for this machine
  state.frameCheckSkip = 3;
}

/**
 * Every room of a storey captured (roomProbe) the first time the camera is on it, and the luminance
 * the storey is exposed for: the geometric mean of its brighter half of rooms.
 */
function captureStorey(y) {
  state.probes ??= new Map();
  state.storeyLight ??= new Map();
  if (state.storeyLight.has(y)) return;
  const logs = [];
  for (const r of sceneRooms().filter((r) => r.y === y)) {
    const key = `${r.name}@${r.y}`;
    if (!state.probes.has(key)) state.probes.set(key, roomProbe(r));
    logs.push(Math.log(Math.max(state.probes.get(key).luminance, 1e-4)));
  }
  const bright = logs.sort((a, b) => b - a).slice(0, Math.ceil(logs.length / 2));
  state.storeyLight.set(y, bright.length ? Math.exp(bright.reduce((a, b) => a + b) / bright.length) : null);
}

/** A room's exposure: its storey's, moved a share (INDOOR.local) of the way to what the room alone would meter. */
function roomExposure(room) {
  const base = state.outdoor.exposure * INDOOR.exposure;
  const own = state.probes.get(`${room.name}@${room.y}`)?.luminance;
  const storey = state.storeyLight.get(room.y);
  if (!own || !storey) return base;
  const metered = (INDOOR.meter / storey) * Math.pow(storey / Math.max(own, 1e-4), INDOOR.local);
  return base * Math.min(3, Math.max(0.4, metered));
}

/**
 * Each frame indoors: the room's environment and exposure; within INDOOR.blend of a door, the two
 * rooms' mixed by where the camera stands across it, so the light changes step by step.
 */
function roomLightAt(room) {
  const p = state.camera.position;
  const key = `${room.name}@${room.y}`;
  let door = null;
  for (const d of storeyDoorways(room.y)) {
    if (d.plus.key !== key && d.minus.key !== key) continue;
    const dx = p.x - d.c[0], dz = p.z - d.c[1];
    const along = dx * d.u[0] + dz * d.u[1], across = dx * d.n[0] + dz * d.n[1];
    if (Math.abs(along) > d.half + 0.15 || Math.abs(across) >= INDOOR.blend) continue;
    if (!door || Math.abs(across) < Math.abs(door.across)) door = { ...d, across };
  }
  const probe = (r) => state.probes.get(r.key ?? `${r.name}@${r.y}`);
  let env, exposure, balance;
  if (door && probe(door.plus) && probe(door.minus)) {
    const w = THREE.MathUtils.smoothstep(door.across, -INDOOR.blend, INDOOR.blend); // 0: the minus side, 1: the plus side
    env = blendEnv(probe(door.minus).env, probe(door.plus).env, w);
    exposure = Math.exp(Math.log(roomExposure(door.minus)) * (1 - w) + Math.log(roomExposure(door.plus)) * w);
    balance = (state.balanceAt ??= new THREE.Vector3()).lerpVectors(probe(door.minus).balance ?? NEUTRAL, probe(door.plus).balance ?? NEUTRAL, w);
    setEnvBox(probe(w < 0.5 ? door.minus : door.plus).box);
    state.indoor.doorway = { from: door.minus.name, to: door.plus.name, w: Math.round(w * 100) / 100 };
  } else {
    env = probe(room)?.env ?? state.outdoor.env;
    exposure = roomExposure(room);
    balance = probe(room)?.balance ?? NEUTRAL;
    setEnvBox(probe(room)?.box);
    state.indoor.doorway = null;
  }
  if (state.scene.environment !== env) state.scene.environment = env;
  if (exposure !== state.exposureTarget) setExposureTarget(exposure);
  state.balanceTarget?.copy(balance);
}

/**
 * Where the light of two rooms of a storey meets: the openings in its partitions, and the passages
 * the floor plan does not list (an open plan, an opening in a wall), each with the rooms on its two
 * sides (across its normal `n`).
 */
function storeyDoorways(y) {
  state.doorways ??= new Map();
  if (state.doorways.has(y)) return state.doorways.get(y);
  const rooms = sceneRooms().filter((r) => r.y === y).map((r) => ({ ...r, key: `${r.name}@${r.y}` }));
  const at = (pt) => rooms.find((r) => insidePolygon(pt, r.polygon));
  const out = [];
  state.houseGroup.traverse((o) => {
    if (o.userData?.kind !== "floorPlan" || o.userData.y !== y) return;
    for (const w of o.userData.partitions) {
      const len = Math.hypot(w.to[0] - w.from[0], w.to[1] - w.from[1]) || 1;
      const u = [(w.to[0] - w.from[0]) / len, (w.to[1] - w.from[1]) / len], n = [-u[1], u[0]];
      for (const op of w.openings) {
        const s = op.offset + op.width / 2, c = [w.from[0] + u[0] * s, w.from[1] + u[1] * s];
        const plus = at([c[0] + n[0] * 0.3, c[1] + n[1] * 0.3]), minus = at([c[0] - n[0] * 0.3, c[1] - n[1] * 0.3]);
        if (plus && minus && plus !== minus) out.push({ c, u, n, half: op.width / 2, plus, minus });
      }
    }
  });
  for (const p of openPassages(rooms, y)) {
    if (!out.some((d) => Math.hypot(d.c[0] - p.c[0], d.c[1] - p.c[1]) < 0.6)) out.push(p);
  }
  state.doorways.set(y, out);
  return out;
}

/**
 * Where two rooms meet with no wall between them at eye height, found from the walls themselves:
 * along each room's edges, every 10 cm, a ray from inside the room to the room beyond. A stretch of
 * at least 0.5 m that is open is a passage, as a doorway (the `minus` side is the room it was found from).
 */
function openPassages(rooms, y) {
  const kinds = new Set(["wall", "perimeter", "partition"]);
  const walls = [];
  state.houseGroup.traverse((o) => {
    if (!o.isMesh) return;
    let kind = null;
    for (let a = o; a && !kind; a = a.parent) kind = a.userData?.kind ?? null;
    if (kinds.has(kind)) walls.push(o);
  });
  const ray = new THREE.Raycaster(), from = new THREE.Vector3(), dir = new THREE.Vector3();
  const eye = y + 1.5, out = [];
  for (const A of rooms) {
    const poly = A.polygon;
    for (let i = 0; i < poly.length; i++) {
      const [ax, az] = poly[i], [bx, bz] = poly[(i + 1) % poly.length];
      const len = Math.hypot(bx - ax, bz - az);
      if (len < 0.5) continue;
      const u = [(bx - ax) / len, (bz - az) / len];
      let n = [u[1], -u[0]]; // outwards: away from the room
      if (insidePolygon([ax + u[0] * len / 2 + n[0] * 0.05, az + u[1] * len / 2 + n[1] * 0.05], poly)) n = [-n[0], -n[1]];
      let run = null;
      const close = () => {
        if (run && run.to - run.from >= 0.45) {
          const s = (run.from + run.to) / 2;
          out.push({ c: [ax + u[0] * s + n[0] * run.depth / 2, az + u[1] * s + n[1] * run.depth / 2], u, n,
            half: (run.to - run.from) / 2 + 0.05, plus: run.B, minus: A, passage: true });
        }
        run = null;
      };
      for (let s = 0.05; s < len - 0.04; s += 0.1) {
        const e = [ax + u[0] * s, az + u[1] * s];
        // the room beyond (across a wall up to 0.45 m thick), and nothing solid on the way to it
        let B = null, depth = 0;
        for (let d = 0.02; d <= 0.47 && !B; d += 0.05) {
          B = rooms.find((r) => r !== A && insidePolygon([e[0] + n[0] * d, e[1] + n[1] * d], r.polygon)) ?? null;
          depth = d;
        }
        let open = false;
        if (B) {
          ray.set(from.set(e[0] - n[0] * 0.15, eye, e[1] - n[1] * 0.15), dir.set(n[0], 0, n[1]));
          ray.far = 0.15 + depth + 0.05;
          open = ray.intersectObjects(walls, false).length === 0;
        }
        if (open && run?.B === B) run.to = s;
        else {
          close();
          if (open) run = { B, from: s, to: s, depth };
        }
      }
      close();
    }
  }
  return out;
}

/** Two rooms' environments (PMREM textures of one size) mixed into one, `w` of the second. */
function blendEnv(a, b, w) {
  if (w <= 0.002) return a;
  if (w >= 0.998) return b;
  const { width, height } = a.image;
  let bl = state.envBlend;
  if (!bl || bl.target.width !== width || bl.target.height !== height) {
    bl?.target.dispose();
    const target = new THREE.WebGLRenderTarget(width, height, { type: THREE.HalfFloatType, format: THREE.RGBAFormat,
      colorSpace: THREE.LinearSRGBColorSpace, magFilter: THREE.LinearFilter, minFilter: THREE.LinearFilter, generateMipmaps: false, depthBuffer: false });
    target.texture.mapping = THREE.CubeUVReflectionMapping;
    const quad = bl?.quad ?? new FullScreenQuad(new THREE.ShaderMaterial({
      uniforms: { tA: { value: null }, tB: { value: null }, w: { value: 0 } },
      vertexShader: /* glsl */ `
        varying vec2 vUv;
        void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
      fragmentShader: /* glsl */ `
        uniform sampler2D tA;
        uniform sampler2D tB;
        uniform float w;
        varying vec2 vUv;
        void main() { gl_FragColor = mix(texture2D(tA, vUv), texture2D(tB, vUv), w); }`,
      depthTest: false,
      depthWrite: false,
    }));
    bl = state.envBlend = { target, quad, a: null, b: null, w: -1 };
  }
  if (bl.a !== a || bl.b !== b || Math.abs(bl.w - w) > 0.002) {
    const u = bl.quad.material.uniforms;
    u.tA.value = a; u.tB.value = b; u.w.value = w;
    const previous = state.renderer.getRenderTarget();
    state.renderer.setRenderTarget(bl.target);
    bl.quad.render(state.renderer);
    state.renderer.setRenderTarget(previous);
    Object.assign(bl, { a, b, w });
  }
  return bl.target.texture;
}

/** A point well inside a polygon: its centroid, or where an L-shaped room's falls outside it, the grid point farthest from its walls. */
function innerPoint(poly) {
  let cx = 0, cz = 0;
  for (const [x, z] of poly) { cx += x / poly.length; cz += z / poly.length; }
  if (insidePolygon([cx, cz], poly)) return [cx, cz];
  const xs = poly.map((q) => q[0]), zs = poly.map((q) => q[1]);
  const [x0, x1, z0, z1] = [Math.min(...xs), Math.max(...xs), Math.min(...zs), Math.max(...zs)];
  let best = [cx, cz], far = -1;
  for (let i = 1; i < 16; i++) {
    for (let j = 1; j < 16; j++) {
      const q = [x0 + ((x1 - x0) * i) / 16, z0 + ((z1 - z0) * j) / 16];
      if (!insidePolygon(q, poly)) continue;
      const d = distanceToPolygon(q, poly);
      if (d > far) { far = d; best = q; }
    }
  }
  return best;
}

function renderFrame() {
  const { renderer, scene, camera, composer } = state;
  if (!renderer) return;
  syncShadows();
  updateIndoorLight();
  if (PRESENTATION && !HEADLESS) adaptExposure();
  placeLampStandIns(camera);
  updateMirrors(camera);
  const culled = cullRooms(camera);
  if (composer && state.gtao?.depthTexture && composer.readBuffer.depthTexture) {
    // the scene pass draws into the composer's read buffer: its depth is what the occlusion reads
    const d = composer.readBuffer.depthTexture;
    state.gtao.gtaoMaterial.uniforms.tDepth.value = d;
    state.gtao.pdMaterial.uniforms.tDepth.value = d;
  }
  const jittered = composer && state.rest?.jitter(camera, renderer);
  if (composer) composer.render();
  else renderer.render(scene, camera);
  if (jittered) jittered();
  if (culled) for (const o of culled) o.visible = true;
  state.mirrors?.done();
}

/**
 * Walk frames (#56): the furniture of the rooms the camera cannot see is not drawn (it was most of a
 * frame's 1,200-4,300 draw calls and 3-5 million triangles, drawn twice: the scene and the ambient
 * occlusion's normals). From the camera's room, a neighbour is seen when the opening between them (a
 * door, an open passage) is in the view's frustum, and so on from there: conservative, an opening
 * does not narrow the view. Walls, floors, slabs and all that is in no room (the outside) are always
 * drawn; the other storeys' rooms are not, unless a stair room is in view (the stairwell). ?cull=0:
 * everything drawn. Returns the meshes hidden for this frame (the caller shows them again).
 */
function cullRooms(camera) {
  if (!state.walk || !CULL) return null;
  const room = cameraRoom(camera);
  if (!room) return null;
  const key = (r) => `${r.name}@${r.y}`;
  const c = (state.cull ??= { frustum: new THREE.Frustum(), m: new THREE.Matrix4(), box: new THREE.Box3(), pts: [0, 1, 2, 3, 4, 5, 6, 7].map(() => new THREE.Vector3()) });
  camera.updateMatrixWorld();
  c.frustum.setFromProjectionMatrix(c.m.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse));
  const seen = new Set([key(room)]), stairs = [room.use === "stair"];
  const queue = [{ ...room, key: key(room) }];
  while (queue.length) {
    const r = queue.shift();
    for (const d of storeyDoorways(r.y)) {
      const other = d.plus.key === r.key ? d.minus : d.minus.key === r.key ? d.plus : null;
      if (!other || seen.has(other.key)) continue;
      // the opening: its width along the partition, 15 cm each side across it, floor to 2.3 m
      let i = 0;
      for (const su of [-1, 1]) for (const sn of [-1, 1]) for (const h of [0, 2.3]) {
        c.pts[i++].set(d.c[0] + su * d.u[0] * d.half + sn * d.n[0] * 0.15, r.y + h, d.c[1] + su * d.u[1] * d.half + sn * d.n[1] * 0.15);
      }
      if (!c.frustum.intersectsBox(c.box.setFromPoints(c.pts))) continue;
      seen.add(other.key);
      stairs.push(other.use === "stair");
      queue.push(other);
    }
  }
  const hidden = [];
  state.lastCull = { room: key(room), seen: [...seen] };
  for (const [k, { y, meshes }] of meshesByRoom()) {
    if (seen.has(k) || (stairs.some(Boolean) && Math.abs(y - room.y) > 0.5)) continue;
    for (const o of meshes) {
      if (!o.visible) continue;
      o.visible = false;
      hidden.push(o);
    }
  }
  return hidden;
}

/**
 * Walk frames (#56): the house's static meshes merged by room (or by 8 m tile for the shell and the
 * outside), material, attributes and shadow flags. A frame drew 1,000-2,000 meshes of 183 materials,
 * twice (the scene and the ambient occlusion's normals), and on the WSL path (ANGLE on Mesa's
 * D3D12) every draw costs: the draw calls were most of a frame, not the pixels. The meshes merged go
 * to a layer no camera draws (MERGED_LAYER), so the 2D plan, the audit and the exports still see the
 * scene as it was built; mirrors, glass and other see-through materials, instanced and multi-material
 * meshes stay as they are. Once, when the walk starts (behind its fade); never headless (the build
 * path's pictures stay those of the built scene). ?merge=0: off.
 */
function mergeHouse() {
  if (state.mergedGroup || !MERGE || HEADLESS || !state.houseGroup) return;
  const t0 = performance.now();
  state.houseGroup.updateMatrixWorld(true);
  const rooms = sceneRooms(), key = (r) => `${r.name}@${r.y}`;
  const box = new THREE.Box3(), c = new THREE.Vector3(), size = new THREE.Vector3();
  const shown = (o) => { for (let a = o; a; a = a.parent) if (!a.visible) return false; return true; };
  const groups = new Map();
  state.houseGroup.traverse((o) => {
    if (!o.isMesh || o.isInstancedMesh || o.isSkinnedMesh || !o.layers.isEnabled(0) || !shown(o)) return;
    const m = o.material, g = o.geometry;
    if (Array.isArray(m) || m.transparent || m.alphaHash || isMirror(o) || o.morphTargetInfluences) return;
    // (a geometry's groups only matter with an array of materials: a box has six, drawn with its one)
    if (o.onBeforeRender !== THREE.Object3D.prototype.onBeforeRender || !g?.attributes?.position) return;
    box.setFromObject(o).getCenter(c);
    box.getSize(size);
    const room = size.x <= 3 && size.z <= 3 ? roomForPoint([c.x, c.z], c.y, rooms) : null;
    const region = room ? key(room) : `${Math.floor(c.x / TILE)},${Math.floor(c.z / TILE)},${Math.floor(c.y / 3)}`;
    const sig = Object.keys(g.attributes).sort().join(",") + (g.index ? "+i" : "-i");
    const k = `${m.uuid}|${sig}|${o.castShadow ? 1 : 0}${o.receiveShadow ? 1 : 0}|${region}`;
    if (!groups.has(k)) groups.set(k, { room: room ? key(room) : null, meshes: [] });
    groups.get(k).meshes.push(o);
  });
  const merged = new THREE.Group();
  merged.name = "house-merged";
  let into = 0, from = 0;
  for (const { room, meshes } of groups.values()) {
    if (meshes.length < 2) continue;
    let geo = null;
    try {
      geo = mergeGeometries(meshes.map((o) => plainGeometry(o.geometry).applyMatrix4(o.matrixWorld)), false);
    } catch { geo = null; }
    if (!geo) continue;
    const mesh = new THREE.Mesh(geo, meshes[0].material);
    mesh.castShadow = meshes[0].castShadow;
    mesh.receiveShadow = meshes[0].receiveShadow;
    mesh.userData = { kind: "merged", room, count: meshes.length };
    merged.add(mesh);
    for (const o of meshes) {
      o.layers.set(MERGED_LAYER);
      o.userData.mergedInto = mesh;
    }
    into += 1;
    from += meshes.length;
  }
  state.scene.add(merged);
  state.mergedGroup = merged;
  state.meshSpots = state.hiddenAround = state.meshesByRoom = null; // worked out again with the merged meshes
  state.merge = { meshes: from, into, ms: Math.round(performance.now() - t0) };
}

// The box glossy reflections are projected on (#59), shared by every patched material: the room's
// (min, max), where its light was captured from, on (1) or off (outdoors)
const ENV_BOX = { min: { value: new THREE.Vector3() }, max: { value: new THREE.Vector3() }, at: { value: new THREE.Vector3() }, on: { value: 0 } };

function setEnvBox(box) {
  if (!box) { ENV_BOX.on.value = 0; return; }
  ENV_BOX.min.value.copy(box.min);
  ENV_BOX.max.value.copy(box.max);
  ENV_BOX.at.value.copy(box.at);
  ENV_BOX.on.value = 1;
}

/**
 * Reflections that sit in the room (#59): a room's light is captured from one point, and a glossy
 * surface (a tile, a worktop, a lacquered front, metal; roughness under 0.5) read it as if the room
 * were infinitely far: a window's reflection slid with the camera. Its reflection vector is now
 * projected on the room's box first (parallax correction), the way the reference tour's captures
 * are. Once, when the walk starts, before its materials are compiled; ?box=0: off.
 */
function boxProjectHouse() {
  if (state.boxProjected || !BOX || !PRESENTATION || HEADLESS || !state.houseGroup) return;
  state.boxProjected = true;
  const done = new Set();
  state.houseGroup.traverse((o) => {
    if (!o.isMesh) return;
    for (const m of Array.isArray(o.material) ? o.material : [o.material]) {
      if (!m?.isMeshStandardMaterial || done.has(m) || m.roughness >= 0.5 || m.envMap) continue;
      done.add(m);
      const base = m.onBeforeCompile, baseKey = m.customProgramCacheKey();
      m.onBeforeCompile = (shader, renderer) => {
        base.call(m, shader, renderer);
        shader.uniforms.envBoxMin = ENV_BOX.min;
        shader.uniforms.envBoxMax = ENV_BOX.max;
        shader.uniforms.envBoxAt = ENV_BOX.at;
        shader.uniforms.envBoxOn = ENV_BOX.on;
        shader.vertexShader = shader.vertexShader
          .replace("#include <common>", "#include <common>\nvarying vec3 vBoxWorld;")
          .replace("#include <project_vertex>", `#include <project_vertex>
	vec4 boxWorld = vec4( transformed, 1.0 );
	#ifdef USE_INSTANCING
		boxWorld = instanceMatrix * boxWorld;
	#endif
	vBoxWorld = ( modelMatrix * boxWorld ).xyz;`);
        shader.fragmentShader = shader.fragmentShader
          .replace("#include <common>", `#include <common>
uniform vec3 envBoxMin;
uniform vec3 envBoxMax;
uniform vec3 envBoxAt;
uniform float envBoxOn;
varying vec3 vBoxWorld;
vec3 boxProject( vec3 dir ) {
	if ( envBoxOn < 0.5 ) return dir;
	vec3 d = normalize( dir ) + vec3( 1e-6 );
	vec3 t = max( ( envBoxMax - vBoxWorld ) / d, ( envBoxMin - vBoxWorld ) / d );
	float dist = min( min( t.x, t.y ), t.z );
	return dist > 0.0 ? vBoxWorld + d * dist - envBoxAt : dir;
}`)
          .replace("#include <envmap_physical_pars_fragment>", THREE.ShaderChunk.envmap_physical_pars_fragment.replace(
            "reflectVec = inverseTransformDirection( reflectVec, viewMatrix );",
            "reflectVec = inverseTransformDirection( reflectVec, viewMatrix );\n\t\t\treflectVec = boxProject( reflectVec );"));
      };
      m.customProgramCacheKey = () => `${baseKey}|env-box`;
      m.needsUpdate = true;
    }
  });
  state.boxPatched = done.size;
}

/** A copy of a geometry with plain float attributes and a 32-bit index: merging wants them alike (a GLB's may be quantized, normalized, interleaved). */
function plainGeometry(g) {
  const out = new THREE.BufferGeometry();
  for (const [name, a] of Object.entries(g.attributes)) {
    const arr = new Float32Array(a.count * a.itemSize);
    for (let i = 0; i < a.count; i++) for (let j = 0; j < a.itemSize; j++) arr[i * a.itemSize + j] = a.getComponent(i, j);
    out.setAttribute(name, new THREE.BufferAttribute(arr, a.itemSize));
  }
  if (g.index) out.setIndex(new THREE.BufferAttribute(Uint32Array.from(g.index.array), 1));
  return out;
}

/** The meshes shorter than 3 m of each room (meshSpots in its polygon on its storey), by room key: worked out once. */
/**
 * The room a mesh centred at `at` (x, z), height `y` belongs to: inside (or within 12 cm of) a room
 * whose storey band holds it, on the highest such floor. A storey's band reaches 30 cm above the
 * next floor up, so a rug or a low table on the ground floor is also in the cellar's band: the first
 * room found used to be the cellar's, and the ground floor's rug was hidden with the cellar.
 */
function roomForPoint(at, y, rooms = sceneRooms()) {
  let best = null;
  for (const q of rooms) {
    if (y <= q.y - 0.1 || y >= q.y + 3.1 || (best && q.y <= best.y)) continue;
    if (insidePolygon(at, q.polygon) || distanceToPolygon(at, q.polygon) < 0.12) best = q;
  }
  return best;
}

function meshesByRoom() {
  if (state.meshesByRoom) return state.meshesByRoom;
  const rooms = sceneRooms(), out = new Map();
  for (const { o, at, y, room } of meshSpots()) {
    const r = room ? rooms.find((q) => `${q.name}@${q.y}` === room) : roomForPoint(at, y, rooms);
    if (!r) continue;
    const k = `${r.name}@${r.y}`;
    if (!out.has(k)) out.set(k, { y: r.y, meshes: [] });
    out.get(k).meshes.push(o);
  }
  return (state.meshesByRoom = out);
}

/**
 * Mirrors (kit/mirror.js): while walking, the mirror most in view shows what is in front of it. Its
 * reflection is drawn with its own room and the rooms its doors open onto, the only ones it can
 * show: the rest of the house is hidden for that render, and so costs nothing.
 */
function updateMirrors(camera) {
  if (!MIRRORS || !state.walk || !state.houseGroup) return;
  if (!state.mirrors) {
    const rooms = sceneRooms(), found = [];
    state.houseGroup.updateMatrixWorld(true);
    state.houseGroup.traverse((o) => {
      if (!isMirror(o)) return;
      const c = new THREE.Box3().setFromObject(o).getCenter(new THREE.Vector3());
      const room = rooms
        .filter((r) => c.y > r.y && c.y < r.y + 3.2)
        .map((r) => ({ r, d: insidePolygon([c.x, c.z], r.polygon) ? 0 : distanceToPolygon([c.x, c.z], r.polygon) }))
        .filter(({ d }) => d < 0.4)
        .sort((a, b) => a.d - b.d)[0]?.r;
      if (room) found.push({ mesh: o, room, inside: (p) => insidePolygon(p, room.polygon) });
    });
    // a phone: half the resolution (multisampling stays: next to free on a phone's tiled GPU)
    const phone = window.matchMedia?.("(pointer: coarse)").matches;
    state.mirrors = new Mirrors(state.renderer, found, phone ? { scale: 0.5 } : {});
  }
  if (!state.mirrors.count) return;
  const here = cameraRoom(camera);
  state.mirrors.update(state.scene, camera, {
    accept: (m) => !!here && roomsSeenFrom(m.room).has(`${here.name}@${here.y}`),
    hidden: (m) => hiddenAround(m.room),
  });
}

/** A room and the rooms its doors open onto (the openings of the floor plan's partitions), as `name@y` keys. */
function roomsSeenFrom(room) {
  const key = (r) => `${r.name}@${r.y}`;
  state.seenFrom ??= new Map();
  if (state.seenFrom.has(key(room))) return state.seenFrom.get(key(room));
  const seen = new Set([key(room)]);
  state.seenFrom.set(key(room), seen);
  state.houseGroup.traverse((o) => {
    if (o.userData?.kind !== "floorPlan" || Math.abs(o.userData.y - room.y) > 0.5) return;
    const rooms = o.userData.rooms.map((r) => ({ ...r, y: o.userData.y }));
    for (const w of o.userData.partitions) {
      const len = Math.hypot(w.to[0] - w.from[0], w.to[1] - w.from[1]) || 1;
      const ux = (w.to[0] - w.from[0]) / len, uz = (w.to[1] - w.from[1]) / len;
      for (const op of w.openings) {
        const at = op.offset + op.width / 2;
        const c = [w.from[0] + ux * at, w.from[1] + uz * at];
        const either = rooms.filter((r) => distanceToPolygon(c, r.polygon) < 0.35);
        if (either.some((r) => key(r) === key(room))) for (const r of either) seen.add(key(r));
      }
    }
  });
  return seen;
}

/**
 * What a mirror cannot show: the pieces of the rooms not in `seen` (any storey). Walls, floors and
 * slabs longer than 3 m stay (they close the rooms that are seen), and so does all that is in no
 * room: the outside, seen through the windows.
 */
function hiddenFrom(seen) {
  const rooms = sceneRooms(), hide = [];
  for (const { o, at, y, room } of meshSpots()) {
    const within = room ? rooms.filter((r) => `${r.name}@${r.y}` === room) : rooms.filter((r) => y > r.y - 0.1 && y < r.y + 3.1
      && (insidePolygon(at, r.polygon) || distanceToPolygon(at, r.polygon) < 0.12));
    if (within.length && !within.some((r) => seen.has(`${r.name}@${r.y}`))) hide.push(o);
  }
  return hide;
}

/** What cannot be seen from a room (hiddenFrom the rooms seen from it), worked out once per room. */
function hiddenAround(room) {
  const key = `${room.name}@${room.y}`;
  state.hiddenAround ??= new Map();
  if (!state.hiddenAround.has(key)) state.hiddenAround.set(key, hiddenFrom(roomsSeenFrom(room)));
  return state.hiddenAround.get(key);
}

/** The meshes of the house shorter than 3 m, with the centre of their box (x, z) and its height: measured once. */
function meshSpots() {
  if (state.meshSpots) return state.meshSpots;
  const box = new THREE.Box3(), c = new THREE.Vector3(), size = new THREE.Vector3(), spots = [];
  state.houseGroup.updateMatrixWorld(true);
  state.houseGroup.traverse((o) => {
    if (!o.isMesh || o.userData.mergedInto) return; // drawn as part of a merged mesh: that one is hidden instead
    box.setFromObject(o).getSize(size);
    if (size.x > 3 || size.z > 3) return;
    box.getCenter(c);
    spots.push({ o, at: [c.x, c.z], y: c.y });
  });
  // the merged meshes (#56) of a room, whatever their size: they carry their room
  for (const o of state.mergedGroup?.children ?? []) {
    if (!o.userData.room) continue;
    box.setFromObject(o).getCenter(c);
    spots.push({ o, at: [c.x, c.z], y: c.y, room: o.userData.room });
  }
  return (state.meshSpots = spots);
}

/**
 * The sun's shadow map depends on the sun and the geometry, never on the camera: it is drawn again
 * only when the sun has moved (ultra's samples spread it over its disc). Drawn on every frame it
 * was most of a walk frame: the shadow camera sees the whole house, furniture included (~2,700 of
 * 3,500 draw calls on a furnished villa), where the view sees one room. Nothing moves once the
 * scene is built (a new version of it is a new page).
 */
function syncShadows() {
  const { renderer, sun } = state;
  if (!renderer?.shadowMap.enabled || !sun || state.shadowSun?.equals(sun.position)) return;
  state.shadowSun = sun.position.clone();
  renderer.shadowMap.needsUpdate = true;
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

/**
 * The scene from above for the surroundings' alignment editor: its exterior walls as segments, its
 * slabs (terraces, pools' decks) as polygons, the boxes of its terrain meshes, in scene metres.
 */
function sceneOutline() {
  const g = state.houseGroup;
  if (!g) return { walls: [], slabs: [], terrain: [] };
  g.updateMatrixWorld(true);
  const walls = [], slabs = [], terrain = [], seen = new Set();
  const w = (o, [x, z], y) => { const v = new THREE.Vector3(x, y, z).applyMatrix4(o.parent?.matrixWorld ?? new THREE.Matrix4()); return [+v.x.toFixed(2), +v.z.toFixed(2)]; };
  g.traverse((o) => {
    const u = o.userData ?? {};
    if (u.kind === "wall" && u.from && u.to) {
      const a = w(o, u.from, u.y ?? 0), b = w(o, u.to, u.y ?? 0);
      const key = [...a, ...b].join(",");
      if (!seen.has(key)) { seen.add(key); walls.push([...a, ...b]); }
    } else if ((u.slab || u.kind === "slab") && Array.isArray(u.polygon)) {
      slabs.push(u.polygon.map((p) => w(o, p, u.y ?? 0)));
    } else if (u.kind === "terrain" && o.isMesh) {
      const b = new THREE.Box3().setFromObject(o);
      if (!b.isEmpty()) terrain.push([b.min.x, b.min.z, b.max.x, b.max.z].map((v) => +v.toFixed(2)));
    }
  });
  return { walls, slabs, terrain };
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
  // the rooms against the areas printed on the plan (#70) and the furniture measured (#67, #68)
  const lines = [...(await sceneLayout()).lines];
  // rooms missing what their use needs (a bath without tiles or towel rail, a bedroom without a bed)
  const { roomEssentials } = await import("./interior.js");
  lines.push(...roomEssentials(state.houseGroup).map((l) => (l.startsWith('"') ? `room incomplete: ${l}` : l)));
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
        const given = givenView({ ...r, y: s.y });
        if (given && !w.free(given.at[0], given.at[1])) {
          lines.push(`the viewpoint set for "${r.name}" at [${given.at.map(f).join(", ")}] stands in furniture or against a wall: move it to where one can stand (or drop it: the walk works one out)`);
        }
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

/**
 * The interior measured (kit/layout.js): the plan check of the rooms' areas and the furniture's layout,
 * computed once (the scene does not change once the page is ready).
 */
async function sceneLayout() {
  if (state.layout) return state.layout;
  const { layout, auditLines, layoutReport } = await import("./layout.js");
  const L = layout(state.houseGroup);
  const out = { lines: auditLines(L), report: layoutReport(L) };
  if (state.ready) state.layout = out;
  return out;
}

/**
 * The quantities of the built scene (kit/quantities.js): computed once (the scene does not change once
 * the page is ready), with the height maps drawn by this page's renderer.
 */
function sceneQuantities() {
  if (!state.houseGroup) return Promise.resolve(null);
  state.quantities ??= (async () => {
    const { quantities, gpuMeasure } = await import("./quantities.js");
    return quantities(state.houseGroup, { measure: gpuMeasure(state.renderer), openPassages: (rooms, y) => openPassages(rooms, y) });
  })().catch((err) => { state.quantities = null; throw err; });
  return state.quantities;
}

/** The rooms of the floor plans in the scene, each with its storey's floor level `y`. */
function sceneRooms() {
  if (state.rooms) return state.rooms;
  const out = [];
  state.houseGroup?.traverse((o) => {
    if (o.userData?.kind === "floorPlan") out.push(...o.userData.rooms.map((r) => ({ ...r, y: o.userData.y, height: o.userData.height })));
  });
  // nothing is added once the page is ready (a new version of the scene is a new page): kept, as
  // every frame asks for the camera's room
  if (state.ready) state.rooms = out;
  return out;
}

/**
 * First-person walk (kit/walk.js): into `roomName`, else a hall, else the largest room; with no floor
 * plan, from where the camera stands. Already walking: jump to that room.
 */
async function startWalk(roomName) {
  endDollhouse();
  const rooms = sceneRooms();
  const room = rooms.find((r) => r.name === roomName)
    ?? rooms.find((r) => r.use === "hall")
    ?? [...rooms].sort((a, b) => b.area - a.area)[0];
  if (state.walk && room && Math.abs(room.y - state.walk.floorY) > 0.5) {
    // another storey: the walk finds its floor (and keeps the grids of the storeys it has been on)
    let cx = 0, cz = 0;
    for (const [x, z] of room.polygon) { cx += x / room.polygon.length; cz += z / room.polygon.length; }
    state.camera.position.set(cx, room.y + 0.02 + state.walk.eye, cz);
    state.walk.sync();
  }
  if (state.walk) {
    if (room) state.walk.jumpTo(room, givenView(room));
    return;
  }
  const { Walk } = await import("./walk.js");
  // into the house behind a short fade (#55): the first room's light is captured and every material
  // made ready (precompileHouse) before the picture comes back
  await fadeThrough(async () => {
    const camera = state.camera;
    if (room) {
      // stand in the room, at eye height above its floor, looking along it
      let cx = 0, cz = 0;
      for (const [x, z] of room.polygon) { cx += x / room.polygon.length; cz += z / room.polygon.length; }
      camera.position.set(cx, room.y + 0.02 + 1.45, cz);
      camera.lookAt(cx + 1, room.y + 1.5, cz);
    }
    state.savedFov = camera.fov;
    camera.fov = 70;
    camera.updateProjectionMatrix();
    state.controls.enabled = false;
    state.walk = new Walk({ camera, canvas: state.renderer.domElement, root: state.houseGroup, renderer: state.renderer,
      onChange: () => { state.needsRender = true; } });
    if (room) state.walk.jumpTo(room, givenView(room));
    window.__walk = state.walk; // debugging
    if (!HEADLESS) {
      mergeHouse();
      boxProjectHouse();
      renderFrame(); // the room's own light (its captures), which the materials are made ready for
      await precompileHouse();
    }
    state.needsRender = true;
  });
  try { parent.postMessage({ type: "house:walking", on: true }, "*"); } catch { /* noop */ }
}

/**
 * Every material of the house made ready for the walk, behind the fade into it (#55). A room nobody had
 * looked at froze the first glide into it for up to a second: three compiles a material's program when
 * its object is first drawn, and again when what the program depends on changes (indoors: the rooms'
 * captured light, a smaller environment than the outdoor one; the mirrors' patched materials); and the
 * GPU driver (ANGLE on D3D12 at least) finishes a program only at its first draw. So: compiled with the
 * room's light on, into the composer's target (linear, not tone mapped: the canvas's variants would
 * never be used), then the whole house drawn once through the composer with nothing culled.
 */
async function precompileHouse() {
  if (state.precompiled || HEADLESS || !state.renderer?.compileAsync) return;
  state.precompiled = true;
  const { renderer, scene, camera, composer } = state;
  const t0 = performance.now(), before = renderer.info.programs.length;
  const target = composer ? composer.renderTarget1 : null;
  const previous = renderer.getRenderTarget();
  renderer.setRenderTarget(target); // the programs are made now; only the wait for them is async
  const done = renderer.compileAsync(scene, camera);
  renderer.setRenderTarget(previous);
  await done.catch((err) => console.warn(`housekit: precompile: ${err?.message ?? err}`));
  const culled = [];
  scene.traverse((o) => {
    if ((o.isMesh || o.isPoints || o.isLine) && o.frustumCulled) {
      o.frustumCulled = false;
      culled.push(o);
    }
  });
  // the whole chain: the scene into the composer's target, and the ambient occlusion's own pass,
  // which draws every object again with its normals material
  if (composer) composer.render();
  else renderer.render(scene, camera);
  for (const o of culled) o.frustumCulled = true;
  state.frameCheckSkip = Math.max(state.frameCheckSkip ?? 0, 2); // the next frame may still be slow: not the machine
  state.precompile = { env: scene.environment?.image?.height ?? null, indoor: !!state.indoor, programs: [before, renderer.info.programs.length],
    ms: Math.round(performance.now() - t0) };
}

/**
 * The dollhouse (#62): storey `index` (storeys(), 0-based) seen from above, everything over it
 * hidden (its ceilings, the slab above, the storeys above, the roof: no clipping plane, which would
 * recompile every material; the walls keep their full height), the orbit centred on it. Hovering a room names it; a click on a
 * room walks into it. setView and the walk end it.
 */
function showDollhouse(index) {
  const s = storeys()[index];
  if (!s || !state.houseGroup) return false;
  stopTour();
  stopWalk();
  endDollhouse();
  // what starts above head height is over the storey (its ceilings, the slab and storeys above, the
  // pendants): a fixed cut rather than the ceiling, so a split level is cut under the next level's
  // floor too; the roof over it goes, so an attic opens
  const cutY = s.y + 1.7;
  const isRoof = (o) => (Array.isArray(o.material) ? o.material : [o.material]).some((m) => m?.userData?.role === "roof" || m?.userData?.finish === "roof-tiles");
  // the scene as built, not the walk's merged meshes (a merged tile can span two storeys)
  const unmerged = [];
  if (state.mergedGroup) {
    state.mergedGroup.visible = false;
    state.houseGroup.traverse((o) => { if (o.userData?.mergedInto) { o.layers.enable(0); unmerged.push(o); } });
  }
  const hidden = [], box = new THREE.Box3();
  state.houseGroup.traverse((o) => {
    if (!o.isMesh || !o.visible || !o.layers.isEnabled(0)) return;
    box.setFromObject(o);
    if (box.min.y > cutY || (box.max.y > cutY && (isRoof(o) || (box.max.y > cutY + 0.3 && facingUp(o) > 0.35)))) {
      o.visible = false;
      hidden.push(o);
    }
  });
  const rooms = s.plan.userData.rooms.map((r) => ({ ...r, y: s.y }));
  let [x0, z0, x1, z1] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const r of rooms) for (const [x, z] of r.polygon) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); z0 = Math.min(z0, z); z1 = Math.max(z1, z); }
  const c = new THREE.Vector3((x0 + x1) / 2, s.y, (z0 + z1) / 2), size = Math.max(x1 - x0, z1 - z0, 4);
  const { camera, controls } = state;
  camera.fov = DEFAULT_FOV;
  camera.updateProjectionMatrix();
  // from above, a little from the south: the rooms read like a plan, the walls and the furniture stand
  const dist = (size / 2 / Math.tan(THREE.MathUtils.degToRad(DEFAULT_FOV) / 2)) * 1.15;
  camera.position.set(c.x, c.y + dist * 0.92, c.z + dist * 0.4);
  controls.target.copy(c);
  controls.maxPolarAngle = Math.PI / 2.6;
  controls.update();
  const label = Object.assign(document.createElement("div"), {});
  label.style.cssText = "position:fixed;pointer-events:none;z-index:6;font:13px/1.2 system-ui,sans-serif;padding:3px 8px;border-radius:10px;background:rgba(20,20,20,.7);color:#fff;display:none";
  document.body.append(label);
  const canvas = state.renderer.domElement, ray = new THREE.Raycaster(), floor = new THREE.Plane(new THREE.Vector3(0, 1, 0), -s.y), hit = new THREE.Vector3();
  const roomAt = (e) => {
    const r = canvas.getBoundingClientRect();
    ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
    return ray.ray.intersectPlane(floor, hit) ? rooms.find((q) => insidePolygon([hit.x, hit.z], q.polygon)) : null;
  };
  let down = null;
  const move = (e) => {
    const r = roomAt(e);
    label.style.display = r ? "block" : "none";
    if (r) { label.textContent = r.name; label.style.left = `${e.clientX + 12}px`; label.style.top = `${e.clientY + 12}px`; }
  };
  const press = (e) => { down = [e.clientX, e.clientY]; };
  const release = (e) => {
    if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 5) return;
    const r = roomAt(e);
    if (r) startWalk(r.name).catch((err) => recordError(err?.message ?? err));
  };
  canvas.addEventListener("pointermove", move);
  canvas.addEventListener("pointerdown", press);
  canvas.addEventListener("pointerup", release);
  state.dollhouse = { index, hidden, unmerged, label, off: () => {
    canvas.removeEventListener("pointermove", move);
    canvas.removeEventListener("pointerdown", press);
    canvas.removeEventListener("pointerup", release);
  } };
  state.renderer.shadowMap.needsUpdate = true; // the hidden storeys and roof no longer shade it
  state.needsRender = true;
  state.accumulate?.reset();
  try { parent.postMessage({ type: "house:dollhouse", index }, "*"); } catch { /* noop */ }
  return true;
}

/**
 * How much of a mesh faces up or down rather than sideways: its triangles' |normal.y| weighted by
 * their area, in the world. A sloped or flat cover (an attic's lined roof, a slab) is near 1; walls,
 * a gable or a wardrobe are near 0 (#62).
 */
function facingUp(o) {
  const g = o.geometry, pos = g?.attributes?.position;
  if (!pos || pos.count > 300000) return 0;
  const idx = g.index, n = idx ? idx.count : pos.count;
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3(), m = o.matrixWorld;
  let up = 0, all = 0;
  for (let i = 0; i + 2 < n; i += 3) {
    const [ia, ib, ic] = idx ? [idx.getX(i), idx.getX(i + 1), idx.getX(i + 2)] : [i, i + 1, i + 2];
    a.fromBufferAttribute(pos, ia).applyMatrix4(m);
    b.fromBufferAttribute(pos, ib).applyMatrix4(m).sub(a);
    c.fromBufferAttribute(pos, ic).applyMatrix4(m).sub(a);
    b.cross(c);
    const area = b.length();
    all += area;
    up += Math.abs(b.y);
  }
  return all > 0 ? up / all : 0;
}

function endDollhouse() {
  const d = state.dollhouse;
  if (!d) return;
  for (const o of d.hidden) o.visible = true;
  for (const o of d.unmerged) o.layers.set(MERGED_LAYER);
  if (state.mergedGroup) state.mergedGroup.visible = true;
  d.off();
  d.label.remove();
  state.controls.maxPolarAngle = Math.PI / 2 - 0.02;
  state.dollhouse = null;
  state.renderer.shadowMap.needsUpdate = true;
  state.needsRender = true;
  try { parent.postMessage({ type: "house:dollhouse", index: null }, "*"); } catch { /* noop */ }
}

// The guided tour (#57): pause: seconds held in each room; skip: the uses it does not visit
const TOUR = { pause: 3, skip: new Set(["storage", "stair", "technical", "garage"]) };

/**
 * The rooms the tour visits, in order: one flat after the other (the part of a room's name after its
 * last dash: "Salon — Appartement 1"), storey by storey from the lowest; on each storey the hall and
 * the day rooms first (hall, living, dining, kitchen), then the others nearest first.
 */
function tourStops() {
  const rooms = sceneRooms().filter((r) => !TOUR.skip.has(r.use ?? ""));
  const flatOf = (r) => /\s[—–-]\s([^—–]+)$/.exec(r.name)?.[1].replace(/,.*$/, "").trim() ?? "";
  const day = ["hall", "living", "kitchen-living", "dining", "kitchen"];
  const centre = (r) => r.polygon.reduce(([x, z], [a, b]) => [x + a / r.polygon.length, z + b / r.polygon.length], [0, 0]);
  const out = [];
  for (const flat of [...new Set(rooms.map(flatOf))]) {
    const mine = rooms.filter((r) => flatOf(r) === flat);
    let at = null;
    for (const y of [...new Set(mine.map((r) => r.y))].sort((a, b) => a - b)) {
      const level = mine.filter((r) => r.y === y);
      const first = level.filter((r) => day.includes(r.use)).sort((a, b) => day.indexOf(a.use) - day.indexOf(b.use));
      for (const r of first) { out.push(r); at = centre(r); }
      const rest = level.filter((r) => !first.includes(r));
      while (rest.length) {
        if (at) rest.sort((a, b) => Math.hypot(...centre(a).map((v, i) => v - at[i])) - Math.hypot(...centre(b).map((v, i) => v - at[i])));
        const r = rest.shift();
        out.push(r);
        at = centre(r);
      }
    }
  }
  return out;
}

/** The tour: from room to room (goToRoom: a glide, or a fade), TOUR.pause seconds in each, until the last or until stopped. */
async function startTour() {
  if (state.tour?.running) return;
  const stops = tourStops();
  if (!stops.length) return;
  const tour = (state.tour = { running: true, stops, i: 0 });
  // the visitor taking over (a key, a click, the wheel) ends it
  tour.stop = () => stopTour();
  for (const t of ["keydown", "pointerdown", "wheel"]) window.addEventListener(t, tour.stop, { capture: true });
  const say = () => { try { parent.postMessage({ type: "house:tour", on: tour.running, room: stops[tour.i]?.name ?? null, index: tour.i, total: stops.length }, "*"); } catch { /* noop */ } };
  for (; tour.i < stops.length && tour.running; tour.i++) {
    say();
    if (state.walk) await goToRoom(stops[tour.i].name);
    else await startWalk(stops[tour.i].name);
    for (let t = 0; t < TOUR.pause * 1000 && tour.running; t += 100) await new Promise((done) => setTimeout(done, 100));
  }
  if (tour.i >= stops.length) tour.i = stops.length - 1;
  stopTour();
}

function stopTour() {
  const tour = state.tour;
  if (!tour?.running) return;
  tour.running = false;
  for (const t of ["keydown", "pointerdown", "wheel"]) window.removeEventListener(t, tour.stop, { capture: true });
  try { parent.postMessage({ type: "house:tour", on: false, room: tour.stops[tour.i]?.name ?? null, index: tour.i, total: tour.stops.length }, "*"); } catch { /* noop */ }
}

// Going from room to room (#55): glideMax: seconds a glide may last, else a fade; fade: seconds to
// black and back
const TRANSITION = { glideMax: 6, fade: 0.3 };

/**
 * To a room's viewpoint the way the viewer's "Go to a room" (and the tour) asks for it: walking
 * there (a glide) when it is on this storey, in this flat and at most TRANSITION.glideMax seconds
 * away; else through a fade to black, behind which the walk changes storey and the room's light is
 * captured and settled. Starts the walk when it is not on. Resolves once there: "glide" or "fade"
 * (false: no such room).
 */
async function goToRoom(roomName) {
  const room = sceneRooms().find((r) => r.name === roomName);
  if (!room) return false;
  if (!state.walk) {
    await startWalk(roomName);
    return "fade";
  }
  const w = state.walk;
  const given = givenView(room);
  if (Math.abs(room.y - w.floorY) < 0.5) {
    const v = w.viewpoint(room, given);
    const plan = v && w.plan(v.at[0], v.at[1]);
    if (v && (!plan || (plan.reaches && plan.duration <= TRANSITION.glideMax))) {
      w.goTo(v.at[0], v.at[1], { yaw: v.yaw, pitch: 0 }, plan); // no plan: already there, only turns
      state.needsRender = true;
      await new Promise((done) => {
        const wait = () => (w.gliding && state.walk === w ? requestAnimationFrame(wait) : done());
        requestAnimationFrame(wait);
      });
      return "glide";
    }
  }
  await fadeThrough(() => {
    if (Math.abs(room.y - w.floorY) >= 0.5) {
      // onto the other storey: the walk finds its floor (its grids are kept once worked out)
      let cx = 0, cz = 0;
      for (const [x, z] of room.polygon) { cx += x / room.polygon.length; cz += z / room.polygon.length; }
      state.camera.position.set(cx, room.y + 0.02 + w.eye, cz);
      w.sync();
    }
    w.jumpTo(room, given);
  });
  return "fade";
}

/**
 * `change()` behind a short fade to black: the page goes black, the change happens, the new place is
 * drawn (its rooms captured, the exposure and the lamps set at once rather than gliding there), then
 * the picture comes back.
 */
async function fadeThrough(change) {
  const frame = () => new Promise((done) => requestAnimationFrame(() => done()));
  const settle = () => {
    if (state.exposureTarget !== undefined) applyExposure(state.exposureTarget);
    if (state.balancePass && state.balanceTarget) state.balancePass.uniforms.gain.value.copy(state.balanceTarget);
    if (state.lamps) state.lamps.at = null; // the next frame places the lamps at once
  };
  if (HEADLESS) {
    await change();
    settle();
    return;
  }
  if (!state.veil) {
    state.veil = document.createElement("div");
    state.veil.style.cssText = `position:fixed;inset:0;background:#000;opacity:0;pointer-events:none;z-index:5;transition:opacity ${TRANSITION.fade}s ease`;
    document.body.append(state.veil);
  }
  const veil = state.veil;
  veil.style.opacity = "1";
  await new Promise((done) => setTimeout(done, TRANSITION.fade * 1000));
  await change();
  state.needsRender = true;
  await frame(); // the first frame captures the new storey's rooms: the light is right after it
  settle();
  state.needsRender = true;
  await frame();
  await frame();
  veil.style.opacity = "0";
  await new Promise((done) => setTimeout(done, TRANSITION.fade * 1000));
}

/**
 * The builder's own viewpoint for a room, if any: `view: { at: [x, z], look: [x, z] }` on the room
 * in its floor plan, else a view the scene returned as `room-<n>` (n: the room's place in
 * sceneRooms, 1-based; its height and pitch are not used: the walk's eye is level). Else the walk
 * works one out (Walk.viewpoint).
 */
function givenView(room) {
  if (room.view?.at && room.view?.look) return room.view;
  const n = sceneRooms().findIndex((r) => r.name === room.name && r.y === room.y) + 1;
  const v = n > 0 ? state.views[`room-${n}`] : null;
  if (!v?.pos || !v.target) return null;
  const t = v.target.isVector3 ? [v.target.x, v.target.z] : [v.target[0], v.target[2]];
  return { at: [v.pos[0], v.pos[2]], look: t };
}

// The photographer's view of a room (#43), for stills: where the walk stands (its viewpoint sees the
// most of the room, a window in frame), lower (1.3 m), a 24 mm lens, level (vertical lines stay
// vertical) and the frame shifted down rather than tilted, as a shift lens does: less ceiling.
const PHOTO = { eye: 1.3, fov: 53, shift: 0.12 };

function photoFrame() {
  const w = state.walk, camera = state.camera;
  if (!w) return;
  w.eye = PHOTO.eye;
  camera.position.y = w.floorY + PHOTO.eye; // level: jumpTo leaves the pitch at 0
  // a frame `shift` of its height lower inside a taller one, at the lens's field of view
  const size = state.renderer.getSize(new THREE.Vector2());
  const tall = 1 + 2 * PHOTO.shift;
  camera.fov = THREE.MathUtils.radToDeg(2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(PHOTO.fov) / 2) * tall));
  camera.setViewOffset(size.x, size.y * tall, 0, size.y * 2 * PHOTO.shift, size.x, size.y);
  camera.updateProjectionMatrix();
  state.accumulate?.reset();
}

function stopWalk() {
  if (!state.walk) return;
  state.camera.clearViewOffset();
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
  endDollhouse();
  state.camera.clearViewOffset();
  // interiors: an eye-height view of a room (room-<n>, 1-based in the order of the rooms), the
  // photographer's view of it (photo-<n>), or the plan section of a storey (plan-section-<n>) drawn
  // at the framing report() gives
  const roomView = /^(room|photo)-(\d+)$/.exec(name);
  const sectionView = /^plan-section-(\d+)$/.exec(name);
  if (roomView) {
    const room = sceneRooms()[Number(roomView[2]) - 1];
    if (!room) { recordError(`unknown view: ${name} (${sceneRooms().length} rooms)`); return false; }
    await startWalk(room.name);
    if (roomView[1] === "photo") photoFrame();
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
