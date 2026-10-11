// housekit lighting: wall lights (their back plate on the wall, facing +z into the room). See lighting.js for the contract.

import * as THREE from "three";
import { metal, cyl, lit, addGlow, addCone, tagFixture, opal, glowMaterial } from "./lighting-parts.js";

/** The family's entries for lighting.js FIXTURES: { make, kind, doc }. */
export const WALL = {};
