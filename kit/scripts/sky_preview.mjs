// Tone-mapped 8-bit previews (PPM) of .hdr sky maps: the whole map at half size and the strip
// 8° above to 8° below the horizon at full width, written next to each input, to look at a map's
// sky and horizon before choosing it (docs/quality-plan-2026-09-12.md, step 3.1b).
//   cd kit && node scripts/sky_preview.mjs /path/to/*.hdr
import fs from "node:fs";
import * as THREE from "three";
import { RGBELoader } from "three/addons/loaders/RGBELoader.js";
const aces = (x) => Math.min(1, Math.max(0, (x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14)));
const srgb = (x) => (x <= 0.0031308 ? 12.92 * x : 1.055 * Math.pow(x, 1 / 2.4) - 0.055);
for (const file of process.argv.slice(2)) {
  const buf = fs.readFileSync(file);
  const loader = new RGBELoader(); loader.setDataType(THREE.FloatType);
  const { width: W, height: H, data } = loader.parse(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength));
  const ch = data.length / (W * H);
  // exposure: median sky luminance (upper half, excluding the sun) to 0.5 linear
  const Ls = []; for (let r = 0; r < H / 2; r += 2) for (let c = 0; c < W; c += 8) { const i = r * W + c; Ls.push(0.2126 * data[i*ch] + 0.7152 * data[i*ch+1] + 0.0722 * data[i*ch+2]); }
  Ls.sort((a, b) => a - b); const med = Ls[Math.floor(Ls.length / 2)]; const k = 0.5 / med;
  const write = (name, x0, y0, w, h, scale) => {
    const ow = Math.floor(w / scale), oh = Math.floor(h / scale); const out = Buffer.alloc(ow * oh * 3);
    for (let y = 0; y < oh; y++) for (let x = 0; x < ow; x++) {
      const i = ((y0 + Math.floor(y * scale)) * W + (x0 + Math.floor(x * scale))) * ch; // row 0 = top (sun found in the top half)
      for (let c = 0; c < 3; c++) out[(y * ow + x) * 3 + c] = Math.round(255 * srgb(aces(data[i + c] * k)));
    }
    fs.writeFileSync(name, Buffer.concat([Buffer.from(`P6\n${ow} ${oh}\n255\n`), out]));
  };
  const base = file.replace(/\.hdr$/, "");
  write(`${base}.full.ppm`, 0, 0, W, H, 2);                       // whole map at 1024x512
  write(`${base}.horizon.ppm`, 0, Math.floor(H * 0.42), W, Math.floor(H * 0.16), 1); // 8 deg above to 8 deg below the horizon, full width
  console.log(`${base.replace(/.*\//, "")}: exposure k=${k.toFixed(2)} (median sky ${med.toFixed(2)})`);
}
