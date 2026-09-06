// Placeholder shell: a simple two-storey box with a flat roof.
// The builder agent replaces this with the real house.

export function buildShell({ house, group }) {
  const { perimeterWalls, windowUnit, door, slidingDoor, slab, flatRoof, mat } = house;
  const W = 10, D = 8, H = 3;
  const plaster = mat.plaster("#e9e6dd");
  // footprint corners: NW, NE, SE, SW  → edges 0 = north, 1 = east, 2 = south, 3 = west
  const footprint = [[-W / 2, -D / 2], [W / 2, -D / 2], [W / 2, D / 2], [-W / 2, D / 2]];

  group.add(slab({ polygon: footprint, y: 0.05, thickness: 0.3 }));

  const win = (offset, w = 1.4) => ({ offset, sill: 0.9, width: w, height: 1.3, kind: "window" });
  for (let floor = 0; floor < 2; floor++) {
    group.add(
      perimeterWalls({
        polygon: footprint,
        height: H,
        y: floor * H,
        material: plaster,
        openings: {
          0: [win(2), win(6.5)], // north
          1: [win(3, 1.2)], // east
          2: floor === 0 ? [{ offset: 3.5, sill: 0, width: 2.4, height: 2.2, kind: "sliding" }] : [win(2), win(6.5)], // south
          3: floor === 0 ? [{ offset: 3.4, sill: 0, width: 1.0, height: 2.1, kind: "door" }] : [], // west
        },
        makeUnit: (o, edge) =>
          o.kind === "door"
            ? door({ width: o.width, height: o.height })
            : o.kind === "sliding"
              ? slidingDoor({ width: o.width, height: o.height })
              : windowUnit({ width: o.width, height: o.height, shutter: edge === 0 ? "roller" : "none" }),
      }),
    );
  }
  group.add(flatRoof({ polygon: footprint, y: 2 * H + 0.3, thickness: 0.3, parapet: 0.35 }));
}
