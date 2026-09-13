// Sun position, sky luminance percentiles and horizon colour of equirectangular .hdr sky maps,
// parsed with three's loader (docs/quality-plan-2026-09-12.md, step 3.1b). Lives under kit/ so
// `three` resolves from kit/node_modules:
//   cd kit && node scripts/sky_stats.mjs /path/to/*.hdr
import fs from "node:fs";
import * as THREE from "three";
import { RGBELoader } from "three/addons/loaders/RGBELoader.js";
const lum = (r, g, b) => 0.2126 * r + 0.7152 * g + 0.0722 * b;
for (const file of process.argv.slice(2)) {
  const buf = fs.readFileSync(file);
  const loader = new RGBELoader(); loader.setDataType(THREE.FloatType);
  const img = loader.parse(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength));
  const { width: W, height: H, data } = img; const ch = data.length / (W * H);
  let max = 0, mi = 0;
  for (let i = 0; i < W * H; i++) { const L = lum(data[i*ch], data[i*ch+1], data[i*ch+2]); if (L > max) { max = L; mi = i; } }
  const maxRow = Math.floor(mi / W), maxCol = mi % W;
  // which half holds the sun: rows 0..H/2 (array top) or the other; the sky half is the sun's half
  const topHalf = maxRow < H / 2;
  // centroid of the sun (pixels above half the max)
  let sc = 0, sr = 0, sn = 0; for (let i = 0; i < W * H; i++) { const L = lum(data[i*ch], data[i*ch+1], data[i*ch+2]); if (L > max * 0.5) { sc += i % W; sr += Math.floor(i / W); sn++; } }
  sc /= sn; sr /= sn;
  // elevation from the row, assuming the sky half is up: v = 1 - row/H if the sun is in the array's top half
  const v = topHalf ? 1 - sr / H : sr / H; const el = (v - 0.5) * 180;
  const u = sc / W; const ang = (u - 0.5) * 2 * Math.PI; const x = Math.cos(ang), z = Math.sin(ang);
  const az = ((Math.atan2(x, -z) * 180 / Math.PI) + 360) % 360;
  // sky statistics on the sky half, away from the sun disc: median luminance, 90th percentile, mean irradiance proxy
  const Ls = []; for (let r = 0; r < H; r++) { const vv = topHalf ? 1 - r / H : r / H; const e = (vv - 0.5) * 180; if (e < 3) continue; for (let c = 0; c < W; c += 4) { const i = r * W + c; const L = lum(data[i*ch], data[i*ch+1], data[i*ch+2]); if (L < max * 0.05) Ls.push(L); } }
  Ls.sort((a, b) => a - b); const med = Ls[Math.floor(Ls.length / 2)], p90 = Ls[Math.floor(Ls.length * 0.9)], p10 = Ls[Math.floor(Ls.length * 0.1)];
  // horizon colour opposite the sun: elevation 1..4 deg, azimuth +-10 deg around u+0.5
  let hr = 0, hg = 0, hb = 0, hn = 0; for (let r = 0; r < H; r++) { const vv = topHalf ? 1 - r / H : r / H; const e = (vv - 0.5) * 180; if (e < 1 || e > 4) continue; for (let c = 0; c < W; c++) { let du = Math.abs(((c / W) - (u + 0.5) + 1.5) % 1 - 0.5); if (du > 10 / 360) continue; const i = r * W + c; hr += data[i*ch]; hg += data[i*ch+1]; hb += data[i*ch+2]; hn++; } }
  console.log(`${file.replace(/.*\//, "")}: ${W}x${H}, sun at row ${Math.round(sr)} (array ${topHalf ? "top" : "bottom"} half), el ${el.toFixed(1)}°, az(u) ${az.toFixed(0)}°, sun max L ${max.toFixed(0)}, sun disc ${sn} px; sky L p10/med/p90 ${p10.toFixed(2)}/${med.toFixed(2)}/${p90.toFixed(2)}; horizon opposite sun rgb ${(hr/hn).toFixed(2)} ${(hg/hn).toFixed(2)} ${(hb/hn).toFixed(2)}`);
}
