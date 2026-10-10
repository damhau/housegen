// RLATC (VD 700.11.1) art. 25, 27, 28 on the habitable rooms of TestVillaGille v10.  usage: node rlatc.mjs <sia416.json> <out.json>
import fs from "node:fs";
const R = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const inside = (p, x, z) => { let c = false; for (let i = 0, j = p.length - 1; i < p.length; j = i++) { const [xi, zi] = p[i], [xj, zj] = p[j]; if ((zi > z) !== (zj > z) && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) c = !c; } return c; };
// roof windows (interior-rooflights.js specs): plan position, opening across the slope × along the ridge
const SKY = [[3.86, -2.625, 0.98, 1.34], [-3.86, -2.625, 0.98, 1.34], [3.86, 3, 0.98, 1.34], [-3.86, 3, 0.98, 1.34], [2.88, 0.15, 0.98, 0.78], [-3.86, 0.515, 0.98, 0.78]];
const hAttic = (x) => Math.min(4.16, 9.64 - 0.546 * Math.abs(x) - 5.48);
const rows = [];
const HABITABLE = new Set(["living", "dining", "kitchen", "kitchen-living", "bedroom", "office"]);
for (const s of R.storeys) {
  for (const r of s.rooms.filter((r) => HABITABLE.has(r.use))) {
    const win = r.openings.filter((o) => o.edge !== undefined && o.kind !== "porte" && o.kind !== "soupirail");
    const sky = s.key === "attic" ? SKY.filter(([x, z]) => inside(r.polygon, x, z)) : [];
    const aWin = win.reduce((a, o) => a + o.w * o.h, 0), aSky = sky.reduce((a, k) => a + k[2] * k[3], 0);
    // usable area (≥ 1.30 m), the part ≥ 2.40 m, and the volume counted from 1.30 m (grid 5 cm)
    let use = 0, high = 0, vol = 0;
    const xs = r.polygon.map((q) => q[0]), zs = r.polygon.map((q) => q[1]), c = 0.05;
    for (let x = Math.min(...xs) + c / 2; x < Math.max(...xs); x += c) for (let z = Math.min(...zs) + c / 2; z < Math.max(...zs); z += c) {
      if (!inside(r.polygon, x, z)) continue;
      const h = s.key === "attic" ? hAttic(x) : s.clear;
      if (h >= 1.3) { use += c * c; vol += c * c * h; }
      if (h >= 2.4) high += c * c;
    }
    const need = Math.max(use / 8, 1), light = aWin + aSky;
    const ok28 = light >= need, ok27 = s.key === "attic" ? high / use >= 0.5 : s.clear >= 2.4, ok25 = vol >= 20;
    rows.push({ no: r.no, name: r.name, flat: r.flat, attic: s.key === "attic", clear: s.clear, use, high: high / use, aWin, aSky, light, need, ratio: use / light, vol, ok25, ok27, ok28 });
    console.log(`${r.no} ${r.name.padEnd(18)} ${r.flat} use ${use.toFixed(2)} | art27 ${s.key === "attic" ? (100 * high / use).toFixed(0) + "% ≥2.40" : s.clear.toFixed(2) + " m"} ${ok27 ? "ok" : "NO"} | art28 baies ${aWin.toFixed(2)}${aSky ? " + tabatières " + aSky.toFixed(2) : ""} = ${light.toFixed(2)} / need ${need.toFixed(2)} (1/${(use / light).toFixed(1)}) ${ok28 ? "ok" : "NO"} | art25 ${vol.toFixed(1)} m³ ${ok25 ? "ok" : "NO"}`);
  }
}

// exterior works (site.js, garden.js, pools.js, dimensions.js)
const shoelace = (p) => Math.abs(p.reduce((a, [x1, z1], i) => { const [x2, z2] = p[(i + 1) % p.length]; return a + x1 * z2 - x2 * z1; }, 0)) / 2;
const len = ([a, b]) => Math.hypot(b[0] - a[0], b[1] - a[1]);
const plot = [[-17.8, -12.3], [-0.5, -13.5], [21.2, -13.5], [18.2, 12.1], [-20.5, 13.0]];
const paved = [[-5.5, 5, 5.5, 8], [-9.5, -6.8, -5.5, -0.8], [-5.5, -6.8, 6.95, -5], [5.5, -5, 7.25, 5]].map(([x0, z0, x1, z1]) => ({ w: x1 - x0, d: z1 - z0, a: (x1 - x0) * (z1 - z0) }));
const hedges = [[[-17.3, -11.9], [-0.5, -13.0], 0], [[-0.5, -13.0], [17.25, -13.0], 0], [[-17.3, -11.9], [-20.0, 12.4], 1], [[-20.0, 12.4], [15.0, 11.6], 1], [[15.0, 11.6], [17.3, 8.0], 1], [[17.3, 8.0], [18.45, 3.6], 0], [[18.45, 3.6], [17.0, -1.1], 0], [[17.0, -1.1], [17.3, -6.1], 0]];
const single = hedges.filter((h) => !h[2]).reduce((a, h) => a + len(h), 0), double = hedges.filter((h) => h[2]).reduce((a, h) => a + len(h), 0);
const exterior = { plot: shoelace(plot), paved, pavedTotal: paved.reduce((a, p) => a + p.a, 0), hedgeSingle: single, hedgeDouble: double, fence: single + double, divider: 12.3, trees: 3, treesKept: 1, pools: { n: 2, l: 6, w: 3, d: 1.5 } };
fs.writeFileSync(process.argv[3], JSON.stringify({ rlatc: rows, exterior }, null, 1));
console.log(JSON.stringify(exterior));
