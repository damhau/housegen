// housekit styles (#99): every style names an entry of each role's catalogue, the precedence piece > room > house >
// style > fallback, fixtures never brass unless asked and readable against a white ceiling, and what the contemporary
// style changes in a bedroom, a kitchen and a living room.
import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import * as house from "../house.js";
import { DEFAULT_STYLE, FALLBACK, MAPS, ROLES, STYLES, look, setHouseStyle, styleName } from "../styles.js";
import { FINISHES, WOODS } from "../lighting-parts.js";
import { FLOOR_TILES, WALL_TILES } from "../finishes.js";
import fx, { BEDDING, FITTINGS, FURNITURE_TONES, HANDLES, MODELS, ROLES as PIECES, RUGS, SOFA_DRESSINGS, pickObject, resetObjects } from "../furnish.js";
import { FIXTURES } from "../lighting.js";
import { BUILT_IN } from "../lighting-built-in.js";
import interior, { FEATURE_WALLS } from "../interior.js";

afterEach(() => setHouseStyle({}));

const CATALOGUES = {
  fixtures: Object.fromEntries(FINISHES.map((f) => [f, true])),
  builtIns: Object.fromEntries(FINISHES.map((f) => [f, true])),
  fittings: FITTINGS,
  wallTile: WALL_TILES,
  bathFloor: FLOOR_TILES,
  bedding: BEDDING,
  featureWall: FEATURE_WALLS,
  handles: HANDLES,
  rug: RUGS,
  sofaDressing: SOFA_DRESSINGS,
  furnitureTone: FURNITURE_TONES,
  models: null, // a map: model names to model or role names
};

test("every style, and the fallback, names an existing entry for every role", () => {
  assert.deepEqual(Object.keys(CATALOGUES).sort(), Object.keys(ROLES).sort());
  for (const [name, style] of [...Object.entries(STYLES), ["fallback", FALLBACK]]) {
    for (const role of Object.keys(ROLES)) {
      const v = style[role];
      assert.ok(v != null, `${name}: no ${role}`);
      if (MAPS.has(role)) {
        for (const [from, to] of Object.entries(v)) {
          assert.ok(from in MODELS, `${name}.${role}: "${from}" is not a model`);
          assert.ok(to in MODELS || to in PIECES, `${name}.${role}: "${to}" is neither a model nor a role`);
        }
        continue;
      }
      const names = typeof v === "object" ? Object.values(v) : [v];
      if (typeof v === "object") assert.ok("default" in v, `${name}.${role}: a value per kind has a default`);
      for (const n of names) {
        // a fixture family's finish with its parts
        const finish = role === "fixtures" && typeof n === "object" ? n.finish : n;
        assert.ok(finish in CATALOGUES[role], `${name}.${role}: "${finish}" is not in its catalogue`);
      }
    }
  }
  for (const v of Object.values(STYLES).map((st) => st.fixtures).filter((v) => typeof v === "object")) {
    for (const kind of Object.keys(v).filter((k) => k !== "default")) assert.ok(kind in FIXTURES, kind);
  }
  for (const v of Object.values(STYLES).map((st) => st.furnitureTone).filter((v) => typeof v === "object")) {
    for (const kind of Object.keys(v).filter((k) => k !== "default")) assert.ok(kind in PIECES, kind);
  }
  assert.ok(DEFAULT_STYLE in STYLES);
});

