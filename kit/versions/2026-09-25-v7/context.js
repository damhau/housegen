// housekit/context — the real surroundings of the plot (#39), for the viewer's presentation looks only:
// the ground as surveyed, the aerial photo on it, the neighbouring buildings. The backend fetched them
// from swisstopo (geo/context.py) into a folder the page is given as ?context=<url>; the build path
// (quality=…: builder, critic, version pictures) never loads them.
//
//   const ctx = await loadContext(url, { houseGroup, heightAt })   → { group, heightAt, disc, credits }
//
// The files are in a local frame around the searched point (x east, z south, y the altitude); the
// alignment in context.json places the scene in it (see geo/context.py). The surroundings are cut out
// under the builder's own ground (its terrain meshes, or the house's footprint on flat ground) and
// blended into it over a ring, and they fade into the meadow at their rim.

import * as THREE from "three";
import { finishMaterial, finishesReady, metricUV } from "./finishes.js";

const RING = 5; // metres over which the real ground joins the builder's (short: a longer ring tilts the roads beside the plot)
const SINK = 3; // metres the real ground lies under the builder's inside its plot
const RIM = 25; // metres over which the photo fades into the meadow at the edge of the disc
const DEG = Math.PI / 180;

/** Scene ← local transform of an alignment: rotate by `rotation`° (clockwise from above) about the scene's origin. */
function transforms(a) {
  const th = (a.rotation ?? 0) * DEG, c = Math.cos(th), s = Math.sin(th);
  // three.js: rotation.y = +th turns +x toward -z (counter-clockwise from above); the scene is turned
  // clockwise by th in the local frame, so local → scene turns counter-clockwise by th
  const toScene = (x, y, z) => {
    const dx = x - a.x, dz = z - a.z;
    return [c * dx + s * dz, y - a.ground, -s * dx + c * dz];
  };
  const toLocal = (x, z) => [a.x + c * x - s * z, a.z + s * x + c * z];
  return { toScene, toLocal };
}

/** The scene's own ground: its terrain meshes' boxes, or the house's footprint (+3 m) when it has none. */
function ownGround(houseGroup) {
  const boxes = [];
  const tmp = new THREE.Box3();
  houseGroup.traverse((o) => {
    if (o.isMesh && o.userData?.kind === "terrain" && !o.userData.excludeFromBounds) {
      tmp.setFromObject(o);
      if (!tmp.isEmpty()) boxes.push(tmp.clone());
    }
  });
  if (boxes.length) return { boxes, flat: false };
  const b = new THREE.Box3();
  houseGroup.traverse((o) => {
    if (!o.isMesh || o.userData?.excludeFromBounds) return;
    tmp.setFromObject(o);
    if (!tmp.isEmpty()) b.union(tmp);
  });
  return { boxes: b.isEmpty() ? [] : [b.expandByScalar(3)], flat: true };
}

const distToBox = (x, z, b) => Math.hypot(Math.max(b.min.x - x, x - b.max.x, 0), Math.max(b.min.z - z, z - b.max.z, 0));

/**
 * Load the surroundings at `url` (a folder with context.json). `heightAt(x, z)` is the scene's own
 * ground (house.groundY), used to join the real ground to it.
 */
