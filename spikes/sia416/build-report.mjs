// Builds the mock report page (HTML) from sia416.json.  usage: node build-report.mjs <in.json> <out.html>
import fs from "node:fs";

const [, , inFile, outFile, extraFile] = process.argv;
const R = JSON.parse(fs.readFileSync(inFile, "utf8"));
const X = JSON.parse(fs.readFileSync(extraFile, "utf8")); // RLATC checks and exterior works (rlatc.mjs)

// ---- formatting (Swiss: apostrophe thousands, point decimals)
const n2 = (v) => { const [i, d] = Math.abs(v).toFixed(2).split("."); return (v < 0 ? "−" : "") + i.replace(/\B(?=(\d{3})+(?!\d))/g, "'") + "." + d; };
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const pct = (a, b) => { const p = ((a - b) / b) * 100; return (p > 0 ? "+" : p < 0 ? "−" : "") + Math.abs(p).toFixed(1) + " %"; };
const sum = (xs, f = (x) => x) => xs.reduce((a, x) => a + f(x), 0);

const S = R.storeys;
const allRooms = S.flatMap((s) => s.rooms.map((r) => ({ ...r, storey: s.label })));
const T = { sp: sum(S, (s) => s.sp), sn: sum(S, (s) => s.sn), sc: sum(S, (s) => s.sc), SUP: sum(S, (s) => s.SUP), SUS: sum(S, (s) => s.SUS), SD: sum(S, (s) => s.SD), SI: sum(S, (s) => s.SI) };
const V = R.volumes;
const vb = sum(V.storeys, (s) => s.v), vbUnder = sum(V.storeys.filter((s) => s.under), (s) => s.v);
const garage = { sp: 33, sn: (6 - 0.36) * (5.5 - 0.36), v: V.garage.v };
garage.sc = garage.sp - garage.sn;
const PAGES = 9;

// ---- the plan of a storey (SVG, metres; +x east, +z south as in the scene)
const CLASS = { SUP: "sup", SUS: "sus", SD: "sd", SI: "si" };
const VB_BOX = [-8.7, -7.4, 15.0, 14.0]; // the same scale on every storey
function planSVG(s) {
  const f = (v) => +v.toFixed(3);
  let g = "";
  // balconies (first floor), open: outside SP and VB
  if (s.key === "first") {
    g += `<path class="pl-balc" d="M-8 -6.8 H0.1 V-5 H-5.5 V-0.8 H-8 Z"/>`;
    g += `<rect class="pl-balc" x="0.62" y="5" width="4.5" height="2.5"/>`;
    g += `<text class="pl-note" x="-7.75" y="-6.25">balcon (hors SP)</text>`;
  }
  for (const r of s.rooms) {
    const pts = r.polygon.map(([x, z]) => `${f(x)},${f(z)}`).join(" ");
    g += `<polygon class="pl-room pl-${CLASS[r.sia]}" points="${pts}"/>`;
    if (r.use === "stair") g += `<polygon class="pl-stair" points="${pts}"/>`;
  }
  // exterior walls, with their openings cut out
  g += `<path class="pl-wall" fill-rule="evenodd" d="M-5.5 -5 H5.5 V5 H-5.5 Z M-5.1 -4.6 V4.6 H5.1 V-4.6 Z"/>`;
  for (const o of s.ext) {
    const [cx, cz] = o.at, w = o.w, horiz = o.edge % 2 === 0;
    const zf = o.edge === 0 ? -5 : 4.6, xf = o.edge === 1 ? 5.1 : -5.5;
    const x = horiz ? cx - w / 2 : xf, z = horiz ? zf : cz - w / 2;
    g += `<rect class="pl-gap" x="${f(x)}" y="${f(z)}" width="${f(horiz ? w : 0.4)}" height="${f(horiz ? 0.4 : w)}"/>`;
    if (o.kind !== "porte") g += horiz ? `<line class="pl-glass" x1="${f(x)}" y1="${f(z + 0.2)}" x2="${f(x + w)}" y2="${f(z + 0.2)}"/>` : `<line class="pl-glass" x1="${f(x + 0.2)}" y1="${f(z)}" x2="${f(x + 0.2)}" y2="${f(z + w)}"/>`;
  }
  // partitions: solid pieces between their openings
  for (const p of s.parts) {
    const dx = p.to[0] - p.from[0], dz = p.to[1] - p.from[1], L = Math.hypot(dx, dz), ux = dx / L, uz = dz / L;
    const cuts = [...p.openings].sort((a, b) => a.offset - b.offset);
    let t = 0;
    const pieces = [];
    for (const o of cuts) { if (o.offset > t) pieces.push([t, o.offset]); t = o.offset + o.width; }
    if (t < L) pieces.push([t, L]);
    for (const [a, b] of pieces) g += `<line class="${p.t >= 0.2 ? "pl-mass" : "pl-part"}" stroke-width="${p.t}" x1="${f(p.from[0] + ux * a)}" y1="${f(p.from[1] + uz * a)}" x2="${f(p.from[0] + ux * b)}" y2="${f(p.from[1] + uz * b)}"/>`;
  }
  // labels: the room's number and its net area
  for (const r of s.rooms) {
    const [x, z] = r.label.at, roomy = r.label.room > 0.62;
    g += `<text class="pl-no" x="${f(x)}" y="${f(z + (roomy ? -0.04 : 0.09))}">${r.no}</text>`;
    if (roomy) g += `<text class="pl-area" x="${f(x)}" y="${f(z + 0.3)}">${n2(r.sn)}</text>`;
  }
  // north (the plans' NW elevation is the scene's north side: up is north-west) and a 5 m bar
  g += `<g transform="translate(4.9 -6.4)"><circle class="pl-ring" r="0.55"/><g transform="rotate(45)"><path class="pl-north" d="M0 -0.5 L0.17 0.18 L0 0.06 L-0.17 0.18 Z"/></g><text class="pl-n" x="0.62" y="-0.5">N</text></g>`;
  g += `<g transform="translate(-8.2 6.1)"><rect class="pl-bar" x="0" y="0" width="2.5" height="0.12"/><rect class="pl-bar2" x="2.5" y="0" width="2.5" height="0.12"/><text class="pl-scale" x="0" y="0.48">0</text><text class="pl-scale" x="5" y="0.48" text-anchor="end">5 m</text></g>`;
  return `<svg class="plan" viewBox="${VB_BOX.join(" ")}" role="img" aria-label="Plan schématique, ${esc(s.label)} : locaux colorés par affectation SIA 416">${g}</svg>`;
}

