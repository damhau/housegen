// Placeholder garden: a path to the door, a couple of trees and a hedge.

export function buildGarden({ house, group }) {
  const { pathway, tree, hedge, bush } = house;
  group.add(pathway({ points: [[-5, 0], [-9, 0], [-9, 8]], width: 1.2 }));
  group.add(tree({ position: [9, 0, 7], height: 7, kind: "round", seed: 7 }));
  group.add(tree({ position: [-8, 0, -7], height: 5, kind: "conifer", seed: 2 }));
  group.add(hedge({ from: [-12, 10], to: [12, 10], height: 1.2 }));
  group.add(bush({ position: [6, 0, 5], radius: 0.7 }));
}
