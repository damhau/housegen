// Placeholder garden: gently sloping ground, a drive, pebbles along the house, trees, a swing.
// (Exercises the landscape components; the builder agent replaces all of it.)

export function buildGarden({ house, group, ground }) {
  const { terrain, ribbon, pebbleStrip, leafTree, leafBush, hedge, swingSet, bench, bicycle } = house;

  // ground falls ~1.2 m from the north-west corner to the south-east; flat under the house
  group.add(
    terrain({
      size: [90, 90],
      points: [[-12, -12, 1.2], [12, -12, 0.4], [-12, 12, 0.4], [12, 12, -0.6], [0, 0, 0], [-6, 0, 0], [6, 0, 0], [0, -5, 0], [0, 5, 0]],
    }),
  );
  ground.visible = false;

  group.add(ribbon({ points: [[-5.5, 3.4], [-9, 3.4], [-12, 8], [-13, 14]], width: 1.4 }));
  group.add(pebbleStrip({ from: [-5.3, -4.3], to: [5.3, -4.3] }));
  group.add(pebbleStrip({ from: [5.3, -4.3], to: [5.3, 4.3] }));

  group.add(leafTree({ position: [9.5, 7.5], height: 8, spread: 3.5, kind: "broadleaf", seed: 7 }));
  group.add(leafTree({ position: [-8.5, -8], height: 9, spread: 3, kind: "pine", seed: 2 }));
  group.add(leafBush({ position: [6.5, 5.2], radius: 0.8, seed: 4 }));
  group.add(leafBush({ position: [-6.8, 5.0], radius: 0.6, seed: 9, color: "#4f7a3a" }));
  group.add(hedge({ from: [-12, 11], to: [12, 11], height: 1.2 }));
  group.add(swingSet({ position: [-4, 9], rotationY: 0.3 }));
  group.add(bench({ position: [3, 7.5], rotationY: Math.PI }));
  group.add(bicycle({ position: [-4.4, -4.9], rotationY: 0.1 }));
}