test("no style: the default style; a house style, a room override and a piece override win in that order", () => {
  assert.equal(styleName(), "scandinavian");
  assert.equal(look("fittings"), "chrome");
  assert.deepEqual(look("fixtures", { kind: "globePendant" }), { finish: "black", canopy: "oak" });
  assert.equal(look("fixtures", { kind: "ledRing" }), "black");

  setHouseStyle({ style: "contemporary", rooms: { "SDB 1": { wallTile: "white", fittings: "chrome" } } });
  assert.equal(styleName(), "contemporary");
  assert.equal(look("wallTile"), "marble-grey");
  assert.equal(look("wallTile", { room: "SDB 2" }), "marble-grey");
  assert.equal(look("wallTile", { room: "SDB 1" }), "white", "the room's override");
  assert.equal(look("fittings", { room: "SDB 1", piece: "black" }), "black", "the piece's own setting");
  assert.equal(look("fixtures", { kind: "globePendant" }), "black");

  setHouseStyle({ style: "contemporary", fixtures: "brass", rooms: { Chambre: { fixtures: "white" } } });
  assert.equal(look("fixtures"), "brass", "the house's override");
  assert.equal(look("fixtures", { room: "Chambre" }), "white");
  assert.equal(look("builtIns"), "white", "built-ins are a role of their own");

  setHouseStyle({ style: "baroque" });
  assert.equal(styleName(), DEFAULT_STYLE, "an unknown style draws in the default");
});

const brassIn = (o) => {
  const found = [];
  o.traverse((c) => {
    for (const m of [c.material ?? []].flat()) if (/brass/i.test(m.name ?? "")) found.push(c.name || m.name);
  });
  return found;
};

test("a fixture with no finish is never brass, in any style; brass when the house asks for it", () => {
  for (const style of [{}, { style: "scandinavian" }, { style: "contemporary" }]) {
    setHouseStyle(style);
    for (const name of Object.keys(FIXTURES)) {
      const f = fx.fixture(name, {});
      assert.notEqual(f.userData.fixture?.finish, "brass", `${name} (${style.style ?? "no style"})`);
      assert.deepEqual(brassIn(f), [], `${name} (${style.style ?? "no style"}) has brass parts`);
    }
  }
  setHouseStyle({ fixtures: "brass" });
  assert.ok(brassIn(fx.fixture("frameGlobes", {})).length > 0);
  assert.deepEqual(brassIn(fx.fixture("downlight", {})), [], "built-ins stay white");
});

const materials = (o) => {
  const names = new Set();
  o.traverse((c) => { for (const m of [c.material ?? []].flat()) names.add(m.name || `#${m.color?.getHexString()}`); });
  return names;
};

test("the bath fittings and the bedding follow the style, a piece's own option wins", () => {
  assert.ok(materials(fx.shower({})).has("#e6e8ea"), "chrome in scandinavian");
  setHouseStyle({ style: "contemporary" });
  for (const p of [fx.shower({}), fx.basin({}), fx.bathtub({}), fx.bathAccessories({})]) assert.ok(materials(p).has("matte black"));
  assert.ok(materials(fx.wc({})).has("matte black plate"));
  assert.ok(!materials(fx.shower({ fittings: "chrome" })).has("matte black"));
  assert.ok(materials(fx.wc({ tiles: true })).has("marble tiles"), "tiles: true, the style's wall tiles");
  assert.equal(fx.bedding().layered, true);
  assert.equal(fx.bedding({ bedding: "linen" }).layered, false);
  assert.ok(fx.bed({}).children.length > fx.bed({ bedding: "linen" }).children.length, "the layered bed has its cushions");
});

// a 4 x 3.5 bedroom (x 0..4, z 0..3.5) with a window in its north wall at the east end and a double bed against it
function bedroom({ bedAt = 1.6, picture = false } = {}) {
  const root = new THREE.Group();
  const room = { name: "Chambre", use: "bedroom", polygon: [[0, 0], [4, 0], [4, 3.5], [0, 3.5]] };
  const plan = interior.floorPlan({ y: 0, rooms: [room], partitions: [] });
  root.add(plan);
  const w = house.windowUnit({ width: 0.8, height: 1.2 });
  w.position.set(3.4, 0.9, -0.15);
  root.add(w);
  const bed = fx.onWall(room.polygon, 0, bedAt, fx.bed({ width: 1.6, length: 2.0 }), { y: 0.015 });
  root.add(bed);
  if (picture) root.add(fx.onWall(room.polygon, 0, bedAt, fx.wardrobe({ width: 0.6, depth: 0.03, height: 0.5 }), { y: 1.3 }));
  return { root, plan, bed };
}