export async function loadContext(url, { houseGroup, heightAt }) {
  const base = new URL(url, location.href);
  const get = (f) => fetch(new URL(f, base)).then((r) => { if (!r.ok) throw new Error(`${f}: HTTP ${r.status}`); return r; });
  const meta = await (await get("context.json")).json();
  const [terrainBuf, buildings, photo] = await Promise.all([
    get(meta.terrain.file).then((r) => r.arrayBuffer()),
    get(meta.buildings.file).then((r) => r.json()),
    new THREE.TextureLoader().loadAsync(new URL(meta.photo.file, base).href),
  ]);
  photo.colorSpace = THREE.SRGBColorSpace;
  photo.anisotropy = 8;
  const R = meta.radius, N = meta.terrain.size, step = meta.terrain.step;
  const alt = new Float32Array(terrainBuf);
  const { toScene, toLocal } = transforms(meta.alignment);
  const own = ownGround(houseGroup);
  const ownY = (x, z) => (own.flat ? 0 : (Number.isFinite(heightAt?.(x, z)) ? heightAt(x, z) : 0));

  // the real ground at a local point (bilinear on the grid), in scene y
  const realY = (lx, lz) => {
    const fi = Math.min(N - 1.001, Math.max(0, (lx + R) / step)), fj = Math.min(N - 1.001, Math.max(0, (lz + R) / step));
    const i = Math.floor(fi), j = Math.floor(fj), u = fi - i, v = fj - j;
    const h = (ii, jj) => alt[jj * N + ii];
    return (h(i, j) * (1 - u) + h(i + 1, j) * u) * (1 - v) + (h(i, j + 1) * (1 - u) + h(i + 1, j + 1) * u) * v - meta.alignment.ground;
  };

  // ---- the ground: the disc of the grid, cut out under the scene's own ground, joined to it
  const positions = new Float32Array(N * N * 3), uvs = new Float32Array(N * N * 2), fade = new Float32Array(N * N);
  for (let j = 0; j < N; j++) for (let i = 0; i < N; i++) {
    const k = j * N + i, lx = -R + i * step, lz = -R + j * step;
    const [x, y, z] = toScene(lx, alt[k], lz);
    let yy = y;
    let d = Infinity, nearest = null;
    for (const b of own.boxes) { const e = distToBox(x, z, b); if (e < d) { d = e; nearest = b; } }
    if (nearest) {
      if (d <= 0) {
        // under the builder's ground: sunk below it (deeper than a pool), never cut out, so no gap
        // opens where that ground does not fill its box exactly (a slope, a retaining wall)
        // just under it along its edge (no step where the two meet), then down
        const inside = Math.min(x - nearest.min.x, nearest.max.x - x, z - nearest.min.z, nearest.max.z - z);
        yy = ownY(x, z) - 0.05 - SINK * THREE.MathUtils.smoothstep(inside, 1.5, 4);
      } else if (d < RING) {
        // on the rim of the builder's ground: its height there, easing into the real ground
        const cx = Math.min(nearest.max.x, Math.max(nearest.min.x, x)), cz = Math.min(nearest.max.z, Math.max(nearest.min.z, z));
        yy = THREE.MathUtils.lerp(ownY(cx, cz) - 0.03, y, THREE.MathUtils.smoothstep(d, 0, RING));
      }
    }
    positions.set([x, yy, z], k * 3);
    uvs.set([(lx + R) / (2 * R), 1 - (lz + R) / (2 * R)], k * 2);
    const r = Math.hypot(lx, lz);
    fade[k] = THREE.MathUtils.smoothstep(r, R - RIM, R);
  }
  const index = [];
  for (let j = 0; j < N - 1; j++) for (let i = 0; i < N - 1; i++) {
    const a = j * N + i, b = a + 1, c = a + N, d = c + 1;
    const lx = -R + (i + 0.5) * step, lz = -R + (j + 0.5) * step;
    if (Math.hypot(lx, lz) > R - step) continue; // a disc: no corners poking out of the meadow
    index.push(a, c, b, b, c, d);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geo.setAttribute("uv", new THREE.BufferAttribute(uvs, 2));
  geo.setAttribute("fade", new THREE.BufferAttribute(fade, 1));
  geo.setIndex(index);
  geo.computeVertexNormals();
  const groundMat = new THREE.MeshStandardMaterial({ map: photo, roughness: 1 });
  const meadowColor = { value: new THREE.Color(0.35, 0.36, 0.25) };
  // with the far landscape under it, the disc stops at its rim (the same photograph continues, coarser);
  // without, its rim fades into the meadow's colour
  const dissolve = { value: meta.far ? 1 : 0 };
  const detail = grain(256);
  groundMat.onBeforeCompile = (shader) => {
    shader.uniforms.uMeadow = meadowColor;
    shader.uniforms.uDissolve = dissolve;
    shader.uniforms.uDetail = { value: detail };
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nattribute float fade;\nvarying float vFade;\nvarying vec3 vGroundWorld;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvFade = fade;\nvGroundWorld = (modelMatrix * vec4(transformed, 1.0)).xyz;");
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", "#include <common>\nvarying float vFade;\nvarying vec3 vGroundWorld;\nuniform vec3 uMeadow;\nuniform float uDissolve;\nuniform sampler2D uDetail;")
      .replace("#include <map_fragment>", [
        "#include <map_fragment>",
        // the photograph (10 cm a pixel, taken from a plane in haze) a little livelier, and up close a
        // fine grain of its own so it does not read as a stretched picture
        "float groundLum = dot(diffuseColor.rgb, vec3(0.2126, 0.7152, 0.0722));",
        "diffuseColor.rgb = pow(max(mix(vec3(groundLum), diffuseColor.rgb, 1.18), 0.0), vec3(1.08)) * 1.04;",
        "float near = 1.0 - smoothstep(15.0, 80.0, length(vGroundWorld - cameraPosition));",
        "float g = texture2D(uDetail, vGroundWorld.xz / 1.9).r * 0.6 + texture2D(uDetail, vGroundWorld.xz / 0.47).r * 0.4;",
        "diffuseColor.rgb *= mix(1.0, 0.72 + 0.56 * g, 0.55 * near);",
        // with the far landscape under it, the disc's rim dissolves into it (the same photo, coarser);
        // without, it fades into the meadow's colour
        "if (uDissolve < 0.5) diffuseColor.rgb = mix(diffuseColor.rgb, uMeadow, vFade);",
      ].join("\n"));
  };
  const ground = new THREE.Mesh(geo, groundMat);
  ground.receiveShadow = true;
  ground.name = "Surroundings: ground";

  // ---- the far landscape (the horizon), when the backend fetched it
  const far = meta.far ? await farLandscape(meta, base, get, toScene, R) : null;

  // ---- the neighbours: roofs wear the photo (seen from above: their real colour, panels, windows),
  // walls a render; anything standing on the scene's own ground is the old state of the plot: left out
  const roofPos = [], roofUv = [], wallPos = [], facade = [], wallLook = [];
  let skipped = 0;
  for (const b of buildings.buildings ?? []) {
    const p = b.positions;
    const pts = [];
    for (let k = 0; k < p.length; k += 3) pts.push(toScene(p[k], p[k + 1], p[k + 2]));
    const cx = pts.reduce((s, q) => s + q[0], 0) / pts.length, cz = pts.reduce((s, q) => s + q[2], 0) / pts.length;
    if (own.boxes.some((bx) => distToBox(cx, cz, bx) <= 0)) { skipped++; continue; }
    for (const i of b.roof) {
      roofPos.push(...pts[i]);
      roofUv.push((p[i * 3] + R) / (2 * R), 1 - (p[i * 3 + 2] + R) / (2 * R));
    }
    for (const i of b.wall) wallPos.push(...pts[i]);
    facades(pts, b.roof, b.wall, facade);
    // each house its render (a Swiss palette, picked by its id so it stays) and, for some, shutters
    const hash = [...String(b.id)].reduce((a, ch) => (a * 31 + ch.charCodeAt(0)) >>> 0, 7);
    const tint = RENDERS[hash % RENDERS.length], shutters = (hash >>> 8) % 3 === 0 ? 1 : 0;
    for (let k = 0; k < b.wall.length; k++) wallLook.push(tint.r, tint.g, tint.b, shutters);
  }
  const group = new THREE.Group();
  group.name = "Surroundings";
  group.userData = { kind: "context", excludeFromBounds: true };
  group.add(ground);
  if (far) group.add(far.mesh);
  if (roofPos.length) {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(roofPos, 3));
    g.setAttribute("uv", new THREE.Float32BufferAttribute(roofUv, 2));
    g.computeVertexNormals();
    const roofs = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ map: photo, roughness: 0.85 }));
    roofs.castShadow = roofs.receiveShadow = true;
    roofs.name = "Surroundings: roofs";
    group.add(roofs);
  }
  if (wallPos.length) {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(wallPos, 3));
    g.computeVertexNormals();
    metricUV(g);
    g.setAttribute("facade", new THREE.Float32BufferAttribute(facade, 4));
    g.setAttribute("wallLook", new THREE.Float32BufferAttribute(wallLook, 4));
    // the render the house's walls wear, copied (windows are drawn on the neighbours' copy only)
    const shared = finishMaterial("render", { color: "#ffffff" });
    await finishesReady();
    const mat = shared ? shared.clone() : new THREE.MeshStandardMaterial({ color: "#ffffff", roughness: 0.95 });
    withWindows(mat);
    const walls = new THREE.Mesh(g, mat);
    walls.castShadow = walls.receiveShadow = true;
    walls.name = "Surroundings: walls";
    group.add(walls);
  }
  group.traverse((o) => { if (o.isMesh) o.userData = { ...o.userData, excludeFromBounds: true }; });

  // the scene-frame disc the meadow must lie under, and the real ground's height in it
  const [cx, , cz] = toScene(0, 0, 0);
  const inDisc = (x, z) => { const [lx, lz] = toLocal(x, z); return Math.hypot(lx, lz) <= R; };
  return {
    group,
    meta,
    skipped,
    credits: meta.credits ?? [],
    disc: { x: cx, z: cz, radius: R },
    // the real ground under a scene point (NaN outside the disc)
    heightAt: (x, z) => { if (!inDisc(x, z)) return NaN; const [lx, lz] = toLocal(x, z); return realY(lx, lz); },
    setMeadow: (color) => meadowColor.value.copy(color),
    far: Boolean(far),
    // the far landscape's aerial haze: the colour the sky has at the horizon, and its depth (metres
    // over which the haze takes about two thirds of the light)
    // `color`: the sky at the horizon as the screen shows it (linear display colour)
    setHaze: (color, depth, exposure) => far?.setHaze(color, depth, exposure),
  };
}

