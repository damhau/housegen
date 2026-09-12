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

Two build-path corrections wait for the next measured run (step 4) and ride with it: the stale
screenshot sentence in the builder prompt, and the shed roof line of the kit reference.

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
