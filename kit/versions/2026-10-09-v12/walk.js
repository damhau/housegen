// housekit/walk — first-person walk through a scene (?walk=1), loaded by runtime.js only when asked.
//
//   desktop: click to lock the mouse and look like in a game (Esc releases), W A S D / arrows to walk,
//            click while locked to glide to the floor under the crosshair; dragging also looks
//   phone:   drag to look, tap the floor to glide there
// The eye stays `eye` metres above the floor it started on (one storey at a time).
//
// Where one can walk is a grid (5 cm cells) made from ONE top-down render of everything between knee
// and head height on this storey (walls, partitions, door leaves, furniture), cut above the head: a
// cell is walkable when the nearest obstacle is further than the walker's radius. Steps slide along
// obstacles on that grid, and a glide follows the shortest route over it (through the doors).
//
// Motion is the reference tour's (#53, Shapespark's walk viewer): a glide keeps to the middle of
// doors and passages, rounds its corners, speeds up and slows down (slower in sharp corners), looks a
// little ahead along its path and eases out of the heading it starts with and into the one it is
// asked to arrive with; keys speed up and stop over a fraction of a second; the mouse look is smoothed.

import * as THREE from "three";

const CELL = 0.05;
const SKIP = new Set(["terrain", "leaves", "roomFloor", "ceiling"]);
const UP = new THREE.Vector3(0, 1, 0);
const LAYER = 31;

// walk: key speed (m/s, Shift doubles it); accel / decel: seconds from rest to that speed and from it
// to rest; turn: arrow-key turn (rad/s), reached in turnEase s; look: seconds the view takes to catch
// up with the mouse; glide: a glide's top speed, corner: its speed through a hairpin, glideAccel: m/s²
// both ways, grip: m/s² sideways in a curve at most; minGlide: seconds a short glide lasts at least; comfort: metres a glide keeps from
// obstacles where it can (the walker's radius is the least); lookAhead: metres ahead on the path the
// eyes aim at; turnRate: rad/s, the fastest a glide turns the view (leaving the start heading, following the path);
// arrive: metres (at most) over which it turns into the arrival heading; level: seconds to level the
// pitch at the start of a glide
export const MOTION = {
  walk: 1.1, accel: 0.5, decel: 0.17, turn: Math.PI / 2, turnEase: 0.25, look: 0.06,
  glide: 2.2, corner: 1.0, glideAccel: 2, grip: 2, minGlide: 1.5, comfort: 0.5, lookAhead: 1.2, turnRate: 2, arrive: 3.75, level: 0.8,
};
const DS = 0.05; // metres between two samples of a glide's track

const ease = (u) => (1 - Math.cos(Math.PI * Math.min(1, Math.max(0, u)))) / 2;
const wrap = (a) => Math.atan2(Math.sin(a), Math.cos(a));
const yawOf = (dx, dz) => Math.atan2(-dx, -dz); // the heading of a direction (0: north, -z)

/**
 * The eyes' top-down render: white (or red for glass) where the surface seen from above blocks the
 * eyes: the inside of a solid the cut opened (a back face) or a surface at eye height or above
 * (`lo`); black for the top of something lower.
 */
function sightMaterial(glass) {
  return new THREE.ShaderMaterial({
    side: THREE.DoubleSide,
    clipping: true,
    uniforms: { lo: { value: 0 } },
    vertexShader: `
      #include <common>
      #include <clipping_planes_pars_vertex>
      varying float vY;
      void main() {
        vec4 local = vec4(position, 1.0);
        #ifdef USE_INSTANCING
          local = instanceMatrix * local;
        #endif
        vec4 world = modelMatrix * local;
        vY = world.y;
        vec4 mvPosition = viewMatrix * world;
        gl_Position = projectionMatrix * mvPosition;
        #include <clipping_planes_vertex>
      }`,
    fragmentShader: `
      #include <clipping_planes_pars_fragment>
      uniform float lo;
      varying float vY;
      void main() {
        #include <clipping_planes_fragment>
        gl_FragColor = (!gl_FrontFacing || vY >= lo) ? vec4(1.0, ${glass ? "0.0" : "1.0"}, 0.0, 1.0) : vec4(0.0, 0.0, 0.0, 1.0);
      }`,
  });
}

// the top-down renders: what blocks walking; what blocks the eyes, and glass (a window in view)
const PAINT = {
  block: new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide }),
  sight: sightMaterial(false),
  sightGlass: sightMaterial(true),
};

/** Glass: light passes (transmission), or a see-through material, or one named so. */
export function isGlass(material) {
  return (Array.isArray(material) ? material : [material]).some((m) => m && ((m.transmission ?? 0) > 0.2
    || (m.transparent && (m.opacity ?? 1) < 0.85) || /glass|glaz|vitr|verre/i.test(m.name ?? "")));
}

function kindOf(o) {
  for (let a = o; a; a = a.parent) if (a.userData?.kind) return a.userData;
  return {};
}

/**
 * A route (points [x, z]) with its corners rounded, as a track: its points every DS metres.
 * Each corner becomes a quadratic Bézier from the segment before it to the segment after, at most
 * `maxR` metres from the corner and half of either segment, shrunk until every point of the curve
 * passes `ok(x, z)` (a sharp corner when none does). `corners`: where each lies on the track
 * (`from`, `to`, metres from the start), how far it turns (`turn`, radians) and its size `r`.
 */
export function roundCorners(points, ok = () => true, maxR = 1.2) {
  points = simplifyRoute(points, ok);
  const raw = [points[0]];
  const marks = [];
  for (let i = 1; i < points.length - 1; i++) {
    const [p0, p, p1] = [points[i - 1], points[i], points[i + 1]];
    const la = Math.hypot(p[0] - p0[0], p[1] - p0[1]), lb = Math.hypot(p1[0] - p[0], p1[1] - p[1]);
    if (la < 1e-6 || lb < 1e-6) continue;
    const ua = [(p[0] - p0[0]) / la, (p[1] - p0[1]) / la], ub = [(p1[0] - p[0]) / lb, (p1[1] - p[1]) / lb];
    const turn = Math.acos(Math.max(-1, Math.min(1, ua[0] * ub[0] + ua[1] * ub[1])));
    let r = Math.min(maxR, la / 2, lb / 2);
    let curve = null;
    while (turn > 0.01 && r > 0.05) {
      const A = [p[0] - ua[0] * r, p[1] - ua[1] * r], B = [p[0] + ub[0] * r, p[1] + ub[1] * r];
      const k = Math.max(4, Math.ceil((2 * r) / DS));
      const pts = [];
      for (let j = 0; j <= k; j++) {
        const t = j / k, a = (1 - t) * (1 - t), b = 2 * (1 - t) * t, c = t * t;
        pts.push([a * A[0] + b * p[0] + c * B[0], a * A[1] + b * p[1] + c * B[1]]);
      }
      if (pts.every(([x, z]) => ok(x, z))) { curve = pts; break; }
      r *= 0.6;
    }
    const start = raw.length;
    if (curve) raw.push(...curve);
    else raw.push(p);
    marks.push({ start, end: raw.length - 1, turn, r: curve ? r : 0 });
  }
  raw.push(points[points.length - 1]);
  // lengths along the raw points, then a point every DS metres
  const cum = [0];
  for (let i = 1; i < raw.length; i++) cum.push(cum[i - 1] + Math.hypot(raw[i][0] - raw[i - 1][0], raw[i][1] - raw[i - 1][1]));
  const length = cum[cum.length - 1];
  const n = Math.max(2, Math.ceil(length / DS) + 1);
  const pts = [];
  for (let i = 0, j = 0; i < n; i++) {
    const s = (length * i) / (n - 1);
    while (j < raw.length - 2 && cum[j + 1] < s) j++;
    const seg = cum[j + 1] - cum[j], u = seg > 1e-9 ? (s - cum[j]) / seg : 0;
    pts.push([raw[j][0] + (raw[j + 1][0] - raw[j][0]) * u, raw[j][1] + (raw[j + 1][1] - raw[j][1]) * u]);
  }
  const corners = marks.map((m) => ({ from: cum[m.start], to: cum[m.end], turn: m.turn, r: m.r }));
  return { pts, length, corners };
}

