// Pilot (interior quality, 2026-10-11): Chambre 1 and SDB of Appartement 1 restaged after the
// Clos des Hêtres benchmark with what the kit already has plus a few pieces made here:
// a panelled feature wall, darker layered bedding on an upholstered bed, globe bedside lamps,
// framed photographs, grey-veined marble to 1.2 m, matte black fittings. Daytime rule: no ceiling
// light on, only the bedside lamps glow.
import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import fx from 'housekit/furnish';
import { tileWalls } from 'housekit/interior';

const ASSETS = '/kit/assets/pilot/';
const tex = (file, srgb = true, repeat = [1, 1]) => {
  const t = new THREE.TextureLoader().load(ASSETS + file);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
  t.repeat.set(...repeat);
  t.anisotropy = 8;
  return t;
};
const black = new THREE.MeshStandardMaterial({ color: '#1c1c1c', roughness: 0.45, metalness: 0.35, name: 'matte black' });
const tag = (g, name, footprint, extra = {}) => { g.userData = { kind: 'furniture', name, footprint, ...extra }; return g; };
const box = (g, [w, h, d], [x, y, z], mat) => {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
  m.position.set(x, y + h / 2, z); m.castShadow = m.receiveShadow = true; g.add(m); return m;
};

// ---------------------------------------------------------------- marble tiles
// A 2 x 2 tile canvas cut from the marble photo (each tile its own part of the slab, joints drawn),
// so neighbouring tiles do not repeat; colour graded toward the benchmark's warm grey.
function marbleTiles({ file, size = [0.6, 1.2], joint = 0.002, jointColor = '#8c8a86', grade = (r, g, b) => [r, g, b], roughness = 0.28 }) {
  const mat = new THREE.MeshStandardMaterial({ color: '#ffffff', roughness, name: 'marble tiles' });
  const img = new Image();
  img.src = ASSETS + file;
  const k = 512 / size[0], W = Math.round(2 * size[0] * k), H = Math.round(2 * size[1] * k);
  const canvas = document.createElement('canvas'); canvas.width = W; canvas.height = H;
  const map = new THREE.CanvasTexture(canvas);
  map.colorSpace = THREE.SRGBColorSpace; map.wrapS = map.wrapT = THREE.RepeatWrapping;
  map.repeat.set(1 / (2 * size[0]), 1 / (2 * size[1])); map.anisotropy = 8;
  mat.map = map;
  img.onload = () => {
    const c = canvas.getContext('2d');
    const tw = W / 2, th = H / 2, j = Math.max(1, Math.round(joint * k / 2));
    c.fillStyle = jointColor; c.fillRect(0, 0, W, H);
    // each tile: a different window of the slab photo, some turned
    const cuts = [[0, 0, false], [0.55, 0.1, true], [0.3, 0.05, true], [0.5, 0, false]];
    cuts.forEach(([u, v, turn], i) => {
      const x = (i % 2) * tw, y = Math.floor(i / 2) * th;
      c.save(); c.beginPath(); c.rect(x + j, y + j, tw - 2 * j, th - 2 * j); c.clip();
      const sw = img.width * 0.45, sh = img.height * 0.45;
      if (turn) { c.translate(x + tw, y + th); c.rotate(Math.PI); c.drawImage(img, u * img.width, v * img.height, sw, sh * 2, 0, 0, tw, th); }
      else c.drawImage(img, u * img.width, v * img.height, sw, sh * 2, x, y, tw, th);
      c.restore();
    });
    const px = c.getImageData(0, 0, W, H);
    for (let i = 0; i < px.data.length; i += 4) {
      const [r, g, b] = grade(px.data[i], px.data[i + 1], px.data[i + 2]);
      px.data[i] = r; px.data[i + 1] = g; px.data[i + 2] = b;
    }
    c.putImageData(px, 0, 0);
    map.needsUpdate = true;
  };
  return mat;
}
// Marble017 is near black with dense white veins: lifted to a warm mid grey, veins softened
const greyMarble = () => marbleTiles({
  file: 'Marble017/Marble017_1K-JPG_Color.jpg',
  grade: (r, g, b) => { const y = 0.3 * r + 0.59 * g + 0.11 * b; const v = 88 + y * 0.42; return [v + 2, v, v - 3]; },
});

