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
// a fixture family's own finish); a fixture family's entry may carry its parts with the finish ({ finish: "black",
// canopy: "oak" }: lighting.js fixture()). A map role (MAPS) names an entry per name only: { "dining-chair-grey":
// "dining-chair-black" } draws that model as the other, and a name it does not list stays itself.
// The catalogue's decor and furniture by role (objects.js) name the styles each piece suits (`styles`, #100): a house
// draws only its style's pieces (furnish.js pickObject); the photo pipeline's library and wall art do the same.

/** The roles, and the catalogue each one names an entry of. */
export const ROLES = {
  fixtures: "decorative light fixtures' finish: lighting-parts.js FINISHES, per family, or { finish, ...parts } (the family's options)",
  builtIns: "built-in fixtures' finish (downlight, track, flushCeiling, stepLight): FINISHES",
  fittings: "taps, mixers, shower, flush plate, bath accessories, kitchen mixer: furnish.js FITTINGS",
  wallTile: "tileWalls and the tiled fronts of wc, bathtub and shower: finishes.js WALL_TILES",
  bathFloor: "the \"tile\" floor of a bath or a WC: finishes.js FLOOR_TILES",
  bedding: "bed dressing and its cushions: furnish.js BEDDING",
  featureWall: "a wall behind the double bed of each unit's main bedroom: interior.js FEATURE_WALLS",
  handles: "kitchen units' handles: furnish.js HANDLES (none: handleless, a grip rail under the worktop)",
  rug: "a rug the scene gives no colour: furnish.js RUGS",
  sofaDressing: "cushions and a throw on every sofa: furnish.js SOFA_DRESSINGS",
  furnitureTone: "the tone catalogue furniture prefers, per role (dining-chair, coffee-table…): furnish.js FURNITURE_TONES",
  accents: "the accents rooms take, one each, for their catalogue decor and a catalogue bed's duvet: furnish.js ACCENTS",
  models: "a named model drawn as another named model or a catalogue role, per name: furnish.js MODELS and ROLES",
};

/** Roles whose value maps names to names ({ "<model>": "<model or role>" }): no default, nothing when unnamed. */
export const MAPS = new Set(["models"]);

export const STYLES = {
  // today's look: light oak, white, grey and sand textiles, chrome, handleless kitchens; decorative fixtures black and
  // white with oak and linen (an oak canopy, collar, bar, stem or foot; a linen shade; white discs and linings), so
  // each reads against a white ceiling and wall; built-ins white
  scandinavian: {
    fixtures: {
      default: "black",
      globePendant: { finish: "black", canopy: "oak" },
      facetedCluster: { finish: "black", inside: "white" },
      discArms: { finish: "black", rim: "white" },
      barCluster: { finish: "black", collar: "oak" },
      linearPendant: { finish: "black", bar: "oak" },
      domePendant: { finish: "black", shade: "linen", canopy: "oak" },
      hexSconce: { finish: "black", inner: "white" },
      globeSconce: { finish: "black", plate: "oak" },
      globeTableLamp: { finish: "black", base: "oak" },
      mushroomLamp: { finish: "white", foot: "oak" },
      globeFloorLamp: { finish: "black", stem: "oak" },
    },
    builtIns: "white",
    fittings: "chrome",
    wallTile: "white",
    bathFloor: "tile",
    bedding: "linen",
    featureWall: "none",
    handles: "none",
    rug: "sand",
    sofaDressing: "none",
    furnitureTone: "any",
    accents: "colours",
    models: {},
  },
  // the interior pilot's (spikes/interior-pilot, sheet C): dark accents on white walls, matte black fittings and
  // handles, grey veined marble in the baths, olive and grey bedding, a panelled wall behind the bed, a charcoal rug,
  // olive and sand cushions on the sofa, dark chairs and tables, rooms in olive, sand or grey
  contemporary: {
    fixtures: "black",
    builtIns: "white",
    fittings: "black",
    wallTile: "marble-grey",
    bathFloor: "marble-light",
    bedding: "olive",
    featureWall: "panelled",
    handles: "black",
    rug: "charcoal",
    sofaDressing: "olive",
    furnitureTone: { default: "any", "dining-chair": "dark", "coffee-table": "dark", "side-table": "dark", sideboard: "dark" },
    accents: "muted",
    // not the oval coffee tables: white and black, they are a nesting pair
    models: { "dining-chair-grey": "dining-chair-black" },
  },
};

export const DEFAULT_STYLE = "scandinavian";

/** The kit's defaults: what a role is when neither the scene nor the style says. */
export const FALLBACK = { fixtures: "black", builtIns: "white", fittings: "chrome", wallTile: "white", bathFloor: "tile",
  bedding: "linen", featureWall: "none", handles: "none", rug: "sand", sofaDressing: "none", furnitureTone: "any",
  accents: "colours", models: {} };

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

/** The house style's own setting of `role` for the room named `room` (src/style.js "rooms"), undefined without one. */
export const roomLook = (role, room) => house.rooms?.[room]?.[role];

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

export default { ROLES, MAPS, STYLES, DEFAULT_STYLE, FALLBACK, setHouseStyle, styleName, look, roomLook, entry };