/**
 * A route without the waypoints that add nothing: closer than 10 cm to the one kept before it, or
 * turning less than 3° (the straightening leaves short kinks by doors, each a corner to slow down for),
 * when the straight line that replaces them passes `ok(x, z)`.
 */
export function simplifyRoute(points, ok = () => true) {
  const clear = (a, b) => {
    const n = Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1]) / DS);
    for (let k = 1; k < n; k++) if (!ok(a[0] + ((b[0] - a[0]) * k) / n, a[1] + ((b[1] - a[1]) * k) / n)) return false;
    return true;
  };
  if (points.length < 3) return points.slice();
  let pts = points.filter((p, i) => i === 0 || i === points.length - 1 || Math.hypot(p[0] - points[i - 1][0], p[1] - points[i - 1][1]) >= 0.1);
  for (let changed = true; changed && pts.length > 2;) {
    changed = false;
    for (let i = 1; i < pts.length - 1; i++) {
      const [a, p, b] = [pts[i - 1], pts[i], pts[i + 1]];
      const turn = Math.abs(wrap(Math.atan2(b[1] - p[1], b[0] - p[0]) - Math.atan2(p[1] - a[1], p[0] - a[0])));
      if ((turn < 0.05 || Math.hypot(p[0] - a[0], p[1] - a[1]) < 0.1) && clear(a, b)) {
        pts.splice(i, 1);
        changed = true;
        break;
      }
    }
  }
  return pts;
}

/**
 * When a glide along `track` is at each of its points (seconds from the start): speeding up and
 * slowing down at `glideAccel`, to `glide` on the straights, slower in the corners (from `glide` for
 * a slight bend to `corner` for a hairpin, and no more sideways pull than `grip` m/s² on the curve's
 * radius), from `v0` (already moving) to rest; at least `minGlide` seconds in all.
 */
export function glideTimes(track, { v0 = 0, motion = MOTION } = {}) {
  const { pts, length, corners } = track;
  const n = pts.length, ds = length / (n - 1), a = motion.glideAccel;
  const lim = new Float64Array(n).fill(motion.glide);
  for (const c of corners) {
    const byTurn = motion.glide - (motion.glide - motion.corner) * Math.min(1, c.turn / ((2 * Math.PI) / 3));
    // a rounding r metres from a corner turning by `turn` is (about) an arc of radius r / tan(turn / 2)
    const radius = c.r > 0 ? c.r / Math.tan(Math.min(c.turn, 3.1) / 2) : 0;
    const v = Math.min(byTurn, Math.sqrt(motion.grip * Math.max(radius, 0.15)));
    for (let i = Math.floor(c.from / ds); i <= Math.ceil(c.to / ds) && i < n; i++) lim[i] = Math.min(lim[i], v);
  }
  const v = new Float64Array(n);
  v[0] = Math.max(0, v0);
  for (let i = 1; i < n; i++) v[i] = Math.min(lim[i], Math.sqrt(v[i - 1] ** 2 + 2 * a * ds));
  v[n - 1] = 0;
  for (let i = n - 2; i >= 0; i--) v[i] = Math.min(v[i], Math.sqrt(v[i + 1] ** 2 + 2 * a * ds));
  const t = new Float64Array(n);
  for (let i = 1; i < n; i++) {
    const sum = v[i - 1] + v[i];
    t[i] = t[i - 1] + (sum > 1e-6 ? (2 * ds) / sum : Math.sqrt((4 * ds) / a));
  }
  const k = t[n - 1] < motion.minGlide && t[n - 1] > 0 ? motion.minGlide / t[n - 1] : 1;
  if (k !== 1) for (let i = 0; i < n; i++) t[i] *= k;
  return { t, v: v.map((x) => x / k), duration: t[n - 1] };
}

export class Walk {
  constructor({ camera, canvas, root, renderer, onChange, eye = 1.45, speed = MOTION.walk, radius = 0.25 }) {
    Object.assign(this, { camera, canvas, root, renderer, onChange, eye, speed, radius });
    this.keys = new Set();
    this.yaw = 0;
    this.pitch = 0;
    this.aim = { yaw: 0, pitch: 0 }; // where the mouse and the arrow keys want the view: it follows
    this.vel = new THREE.Vector2(); // walking velocity (m/s, x and z)
    this.turnVel = 0; // arrow-key turn (rad/s)
    this.move = null; // the glide under way
    this.path = []; // its corners (debugging)
    this.glide = null; // where it goes (debugging)
    this.ray = new THREE.Raycaster();
    this.clock = new THREE.Clock();
    this.floorY = 0;
    this.#listen();
    this.sync();
  }