// ---- the section used for the volume (across the roof slope)
function sectionSVG() {
  const y = (v) => +(-v).toFixed(3);
  const eave = V.roofEave; // 6.997
  let g = "";
  g += `<rect class="sx-ground" x="-6.6" y="0" width="15.6" height="3.6"/>`;
  g += `<rect class="sx-under" x="-5.5" y="${y(0)}" width="11" height="3.05"/>`;
  g += `<rect class="sx-above" x="-5.5" y="${y(2.74)}" width="11" height="2.74"/>`;
  g += `<rect class="sx-above" x="-5.5" y="${y(5.48)}" width="11" height="2.74"/>`;
  g += `<polygon class="sx-above sx-roof" points="-5.5,${y(5.48)} 5.5,${y(5.48)} 5.5,${y(eave)} 0,${y(10)} -5.5,${y(eave)}"/>`;
  g += `<path class="sx-over" d="M5.5 ${y(eave)} L6.3 ${y(10 - 0.546 * 6.3)} M-5.5 ${y(eave)} L-6.3 ${y(10 - 0.546 * 6.3)}"/>`;
  g += `<line class="sx-terrain" x1="-6.6" y1="0" x2="9" y2="0"/>`;
  for (const v of [2.74, 5.48]) g += `<line class="sx-slab" x1="-5.5" y1="${y(v)}" x2="5.5" y2="${y(v)}"/>`;
  // levels on the left
  const lv = [[10, "+10.00 faîte"], [5.48, "+5.48"], [2.74, "+2.74"], [0, "±0.00 (667.60)"], [-2.6, "−2.80"], [-3.25, "−3.05 radier"]];
  for (const [v, t] of lv) g += `<line class="sx-tick" x1="-6.9" y1="${y(v)}" x2="-5.6" y2="${y(v)}"/><text class="sx-lv" x="-7.1" y="${y(v) + 0.17}">${t}</text>`;
  // volumes on the right
  const labels = [[-1.5, V.storeys[0]], [1.37, V.storeys[1]], [4.11, V.storeys[2]], [7.2, V.storeys[3]]];
  for (const [v, st] of labels) g += `<text class="sx-v" x="0" y="${y(v) + 0.2}">${n2(st.v)} m³</text>`;
  g += `<text class="sx-cap" x="5.9" y="${y(-1.5) + 0.17}">sous-sol</text><text class="sx-cap" x="6.5" y="${y(4) + 0.17}">hors-sol</text>`;
  return `<svg class="section" viewBox="-13.4 -10.8 22.6 14.6" role="img" aria-label="Coupe schématique : volume bâti hors-sol et sous-sol">${g}</svg>`;
}

// ---- tables
const roomRows = (s) => s.rooms.map((r) => {
  const gap = r.plan ? (r.sn - r.plan) / r.plan : null;
  const flag = gap !== null && Math.abs(gap) > 0.05;
  return `<tr${flag ? ' class="flag"' : ""}><td class="mono no">${r.no}</td><td>${esc(r.name)}<span class="flatname">${r.flat}</span></td><td><span class="cls cls-${CLASS[r.sia]}">${r.sia}</span></td><td class="mono formula">${esc(r.formula)}</td><td class="num">${n2(r.sn)}</td><td class="num muted">${r.plan ? n2(r.plan) : "—"}</td><td class="num ${flag ? "warn" : "muted"}">${gap === null ? "" : pct(r.sn, r.plan)}</td></tr>`;
}).join("");

function storeyBlock(s) {
  return `<section class="storey" aria-labelledby="st-${s.key}">
  <div class="storey-head"><h3 id="st-${s.key}">${s.label}</h3><p class="mono small">niveau ${s.y >= 0 ? "+" : "−"}${Math.abs(s.y).toFixed(2)} · hauteur libre ${s.key === "attic" ? "sous pente, 1.38 à 4.16 m" : s.clear.toFixed(2) + " m"}</p></div>
  <figure class="plan-fig plan-big">${planSVG(s)}<figcaption>Schéma SIA 416 · ${s.label} · même échelle à chaque niveau</figcaption></figure>
  <div class="tablewrap"><table class="rooms">
      <thead><tr><th>N°</th><th>Local</th><th>SIA</th><th>Calcul</th><th class="num">SN m²</th><th class="num">Plan m²</th><th class="num">Écart</th></tr></thead>
      <tbody>${roomRows(s)}</tbody>
      <tfoot>
        <tr><td colspan="4">Surface nette SN</td><td class="num">${n2(s.sn)}</td><td colspan="2"></td></tr>
        <tr><td colspan="4">Surface de construction SC = SP − SN</td><td class="num">${n2(s.sc)}</td><td colspan="2"></td></tr>
        <tr class="strong"><td colspan="4">Surface de plancher SP <span class="mono small">11.00 × 10.00</span></td><td class="num">${n2(s.sp)}</td><td colspan="2"></td></tr>
      </tfoot>
  </table></div>
</section>`;
}

// ---- the finishes take-off (eCCC-Bât, CFC column, formula per line)
const lines = [];
const L = (eccc, cfc, what, where, formula, unit, qty) => lines.push({ eccc, cfc, what, where, formula, unit, qty });
const group = (title) => lines.push({ group: title });
const short = (no) => no;
group("C · Construction du bâtiment");
for (const s of S) {
  const c = s.parts.filter((p) => p.t >= 0.2);
  if (!c.length) continue;
  const g = sum(c, (p) => p.gross), h = sum(c, (p) => p.holes), doors = sum(c, (p) => p.n);
  L("C02", "211", "Parois porteuses intérieures, maçonnerie 25 cm", s.label, `Σ ${n2(sum(c, (p) => p.len))} m × ${s.clear.toFixed(2)}${doors ? ` − ${doors} ouv. ${n2(h)}` : ""}`, "m²", g - h);
}
group("E · Revêtements de façades et de murs contre terre");
L("E01", "225", "Étanchéité des murs contre terre", "Sous-sol", `42.00 × 2.80 · 4 soupiraux < 1 m² non déduits`, "m²", R.basementWalls.gross);
for (const f of R.facades) L("E02", "226", "Crépissage de façade", `Façade ${f.name}`, `${f.len.toFixed(2)} × 5.48 + ${f.topKind} ${n2(f.top)} − ${f.nHoles} ouv. ${n2(f.holes)}${f.nKeep ? ` · ${f.nKeep} < 1 m² non déd.` : ""}`, "m²", f.net);
const ext = S.flatMap((s) => s.ext);
const glazed = ext.filter((o) => o.kind !== "porte" && o.kind !== "soupirail"), doorsExt = ext.filter((o) => o.kind === "porte");
L("E03", "221", "Fenêtres et portes-fenêtres", "Toutes façades", `${glazed.length} pces · Σ l × h`, "m²", sum(glazed, (o) => o.w * o.h));
L("E03", "221", "Portes extérieures", "Rez", `${doorsExt.length} pces · Σ l × h`, "m²", sum(doorsExt, (o) => o.w * o.h));
group("G · Aménagements intérieurs");
for (const s of S) {
  const g01 = s.parts.filter((p) => p.t < 0.2);
  const gross = sum(g01, (p) => p.gross), h = sum(g01, (p) => p.holes), doors = sum(g01, (p) => p.n);
  L("G01", "271", "Cloisons légères 10–12 cm", s.label, s.key === "attic" ? `Σ ${n2(sum(g01, (p) => p.len))} m, hauteur sous pente − ${doors} portes ${n2(h)}` : `Σ ${n2(sum(g01, (p) => p.len))} m × ${s.clear.toFixed(2)} − ${doors} portes ${n2(h)}`, "m²", gross - h);
}
for (const s of S) {
  const byFloor = {};
  for (const r of s.rooms) (byFloor[r.floor] ??= []).push(r);
  for (const [floor, rs] of Object.entries(byFloor)) {
    if (floor === "concrete") { L("G02", "—", "Sol béton brut, sans revêtement", s.label, `locaux ${rs.map((r) => short(r.no)).join(", ")}`, "m²", sum(rs, (r) => r.sn)); continue; }
    L("G02", "281", floor === "tile" ? "Revêtement de sol : carrelage grès cérame" : "Revêtement de sol : parquet chêne clair", s.label, `Σ SN locaux ${rs.map((r) => short(r.no)).join(", ")}`, "m²", sum(rs, (r) => r.sn));
  }
}
for (const s of S) for (const r of s.rooms.filter((r) => r.tiles > 0)) {
  L("G03", "282", "Faïence murale", `${r.no} ${r.name}`, `périmètre × 1.20${r.tileFull ? ` (2.40 sur ${r.tileFull} mur${r.tileFull > 1 ? "s" : ""})` : ""}${s.key === "attic" ? ", sous pente" : ""} − ouv. ≥ 1 m²`, "m²", r.tiles);
}
for (const s of S) {
  const g = sum(s.rooms, (r) => r.wallsGross), h = sum(s.rooms, (r) => r.holes), t = sum(s.rooms, (r) => r.tiles);
  L("G03", "271 / 285", "Enduit plâtre et peinture des parois", s.label, `Σ parois ${n2(g)} − ouv. ${n2(h)}${t ? ` − faïence ${n2(t)}` : ""}`, "m²", g - h - t);
}
const smallKept = sum(S, (s) => sum(s.rooms, (r) => r.small));
const takeoffRows = lines.map((l) => l.group
  ? `<tr class="grp"><td colspan="9">${esc(l.group)}</td></tr>`
  : `<tr><td class="mono">${l.eccc}</td><td class="mono">${l.cfc}</td><td>${esc(l.what)}</td><td>${esc(l.where)}</td><td class="mono formula">${esc(l.formula)}</td><td>${l.unit}</td><td class="num">${n2(l.qty)}</td><td class="num muted">—</td><td class="num muted">—</td></tr>`).join("");

