// housekit/interior as the recorder sees it: floorPlan and tileWalls tag what they build.
export * from "housekit/interior";
import { floorPlan as floorPlanKit, tileWalls as tileWallsKit } from "housekit/interior";
import { wrap } from "./rec-tag.js";

export const floorPlan = wrap({ kit: "floorPlan" }, floorPlanKit);
// tileWalls(group, room, options): the group is where it looks for walls and openings, not data
export const tileWalls = (group, ...rest) => {
  const g = tileWallsKit(group, ...rest);
  return wrap({ kit: "tileWalls" }, () => g)(...rest);
};