  /** Take over from wherever the camera is: its heading, the floor under it, where one can walk. */
  sync() {
    const dir = this.camera.getWorldDirection(new THREE.Vector3());
    this.#look(Math.atan2(-dir.x, -dir.z), Math.asin(THREE.MathUtils.clamp(dir.y, -1, 1)));
    this.move = null;
    this.vel.set(0, 0);
    this.root.updateMatrixWorld(true);
    this.surfaces = [];
    this.root.traverse((o) => {
      const k = kindOf(o).kind;
      if (o.isMesh && o.visible && k !== "terrain" && k !== "leaves") this.surfaces.push(o);
    });
    // the floor: the upward surface under the camera closest to where the feet should be
    const p = this.camera.position;
    const feet = p.y - this.eye;
    this.ray.set(p.clone(), new THREE.Vector3(0, -1, 0));
    this.ray.far = 4;
    const floors = this.ray.intersectObjects(this.surfaces, false).filter((h) => this.#isFloor(h));
    floors.sort((a, b) => Math.abs(a.point.y - feet) - Math.abs(b.point.y - feet));
    this.floorY = floors.length ? floors[0].point.y : feet;
    this.#plans();
    this.#grid();
    this.camera.position.y = this.floorY + this.eye;
    if (!this.free(p.x, p.z)) {
      const spot = this.#nearestFree(p.x, p.z);
      if (spot) this.camera.position.set(spot[0], p.y, spot[1]);
    }
    this.#apply();
  }

  /**
   * Glide to [x, z] along the shortest walkable route (to the nearest walkable spot if it is taken),
   * arriving with the heading `yaw` and the pitch `pitch` when given (else along the last stretch,
   * level). False when there is nowhere to go.
   */
  goTo(x, z, { yaw = null, pitch = 0 } = {}) {
    const p = this.camera.position;
    const route = this.#route([p.x, p.z], [x, z]);
    if (!route?.length || Math.hypot(route[route.length - 1][0] - p.x, route[route.length - 1][1] - p.z) < 0.02) {
      if (yaw !== null) this.#look(yaw, pitch, true); // already there: only turn
      return false;
    }
    const track = roundCorners([[p.x, p.z], ...route], (a, b) => this.free(a, b));
    const times = glideTimes(track, { v0: this.move ? this.move.speed : this.vel.length() });
    // the heading the path itself takes at its start: how far the view has to turn before it follows
    const head = this.#ahead(track, 0) ?? this.yaw;
    const turn = Math.abs(wrap(head - this.yaw));
    // the turn into the arrival heading: over `arrive` metres, more when it is a large turn (no
    // faster than turnRate at the top speed), at most 60 % of the way
    const n = track.pts.length, last = track.pts[n - 1], before = track.pts[Math.max(0, n - 1 - Math.round(1 / DS))];
    const late = yaw === null ? 0 : Math.abs(wrap(yaw - yawOf(last[0] - before[0], last[1] - before[1])));
    const arrive = Math.min(0.6 * track.length, Math.max(MOTION.arrive, (Math.PI * late * MOTION.glide) / (2 * MOTION.turnRate)));
    this.move = { track, times, t: 0, speed: 0, heading: head, y0: this.yaw, p0: this.pitch, yaw, pitch, arrive,
      turnTime: Math.max(0.5, (Math.PI * turn) / (2 * MOTION.turnRate)), free: false };
    this.vel.set(0, 0);
    this.turnVel = 0;
    this.glide = route[route.length - 1];
    this.path = route.slice(0, -1);
    this.onChange?.();
    return true;
  }

  /** True while a glide is under way. */
  get gliding() {
    return this.move !== null;
  }

  /**
   * Jump into a room at its viewpoint (`viewpoint`: where a photographer would stand), level; `given`:
   * the builder's own viewpoint for it ({ at: [x, z], look: [x, z] }).
   */
  jumpTo(room, given = null) {
    const v = this.viewpoint(room, given);
    this.path = [];
    this.glide = null;
    this.move = null;
    this.vel.set(0, 0);
    if (v) {
      this.camera.position.set(v.at[0], this.floorY + this.eye, v.at[1]);
      this.#look(v.yaw, 0);
      const r = (x) => Math.round(x * 100) / 100;
      this.lastView = { room: room.name, at: v.at.map(r), yaw: r(v.yaw), near: r(v.near), own: r(v.own), window: r(v.window), cover: r(v.cover), set: v.set };
      this.#apply();
    } else {
      // nowhere free in or at the door of the room: its centre, facing its longest view
      const poly = room.polygon;
      let cx = 0, cz = 0;
      for (const [x, z] of poly) { cx += x / poly.length; cz += z / poly.length; }
      const spot = this.#nearestFree(cx, cz, poly) ?? this.#nearestFree(cx, cz);
      if (spot) this.camera.position.set(spot[0], this.floorY + this.eye, spot[1]);
      this.lastView = { room: room.name, set: false, fallback: true };
      this.faceOpenView(poly);
    }
    this.onChange?.();
  }

  /**
   * Where a photographer would stand in `room` (#54): from a corner or the doorway, level, across the
   * room toward its windows. Among the free spots of the room (40 cm from anything) and those just
   * outside its doors, every 25 cm, and 32 headings each: the view (a fan of level rays at eye height
   * over the sight map, as wide as the camera's) that shows most of the room's floor, with nothing
   * nearer than 1 m (less in a small room) filling it, ending on the room's own walls and windows rather than the next room,
   * a window in it. `given` ({ at, look }: the builder's own) is taken as it is, and judged the same.
   * Returns { at: [x, z], yaw, near, own, window (shares of the view), cover (of the room's floor),
   * set } or null when nowhere fits.
   */
  viewpoint(room, given = null) {
    const aspect = this.camera.aspect || 1.6;
    const key = `${room.name}|${aspect.toFixed(1)}|${given ? given.at.join() + given.look.join() : ""}`;
    if (this.views.has(key)) return this.views.get(key);
    const poly = room.polygon;
    const half = Math.atan(Math.tan(THREE.MathUtils.degToRad(this.camera.fov) / 2) * aspect);
    const mask = this.#roomMask([poly]);
    // the view may end beyond the room where nothing separates it from the next (an open-plan
    // kitchen-living): that is still the room's own view, not the next room through a door
    const own = this.#roomMask([poly, ...this.#openTo(room).map((r) => r.polygon)]);
    const area = Math.max(1, room.area ?? polygonArea(poly));
    // "too near": 1 m in a room, less in a WC or a shower room (from anywhere in it, its walls are)
    const nearD = Math.min(1, Math.max(0.5, 0.45 * Math.sqrt(area)));
    const R = 180, dA = (2 * Math.PI) / R;
    const fan = (x, z) => {
      const rays = [];
      for (let k = 0; k < R; k++) rays.push(this.#cast(x, z, -Math.sin(k * dA), -Math.cos(k * dA), mask, own));
      return rays;
    };
    const judge = (rays, yaw) => {
      let n = 0, near = 0, own = 0, win = 0, cover = 0;
      const ds = [];
      for (let k = 0; k < R; k++) {
        if (Math.abs(wrap(k * dA - yaw)) > half) continue;
        const [d, hit, inRoom, mine] = rays[k];
        n++;
        if (d < nearD) near++;
        if (mine) own++;
        if (mine && hit === 2) win++;
        cover += 0.5 * inRoom * inRoom * dA;
        ds.push(d);
      }
      ds.sort((a, b) => a - b);
      const v = { near: near / n, own: own / n, window: win / n, cover: Math.min(1, cover / area), depth: ds[ds.length >> 1] ?? 0 };
      v.score = v.cover + (v.window > 0.03 ? 0.25 : 0) + 0.15 * Math.min(1, v.depth / 5)
        - 3 * Math.max(0, v.near - 0.05) - 2 * Math.max(0, 0.88 - v.own);
      return v;
    };
    let best = null;
    if (given) {
      const [x, z] = given.at;
      const yaw = yawOf(given.look[0] - x, given.look[1] - z);
      best = { at: [x, z], yaw, ...judge(fan(x, z), yaw), set: true };
    } else {
      // candidates: free spots of the room, spots just outside its doors, and the door thresholds
      // themselves (from where a small room is shot: the jambs fall outside the frame)
      const cands = [];
      const doors = this.#doorSides(poly);
      const [xs, zs] = [poly.map((q) => q[0]), poly.map((q) => q[1])];
      for (const minClear of [0.4, this.radius]) {
        for (let x = Math.min(...xs) - 0.775; x < Math.max(...xs) + 0.8; x += 0.25) {
          for (let z = Math.min(...zs) - 0.775; z < Math.max(...zs) + 0.8; z += 0.25) {
            const c = this.#clearance(x, z), inRoom = inside(poly, x, z);
            if (inRoom ? c >= minClear : c >= this.radius && doors.some(({ out: [a, b] }) => Math.hypot(a - x, b - z) < 0.75)) cands.push([x, z, inRoom]);
          }
        }
        for (const { out: [ax, az], in: [bx, bz] } of doors) {
          for (let t = 0.2; t <= 0.61; t += 0.1) {
            const x = ax + (bx - ax) * t, z = az + (bz - az) * t;
            if (this.#clearance(x, z) >= 0.2) cands.push([x, z, inside(poly, x, z)]);
          }
        }
        if (cands.some(([, , inRoom]) => inRoom)) break; // else a tiny room: closer to things rather than nowhere
      }
      for (const [x, z, inRoom] of cands) {
        const rays = fan(x, z);
        for (let h = 0; h < 32; h++) {
          const yaw = wrap((h * 2 * Math.PI) / 32);
          const v = judge(rays, yaw);
          const score = v.score - (inRoom ? 0 : 0.05); // a little in favour of standing in the room
          if (!best || score > best.score) best = { at: [x, z], yaw, ...v, score, set: false };
        }
      }
    }
    this.views.set(key, best);
    return best;
  }

  /**
   * One level ray at eye height over the sight map: [distance, hit (0 nothing, 1 wall/furniture,
   * 2 glass), metres in the room (`mask`), ends on the room's own side (`own`: the room and the rooms
   * open to it)].
   */
  #cast(x, z, dx, dz, mask, own = mask) {
    let d = 0, inRoom = 0, last = -1, hit = 0;
    for (; d < 15; d += CELL) {
      const i = Math.floor((x + dx * d - this.gx0) / CELL), j = Math.floor((z + dz * d - this.gz0) / CELL);
      if (i < 0 || j < 0 || i >= this.nx || j >= this.nz) { hit = 1; break; }
      const k = j * this.nx + i;
      if (this.sight[k]) { hit = this.sight[k]; break; }
      if (mask[k]) inRoom += CELL;
      if (own[k]) last = d;
    }
    // ends on the room's own side: the hit is just past its last cell (a pane may sit 40 cm into a thick wall)
    return [d, hit, inRoom, hit !== 0 && last >= d - (hit === 2 ? 0.6 : 0.3)];
  }

  /** The grid cells inside any of the polygons. */
  #roomMask(polys) {
    const m = new Uint8Array(this.nx * this.nz);
    for (const poly of polys) {
      const [xs, zs] = [poly.map((q) => q[0]), poly.map((q) => q[1])];
      const [i0, j0] = this.#cell(Math.min(...xs), Math.min(...zs)), [i1, j1] = this.#cell(Math.max(...xs), Math.max(...zs));
      for (let j = Math.max(0, j0); j <= Math.min(this.nz - 1, j1); j++) {
        for (let i = Math.max(0, i0); i <= Math.min(this.nx - 1, i1); i++) {
          if (inside(poly, this.gx0 + (i + 0.5) * CELL, this.gz0 + (j + 0.5) * CELL)) m[j * this.nx + i] = 1;
        }
      }
    }
    return m;
  }

  /**
   * The rooms of this storey open to `room`: along at least 60 cm of its edge the other room is
   * across (within 20 cm) with no partition in between (an open-plan kitchen-living, a dining area).
   */
  #openTo(room) {
    const P = room.polygon;
    return this.rooms.filter((other) => {
      if (other.name === room.name) return false;
      let open = 0;
      for (let i = 0; i < P.length; i++) {
        const a = P[i], b = P[(i + 1) % P.length], L = Math.hypot(b[0] - a[0], b[1] - a[1]);
        for (let t = 0.05; t < L; t += 0.1) {
          const q = [a[0] + ((b[0] - a[0]) * t) / L, a[1] + ((b[1] - a[1]) * t) / L];
          if (!nearPolygon(other.polygon, q, 0.2)) continue;
          if (this.partitions.some((w) => segmentDistance(q, w.from, w.to) < (w.thickness ?? 0.1) / 2 + 0.12)) continue;
          open += 0.1;
        }
      }
      return open >= 0.6;
    });
  }

