// housekit/sunstudy — the sun study on a presentation page with aligned surroundings (#49): the real sun
// for a day and an hour (sun.js), the shadows it casts over the 3D radius (the house, the neighbours,
// the lidar's trees, the terrain), the far relief as a horizon mask, the analytic sky following the
// sun; and the sunshine-hours map: how many hours of direct sun each spot of the plot gets over a day
// (or, on average, a season), accumulated on the GPU from the very shadow map the page draws with.
//
//   const study = createSunStudy({ renderer, scene, sun, look, sampleHorizon, context, state })
//   study.set({ date: "2026-06-21", hour: 15.5 })  → { elevation, azimuth, horizon, lit, sunrise, … }
//   await study.hours({ date, span: "day" | "season", onProgress })  → { max, mean, … }
//   study.hideHours(); study.end()

import * as THREE from "three";
import { Sky } from "three/addons/objects/Sky.js";
import { horizonAt, sceneSunDirection, sunPosition, sunTimes, zurichToUTC } from "./sun.js";

const STUDY_SHADOW = 4096; // the shadow map over the 3D radius
const CELL = 0.25; // metres per texel of the sunshine map

const parseDate = (date) => {
  const [y, m, d] = String(date).split("-").map(Number);
  if (!y || !m || !d) throw new Error(`not a date: ${date}`);
  return [y, m, d];
};

/** Is a mesh vegetation (leaves, branches, a bush's core): a caster, never the surface the map is on. */
function vegetation(o) {
  for (let p = o; p; p = p.parent) {
    const k = p.userData?.kind;
    if (k === "leaves" || k === "wood" || k === "core" || k === "tree" || k === "bush" || k === "trees" || k === "hedge") return true;
  }
  return false;
}

