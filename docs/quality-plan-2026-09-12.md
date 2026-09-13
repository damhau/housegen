# Quality plan: from a study model to a semi-photorealistic view (2026-09-12)

The goal is a real-time view of a house, built from its plans and photographs, that an owner
reads as a picture of their house and not as a model of it. Two things have to be true at once
and they are measured separately:

- **Fidelity**: the geometry, openings, materials and site are the owner's house. Judged by the
  critic against the photographs (or the elevation sheets) and by a person reading its findings.
- **Looks**: the picture reads as a photograph. Judged by a person on a look sheet of the same
  version from the same cameras, never by the critic (its prompt tells it to ignore lighting and
  style, and that is right: a model scoring "realism" drifts with its prompt).

This file is the order of work, what each step changes, how it is measured and what it must not
touch. It builds on `build-path-2026-09-07.md` (the freeze and its rule) and
`presentation-look.md` (the two render paths and the calibration).

## The principle every step follows

**The builder describes the house semantically; the presentation path decides how it is drawn.**

The kit already tags what it builds (`userData.kind`, the material cache keyed by name, a tree's
kind, seed, height and spread, a window's width and height). A presentation page can walk the
built scene after `buildScene` and swap plaster for textured plaster, a chip tree for a real one,
a glass card for glass over a dark room, without moving one diagnostic pixel. That is why the
September 7 rendering track failed and this plan will not: those five changes altered what the
builder saw, landed together, and nobody looked at a render. Here every looks change is
presentation-only, lands alone, and is looked at on two real projects before it merges.

Changes that do touch the build path (kit components the builder is told about, prompt text,
tool behaviour) are batched into as few measured runs as possible, each compared with a v0.1.0
run of the same project as the freeze rule requires.

## Where we start

Measured on the dev environment on 2026-09-12:

| what | value | source |
|---|---|---|
| render call, 4-9 views at quality=high | 5-8 s on the Modal T4 (47 s before, in software on the pod) | `render.remote.done` lines |
| generate job, Montelly, gpt-6-astra xhigh | 11:22 wall, 14 turns, $5.10, of which $3.04 uncached input | run summary |
| cache misses per job | 5 (2-3 consecutive turns after every image prune) | `llm.turn` lines |
| what the builder writes on Montelly | 81 `house.box`, 20 `slab`, 15 `rod`, 4 `windowUnit`, 4 `perimeterWalls`, 5 `wall` | grep over `scene/src` |
| meshes per window on Montelly | about 10 (unit + backdrop + trim + slats + grille) | `openings.js` of the project |

What the Montelly scene sources show is the central fact for the kit: the builder does not build
from boxes because it wants to, it builds from boxes what the kit does not offer, and it does it
well. By hand, per project, on that house: a dark backdrop behind every window, architrave trim,
its own roller shutter with slat lines, security grilles, an open casement on a pivot, a double
door with sidelights, curtains, a glass-block wall batched into instanced meshes, a porthole
wall, horizontal cladding lines as hundreds of boxes, fascia boards from `wall`, a storey ledge.
Every one of those is a recurring feature of real houses, rebuilt at the cost of thinking time,
tokens, draw calls, and a look that differs on the next project.

### Bugs found while reading (verified in Node against the vendored three.js)

| where | what | effect |
|---|---|---|
| `house.volume` | `rotation.x = -π/2` mirrors the footprint across the x axis: an L with its notch south-east comes out with the notch north-east (`z 0..4` becomes `z -4..0`) | any asymmetric storey blocked out with `volume` is flipped north-south; no dev project uses it today, so the fix breaks nothing |
| `house.shedRoof` | the high edge is at local **+z**; the code comment and the kit reference say -z | a builder following the reference gets the slope backwards |
| `house.hedge`, `house.planter` | `Math.random()` for the tilt and the sphere sizes | the scene is not deterministic although the prompt requires it; part of the 0.4 % pixel drift between identical renders noted in `presentation-look.md` |
| `house.mat.glass` | transmission 0.35 with opacity 0.55 | three's documentation says opacity stays 1 with transmission; the glass dims twice |
| `kit/runtime.js`, presentation composer | `EffectComposer` reads the renderer's pixel ratio before it looks at the custom target, which is already at drawing-buffer size, so every pass starts at size × ratio | at DPR 2 the occlusion pass runs on four times the pixels until the first window resize |
| `pipeline.py`, modify | after the verifier flags issues, the fix pass is snapshotted with the pre-fix score and never verified | a version carries a score that judged a different version |
| `prompts.py`, builder | "Older screenshots are dropped from your context as you go; only the latest render set stays" | stale since 79e2b35: screenshots stay until a prompt passes `BUILDER_PRUNE_ABOVE_TOKENS`; the sentence makes the builder re-render for nothing |

The rest of the kit reference matches `house.js` line by line (checked 2026-09-12); the tool
descriptions match `tools.py`; the critic and intake prompts match what `critic.py` and
`intake.py` send.

## Rules for every step

- One change per commit. A presentation-path change lands with a look sheet
  (`backend/scripts/look_sheet.py`) on Montelly version 5 and one other real project, looked at
  by a person. A build-path change lands with a measured run against a v0.1.0 run of the same
  project (score, steps, cost, findings read by a person).
- Numbers to record per project, before and after: `house.box` count in `scene/src`, mesh count
  and draw calls of the built scene (`renderer.info`), critic score, builder steps, cost, wall
  time. The grep and the look sheet are the two instruments; step 2 turns them into scripts.
- Existing versions load the live kit with their own copy of the scene sources. A kit change to
  a component's internals is checked against the saved versions on dev, not only the template
  (Montelly hides the kit's glass planes by matching on geometry type).
- Nothing in this plan touches what the critic ignores or judges: looks are a person's call.

## Steps

### Step 0: the baseline picture

Run the look sheet on Montelly version 5 and on `df4cb51b6685`, presentation and ultra, the
four photo views and the aerial. Keep the sheets outside the repo (they are large) but note the
date and the commit in this file. Everything after is compared with these.

### Step 1: correctness, small and verified

All from the table above. Each is a few lines and its own commit.

1. `volume`: rotate by +π/2 and flip the extrusion so the footprint keeps its orientation and
   `y` stays the base. Unit test with an asymmetric polygon (bounding box and corner set).
2. `shedRoof`: make the high edge local -z as documented, or change the doc and the reference;
   the code is the easier one to change. Unit test on the highest vertex.
3. `hedge` and `planter`: the kit's `seeded()` instead of `Math.random()`.
4. Composer size: `composer.setSize(W, H)` after construction on presentation pages, and the
   viewer shows "effects off" when the frame-time guard drops them.
5. Modify: a fixed version gets no score, or a second verification; the verdict is attached to
   the version it inspected.
6. Render service warm-up: an async GET to its health endpoint when a job starts, so the first
   render never pays the 10-16 s cold start.
7. Effort list: check the OpenAI model documentation for what gpt-6-astra accepts; make the
   offered levels per model instead of per provider.

Items 1-3 change what a scene renders like only where the component is used; no dev project
uses `volume` or `shedRoof`, and the random tilt was noise. They ship without a measured run,
with the unit tests. Items 4-7 are outside the build path.

The two prompt corrections (the stale screenshot sentence, the shed roof line of the reference) shipped on
2026-09-12 with the first-build planting change and `RENDER_QUALITY=high`; see `build-path-2026-09-07.md`,
"Changes since", for the baseline run they are measured against.

### Step 2: instruments

Done 2026-09-12: **renderer versions**. `kit/versions/<name>/` snapshots, the build path pinned to one
(`2026-09-12-baseline`, byte-identical to the kit of that day), scene pages served with `?kit=`, a renderer
selector and a side-by-side Compare in the viewer, `look_sheet.py --kit`, the renderer recorded on every
version. Every looks change below is now judged as "dev next to the newest snapshot" on a saved version,
and the freeze is a pin instead of a rule.

Still to do, so the two measurements are repeatable:

- `backend/scripts/look_sheet.py`: accept several projects and produce one sheet per project
  with the same views and looks, so a step's before and after are two files to open side by side.
- `backend/scripts/kit_usage.py`: the grep above as a script over `data/projects`, plus mesh and
  draw-call counts read from the headless page (`renderer.info`), printed per project. Run on
  dev before and after step 4.
- The run summary already gives score, steps, cost and cache misses per job (#13).

### Step 3: looks, on the presentation path only

One commit per item, in this order, each with the two look sheets. Expected return per hour
decreases down the list.

1. **A real HDRI instead of the analytic sky.** Two or three CC0 2k maps (clear, partly cloudy,
   overcast), about 1 MB each, vendored under `kit/assets`. Sky, clouds, horizon context and,
   above all, believable reflections in glass and metal. The directional light is aligned with
   the map's sun; the calibration knobs of `presentation-look.md` are re-measured against
   quality=high the same way.
2. **Something behind the glass.** After `buildScene`, for every object tagged `window` or
   `door` with glass, insert a dark room cell behind the pane and set the pane to low roughness,
   no transmission, opacity 1 and a strong environment reflection. The builder invented the dark
   backdrop itself on Montelly; this makes it the default without asking it to. The reverted
   commit `b7d6cd4` holds a full interior-mapping shader for later; the cell plus reflection is
   most of the effect for thirty lines.
3. **Textured materials with normal maps.** Bring back the twelve ambientCG sets of `7d9414f`
   as a substitution keyed by the material's name (`plaster`, `tile`, `roof`, `wood`, `concrete`,
   `gravel`, `grass`, `asphalt`) and tinted by the builder's colour, with metre-scale UVs. Roofs
   first: they fill the aerial and the elevated views. This needs UVs on the extruded
   geometries (a kit change that moves no pixel on the diagnostic path, checked by the sheet).
4. **Trees and bushes** as ez-tree instances (`22060bc`), keyed by kind, seed, height and spread.
5. **Merged geometry for the viewer.** After `buildScene` on presentation pages, merge static
   meshes per material. A real house is several hundred to a thousand draw calls, which is what
   makes the frame-time guard drop the effects on laptops. Invisible to the eye; measured by
   frame time on a mid-range laptop at 1080p.
6. Later, and only after 1-5 hold up: instanced grass near the house and screen-space
   reflections on glass (`3c325d4`), interactive GPU pages only.

### Step 4: kit v2, one measured run

The components the builder rebuilds by hand, plus the structural gaps for other house types.
Every addition enters the kit reference the builder reads, so the batch ships as one build-path
change, measured on Montelly and on a pitched-roof project against their v0.1.0 runs. The
success criterion is not only the critic score: `house.box` per project should drop from 81 to a
handful, the mesh count should drop, steps and cost should not rise.

Ranked by the Montelly evidence, then by the house stock:

1. `windowUnit` options: a dark backdrop by default, `trim` (architrave), `grille` (straight or
   curved bars), `openLeaf` with an angle, `curtains`, a lamella blind, and a roller shutter with
   a per-window closed fraction that looks the way the builder wanted. `entranceDoor` with
   sidelights, `frenchDoors`.
2. Openings with a shape (`rect`, `arch`, `circle`) on `wall` and `perimeterWalls`; a wall with
   an optional profile polygon in its own plane (gable ends, sloped tops for shed roofs).
3. `cladding: { type: "horizontal", pitch }` on walls, drawn as one merged geometry (and as a
   normal map on the presentation path once step 3.3 exists).
4. Roof edge details on every roof: fascia, gutter, downpipe; a `ledge` for string courses.
5. `hipRoof` and half-hip over a rectangle; dormers (gable and shed); roof windows; `solarArray`
   on a roof plane. A roof over any footprint (straight skeleton) is the one large item and can
   follow separately.
6. Site on a slope: `terrace` (a platform cut into the terrain with its retaining wall), and
   draped variants of `groundPatch`, `pathway`, `fence` and `hedge` following `groundY` the way
   `ribbon` does.
7. `house.instanced(boxes, material)`: the batching helper the builder wrote for the glass
   blocks, in the kit, and used by `railing`, the shutter slats and the cladding lines.
8. `glassBlockWall`, `stairs` with a landing, `pergola`, `carport`, a table set, a trampoline,
   bins, exterior lights: the suggestions the owner ticks and the kit cannot make today.
9. Metre-scale UVs on walls, slabs, roofs and boxes (needed by step 3.3 anyway).

With the batch: the two prompt corrections from step 1, and a material vocabulary in the
reference (render, exposed concrete, brick, timber cladding, standing-seam metal, slate, clay
tile, stone), each name mapping to a texture set on the presentation path so the same word
drives fidelity and looks.

### Step 5: fidelity, one measured run

- **A camera per photograph.** The builder estimates the viewpoint of each labelled photo once
  (eye height, distance, azimuth, fov: the overrides `render_views` already takes), the app
  persists it with the photo, and both the builder's `-photo` views and the critic's pairs use it.
  Today the critic pairs a photo with a fixed 1.6 m, 50° guess.
- **A compare tool.** Given a photo and a view, return the two side by side at the same size, or
  blended, from that camera. Sill heights, roof visibility and proportions jump out of an
  overlay in a way two separate images never show.
- The critic keeps ignoring looks. Its one addition: the persisted camera in the render label.

### Step 6: still renders

Real-time rasterisation will not bounce light. For the share page, a "render this view as a
photo" button that produces a converged image on the GPU service: either three-gpu-pathtracer
on the same page (check its handling of instanced meshes against the kit's leaves and pebbles),
or a glTF export of the built scene rendered by Blender Cycles on the service (glTF carries
instances through `EXT_mesh_gpu_instancing`, which Blender reads). Decide by a prototype of
each on Montelly version 5. Only after step 3 holds up: a path tracer of flat colours is a
sharper study model.

### Step 7: speed and cost, ongoing

Done on 2026-09-12: the GPU render service (rendering 5 % of a build instead of 50 %),
`prompt_cache_key` per job, pruning gated on prompt size. To confirm on the next runs: cache
misses per job at 0 or 1, cost near $2.60 for a Montelly-sized build. Then: the warm-up ping
(step 1.6), and one experiment with `BUILDER_EFFORT=high` against `xhigh` on the same project
through the run settings sheet, judged by score, pictures and wall time. Thinking is 8 of the
11 minutes of a build and is the only lever left on time.

## Not in this plan

- Generated 3D assets (TRELLIS, Hunyuan3D and the like): no dimensional accuracy, megabytes per
  asset, and the house must stay parametric and editable. Their use would be a tree or a car,
  which ez-tree and two CC0 props cover.
- Neighbouring buildings and interiors: the prompt says the house and its plot, and that stays.
- A second LLM judging realism as a gate.

## Status, 2026-09-13

Done on 2026-09-12 (all on `main`, deployed to dev, build path still pinned to `2026-09-12-baseline`):

- **Speed and cost (step 7)**: the GPU render service on Modal (`deploy/modal_render.py`): a render call went from
  ~47 s to 5-8 s, rendering from 50 % of a build to 5 %. `prompt_cache_key` per job (#25). Pruning gated on prompt
  size (#26). Measured: the same house 81 min / ~$33 on v0.0.4 → 14.4 min / $6.45 (Dev3) with an equal critic score.
- **Instruments (step 2)**: renderer versions (`kit/versions/`, `?kit=`, viewer selector and Compare,
  `look_sheet.py --kit` and `--scene-url` through the GPU service), `window.__house.gl` and `.stats`, kit and scene
  files served with `Cache-Control: no-cache` and the dev kit URLs versioned by modification time.
- **Build path, one measured run** (`build-path-2026-09-07.md`, "Changes since"): the first build furnishes the fixed
  planting, in-loop renders at high, two prompt corrections. Dev4 vs Dev3: the garden is there (the picture the owner
  wanted), 76/77 vs 82 on a critic measured to be ±4 at the same effort, $11.75 with the fix pass.
- **Evaluation finding**: the same critic on the same version gave 82 and 78; high 81, xhigh 76, all with the same
  seven findings. Effort does not buy accuracy; the score near 80 is noise. `CRITIC_FIRST_FIX` (on) makes the fix
  pass unconditional; a photo-critic fix pass gained one point on every run measured (78→79, 76→77), so the setting
  buys addressed findings, not score, and may be turned off.
- **UI**: one form after a version (review findings, ticked additions, answers, note → one request, one job).
- **Modify (2026-09-13)**: the verifier's findings are a review on the version ("Apply the review's findings"), no
  automatic fix pass: it ran a full builder pass on findings nobody had read and saved its version under the score
  of the one inspected (step 1.5, closed by removing the pass).
- **Looks (step 3.4, trees)**: `leafTree`, `leafBush`, `hedge` rebuilt without a library (recursive limbs, leaf
  clusters, leaf-shell bushes and hedges). Snapshot `2026-09-12-v1` is the viewer's default; dev holds the leaf-shell
  bushes (`c79d63c`) for a `v2` once judged.

Measured but not fixed yet:

- The presentation look is washed out: same shade level as quality=high (p10 89/89) with darker lit walls (p50 182 vs
  203), blue-grey sky fill desaturating the greens, a plot square on the meadow. Levels alone do not fix it (they
  darken everything); it needs the light design: less sky fill, warmer stronger sun, darker warmer ground, no haze on
  the plot, a slight contrast curve. Step 3.1-3.2 territory, judged on Dev4 v2 in Compare.
- The two kit orientation bugs (`volume`, `shedRoof`) and the glass transmission/opacity are still in the code (the
  reference was corrected for `shedRoof`). Step 1.

Next, in order:

1. Step 3, looks, on the presentation path with Compare on Dev4 v2: the light design pass above, then HDRI, then glass
   with a dark cell, then textured materials. Snapshot after each one that holds up.
2. Step 1 leftovers as one commit: `volume`, `shedRoof` code, glass, composer size, the unscored fixed version, the
   warm-up ping, the effort list.
3. Step 4, kit v2 (windows options, shaped openings, cladding, roof edges, hip roofs, terrace on a slope, batching
   helper), one measured run; then move the pin to the newest snapshot so the builder and the critic see the new
   plants and materials.
4. Step 5, fidelity: a persisted camera per photo and a compare tool, the real fix for the critic's noise.


Where we stand against the plan

┌───────────────────────────┬────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│           step            │                                                                                 state                                                                                  │
├───────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 7, speed and cost         │ Done. GPU render service on Modal: a render call 47 s to 5 to 8 s. Cache key per job, pruning gated on prompt size. Same house: 81 min and about $33 on the old build, │
│                           │  14.4 min and $6.45 now, equal critic score.                                                                                                                           │
├───────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 2, instruments            │ Done. Renderer versions with Compare in the viewer, the look sheet through the GPU service, GPU and stats getters, cache-proof kit URLs.                               │
├───────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ build path, one measured  │ Done and kept. First build furnishes the fixed planting, in-loop renders at high. Dev4 has the garden the prod picture had; its score sits inside the critic's         │
│ run                       │ measured noise.                                                                                                                                                        │
├───────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ evaluation                │ Measured, not fixed. Same critic, same version: 82 and 78; effort changes labels, not findings. CRITIC_FIRST_FIX is on; it buys addressed findings, not score, and you │
│                           │  can turn it off.                                                                                                                                                      │
├───────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ UI                        │ Done. One form and one job after a version.                                                                                                                            │
├───────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 3.4, trees                │ Done to your reference: recursive trunks and limbs, leaf clusters. 2026-09-12-v1 is the viewer default. Leaf-shell bushes and hedges are in dev, waiting for your      │
│                           │ verdict for a v2.                                                                                                                                                      │
├───────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 3.1 to 3.3, the look      │ Not started. The washed-out final look is measured: same shade level as the fast path, darker lit walls, blue-grey sky fill. Levels do not fix it; the light design    │
│                           │ does.                                                                                                                                                                  │
├───────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 1, correctness            │ Partly. Cache headers and prompt lines done; volume, shedRoof code, glass, composer size, the unscored fixed version, the warm-up ping and the effort list still open. │
├───────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 4, kit v2 and moving the  │ Not started.                                                                                                                                                           │
│ pin                       │                                                                                                                                                                        │
├───────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 5, fidelity, camera per   │ Not started. This is the real fix for the critic's noise.                                                                                                              │
│ photo                     │                                                                                                                                                                        │
└───────────────────────────┴────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

What I would do next, in order

1. The light design pass on the presentation path, judged in Compare on Dev4 version 2: less sky fill, a warmer and stronger sun, a darker warmer ground with no plot square, a touch of contrast. Then the HDRI and the dark cells behind glass. Each one a snapshot when it holds up. This is what turns the viewer's picture from "model" to "photo", and nothing in it touches the builder.
2. Step 1 leftovers as one small commit, since they are all verified and a few lines each.
3. Kit v2 with its measured run, and only then move the pin so the builder and the critic see the new plants and materials.
4. Camera per photo and the compare tool, which is the change that would make the score mean something.
## Status, 2026-09-13 evening: step 3 in progress

Done today, all uncommitted or on `main` as noted:

- **Renderer snapshots** `2026-09-13-v2` (the leaf-shell bushes and hedges, the old presentation look) and
  `2026-09-13-v3` (v2 plus the light design pass below). v3 is the viewer's default; the build path stays pinned to
  the baseline.
- **Light design pass (step 3.1a), in v3 and dev**, presentation pages only, in `kit/runtime.js`: sun 2.2 → 3.0 and
  warmer (`#ffe2b8`), sky fill 0.4 → 0.3, hemisphere 0.1 → 0.08 and near neutral, a grade pass at the end (contrast
  1.08 about mid grey, saturation 1.06, the vignette as before). The land beyond the plot is one disc to the horizon
  with a hole the size of the terrain's footprint (the old disc sliced through pools and anything dug below its
  height), the lawn's own colour at the plot's edge darkening and warming over 60 m into a meadow (shader gradient by
  world distance, no vertex-colour triangle pattern), one grain over all of it. Knobs: `p_contrast`, `p_sat`,
  `p_vignette`, `p_meadow` next to the old ones. **Not yet looked at on a real project**: the first check is Compare
  on Dev4 v2, v3 next to v2, aerial and south-photo: lit walls near the quality=high level (p50 ~200 vs 182 before),
  the shade a little deeper, the greens greener, no square around the plot, the pools back. `presentation-look.md`
  gets the new calibration once it holds.
- **Modify: the verifier proposes, it does not fix.** Its findings go on the version as a review ("Apply the review's
  findings"); the automatic fix pass and its mis-scored version are gone (step 1.5 closed).
- **Plans: `PLAN_MAX_PAGES` 6 → 40**, the cut is recorded (`pages_total`), logged and shown in the plans panel
  ("6 of 21 sheets"). Each sheet is ~1-2.5k tokens per builder call plus its PNG bytes on every call: ~0.3 MB for a CAD
  export, ~3.6 MB for a scan, so 21 scanned sheets (76 MB a call) would exceed both providers' request limits. The fix
  for that, a 1568 px JPEG of each sheet for the model (both providers reduce the image to that anyway) with the PNG
  kept for `inspect_image`, is a build-path change for the next measured batch.
- **Conversation layer**: issue #35 (talk before the job: clarify, confirm, then build). The observed case: a builder
  question answered "no" started a full modification job on an ambiguous answer.

### Step 3.1b, the HDRI sky: the plan

What the owner wants is the photograph's sky: blue with cumulus, a warm sun, the clouds mirrored in the glass, and the
map's own horizon (tree lines, hills) as the free scenery beyond the meadow. Everything below the horizon stays hidden
under the meadow disc, so only the strip above the horizon of a map is ever seen; it has to look like a Swiss suburb.

Measured on 2026-09-13 (Poly Haven, CC0, 2k `.hdr`; a script that parses the map with three's loader and prints the
sun position, the sky's luminance percentiles and the horizon colour opposite the sun, plus tone-mapped previews of
the whole map and the horizon strip: `kit/scripts/sky_stats.mjs` and `kit/scripts/sky_preview.mjs`, run from `kit/`):

| map | size | sun elevation | sky median | sky | horizon strip |
|---|---|---|---|---|---|
| `kloofendal_48d_partly_cloudy_puresky` | 5.5 MB | 48° | 0.35 | the photo's cumulus sky | none (sky only) |
| `kloofendal_43d_clear_puresky` | 4.6 MB | 43° | 0.22 | deep clear blue | none |
| `kloppenheim_06_puresky` | 4.4 MB | 7° | 0.53 | overcast, sun on the horizon | none |
| `kloofendal_48d_partly_cloudy` | 6.5 MB | 48° | 0.34 | the same cumulus sky | a rocky South African hill and a town: wrong |
| `je_gray_park` | 6.1 MB | high | 0.07 | clear, a few clouds | a flat park with a tree line all round: right |
| `noon_grass` | 6.4 MB | noon | 0.30 | light clear | a green park, mature trees on the horizon: right |
| `meadow_2` | 6.3 MB | high | 0.17 | clear | a meadow with bushes and trees close by: usable |
| `sunny_vondelpark`, `lakeside` | | | | | under trees / a lake: unusable |

No single map has both the cumulus sky and a fitting horizon. The plan, one commit each, judged in Compare against v3
and on the look sheet (p10/p50/p90 against quality=high, as in `presentation-look.md`):

1. **Loading and alignment.** `?sky=<name>` on presentation pages (default the partly cloudy sky, `sky=analytic`
   keeps today's Sky shader for comparison). The map is loaded once with three's `HDRLoader` (`RGBELoader` is
   deprecated in r181), equirectangular. The sun is found in the map (centroid of the pixels above half the maximum):
   its elevation replaces `sun_el`, and `scene.backgroundRotation` / `environmentRotation` turn the map so the sun
   lands at the runtime's azimuth (200°, or `sun_az`); the directional light is aligned with it. The fog colour is
   read from the map just above the horizon opposite the sun.
2. **Light and background from the same map, the sun removed from the light.** The background is the full map. The
   environment is a PMREM of a copy with its luminance clamped (about 8× the sky's median) so the sun's disc does not
   light the scene a second time on top of the directional light and wash out the shadows; the analytic path did the
   same by damping the forward scattering. One scale normalises the map (median sky radiance → 0.9 linear) before the
   existing `p_env` / `p_bg` knobs, so the calibration numbers keep their meaning across maps.
3. **Assets.** `kit/assets/sky/<map>.hdr`, served by the existing `/kit` mount (`/kit/assets/sky/...`, one copy for
   every renderer snapshot since the files are named by content), added to the Dockerfile (`COPY kit/assets`) and to
   the export zip. Budget: two maps at 2k, about 12 MB; 1k is too soft for the clouds on a 1080p screen.
4. **The horizon.** Start with two maps: the partly cloudy puresky (the wanted sky, no scenery: the meadow and the
   scene's own trees close the view) and `je_gray_park` or `noon_grass` (the tree line, a clearer sky), and judge
   both on Dev4. If the owner wants the cumulus sky with the tree line, composite once, offline: the upper hemisphere
   of the partly cloudy map over the horizon band of the park map, exposures matched at the horizon, saved as one
   `.hdr` under `kit/assets/sky/` with the script that made it in `backend/scripts/`.
5. **Calibration and snapshot.** Look sheet on Dev4 v2 and one other project, the four photo views and the aerial,
   `quality=high` next to `look=presentation` with each map; re-measure `p_env` / `p_bg` the way `presentation-look.md`
   did (a sunlit white wall level with quality=high, the shade a little under). Snapshot as `2026-09-1x-v4` when it
   holds, update `presentation-look.md`.

Then, in the plan's order: something behind the glass (3.2), textured materials (3.3, roofs and lawn first: the
mowing stripes and the grain are most of what separates the render from the photograph once the sky is right),
merged geometry for the frame rate (3.5). The scenery beyond the plot stays the map's horizon; a tree line of
billboards at the plot's edge is the one cheap addition worth trying after 3.2, real relief beyond the plot is not
planned (an Unreal-style landscape is neither the owner's plot nor a laptop's job).
