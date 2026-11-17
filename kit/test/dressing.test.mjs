// housekit dressing (#103): what a stager adds to the furnished rooms once the scene is built, the listing photos'
// rules: a nightstand's lamp, books and carafe, a laid table, cushions on a bare sofa, a sideboard's objects, free
// corners on the floor, a bath's towels; never on what the scene dressed itself, never hiding anything of the scene.
import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import { dressRooms, floorSpots } from "../dressing.js";
import fx, { resetObjects } from "../furnish.js";
import { perimeterWalls } from "../house.js";
import * as interior from "../interior.js";
import { setHouseStyle } from "../styles.js";

afterEach(() => {
  setHouseStyle({});
  resetObjects();
  delete globalThis.__housekitSeed;
});

const ROOMS = [
  { name: "Chambre", use: "bedroom", polygon: [[0, 0], [4, 0], [4, 3.5], [0, 3.5]] },
  { name: "Bain", use: "bath", polygon: [[4, 0], [6.5, 0], [6.5, 3.5], [4, 3.5]] },
  { name: "Hall", use: "hall", polygon: [[6.5, 0], [10, 0], [10, 3.5], [6.5, 3.5]] },
  { name: "Séjour", use: "living", polygon: [[0, 3.5], [10, 3.5], [10, 8], [0, 8]] },
];
const room = (name) => ROOMS.find((r) => r.name === name).polygon;

function sideboard() {
  const g = new THREE.Group();
  const m = new THREE.Mesh(new THREE.BoxGeometry(1.6, 0.75, 0.45).translate(0, 0.375, 0), fx.finish.oak());
  g.add(m);
  g.userData = { kind: "furniture", name: "sideboard", footprint: [1.6, 0.45] };
  return g;
}

// four rooms in a 10 x 8 m shell: a bedroom with a bed between two nightstands (a lamp on each), a bath with a tub,
// a hall, a living room with a dining set, a sofa and a sideboard
function flat({ dressedNightstand = false } = {}) {
  const root = new THREE.Group();
  root.add(perimeterWalls({ polygon: [[-0.3, -0.3], [10.3, -0.3], [10.3, 8.3], [-0.3, 8.3]], height: 2.6, thickness: 0.3 }));
  root.add(interior.floorPlan({
    rooms: ROOMS,
    partitions: [
      { from: [0, 3.5], to: [10, 3.5], openings: [{ offset: 2.6, width: 0.9, door: { hinge: "start", swing: "left" } },
        { offset: 4.6, width: 0.8, door: { hinge: "start", swing: "left" } }, { offset: 7.6, width: 1.4 }] },
      { from: [4, 0], to: [4, 3.5] },
      { from: [6.5, 0], to: [6.5, 3.5] },
    ],
  }));
  const on = (p, polygon, edge, at, y = 0) => { root.add(fx.onWall(polygon, edge, at, p, { y })); return p; };
  const bed = on(fx.bed({ width: 1.6 }), room("Chambre"), 0, 2);
  const stands = [on(fx.nightstand(), room("Chambre"), 0, 0.9), on(fx.nightstand(), room("Chambre"), 0, 3.1)];
  const lamps = stands.map((s) => {
    const l = fx.fixture("globeTableLamp");
    l.position.set(s.position.x, 0.52, s.position.z);
    root.add(l);
    return l;
  });
  if (dressedNightstand) {
    const book = new THREE.Mesh(new THREE.BoxGeometry(0.2, 0.04, 0.15).translate(0, 0.02, 0), fx.finish.black());
    book.position.set(stands[0].position.x + 0.1, 0.521, stands[0].position.z + 0.08);
    book.userData = { kind: "furniture", name: "books", footprint: [0.2, 0.15] };
    root.add(book);
  }
  const tub = on(fx.bathtub({ length: 1.7, width: 0.75 }), room("Bain"), 0, 1.25);
  const table = fx.diningSet({ length: 1.8, width: 0.9 });
  table.position.set(3, 0, 5.8);
  root.add(table);
  const sofa = on(fx.sofa({ width: 2.2 }), room("Séjour"), 2, 3, 0.015);
  const board = on(sideboard(), room("Séjour"), 3, 2.2);
  root.updateMatrixWorld(true);
  return { root, bed, stands, lamps, tub, table, sofa, board };
}

const held = (root, pred = () => true) => {
  const out = [];
  root.traverse((o) => { if (o.userData?.kind === "dressing" && o.userData.dressing && pred(o.userData)) out.push(o); });
  return out;
};
const at = (o) => o.getWorldPosition(new THREE.Vector3());
const inBox = (p, box, pad = 0) => p.x > box.min.x - pad && p.x < box.max.x + pad && p.z > box.min.z - pad && p.z < box.max.z + pad;

