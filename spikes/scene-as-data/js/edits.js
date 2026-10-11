// The edits of the spike, run in the page on the data-built house (window.__interior): each changes
// the data of one or more pieces and rebuilds only those, then the frame is drawn again.
// The runtime's lamp pools keep the lights they found at load (same places): a rebuilt fixture's
// own lights stay hidden, its glowing parts follow the daytime rule.

const I = () => window.__interior;

export async function swap(id, change) {
  const { data, byId, build } = I();
  const p = data.pieces.find((x) => x.id === id);
  change(p);
  const old = byId.get(id);
  const o = await build(p);
  const { applyLampMode } = await import("/kit/lighting.js");
  applyLampMode(o, "day");
  o.traverse((c) => { if (c.isLight) c.visible = false; });
  old.parent.add(o);
  old.parent.remove(old);
  byId.set(id, o);
  return o;
}

export function move(id, dx, dz) {
  const { data, byId } = I();
  const p = data.pieces.find((x) => x.id === id);
  p.position[0] += dx;
  p.position[2] += dz;
  byId.get(id).position.fromArray(p.position);
}

export async function audit() {
  const m = await import("/kit/layout.js");
  const L = m.layout(I().ctx.group);
  return m.auditLines(L);
}
