# Light fixtures (kit/lighting*.js)

Fixtures are kit pieces that carry their own light. They replace the builder's habit of a bare
`PointLight` next to a mesh, which lit walls through shades and made hot spots (2026-10-11 pilot:
a wall light's bulb 13 cm from the wall, a flush light's 17 cm under the ceiling). The benchmark is
the Clos des Hêtres tour (Lausanne): about a dozen different fixtures in nine pictures, every one a
design piece.

## Files

| File | What | Owner |
|---|---|---|
| `kit/lighting.js` | the contract (header), the registry `FIXTURES`, `fixture()`, `fixtureReference()`, the daytime switch `applyLampMode()` | integration only |
| `kit/lighting-parts.js` | units, finishes, glowing materials, suspension, `addGlow`, `addCone`, `tagFixture`, `ceilingUnderside` | integration only |
| `kit/lighting-pendants.js` | pendants (`PENDANTS`) | one family file per agent |
| `kit/lighting-wall.js` | wall lights (`WALL`) | |
| `kit/lighting-standing.js` | table, floor and desk lamps (`STANDING`) | |
| `kit/lighting-built-in.js` | downlights, tracks, strips (`BUILT_IN`) | |
| `kit/test/lighting.test.mjs` | the contract, checked for every registered family | |
| `kit/showroom/` | one windowless booth per fixture (the catalogue's scene) | |
| `backend/scripts/fixture_sheet.py` | the catalogue: each booth photographed on the GPU | |

A family file exports one object: `{ familyName: { make, kind, doc } }`. `make(params)` returns the
fixture; `kind` is `"ceiling"`, `"wall"` or `"standing"`; `doc` is one line for the builder,
starting with `familyName(` and listing the parameters with their defaults.

## The contract (the test checks every point)

- **Tag:** `tagFixture(g, { name, family, role, finish, footprint, height, hang, wall })`. The name must
  read as a lamp to the layout audit (it contains lamp, light, pendant, sconce, chandelier or
  luminaire).
- **Frame:** metres.
  - Ceiling fixtures: origin at the fixing point, everything below it (y ≤ 0). They are placed at
    `ceilingUnderside(storeyY, height)`.
  - Wall fixtures: origin at the centre of the back plate, facing +z, nothing behind z = 0. Tag them
    `wall: true` (a cove strip too): `fx.onWall` then puts them flat on the wall.
  - Standing lamps: origin at the bottom centre of the base.
- **Role:** `"ceiling"` is general light, off by day. `"accent"` is bedside, table, floor and wall
  lights, a pendant over a table or an island, strips: on by day. Give a pendant an `over` parameter
  when it can hang over a table (then `accent`).
- **Light:** `addGlow` (a point: what shines every way) and `addCone` (a spot: what a shade,
  reflector or lens throws one way). No shadows; the spot's target stays in the fixture
  (`addCone` does it). At most 3 lights per fixture: the walk keeps 6 points and 6 spots live.
- **Glowing parts:** `glowMaterial`, `opal`, `bulbGlow`, made per fixture (never cached): the
  runtime turns them off with the fixture's lights.
- **Finishes:** a `finish` parameter that accepts every name in `FINISHES` (brass, black, white,
  chrome, bronze). Glass, fabric and paper are materials of their own.
- **Budget:** at most 20,000 triangles per fixture. Aim for 2–8k: a storey has 10+ fixtures.

## Light, by eye and by number

- **Flux, in lumens:**

  | Fixture | Lumens |
  |---|---|
  | A 60 W-like bulb | 800 |
  | Downlight | 400–600 |
  | Pendant over a table | 600–1,200 |
  | Large LED ring or linear pendant | 1,500–2,500 |
  | Table lamp | 250–400 |
  | Wall light | 300–500 |
  | Strip | about 300 per metre |

  `glowIntensity` and `coneIntensity` convert lumens; they are calibrated on the lamps the builders
  placed before.
- **Colour:** `WARM` (2700 K) by default, `NEUTRAL` for kitchens and baths if a family offers it.
- **No hot spots:**
  - Never put a point light within 15 cm of the surface the fixture is fixed to.
  - A wall light throws cones up and/or down along the wall (the scallop).
  - An opaque shade throws a cone down (plus a small glow up if its top is open).
  - An opal or paper shade glows: a point inside it, at a modest flux.
- **The fixture is a design object first:** by day, ceiling fixtures are seen switched off. Get the
  proportions, the thin cables and rods, the canopy and the joints right.

## Checking your work

```bash
# tests (from the repository root of your checkout)
cd kit && node --test test/*.test.mjs
# the catalogue on the GPU: your families and their variants, lit (an evening view)
uv run --project /home/damien/code/perso/housegen/backend python backend/scripts/fixture_sheet.py \
  --show "discArms,discArms~finish=black,facetedCluster" \
  --chrome /tmp/claude-1000/-home-damien-code-perso-housegen/fce83024-2d45-49cc-975a-561a3edc8a34/scratchpad/cft/chrome/linux-145.0.7632.159/chrome-linux64/chrome \
  --out /tmp/fixtures-<you>
# the same by day (only the accents lit): --lamps day
```

- **Showroom placement:**
  - ceiling fixtures at the booth's centre, with a table under any fixture taller than 25 cm;
  - wall fixtures on the back wall at 1.8 m (`~y=1.4` sets another height);
  - table lamps on a side table;
  - floor lamps on the floor.
- **The GPU is shared.** The script waits for `/tmp/housegen-gpu.lock`. Do not run other renders.
  The page must report D3D12; the script stops otherwise.
- **Look at the sheet** next to the benchmark pictures:
  - living room, glass globes with bare bulbs, an LED ring, small pendants over the bar:
    `/tmp/claude-1000/-home-damien-code-perso-housegen/fce83024-2d45-49cc-975a-561a3edc8a34/images/3.png`;
  - WC, LED bar over the mirror: `.../images/4.png`;
  - hall, black surface downlight, recessed spots: `.../images/5.png`;
  - bedroom, black multi-arm fixture with brass discs, globe bedside lamp: `.../images/6.png`;
  - bedroom, faceted metal cluster, hexagonal wall light, brass floor lamp, LED strip in a ceiling
    recess, black desk lamp: `.../images/7.png`;
  - bedroom, brass rectangle frame with opal globes, articulated desk lamp: `.../images/10.png`.