/**
 * The relief out to the horizon (geo/far.py): a polar grid of the altitudes around the place (the Earth's
 * curvature already taken off), its two aerial photos. Beyond COMPRESS metres from the camera it is drawn
 * pulled in toward the camera, at the same angle (inside the camera's far plane, with depth precision),
 * and hazed by its real distance.
 */
const COMPRESS = 1500;

// three's ACESFilmicToneMapping and its inverse (the sky dome's), for colours mixed as the screen shows them
const ACES_GLSL = `
  const mat3 ACES_IN = mat3(vec3(0.59719, 0.07600, 0.02840), vec3(0.35458, 0.90834, 0.13383), vec3(0.04823, 0.01566, 0.83777));
  const mat3 ACES_OUT = mat3(vec3(1.60475, -0.10208, -0.00327), vec3(-0.53108, 1.10813, -0.07276), vec3(-0.07367, -0.00605, 1.07602));
  vec3 acesForward(vec3 c) {
    c = ACES_IN * (c / 0.6);
    vec3 a = c * (c + 0.0245786) - 0.000090537, b = c * (0.983729 * c + 0.4329510) + 0.238081;
    return clamp(ACES_OUT * (a / b), 0.0, 1.0);
  }
  vec3 acesInverse(vec3 display) {
    vec3 y = clamp(inverse(ACES_OUT) * clamp(display, 0.0, 0.96), 0.0, 0.99);
    vec3 a = 1.0 - 0.983729 * y, b = 0.0245786 - 0.4329510 * y, c = -0.000090537 - 0.238081 * y;
    vec3 v = (-b + sqrt(max(b * b - 4.0 * a * c, 0.0))) / (2.0 * a);
    return max(inverse(ACES_IN) * v, 0.0) * 0.6;
  }`;
