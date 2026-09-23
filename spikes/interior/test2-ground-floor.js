// Ground floor (sheet 3, "03 | REZ-DE-CHAUSSEE", 1:100), read from the chained dimensions.
// Interior faces of the 40 cm exterior walls: x ±5.10, z ±4.60. Walls by centre line.
import { floorPlan } from "housekit/interior";
import { LEVEL } from "./dimensions.js";

const GF = {
  rooms: [
    { name: "Cuisine / séjour", use: "kitchen-living", floor: "oak", polygon: [[-5.1, -2.45], [-1.275, -2.45], [-1.275, 4.6], [-5.1, 4.6]] },
    { name: "Salon", use: "living", floor: "oak", polygon: [[-1.275, 1.61], [5.1, 1.61], [5.1, 4.6], [-1.275, 4.6]] },
    { name: "WC", use: "wc", floor: "tile", polygon: [[-1.025, -0.98], [-0.125, -0.98], [-0.125, 1.51], [-1.025, 1.51]] },
    { name: "Hall app. 1", use: "hall", floor: "tile", polygon: [[1.025, -0.4], [5.1, -0.4], [5.1, 0.9], [3.81, 0.9], [3.81, 1.51], [2.585, 1.51], [2.585, 0.6], [1.025, 0.6]] },
    { name: "Escalier app. 1", use: "stair", floor: "tile", polygon: [[0.125, -0.4], [1.025, -0.4], [1.025, 0.6], [2.585, 0.6], [2.585, 1.51], [0.125, 1.51]] },
    { name: "Chambre 1", use: "bedroom", floor: "oak", polygon: [[2.105, -4.6], [5.1, -4.6], [5.1, -1.1], [3.81, -1.1], [3.81, -0.5], [0.125, -0.5], [0.125, -2.11], [2.105, -2.11]] },
    { name: "SDB", use: "bath", floor: "tile", polygon: [[0.125, -4.6], [2.005, -4.6], [2.005, -2.21], [0.125, -2.21]] },
    { name: "Armoire chambre", use: "storage", floor: "oak", polygon: [[3.91, -1.0], [5.1, -1.0], [5.1, -0.5], [3.91, -0.5]] },
    { name: "Armoire hall", use: "storage", floor: "tile", polygon: [[3.91, 0.95], [5.1, 0.95], [5.1, 1.51], [3.91, 1.51]] },
    { name: "Escalier app. 2", use: "stair", floor: "tile", polygon: [[-5.1, -4.6], [-3.01, -4.6], [-3.01, -2.7], [-5.1, -2.7]] },
    { name: "Hall app. 2", use: "hall", floor: "tile", polygon: [[-2.89, -4.6], [-0.125, -4.6], [-0.125, -1.23], [-1.025, -1.23], [-1.025, -2.7], [-2.89, -2.7]] },
  ],
  partitions: [
    // masonry, 25 cm
    { from: [-5.1, -2.575], to: [-1.025, -2.575], thickness: 0.25 },
    { from: [-1.15, -2.7], to: [-1.15, 1.61], thickness: 0.25 },
    { from: [0, -4.6], to: [0, 1.51], thickness: 0.25 },
    { from: [-1.025, -1.105], to: [-0.125, -1.105], thickness: 0.25 },
    // app. 2 stair side, 12 cm
    { from: [-2.95, -3.85], to: [-2.95, -2.7], thickness: 0.12 },
    // light partitions, 10 cm
    { from: [0.125, -2.16], to: [2.005, -2.16], openings: [{ offset: 0.79, width: 0.78, door: { hinge: "end", swing: "left" } }] },
    { from: [2.055, -4.6], to: [2.055, -2.0] },
    { from: [2.055, -1.05], to: [2.055, -0.45] },
    { from: [0.125, -0.45], to: [3.91, -0.45], openings: [{ offset: 2.64, width: 0.88, door: { hinge: "end", swing: "left" } }] },
    { from: [3.86, -1.05], to: [3.86, -0.45] },
    { from: [3.81, -1.05], to: [5.1, -1.05] },
    { from: [1.025, 0.55], to: [2.685, 0.55] },
    { from: [-1.025, 1.56], to: [5.1, 1.56], openings: [
      { offset: 0.07, width: 0.78, door: { hinge: "start", swing: "left" } },
      { offset: 3.71, width: 0.88, door: { hinge: "start", swing: "left" } },
    ] },
    { from: [3.86, 0.9], to: [3.86, 1.56] },
  ],
};

export function buildInterior({ group }) {
  group.add(floorPlan({ y: LEVEL.ground, height: 2.5, ...GF }));
}
