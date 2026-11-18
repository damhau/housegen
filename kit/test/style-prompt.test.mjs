// The interior prompt's line of style roles (agent/prompts.py "Roles and their names") lists only names the kit's
// catalogues hold (#76): a name the kit drops or renames would otherwise reach the builder and draw the fallback.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { MAPS, ROLES } from "../styles.js";
import { FINISHES } from "../lighting-parts.js";
import { FLOOR_TILES, WALL_TILES } from "../finishes.js";
import { BEDDING, FITTINGS, FURNITURE_TONES, HANDLES, RUGS, SOFA_DRESSINGS } from "../furnish.js";
import { FEATURE_WALLS } from "../interior.js";

const finishes = Object.fromEntries(FINISHES.map((f) => [f, true]));
const CATALOGUES = {
  fixtures: finishes,
  builtIns: finishes,
  fittings: FITTINGS,
  wallTile: WALL_TILES,
  bathFloor: FLOOR_TILES,
  bedding: BEDDING,
  featureWall: FEATURE_WALLS,
  handles: HANDLES,
  rug: RUGS,
  sofaDressing: SOFA_DRESSINGS,
  furnitureTone: FURNITURE_TONES,
};

function promptRoles() {
  const prompts = readFileSync(new URL("../../backend/src/housegen/agent/prompts.py", import.meta.url), "utf8");
  const start = prompts.indexOf("Roles and their names:");
  assert.ok(start >= 0, "the prompt's style roles line is gone");
  const text = prompts.slice(start + "Roles and their names:".length, prompts.indexOf("A piece's own option", start));
  const out = {};
  for (const part of text.replace(/\s+/g, " ").split(" · ")) {
    const m = part.trim().match(/^([A-Za-z ]+?)\s+((?:"[^"]+"\|?)+)/);
    if (!m) continue; // models { … }: a map, not a list of names
    const names = [...m[2].matchAll(/"([^"]+)"/g)].map((x) => x[1]);
    for (const role of m[1].split(" and ")) out[role.trim()] = names;
  }
  return out;
}

test("every style name the interior prompt lists is in its role's catalogue", () => {
  const roles = promptRoles();
  assert.ok(Object.keys(roles).length >= 10, `only ${Object.keys(roles).join(", ")} parsed`);
  for (const [role, names] of Object.entries(roles)) {
    assert.ok(role in ROLES && !MAPS.has(role), `the prompt lists "${role}", which is not a style role`);
    for (const name of names) assert.ok(name in CATALOGUES[role], `prompt: ${role} "${name}" is not in its catalogue`);
  }
});
