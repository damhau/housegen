// The recorder's last step: what the furnishing and lighting code added to the house, written down
// as data. Each top-level object added since `before` becomes a piece: its call (a kit maker, or a
// function of the scene's own code with JSON arguments), its transform, its size and name. What was
// built some other way is listed as "unknown" (what data cannot express without more vocabulary).

const r = (v, n = 5) => Math.round(v * 10 ** n) / 10 ** n;
const arr = (v) => v.toArray().map((x) => r(x));

export function record(group, before) {
  const pieces = [], unknown = [];
  let n = 0;
  for (const o of group.children) {
    if (before.has(o)) continue;
    const u = o.userData ?? {}, s = u.spec;
    const base = {
      id: `p${++n}`,
      name: u.name ?? (o.name || o.type),
      position: arr(o.position), quaternion: arr(o.quaternion), scale: arr(o.scale),
      ...(u.footprint ? { footprint: u.footprint.map((x) => r(x, 3)) } : {}),
      ...(u.height !== undefined ? { height: r(u.height, 3) } : {}),
      ...(u.fixture ? { role: u.fixture.role } : {}),
    };
    if (s && !s.opaque) {
      const { kit, code, args } = s;
      pieces.push({ ...base, ...(kit ? { kit } : { code }), args, ...(u.post ? { post: u.post } : {}) });
    } else if (o.isLight) {
      pieces.push({ ...base, light: { type: o.type, color: `#${o.color.getHexString()}`, intensity: r(o.intensity, 4), distance: o.distance, decay: o.decay } });
    } else {
      let meshes = 0, triangles = 0;
      o.traverse((c) => {
        if (!c.isMesh) return;
        meshes++;
        const g = c.geometry;
        triangles += (g.index ? g.index.count : g.attributes.position.count) / 3;
      });
      unknown.push({ ...base, type: o.type, spec: s ?? null, meshes, triangles });
    }
  }
  // the floor plans (built by the structure's code): their definitions, for the checks
  const floorPlans = [];
  group.traverse((o) => { if (o.userData?.spec?.kit === "floorPlan") floorPlans.push(o.userData.spec.args?.[0] ?? null); });
  return { format: "housegen-interior/0", pieces, unknown, floorPlans };
}