// ---------------------------------------------------------------- bedroom pieces
/** Vertical boards painted `color`, `height` high along a wall `width` wide, an oak ledge on top. */
function panelledWall(width, { height = 1.25, board = 0.12, color = '#8b8b86' } = {}) {
  const g = new THREE.Group();
  const paint = new THREE.MeshStandardMaterial({ color, roughness: 0.75 });
  const n = Math.floor(width / board), w = width / n;
  for (let i = 0; i < n; i++) box(g, [w - 0.006, height, 0.018], [-width / 2 + w * (i + 0.5), 0, 0.009], paint);
  box(g, [width - 0.006, height, 0.012], [0, 0, 0.003], new THREE.MeshStandardMaterial({ color: '#5d5d59', roughness: 0.9 })); // the groove shade
  box(g, [width, 0.03, 0.11], [0, height, 0.055], fx.finish.oak());
  return tag(g, 'panelled feature wall with oak ledge', [width, 0.11], { height: height + 0.03 });
}

/** A cloth sheet draped over the bed (the project's linen bed, generalised): heights in metres. */
function drape(width, z0, z1, y, mat, { side = 0.17, foot = 0.14, wave = 1 } = {}) {
  const nx = 48, nz = 44, pos = [], uv = [], idx = [];
  for (let j = 0; j <= nz; j++) for (let i = 0; i <= nx; i++) {
    const u = i / nx, v = j / nz, x = (u - 0.5) * width, z = z0 + v * (z1 - z0);
    const s = Math.pow(Math.max(0, (Math.abs(u - 0.5) - 0.425) / 0.075), 1.3);
    const f = Math.pow(Math.max(0, (v - 0.925) / 0.075), 1.3);
    const waves = wave * (0.007 * Math.sin(x * 31 + z * 8) + 0.009 * Math.sin(x * 13 - z * 16));
    pos.push(x, y + waves - side * s - foot * f, z); uv.push(u * width, v * (z1 - z0));
  }
  for (let j = 0; j < nz; j++) for (let i = 0; i < nx; i++) { const a = j * (nx + 1) + i, b = a + nx + 1; idx.push(a, b, a + 1, b, b + 1, a + 1); }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  geo.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2)); geo.setIndex(idx); geo.computeVertexNormals();
  const m = mat.clone(); m.side = THREE.DoubleSide;
  const mesh = new THREE.Mesh(geo, m); mesh.castShadow = mesh.receiveShadow = true; return mesh;
}

