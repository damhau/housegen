// Real sanitary ware for the kit's wc() and basin() (kit/assets/sanitary/*.glb), from manufacturers'
// CAD files (OBJ): each put in the kit's frame (metres, +y up, facing +z), simplified to a web
// budget, given creased normals and compressed with meshopt. The parts of a piece (a flush plate and
// its buttons) are primitives named after them: furnish.js gives each its material.
//
//   node scripts/sanitary.mjs <dir with the OBJ files> [out_dir = assets/sanitary]
//
// Run from kit/ (the glTF tools are dev dependencies: not in the image).
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import * as THREE from "three";
import { OBJLoader } from "three/addons/loaders/OBJLoader.js";
import { mergeVertices, toCreasedNormals } from "three/addons/utils/BufferGeometryUtils.js";
import { Document, NodeIO } from "@gltf-transform/core";
import { ALL_EXTENSIONS } from "@gltf-transform/extensions";
import { meshopt } from "@gltf-transform/functions";
import { MeshoptDecoder, MeshoptEncoder, MeshoptSimplifier } from "meshoptimizer";

// CAD axes to the kit's (rows: x', y', z'), and units to metres
const GEBERIT = [[1, 0, 0], [0, 0, 1], [0, -1, 0]]; // z up, -y out of the wall
const LAUFEN = [[0, 1, 0], [0, 0, 1], [1, 0, 0]]; // x = depth from the wall, z up
const KWC = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]; // y up, the spout toward +z

// origin: "back" = centred in x, its back at z = 0, its height as drawn (a WC hangs at its height);
// "plate" = centred in x and y, back at z = 0; "top" = centred in x, back at z = 0, its top at y = 0;
// "base" = the middle of its foot at the origin (a mixer stands in its tap hole)
const PIECES = [
  { out: "wc-wall-hung", file: "geberit-icon-wc.obj", basis: GEBERIT, scale: 0.01, origin: "back", triangles: 9000, lid: true },
  { out: "flush-plate", file: "geberit-sigma20.obj", basis: GEBERIT, scale: 0.01, origin: "plate", triangles: 3000,
    parts: (mtl) => (mtl === "255,255,255" ? "plate" : "buttons") },
  { out: "basin-60", file: "laufen-kartell-basin.obj", basis: LAUFEN, scale: 0.001, origin: "top", triangles: 6000 },
  { out: "basin-mixer", file: "kwc-bevo-basin-mixer.obj", basis: KWC, scale: 0.001, origin: "base", triangles: 5000 },
];

const [src, out = "assets/sanitary"] = process.argv.slice(2);
if (!src) {
  console.error("usage: node scripts/sanitary.mjs <dir with the OBJ files> [out_dir]");
  process.exit(2);
}
await MeshoptSimplifier.ready;
await MeshoptEncoder.ready;
await MeshoptDecoder.ready;
const io = new NodeIO()
  .registerExtensions(ALL_EXTENSIONS)
  .registerDependencies({ "meshopt.decoder": MeshoptDecoder, "meshopt.encoder": MeshoptEncoder });

/** The OBJ's geometry split by material: [{ part, geometry }] in the kit's axes and metres. */
function parts(text, piece) {
  const m = new THREE.Matrix4()
    .set(...piece.basis[0], 0, ...piece.basis[1], 0, ...piece.basis[2], 0, 0, 0, 0, 1)
    .premultiply(new THREE.Matrix4().makeScale(piece.scale, piece.scale, piece.scale));
  const byPart = new Map();
  new OBJLoader().parse(text).traverse((o) => {
    if (!o.isMesh) return;
    const mats = [o.material].flat();
    const groups = o.geometry.groups.length ? o.geometry.groups : [{ start: 0, count: o.geometry.attributes.position.count, materialIndex: 0 }];
    const pos = o.geometry.attributes.position;
    for (const gr of groups) {
      const part = piece.parts ? piece.parts(mats[gr.materialIndex]?.name ?? "") : "body";
      const arr = byPart.get(part) ?? [];
      for (let i = gr.start; i < gr.start + gr.count; i++) arr.push(pos.getX(i), pos.getY(i), pos.getZ(i));
      byPart.set(part, arr);
    }
  });
  return [...byPart].map(([part, arr]) => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(arr, 3));
    g.applyMatrix4(m);
    return { part, geometry: g };
  });
}

/** Down to about `share` of its triangles, then creased normals (30°), indexed again. */
function simplify(g, share) {
  const w = mergeVertices(g, 1e-6);
  const index = new Uint32Array(w.index.array);
  const target = Math.max(3, Math.floor((index.length * share) / 3) * 3);
  const [kept] = MeshoptSimplifier.simplify(index, w.attributes.position.array, 3, target, 0.002);
  const s = new THREE.BufferGeometry();
  s.setAttribute("position", w.attributes.position);
  s.setIndex(new THREE.BufferAttribute(kept, 1));
  return mergeVertices(toCreasedNormals(s, THREE.MathUtils.degToRad(30)), 1e-6);
}