async function farLandscape(meta, base, get, toScene, R) {
  const f = meta.far;
  const [buf, ...photos] = await Promise.all([
    get(f.file).then((r) => r.arrayBuffer()),
    ...f.photos.map((p) => new THREE.TextureLoader().loadAsync(new URL(p.file, base).href)),
  ]);
  for (const t of photos) { t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 16; } // seen at grazing angles
  const h = new Float32Array(buf);
  const radii = f.radii, A = f.azimuths, N = radii.length;
  const positions = new Float32Array(N * A * 3), local = new Float32Array(N * A * 2);
  for (let j = 0; j < N; j++) for (let i = 0; i < A; i++) {
    const a = (i / A) * Math.PI * 2, r = radii[j], k = j * A + i;
    const lx = r * Math.cos(a), lz = r * Math.sin(a);
    // under the disc (and its dissolving rim) it lies a little lower: the disc's ground wins
    const [x, y, z] = toScene(lx, h[k] - (r < R ? 0.4 : 0), lz);
    positions.set([x, y, z], k * 3);
    local.set([lx, lz], k * 2);
  }
  const index = [];
  for (let j = 0; j < N - 1; j++) for (let i = 0; i < A; i++) {
    const a = j * A + i, b = j * A + ((i + 1) % A), c = a + A, d = b + A;
    index.push(a, b, c, b, d, c);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geo.setAttribute("local", new THREE.BufferAttribute(local, 2));
  geo.setIndex(index);
  geo.computeVertexNormals();
  // the haze's colour as the screen shows it (linear, before the output transfer), and the exposure
  const haze = { color: { value: new THREE.Color(0.6, 0.7, 0.85) }, depth: { value: 60000 }, exposure: { value: 1 } };
  const sunView = { value: new THREE.Vector3(0, 1, 0) }, sunWorld = new THREE.Vector3(0, 1, 0);
  const mat = new THREE.MeshStandardMaterial({ roughness: 1, fog: false });
  mat.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, {
      uMid: { value: photos[0] }, uFar: { value: photos[1] },
      uMidExtent: { value: f.photos[0].extent }, uFarExtent: { value: f.photos[1].extent },
      uHaze: haze.color, uHazeDepth: haze.depth, uSunView: sunView, uExposure: haze.exposure,
    });
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nattribute vec2 local;\nvarying vec2 vLocal;\nvarying float vRealDist;\nvarying float vUp;")
      .replace("#include <beginnormal_vertex>", "#include <beginnormal_vertex>\nvUp = normalize(mat3(modelMatrix) * objectNormal).y;")
      .replace("#include <project_vertex>", `
        vLocal = local;
        vec4 wp = modelMatrix * vec4(transformed, 1.0);
        vec3 rel = wp.xyz - cameraPosition;
        float dist = length(rel);
        vRealDist = dist;
        // pulled in toward the camera beyond ${COMPRESS} m, at the same angle, inside the far plane
        float pulled = dist > ${COMPRESS}.0 ? ${COMPRESS}.0 + 420.0 * (1.0 - exp(-(dist - ${COMPRESS}.0) / 20000.0)) : dist;
        wp.xyz = cameraPosition + rel * (pulled / max(dist, 1e-3));
        vec4 mvPosition = viewMatrix * wp;
        gl_Position = projectionMatrix * mvPosition;`);
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", "#include <common>\nuniform sampler2D uMid, uFar;\nuniform float uMidExtent, uFarExtent, uHazeDepth, uExposure;\nuniform vec3 uHaze, uSunView;\nvarying vec2 vLocal;\nvarying float vRealDist;\nvarying float vUp;\n" + ACES_GLSL)
      .replace("#include <map_fragment>", `
        vec2 uvMid = vec2((vLocal.x + uMidExtent) / (2.0 * uMidExtent), 1.0 - (vLocal.y + uMidExtent) / (2.0 * uMidExtent));
        vec2 uvFar = vec2((vLocal.x + uFarExtent) / (2.0 * uFarExtent), 1.0 - (vLocal.y + uFarExtent) / (2.0 * uFarExtent));
        float toFar = smoothstep(0.8, 0.95, max(abs(vLocal.x), abs(vLocal.y)) / uMidExtent);
        vec3 photo = mix(texture2D(uMid, uvMid).rgb, texture2D(uFar, uvFar).rgb, toFar);
        // steep slopes: a photograph taken from above is stretched there; rock in its brightness instead
        float photoLum = dot(photo, vec3(0.2126, 0.7152, 0.0722));
        photo = mix(photo, vec3(0.44, 0.42, 0.39) * (0.55 + 1.6 * photoLum), smoothstep(0.45, 0.8, 1.0 - vUp));
        diffuseColor.rgb *= photo;`)
      .replace("#include <opaque_fragment>", `
        // its own light: the photograph already holds the land's shading, so up close it is shown nearly
        // as it is; farther the sun models the relief so the mountains read. No sky fill: a blue sky
        // over the dark tones of a distant photograph is what turned them ultramarine
        float farK = smoothstep(2000.0, 20000.0, vRealDist);
        float sun = max(dot(normal, uSunView), 0.0);
        outgoingLight = diffuseColor.rgb * (mix(0.62, 0.42, farK) + mix(0.3, 0.85, farK) * sun) * mix(1.35, 0.85, farK);
        #include <opaque_fragment>`)
      .replace("#include <fog_fragment>", `
        // aerial perspective, in the colours the screen will show (the tone mapping applied, the mix made,
        // the tone mapping undone): mixed before it, a blue haze comes out far more vivid than either.
        // The air first turns the land slate blue (a mountain 20 km away), and far off fades it into the
        // sky's colour at the horizon
        float blueK = smoothstep(3000.0, 20000.0, vRealDist);
        float hz = 1.0 - exp(-vRealDist / uHazeDepth);
        vec3 shown = acesForward(gl_FragColor.rgb * uExposure);
        float lum = dot(shown, vec3(0.2126, 0.7152, 0.0722));
        shown = mix(shown, lum * vec3(0.42, 0.66, 1.12), 0.6 * blueK);
        shown = mix(shown, uHaze, hz);
        gl_FragColor.rgb = acesInverse(shown) / uExposure;`);
  };
  const mesh = new THREE.Mesh(geo, mat);
  // the sun's direction in the camera's frame, for the shading above
  mesh.onBeforeRender = (_r, scene, camera) => {
    if (!mesh.userData.sun) scene.traverse((o) => { if (o.isDirectionalLight && o.castShadow) mesh.userData.sun = o; });
    const sunLight = mesh.userData.sun;
    if (sunLight) sunWorld.copy(sunLight.position).sub(sunLight.target.position).normalize();
    sunView.value.copy(sunWorld).transformDirection(camera.matrixWorldInverse);
  };
  mesh.name = "Surroundings: far landscape";
  mesh.frustumCulled = false;
  mesh.receiveShadow = false;
  mesh.userData = { kind: "context", excludeFromBounds: true };
  return {
    mesh,
    setHaze: (color, depth, exposure = 1) => {
      haze.color.value.copy(color);
      if (depth) haze.depth.value = depth;
      haze.exposure.value = exposure;
    },
  };
}