/** Upholstered bed (dark fabric frame and tall headboard), white sheet, olive duvet, grey throw, pillows and cushions. */
function layeredBed({ width = 1.6, length = 2.0, frame = '#6a6964', duvet = '#5b5a44', throwColor = '#c4c2bb', cushion = '#7a7a5a' } = {}) {
  const g = new THREE.Group();
  const fabric = fx.finish.fabric(frame);
  const base = new THREE.Mesh(new RoundedBoxGeometry(width + 0.1, 0.32, length + 0.06, 4, 0.03), fabric);
  base.position.set(0, 0.2, 0.0); base.castShadow = base.receiveShadow = true; g.add(base);
  for (const s of [-1, 1]) box(g, [0.04, 0.04, 0.04], [s * (width / 2 - 0.05), 0, length / 2 - 0.05], black); // feet
  const head = new THREE.Mesh(new RoundedBoxGeometry(width + 0.14, 1.05, 0.1, 4, 0.04), fabric);
  head.position.set(0, 0.525, -length / 2 - 0.02); head.castShadow = head.receiveShadow = true; g.add(head);
  const mattress = new THREE.Mesh(new RoundedBoxGeometry(width - 0.02, 0.2, length - 0.04, 4, 0.05), fx.finish.linen('#f7f6f2'));
  mattress.position.set(0, 0.46, 0.0); g.add(mattress);
  g.add(drape(width + 0.14, -length * 0.12, length / 2 + 0.07, 0.585, fx.finish.linen('#f3f2ee'), { side: 0.2, foot: 0.16 })); // sheet turned down
  g.add(drape(width + 0.16, -length * 0.04, length / 2 + 0.08, 0.6, fx.finish.wool(duvet), { side: 0.22, foot: 0.2 }));
  g.add(drape(width + 0.2, length * 0.18, length * 0.42, 0.635, fx.finish.weave(throwColor), { side: 0.12, foot: 0.02, wave: 1.4 }));
  // two sleeping pillows against the headboard, two square ones in front, two olive cushions
  const pillow = (w, h, d, x, y, z, rx, mat) => {
    const p = new THREE.Mesh(new RoundedBoxGeometry(w, h, d, 5, Math.min(h, d) * 0.45), mat);
    p.position.set(x, y, z); p.rotation.x = rx; p.castShadow = p.receiveShadow = true; g.add(p);
  };
  const white = fx.finish.linen('#fbfaf7');
  for (const s of [-1, 1]) pillow(width / 2 - 0.12, 0.17, 0.48, s * width / 4, 0.66, -length / 2 + 0.24, -0.25, white);
  for (const s of [-1, 1]) pillow(0.46, 0.42, 0.13, s * width / 4.2, 0.77, -length / 2 + 0.31, -0.32, white);
  for (const s of [-1, 1]) pillow(0.45, 0.3, 0.13, s * width / 5, 0.74, -length / 2 + 0.43, -0.22, fx.finish.wool(cushion));
  return tag(g, 'upholstered double bed with layered bedding', [width + 0.14, length + 0.16]);
}

/** Opal globe lamp on a small black base (bedside), its light inside the globe. */
function globeLamp({ d = 0.2 } = {}) {
  const g = new THREE.Group();
  box(g, [0.1, 0.03, 0.1], [0, 0, 0], black);
  box(g, [0.018, 0.08, 0.018], [0, 0.03, 0], black);
  const opal = new THREE.MeshStandardMaterial({ color: '#ffffff', emissive: '#ffe9cf', emissiveIntensity: 0.55, roughness: 0.3, name: 'opal glass' });
  const globe = new THREE.Mesh(new THREE.SphereGeometry(d / 2, 32, 20), opal);
  globe.position.y = 0.11 + d / 2; globe.castShadow = false; g.add(globe);
  const l = new THREE.PointLight('#ffd7a6', 0.45, 2.2, 2); l.position.y = 0.11 + d / 2; g.add(l);
  return tag(g, 'opal globe bedside lamp', [d, d], { height: 0.11 + d });
}

/** A photograph in a thin frame with a white mat. */
function framedPhoto(file, { width = 0.5, height = 0.7, frame = 'black', mat = 0.06 } = {}) {
  const g = new THREE.Group();
  const fm = frame === 'oak' ? fx.finish.oak() : black;
  const t = 0.02, d = 0.025;
  box(g, [width, t, d], [0, 0, d / 2], fm); box(g, [width, t, d], [0, height - t, d / 2], fm);
  box(g, [t, height, d], [-width / 2 + t / 2, 0, d / 2], fm); box(g, [t, height, d], [width / 2 - t / 2, 0, d / 2], fm);
  box(g, [width - 2 * t, height - 2 * t, 0.004], [0, t, 0.006], new THREE.MeshStandardMaterial({ color: '#f6f5f1', roughness: 0.9 }));
  const pw = width - 2 * t - 2 * mat, ph = height - 2 * t - 2 * mat;
  const photo = new THREE.Mesh(new THREE.PlaneGeometry(pw, ph), new THREE.MeshStandardMaterial({ map: tex(file), roughness: 0.6 }));
  photo.position.set(0, height / 2, 0.0085); g.add(photo);
  const glass = new THREE.Mesh(new THREE.PlaneGeometry(width - 2 * t, height - 2 * t),
    new THREE.MeshStandardMaterial({ color: '#ffffff', roughness: 0.05, metalness: 0, transparent: true, opacity: 0.08 }));
  glass.position.set(0, height / 2, d - 0.004); g.add(glass);
  return tag(g, 'framed photograph', [width, d], { height });
}

