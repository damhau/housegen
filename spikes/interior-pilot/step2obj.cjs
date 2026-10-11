// STEP -> OBJ (one group per mesh), tessellated by OpenCascade (occt-import-js). Usage: node step2obj.cjs in.stp out.obj [linearDeflection]
const fs = require("fs");
const occtimportjs = require(process.env.OCCT_DIR + "/node_modules/occt-import-js/dist/occt-import-js.js");
(async () => {
  const [inp, out, defl = "0.2"] = process.argv.slice(2);
  const occt = await occtimportjs();
  const res = occt.ReadStepFile(new Uint8Array(fs.readFileSync(inp)), {
    linearUnit: "millimeter", linearDeflectionType: "absolute_value", linearDeflection: Number(defl), angularDeflection: 0.2,
  });
  if (!res.success) throw new Error("read failed");
  let lines = [], base = 1, tris = 0, min = [1e9, 1e9, 1e9], max = [-1e9, -1e9, -1e9];
  res.meshes.forEach((m, k) => {
    const p = m.attributes.position.array, n = m.attributes.normal?.array, idx = m.index.array;
    lines.push(`g ${(m.name || "mesh" + k).replace(/\s+/g, "_")}`);
    for (let i = 0; i < p.length; i += 3) {
      lines.push(`v ${p[i]} ${p[i + 1]} ${p[i + 2]}`);
      for (let a = 0; a < 3; a++) { min[a] = Math.min(min[a], p[i + a]); max[a] = Math.max(max[a], p[i + a]); }
    }
    if (n) for (let i = 0; i < n.length; i += 3) lines.push(`vn ${n[i]} ${n[i + 1]} ${n[i + 2]}`);
    for (let i = 0; i < idx.length; i += 3) {
      const [a, b, c] = [idx[i] + base, idx[i + 1] + base, idx[i + 2] + base];
      lines.push(n ? `f ${a}//${a} ${b}//${b} ${c}//${c}` : `f ${a} ${b} ${c}`);
    }
    base += p.length / 3; tris += idx.length / 3;
  });
  fs.writeFileSync(out, lines.join("\n") + "\n");
  console.log(JSON.stringify({ meshes: res.meshes.length, tris, min, max, names: res.meshes.map((m) => m.name).slice(0, 8) }));
})();