/** A tiling grain (0..1, mean 0.5): smooth value noise, two octaves. */
function grain(n) {
  const rnd = (x, y) => { const s = Math.sin(x * 127.1 + y * 311.7) * 43758.5453; return s - Math.floor(s); };
  const noise = (x, y, cells) => {
    const fx = (x / n) * cells, fy = (y / n) * cells, x0 = Math.floor(fx), y0 = Math.floor(fy);
    const u = fx - x0, v = fy - y0, w = (a) => a * a * (3 - 2 * a);
    const at = (i, j) => rnd(((i % cells) + cells) % cells, ((j % cells) + cells) % cells);
    return (at(x0, y0) * (1 - w(u)) + at(x0 + 1, y0) * w(u)) * (1 - w(v)) + (at(x0, y0 + 1) * (1 - w(u)) + at(x0 + 1, y0 + 1) * w(u)) * w(v);
  };
  const data = new Uint8Array(n * n * 4);
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
    const v = noise(x, y, 16) * 0.6 + noise(x, y, 64) * 0.4;
    data.fill(Math.round(v * 255), (y * n + x) * 4, (y * n + x) * 4 + 3);
    data[(y * n + x) * 4 + 3] = 255;
  }
  const t = new THREE.DataTexture(data, n, n);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.magFilter = THREE.LinearFilter;
  t.minFilter = THREE.LinearMipmapLinearFilter;
  t.generateMipmaps = true;
  t.needsUpdate = true;
  return t;
}