/** A model's cloth in light linen (the Martel curtains' own texture reads near black at full size). */
function relinen(o, color) {
  const mat = fx.finish.linen(color).clone(); mat.side = THREE.DoubleSide;
  o.traverse((m) => { if (m.isMesh) m.material = mat; });
  return o;
}

export async function pilotBedroom(t, r) {
  const pi = Math.PI, Y = t.Y;
  // feature wall: the north wall behind the bed, x 2.105..5.1
  t.put(panelledWall(2.995, { height: 1.25, color: '#8a8a85' }), 3.6025, -4.6);
  t.put(layeredBed({ width: 1.6, length: 2.0 }), 3.64, -3.46);
  for (const x of [2.5, 4.78]) {
    const ns = await t.model('nightstand-round-black', x, -4.3, 0, { width: 0.42, length: 0.42, height: 0.5 });
    t.put(globeLamp(), x, -4.3, 0, ns.userData.height ?? 0.5);
  }
  // photographs on the ledge (leaning) and a large one on the west wall of the bed's bay
  const ledge = 1.25 + 0.03;
  const p1 = framedPhoto('photo-forest.jpg', { width: 0.5, height: 0.68 }); p1.rotation.x = -0.08;
  t.put(p1, 4.1, -4.55, 0, ledge);
  const p2 = framedPhoto('photo-alps.jpg', { width: 0.36, height: 0.48, frame: 'oak' }); p2.rotation.x = -0.08;
  t.put(p2, 3.62, -4.56, 0, ledge);
  await t.model('candle-holder-brass', 2.75, -4.56, 0, { height: 0.18 }, ledge);
  await t.model('vase-dry-branches', 4.75, -4.3, 0, { height: 0.45, width: 0.16 }, 0.5);
  // light curtains to the floor either side of the east window
  for (const z of [-3.88, -1.28]) relinen(await t.model('curtain-grey', 4.96, z, -pi / 2, { width: 0.34, height: 2.4, length: 0.12 }), '#dedcd6');
  t.put(fx.wardrobe({ width: 1.82, depth: 0.60 }), 1.08, -.74, pi);
  t.put(fx.wardrobe({ width: 1.15, depth: 0.55 }), 4.48, -.81, pi);
  await t.model('plant-large', 4.72, -1.95, 0, { width: 0.45, height: 1.2 });
  await t.model('pendant-globe', 3.64, -2.85, 0, {}, 2.45 + 0.28);
}

// ---------------------------------------------------------------- bathroom
/** Free-standing toilet-roll holder in matte black, with its roll and a brush pot. */
function rollStand() {
  const g = new THREE.Group();
  const disc = new THREE.Mesh(new THREE.CylinderGeometry(0.1, 0.1, 0.012, 32), black); disc.position.y = 0.006; g.add(disc);
  const rod = new THREE.Mesh(new THREE.CylinderGeometry(0.008, 0.008, 0.72, 12), black); rod.position.y = 0.36; g.add(rod);
  const arm = new THREE.Mesh(new THREE.TorusGeometry(0.055, 0.006, 8, 20, Math.PI), black);
  arm.position.set(0, 0.62, 0.05); arm.rotation.y = pi2; g.add(arm);
  const roll = new THREE.Mesh(new THREE.CylinderGeometry(0.055, 0.055, 0.1, 24), new THREE.MeshStandardMaterial({ color: '#fbfbf8', roughness: 0.95 }));
  roll.rotation.z = Math.PI / 2; roll.position.set(0, 0.6, 0.06); g.add(roll);
  const pot = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.05, 0.3, 24), new THREE.MeshStandardMaterial({ color: '#efeeea', roughness: 0.35 }));
  pot.position.set(0, 0.162, 0); g.add(pot);
  g.traverse((m) => { if (m.isMesh) m.castShadow = m.receiveShadow = true; });
  return tag(g, 'toilet roll stand and brush', [0.2, 0.2], { height: 0.75 });
}
const pi2 = Math.PI / 2;

