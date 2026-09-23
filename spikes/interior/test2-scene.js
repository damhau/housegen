import { buildSite } from "./site.js";
import { buildShell } from "./shell.js";
import { buildRoof } from "./roof.js";
import { buildInterior } from "./interior.js";
import { buildFurniture } from "./furniture.js";
import { loadFinishes } from "housekit/finishes";

export async function buildScene(ctx) {
  await loadFinishes(); // textured floors, walls and furniture
  // Terrain is registered first so all paths and boundary elements can follow it.
  buildSite(ctx);
  buildShell(ctx);
  buildRoof(ctx);
  buildInterior(ctx);
  await buildFurniture(ctx);

  return {
    views: {
      site: { position: [28, 22, 31], target: [0, 3.0, 1] },
      entrance: { position: [15, 4.0, -9], target: [3, 2.6, -3] },
      // eye height 1.6 m above the ground floor (0.15)
      "in-salon": { position: [4.8, 1.75, 4.3], target: [-3.0, 1.3, 1.2], fov: 70 },
      "in-kitchen": { position: [-4.8, 1.75, 4.3], target: [-2.6, 1.2, -2.4], fov: 70 },
      "in-chambre": { position: [4.8, 1.75, -1.3], target: [1.2, 1.2, -4.3], fov: 70 },
      "close-sofa": { position: [2.9, 1.35, 2.2], target: [0.6, 0.6, 3.6], fov: 55 },
      "close-kitchen": { position: [-2.4, 1.6, -0.6], target: [-3.6, 0.9, -2.3], fov: 55 },
      "close-bed": { position: [4.6, 1.5, -1.6], target: [3.3, 0.5, -3.6], fov: 55 },
      "in-hall": { position: [4.7, 1.75, 0.25], target: [0.4, 1.3, 0.0], fov: 70 },
    },
  };
}