  /** Each door of the room: the point 45 cm outside it and the one 45 cm inside (where one stands in the doorway, looking in). */
  #doorSides(poly) {
    const out = [];
    for (let i = 0; i + 1 < this.doorways.length; i += 2) {
      const [a, b] = [this.doorways[i], this.doorways[i + 1]];
      const ia = inside(poly, a[0], a[1]), ib = inside(poly, b[0], b[1]);
      if (ia !== ib) out.push(ia ? { out: b, in: a } : { out: a, in: b });
    }
    return out;
  }

  /**
   * Turn to the longest open view from where the walker stands (level), not to the nearest wall; with
   * a room polygon, the longest view that stays in that room (the room itself, not the next door).
   */
  faceOpenView(poly = null) {
    const p = this.camera.position;
    this.#look(this.#longestView(p.x, p.z, poly, 32)[1], 0);
    this.#apply();
    this.onChange?.();
  }

  /** The longest level line of sight from (x, z) (in the room `poly` when given): [metres, yaw]. */
  #longestView(x, z, poly = null, dirs = 16) {
    let best = 0, yaw = this.yaw;
    for (let k = 0; k < dirs; k++) {
      const a = (k / dirs) * 2 * Math.PI;
      const dx = -Math.sin(a), dz = -Math.cos(a);
      let d = 0;
      while (d < 15 && this.#clearance(x + dx * d, z + dz * d) > 0.05 && (!poly || inside(poly, x + dx * d, z + dz * d))) d += CELL * 2;
      if (d > best + 1e-6) { best = d; yaw = a; }
    }
    return [best, yaw];
  }

  /** Can the walker stand at (x, z)? */
  free(x, z) {
    return this.#clearance(x, z) >= this.radius;
  }

  /** Door openings of the floor plans with a side where nobody can stand: [x, z] of that side. */
  blockedDoorways() {
    return this.doorways.filter(([x, z]) => !this.free(x, z));
  }

  /** The same, with the partition and opening each blocked side belongs to. */
  blockedDoors() {
    return this.doorways
      .map((p, i) => ({ at: p, ...this.doorwayOf[i] }))
      .filter(({ at: [x, z] }) => !this.free(x, z));
  }

  /**
   * Which rooms one can walk between: groups of room names, one per connected walkable area (0.2 m² or
   * more). A room split by furniture appears in two groups; a room nobody can stand in appears in none.
   */
  reach() {
    const { nx, nz } = this;
    const label = new Int32Array(nx * nz).fill(-1);
    const groups = [];
    const walk = (k) => this.clear[k] * CELL >= this.radius;
    for (let k0 = 0; k0 < nx * nz; k0++) {
      if (label[k0] >= 0 || !walk(k0)) continue;
      const id = groups.length, names = new Set(), stack = [k0];
      let cells = 0;
      label[k0] = id;
      while (stack.length) {
        const k = stack.pop(), i = k % nx, j = Math.floor(k / nx);
        cells++;
        const x = this.gx0 + (i + 0.5) * CELL, z = this.gz0 + (j + 0.5) * CELL;
        for (const r of this.rooms) if (inside(r.polygon, x, z)) names.add(r.name);
        for (const [a, b] of [[i + 1, j], [i - 1, j], [i, j + 1], [i, j - 1]]) {
          if (a < 0 || b < 0 || a >= nx || b >= nz) continue;
          const n = b * nx + a;
          if (label[n] < 0 && walk(n)) { label[n] = id; stack.push(n); }
        }
      }
      // a patch smaller than 0.2 m² (a corner boxed in by a plant) is not an area anyone walks to
      if (names.size && cells * CELL * CELL >= 0.2) groups.push([...names]);
    }
    return groups;
  }

  /** Advance one frame (or `step` seconds); true when the camera moved or turned. */
  update(step) {
    const dt = step ?? Math.min(0.1, this.clock.getDelta());
    let changed = this.dirty === true;
    this.dirty = false;
    const want = this.#keyVelocity();
    if (this.move && want.lengthSq() > 0) this.#stopGlide(); // the keys take over, at the glide's speed
    if (this.move) changed = this.#glideStep(dt) || changed;
    else changed = this.#keyStep(dt, want) || changed;
    changed = this.#turnStep(dt) || changed;
    changed = this.#lookStep(dt) || changed;
    if (changed) this.#apply();
    return changed;
  }

  /** Stop a glide where it is, keeping its velocity for the keys. */
  #stopGlide() {
    const m = this.move;
    if (!m) return;
    this.vel.copy(m.velocity ?? new THREE.Vector2());
    this.aim.yaw = this.yaw;
    this.aim.pitch = this.pitch;
    this.move = null;
    this.glide = null;
    this.path = [];
  }

  /** One frame of a glide: where it is at its time on the track, and where it looks. */
  #glideStep(dt) {
    const m = this.move;
    const { pts, length } = m.track;
    const { t, v, duration } = m.times;
    m.t = Math.min(duration, m.t + dt);
    // the track point at this time (binary search, then linear between two points 5 cm apart)
    let lo = 0, hi = pts.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (t[mid] <= m.t) lo = mid;
      else hi = mid;
    }
    // within the 5 cm step, at a constant acceleration from its start speed to its end speed (a
    // constant speed per step would creep through the first one, 0.2 s long, then jump)
    const span = t[hi] - t[lo], tau = Math.min(span, m.t - t[lo]), mean = (v[lo] + v[hi]) / 2;
    const u = span <= 1e-9 ? 1 : mean > 1e-9 ? Math.min(1, (v[lo] * tau + (0.5 * (v[hi] - v[lo]) * tau * tau) / span) / (mean * span)) : tau / span;
    const x = pts[lo][0] + (pts[hi][0] - pts[lo][0]) * u, z = pts[lo][1] + (pts[hi][1] - pts[lo][1]) * u;
    const p = this.camera.position;
    m.velocity = new THREE.Vector2(x - p.x, z - p.z).divideScalar(Math.max(dt, 1e-3));
    m.speed = m.velocity.length();
    p.x = x;
    p.z = z;
    if (!m.free) {
      const s = ((lo + u) / (pts.length - 1)) * length;
      // eyes a little ahead along the path (none in the last centimetres: keep the last heading)
      const head = this.#ahead(m.track, lo);
      if (head !== null) {
        // no faster than turnRate: at a door the point ahead swings round the frame
        const max = MOTION.turnRate * dt;
        m.heading += Math.max(-max, Math.min(max, wrap(head - m.heading)));
      }
      // out of the start heading (no faster than turnRate), then along the path, then into the arrival heading
      let yaw = m.y0 + wrap(m.heading - m.y0) * ease(m.t / m.turnTime);
      const w = m.arrive > 0 ? ease((s - (length - m.arrive)) / m.arrive) : 1;
      if (m.yaw !== null) yaw += wrap(m.yaw - yaw) * w;
      // level at the start, the arrival pitch at the end
      const pitch = m.p0 * (1 - ease(m.t / MOTION.level)) * (1 - w) + m.pitch * w;
      // the view follows that, no faster than turnRate (+20 %): leaving the start heading while the
      // path itself turns would add up to twice that
      const maxYaw = 1.2 * MOTION.turnRate * dt, maxPitch = 0.6 * MOTION.turnRate * dt;
      this.yaw += Math.max(-maxYaw, Math.min(maxYaw, wrap(yaw - this.yaw)));
      this.pitch += Math.max(-maxPitch, Math.min(maxPitch, pitch - this.pitch));
      this.aim.yaw = this.yaw;
      this.aim.pitch = this.pitch;
      m.settled = Math.abs(wrap(yaw - this.yaw)) < 1e-3 && Math.abs(pitch - this.pitch) < 1e-3;
    }
    if (m.t >= duration && (m.free || m.settled)) {
      this.move = null;
      this.glide = null;
      this.path = [];
      this.vel.set(0, 0);
    }
    return true;
  }

  /** The heading toward the track point lookAhead metres after point `i` (null in the last 25 cm). */
  #ahead(track, i) {
    const { pts, length } = track;
    const j = Math.min(pts.length - 1, i + Math.round((MOTION.lookAhead * (pts.length - 1)) / Math.max(length, 1e-6)));
    const dx = pts[j][0] - pts[i][0], dz = pts[j][1] - pts[i][1];
    return Math.hypot(dx, dz) > 0.25 ? yawOf(dx, dz) : null;
  }

  /** The velocity the keys ask for (m/s, x and z), relative to the heading; Shift runs. */
  #keyVelocity() {
    const f = new THREE.Vector2(-Math.sin(this.yaw), -Math.cos(this.yaw));
    const r = new THREE.Vector2(-f.y, f.x);
    const v = new THREE.Vector2();
    if (this.keys.has("w") || this.keys.has("arrowup")) v.add(f);
    if (this.keys.has("s") || this.keys.has("arrowdown")) v.sub(f);
    if (this.keys.has("d")) v.add(r);
    if (this.keys.has("a")) v.sub(r);
    if (v.lengthSq() > 0) v.normalize().multiplyScalar(this.speed * (this.keys.has("shift") ? 2 : 1));
    return v;
  }

  /** Walking on the keys: toward the wanted velocity in `accel` seconds, to rest in `decel`; slides along obstacles. */
  #keyStep(dt, want) {
    const speedingUp = want.lengthSq() > 0 && want.dot(this.vel) >= 0 && want.length() >= this.vel.length() - 1e-9;
    const rate = speedingUp ? want.length() / MOTION.accel : this.speed / MOTION.decel;
    const dv = want.clone().sub(this.vel);
    if (dv.length() > rate * dt) dv.setLength(rate * dt);
    this.vel.add(dv);
    if (this.vel.lengthSq() < 1e-6) {
      this.vel.set(0, 0);
      return false;
    }
    const done = this.#step(this.vel.x * dt, this.vel.y * dt);
    if (!done) {
      this.vel.set(0, 0);
      return false;
    }
    if (done[0] === 0) this.vel.x = 0; // slid along an obstacle: that way is blocked
    if (done[1] === 0) this.vel.y = 0;
    return true;
  }

  /** Arrow keys: turning at `turn` rad/s, reached and left in `turnEase` seconds. */
  #turnStep(dt) {
    const want = ((this.keys.has("arrowleft") ? 1 : 0) - (this.keys.has("arrowright") ? 1 : 0)) * MOTION.turn;
    const rate = (MOTION.turn / MOTION.turnEase) * dt;
    this.turnVel += Math.max(-rate, Math.min(rate, want - this.turnVel));
    if (Math.abs(this.turnVel) < 1e-4) {
      this.turnVel = 0;
      return false;
    }
    if (this.move) this.move.free = true; // turning during a glide takes the view over
    this.aim.yaw += this.turnVel * dt;
    return true;
  }

  /** The view following the aim (mouse, arrow keys): smoothed over `look` seconds. */
  #lookStep(dt) {
    if (this.move && !this.move.free) return false; // the glide drives the view
    const dy = wrap(this.aim.yaw - this.yaw), dp = this.aim.pitch - this.pitch;
    if (Math.abs(dy) < 1e-4 && Math.abs(dp) < 1e-4) {
      if (dy === 0 && dp === 0) return false;
      this.yaw = this.aim.yaw;
      this.pitch = this.aim.pitch;
      return true;
    }
    const k = 1 - Math.exp(-dt / MOTION.look);
    this.yaw += dy * k;
    this.pitch += dp * k;
    return true;
  }

  /** Set the view (and its aim) at once; `turn`: ease there instead (the smoothing does it). */
  #look(yaw, pitch, turn = false) {
    this.aim.yaw = yaw;
    this.aim.pitch = pitch;
    if (turn) {
      this.dirty = true;
      return;
    }
    this.yaw = yaw;
    this.pitch = pitch;
  }

  dispose() {
    for (const [t, el, fn] of this.handlers) el.removeEventListener(t, fn);
    this.ui?.cross.remove();
    this.ui?.line.remove();
  }

  // ------------------------------------------------------------------------
  // The walkable grid
  // ------------------------------------------------------------------------

  /** Rooms and door openings of the floor plans on this storey. */
  #plans() {
    this.rooms = [];
    this.doorways = [];
    this.doorwayOf = []; // the partition opening of each doorway point
    this.partitions = [];
    this.root.traverse((o) => {
      if (o.userData?.kind !== "floorPlan" || Math.abs(o.userData.y - this.floorY) > 0.5) return;
      this.rooms.push(...o.userData.rooms);
      for (const w of o.userData.partitions) {
        this.partitions.push(w);
        const len = Math.hypot(w.to[0] - w.from[0], w.to[1] - w.from[1]);
        const ux = (w.to[0] - w.from[0]) / len, uz = (w.to[1] - w.from[1]) / len;
        for (const op of w.openings) {
          const c = op.offset + op.width / 2;
          for (const s of [-1, 1]) {
            this.doorways.push([w.from[0] + ux * c - uz * s * 0.45, w.from[1] + uz * c + ux * s * 0.45]);
            this.doorwayOf.push({ from: w.from, to: w.to, offset: op.offset, width: op.width });
          }
        }
      }
    });
  }

  /** Render the obstacles of this storey from above into a grid of clearances. */
  #grid() {
    const lo = this.floorY + 0.2, hi = this.floorY + 1.9;
    const obstacles = [];
    const box = new THREE.Box3(), tmp = new THREE.Box3();
    for (const o of this.surfaces) {
      const k = kindOf(o);
      if (SKIP.has(k.kind) || k.flat || k.hang) continue;
      tmp.setFromObject(o);
      if (tmp.max.y > lo && tmp.min.y < hi) obstacles.push(o);
    }
    // the area: the rooms of this storey (+1 m), else 80 m around the camera
    if (this.rooms.length) {
      for (const r of this.rooms) for (const [x, z] of r.polygon) box.expandByPoint(new THREE.Vector3(x, 0, z));
      box.expandByScalar(1);
    } else {
      const c = this.camera.position;
      box.set(new THREE.Vector3(c.x - 40, 0, c.z - 40), new THREE.Vector3(c.x + 40, 0, c.z + 40));
    }
    const x0 = box.min.x, z0 = box.min.z;
    const nx = Math.ceil((box.max.x - x0) / CELL), nz = Math.ceil((box.max.z - z0) / CELL);
    Object.assign(this, { gx0: x0, gz0: z0, nx, nz });

    const px = this.#topView(obstacles, hi);

    // what the eyes see past (#54, the room viewpoints): everything at eye height (walls, door
    // leaves, wardrobes; not a table or a sofa), glass apart (a window in view is not a wall)
    const eyeLo = this.floorY + 1.15, eyeHi = this.floorY + 1.8;
    const seen = [];
    for (const o of this.surfaces) {
      const k = kindOf(o);
      if (SKIP.has(k.kind) || k.flat || k.hang) continue;
      tmp.setFromObject(o);
      if (tmp.max.y > eyeLo && tmp.min.y < eyeHi) seen.push(o);
    }
    // cut above the eyes; what is then seen from above blocks the eyes when it is the inside of a cut
    // solid (a wall, a wardrobe) or a surface at eye height, not when it is the top of something
    // lower (a table, a sofa, the parapet under a window of a wall made as one mesh with a hole)
    PAINT.sight.uniforms.lo.value = PAINT.sightGlass.uniforms.lo.value = eyeLo;
    const eye = this.#topView(seen, eyeHi, (o) => (isGlass(o.material) ? PAINT.sightGlass : PAINT.sight));
    this.sight = new Uint8Array(nx * nz);
    for (let j = 0; j < nz; j++) for (let i = 0; i < nx; i++) {
      const q = ((nz - 1 - j) * nx + i) * 4;
      this.sight[j * nx + i] = eye[q] > 127 ? (eye[q + 1] > 127 ? 1 : 2) : 0;
    }
    // a pane is a plane (or 1 cm thick): edge-on from above it covers no pixel. Its cells from its
    // volume instead: points through its bounding box, those at eye height
    const p = new THREE.Vector3(), size = new THREE.Vector3();
    for (const o of seen) {
      if (!o.geometry || !isGlass(o.material)) continue;
      if (!o.geometry.boundingBox) o.geometry.computeBoundingBox();
      const b = o.geometry.boundingBox;
      b.getSize(size);
      const n = [size.x, size.y, size.z].map((v) => Math.max(1, Math.min(60, Math.ceil(v / 0.03))));
      for (let a = 0; a < n[0]; a++) for (let c = 0; c < n[1]; c++) for (let e = 0; e < n[2]; e++) {
        p.set(b.min.x + (size.x * (a + 0.5)) / n[0], b.min.y + (size.y * (c + 0.5)) / n[1], b.min.z + (size.z * (e + 0.5)) / n[2]).applyMatrix4(o.matrixWorld);
        if (p.y < eyeLo || p.y > eyeHi) continue;
        const [i, j] = this.#cell(p.x, p.z);
        if (i >= 0 && j >= 0 && i < nx && j < nz && this.sight[j * nx + i] !== 1) this.sight[j * nx + i] = 2;
      }
    }
    this.views = new Map();

    // with floor plans, only the rooms are walkable: outside them counts as occupied, the rooms widened
    // by 15 cm so a door opening (a gap in a partition between two rooms) still joins them
    const inRooms = (x, z) => this.rooms.some((r) => inside(r.polygon, x, z));
    const roomCell = (i, j) => {
      if (!this.rooms.length) return true;
      const x = x0 + (i + 0.5) * CELL, z = z0 + (j + 0.5) * CELL;
      if (inRooms(x, z)) return true;
      for (const [ox, oz] of [[0.15, 0], [-0.15, 0], [0, 0.15], [0, -0.15]]) if (inRooms(x + ox, z + oz)) return true;
      return false;
    };
    // clearance in cells: chamfer distance to the nearest occupied cell (pixel row 0 is the south edge)
    const INF = 1e9, D = new Float32Array(nx * nz);
    for (let j = 0; j < nz; j++) for (let i = 0; i < nx; i++) {
      D[j * nx + i] = px[((nz - 1 - j) * nx + i) * 4] > 127 || !roomCell(i, j) ? 0 : INF;
    }
    const S2 = Math.SQRT2;
    for (let j = 0; j < nz; j++) for (let i = 0; i < nx; i++) {
      let d = D[j * nx + i];
      if (i > 0) d = Math.min(d, D[j * nx + i - 1] + 1);
      if (j > 0) {
        d = Math.min(d, D[(j - 1) * nx + i] + 1);
        if (i > 0) d = Math.min(d, D[(j - 1) * nx + i - 1] + S2);
        if (i < nx - 1) d = Math.min(d, D[(j - 1) * nx + i + 1] + S2);
      }
      D[j * nx + i] = d;
    }
    for (let j = nz - 1; j >= 0; j--) for (let i = nx - 1; i >= 0; i--) {
      let d = D[j * nx + i];
      if (i < nx - 1) d = Math.min(d, D[j * nx + i + 1] + 1);
      if (j < nz - 1) {
        d = Math.min(d, D[(j + 1) * nx + i] + 1);
        if (i < nx - 1) d = Math.min(d, D[(j + 1) * nx + i + 1] + S2);
        if (i > 0) d = Math.min(d, D[(j + 1) * nx + i - 1] + S2);
      }
      D[j * nx + i] = d;
    }
    this.clear = D;
  }

  /**
   * `objs` seen from straight above over the grid, cut at height `cut` (looking down into a cut wall
   * shows its inside, so the wall still covers its cells): RGBA pixels, row 0 the south edge. White
   * by default; `paint(o)` gives each object its own material instead.
   */
  #topView(objs, cut, paint = null) {
    const { gx0: x0, gz0: z0, nx, nz } = this;
    const scene = this.root.parent ?? this.root;
    const cam = new THREE.OrthographicCamera(x0, x0 + nx * CELL, -z0, -(z0 + nz * CELL), 0.1, 200);
    cam.up.set(0, 0, -1);
    cam.position.set(0, cut + 50, 0);
    cam.lookAt(0, 0, 0);
    cam.layers.set(LAYER);
    const layered = objs.map((o) => { const had = o.layers.isEnabled(LAYER); o.layers.enable(LAYER); return [o, had]; });
    const painted = paint ? objs.map((o) => { const m = o.material; o.material = paint(o); return [o, m]; }) : [];
    const target = new THREE.WebGLRenderTarget(nx, nz);
    const r = this.renderer;
    const saved = { target: r.getRenderTarget(), planes: r.clippingPlanes, color: r.getClearColor(new THREE.Color()), alpha: r.getClearAlpha(),
      override: scene.overrideMaterial, background: scene.background, fog: scene.fog };
    scene.overrideMaterial = paint ? null : PAINT.block;
    scene.background = null;
    scene.fog = null;
    r.clippingPlanes = [new THREE.Plane(new THREE.Vector3(0, -1, 0), cut)];
    r.setRenderTarget(target);
    r.setClearColor(0x000000, 1);
    r.clear();
    r.render(scene, cam);
    const px = new Uint8Array(nx * nz * 4);
    r.readRenderTargetPixels(target, 0, 0, nx, nz, px);
    r.setRenderTarget(saved.target);
    r.clippingPlanes = saved.planes;
    r.setClearColor(saved.color, saved.alpha);
    Object.assign(scene, { overrideMaterial: saved.override, background: saved.background, fog: saved.fog });
    for (const [o, m] of painted) o.material = m;
    for (const [o, had] of layered) if (!had) o.layers.disable(LAYER);
    target.dispose();
    return px;
  }

  #cell(x, z) {
    return [Math.floor((x - this.gx0) / CELL), Math.floor((z - this.gz0) / CELL)];
  }

  #clearance(x, z) {
    const [i, j] = this.#cell(x, z);
    if (i < 0 || j < 0 || i >= this.nx || j >= this.nz) return this.rooms.length ? 0 : Infinity;
    return this.clear[j * this.nx + i] * CELL;
  }

  #nearestFree(cx, cz, poly = null) {
    let best = null, bestD = Infinity;
    const margin = this.radius + 0.1; // a little more than just fitting
    for (let j = 0; j < this.nz; j++) for (let i = 0; i < this.nx; i++) {
      if (this.clear[j * this.nx + i] * CELL < margin) continue;
      const x = this.gx0 + (i + 0.5) * CELL, z = this.gz0 + (j + 0.5) * CELL;
      if (poly && !inside(poly, x, z)) continue;
      const d = (x - cx) ** 2 + (z - cz) ** 2;
      if (d < bestD) { bestD = d; best = [x, z]; }
    }
    return best;
  }

  /** Move by (dx, dz), sliding along obstacles: the move made ([dx, dz], [dx, 0] or [0, dz]), or null. */
  #step(dx, dz) {
    const p = this.camera.position;
    const here = this.#clearance(p.x, p.z);
    // a walker dropped too close to something may always move away from it
    const ok = (x, z) => { const c = this.#clearance(x, z); return c >= this.radius || c > here + 1e-6; };
    for (const [mx, mz] of [[dx, dz], [dx, 0], [0, dz]]) {
      if ((mx || mz) && ok(p.x + mx, p.z + mz)) {
        p.x += mx;
        p.z += mz;
        return [mx, mz];
      }
    }
    return null;
  }

  /**
   * Shortest walkable route (A* over the grid, then straightened), as waypoints [x, z]. Steps closer
   * to an obstacle than MOTION.comfort cost more, and the straightening never passes closer to one
   * than the route itself did (up to that distance): a route keeps to the middle of doors and passages.
   */
  #route([sx, sz], [gx, gz]) {
    const { nx, nz } = this;
    const walk = (k) => this.clear[k] * CELL >= this.radius;
    const near = (k) => Math.max(0, MOTION.comfort - this.clear[k] * CELL) / MOTION.comfort; // 0 (clear) .. 1 (touching)
    const inGrid = (i, j) => i >= 0 && j >= 0 && i < nx && j < nz;
    const [si, sj] = this.#cell(sx, sz);
    let [gi, gj] = this.#cell(gx, gz);
    if (!inGrid(si, sj)) return null;
    if (!inGrid(gi, gj) || !walk(gj * nx + gi)) {
      // the spot is taken (a bed, a corner): the walkable cell nearest to it
      const spot = this.#nearestFree(gx, gz);
      if (!spot) return null;
      [gx, gz] = spot;
      [gi, gj] = this.#cell(gx, gz);
    }
    const start = sj * nx + si, goal = gj * nx + gi;
    const g = new Float64Array(nx * nz).fill(Infinity), came = new Int32Array(nx * nz).fill(-1);
    const closed = new Uint8Array(nx * nz);
    const h = (k) => Math.hypot((k % nx) - gi, Math.floor(k / nx) - gj);
    const open = new Heap();
    g[start] = 0;
    open.push(start, h(start));
    const steps = [[1, 0, 1], [-1, 0, 1], [0, 1, 1], [0, -1, 1], [1, 1, Math.SQRT2], [1, -1, Math.SQRT2], [-1, 1, Math.SQRT2], [-1, -1, Math.SQRT2]];
    while (open.size) {
      const k = open.pop();
      if (closed[k]) continue;
      closed[k] = 1;
      if (k === goal) break;
      const i = k % nx, j = Math.floor(k / nx);
      for (const [di, dj, c] of steps) {
        const a = i + di, b = j + dj;
        if (!inGrid(a, b)) continue;
        const n = b * nx + a;
        if (closed[n] || !walk(n)) continue;
        const ng = g[k] + c * (1 + 2 * near(n));
        if (ng < g[n]) { g[n] = ng; came[n] = k; open.push(n, ng + h(n)); }
      }
    }
    let end = goal;
    if (g[goal] === Infinity) {
      // not connected (another flat, a room cut off by furniture): as close as one can get
      let best = Infinity;
      for (let k = 0; k < nx * nz; k++) if (closed[k] && h(k) < best) { best = h(k); end = k; }
      if (end === start) return null;
      [gx, gz] = [this.gx0 + ((end % nx) + 0.5) * CELL, this.gz0 + (Math.floor(end / nx) + 0.5) * CELL];
    }
    const cells = [];
    for (let k = end; k !== -1; k = came[k]) cells.unshift(k);
    // straighten: from each kept point, on to the farthest cell still in line of sight, the line
    // keeping at least the clearance the route had between the two (up to comfort, the radius at least)
    const pt = (k) => [this.gx0 + ((k % nx) + 0.5) * CELL, this.gz0 + (Math.floor(k / nx) + 0.5) * CELL];
    const out = [];
    let from = [sx, sz], a = 0;
    while (a < cells.length - 1) {
      const least = new Float64Array(cells.length);
      for (let b = a, m = Infinity; b < cells.length; b++) least[b] = m = Math.min(m, this.clear[cells[b]] * CELL);
      const need = (b) => Math.max(this.radius, Math.min(MOTION.comfort, least[b]) - CELL);
      let b = cells.length - 1;
      while (b > a + 1 && !this.#sees(from, pt(cells[b]), need(b))) b--;
      from = pt(cells[b]);
      out.push(from);
      a = b;
    }
    if (out.length) out[out.length - 1] = [gx, gz];
    return out;
  }

  /** Is the straight line from a to b at least `need` metres from every obstacle (the walker's radius by default)? */
  #sees([ax, az], [bx, bz], need = this.radius) {
    const n = Math.ceil(Math.hypot(bx - ax, bz - az) / (CELL / 2));
    for (let s = 1; s <= n; s++) {
      if (this.#clearance(ax + ((bx - ax) * s) / n, az + ((bz - az) * s) / n) < need) return false;
    }
    return true;
  }

  // ------------------------------------------------------------------------

  #apply() {
    const q = new THREE.Quaternion().setFromEuler(new THREE.Euler(this.pitch, this.yaw, 0, "YXZ"));
    this.camera.quaternion.copy(q);
    this.camera.updateMatrixWorld();
  }

  #isFloor(h) {
    const n = h.face?.normal?.clone().transformDirection(h.object.matrixWorld);
    return !!n && n.dot(UP) > 0.7;
  }

  /** Crosshair (while the mouse is locked) and a hint line that fades after a few seconds. */
  #overlay() {
    const css = "position:fixed;pointer-events:none;z-index:10;font:13px/1.3 system-ui,sans-serif;";
    const cross = Object.assign(document.createElement("div"), { textContent: "+" });
    cross.style.cssText = css + "left:50%;top:50%;transform:translate(-50%,-50%);color:#fff;font-size:22px;text-shadow:0 0 3px #000;display:none";
    const line = document.createElement("div");
    line.style.cssText = css + "left:50%;bottom:18px;transform:translateX(-50%);padding:6px 12px;border-radius:14px;background:rgba(20,20,20,.6);color:#fff;transition:opacity .6s;opacity:0;white-space:nowrap";
    document.body.append(cross, line);
    let timer = null;
    const hint = (text) => {
      clearTimeout(timer);
      if (!text) { line.style.opacity = "0"; return; }
      line.textContent = text;
      line.style.opacity = "1";
      timer = setTimeout(() => { line.style.opacity = "0"; }, 6000);
    };
    const touch = matchMedia("(pointer: coarse)").matches;
    // in the app its toolbar shows the hint; a headless render shows none
    if (window.parent !== window || new URLSearchParams(location.search).get("headless") === "1") line.style.display = "none";
    hint(touch ? "Drag to look around · tap the floor to go there" : "Click to look around with the mouse · W A S D to walk · drag also works");
    this.ui = { cross, line };
    return { cross, hint };
  }

  #listen() {
    const el = this.canvas;
    const handlers = [];
    const on = (t, target, fn) => { target.addEventListener(t, fn); handlers.push([t, target, fn]); };
    let down = null;
    // desktop: the first click locks the mouse (game-style look, crosshair), a click while locked glides
    // to the floor under the crosshair, Esc releases it. Touch: drag to look, tap the floor to glide.
    this.locked = false;
    const ui = this.#overlay();
    on("pointerlockchange", document, () => {
      this.locked = document.pointerLockElement === el;
      ui.cross.style.display = this.locked ? "block" : "none";
      ui.hint(this.locked ? "Mouse to look · W A S D to walk · click the floor to go there · Esc to release" : null);
    });
    const look = (dx, dy, k) => {
      if (this.move) this.move.free = true; // looking around during a glide: the visitor's view from now on
      this.aim.yaw -= dx * k; // right: turn right
      this.aim.pitch = THREE.MathUtils.clamp(this.aim.pitch - dy * k, -1.2, 1.2); // up: look up
      this.dirty = true;
      this.onChange?.();
    };
    const glideAt = (ndc) => {
      this.ray.setFromCamera(ndc, this.camera);
      this.ray.far = 30;
      const hit = this.ray.intersectObjects(this.surfaces, false)[0];
      if (hit && hit.point.y < this.floorY + 1.2) this.goTo(hit.point.x, hit.point.z);
    };
    on("pointerdown", el, (e) => {
      down = { x: e.clientX, y: e.clientY, t: performance.now(), lx: e.clientX, ly: e.clientY };
      if (!this.locked) el.setPointerCapture?.(e.pointerId);
    });
    on("pointermove", el, (e) => {
      if (this.locked) return look(e.movementX, e.movementY, 0.0022);
      if (!down) return;
      const dx = e.clientX - down.lx, dy = e.clientY - down.ly;
      down.lx = e.clientX;
      down.ly = e.clientY;
      // dragging moves the view the other way up/down than the mouse does in look mode: grab the scene
      look(dx, -dy, e.pointerType === "touch" ? 0.005 : 0.0035);
    });
    on("pointerup", el, (e) => {
      if (!down) return;
      const still = Math.hypot(e.clientX - down.x, e.clientY - down.y) < 6 && performance.now() - down.t < 450;
      down = null;
      if (this.locked) return glideAt(new THREE.Vector2(0, 0)); // the crosshair
      if (!still) return;
      if (e.pointerType === "mouse" && el.requestPointerLock) {
        el.requestPointerLock();
        return;
      }
      // a tap on the floor (or on something standing on it): glide there
      const rect = el.getBoundingClientRect();
      glideAt(new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1));
    });
    on("keydown", window, (e) => {
      const k = e.key.toLowerCase();
      if (["w", "a", "s", "d", "arrowup", "arrowdown", "arrowleft", "arrowright"].includes(k)) {
        this.keys.add(k);
        this.onChange?.();
        e.preventDefault();
      } else if (k === "shift") this.keys.add(k); // run
    });
    on("keyup", window, (e) => this.keys.delete(e.key.toLowerCase()));
    on("blur", window, () => this.keys.clear());
    this.handlers = handlers;
  }
}