test("contemporary puts a panelled wall behind a double bed, once, under what hangs above it; scandinavian none", () => {
  const plain = bedroom();
  interior.roomDetails(plain.root);
  assert.equal(plain.plan.children.filter((c) => c.userData.kind === "featureWall").length, 0);

  setHouseStyle({ style: "contemporary" });
  const { root, plan } = bedroom();
  interior.roomDetails(root);
  const walls = plan.children.filter((c) => c.userData.kind === "featureWall");
  assert.equal(walls.length, 1);
  root.updateMatrixWorld(true);
  const b = new THREE.Box3().setFromObject(walls[0]);
  assert.ok(Math.abs(b.min.z) < 0.005 && b.max.z < 0.12, `against the north wall: z ${b.min.z.toFixed(3)}..${b.max.z.toFixed(3)}`);
  assert.ok(b.min.x < 0.05 && b.max.x > 2.6 && b.max.x < 3.0 + 1e-3, `from the corner to the window: x ${b.min.x.toFixed(2)}..${b.max.x.toFixed(2)}`);
  assert.ok(Math.abs(b.max.y - (0.015 + 1.28)) < 0.01, `its ledge at 1.25 m: ${b.max.y.toFixed(3)}`);
  assert.equal(interior.roomDetails(root), 0, "a second pass adds nothing");

  const hung = bedroom({ picture: true });
  interior.roomDetails(hung.root);
  const under = hung.plan.children.find((c) => c.userData.kind === "featureWall");
  assert.ok(under && new THREE.Box3().setFromObject(under).max.y < 1.3 + 0.015 - 0.05, "below the piece hung over the bed");

  setHouseStyle({ style: "contemporary", rooms: { Chambre: { featureWall: "none" } } });
  const off = bedroom();
  interior.roomDetails(off.root);
  assert.equal(off.plan.children.filter((c) => c.userData.kind === "featureWall").length, 0, "a room's override");
});

// bedrooms of 4 x d (x from x0, z 0..d) with a double bed on their north wall, or standing free in the middle
function flats(rooms) {
  const root = new THREE.Group();
  const defs = rooms.map(({ name, x0, d }) => ({ name, use: "bedroom", polygon: [[x0, 0], [x0 + 4, 0], [x0 + 4, d], [x0, d]] }));
  const plan = interior.floorPlan({ y: 0, rooms: defs, partitions: [] });
  root.add(plan);
  rooms.forEach(({ x0, d, free }, i) => {
    const bed = fx.bed({ width: 1.6, length: 2.0 });
    if (free) bed.position.set(x0 + 2, 0.015, d / 2 + 0.3);
    root.add(free ? bed : fx.onWall(defs[i].polygon, 0, 1.6, bed, { y: 0.015 }));
  });
  const walls = () => plan.children.filter((c) => c.userData.kind === "featureWall").map((c) => c.userData.room ?? "the scene's").sort();
  return { root, plan, defs, walls };
}

