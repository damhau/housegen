// housekit/furnish as the recorder sees it: the same default export, its piece makers tagging
// what they build with their call (rec-tag.js). Calls the kit makes internally are not recorded.
import fx from "housekit/furnish";
import { wrap } from "./rec-tag.js";

const PIECES = [
  "sofa", "bed", "nightstand", "chair", "diningSet", "wardrobe", "kitchenRun", "wc", "basin", "bathtub",
  "shower", "towelRail", "coatHooks", "bench", "washer", "bathAccessories", "towelStack", "laundryBasket",
  "rug", "ceilingLight", "model", "fixture",
];

const rec = { ...fx };
for (const n of PIECES) if (typeof fx[n] === "function") rec[n] = wrap({ kit: n }, fx[n]);
export default rec;