test("the dressing is one group, the house group's last child; nothing of the scene is hidden or reordered", () => {
  globalThis.__housekitSeed = "dress";
  const { root } = flat();
  const before = [...root.children];
  const visible = [];
  root.traverse((o) => { if (o.visible) visible.push(o); });
  const done = dressRooms(root);
  assert.ok(done.pieces > 6, `pieces: ${JSON.stringify(done)}`);
  assert.equal(root.children.length, before.length + 1);
  before.forEach((c, i) => assert.equal(root.children[i], c, "the scene's own children keep their order (the export's node ids)"));
  assert.equal(root.children.at(-1).userData.kind, "dressing");
  assert.equal(root.userData.kitDressing, 1, "the photo service leaves these rules to the kit");
  assert.ok(visible.every((o) => o.visible), "nothing hidden");
  for (const h of held(root)) assert.ok(h.userData.room, `${h.userData.dressing} in a room`);
});

test("a nightstand: its lamp at the back corner away from the bed, books or something small at the front on the bed's side", () => {
  globalThis.__housekitSeed = "dress";
  const { root, bed, stands, lamps } = flat();
  const was = lamps.map((l) => at(l));
  dressRooms(root);
  const bx = at(bed).x;
  stands.forEach((s, i) => {
    const box = new THREE.Box3().setFromObject(s), c = box.getCenter(new THREE.Vector3()), side = Math.sign(bx - c.x);
    const now = at(lamps[i]);
    assert.ok((now.x - c.x) * side < 0, `lamp ${i} away from the bed: ${now.x.toFixed(2)} (stand at ${c.x.toFixed(2)})`);
    assert.ok(now.z < was[i].z, `lamp ${i} to the back (the wall at z = 0)`);
    assert.ok(Math.abs(now.y - was[i].y) < 1e-9, "at the same height");
    const on = held(root, (u) => u.room === "Chambre").filter((h) => inBox(at(h), box, 0.01) && at(h).y > box.max.y - 0.02);
    assert.ok(on.length >= 1, `something on nightstand ${i}`);
    for (const h of on.filter((o) => o.userData.made === "books")) {
      assert.match(h.userData.arg, /^\d-\d-\d$/, "the covers");
      assert.ok((at(h).x - c.x) * side > 0, "the books on the bed's side");
    }
  });
});

test("a nightstand the scene dressed itself is left as it is", () => {
  globalThis.__housekitSeed = "dress";
  const { root, stands, lamps } = flat({ dressedNightstand: true });
  const was = at(lamps[0]);
  dressRooms(root);
  const box = new THREE.Box3().setFromObject(stands[0]);
  assert.equal(held(root).filter((h) => inBox(at(h), box, 0.01) && at(h).y > 0.4).length, 0);
  assert.ok(at(lamps[0]).distanceTo(was) < 1e-9, "its lamp stays");
});

test("a dining table: up to three things on its centre line, on its top, apart", () => {
  for (const seed of ["a", "b", "c", "d"]) {
    globalThis.__housekitSeed = seed;
    resetObjects();
    const { root, table } = flat();
    dressRooms(root);
    const top = 0.75;
    const on = held(root, (u) => u.room === "Séjour").filter((h) => Math.abs(at(h).y - top) < 0.01 && inBox(at(h), new THREE.Box3(
      new THREE.Vector3(2.55, 0, 4.9), new THREE.Vector3(3.45, 1, 6.7))));
    const centre = on.filter((h) => h.userData.made !== "runner" && h.userData.made !== "napkin");
    assert.ok(centre.length >= 1 && centre.length <= 3, `${seed}: ${centre.map((h) => h.userData.dressing)}`);
    for (const h of centre) assert.ok(Math.abs(at(h).x - at(table).x) < 0.01, `${seed}: ${h.userData.dressing} on the centre line`);
    const zs = centre.map((h) => at(h).z).sort((a, b) => a - b);
    for (let i = 1; i < zs.length; i++) assert.ok(zs[i] - zs[i - 1] > 0.15, `${seed}: apart`);
    const runner = on.find((h) => h.userData.made === "runner");
    if (runner) assert.match(runner.userData.arg, /^30-1\d\d$/, "a runner 30 cm wide along the table");
  }
});

