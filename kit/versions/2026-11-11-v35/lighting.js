// housekit lighting: light fixtures that carry their own light (#41 point 3, interior pilot).
//
// A fixture is a Group tagged like the other pieces ({ kind: "furniture", name, footprint, height })
// plus `fixture: { family, role, finish }`, holding its mesh and its lights:
//   - a cone (THREE.SpotLight): the light a shade, a reflector or a lens throws one way
//     (a downlight's pool, an opaque pendant's light on the table, a wall light's wash up or down);
//   - a glow (THREE.PointLight): what shines every way (an opal globe, a bare bulb, a shade's sides).
// No light casts shadows (the runtime keeps a fixed number of live lights, see runtime.js "Lamps").
// The parts that shine use a glow material (glowMaterial): the runtime dims them with the lights.
//
// Roles (the property-photo rule, decided 2026-10-11): by day the general light is off and only the
// accents glow; ?lamps=all lights everything (an evening, the fixture catalogue), ?lamps=off nothing.
//   "ceiling": general light — ceiling lights, downlights, a pendant over nothing in particular
//   "accent":  bedside, table, floor and wall lights, a pendant over a table or an island, strips
//
// Frame: metres. A ceiling fixture's origin is its fixing point on the ceiling (place it at the
// ceiling's underside: fx.place(f, [x, z], rot, ceilingUnderside(y, height))); a wall light's, the
// centre of its back plate on the wall (facing +z, into the room); a standing lamp's, the bottom
// centre of its base.
// Families live in lighting-pendants.js, lighting-wall.js, lighting-standing.js, lighting-built-in.js
// (made of lighting-parts.js) and are gathered in FIXTURES; fixture(name, params) builds one;
// fixtureReference() documents them for the builder (agent/prompts.py lists it).

export * from "./lighting-parts.js";
import { PENDANTS } from "./lighting-pendants.js";
import { WALL } from "./lighting-wall.js";
import { STANDING } from "./lighting-standing.js";
import { BUILT_IN } from "./lighting-built-in.js";

// ---------------------------------------------------------------- registry

/**
 * Every family: `make(params)`, its `kind` ("ceiling" | "wall" | "standing"), its default `role` and
 * one line for the builder's reference, gathered from the family modules.
 */
export const FIXTURES = { ...PENDANTS, ...WALL, ...STANDING, ...BUILT_IN };

/** A fixture by family name: fixture("downlight", { mount: "recessed" }). */
export function fixture(name, params = {}) {
  const spec = FIXTURES[name];
  if (!spec) throw new Error(`unknown fixture ${name}. Known: ${Object.keys(FIXTURES).join(", ")}`);
  return spec.make(params);
}

/** The builder's reference: one line per family. */
export function fixtureReference() {
  return Object.entries(FIXTURES).map(([, s]) => `- ${s.doc}`).join("\n");
}

// ---------------------------------------------------------------- the runtime's switch

/** Is a fixture of `role` on in `mode` ("day" | "all" | "off")? */
export const isOn = (role, mode) => mode === "all" || (mode === "day" && role === "accent");

/**
 * Switch every fixture under `root` for `mode`: an off fixture's lights are hidden (the runtime's
 * lamp budget ignores them) and its glowing parts go dark; an on one's are as built.
 */
export function applyLampMode(root, mode = "day") {
  let on = 0, off = 0;
  root.traverse((o) => {
    const f = o.userData?.fixture;
    if (!f) return;
    const shining = isOn(f.role, mode);
    shining ? on++ : off++;
    o.traverse((c) => {
      if (c.isLight) c.visible = shining;
      const mats = c.isMesh ? (Array.isArray(c.material) ? c.material : [c.material]) : [];
      for (const m of mats) if (m.userData?.glow !== undefined) m.emissiveIntensity = shining ? m.userData.glow : 0;
    });
  });
  return { on, off };
}
