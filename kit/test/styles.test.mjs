// housekit styles (#99): every style names an entry of each role's catalogue, the precedence piece > room > house >
// style > fallback, fixtures never brass unless asked, and what the contemporary style changes in a bedroom.
import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import * as house from "../house.js";
import { DEFAULT_STYLE, FALLBACK, ROLES, STYLES, look, setHouseStyle, styleName } from "../styles.js";
import { FINISHES } from "../lighting-parts.js";
import { FLOOR_TILES, WALL_TILES } from "../finishes.js";
import fx, { BEDDING, FITTINGS } from "../furnish.js";
import { FIXTURES } from "../lighting.js";
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
};

test("every style, and the fallback, names an existing entry for every role", () => {
  assert.deepEqual(Object.keys(CATALOGUES).sort(), Object.keys(ROLES).sort());
  for (const [name, style] of [...Object.entries(STYLES), ["fallback", FALLBACK]]) {
    for (const role of Object.keys(ROLES)) {
      const v = style[role];
      assert.ok(v != null, `${name}: no ${role}`);
      const names = typeof v === "object" ? Object.values(v) : [v];
      if (typeof v === "object") assert.ok("default" in v, `${name}.${role}: a value per kind has a default`);
      for (const n of names) assert.ok(n in CATALOGUES[role], `${name}.${role}: "${n}" is not in its catalogue`);
    }
  }
  for (const kind of Object.keys(STYLES.scandinavian.fixtures).filter((k) => k !== "default")) assert.ok(kind in FIXTURES, kind);
  assert.ok(DEFAULT_STYLE in STYLES);
});

test("no style: the default style; a house style, a room override and a piece override win in that order", () => {
  assert.equal(styleName(), "scandinavian");
  assert.equal(look("fittings"), "chrome");
  assert.equal(look("fixtures", { kind: "globePendant" }), "white");
  assert.equal(look("fixtures", { kind: "hexSconce" }), "black");

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