test("a dining table's plates get a napkin each", () => {
  for (const seed of ["a", "b", "c", "d"]) {
    globalThis.__housekitSeed = seed;
    resetObjects();
    const { root } = flat();
    const plates = [5.3, 6.3].map((z) => {
      const p = new THREE.Group();
      p.add(new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.1, 0.02, 24).translate(0, 0.01, 0), fx.finish.oak()));
      p.userData = { kind: "furniture", name: "plate", footprint: [0.24, 0.24] };
      p.position.set(2.75, 0.75, z);
      root.add(p);
      return p;
    });
    root.updateMatrixWorld(true);
    dressRooms(root);
    const napkins = held(root, (u) => u.made === "napkin");
    assert.equal(napkins.length, plates.length, `${seed}: one napkin per plate`);
    for (const p of plates) {
      assert.ok(napkins.some((n) => Math.hypot(at(n).x - at(p).x, at(n).z - at(p).z) < 0.01), `${seed}: a napkin on each plate`);
    }
  }
});

test("a bare sofa gets cushions against its back; a sofa the house style dressed, none", () => {
  globalThis.__housekitSeed = "dress";
  const { root, sofa } = flat();
  interior.roomDetails(root); // scandinavian: no sofa dressing
  dressRooms(root);
  const box = new THREE.Box3().setFromObject(sofa);
  const cushions = held(root, (u) => u.dressing === "cushion");
  assert.ok(cushions.length >= 2, `cushions: ${cushions.length}`);
  for (const c of cushions) {
    const p = at(c);
    assert.ok(inBox(p, box) && p.y > 0.4 && p.y < 0.6, `on the seat: ${p.toArray().map((v) => v.toFixed(2))}`);
    assert.ok(p.z > box.max.z - 0.6, "against the back (the wall at z = 8)");
    assert.ok(c.userData.model, "a catalogue cushion");
  }
  const xs = cushions.map((c) => at(c).x), mid = (box.min.x + box.max.x) / 2;
  assert.ok(xs.some((x) => x < mid) && xs.some((x) => x > mid), "at both ends");

  setHouseStyle({ style: "contemporary" });
  resetObjects();
  const olive = flat();
  interior.roomDetails(olive.root);
  dressRooms(olive.root);
  assert.equal(held(olive.root, (u) => u.dressing === "cushion" || u.made === "sofathrow").length, 0, "dressed by the style");
});

// a 2.2 m sofa whose arms stand 17 cm over its 45 cm seat
function armedSofa() {
  const g = new THREE.Group(), fab = fx.finish.fabric();
  const box = (w, h, d, x, z) => g.add(new THREE.Mesh(new THREE.BoxGeometry(w, h, d).translate(x, h / 2, z), fab));
  box(1.9, 0.45, 0.9, 0, 0);
  box(1.9, 0.8, 0.2, 0, -0.35);
  for (const s of [-1, 1]) box(0.15, 0.62, 0.9, s * 1.025, 0);
  g.userData = { kind: "furniture", name: "sofa", footprint: [2.2, 0.9] };
  return g;
}

test("now and then a throw over the sofa's far arm, measured for the photo's own", () => {
  const throws = [];
  for (const seed of "abcdefghijkl") {
    globalThis.__housekitSeed = seed;
    resetObjects();
    const root = new THREE.Group();
    root.add(interior.floorPlan({ rooms: [ROOMS[3]] }));
    const sofa = fx.onWall(room("Séjour"), 2, 5, armedSofa());
    root.add(sofa);
    dressRooms(root);
    throws.push(...held(root, (u) => u.made === "sofathrow"));
  }
  assert.ok(throws.length >= 3 && throws.length < 12, `throws: ${throws.length}`);
  for (const t of throws) {
    assert.equal(t.userData.arg, "15-62-45", "arm width, arm height, seat height (cm)");
    assert.ok(Math.abs(Math.abs(at(t).x - 5) - 0.95) < 0.02 && Math.abs(at(t).y) < 1e-6, `at an arm's inner face: ${at(t).toArray()}`);
    assert.equal(t.userData.side ?? 1, -Math.sign(at(t).x - 5), "the sofa faces north: its left arm (side -1) is to the east");
  }
});

test("a sideboard: something tall at one end, books at the other, nothing over its edges", () => {
  globalThis.__housekitSeed = "dress";
  const { root, board } = flat();
  dressRooms(root);
  const box = new THREE.Box3().setFromObject(board);
  const on = held(root).filter((h) => inBox(at(h), box, 0.001) && Math.abs(at(h).y - 0.751) < 0.01);
  assert.ok(on.length >= 2, `on the sideboard: ${on.map((h) => h.userData.dressing)}`);
  assert.ok(on.some((h) => h.userData.made === "books"));
  assert.ok(on.some((h) => ["pot-plant", "vase"].includes(h.userData.dressing)));
});

