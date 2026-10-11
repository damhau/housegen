// The fixture showroom (lighting.js): one windowless white booth per fixture, open onto a corridor,
// so each fixture's own light shows on its walls, ceiling and floor. backend/scripts/fixture_sheet.py
// walks along the corridor and photographs each booth.
//
//   ?show=globePendant,globePendant~glass=clear,downlight~mount=recessed~finish=white
//      which fixtures (a family, then ~param=value pairs; numbers and true/false are parsed);
//      default: every registered family with its defaults
//   ?lamps=all   (the runtime's switch) lights them all; by day only the accents would shine
//
// Booth i: x from i * (W + T) to that + W, z from -D to 0; the corridor: z from 0 to C.
// buildScene returns `booths`: per booth its label, centre x, and the spot the camera stands on.

import * as THREE from "three";
import { floorPlan } from "housekit/interior";
import fx from "housekit/furnish";

const W = 2.4, D = 2.4, T = 0.12, C = 2.4, H = 2.5, WALL = 0.25;

function parseShow(q) {
  if (!q) return Object.keys(fx.FIXTURES).map((name) => ({ name, params: {} }));
  return q.split(",").filter(Boolean).map((item) => {
    const [name, ...pairs] = item.split("~");
    const params = {};
    for (const p of pairs) {
      const [k, v] = p.split("=");
      params[k] = v === "true" ? true : v === "false" ? false : Number.isFinite(Number(v)) && v !== "" ? Number(v) : v;
    }
    return { name, params };
  });
}

const label = ({ name, params }) => [name, ...Object.entries(params).map(([k, v]) => `${k}=${v}`)].join(" ");

export async function buildScene({ house, group }) {
  const { perimeterWalls, slab, mat } = house;
  const show = parseShow(new URLSearchParams(location.search).get("show"));
  const n = show.length, L = n * W + (n - 1) * T;
  const white = mat.plaster("#f2f1ed");

  // the shell: a slab, windowless walls whose inside faces bound the booths and the corridor, a roof
  const outer = [[-WALL, -D - WALL], [L + WALL, -D - WALL], [L + WALL, C + WALL], [-WALL, C + WALL]];
  group.add(slab({ polygon: outer, y: 0, thickness: 0.3 }));
  group.add(perimeterWalls({ polygon: outer, height: H + 0.3, thickness: WALL, material: white }));
  group.add(slab({ polygon: outer, y: H + 0.6, thickness: 0.3 }));

  const rooms = [], partitions = [];
  show.forEach((s, i) => {
    const x0 = i * (W + T);
    rooms.push({ name: `Booth ${i + 1}`, use: "living", polygon: [[x0, -D], [x0 + W, -D], [x0 + W, 0], [x0, 0]], floor: "oak-light" });
    if (i < n - 1) partitions.push({ from: [x0 + W + T / 2, -D], to: [x0 + W + T / 2, 0], thickness: T });
  });
  rooms.push({ name: "Corridor", use: "hall", polygon: [[0, 0], [L, 0], [L, C], [0, C]], floor: "oak-light" });
  group.add(floorPlan({ y: 0, height: H, rooms, partitions }));

  const Y = 0.015; // the floor finish
  const ceilingY = fx.lighting.ceilingUnderside(0, H); // where ceiling fixtures are fixed
  const booths = [];
  for (const [i, s] of show.entries()) {
    const x0 = i * (W + T), cx = x0 + W / 2;
    const spec = fx.FIXTURES[s.name];
    let f;
    try {
      f = fx.fixture(s.name, s.params);
    } catch (err) {
      console.error(`showroom: ${label(s)}: ${err.message}`);
      booths.push({ label: label(s), x: cx, error: err.message, at: [cx, 1.3] });
      continue;
    }
    if (spec.kind === "ceiling") {
      group.add(fx.place(f, [cx, -D / 2], 0, ceilingY));
      // something under a pendant to catch its pool (a low table), none under the flush ones
      if ((f.userData.height ?? 0) > 0.25) {
        const t = new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.04, 0.9), fx.finish.oak());
        t.position.set(cx, Y + 0.74, -D / 2);
        t.castShadow = t.receiveShadow = true;
        const leg = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.05, 0.72, 16), fx.finish.black());
        leg.position.set(cx, Y + 0.36, -D / 2);
        group.add(t, leg);
      }
    } else if (spec.kind === "wall") {
      group.add(fx.place(f, [cx, -D], 0, Y + (s.params.y ?? 1.8)));
    } else if ((f.userData.height ?? 0) < 0.9) {
      // a table lamp on a side table against the back wall
      const st = new THREE.Mesh(new THREE.BoxGeometry(0.6, 0.55, 0.4), fx.finish.lacquer("#ece9e2"));
      st.position.set(cx, Y + 0.275, -D + 0.25);
      st.castShadow = st.receiveShadow = true;
      group.add(st, fx.place(f, [cx, -D + 0.25], 0, Y + 0.55));
    } else {
      group.add(fx.place(f, [cx + 0.5, -D + 0.45], 0, Y));
    }
    booths.push({ label: label(s), x: cx, family: s.name, role: f.userData.fixture.role, at: [cx, 1.25] });
  }
  return { views: {}, booths };
}
