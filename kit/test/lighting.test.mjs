// housekit/lighting: every registered fixture family follows the contract (tags, frame, its own
// light, a web budget, every finish), and the daytime rule switches the right ones. No renderer.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import { FIXTURES, FINISHES, fixture, applyLampMode, fixtureReference, isOn } from "../lighting.js";

// the audit classes a piece as a lamp by its name (layout.js pieceType)
const LAMP_NAME = /lamp|light|pendant|sconce|chandelier|luminaire|lustre/;
// triangles per fixture: they are small and many per storey
const MAX_TRIANGLES = 20000;

function box(o) {
  o.updateMatrixWorld(true);
  const b = new THREE.Box3();
  o.traverse((c) => { if (c.isMesh) b.expandByObject(c); });
  return b;
}
function triangles(o) {
  let n = 0;
  o.traverse((c) => {
    if (!c.isMesh) return;
    const g = c.geometry;
    n += (g.index ? g.index.count : g.attributes.position.count) / 3;
  });
  return n;
}
const lights = (o) => { const l = []; o.traverse((c) => { if (c.isLight) l.push(c); }); return l; };

test("every family builds with its defaults, tagged as a lamp with its family, role and finish", () => {
  for (const [name, spec] of Object.entries(FIXTURES)) {
    assert.ok(["ceiling", "wall", "standing"].includes(spec.kind), `${name}: kind ${spec.kind}`);
    assert.equal(typeof spec.doc, "string", `${name}: doc`);
    assert.ok(spec.doc.startsWith(name), `${name}: its doc starts with its name`);
    const f = fixture(name);
    const u = f.userData;
    assert.equal(u.kind, "furniture", name);
    assert.match(u.name, LAMP_NAME, `${name}: "${u.name}" must read as a lamp to the audit`);
    assert.equal(u.fixture.family, name);
    assert.ok(["ceiling", "accent"].includes(u.fixture.role), `${name}: role ${u.fixture.role}`);
    assert.ok(Array.isArray(u.footprint) && u.footprint.length === 2 && u.footprint.every((v) => v > 0), `${name}: footprint`);
    assert.ok(u.height > 0, `${name}: height`);
  }
});

test("each family sits in its frame: under the ceiling, in front of the wall, or on the floor", () => {
  for (const [name, spec] of Object.entries(FIXTURES)) {
    const b = box(fixture(name));
    assert.ok(!b.isEmpty(), `${name}: no mesh`);
    if (spec.kind === "ceiling") {
      assert.ok(b.max.y <= 0.002, `${name}: ${b.max.y.toFixed(3)} above its ceiling fixing`);
      assert.ok(b.min.y > -2.2, `${name}: hangs ${(-b.min.y).toFixed(2)} m`);
    } else if (spec.kind === "wall") {
      assert.ok(b.min.z >= -0.002, `${name}: ${(-b.min.z).toFixed(3)} m into the wall`);
      assert.ok(b.max.z < 0.8, `${name}: sticks out ${b.max.z.toFixed(2)} m`);
    } else {
      assert.ok(Math.abs(b.min.y) < 0.005, `${name}: bottom at ${b.min.y.toFixed(3)}`);
    }
  }
});

test("each family carries its light (no shadows) and stays within the triangle budget", () => {
  for (const name of Object.keys(FIXTURES)) {
    const f = fixture(name);
    const ls = lights(f);
    assert.ok(ls.length > 0, `${name}: no light`);
    for (const l of ls) {
      assert.equal(l.castShadow, false, `${name}: a light casts shadows`);
      assert.ok(l.isPointLight || l.isSpotLight, `${name}: ${l.type} (point or spot only)`);
      assert.ok(l.intensity > 0 && Number.isFinite(l.intensity), `${name}: intensity ${l.intensity}`);
      if (l.isSpotLight) {
        let inside = false;
        f.traverse((c) => { if (c === l.target) inside = true; });
        assert.ok(inside, `${name}: a spot's target must be in the fixture (it moves with it)`);
      }
    }
    const t = triangles(f);
    assert.ok(t <= MAX_TRIANGLES, `${name}: ${t} triangles (budget ${MAX_TRIANGLES})`);
  }
});

test("every finish builds for every family", () => {
  for (const name of Object.keys(FIXTURES)) {
    for (const finish of FINISHES) {
      const f = fixture(name, { finish });
      assert.ok(f.userData.fixture, `${name} in ${finish}`);
    }
  }
});

test("the daytime rule: by day only the accents shine, all lights everything, off nothing", () => {
  const group = new THREE.Group();
  const made = Object.keys(FIXTURES).map((n) => fixture(n));
  group.add(...made);
  for (const mode of ["day", "all", "off"]) {
    applyLampMode(group, mode);
    for (const f of made) {
      const on = isOn(f.userData.fixture.role, mode);
      for (const l of lights(f)) assert.equal(l.visible, on, `${f.userData.name} light in ${mode}`);
      f.traverse((c) => {
        if (!c.isMesh) return;
        for (const m of [c.material].flat()) {
          if (m.userData?.glow === undefined) continue;
          assert.equal(m.emissiveIntensity, on ? m.userData.glow : 0, `${f.userData.name} glow in ${mode}`);
        }
      });
    }
  }
  assert.equal(isOn("ceiling", "day"), false);
  assert.equal(isOn("accent", "day"), true);
});

test("the builder's reference names every family", () => {
  const ref = fixtureReference();
  for (const name of Object.keys(FIXTURES)) assert.ok(ref.includes(`${name}(`), name);
});
