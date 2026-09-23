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

import * as THREE from "three";

const CELL = 0.05;
const SKIP = new Set(["terrain", "leaves", "roomFloor", "ceiling"]);
const UP = new THREE.Vector3(0, 1, 0);
const LAYER = 31;

function kindOf(o) {
  for (let a = o; a; a = a.parent) if (a.userData?.kind) return a.userData;
  return {};
}

export class Walk {
  constructor({ camera, canvas, root, renderer, onChange, eye = 1.6, speed = 1.4, radius = 0.25 }) {
    Object.assign(this, { camera, canvas, root, renderer, onChange, eye, speed, radius });
    this.keys = new Set();
    this.yaw = 0;
    this.pitch = 0;
    this.path = [];
    this.glide = null;
    this.ray = new THREE.Raycaster();
    this.clock = new THREE.Clock();
    this.floorY = 0;
    this.#listen();
    this.sync();
  }

  /** Take over from wherever the camera is: its heading, the floor under it, where one can walk. */
  sync() {
    const dir = this.camera.getWorldDirection(new THREE.Vector3());
    this.yaw = Math.atan2(-dir.x, -dir.z);
    this.pitch = Math.asin(THREE.MathUtils.clamp(dir.y, -1, 1));
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

  /** Glide to [x, z] along the shortest walkable route (to the nearest walkable spot if it is taken). */
  goTo(x, z) {
    const p = this.camera.position;
    this.path = this.#route([p.x, p.z], [x, z]) ?? [];
    this.glide = this.path.shift() ?? null;
    this.onChange?.();
  }

  /**
   * Jump into a room, where a photographer would stand: among the free spots of the room (every
   * 25 cm), the one with the longest view that stays in the room, a little in favour of its centre;
   * facing along that view.
   */
  jumpTo(room) {
    const poly = room.polygon;
    let cx = 0, cz = 0;
    for (const [x, z] of poly) { cx += x / poly.length; cz += z / poly.length; }
    let best = null;
    const [xs, zs] = [poly.map((p) => p[0]), poly.map((p) => p[1])];
    for (let x = Math.min(...xs) + 0.125; x < Math.max(...xs); x += 0.25) {
      for (let z = Math.min(...zs) + 0.125; z < Math.max(...zs); z += 0.25) {
        if (!inside(poly, x, z) || this.#clearance(x, z) < this.radius + 0.1) continue;
        const [len, yaw] = this.#longestView(x, z, poly);
        const score = len - 0.3 * Math.hypot(x - cx, z - cz);
        if (!best || score > best.score) best = { score, x, z, yaw };
      }
    }
    const spot = best ? [best.x, best.z] : this.#nearestFree(cx, cz, poly);
    if (spot) this.camera.position.set(spot[0], this.floorY + this.eye, spot[1]);
    this.path = [];
    this.glide = null;
    this.faceOpenView(poly);
    this.onChange?.();
  }

  /**
   * Turn to the longest open view from where the walker stands (level), not to the nearest wall; with
   * a room polygon, the longest view that stays in that room (the room itself, not the next door).
   */
  faceOpenView(poly = null) {
    const p = this.camera.position;
    this.yaw = this.#longestView(p.x, p.z, poly, 32)[1];
    this.pitch = -0.05;
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
    const f = new THREE.Vector2(-Math.sin(this.yaw), -Math.cos(this.yaw));
    const r = new THREE.Vector2(-f.y, f.x);
    const move = new THREE.Vector2();
    if (this.keys.has("w") || this.keys.has("arrowup")) move.add(f);
    if (this.keys.has("s") || this.keys.has("arrowdown")) move.sub(f);
    if (this.keys.has("d")) move.add(r);
    if (this.keys.has("a")) move.sub(r);
    let turned = false;
    if (this.keys.has("arrowleft")) { this.yaw += 1.6 * dt; turned = true; }
    if (this.keys.has("arrowright")) { this.yaw -= 1.6 * dt; turned = true; }
    const p = this.camera.position;
    if (move.lengthSq() > 0) {
      this.glide = null;
      this.path = [];
      move.normalize().multiplyScalar(this.speed * dt);
    } else if (this.glide) {
      const to = new THREE.Vector2(this.glide[0] - p.x, this.glide[1] - p.z);
      const d = to.length();
      const reach = this.speed * 1.5 * dt;
      if (d <= reach) {
        move.copy(to);
        this.glide = this.path.shift() ?? null;
      } else {
        move.copy(to).multiplyScalar(reach / d);
        // turn gently towards where we are going
        let dy = Math.atan2(-to.x, -to.y) - this.yaw;
        dy = Math.atan2(Math.sin(dy), Math.cos(dy));
        this.yaw += dy * Math.min(1, dt * 3);
        turned = true;
      }
    }
    let moved = false;
    if (move.lengthSq() > 0) {
      moved = this.#step(move.x, move.y);
      if (this.glide && !moved) { this.glide = null; this.path = []; }
    }
    if (moved || turned || this.dirty) {
      this.dirty = false;
      this.#apply();
      return true;
    }
    return false;
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
    this.root.traverse((o) => {
      if (o.userData?.kind !== "floorPlan" || Math.abs(o.userData.y - this.floorY) > 0.5) return;
      this.rooms.push(...o.userData.rooms);
      for (const w of o.userData.partitions) {
        const len = Math.hypot(w.to[0] - w.from[0], w.to[1] - w.from[1]);
        const ux = (w.to[0] - w.from[0]) / len, uz = (w.to[1] - w.from[1]) / len;
        for (const op of w.openings) {
          const c = op.offset + op.width / 2;
          for (const s of [-1, 1]) this.doorways.push([w.from[0] + ux * c - uz * s * 0.45, w.from[1] + uz * c + ux * s * 0.45]);
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

    const scene = this.root.parent ?? this.root;
    const cam = new THREE.OrthographicCamera(x0, x0 + nx * CELL, -z0, -(z0 + nz * CELL), 0.1, 200);
    cam.up.set(0, 0, -1);
    cam.position.set(0, hi + 50, 0);
    cam.lookAt(0, 0, 0);
    cam.layers.set(LAYER);
    const layered = obstacles.map((o) => { const had = o.layers.isEnabled(LAYER); o.layers.enable(LAYER); return [o, had]; });
    const target = new THREE.WebGLRenderTarget(nx, nz);
    const r = this.renderer;
    const saved = { target: r.getRenderTarget(), planes: r.clippingPlanes, color: r.getClearColor(new THREE.Color()), alpha: r.getClearAlpha(),
      override: scene.overrideMaterial, background: scene.background, fog: scene.fog };
    scene.overrideMaterial = new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide });
    scene.background = null;
    scene.fog = null;
    // cut above the head: looking down into a cut wall shows its inside, so the wall still covers its cells
    r.clippingPlanes = [new THREE.Plane(new THREE.Vector3(0, -1, 0), hi)];
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
    for (const [o, had] of layered) if (!had) o.layers.disable(LAYER);
    target.dispose();

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

  /** Move by (dx, dz), sliding along obstacles; true when it moved. */
  #step(dx, dz) {
    const p = this.camera.position;
    const here = this.#clearance(p.x, p.z);
    // a walker dropped too close to something may always move away from it
    const ok = (x, z) => { const c = this.#clearance(x, z); return c >= this.radius || c > here + 1e-6; };
    for (const [mx, mz] of [[dx, dz], [dx, 0], [0, dz]]) {
      if ((mx || mz) && ok(p.x + mx, p.z + mz)) {
        p.x += mx;
        p.z += mz;
        return true;
      }
    }
    return false;
  }

  /** Shortest walkable route (A* over the grid, then straightened), as waypoints [x, z]. */
  #route([sx, sz], [gx, gz]) {
    const { nx, nz } = this;
    const walk = (k) => this.clear[k] * CELL >= this.radius;
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
        const ng = g[k] + c;
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
    // straighten: from each kept point, on to the farthest cell still in walkable line of sight
    const pt = (k) => [this.gx0 + ((k % nx) + 0.5) * CELL, this.gz0 + (Math.floor(k / nx) + 0.5) * CELL];
    const out = [];
    let from = [sx, sz], a = 0;
    while (a < cells.length - 1) {
      let b = cells.length - 1;
      while (b > a + 1 && !this.#sees(from, pt(cells[b]))) b--;
      from = pt(cells[b]);
      out.push(from);
      a = b;
    }
    if (out.length) out[out.length - 1] = [gx, gz];
    return out;
  }

  #sees([ax, az], [bx, bz]) {
    const n = Math.ceil(Math.hypot(bx - ax, bz - az) / (CELL / 2));
    for (let s = 1; s <= n; s++) {
      if (!this.free(ax + ((bx - ax) * s) / n, az + ((bz - az) * s) / n)) return false;
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
    if (window.parent !== window) line.style.display = "none"; // in the app: its toolbar shows the hint
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
      this.yaw -= dx * k; // right: turn right
      this.pitch = THREE.MathUtils.clamp(this.pitch - dy * k, -1.2, 1.2); // up: look up
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
      }
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