/** Every chrome or steel part of `o` in matte black (taps, flush plate, rails). */
function blackFittings(o) {
  o.traverse((m) => { if (m.isMesh && m.material?.metalness > 0.8 && m.material.name !== 'mirror') m.material = black; });
  return o;
}

export async function pilotBath(t, r) {
  const pi = Math.PI;
  const marble = greyMarble();
  // the marble to 1.2 m all round, white paint above (the benchmark's bath)
  const tiles = { size: [0.6, 1.2], color: '#8a8884', jointColor: '#8c8a86', joint: 0.002 };
  const g = tileWalls(t.group, r, { y: t.Y, height: 1.2, full: [], tiles });
  g.traverse((m) => { if (m.isMesh) m.material = marble; });
  t.group.add(g);
  t.put(blackFittings(fx.bathtub({ length: 1.78, width: 0.75 })), 1.065, -4.19);
  t.put(blackFittings(fx.basin({ width: 0.6, depth: 0.42, vanity: false })), 0.345, -3.37, pi2);
  const wc = blackFittings(fx.wc({ tiles }));
  wc.traverse((m) => { if (m.isMesh && m.material?.userData?.finish === 'tiles') m.material = marble; });
  t.put(wc, 0.5, -2.74, pi2);
  // wooden towel ladder leaning on the east wall with a dark towel
  t.put(ladder(), 1.93, -3.3, -pi2);
  t.put(rollStand(), 0.32, -2.32, pi2);
  t.put(fx.bathAccessories({ width: 0.25, tray: '#1c1c1c' }), 0.23, -2.74, pi2, 1.1);
  t.put(fx.rug({ width: 0.8, depth: 0.5, color: '#cfcac0' }), 1.2, -3.54);
  await t.model('plant-small', 1.8, -4.43, 0, { width: 0.22, height: 0.3 }, 0.56);
  t.put(fx.towelStack({ width: 0.3, depth: 0.22, colors: ['#3d3e40', '#cfcac0', '#3d3e40'] }), 1.55, -4.43, 0, 0.56);
}

/** A wooden towel ladder leaning on the wall, a dark towel over a rung. */
function ladder({ height = 1.7, width = 0.45 } = {}) {
  const g = new THREE.Group();
  const wood = new THREE.MeshStandardMaterial({ color: '#7a5233', roughness: 0.6 });
  const lean = 0.12;
  const inner = new THREE.Group(); inner.rotation.x = -Math.atan2(lean, height); inner.position.z = lean; g.add(inner);
  for (const s of [-1, 1]) box(inner, [0.045, height, 0.03], [s * (width / 2 - 0.022), 0, 0], wood);
  for (let i = 1; i <= 4; i++) {
    const rung = new THREE.Mesh(new THREE.CylinderGeometry(0.012, 0.012, width - 0.05, 12), wood);
    rung.rotation.z = Math.PI / 2; rung.position.set(0, i * height / 5, 0); inner.add(rung);
  }
  const towel = fx.finish.towel('#3a3b3d');
  const barY = 3 * height / 5;
  box(inner, [width - 0.12, 0.5, 0.014], [0, barY - 0.5, 0.02], towel);
  box(inner, [width - 0.12, 0.42, 0.014], [0, barY - 0.42, -0.02], towel);
  g.traverse((m) => { if (m.isMesh) m.castShadow = m.receiveShadow = true; });
  return tag(g, 'wooden towel ladder with towel', [width, 0.16], { height });
}