export function createSunStudy({ renderer, scene, sun, look, sampleHorizon, context, state }) {
  const [lat, lon] = context.place;
  const site = new THREE.Vector3(context.site[0], 0, context.site[1]);
  const reach = Math.min(180, (context.near ?? 120) + 15);
  let saved = null, sky = null, skyBaked = null, lastSky = null;
  let overlay = null, accum = null;

  // the analytic sky for the sun's direction: an environment map (the light from the sky) that is also
  // the sky the camera sees, and the haze colour at the horizon (as setupPresentationSky does)
  function bakeSky(dir) {
    if (!sky) {
      sky = new Sky();
      sky.scale.setScalar(1500);
      const u = sky.material.uniforms;
      u.turbidity.value = look.sky.turbidity;
      u.rayleigh.value = look.sky.rayleigh;
      u.mieDirectionalG.value = look.sky.mieDirectionalG;
      u.mieCoefficient.value = 0.0004;
    }
    sky.material.uniforms.sunPosition.value.copy(dir);
    const staging = new THREE.Scene();
    staging.add(sky);
    const pmrem = new THREE.PMREMGenerator(renderer);
    const map = pmrem.fromScene(staging, 0.02, 1, 4000).texture;
    pmrem.dispose();
    const horizon = sampleHorizon(renderer, staging, dir).multiplyScalar(look.backgroundIntensity);
    staging.remove(sky);
    skyBaked?.dispose();
    skyBaked = map;
    scene.environment = map;
    scene.environmentIntensity = look.environmentIntensity;
    scene.background = map;
    scene.backgroundIntensity = look.backgroundIntensity;
    scene.backgroundBlurriness = 0;
    if (scene.fog) scene.fog.color.copy(horizon);
  }

  function begin() {
    if (saved) return;
    const sh = sun.shadow;
    saved = {
      environment: scene.environment, environmentIntensity: scene.environmentIntensity,
      background: scene.background, backgroundIntensity: scene.backgroundIntensity,
      fog: scene.fog?.color.clone(), dome: state.presentation?.dome?.visible,
      color: sun.color.clone(), intensity: sun.intensity, position: sun.position.clone(), target: sun.target.position.clone(),
      camera: { l: sh.camera.left, r: sh.camera.right, t: sh.camera.top, b: sh.camera.bottom, n: sh.camera.near, f: sh.camera.far },
      mapSize: sh.mapSize.x, bias: sh.bias,
    };
    if (state.presentation?.dome) state.presentation.dome.visible = false;
    // the shadows reach the whole 3D radius: a low winter sun throws a 10 m house's shadow 27 m and more
    Object.assign(sh.camera, { left: -reach, right: reach, top: reach, bottom: -reach, near: 1, far: 1600 });
    sh.camera.updateProjectionMatrix();
    if (sh.mapSize.x < STUDY_SHADOW) {
      sh.mapSize.set(STUDY_SHADOW, STUDY_SHADOW);
      sh.map?.dispose();
      sh.map = null;
    }
    sh.bias = -0.0006;
  }

  function end() {
    hideHours();
    if (!saved) return;
    const sh = sun.shadow;
    Object.assign(scene, { environment: saved.environment, environmentIntensity: saved.environmentIntensity, background: saved.background, backgroundIntensity: saved.backgroundIntensity });
    if (saved.fog && scene.fog) scene.fog.color.copy(saved.fog);
    if (state.presentation?.dome) state.presentation.dome.visible = saved.dome;
    sun.color.copy(saved.color);
    sun.intensity = saved.intensity;
    sun.position.copy(saved.position);
    sun.target.position.copy(saved.target);
    sun.target.updateMatrixWorld();
    const c = saved.camera;
    Object.assign(sh.camera, { left: c.l, right: c.r, top: c.t, bottom: c.b, near: c.n, far: c.f });
    sh.camera.updateProjectionMatrix();
    if (sh.mapSize.x !== saved.mapSize) {
      sh.mapSize.set(saved.mapSize, saved.mapSize);
      sh.map?.dispose();
      sh.map = null;
    }
    sh.bias = saved.bias;
    skyBaked?.dispose();
    skyBaked = null;
    lastSky = null;
    saved = null;
    changed();
  }

  function changed() {
    if (renderer.shadowMap.enabled) renderer.shadowMap.needsUpdate = true;
    state.accumulate?.rememberSun?.();
    state.accumulate?.reset?.();
    state.needsRender = true;
  }

  /** The sun at a Swiss local time: where it stands, whether the far relief hides it. */
  function position(y, m, d, hour) {
    const p = sunPosition(zurichToUTC(y, m, d, hour), lat, lon);
    const horizon = context.horizon ? horizonAt(context.horizon, p.azimuth) : 0;
    return { ...p, horizon, lit: p.elevation > Math.max(0, horizon) };
  }

  /** The day's sun: sunrise and sunset on a flat horizon, and when it first and last clears the relief. */
  const days = new Map();
  function day(date) {
    if (days.has(date)) return days.get(date);
    const [y, m, d] = parseDate(date);
    const t = sunTimes(y, m, d, lat, lon);
    let first = null, last = null;
    if (t.sunrise !== null) {
      for (let h = t.sunrise; h <= t.sunset; h += 1 / 30) {
        if (position(y, m, d, h).lit) { if (first === null) first = h; last = h; }
      }
    }
    const out = { ...t, firstSun: first, lastSun: last };
    days.set(date, out);
    return out;
  }

  function set({ date, hour }) {
    const [y, m, d] = parseDate(date);
    begin();
    const p = position(y, m, d, hour);
    const dir = new THREE.Vector3(...sceneSunDirection(p.elevation, p.azimuth, context.rotation));
    sun.target.position.copy(site);
    sun.target.updateMatrixWorld();
    sun.position.copy(site).addScaledVector(dir, 800);
    // low sun: dimmer and warmer; behind the relief or below the horizon: no direct light at all
    const k = THREE.MathUtils.smoothstep(p.elevation, 2, 25);
    sun.intensity = p.lit ? look.sunIntensity * (0.3 + 0.7 * k) : 0;
    sun.color.set("#ffad6b").lerp(new THREE.Color(look.sunColor), THREE.MathUtils.smoothstep(p.elevation, 3, 30));
    // the sky follows the sun (baked again once it has moved a little)
    const skyDir = new THREE.Vector3(...sceneSunDirection(Math.max(p.elevation, -4), p.azimuth, context.rotation));
    if (!lastSky || lastSky.angleTo(skyDir) > 0.6 * (Math.PI / 180)) {
      bakeSky(skyDir);
      lastSky = skyDir;
    }
    changed();
    return { date, hour, elevation: p.elevation, azimuth: p.azimuth, horizon: p.horizon, lit: p.lit, ...day(date) };
  }

  // ---- the sunshine-hours map

  /** The surfaces seen from above over the study square, vegetation left out: their heights (world y). */
  function topHeights(half, n) {
    const rt = new THREE.WebGLRenderTarget(n, n, { type: THREE.FloatType, depthBuffer: true });
    const cam = new THREE.OrthographicCamera(-half, half, half, -half, 1, 2000);
    cam.position.set(site.x, 1000, site.z);
    cam.up.set(0, 0, -1);
    cam.lookAt(site.x, 0, site.z);
    cam.updateMatrixWorld();
    const material = new THREE.ShaderMaterial({
      side: THREE.DoubleSide,
      vertexShader: "varying float vY; void main() { vec4 w = modelMatrix * vec4(position, 1.0); vY = w.y; gl_Position = projectionMatrix * viewMatrix * w; }",
      fragmentShader: "varying float vY; void main() { gl_FragColor = vec4(vY, 0.0, 0.0, 1.0); }",
    });
    const hidden = [];
    scene.traverse((o) => {
      const k = o.userData?.kind;
      if (o.visible && (o.isMesh || o.isPoints || o.isLine || o.isSprite) && (vegetation(o) || k === "sky" || o.name === "Surroundings: far landscape" || o.material?.transparent)) {
        hidden.push(o);
        o.visible = false;
      }
    });
    const prev = { target: renderer.getRenderTarget(), override: scene.overrideMaterial, background: scene.background, fog: scene.fog };
    scene.overrideMaterial = material;
    scene.background = null;
    scene.fog = null;
    renderer.setRenderTarget(rt);
    renderer.setClearColor(0x000000, 0);
    renderer.clear();
    renderer.render(scene, cam);
    const px = new Float32Array(n * n * 4);
    renderer.readRenderTargetPixels(rt, 0, 0, n, n, px);
    renderer.setRenderTarget(prev.target);
    Object.assign(scene, { overrideMaterial: prev.override, background: prev.background, fog: prev.fog });
    for (const o of hidden) o.visible = true;
    rt.dispose();
    material.dispose();
    // rows bottom → top of the target = south → north (the camera's up is -z)
    const h = new Float32Array(n * n);
    for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) h[j * n + i] = px[4 * (j * n + i) + 3] > 0 ? px[4 * (j * n + i)] : NaN;
    return h;
  }

  /** A sheet over the top surfaces (2 cm above them), in the scene's frame, its UVs on the map's texels. */
  function receiverGeometry(half, n, heights) {
    const g = new THREE.PlaneGeometry(2 * half, 2 * half, n - 1, n - 1);
    g.rotateX(-Math.PI / 2);
    const p = g.attributes.position;
    // the plane's vertices run row by row from north (z = -half) to south; the heights from south to north
    let fallback = 0, count = 0;
    for (const v of heights) if (Number.isFinite(v)) { fallback += v; count++; }
    fallback = count ? fallback / count : 0;
    for (let k = 0; k < p.count; k++) {
      const i = k % n, row = Math.floor(k / n);
      const y = heights[(n - 1 - row) * n + i];
      p.setXYZ(k, p.getX(k) + site.x, (Number.isFinite(y) ? y : fallback) + 0.02, p.getZ(k) + site.z);
    }
    g.computeVertexNormals();
    return g;
  }

  function receiverMaterial() {
    return new THREE.ShaderMaterial({
      lights: true,
      uniforms: THREE.UniformsUtils.merge([THREE.UniformsLib.lights, { uWeight: { value: 0 } }]),
      vertexShader: `
        #include <common>
        #include <shadowmap_pars_vertex>
        void main() {
          vec3 objectNormal = normal;
          vec3 transformedNormal = normalMatrix * objectNormal;
          vec4 worldPosition = modelMatrix * vec4(position, 1.0);
          #include <shadowmap_vertex>
          gl_Position = projectionMatrix * viewMatrix * worldPosition;
        }`,
      fragmentShader: `
        #include <common>
        #include <packing>
        #include <lights_pars_begin>
        #include <shadowmap_pars_fragment>
        uniform float uWeight;
        void main() {
          float lit = 1.0;
          #if defined( USE_SHADOWMAP ) && NUM_DIR_LIGHT_SHADOWS > 0
            DirectionalLightShadow s = directionalLightShadows[ 0 ];
            lit = getShadow( directionalShadowMap[ 0 ], s.shadowMapSize, 1.0, s.shadowBias, 0.0, vDirectionalShadowCoord[ 0 ] );
          #endif
          gl_FragColor = vec4( lit * uWeight, uWeight, 0.0, 1.0 );
        }`,
      blending: THREE.CustomBlending,
      blendEquation: THREE.AddEquation,
      blendSrc: THREE.OneFactor,
      blendDst: THREE.OneFactor,
      depthTest: false,
      depthWrite: false,
      transparent: true,
    });
  }

  /** The colours of the map: hours (0 → max) from deep blue through green and yellow to red. */
  const RAMP = ["#2b3a8f", "#2f7fc1", "#3fb39b", "#9bd15a", "#f2d64b", "#f08a3a", "#d6402b"];

  function overlayMaterial(texture, max) {
    const stops = RAMP.map((c) => new THREE.Color(c));
    return new THREE.ShaderMaterial({
      uniforms: { uMap: { value: texture }, uMax: { value: max }, uStops: { value: stops } },
      vertexShader: "varying vec2 vUv; varying float vUp; void main() { vUv = uv; vUp = normal.y; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
      fragmentShader: `
        uniform sampler2D uMap; uniform float uMax; uniform vec3 uStops[${RAMP.length}];
        varying vec2 vUv;
        varying float vUp;
        void main() {
          // the sheet drops down the walls between a roof and the ground: no colour there
          if (vUp < 0.45) discard;
          vec4 a = texture2D(uMap, vUv);
          float t = clamp(a.r / max(uMax, 1e-3), 0.0, 1.0) * ${(RAMP.length - 1).toFixed(1)};
          int i = int(floor(min(t, ${(RAMP.length - 1.001).toFixed(3)})));
          vec3 c = uStops[0];
          for (int k = 0; k < ${RAMP.length - 1}; k++) if (k == i) c = mix(uStops[k], uStops[k + 1], t - float(k));
          // bands every hour: the map reads like a contour plan
          float band = fract(a.r) < 0.06 ? 0.82 : 1.0;
          gl_FragColor = vec4(c * band, 0.82);
          #include <colorspace_fragment>
        }`,
      transparent: true,
      depthWrite: false,
      polygonOffset: true,
      polygonOffsetFactor: -4,
    });
  }

  function hideHours() {
    if (!overlay) return;
    scene.remove(overlay);
    overlay.geometry.dispose();
    overlay.material.dispose();
    accum?.dispose();
    overlay = accum = null;
    state.needsRender = true;
  }

  /**
   * The sunshine-hours map over the plot: for every sun position of the day (every 10 minutes) or of
   * the season (13 days a week apart around `date`, every 20 minutes) the sun's shadow map is drawn
   * and every spot it lights gains that time. Returns { max, mean, steps, span, from, to } (hours per day).
   */
  async function hours({ date, span = "day", onProgress }) {
    const [y, m, d] = parseDate(date);
    hideHours();
    begin();
    const keep = { position: sun.position.clone(), intensity: sun.intensity, color: sun.color.clone() };
    const half = Math.min(80, Math.max(40, reach * 0.5));
    const n = Math.round((2 * half) / CELL);
    const heights = topHeights(half, n);
    const geo = receiverGeometry(half, n, heights);
    const receiver = new THREE.Mesh(geo, receiverMaterial());
    receiver.receiveShadow = true;
    // its own scene (the sun is moved into it for pass B: three takes the lights a camera's layers see)
    const rscene = new THREE.Scene();
    rscene.add(receiver);
    accum = new THREE.WebGLRenderTarget(n, n, { type: THREE.FloatType, depthBuffer: false, minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter });
    const top = new THREE.OrthographicCamera(-half, half, half, -half, 1, 3000);
    top.position.set(site.x, 1500, site.z);
    top.up.set(0, 0, -1);
    top.lookAt(site.x, 0, site.z);
    top.updateMatrixWorld();
    // pass A: the shadow map of the whole scene, through a camera that sees none of it
    const blind = new THREE.OrthographicCamera(-0.01, 0.01, 0.01, -0.01, 0.1, 0.2);
    blind.position.set(site.x, -5000, site.z);
    blind.lookAt(site.x, -6000, site.z);
    blind.updateMatrixWorld();
    const tiny = new THREE.WebGLRenderTarget(1, 1);
    // the samples: the days, the times
    const dayList = span === "season" ? Array.from({ length: 13 }, (_, k) => k - 6) : [0];
    const step = span === "season" ? 20 / 60 : 10 / 60;
    const samples = [];
    for (const off of dayList) {
      const dt = new Date(Date.UTC(y, m - 1, d) + off * 7 * 86400e3);
      const [yy, mm, dd] = [dt.getUTCFullYear(), dt.getUTCMonth() + 1, dt.getUTCDate()];
      const t = sunTimes(yy, mm, dd, lat, lon);
      if (t.sunrise === null) continue;
      for (let h = t.sunrise + step / 2; h < t.sunset; h += step) {
        const p = position(yy, mm, dd, h);
        if (p.lit) samples.push({ p, label: `${yy}-${mm}-${dd}` });
      }
    }
    const weight = step / dayList.length; // hours per day
    const prev = { target: renderer.getRenderTarget(), auto: renderer.shadowMap.autoUpdate, autoClear: renderer.autoClear, background: scene.background, fog: scene.fog };
    renderer.setRenderTarget(accum);
    renderer.setClearColor(0x000000, 0);
    renderer.clear();
    renderer.autoClear = false;
    renderer.shadowMap.autoUpdate = false;
    receiver.material.uniforms.uWeight.value = weight;
    sun.intensity = 1;
    // what is drawn whatever the camera (the far landscape, the sky dome) and casts no shadow: hidden
    // while the samples run, or every one of them would draw it again
    const idle = [];
    scene.traverse((o) => { if (o.isMesh && o.visible && o.frustumCulled === false && !o.castShadow) { idle.push(o); o.visible = false; } });
    const t0 = performance.now();
    for (let k = 0; k < samples.length; k++) {
      const { p } = samples[k];
      const dir = new THREE.Vector3(...sceneSunDirection(p.elevation, p.azimuth, context.rotation));
      sun.position.copy(site).addScaledVector(dir, 800);
      sun.updateMatrixWorld();
      // A: the shadow map from this sun
      renderer.shadowMap.needsUpdate = true;
      renderer.setRenderTarget(tiny);
      renderer.render(scene, blind);
      // B: the receiver, lit or not by it, added in
      renderer.shadowMap.needsUpdate = false;
      rscene.add(sun, sun.target);
      renderer.setRenderTarget(accum);
      renderer.render(rscene, top);
      scene.add(sun, sun.target);
      if (onProgress && (k % 24 === 23 || k === samples.length - 1)) {
        onProgress({ done: k + 1, total: samples.length });
        await new Promise((r) => setTimeout(r, 0));
      }
    }
    const ms = performance.now() - t0;
    for (const o of idle) o.visible = true;
    Object.assign(renderer, { autoClear: prev.autoClear });
    renderer.shadowMap.autoUpdate = prev.auto;
    renderer.setRenderTarget(prev.target);
    tiny.dispose();
    receiver.material.dispose();
    sun.position.copy(keep.position);
    sun.intensity = keep.intensity;
    sun.color.copy(keep.color);
    // the result: hours per day per texel (R), where there is a surface
    const px = new Float32Array(n * n * 4);
    renderer.readRenderTargetPixels(accum, 0, 0, n, n, px);
    let max = 0, sum = 0, cells = 0;
    for (let i = 0; i < n * n; i++) {
      if (!Number.isFinite(heights[i])) continue;
      const v = px[4 * i];
      max = Math.max(max, v);
      sum += v;
      cells++;
    }
    const total = samples.length * weight; // the most a spot open to the sky can get
    overlay = new THREE.Mesh(geo, overlayMaterial(accum.texture, Math.max(total, 1e-3)));
    overlay.position.y = 0.03;
    overlay.renderOrder = 5;
    overlay.name = "Sunshine hours";
    overlay.userData = { kind: "overlay", excludeFromBounds: true };
    scene.add(overlay);
    changed();
    const from = new Date(Date.UTC(y, m - 1, d) + dayList[0] * 7 * 86400e3).toISOString().slice(0, 10);
    const to = new Date(Date.UTC(y, m - 1, d) + dayList[dayList.length - 1] * 7 * 86400e3).toISOString().slice(0, 10);
    const result = { span, date, from, to, max: total, best: max, mean: cells ? sum / cells : 0, steps: samples.length, ms: Math.round(ms), size: [2 * half, 2 * half], cell: CELL };
    overlay.userData.result = result;
    return result;
  }

  /** What the map says at a scene point (hours per day), for checks. */
  function hoursAt(x, z) {
    if (!overlay || !accum) return null;
    const half = overlay.userData.result.size[0] / 2, n = accum.width;
    const i = Math.floor(((x - site.x + half) / (2 * half)) * n), j = Math.floor(((site.z - z + half) / (2 * half)) * n);
    if (i < 0 || j < 0 || i >= n || j >= n) return null;
    const px = new Float32Array(4);
    renderer.readRenderTargetPixels(accum, i, j, 1, 1, px);
    return px[0];
  }

  return { set, end, day, hours, hideHours, hoursAt, get on() { return Boolean(saved); } };
}

export default { createSunStudy };