// renders seen on Swiss houses (linear): cream, beige, off-white, light grey, pale yellow, sand
const RENDERS = ["#efe7d6", "#e7dcc6", "#f2efe8", "#dddcd6", "#efe4c2", "#e3d6bd"].map((c) => new THREE.Color(c));

/**
 * Per wall vertex of a building, where it lies on its façade: [distance along the façade from its left
 * end, the façade's length, height above the building's foot, the eaves' height above it; negative
 * length on the longest façade: it gets the door]. A façade = the wall triangles of one plane.
 */
function facades(pts, roof, wall, out) {
  let base = Infinity, top = -Infinity, eave = Infinity;
  for (const q of pts) { base = Math.min(base, q[1]); top = Math.max(top, q[1]); }
  for (const i of roof) eave = Math.min(eave, pts[i][1]);
  if (!Number.isFinite(eave)) eave = top;
  const planes = new Map();
  for (let t = 0; t < wall.length; t += 3) {
    const [a, b, c] = [pts[wall[t]], pts[wall[t + 1]], pts[wall[t + 2]]];
    const ux = b[0] - a[0], uz = b[2] - a[2], vx = c[0] - a[0], vz = c[2] - a[2], vy = c[1] - a[1], uy = b[1] - a[1];
    let nx = uy * vz - uz * vy, nz = ux * vy - uy * vx;
    const l = Math.hypot(nx, nz) || 1; nx /= l; nz /= l;
    const off = a[0] * nx + a[2] * nz;
    const key = `${Math.round(Math.atan2(nz, nx) * 20)}:${Math.round(off * 3)}`;
    if (!planes.has(key)) planes.set(key, { tx: -nz, tz: nx, tris: [] });
    planes.get(key).tris.push(t);
  }
  let longest = null, best = 0;
  for (const f of planes.values()) {
    let lo = Infinity, hi = -Infinity;
    for (const t of f.tris) for (let k = 0; k < 3; k++) {
      const q = pts[wall[t + k]], u = q[0] * f.tx + q[2] * f.tz;
      lo = Math.min(lo, u); hi = Math.max(hi, u);
    }
    Object.assign(f, { lo, len: hi - lo });
    if (f.len > best) { best = f.len; longest = f; }
  }
  const byTri = new Map();
  for (const f of planes.values()) for (const t of f.tris) byTri.set(t, f);
  for (let t = 0; t < wall.length; t += 3) {
    const f = byTri.get(t);
    for (let k = 0; k < 3; k++) {
      const q = pts[wall[t + k]];
      out.push(q[0] * f.tx + q[2] * f.tz - f.lo, f === longest ? -f.len : f.len, q[1] - base, eave - base);
    }
  }
}