function origin(geoms, kind) {
  const box = new THREE.Box3();
  for (const g of geoms) box.union(new THREE.Box3().setFromBufferAttribute(g.attributes.position));
  const c = box.getCenter(new THREE.Vector3());
  const t = new THREE.Vector3(-c.x, 0, -box.min.z);
  if (kind === "plate") t.y = -c.y;
  if (kind === "top") t.y = -box.max.y;
  if (kind === "base") {
    // the middle of the foot: the points within 5 mm of the bottom
    let zs = 0, xs = 0, n = 0;
    for (const g of geoms) {
      const p = g.attributes.position;
      for (let i = 0; i < p.count; i++) if (p.getY(i) < box.min.y + 0.005) { xs += p.getX(i); zs += p.getZ(i); n++; }
    }
    t.set(-xs / n, -box.min.y, -zs / n);
  }
  for (const g of geoms) g.translate(t.x, t.y, t.z);
  const b = new THREE.Box3();
  for (const g of geoms) b.union(new THREE.Box3().setFromBufferAttribute(g.attributes.position));
  return b;
}

/** Convex hull of 2D points (monotone chain), counter-clockwise. */
function hull(pts) {
  const p = [...pts].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lo = [], up = [];
  for (const q of p) { while (lo.length >= 2 && cross(lo.at(-2), lo.at(-1), q) <= 0) lo.pop(); lo.push(q); }
  for (const q of p.reverse()) { while (up.length >= 2 && cross(up.at(-2), up.at(-1), q) <= 0) up.pop(); up.push(q); }
  return lo.slice(0, -1).concat(up.slice(0, -1));
}

/**
 * A closed seat and lid for a WC bowl drawn without them (listing photos show the lid down): the
 * outline of the bowl's rim (its top 6 mm, seen from above), a 15 mm seat on it and a 20 mm lid, a
 * little inside, on the seat.
 */
function seatAndLid(body) {
  const p = body.attributes.position;
  let top = -Infinity;
  for (let i = 0; i < p.count; i++) top = Math.max(top, p.getY(i));
  const rim = [];
  for (let i = 0; i < p.count; i++) if (p.getY(i) > top - 0.006) rim.push([p.getX(i), -p.getZ(i)]);
  const outline = hull(rim);
  const c = outline.reduce((a, q) => [a[0] + q[0] / outline.length, a[1] + q[1] / outline.length], [0, 0]);
  const slab = (inset, y0, h, bevel) => {
    const pts = outline.map(([x, y]) => {
      const dx = x - c[0], dy = y - c[1], d = Math.hypot(dx, dy) || 1;
      return new THREE.Vector2(x - (dx / d) * inset, y - (dy / d) * inset);
    });
    const g = new THREE.ExtrudeGeometry(new THREE.Shape(pts), {
      depth: h - 2 * bevel, bevelEnabled: true, bevelThickness: bevel, bevelSize: bevel, bevelSegments: 2,
    });
    g.rotateX(-Math.PI / 2); // the outline in x/z (shape y = -z), extruded up
    g.translate(0, y0 + bevel, 0);
    g.deleteAttribute("uv");
    return mergeVertices(toCreasedNormals(g, THREE.MathUtils.degToRad(30)), 1e-6);
  };
  return [slab(0.004, top, 0.015, 0.004), slab(0.008, top + 0.015, 0.02, 0.006)];
}

await mkdir(out, { recursive: true });
const info = {};
for (const piece of PIECES) {
  const ps = parts(await readFile(join(src, piece.file), "utf8"), piece);
  const total = ps.reduce((n, p) => n + p.geometry.attributes.position.count / 3, 0);
  const share = Math.min(1, piece.triangles / total);
  const simple = ps.map((p) => ({ part: p.part, geometry: simplify(p.geometry, share) }));
  const box = origin(simple.map((p) => p.geometry), piece.origin);
  if (piece.lid) {
    const [seat, lid] = seatAndLid(simple[0].geometry);
    simple.push({ part: "seat", geometry: seat }, { part: "lid", geometry: lid });
    box.expandByObject(new THREE.Mesh(lid));
  }

  const doc = new Document();
  const buffer = doc.createBuffer();
  const mesh = doc.createMesh(piece.out);
  let triangles = 0;
  for (const { part, geometry } of simple) {
    const acc = (type, array) => doc.createAccessor().setType(type).setArray(array).setBuffer(buffer);
    triangles += geometry.index.count / 3;
    mesh.addPrimitive(
      doc.createPrimitive()
        .setAttribute("POSITION", acc("VEC3", new Float32Array(geometry.attributes.position.array)))
        .setAttribute("NORMAL", acc("VEC3", new Float32Array(geometry.attributes.normal.array)))
        .setIndices(acc("SCALAR", new Uint32Array(geometry.index.array)))
        .setMaterial(doc.createMaterial(part)),
    );
  }
  doc.createScene().addChild(doc.createNode(piece.out).setMesh(mesh));
  await doc.transform(meshopt({ encoder: MeshoptEncoder, level: "medium" }));
  const bytes = await io.writeBinary(doc);
  await writeFile(join(out, `${piece.out}.glb`), bytes);
  const r = (v) => Math.round(v * 1000) / 1000;
  info[piece.out] = { triangles, from: total, kb: Math.round(bytes.length / 1024), min: box.min.toArray().map(r), max: box.max.toArray().map(r) };
  console.log(piece.out, info[piece.out]);
}
await writeFile(join(out, "sanitary.json"), JSON.stringify(info, null, 1) + "\n");
