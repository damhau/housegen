// Node cannot fetch the catalogue's models: a box of each piece's catalogue size stands in for it (fetch answers for
// objects/<id>.glb and made/<id>.glb), so the kit fits a model to the size asked exactly as in a page.
import { OBJECTS } from "../objects.js";

/** A glTF binary of one box `w` x `h` x `d`, its bottom on y = 0, as one node per name of `nodes` (null: unnamed). */
export function boxGlb(w, h, d, nodes = [null]) {
  const positions = [];
  for (const x of [-w / 2, w / 2]) for (const y of [0, h]) for (const z of [-d / 2, d / 2]) positions.push(x, y, z);
  const pos = new Float32Array(positions);
  const ind = new Uint16Array([0, 1, 3, 0, 3, 2, 4, 6, 7, 4, 7, 5, 0, 4, 5, 0, 5, 1, 2, 3, 7, 2, 7, 6, 0, 2, 6, 0, 6, 4, 1, 5, 7, 1, 7, 3]);
  const bin = new Uint8Array(pos.byteLength + ind.byteLength);
  bin.set(new Uint8Array(pos.buffer), 0);
  bin.set(new Uint8Array(ind.buffer), pos.byteLength);
  const gltf = {
    asset: { version: "2.0" }, scene: 0, scenes: [{ nodes: nodes.map((_, i) => i) }],
    nodes: nodes.map((name) => ({ mesh: 0, ...(name ? { name } : {}) })),
    meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1 }] }],
    buffers: [{ byteLength: bin.byteLength }],
    bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: pos.byteLength }, { buffer: 0, byteOffset: pos.byteLength, byteLength: ind.byteLength }],
    accessors: [
      { bufferView: 0, componentType: 5126, count: 8, type: "VEC3", min: [-w / 2, 0, -d / 2], max: [w / 2, h, d / 2] },
      { bufferView: 1, componentType: 5123, count: ind.length, type: "SCALAR" },
    ],
  };
  let json = new TextEncoder().encode(JSON.stringify(gltf));
  json = Uint8Array.from([...json, ...Array((4 - (json.length % 4)) % 4).fill(0x20)]);
  const out = new Uint8Array(12 + 8 + json.length + 8 + bin.length);
  const v = new DataView(out.buffer);
  v.setUint32(0, 0x46546c67, true);
  v.setUint32(4, 2, true);
  v.setUint32(8, out.length, true);
  v.setUint32(12, json.length, true);
  v.setUint32(16, 0x4e4f534a, true);
  out.set(json, 20);
  v.setUint32(20 + json.length, bin.length, true);
  v.setUint32(24 + json.length, 0x004e4942, true);
  out.set(bin, 28 + json.length);
  return out;
}

const BY_ID = new Map(OBJECTS.map((o) => [o.id, o]));

/**
 * Answer the catalogue's model files with boxes of their pieces' sizes. `delay(n, piece)`: milliseconds before the
 * n-th file asked for (from 0) arrives; `nodes(piece)`: the node names of its file (a plant's variants side by side).
 */
export function stubCatalogue({ delay = () => 0, nodes = () => [null] } = {}) {
  const realFetch = globalThis.fetch;
  let n = 0;
  globalThis.fetch = async (input, init) => {
    const url = typeof input === "string" ? input : input.url;
    const o = BY_ID.get(url.match(/\/(?:objects|made)\/([^/]+)\.glb$/)?.[1]);
    if (!o) return realFetch(input, init);
    const wait = delay(n++, o);
    if (wait > 0) await new Promise((done) => setTimeout(done, wait));
    const [w, d, h] = o.size;
    const bytes = boxGlb(w, h, d, nodes(o));
    // no body stream: three's loader would report progress with ProgressEvent, which Node lacks
    return { status: 200, ok: true, url, body: undefined, arrayBuffer: async () => bytes.buffer };
  };
}
