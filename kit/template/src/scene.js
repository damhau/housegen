// Entry point of the scene. The runtime calls buildScene(ctx) once.
//   ctx.house  — the housekit component library (walls, windows, roofs, garden…)
//   ctx.group  — add everything that belongs to the house/site here
//   ctx.THREE  — three.js
// Units: metres. +x east, +z south, +y up. Ground is y = 0.
//
// Keep this file small: put the shell in shell.js, openings in openings.js,
// the garden in garden.js, and import them here.

import { buildShell } from "./shell.js";
import { buildGarden } from "./garden.js";

export async function buildScene(ctx) {
  buildShell(ctx);
  buildGarden(ctx);
  // Optional custom camera views: { name: { position: [x,y,z], target: [x,y,z] } }
  return { views: {} };
}