// ---- living area by flat (sale), the weighting is practice, not a norm
const flats = ["App. 1", "App. 2"].map((flat) => {
  const rs = allRooms.filter((r) => r.flat === flat);
  const living = rs.filter((r) => r.sia === "SUP" || r.use === "hall");
  const stairs = rs.filter((r) => r.use === "stair");
  const annex = rs.filter((r) => r.sia === "SUS");
  const balc = V.balconies.filter((b) => b.flat === flat);
  const hab = sum(living, (r) => r.sn), w = sum(balc, (b) => b.area * 0.5);
  return { flat, living, stairs, annex, balc, hab, w, total: hab + w };
});
const flatBlock = (F) => `<div class="flat">
  <h3>${F.flat}</h3>
  <div class="tablewrap"><table class="rooms compact">
    <thead><tr><th>N°</th><th>Local</th><th>Niveau</th><th class="num">m²</th></tr></thead>
    <tbody>${F.living.map((r) => `<tr><td class="mono">${r.no}</td><td>${esc(r.name)}</td><td>${r.storey}</td><td class="num">${n2(r.sn)}</td></tr>`).join("")}</tbody>
    <tfoot>
      <tr class="strong"><td colspan="3">Surface habitable nette</td><td class="num">${n2(F.hab)}</td></tr>
      ${F.balc.map((b) => `<tr><td colspan="3">${esc(b.name)} <span class="mono small">${n2(b.area)} × 50 %</span></td><td class="num">${n2(b.area * 0.5)}</td></tr>`).join("")}
      <tr class="strong"><td colspan="3">Surface pondérée</td><td class="num">${n2(F.total)}</td></tr>
    </tfoot>
  </table></div>
  <p class="small muted">Non comptés : escaliers ${F.stairs.map((r) => r.no).join(", ")} (${n2(sum(F.stairs, (r) => r.sn))} m²) ; annexes au sous-sol ${F.annex.map((r) => r.no).join(", ")} (${n2(sum(F.annex, (r) => r.sn))} m²).</p>
</div>`;

const flagged = allRooms.filter((r) => r.plan && Math.abs((r.sn - r.plan) / r.plan) > 0.05);
const withPlan = allRooms.filter((r) => r.plan);

// ---- the page
const header = (n, title) => `<header class="run"><span>housegen · Surfaces et volumes SIA 416</span><span>TestVillaGille · version 10</span></header><h2 class="page-title"><span class="page-no mono">${n}/${PAGES}</span>${title}</h2>`;
const footer = (n) => `<footer class="run"><span>Estimé sur la maquette 3D, non contractuel. Les surfaces lues sur les plans sont indiquées à côté.</span><span class="mono">${n}/${PAGES}</span></footer>`;

