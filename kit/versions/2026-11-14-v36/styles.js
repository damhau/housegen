// housekit styles (#76): a house's look as a named choice, for each role, of an entry of the kit's catalogue.
// A role is what a scene asks for by name and leaves to the style: a fixture's finish, the bath fittings, the wall
// tiles, the bedding… A style names an entry for every role; the house style, src/style.js in the scene, picks a
// style and may override any role for the whole house or for one room:
//
//   export default { style: "contemporary", fixtures: "brass", rooms: { "SDB — App. 1": { wallTile: "white" } } };
//
// The most specific wins: the piece's own setting, the room's override, the house's, the style's, the fallback
// (the kit's defaults). The runtime reads src/style.js before buildScene; a scene without it draws in
// DEFAULT_STYLE, today's look. A role's value may also be one entry per kind ({ default, globePendant: "white" }:
// a fixture family's own finish).

/** The roles, and the catalogue each one names an entry of. */
export const ROLES = {
  fixtures: "decorative light fixtures' finish: lighting-parts.js FINISHES, per family",
  builtIns: "built-in fixtures' finish (downlight, track, flushCeiling, stepLight): FINISHES",
  fittings: "taps, mixers, shower, flush plate, bath accessories, kitchen mixer: furnish.js FITTINGS",
  wallTile: "tileWalls and the tiled fronts of wc, bathtub and shower: finishes.js WALL_TILES",
  bathFloor: "the \"tile\" floor of a bath or a WC: finishes.js FLOOR_TILES",
  bedding: "bed dressing and its cushions: furnish.js BEDDING",
  featureWall: "a wall behind each double bed: interior.js FEATURE_WALLS",
};

export const STYLES = {
  // today's look: light oak, white, grey and sand textiles, chrome; fixtures white and black, built-ins white
  scandinavian: {
    fixtures: { default: "black", globePendant: "white", globeSconce: "white", mushroomLamp: "white" },
    builtIns: "white",
    fittings: "chrome",
    wallTile: "white",
    bathFloor: "tile",
    bedding: "linen",
    featureWall: "none",
  },
  // the interior pilot's (spikes/interior-pilot, sheet C): dark accents on white walls, matte black fittings, grey
  // veined marble in the baths, olive and grey bedding, a panelled wall behind the bed
  contemporary: {
    fixtures: "black",
    builtIns: "white",
    fittings: "black",
    wallTile: "marble-grey",
    bathFloor: "marble-light",
    bedding: "olive",
    featureWall: "panelled",
  },
};

export const DEFAULT_STYLE = "scandinavian";

/** The kit's defaults: what a role is when neither the scene nor the style says. */
export const FALLBACK = { fixtures: "black", builtIns: "white", fittings: "chrome", wallTile: "white", bathFloor: "tile",
  bedding: "linen", featureWall: "none" };

let house = {};

/** The house style (src/style.js's default export); {} for none. */
export function setHouseStyle(style) {
  house = style && typeof style === "object" ? style : {};
  if (house.style !== undefined && !STYLES[house.style]) {
    console.warn(`housekit: unknown style "${house.style}" in src/style.js (known: ${Object.keys(STYLES).join(", ")}): ${DEFAULT_STYLE}`);
  }
}

/** The name of the style the house draws in. */
export const styleName = () => (STYLES[house.style] ? house.style : DEFAULT_STYLE);

const pick = (v, kind) => (v !== null && typeof v === "object" ? v[kind] ?? v.default : v);

/**
 * The entry a role takes: `piece` (the piece's own setting), then the house style's override for `room` (a room's
 * name), the house's, the style's, the fallback. `kind` picks within a value given per kind (a fixture's family).
 */
export function look(role, { room, piece, kind } = {}) {
  const levels = [piece, room == null ? undefined : house.rooms?.[room]?.[role], house[role], STYLES[styleName()][role], FALLBACK[role]];
  for (const level of levels) {
    const v = pick(level, kind);
    if (v != null) return v;
  }
  return undefined;
}

const _warned = new Set();

/** The `catalogue` entry `role` resolves to (look's options); the fallback's, with a warning, for an unknown name. */
export function entry(catalogue, role, options = {}) {
  const name = look(role, options);
  if (name in catalogue) return catalogue[name];
  if (!_warned.has(`${role}:${name}`)) {
    _warned.add(`${role}:${name}`);
    console.warn(`housekit: unknown ${role} "${name}" (known: ${Object.keys(catalogue).join(", ")}): ${FALLBACK[role]}`);
  }
  return catalogue[FALLBACK[role]];
}

export default { ROLES, STYLES, DEFAULT_STYLE, FALLBACK, setHouseStyle, styleName, look, entry };