/** Binary min-heap of (key, priority) for A*. */
class Heap {
  constructor() { this.k = []; this.p = []; }
  get size() { return this.k.length; }
  push(k, p) {
    const K = this.k, P = this.p;
    let i = K.length;
    K.push(k); P.push(p);
    while (i > 0) {
      const up = (i - 1) >> 1;
      if (P[up] <= p) break;
      K[i] = K[up]; P[i] = P[up]; i = up;
    }
    K[i] = k; P[i] = p;
  }
  pop() {
    const K = this.k, P = this.p, top = K[0], k = K.pop(), p = P.pop();
    if (K.length) {
      let i = 0;
      for (;;) {
        const l = 2 * i + 1, r = l + 1;
        let m = i, mp = p;
        if (l < K.length && P[l] < mp) { m = l; mp = P[l]; }
        if (r < K.length && P[r] < mp) { m = r; mp = P[r]; }
        if (m === i) break;
        K[i] = K[m]; P[i] = P[m]; i = m;
      }
      K[i] = k; P[i] = p;
    }
    return top;
  }
}

function inside(poly, x, z) {
  let c = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, zi] = poly[i], [xj, zj] = poly[j];
    if ((zi > z) !== (zj > z) && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) c = !c;
  }
  return c;
}

/** Area of a polygon [[x, z], ...] (shoelace). */
function polygonArea(poly) {
  let a = 0;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) a += poly[j][0] * poly[i][1] - poly[i][0] * poly[j][1];
  return Math.abs(a) / 2;
}

/** Distance from point q to the segment a-b. */
function segmentDistance(q, a, b) {
  const dx = b[0] - a[0], dz = b[1] - a[1], L2 = dx * dx + dz * dz;
  const t = L2 > 0 ? Math.max(0, Math.min(1, ((q[0] - a[0]) * dx + (q[1] - a[1]) * dz) / L2)) : 0;
  return Math.hypot(q[0] - a[0] - t * dx, q[1] - a[1] - t * dz);
}

/** Is q inside the polygon or within `margin` metres of its edges? */
function nearPolygon(poly, q, margin) {
  if (inside(poly, q[0], q[1])) return true;
  for (let i = 0; i < poly.length; i++) if (segmentDistance(q, poly[i], poly[(i + 1) % poly.length]) < margin) return true;
  return false;
}
