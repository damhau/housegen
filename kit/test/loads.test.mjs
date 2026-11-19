// housekit: the same scene draws the same pieces however its models arrive (#127). A page asks for many pieces at once
// (dressing.js dresses every room in one go) and their models load in any order, so every pick from a role's deck is
// taken when the piece is asked for, before anything loads: else two loads of a page swap a vase's stems, or a plant's
// variant, and the viewer, the share page and the listing photos disagree.
import { test } from "node:test";
import assert from "node:assert/strict";
import fx, { resetObjects } from "../furnish.js";
import { setHouseStyle } from "../styles.js";
import { stubCatalogue } from "./stub-models.mjs";

// slow: the files arrive in the reverse of the order they are asked for; a plant's leaves come as five variants side
// by side, as the catalogue's Poly Haven plants do
let slow = false;
stubCatalogue({
  delay: (n) => (slow ? Math.max(0, 160 - 10 * n) : 0),
  nodes: (o) => (o.role === "foliage" ? ["a", "b", "c", "d", "e"].map((v) => `leaves_${v}`) : [null]),
});

// what a piece draws of the catalogue: its pieces' ids and variants
function drawn(p) {
  const out = [];
  p.traverse((o) => { if (o.userData?.catalogue) out.push(`${o.userData.catalogue}${o.userData.variant ? `/${o.userData.variant}` : ""}`); });
  return out.join(" + ");
}

// the pieces `calls` ([name, options]) draw on a fresh page: asked for all at once, or each once the one before is drawn
async function draw(calls, together) {
  setHouseStyle({ style: "scandinavian" });
  globalThis.__housekitSeed = "fa8bd00053e6";
  resetObjects();
  if (together) return (await Promise.all(calls.map(([name, opts]) => fx.model(name, opts)))).map(drawn);
  const out = [];
  for (const [name, opts] of calls) out.push(drawn(await fx.model(name, opts)));
  return out;
}

test("vases dressed at once hold the same stems however their models arrive", async () => {
  // the dressing's vases: on a bedside, a table, a sideboard, a floor vase
  const calls = [0.17, 0.24, 0.3, 0.17, 0.24, 0.3, 0.46].map((height, i) => ["vase", { height, stems: true, room: `Room ${i % 3}`, ...(height > 0.4 ? { spread: 0.5 } : {}) }]);
  slow = true; // nothing loaded yet: the vases asked for first arrive last
  const first = await draw(calls, true);
  slow = false;
  const again = await draw(calls, false);
  assert.ok(first.every((d) => d.includes(" + ")), `every vase holds stems: ${first.join(", ")}`);
  assert.deepEqual(first, again);
});

test("plants asked for at once take the same leaves as plants asked for one after another", async () => {
  // more plants than the style has leaves: each is drawn several times, in its variants
  const calls = [...Array(15)].map((_, i) => ["pot-plant", { room: `Room ${i % 2}` }]);
  const together = await draw(calls, true);
  const apart = await draw(calls, false);
  assert.ok(together.every((d) => /\/[a-e]$/.test(d)), `every plant in one of its variants: ${together.join(", ")}`);
  assert.deepEqual(together, apart);
});