test("the feature wall goes in each unit's main bedroom only (the largest with a double bed); a room's override adds or removes one", () => {
  const house = [
    { name: "Chambre 1 — Appartement 1", x0: 0, d: 3.5 },
    { name: "Chambre 2 — Appartement 1", x0: 4.2, d: 4.5 },
    { name: "Chambre 1 — Appartement 2", x0: 8.4, d: 4.0 },
    { name: "Chambre 2 — Appartement 2", x0: 12.6, d: 3.6 },
  ];
  setHouseStyle({ style: "contemporary" });
  const two = flats(house);
  interior.roomDetails(two.root);
  assert.deepEqual(two.walls(), ["Chambre 1 — Appartement 2", "Chambre 2 — Appartement 1"]);

  const one = flats([{ name: "Chambre", x0: 0, d: 3.5 }, { name: "Chambre parents", x0: 4.2, d: 4.5 }]);
  interior.roomDetails(one.root);
  assert.deepEqual(one.walls(), ["Chambre parents"], "a house of one unit: one wall");

  setHouseStyle({ style: "contemporary", rooms: { "Chambre 2 — Appartement 1": { featureWall: "none" }, "Chambre 2 — Appartement 2": { featureWall: "panelled" } } });
  const own = flats(house);
  interior.roomDetails(own.root);
  assert.deepEqual(own.walls(), ["Chambre 1 — Appartement 2", "Chambre 2 — Appartement 2"], "removed, not moved; added where asked");

  setHouseStyle({ style: "contemporary" });
  const free = flats([{ name: "Chambre 1 — App. 1", x0: 0, d: 3.5 }, { name: "Chambre 2 — App. 1", x0: 4.2, d: 4.5, free: true }]);
  interior.roomDetails(free.root);
  assert.deepEqual(free.walls(), ["Chambre 1 — App. 1"], "the next largest when the largest's bed stands free");

  const drawn = flats([{ name: "Chambre 1 — App. 1", x0: 0, d: 3.5 }, { name: "Chambre 2 — App. 1", x0: 4.2, d: 4.5 }]);
  drawn.root.add(fx.onWall(drawn.defs[0].polygon, 0, 2, interior.panelledWall(2), { y: 0.015, gap: 0 }));
  interior.roomDetails(drawn.root);
  assert.equal(drawn.root.children.filter((c) => c.userData.kind === "featureWall").length, 1, "the scene's own is the flat's");
  assert.deepEqual(drawn.walls(), [], "none added");

  setHouseStyle({ featureWall: "panelled" });
  const scandi = flats(house);
  interior.roomDetails(scandi.root);
  assert.deepEqual(scandi.walls(), ["Chambre 1 — Appartement 2", "Chambre 2 — Appartement 1"], "scandinavian when the house asks");
});

// a part of a fixture that reads against a white ceiling or wall: dark, or wood
const reads = (f) => {
  let found = false;
  f.traverse((c) => {
    for (const m of [c.isMesh ? c.material : []].flat()) {
      if (m.userData?.glow !== undefined || m.transparent) continue;
      const hsl = m.color?.getHSL({});
      if (/wood|oak|walnut/.test(m.name ?? "") || m.userData?.finish === "oak" || (hsl && hsl.l < 0.3)) found = true;
    }
  });
  return found;
};
const OWN = ["mirrorBar", "coveStrip", "underCabinet"];

test("scandinavian's decorative fixtures are black and white with oak and linen, each reads against a white ceiling; contemporary's black; built-ins white", () => {
  for (const [style, finish] of [[{}, null], [{ style: "contemporary" }, "black"]]) {
    setHouseStyle(style);
    for (const name of Object.keys(FIXTURES).filter((n) => !OWN.includes(n))) {
      const f = fx.fixture(name, {});
      if (name in BUILT_IN) {
        assert.equal(f.userData.fixture.finish, "white", `${name} (${style.style ?? "no style"})`);
        continue;
      }
      assert.ok(["black", "white"].includes(f.userData.fixture.finish), `${name}: ${f.userData.fixture.finish}`);
      if (finish) assert.equal(f.userData.fixture.finish, finish, name);
      assert.ok(reads(f), `${name} (${style.style ?? "no style"}) has a dark or wooden part`);
    }
  }
  setHouseStyle({});
  const woody = (f) => materials(f).has("oak wood") || [...materials(f)].some((n) => /oak/.test(n));
  const linen = (f) => { let c = null; f.traverse((o) => { if (o.material?.name === "fabric shade") c = o.material.color.getHexString(); }); return c; };
  assert.ok(woody(fx.fixture("globeFloorLamp", {})), "an oak stem");
  assert.ok(woody(fx.fixture("mushroomLamp", {})), "an oak foot");
  assert.equal(linen(fx.fixture("domePendant", {})), "e6dccb", "a linen shade");
  assert.equal(fx.fixture("discArms", { finish: "black" }).userData.fixture.finish, "black", "a fixture's own finish");
  assert.ok(!woody(fx.fixture("globeFloorLamp", { finish: "black" })), "a call's own finish leaves the style's parts out");
  assert.ok(!woody(fx.fixture("globeFloorLamp", { stem: "black" })), "a call's own part wins");
  assert.ok(woody(fx.fixture("globeFloorLamp", { stem: undefined })), "an undefined part is the style's");
  setHouseStyle({ style: "contemporary" });
  assert.equal(linen(fx.fixture("domePendant", {})), null, "a metal shade");
  assert.ok(!woody(fx.fixture("globeFloorLamp", {})));
  for (const wood of WOODS) assert.ok(fx.fixture("barCluster", { collar: wood }).userData.fixture);
});