const html = `<title>Rapport SIA 416</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* Layout: a desk (themed) holding A4-proportioned sheets of paper (one look, the printed report). */
:root {
  --desk: #e6e9e6; --desk-ink: #26302d; --desk-muted: #5c6763; --desk-line: #c9cfcb;
  --paper: #ffffff; --ink: #1d2422; --muted: #5f6966; --rule: #d9dedb; --rule-strong: #1d2422;
  --accent: #1f5f7a; --warn: #a2471a; --warn-bg: #fbeee6; --ok: #2d6a45; --ok-bg: #e6f1ea; --field: #eef5f7;
  --sup: #f1dfb5; --sus: #d2dce6; --sd: #e5e3dc; --si: #e9c9bd; --balc: #f3f4f1;
  --font-body: "Archivo", "Helvetica Neue", Arial, sans-serif;
  --font-mono: "IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --desk: #161a19; --desk-ink: #e3e8e5; --desk-muted: #a3aeaa; --desk-line: #2c3331; color-scheme: dark; } }
:root[data-theme="dark"] { --desk: #161a19; --desk-ink: #e3e8e5; --desk-muted: #a3aeaa; --desk-line: #2c3331; color-scheme: dark; }
* { box-sizing: border-box; }
body { background: var(--desk); color: var(--desk-ink); font-family: var(--font-body); font-size: 14px; line-height: 1.45; padding-inline: 16px; padding-block: 28px 56px; }
.mono { font-family: var(--font-mono); }
.small { font-size: 11.5px; }
.desk-head { max-width: 860px; margin: 0 auto 22px; display: grid; gap: 8px; }
.desk-head h1 { font-size: 26px; line-height: 1.15; margin: 0; letter-spacing: -0.01em; text-wrap: balance; }
.desk-head p { margin: 0; color: var(--desk-muted); max-width: 68ch; }
.toc { display: flex; flex-wrap: wrap; gap: 6px 14px; margin-top: 4px; font-size: 13px; }
.toc a { color: var(--desk-ink); text-decoration: none; border-bottom: 1px solid var(--desk-line); padding-bottom: 1px; }
.toc a:hover, .toc a:focus-visible { border-color: var(--desk-ink); outline: none; }
.sheet { background: var(--paper); color: var(--ink); max-width: 860px; margin: 0 auto 26px; padding: 34px 44px 26px; box-shadow: 0 1px 2px rgb(0 0 0 / .12), 0 8px 28px rgb(0 0 0 / .10); display: flex; flex-direction: column; gap: 18px; min-height: 1100px; }
.sheet > .grow { flex: 1; display: flex; flex-direction: column; gap: 18px; }
.run { display: flex; justify-content: space-between; gap: 12px; font-size: 10.5px; color: var(--muted); letter-spacing: .02em; flex-wrap: wrap; }
header.run { border-bottom: 1px solid var(--rule); padding-bottom: 8px; text-transform: uppercase; letter-spacing: .08em; }
footer.run { border-top: 1px solid var(--rule); padding-top: 8px; }
.page-title { font-size: 21px; margin: 0; display: flex; align-items: baseline; gap: 12px; text-wrap: balance; }
.page-no { font-size: 12px; color: var(--muted); font-weight: 500; }
h3 { font-size: 15px; margin: 0; }
p { margin: 0; }
.lead { max-width: 68ch; color: var(--muted); }
.cover { display: grid; grid-template-columns: 1.2fr 1fr; gap: 24px; align-items: start; }
.cover dl { display: grid; grid-template-columns: auto 1fr; gap: 4px 14px; margin: 0; font-size: 13px; }
.cover dt { color: var(--muted); }
.cover dd { margin: 0; }
.big { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0; border-top: 2px solid var(--rule-strong); border-bottom: 1px solid var(--rule); }
.big div { padding: 10px 12px 10px 0; display: grid; gap: 2px; }
.big div + div { padding-left: 14px; border-left: 1px solid var(--rule); }
.big b { font-size: 24px; font-variant-numeric: tabular-nums; letter-spacing: -0.01em; }
.big span { font-size: 11.5px; color: var(--muted); }
.tablewrap { overflow-x: auto; min-width: 0; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: 5px 8px 5px 0; vertical-align: top; }
thead th { font-size: 10.5px; text-transform: uppercase; letter-spacing: .07em; color: var(--muted); font-weight: 600; border-bottom: 1px solid var(--rule-strong); white-space: nowrap; }
tbody td { border-bottom: 1px solid var(--rule); }
tfoot td { padding-top: 6px; }
tr.strong td { font-weight: 700; border-top: 1px solid var(--rule-strong); }
.num { text-align: right; white-space: nowrap; }
td.num:last-child, th.num:last-child { padding-right: 0; }
.muted { color: var(--muted); }
.warn { color: var(--warn); font-weight: 600; }
tr.flag td { background: var(--warn-bg); }
.formula { font-size: 11.5px; color: var(--muted); }
table.quant td:first-child { width: 46%; }
table.quant .sub td:first-child { padding-left: 16px; color: var(--muted); }
table.quant .code { font-family: var(--font-mono); font-size: 11.5px; color: var(--muted); }
.cls { display: inline-block; font-family: var(--font-mono); font-size: 10.5px; padding: 0 5px; border-radius: 2px; color: var(--ink); }
.cls-sup { background: var(--sup); } .cls-sus { background: var(--sus); } .cls-sd { background: var(--sd); } .cls-si { background: var(--si); }
.legend { display: flex; flex-wrap: wrap; gap: 6px 16px; font-size: 12px; color: var(--muted); }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.legend i { width: 14px; height: 10px; display: inline-block; border: 1px solid rgb(0 0 0 / .12); }
.note { border-left: 3px solid var(--accent); padding: 8px 12px; background: #f4f8f9; font-size: 12.5px; display: grid; gap: 4px; }
.note.warnbox { border-color: var(--warn); background: var(--warn-bg); }
.note b { font-weight: 600; }
.storey { display: grid; gap: 10px; }
.storey + .storey { border-top: 1px solid var(--rule); padding-top: 18px; }
.storey-head { display: flex; justify-content: space-between; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.plan-big { max-width: 560px; width: 100%; justify-self: center; }
table.rooms td.no { font-size: 12px; white-space: nowrap; }
.flatname { display: block; font-size: 11.5px; color: var(--muted); }
.plan-fig { margin: 0; display: grid; gap: 4px; }
.plan-fig figcaption { font-size: 10.5px; color: var(--muted); text-transform: uppercase; letter-spacing: .07em; }
svg.plan { width: 100%; height: auto; display: block; font-family: var(--font-body); }
.pl-room { stroke: none; } .pl-sup { fill: var(--sup); } .pl-sus { fill: var(--sus); } .pl-sd { fill: var(--sd); } .pl-si { fill: var(--si); }
.pl-stair { fill: url(#hatch); }
.pl-balc { fill: var(--balc); stroke: var(--muted); stroke-width: 0.03; stroke-dasharray: 0.15 0.1; }
.pl-wall { fill: var(--ink); }
.pl-gap { fill: var(--paper); }
.pl-glass { stroke: var(--ink); stroke-width: 0.035; }
.pl-mass { stroke: var(--ink); } .pl-part { stroke: #4a5350; }
.pl-no { font-size: 0.34px; font-weight: 700; fill: var(--ink); text-anchor: middle; }
.pl-area { font-size: 0.28px; fill: var(--muted); text-anchor: middle; font-variant-numeric: tabular-nums; }
.pl-note { font-size: 0.32px; fill: var(--muted); }
.pl-ring { fill: none; stroke: var(--muted); stroke-width: 0.03; } .pl-north { fill: var(--ink); } .pl-n { font-size: 0.38px; font-weight: 700; fill: var(--ink); }
.pl-bar { fill: var(--ink); } .pl-bar2 { fill: none; stroke: var(--ink); stroke-width: 0.03; } .pl-scale { font-size: 0.32px; fill: var(--muted); }
table.rooms td:nth-child(2) { min-width: 9em; }
table.compact td, table.compact th { padding-block: 4px; }
.vol { display: grid; grid-template-columns: 1.15fr 1fr; gap: 22px; align-items: start; }
.vol > * { min-width: 0; }
svg.section { width: 100%; height: auto; display: block; font-family: var(--font-body); }
.sx-ground { fill: #efeee8; } .sx-under { fill: var(--sus); stroke: var(--ink); stroke-width: 0.06; } .sx-above { fill: var(--sup); stroke: var(--ink); stroke-width: 0.06; }
.sx-over { stroke: var(--muted); stroke-width: 0.06; stroke-dasharray: 0.2 0.14; fill: none; }
.sx-terrain { stroke: var(--ink); stroke-width: 0.09; } .sx-slab { stroke: var(--ink); stroke-width: 0.05; }
.sx-tick { stroke: var(--muted); stroke-width: 0.03; } .sx-lv { font-size: 0.5px; fill: var(--muted); text-anchor: end; font-family: var(--font-mono); }
.sx-v { font-size: 0.6px; font-weight: 700; fill: var(--ink); text-anchor: middle; } .sx-cap { font-size: 0.5px; fill: var(--muted); }
table.takeoff td:nth-child(1), table.takeoff td:nth-child(2) { white-space: nowrap; font-size: 12px; } table.takeoff td:nth-child(3) { min-width: 12em; } table.takeoff td:nth-child(5) { min-width: 16em; }
table.takeoff tr.grp td { padding-top: 12px; font-weight: 700; font-size: 12px; border-bottom: 1px solid var(--rule-strong); }
.rules { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px 22px; font-size: 12.5px; }
.rules > div { display: grid; gap: 2px; min-width: 0; }
.rules b { font-weight: 600; }
.flats { display: grid; grid-template-columns: 1fr 1fr; gap: 22px; }
.flats > * { min-width: 0; }
.flat { display: grid; gap: 8px; align-content: start; }
.est { display: grid; gap: 8px; }
.est + .est { border-top: 1px solid var(--rule); padding-top: 16px; }
.est-head { display: flex; justify-content: space-between; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.field { width: 7.5em; font: inherit; font-variant-numeric: tabular-nums; color: var(--ink); background: var(--field); border: 0; border-bottom: 1.5px solid var(--accent); border-radius: 2px 2px 0 0; padding: 3px 6px; text-align: right; }
.field::placeholder { color: var(--muted); opacity: .8; }
.field:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.presets { display: inline-flex; gap: 4px; flex-wrap: wrap; }
.presets button { font: inherit; font-size: 12px; color: var(--ink); background: var(--paper); border: 1px solid var(--rule); border-radius: 3px; padding: 3px 9px; cursor: pointer; }
.presets button[aria-pressed="true"] { border-color: var(--accent); background: var(--field); color: var(--accent); font-weight: 600; }
.presets button:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
td.amount { font-weight: 600; }
td.amount.empty { color: var(--muted); font-weight: 400; }
table.est-t td:nth-child(2) { min-width: 13em; }
table.est-t tr.grp td { padding-top: 10px; font-size: 12px; border-bottom: 1px solid var(--rule-strong); }
table.est-t td .field { margin-block: -3px; }
tr.total td { font-weight: 700; border-top: 1.5px solid var(--rule-strong); }
.balance { display: grid; grid-template-columns: repeat(3, 1fr); border-top: 2px solid var(--rule-strong); border-bottom: 1px solid var(--rule); }
.balance div { padding: 10px 12px 10px 0; display: grid; gap: 2px; }
.balance div + div { padding-left: 14px; border-left: 1px solid var(--rule); }
.balance b { font-size: 20px; font-variant-numeric: tabular-nums; }
.balance span { font-size: 11.5px; color: var(--muted); }
.st { display: inline-block; font-size: 11px; font-weight: 600; padding: 0 6px; border-radius: 2px; white-space: nowrap; }
.st-ok { color: var(--ok); background: var(--ok-bg); } .st-warn { color: var(--warn); background: var(--warn-bg); }
@media (max-width: 760px) {
  .balance { grid-template-columns: 1fr; } .balance div + div { border-left: 0; padding-left: 0; border-top: 1px solid var(--rule); }
  .sheet { padding: 20px 16px 16px; min-height: 0; }
  .cover, .vol, .rules, .flats { grid-template-columns: 1fr; }
  .big { grid-template-columns: 1fr; } .big div + div { border-left: 0; padding-left: 0; border-top: 1px solid var(--rule); }
}
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto; } }
</style>

<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs><pattern id="hatch" patternUnits="userSpaceOnUse" width="0.22" height="0.22" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="0.22" stroke="#8b938f" stroke-width="0.025"/></pattern></defs></svg>

<div class="desk-head">
  <h1>Rapport SIA 416, maquette</h1>
  <p>La mise en page du rapport que housegen produira (#47, #48), calculée ici sur TestVillaGille, version 10 sur dev : les surfaces, plans, volumes et quantités viennent de la maquette 3D de la villa. À la page 8, les prix se saisissent et l'estimation se recalcule. Le rapport final se télécharge en PDF et en CSV.</p>
  <nav class="toc" aria-label="Pages du rapport"><a href="#p1">1 Récapitulatif</a><a href="#p2">2–5 Surfaces par niveau</a><a href="#p6">6 Volume bâti</a><a href="#p7">7 Métré des finitions</a><a href="#p8">8 Estimation</a><a href="#p9">9 Surface habitable</a></nav>
</div>

<article class="sheet" id="p1">
  ${header(1, "Quantités de base selon SIA 416")}
  <div class="grow">
    <div class="cover">
      <div style="display:grid;gap:10px">
        <p class="lead">Récapitulatif des surfaces et volumes selon la norme SIA 416 (édition 2003), sur le modèle de la fiche L9 du Guide romand des marchés publics. Les calculs sont détaillés par niveau (pages 2 à 5), chacun avec son schéma.</p>
        <div class="legend" aria-label="Affectations SIA 416"><span><i style="background:var(--sup)"></i>SUP utile principale</span><span><i style="background:var(--sus)"></i>SUS utile secondaire</span><span><i style="background:var(--sd)"></i>SD dégagement</span><span><i style="background:var(--si)"></i>SI installations</span></div>
      </div>
      <dl>
        <dt>Projet</dt><dd>TestVillaGille, villa de deux appartements</dd>
        <dt>Parcelle</dt><dd>BF n° 3013, Le Mont-sur-Lausanne</dd>
        <dt>Source</dt><dd>maquette 3D, version 10</dd>
        <dt>Date</dt><dd class="mono">10.10.2026</dd>
        <dt>Norme</dt><dd>SIA 416:2003, Surfaces et volumes des bâtiments</dd>
      </dl>
    </div>
    <div class="big" role="group" aria-label="Chiffres principaux, objet 1">
      <div><b>${n2(T.sp)} m²</b><span>surface de plancher SP</span></div>
      <div><b>${n2(T.sn)} m²</b><span>surface nette SN</span></div>
      <div><b>${n2(vb)} m³</b><span>volume bâti VB, dont ${n2(vbUnder)} m³ en sous-sol</span></div>
    </div>
    <div class="tablewrap"><table class="quant">
      <thead><tr><th>Quantité SIA 416</th><th class="num">Objet 1 · Villa</th><th class="num">Objet 2 · Garage</th></tr></thead>
      <tbody>
        <tr><td><span class="code">SB</span> Surface de terrain bâtie</td><td class="num">110.00 m²</td><td class="num">33.00 m²</td></tr>
        <tr><td><span class="code">SP</span> Surface de plancher</td><td class="num">${n2(T.sp)} m²</td><td class="num">${n2(garage.sp)} m²</td></tr>
        <tr><td><span class="code">SN</span> Surface nette</td><td class="num">${n2(T.sn)} m²</td><td class="num">${n2(garage.sn)} m²</td></tr>
        <tr class="sub"><td><span class="code">SUP</span> utile principale</td><td class="num">${n2(T.SUP)} m²</td><td class="num">—</td></tr>
        <tr class="sub"><td><span class="code">SUS</span> utile secondaire</td><td class="num">${n2(T.SUS)} m²</td><td class="num">${n2(garage.sn)} m²</td></tr>
        <tr class="sub"><td><span class="code">SD</span> dégagement</td><td class="num">${n2(T.SD)} m²</td><td class="num">—</td></tr>
        <tr class="sub"><td><span class="code">SI</span> installations</td><td class="num">${n2(T.SI)} m²</td><td class="num">—</td></tr>
        <tr><td><span class="code">SC</span> Surface de construction</td><td class="num">${n2(T.sc)} m² <span class="muted small">(${((T.sc / T.sp) * 100).toFixed(1)} % de SP)</span></td><td class="num">${n2(garage.sc)} m²</td></tr>
        <tr class="strong"><td><span class="code">VB</span> Volume bâti</td><td class="num">${n2(vb)} m³</td><td class="num">${n2(garage.v)} m³</td></tr>
        <tr class="sub"><td>hors-sol (rez, étage, combles et toiture)</td><td class="num">${n2(vb - vbUnder)} m³</td><td class="num">${n2(garage.v)} m³</td></tr>
        <tr class="sub"><td>sous-sol</td><td class="num">${n2(vbUnder)} m³</td><td class="num">—</td></tr>
      </tbody>
    </table></div>
    <div class="tablewrap"><table>
      <thead><tr><th>Niveau</th><th class="num">SP</th><th class="num">SN</th><th class="num">SC</th><th class="num">SUP</th><th class="num">SUS</th><th class="num">SD</th><th class="num">SI</th><th class="num">VB m³</th></tr></thead>
      <tbody>${S.map((s, i) => `<tr><td>${s.label}</td><td class="num">${n2(s.sp)}</td><td class="num">${n2(s.sn)}</td><td class="num">${n2(s.sc)}</td><td class="num">${n2(s.SUP)}</td><td class="num">${n2(s.SUS)}</td><td class="num">${n2(s.SD)}</td><td class="num">${n2(s.SI)}</td><td class="num">${n2(V.storeys[i].v)}</td></tr>`).join("")}</tbody>
      <tfoot><tr class="strong"><td>Total</td><td class="num">${n2(T.sp)}</td><td class="num">${n2(T.sn)}</td><td class="num">${n2(T.sc)}</td><td class="num">${n2(T.SUP)}</td><td class="num">${n2(T.SUS)}</td><td class="num">${n2(T.SD)}</td><td class="num">${n2(T.SI)}</td><td class="num">${n2(vb)}</td></tr></tfoot>
    </table></div>
    <div class="note warnbox"><b>${flagged.length} locaux s'écartent de plus de 5 % de la surface inscrite sur les plans</b><span>Sur ${withPlan.length} locaux dont le plan donne la surface, l'écart total est de ${pct(sum(withPlan, (r) => r.sn), sum(withPlan, (r) => r.plan))}. Les trois chambres des combles mesurent 9 à 22 % de plus dans la maquette : les plans comptent probablement sans la bande basse sous la pente (mur de pied, ou une règle de hauteur). Le hall de l'appartement 2 au rez mesure 16 % de moins, les deux WC 8 à 9 % de plus. À vérifier avant de reprendre ces chiffres.</span></div>
  </div>
  ${footer(1)}
</article>

${S.map((s, i) => `<article class="sheet" id="p${i + 2}">
  ${header(i + 2, `Surfaces par niveau : ${s.label.toLowerCase()}`)}
  <div class="grow">${storeyBlock(s)}</div>
  ${footer(i + 2)}