/**
 * Generic windows and a door on a render: a row of windows per storey (2.8 m) below the eaves, 1.2 m
 * wide every 3 m along each façade, centred; the door in the middle of the longest façade's ground floor.
 */
function withWindows(mat) {
  mat.onBeforeCompile = (shader) => {
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nattribute vec4 facade;\nattribute vec4 wallLook;\nvarying vec4 vFacade;\nvarying vec4 vWallLook;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvFacade = facade;\nvWallLook = wallLook;");
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", "#include <common>\nvarying vec4 vFacade;\nvarying vec4 vWallLook;\nfloat glassAmount = 0.0;")
      .replace("#include <map_fragment>", `#include <map_fragment>
        {
          float along = vFacade.x, len = abs(vFacade.y), h = vFacade.z, eave = vFacade.w;
          // the house's render, a little darker toward its foot (splashes, the ground's shade)
          diffuseColor.rgb *= vWallLook.rgb * mix(0.8, 1.0, smoothstep(0.0, 0.7, h));
          bool doorFacade = vFacade.y < 0.0;
          float storey = 2.8, pitch = 3.0;
          float n = floor((len - 1.2) / pitch);
          float margin = (len - n * pitch) * 0.5;
          float x = along - margin, cell = mod(x, pitch), slot = floor(x / pitch);
          float floorIndex = floor(h / storey), y = h - floorIndex * storey;
          bool rowFits = floorIndex * storey + 2.25 < eave - 0.15;
          bool inRow = n >= 1.0 && x > 0.0 && x < n * pitch && rowFits;
          float middle = floor(n * 0.5);
          bool doorHere = doorFacade && floorIndex < 0.5 && inRow && slot == middle;
          // the door: 1.0 m wide, 2.1 m high, dark wood with a thin frame
          if (doorHere && abs(cell - 1.5) < 0.5 && y < 2.1) {
            bool frame = abs(cell - 1.5) > 0.44 || y > 2.04;
            diffuseColor.rgb = frame ? vec3(0.85) : vec3(0.22, 0.16, 0.11);
          } else if (inRow && !doorHere && cell > 0.8 && cell < 2.2 && y > 0.8 && y < 2.3) {
            // a window, 1.4 x 1.5 m: a light frame around glass reflecting the sky, split by a mullion
            bool frame = cell < 0.87 || cell > 2.13 || y < 0.87 || y > 2.23 || abs(cell - 1.5) < 0.03;
            diffuseColor.rgb = frame ? vec3(0.9) : vec3(0.1, 0.13, 0.17);
            glassAmount = frame ? 0.0 : 1.0;
          } else if (inRow && !doorHere && vWallLook.a > 0.5 && y > 0.8 && y < 2.3 && ((cell > 0.28 && cell < 0.78) || (cell > 2.22 && cell < 2.72))) {
            // shutters either side, painted, with their slats
            float slat = step(0.5, fract(y * 14.0));
            diffuseColor.rgb = vec3(0.30, 0.38, 0.32) * (0.85 + 0.15 * slat);
          }
        }`)
      .replace("#include <roughnessmap_fragment>", "#include <roughnessmap_fragment>\nroughnessFactor = mix(roughnessFactor, 0.08, glassAmount);");
  };
  mat.customProgramCacheKey = () => "housekit-context-windows";
  return mat;
}

export default { loadContext };