const meshes = (o, test) => {
  const found = [];
  o.traverse((c) => { if (c.isMesh && test(c)) found.push(c); });
  return found;
};
const named = (name) => (c) => [c.material].flat().some((m) => m?.name === name);

test("a kitchen: handleless in scandinavian, black handles, sink, hood and mixer in contemporary; its fronts and worktop its own", () => {
  const run = () => fx.kitchenRun({ length: 3.6, tall: [{ at: 0.3, width: 0.6 }, { at: 3.3, width: 0.6, oven: true }], sink: 1.2, hob: 2.4, worktop: "oak", front: "#f2f1ed" });
  const scandi = run();
  assert.equal(meshes(scandi, named("matte black")).length, 0);
  setHouseStyle({ style: "contemporary" });
  const dark = run();
  const handles = meshes(dark, (c) => named("matte black")(c) && c.geometry.parameters?.depth === 0.026);
  // two per base column (a drawer over a door), one per wall-unit door, two on each tall unit
  assert.ok(handles.length >= 12, `${handles.length} handles`);
  assert.ok(meshes(dark, named("black composite sink")).length > 0, "a black sink");
  const fronts = (k) => new Set(meshes(k, () => true).map((c) => c.material).filter((m) => m.color?.getHexString() === new THREE.Color("#f2f1ed").getHexString()));
  assert.deepEqual([...fronts(dark)], [...fronts(scandi)], "the same fronts");
  const own = fx.kitchenRun({ sink: 1.2, hob: 2.4, tall: [{ at: 3.3, width: 0.6, oven: true }], handles: "none", fittings: "chrome" });
  assert.equal(meshes(own, named("matte black")).length, 0, "the piece's own options win");
});

test("a rug given no colour is the style's, a colour wins", () => {
  const colour = (r) => meshes(r, () => true)[0].material.color.getHexString();
  const sand = colour(fx.rug());
  setHouseStyle({ style: "contemporary" });
  const charcoal = colour(fx.rug());
  assert.notEqual(sand, charcoal);
  assert.equal(colour(fx.rug({ color: "#d8d2c5" })), sand, "the scene's colour");
  assert.equal(colour(fx.rug({ rug: "sand" })), sand, "the piece's own rug");
  setHouseStyle({ style: "contemporary", rooms: { Salon: { rug: "sand" } } });
  assert.equal(colour(fx.rug({ room: "Salon" })), sand, "a room's override");
});

// a 5 x 4 living room (x 0..5, z 0..4) with a parametric sofa against its north wall
function livingRoom({ cushion = false } = {}) {
  const root = new THREE.Group();
  const room = { name: "Séjour", use: "living", polygon: [[0, 0], [5, 0], [5, 4], [0, 4]] };
  const plan = interior.floorPlan({ y: 0, rooms: [room], partitions: [] });
  root.add(plan);
  const sofa = fx.onWall(room.polygon, 0, 2.5, fx.sofa({ width: 2.2 }), { y: 0.015 });
  root.add(sofa);
  if (cushion) {
    const c = new THREE.Mesh(new THREE.BoxGeometry(0.45, 0.45, 0.15), fx.finish.weave("#aa3333"));
    c.name = "red cushion";
    c.position.set(2.0, 0.7, 0.35);
    root.add(c);
  }
  return { root, sofa };
}
const dressings = (root) => {
  const found = [];
  root.traverse((o) => { if (o.userData.kind === "sofaDressing") found.push(o); });
  return found;
};