test("the floor: accents against the walls, clear of the doors and the furniture, more in a larger room", () => {
  globalThis.__housekitSeed = "dress";
  const { root, table, sofa, board, bed } = flat();
  dressRooms(root);
  const floor = held(root, (u) => ["floor-plant", "vase", "pouf", "basket"].includes(u.dressing)).filter((h) => at(h).y < 0.01);
  const living = floor.filter((h) => h.userData.room === "Séjour"), bedroom = floor.filter((h) => h.userData.room === "Chambre");
  assert.ok(living.length >= 1 && living.length <= 3, `living: ${living.length}`);
  assert.ok(bedroom.length <= 2, `bedroom: ${bedroom.length}`);
  const boxes = [table, sofa, board, bed].map((p) => new THREE.Box3().setFromObject(p));
  const doors = [[3.05, 3.5], [4.9, 3.5], [8.3, 3.5]];
  for (const h of floor) {
    const p = at(h);
    assert.ok(!boxes.some((b) => inBox(p, b, 0.15)), `${h.userData.dressing} clear of the furniture at ${p.x.toFixed(2)}, ${p.z.toFixed(2)}`);
    assert.ok(doors.every(([x, z]) => Math.hypot(p.x - x, p.z - z) > 0.9), `${h.userData.dressing} clear of the doors`);
    const poly = ROOMS.find((r) => r.name === h.userData.room).polygon, xs = poly.map((q) => q[0]), zs = poly.map((q) => q[1]);
    const edge = Math.min(p.x - Math.min(...xs), Math.max(...xs) - p.x, p.z - Math.min(...zs), Math.max(...zs) - p.z);
    assert.ok(edge < 0.6, `${h.userData.dressing} against a wall: ${edge.toFixed(2)}`);
  }
});

test("floorSpots: corners first, never in a door's way, in furniture or before a low window; an open side is no wall", () => {
  const polygon = [[0, 0], [4, 0], [4, 4], [0, 4]];
  const walls = [{ a: [0, -0.05], b: [4, -0.05], t: 0.1 }, { a: [4.05, 0], b: [4.05, 4], t: 0.1 }, { a: [-0.15, 0], b: [-0.15, 4], t: 0.3 }];
  const spots = floorSpots({ polygon, walls, boxes: [{ rect: [1.5, 0, 2.5, 0.6], y0: 0, y1: 0.8 }], doors: [{ a: [3.8, 0], b: [3.8, 0.9] }],
    windows: [{ a: [0, 1], b: [0, 2], y0: 0.4 }], r: 0.25, h: 1.2 });
  assert.ok(spots.length > 3);
  assert.ok(spots[0].corner, "a corner first");
  assert.ok(spots[0].x < 1 && spots[0].z < 1, `the corner away from the door: ${spots[0].x}, ${spots[0].z}`);
  for (const s of spots) {
    assert.ok(Math.hypot(s.x - 3.8, s.z - Math.min(0.9, Math.max(0, s.z))) >= 1.15 - 1e-9, `clear of the door: ${s.x}, ${s.z}`);
    assert.ok(!(s.x > 1.2 && s.x < 2.8 && s.z < 0.9), `clear of the box: ${s.x}, ${s.z}`);
    assert.ok(!(s.x < 0.6 && s.z > 0.7 && s.z < 2.3), `not before the window: ${s.x}, ${s.z}`);
    assert.ok(!(s.x > 0.8 && s.x < 3.2 && s.z > 3.3), `the south side has no wall: ${s.x}, ${s.z}`);
    assert.ok(!(s.corner && s.z > 3.3), `no corner on the open side: ${s.x}, ${s.z}`);
  }
});

test("a bath: a towel on a bar on a free wall when it has no rail", () => {
  globalThis.__housekitSeed = "dress";
  const { root } = flat();
  dressRooms(root);
  const [bar] = held(root, (u) => u.made === "rail");
  assert.ok(bar, "a towel bar");
  const p = at(bar);
  assert.ok(Math.abs(p.y - 1.2) < 1e-6 && p.x > 4 && p.x < 6.5 && p.z > 0 && p.z < 3.5, `in the bath at 1.2 m: ${p.toArray()}`);
});

test("the same scene draws the same dressing", () => {
  const plan = () => {
    globalThis.__housekitSeed = "same";
    resetObjects();
    const { root } = flat();
    dressRooms(root);
    return held(root).map((h) => `${h.userData.dressing}:${h.userData.arg ?? h.userData.model ?? ""}@${at(h).toArray().map((v) => v.toFixed(3))}`);
  };
  assert.deepEqual(plan(), plan());
});
