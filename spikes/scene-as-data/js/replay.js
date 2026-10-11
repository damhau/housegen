// The interpreter (spike "scene as data"): builds the furnishing and lighting from interior.json
// with the kit, instead of running the code that made them. A piece is a kit call
// ({ kit: "wc", args }), a function of the scene's own code ({ code: { module, fn }, args }) or a
// light; then its transform, then the post-processing steps its record names.
// __interior keeps the pieces by id: the edits (edits.js) change one and rebuild only it.
import fx from "housekit/furnish";
import { floorPlan, tileWalls } from "housekit/interior";
import { CODE, POST } from "./replay-code.js";

export async function build(ctx, p) {
  let o;
  if (p.light) {
    const L = new ctx.THREE[p.light.type](p.light.color, p.light.intensity, p.light.distance, p.light.decay);
    o = L;
  } else if (p.kit === "tileWalls") o = tileWalls(ctx.group, ...p.args);
  else if (p.kit === "floorPlan") o = floorPlan(...p.args);
  else if (p.kit) o = await fx[p.kit](...p.args);
  else o = await CODE[p.code.module][p.code.fn](...p.args);
  o.position.fromArray(p.position);
  o.quaternion.fromArray(p.quaternion);
  o.scale.fromArray(p.scale);
  return o;
}

export async function replay(ctx, data) {
  const t0 = performance.now();
  const byId = new Map();
  for (const p of data.pieces) {
    const o = await build(ctx, p);
    ctx.group.add(o);
    for (const step of p.post ?? []) POST[step](o);
    byId.set(p.id, o);
  }
  window.__interior = { data, byId, ctx, ms: performance.now() - t0, build: (p) => build(ctx, p) };
}