test("contemporary dresses every sofa with olive and sand cushions and a wool throw, once; not one the scene dressed", () => {
  const plain = livingRoom();
  interior.roomDetails(plain.root);
  assert.equal(dressings(plain.root).length, 0, "scandinavian adds nothing");

  setHouseStyle({ style: "contemporary" });
  const { root, sofa } = livingRoom();
  interior.roomDetails(root);
  const [d] = dressings(root);
  assert.ok(d, "a dressing");
  assert.equal(d.children.length, 4, "three cushions and a throw");
  root.updateMatrixWorld(true);
  const seat = new THREE.Box3().setFromObject(sofa);
  for (const c of d.children) {
    const b = new THREE.Box3().setFromObject(c);
    assert.ok(b.min.x > seat.min.x && b.max.x < seat.max.x && b.min.z > seat.min.z - 0.01 && b.max.z < seat.max.z + 0.06, "on the sofa");
    assert.ok(b.max.y > 0.015 + 0.48 && b.min.y < 0.015 + 0.48 + 0.05, `on its seat: ${b.min.y.toFixed(2)}..${b.max.y.toFixed(2)}`);
  }
  assert.equal(interior.roomDetails(root), 0, "a second pass adds nothing");

  const own = livingRoom({ cushion: true });
  interior.roomDetails(own.root);
  assert.equal(dressings(own.root).length, 0, "the scene's own cushion");
  setHouseStyle({ style: "contemporary", rooms: { "Séjour": { sofaDressing: "none" } } });
  const off = livingRoom();
  interior.roomDetails(off.root);
  assert.equal(dressings(off.root).length, 0, "a room's override");
});

test("a style draws a named model as another or as a role; the house can keep the name", async () => {
  const warn = console.warn;
  console.warn = () => {};
  try {
    setHouseStyle({ style: "contemporary" });
    const table = await fx.model("coffee-table-oval-white", { width: 0.8 });
    assert.equal(table.userData.styledFrom, undefined, "the oval pair keeps its white table");
    const chair = await fx.model("dining-chair-grey", { width: 0.44 });
    assert.equal(chair.userData.styledFrom, "dining-chair-grey", "a dark chair (the parametric one in Node)");
    setHouseStyle({ style: "contemporary", models: { "dining-chair-grey": "dining-chair-grey" } });
    assert.equal((await fx.model("dining-chair-grey")).userData.styledFrom, undefined);
    setHouseStyle({ models: { "vase-white": "vase" } });
    const vase = await fx.model("vase-white", { height: 0.3 });
    assert.equal(vase.userData.name, "vase");
    setHouseStyle({});
    assert.equal((await fx.model("coffee-table-oval-white")).userData.styledFrom, undefined, "scandinavian keeps it");
  } finally {
    console.warn = warn;
  }
});

test("contemporary's catalogue tables, chairs and sideboards are dark first; scandinavian's any quiet tone", () => {
  globalThis.__housekitSeed = "tones";
  try {
    for (const role of ["dining-chair", "coffee-table", "side-table", "sideboard"]) {
      setHouseStyle({ style: "contemporary" });
      resetObjects();
      assert.equal(pickObject(role).piece.tone, "dark", role);
      setHouseStyle({});
      resetObjects();
      assert.ok(["neutral", "dark"].includes(pickObject(role).piece.tone), role);
    }
    setHouseStyle({ style: "contemporary" });
    resetObjects();
    assert.ok(["neutral", "dark"].includes(pickObject("armchair").piece.tone), "other roles as before");
  } finally {
    delete globalThis.__housekitSeed;
    resetObjects();
  }
});