</article>`).join("\n")}

<article class="sheet" id="p6">
  ${header(6, "Volume bâti VB")}
  <div class="grow">
    <p class="lead">Volume réel délimité par les faces extérieures de l'enveloppe, du dessous du radier à la surface extérieure de la toiture, calculé niveau par niveau. Les dimensions horizontales sont les dimensions effectives ; la toiture est intégrée sous ses deux pans.</p>
    <div class="vol">
      <figure class="plan-fig">${sectionSVG()}<figcaption>Coupe schématique perpendiculaire au faîte</figcaption></figure>
      <div class="tablewrap"><table>
        <thead><tr><th>Niveau</th><th>Calcul</th><th class="num">m³</th></tr></thead>
        <tbody>${V.storeys.map((s) => `<tr><td>${s.label}</td><td class="mono formula">${esc(s.formula)}</td><td class="num">${n2(s.v)}</td></tr>`).join("")}</tbody>
        <tfoot>
          <tr><td colspan="2">hors-sol</td><td class="num">${n2(vb - vbUnder)}</td></tr>
          <tr><td colspan="2">sous-sol</td><td class="num">${n2(vbUnder)}</td></tr>
          <tr class="strong"><td colspan="2">VB objet 1 · Villa</td><td class="num">${n2(vb)}</td></tr>
          <tr><td>Objet 2 · Garage</td><td class="mono formula">${esc(V.garage.formula)}</td><td class="num">${n2(garage.v)}</td></tr>
        </tfoot>
      </table></div>
    </div>
    <div class="rules">
      <div><b>Compris</b><span>Tous les niveaux, sous-sol compris ; les combles et la toiture jusqu'à sa surface extérieure.</span></div>
      <div><b>Non compris</b><span>Les balcons ouverts (${V.balconies.map((b) => n2(b.area) + " m²").join(" et ")}), les débords de toiture (en pointillé sur la coupe), les fondations spéciales.</span></div>
      <div><b>Hypothèse</b><span>Radier de ${V.radier.toFixed(2)} m sous le sous-sol : il n'est pas dans la maquette. À corriger d'après la coupe des plans.</span></div>
      <div><b>Garage</b><span>Bâtiment non contigu (environ 2 m de la villa) : compté comme un objet à part, comme le demande la fiche L9.</span></div>
    </div>
  </div>
  ${footer(6)}
</article>

<article class="sheet" id="p7">
  ${header(7, "Métré des finitions")}
  <div class="grow">
    <p class="lead">Quantités par élément du code des coûts eCCC-Bât (SN 506 511), avec le CFC correspondant. Chaque ligne donne son calcul, pour qu'elle puisse être vérifiée et reprise dans un devis. Les colonnes de prix sont à compléter.</p>
    <div class="tablewrap"><table class="takeoff">
      <thead><tr><th>eCCC</th><th>CFC</th><th>Désignation</th><th>Niveau / local</th><th>Calcul</th><th>Unité</th><th class="num">Quantité</th><th class="num">PU CHF</th><th class="num">Montant CHF</th></tr></thead>
      <tbody>${takeoffRows}</tbody>
    </table></div>
    <div class="rules">
      <div><b>Ouvertures</b><span>Déduites dès 1 m² (NPK 643/651) ; ${smallKept + R.basementWalls.small} ouvertures plus petites ne sont pas déduites.</span></div>
      <div><b>Carrelage, petites surfaces</b><span>Aucune surface de moins de 2 m² par local : pas de majoration de 20 % (NPK 645).</span></div>
      <div><b>Hauteurs</b><span>Parois : hauteur libre du niveau ; aux combles, hauteur sous pente au point près. Faïence : 1.20 m, 2.40 m aux murs de douche et de baignoire.</span></div>
      <div><b>Pas encore compté</b><span>Embrasures et tablettes de fenêtres, plinthes, plafonds (G04) : à ajouter dans le rapport final.</span></div>
    </div>
  </div>
  ${footer(7)}
</article>

<article class="sheet" id="p8">
  ${header(8, "Estimation des coûts et de la valeur")}
  <div class="grow">
    <p class="lead">Les quantités viennent de la maquette ; les prix se saisissent dans les champs et les montants se recalculent. Les coûts suivent les groupes principaux du CFC. Les prix proposés par défaut sont indicatifs : l'agence les remplace par ses propres références.</p>
    <section class="est" aria-labelledby="est-a">
      <div class="est-head"><h3 id="est-a">A · Bâtiment (CFC 2) par le volume SIA 416</h3>
        <div class="presets" role="group" aria-label="Standard de construction de la villa"><button type="button" data-rate="675" aria-pressed="false">Simple</button><button type="button" data-rate="850" aria-pressed="true">Moyen</button><button type="button" data-rate="1075" aria-pressed="false">Élevé</button></div></div>
      <div class="tablewrap"><table class="est-t">
        <thead><tr><th>Objet</th><th>Volume bâti</th><th class="num">CHF/m³</th><th class="num">Montant CHF</th></tr></thead>
        <tbody>
          <tr><td>Villa</td><td class="mono">${n2(vb)} m³</td><td class="num"><input class="field" id="pu-villa" type="number" inputmode="decimal" min="0" step="5" value="850" aria-label="Prix au m³ de la villa"></td><td class="num amount" id="am-villa"></td></tr>
          <tr><td>Garage</td><td class="mono">${n2(garage.v)} m³</td><td class="num"><input class="field" id="pu-garage" type="number" inputmode="decimal" min="0" step="5" value="600" aria-label="Prix au m³ du garage"></td><td class="num amount" id="am-garage"></td></tr>
        </tbody>
        <tfoot><tr class="total"><td colspan="3">Total CFC 2</td><td class="num" id="am-cfc2"></td></tr></tfoot>
      </table></div>
      <p class="small muted">Repères indicatifs d'un constructeur (neho.ch), CHF par m³ SIA 416 : simple 600–750, moyen 750–950, élevé 950–1'200. Vérifier si la référence comprend les honoraires (CFC 29). Indexer les références anciennes avec l'indice OFS des prix de la construction, région lémanique : 116.3 en octobre 2025 (octobre 2020 = 100).</p>
    </section>
    <section class="est" aria-labelledby="est-b">
      <h3 id="est-b">B · Coût du projet par groupes CFC</h3>
      <div class="tablewrap"><table class="est-t">
        <thead><tr><th>CFC</th><th>Poste</th><th>Quantité de la maquette</th><th class="num">Prix unitaire</th><th class="num">Montant CHF</th></tr></thead>
        <tbody>
          <tr class="grp"><td colspan="5"><b>0 Terrain</b></td></tr>
          <tr><td class="mono">0</td><td>Parcelle</td><td class="mono" data-q="${X.exterior.plot.toFixed(2)}">${n2(X.exterior.plot)} m², tracée d'après les plans</td><td class="num"><input class="field" id="pu-0" type="number" inputmode="decimal" min="0" placeholder="CHF/m²" aria-label="Prix du terrain au m²"></td><td class="num amount" id="am-0"></td></tr>
          <tr class="grp"><td colspan="5"><b>1 Travaux préparatoires</b></td></tr>
          <tr><td class="mono">1</td><td>Fouille du sous-sol</td><td class="mono" data-q="${vbUnder.toFixed(2)}">${n2(vbUnder)} m³, volume enterré sans surlargeur ni talus</td><td class="num"><input class="field" id="pu-1" type="number" inputmode="decimal" min="0" placeholder="CHF/m³" aria-label="Prix de la fouille au m³"></td><td class="num amount" id="am-1"></td></tr>
          <tr class="grp"><td colspan="5"><b>2 Bâtiment</b></td></tr>
          <tr><td class="mono">2</td><td>Villa et garage</td><td>selon A</td><td class="num muted">—</td><td class="num amount" id="am-2"></td></tr>
          <tr class="grp"><td colspan="5"><b>4 Aménagements extérieurs</b></td></tr>
          <tr><td class="mono">4</td><td>Piscines 6.00 × 3.00 × 1.50 m, une par appartement</td><td class="mono" data-q="2">2 pces</td><td class="num"><input class="field" id="pu-4a" type="number" inputmode="decimal" min="0" placeholder="CHF/pce" aria-label="Prix d'une piscine"></td><td class="num amount" id="am-4a"></td></tr>
          <tr><td class="mono">4</td><td>Dallages et terrasses</td><td class="mono" data-q="${X.exterior.pavedTotal.toFixed(2)}">${n2(X.exterior.pavedTotal)} m², 4 surfaces</td><td class="num"><input class="field" id="pu-4b" type="number" inputmode="decimal" min="0" placeholder="CHF/m²" aria-label="Prix du dallage au m²"></td><td class="num amount" id="am-4b"></td></tr>
          <tr><td class="mono">4</td><td>Haie vive et clôture en limite de parcelle</td><td class="mono" data-q="${X.exterior.fence.toFixed(2)}">${n2(X.exterior.fence)} m, dont ${n2(X.exterior.hedgeDouble)} m en double rang</td><td class="num"><input class="field" id="pu-4c" type="number" inputmode="decimal" min="0" placeholder="CHF/m" aria-label="Prix de la haie et clôture au mètre"></td><td class="num amount" id="am-4c"></td></tr>
          <tr><td class="mono">4</td><td>Haie basse entre les deux jardins</td><td class="mono" data-q="${X.exterior.divider.toFixed(2)}">${n2(X.exterior.divider)} m</td><td class="num"><input class="field" id="pu-4d" type="number" inputmode="decimal" min="0" placeholder="CHF/m" aria-label="Prix de la haie basse au mètre"></td><td class="num amount" id="am-4d"></td></tr>
          <tr><td class="mono">4</td><td>Arbres à planter</td><td class="mono" data-q="${X.exterior.trees}">${X.exterior.trees} pces, ${X.exterior.treesKept} arbre existant conservé</td><td class="num"><input class="field" id="pu-4e" type="number" inputmode="decimal" min="0" placeholder="CHF/pce" aria-label="Prix d'un arbre"></td><td class="num amount" id="am-4e"></td></tr>
          <tr class="grp"><td colspan="5"><b>5 Frais secondaires</b></td></tr>
          <tr><td class="mono">5</td><td>Autorisations, taxes de raccordement, assurances, financement</td><td>en % du CFC 2</td><td class="num"><input class="field" id="pu-5" type="number" inputmode="decimal" min="0" max="100" step="0.5" placeholder="%" aria-label="Frais secondaires en pour cent du CFC 2"></td><td class="num amount" id="am-5"></td></tr>
        </tbody>
        <tfoot><tr class="total"><td colspan="4">Coût du projet <span class="small muted" id="cost-note"></span></td><td class="num" id="am-cost"></td></tr></tfoot>
      </table></div>
    </section>
    <section class="est" aria-labelledby="est-c">
      <h3 id="est-c">C · Valeur de vente</h3>
      <div class="tablewrap"><table class="est-t">
        <thead><tr><th>Logement</th><th>Surface pondérée (page 9)</th><th class="num">CHF/m²</th><th class="num">Valeur CHF</th></tr></thead>
        <tbody>${flats.map((F, i) => `<tr><td>${F.flat}</td><td class="mono">${n2(F.total)} m²</td><td class="num"><input class="field" id="pu-v${i}" type="number" inputmode="decimal" min="0" step="50" placeholder="CHF/m²" aria-label="Prix de vente au m² pondéré, ${F.flat}"></td><td class="num amount" id="am-v${i}"></td></tr>`).join("")}</tbody>
        <tfoot><tr class="total"><td colspan="3">Valeur de vente</td><td class="num" id="am-value"></td></tr></tfoot>
      </table></div>
      <div class="balance" aria-live="polite"><div><b id="b-value">—</b><span>valeur de vente</span></div><div><b id="b-cost">—</b><span>coût du projet</span></div><div><b id="b-margin">—</b><span>marge brute</span></div></div>
      <p class="small muted">Le prix au m² vient de l'agence ou d'un outil d'estimation (Wüest Partner, IAZI, RealAdvisor, Fahrländer). Ces outils sont payants ; un accès de l'agence pourrait remplir ces champs.</p>
    </section>
  </div>
  ${footer(8)}
</article>

<article class="sheet" id="p9">
  ${header(9, "Annexe : surface habitable et habitabilité")}
  <div class="grow">
    <p class="lead">Pour la vente : la surface habitable nette de chaque appartement (pièces et dégagements, sans les escaliers ni les annexes) et une surface pondérée. Il n'existe pas de norme nationale pour ce calcul : les règles ci-dessous sont celles de la pratique courante et se règlent par projet.</p>
    <div class="flats">${flats.map(flatBlock).join("")}</div>
    <div class="rules">
      <div><b>Combles</b><span>Les surfaces de moins de 1.30 m de hauteur libre ne comptent pas (RLATC art. 27). Dans la maquette, la hauteur au pied des murs est de ${R.attic.minClear.toFixed(2)} m : rien n'est déduit.</span></div>
      <div><b>Pondération</b><span>Balcons 50 %. Terrasses (33 %) et jardins privatifs (10 %) : à attribuer par logement selon la répartition PPE ; la maquette les mesure (page 8) mais ne sait pas à qui ils appartiennent.</span></div>
    </div>
    <h3>Habitabilité selon le RLATC vaudois</h3>
    <p class="small muted">Art. 25 : volume d'au moins 20 m³. Art. 27 : hauteur d'au moins 2.40 m ; aux combles, sur au moins la moitié de la surface utilisable, comptée dès 1.30 m. Art. 28 : baies d'au moins 1/8 du plancher et 1 m² (1/15 et 0.80 m² pour les lucarnes et tabatières).</p>
    <div class="tablewrap"><table class="rooms compact">
      <thead><tr><th>N°</th><th>Local</th><th class="num">Utilisable m²</th><th>Hauteur, art. 27</th><th>Baies, art. 28</th><th class="num">Volume, art. 25</th><th>Résultat</th></tr></thead>
      <tbody>${X.rlatc.map((q) => {
        // 1.02 Séjour is open to the kitchen 1.01 and the living room 1.03 (no partition between them): one room for art. 25
        const open = q.no === "1.02" ? X.rlatc.filter((o) => ["1.01", "1.02", "1.03"].includes(o.no)) : null;
        const ok = q.ok27 && q.ok28 && (q.ok25 || open);
        const res = !q.ok25 && open ? `<span class="st st-ok">conforme</span> <span class="small muted">pièce ouverte sur 1.01 et 1.03 : ${n2(sum(open, (o) => o.vol))} m³ ensemble</span>` : ok ? `<span class="st st-ok">conforme</span>` : `<span class="st st-warn">à vérifier</span>`;
        return `<tr><td class="mono no">${q.no}</td><td>${esc(q.name)}<span class="flatname">${q.flat}</span></td><td class="num">${n2(q.use)}</td><td>${q.attic ? `${Math.round(q.high * 100)} % à 2.40 m ou plus` : `${q.clear.toFixed(2)} m`}</td><td>${n2(q.light)} m², 1/${q.ratio.toFixed(1)}${q.aSky ? ` <span class="small muted">dont tabatières ${n2(q.aSky)}</span>` : ""}</td><td class="num${q.ok25 ? "" : " warn"}">${n2(q.vol)}</td><td>${res}</td></tr>`;
      }).join("")}</tbody>
    </table></div>
  </div>
  ${footer(9)}
</article>

<script>
(() => {
  const VB = ${vb.toFixed(4)}, VG = ${garage.v.toFixed(4)};
  const $ = (id) => document.getElementById(id);
  const chf = (v) => { const s = Math.round(Math.abs(v)).toString().replace(/\\B(?=(\\d{3})+(?!\\d))/g, "'"); return (v < 0 ? "−" : "") + s; };
  const num = (id) => { const v = parseFloat($(id).value); return Number.isFinite(v) && v >= 0 ? v : null; };
  const qty = (id) => parseFloat($(id).closest("tr").querySelector("[data-q]").dataset.q);
  const put = (id, v) => { const el = $(id); el.textContent = v === null ? "à saisir" : chf(v); el.classList.toggle("empty", v === null); };
  function update() {
    const villa = num("pu-villa"), gar = num("pu-garage");
    const aV = villa === null ? null : VB * villa, aG = gar === null ? null : VG * gar;
    put("am-villa", aV); put("am-garage", aG);
    const cfc2 = aV === null && aG === null ? null : (aV ?? 0) + (aG ?? 0);
    $("am-cfc2").textContent = cfc2 === null ? "—" : chf(cfc2);
    put("am-2", cfc2);
    let cost = cfc2 ?? 0, open = cfc2 === null ? 1 : 0;
    for (const k of ["0", "1", "4a", "4b", "4c", "4d", "4e"]) {
      const p = num("pu-" + k), a = p === null ? null : p * qty("pu-" + k);
      put("am-" + k, a); if (a === null) open++; else cost += a;
    }
    const pct = num("pu-5"), a5 = pct === null || cfc2 === null ? null : (cfc2 * pct) / 100;
    put("am-5", a5); if (a5 === null) open++; else cost += a5;
    $("am-cost").textContent = chf(cost);
    $("cost-note").textContent = open ? "(" + open + " poste" + (open > 1 ? "s" : "") + " encore à saisir)" : "";
    let value = 0, missing = 0;
    for (let i = 0; i < ${flats.length}; i++) {
      const p = num("pu-v" + i), area = [${flats.map((F) => F.total.toFixed(2)).join(", ")}][i], a = p === null ? null : p * area;
      put("am-v" + i, a); if (a === null) missing++; else value += a;
    }
    $("am-value").textContent = missing === ${flats.length} ? "—" : chf(value);
    $("b-value").textContent = missing === ${flats.length} ? "—" : "CHF " + chf(value);
    $("b-cost").textContent = "CHF " + chf(cost);
    $("b-margin").textContent = missing || open ? "—" : "CHF " + chf(value - cost);
    for (const b of document.querySelectorAll(".presets button")) b.setAttribute("aria-pressed", String(+b.dataset.rate === villa));
  }
  for (const b of document.querySelectorAll(".presets button")) b.addEventListener("click", () => { $("pu-villa").value = b.dataset.rate; update(); });
  for (const i of document.querySelectorAll(".est input")) i.addEventListener("input", update);
  update();
})();
</script>
`;
fs.writeFileSync(outFile, html);
console.log(`wrote ${outFile} (${(html.length / 1024).toFixed(0)} KB), ${lines.filter((l) => !l.group).length} take-off lines, flagged ${flagged.map((r) => r.no).join(" ")}`);
for (const F of flats) console.log(F.flat, n2(F.hab), n2(F.total));
