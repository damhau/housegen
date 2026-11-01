# Archviz stills in Cycles (#40 revisited), 2026-10-30

Question: can one furnished room of a housegen scene reach the quality of a professional archviz render
(the agency renders in `reference/`, kept local: third-party images) with Blender Cycles on the local RTX 3050,
no AI repaint, only CC0 assets and our own?

Project: TestVillaGille on dev (`fa8bd00053e6`, version 21), the ground floor's open kitchen / dining / living.
The still: `final.jpg` (r5, 2560x1440); round by round: `progress.jpg`. Side by side with the reference
(kept local): `sheets/final.jpg`. Scores: [Blind judgments](#blind-judgments).

## Pipeline, step by step

Everything runs from this folder. Blender is `bpy` 5.0.1 from PyPI (`uv run --no-project --python 3.11 --with bpy`),
Cycles on CUDA (OptiX does not initialise under WSL), OIDN denoiser.

1. **Export the house** from its scene page on dev (headless Chrome 145 on the GPU, `GLTFExporter`), 51 s, 260 MB,
   with its rooms (`export.py`: the bake spike's exporter with the v34 kit and the async `report()`):

       cd ../../backend && WALK_CHROME=<chrome 145> uv run python ../spikes/archviz/export.py fa8bd00053e6 ../spikes/archviz/work

2. **Import** into a .blend (8 s) and look at the storey from above to place the camera:

       uv run --no-project --python 3.11 --with bpy python plan.py work/house.glb work/plan-rdc.png 0

3. **Assets**: `ph_get.py <id>` (Poly Haven models, glTF 2k) and `ph_get.py --tex <id>` (texture sets) into
   `assets/` (git-ignored); the HDRI `meadow_8k.exr` (Poly Haven, CC0). Ours, made by script in `made/`:
   - `made/eucalyptus.py` -> `eucalyptus.glb`: branches with round leaves for the table vase;
   - `made/art.py` -> `art-print.jpg`, `art.glb`: a generated abstract print in an oak frame with a mount;
   - `made/throw.py` -> `throw.glb`: a wool throw dropped over the lounge chair by cloth simulation.
4. **Render**: `still.py` opens the .blend, applies the steps, renders, and saves the grades asked for:

       PLANT_AT=-1.72,0.01,1.25 PLANT_H=1.5 VIEW_GAIN=2.2 SKY=0.6 SUN_POWER=5 HDRI_TURN=257 SUN_AZ=215 SUN_ELEV=24 \
       GRADES="6400:High Contrast:0.7" \
       uv run --no-project --python 3.11 --with bpy --with numpy python still.py work/house.blend work/r5.png \
         "0.6,1.25,2.3>-4.5,1.0,4.15@54" 2560 1440 400 \
         --step lamps_off outside glass sun photo_ground finishes linen decor
       uv run --no-project --with pillow --with numpy python post.py work/r5-6400-HighContrast-0.7.png sheets/ours-final.png

   The camera is `x,y,z>tx,ty,tz@vfov` in scene metres, level, the target's height reached with the lens shift
   (vertical lines stay vertical).

### The steps (`still.py`, `dress.py`)

| step | what it does |
|---|---|
| base | Cycles GPU, adaptive sampling, OIDN, AgX; the kit's room ceilings hidden (2 cm inside the slab: black seams) |
| `lamps_off` | daytime: every lamp off |
| `outside` | the view: a CC0 HDRI of a sunlit wood for camera and reflection rays (Light Path: camera or glossy), the physical sky for the light the room receives; the hedges' bare cores go (their leaves are instanced, not exported) |
| `glass` | window, door and railing panes: thin clear glass (Transparent + a Layer Weight reflection, transparent to shadow rays); the export's panes are 55 % opaque blue-grey, and a Fresnel node on a thin pane goes totally reflective from inside and stops the sun |
| `sun` | a sun lamp (0.53 deg) instead of the sky's disc: sharp patches, no noise; `SKY` scales the sky's fill |
| `photo_ground` | the export's terrain (no shader: flat pale green) and the garden's low pieces beyond 3 m go; the HDRI's own grass shows |
| `finishes` | the oak floor as a 1.7 m photo-scanned plank set, scaled to the old texture's mean colour; a plaster relief and matte roughness on walls, partitions and ceilings; the terrace tiles at a stone grey |
| `linen` | the grey curtains as translucent off-white linen |
| `decor` | rug (CC0 boucle scan, our border band), pachira, eucalyptus in the table vase and in a floor vase, a board, bowl and fruit on the island, the framed print, a linen runner, the lounge chair with the throw and a cushion, the dining chairs' shells in a fine wool |
| `edges` | (tried last, not in the final) a 4 mm Bevel node in every material's normal: invisible at the judged size |
| `post.py` | the global finish: a soft bloom around the windows, an unsharp mask (radius 1.6 px, 60 %) |

## Timings (RTX 3050 6 GB, CUDA under WSL)

| | |
|---|---|
| export from dev | 51 s (260 MB GLB) |
| import to .blend | ~10 s |
| 640x360, 24-32 samples (framing, sun, HDRI turn) | 4-6 s |
| 960x540, 64-96 samples (drafts) | 12-16 s |
| 1920x1080, 256 samples | 135-157 s |
| 1920x1080, 400 samples | 274 s |
| **2560x1440, 400 samples, final** | **425 s** |
| cloth simulation of the throw (90 frames) | ~10 s |

## Blind judgments

Each round: three fresh subagents, each shown only two images (ours scaled to the reference's 967x543, both
re-saved as JPEG q92) as A and B in random order, no other context; scores 1-10 (8 = "an agency would publish
it as is"). `judge_prep.py` makes the pairs, `work/judge/r<n>/key.json` says which was ours.

| round | still | judge | Daylight | Materials | Decor | View | Grade | Overall | ref Overall |
|---|---|---|---|---|---|---|---|---|---|
| r1 | base1 (export + HDRI view + clear glass + sun, no dressing) | j1 | 6 | 4 | 3 | 6 | 6 | 4 | 5 |
| r1 | | j2 | 5 | 4 | 3 | 6 | 6 | 4 | 6 |
| r1 | | j3 | 6 | 5 | 3 | 6 | 6 | 4 | 5 |
| r2 | r2 (view D, finishes, linen, rug, plants, eucalyptus, island) | j1 | 7 | 6 | 6 | 7 | 7 | 7 | 5 |
| r2 | | j2 | 6 | 6 | 6 | 7 | 6 | 6 | 4 |
| r2 | | j3 | 7 | 6 | 6 | 7 | 7 | 7 | 5 |
| r3 | r3 (+ grade 6400 K High Contrast, sky 0.6 / sun 5, print, rug border, runner, lounge chair + cloth throw, terrace) | j1 | 7 | 7 | 7 | 7 | 7 | 7 | 4 |
| r3 | | j2 | 8 | 7 | 7 | 7 | 8 | 7 | 5 |
| r3 | | j3 | 7 | 6 | 6 | 7 | 7 | 6 | 4 |
| r4 | r4 (+ view x1.5, 400 samples, chair/plant moved, floor vase, relief 0.25, post: bloom + unsharp mask) | j1 | 7 | 7 | 7 | 7 | 7 | 7 | 4 |
| r4 | | j2 | 7 | 6 | 7 | 7 | 7 | 7 | 5 |
| r4 | | j3 | 7 | 7 | 7 | 7 | 7 | 7 | 5 |
| r5 | r5 (+ satin 4k floor, wool-upholstered chairs, cushion, view x2.2, tighter frame, 2560x1440) | j1 | 7 | 6 | 7 | 7 | 7 | 7 | 4 |
| r5 | | j2 | 7 | 7 | 7 | 8 | 7 | 7 | 5 |
| r5 | | j3 | 7 | 7 | 7 | 8 | 7 | 7 | 4 |
| r6 | r6 (r5 with sky 0.35, sun 6 at 232/19, exposure 1.35, right plant removed: a white strip appears at the edge) | j1 | 7 | 6 | 7 | 7 | 7 | 6 | 5 |
| r6 | | j2 | 6 | 6 | 7 | 7 | 7 | 6 | 5 |
| r6 | | j3 | 6 | 5 | 6 | 6 | 6 | 6 | 4 |

**The goal's bar was not met.** It asked for at least 8 on every line from every judge; the best still (r5)
scores 7 on almost every line (View 8 for two judges, Materials 6 for one). Its overall (7/7/7) is above the
reference's (4/5/4): the judges mark the reference down for its dark, veiled grade (Grade 2-4 in every round)
while scoring its materials and decor 6-8. Scores move by about one point between judges on the same image.

Image statistics (`stats.py`; luminance 0-255, the wall's CIELAB a*/b*, 0/0 = neutral):

| | mean | p10 | p50 | p90 | wall L* | a* | b* |
|---|---|---|---|---|---|---|---|
| ours (r5, final) | 127 | 37 | 140 | 195 | 51 | -1.2 | +4.8 |
| reference | 105 | 71 | 107 | 135 | 51 | +0.3 | -0.1 |

## What made the biggest difference

1. **The styling and the camera (r1 -> r2: overall 4 -> 6-7, decor 3 -> 6).** A view with depth and no blank
   wall, then the dressing: linen curtains, a rug, plants, eucalyptus, fruit, photo-scanned finishes.
2. **Light that gets in at all.** Two bugs hid the sun: the export's window panes (55 % opaque blue-grey), and a
   Fresnel-mixed thin pane, which goes totally reflective seen from inside at the sun's angle (total internal
   reflection) and blocks shadow rays. Clear glass (Transparent + Layer Weight, transparent to shadow rays) and a
   sun lamp gave the sun patches and chair shadows.
3. **The view outside.** A CC0 HDRI of a sunlit wood seen only by camera and reflection rays (the room keeps the
   physical sky's light), the export's flat terrain and bare hedge cores hidden. The judges rated it 7-8 from r2.
4. **A neutral grade.** Blender's white balance at 6000-6400 K (higher values warm it up: the setting is the
   light that counts as white), AgX High Contrast, less sky fill: walls from b* +10.9 to +4.8.

From r3 on, targeted fixes (a print, a chair with a cloth-simulated throw, bloom and sharpening, a 4k satin
floor, wool chairs, 2560 px) moved single lines by about a point but not the overall: a plateau at 7.

## What still looks worse than the reference

What the judges kept naming, round after round:

- **"Plain, flat walls and ceiling."** That is the house's own white plaster; the reference has a board-formed
  concrete ceiling, a stone backsplash and wood. The goal kept finishes in their kind and colour, so no feature
  wall or concrete ceiling was added.
- **"Plastic-looking identical grey chairs", a plain table.** The kit's furniture is simple geometry: clean
  edges, no wear, one material. Better models would help most here, and CC0 libraries have few modern
  dining pieces (Poly Haven: a tufted leather chair, classic consoles).
- **A slightly soft, clean "real-time CG" surface**, weak contact shadows. Real geometric detail (bevels in the
  mesh, seams, stitching) is missing. A shading bevel (`edges`) and sharpening barely show at 967 px.
- **The sun/shade contrast.** A lower sun and less fill (r6) gave stronger patches, but the judges did not
  score it higher (and the plant's removal opened a white strip at the right edge).
- **Small staging flaws**: plants cropped at the frame edges, the cloth throw a little stiff where it hangs.

Fidelity notes:
- **The view outside is a stock photo of a wood, not the site.** For a real listing it should be the project's
  own surroundings (the context photo, or a photo taken on the plot).
- The furniture, decor and textiles are staging, not the owner's design. The architecture, openings, floor and
  wall colours are the house's.

## Licences

Poly Haven assets are CC0 (models, textures, the HDRI `meadow` by Sergej Majboroda); none is marked NoAI and none
was bought. Phase 2 added the CC0 texture sets `concrete_wall_008`, `curly_teddy_natural`, `wood_floor_deck` and
`oak_veneer_01`. Phase 3 added the CC0 models `potted_plant_04` (the bedside succulent), `ceramic_vase_01` (the
floor and sill vases), `outdoor_table_chair_set_01` (the balcony and terrace set), `modern_wooden_cabinet` (the
sideboard) and `ceramic_vase_04` (the jug on it); `potted_plant_02` was already used for the kit's small plants, and
`wooden_bowl_01`, `lemon` and `food_apple_01` for the kitchen island. `made/` is ours, the phase 3 props included
(books, carafe, candles, napkins, the stool, the bath mat, the towel rail, the second print `art2`). The reference
renders are third-party marketing images, kept in `reference/`
(git-ignored), as are `assets/`, `work/` and `sheets/` (the sheet contains the reference).

## Second room: the bedroom (Chambre 4, first floor), 2026-10-31

The same pipeline in the room that opens onto the south balcony, judged blind against
`reference/bedroom-balcony.jpg`. The still: `bedroom.jpg`; round by round: `bedroom-progress.jpg`.

    CUSHION_RGB=0.16,0.19,0.13 PILLOW_TURN=90 LAMPS_ON="opal mushroom table lamp" FLOOR_AT=2.0,2.75,3.0 \
    VIEW_GAIN=2.2 SKY=0.5 SUN_POWER=6 HDRI_TURN=257 SUN_AZ=165 SUN_ELEV=22 GRADES="6800:High Contrast:0.8" \
    uv run --no-project --python 3.11 --with bpy --with numpy python still.py work/house.blend work/bedr4.png \
      "3.5,4.0,1.95>0.6,3.6,4.3@55" 2560 1440 400 \
      --step lamps_off outside glass sun photo_ground finishes linen bedroom

What the `bedroom` step adds (dress.py) and what was made for it:
- `made/bed.py` -> `bed.glb`: an upholstered platform bed (linen base on a recessed plinth, padded headboard,
  a 160 x 200 mattress). The kit's bed has its bedding baked into one mesh with one material: it cannot be
  re-dressed, so it is hidden.
- `made/bedding.py` -> `bedding.glb`: cloth simulation on that mattress: a linen duvet pinned under the pillows,
  shrinking a little as it settles (creases), its top turned back, a wool throw across the foot.
  Pressure-inflated pillows were tried and collapse (a soft bag does not stand against a headboard): the pillows
  are Poly Haven's CC0 cushion pair, pillow-sized, in the duvet's linen, a sage velvet pair in front.
- the balcony slab gets a timber deck material (the glTF wall material it shared went sky-blue-white in full sun);
  the bedside lamp is on (`LAMPS_ON`), a plant by the glazing.
- the sun: a morning sun from the south-east (165 deg) is the one that reaches the bed (west side of the room);
  from the south-west the patches fall on floor the camera does not see, and the judges flagged sun on the deck
  without sun inside.

| round | still | judge | Daylight | Materials | Decor | View | Grade | Overall | ref Overall |
|---|---|---|---|---|---|---|---|---|---|
| round | still | judge | Daylight | Materials | Decor | View | Grade | Overall | ref Overall |
|---|---|---|---|---|---|---|---|---|---|
| b1 | kit bed under a cloth-sim duvet on a proxy box, throw, cushions | j1 | 5 | 4 | 4 | 6 | 5 | 4 | 8 |
| b1 | | j2 | 5 | 4 | 3 | 6 | 5 | 4 | 8 |
| b1 | | j3 | 5 | 5 | 4 | 6 | 5 | 4 | 8 |
| b2 | made bed (upholstered, padded headboard, box pillows), duvet + fold + throw on its mattress, plant, lamp on, timber deck | j1 | 6 | 5 | 5 | 7 | 6 | 5 | 8 |
| b2 | | j2 | 5 | 4 | 4 | 6 | 5 | 4 | 8 |
| b2 | | j3 | 5 | 4 | 5 | 6 | 6 | 4 | 8 |
| b3 | + CC0 cushions as pillows (linen), cloth-sim creased duvet, fold, throw on our bed | j1 | 6 | 6 | 6 | 7 | 6 | 6 | 8 |
| b3 | | j2 | 6 | 5 | 5 | 7 | 6 | 5 | 8 |
| b3 | | j3 | 6 | 5 | 5 | 7 | 6 | 5 | 8 |
| b4 | + sage velvet cushions, morning sun 165/22 across the bed, sky 0.5 / sun 6 | j1 | 6 | 6 | 5 | 6 | 6 | 5 | 7 |
| b4 | | j2 | 5 | 5 | 6 | 6 | 5 | 5 | 7 |
| b4 | | j3 | 6 | 6 | 5 | 7 | 6 | 6 | 8 |

**Result: 5-6 against a reference the judges rate 7-8.** The dining room's reference was scored 4-5 (its dark
grade); this one is a well-lit, densely styled studio render, and the gap is real. From a box-like bed (4) the
bedding work brought the room to 5-6; the judges' remaining points are the same as in the dining room, heavier
here because a bedroom is mostly textiles:
- bedding that still reads synthetic (simulated folds are softer and more uniform than slept-in linen);
- sparse styling and plain walls, base and ceiling (the reference has a desk, a mirror, art, textured upholstery);
- the lamp's glow halo (the post bloom) and weak contact shadows.

**Lesson:** for textile-heavy rooms, the bottleneck is soft-goods assets. Cloth simulation from scratch reaches
"plausible" in a few iterations, not "studio"; a library of good bedding, cushions and throws (CC0 or licensed for
AI use) would move this room more than any renderer setting.


## Third part: any room, no per-room settings (`photo.py`), 2026-10-31

The question: does the recipe generalise to any room of any housegen house? `photo.py` takes only the export
(`work/house.blend`, `work/rooms.json`) and a camera (as the owner would frame it in the viewer). It decides
everything the two hand-tuned rooms set with environment variables:

    uv run --no-project --python 3.11 --with bpy --with numpy --with pillow python photo.py \
        work/house.blend work/rooms.json "0.6,1.25,2.3>-4.5,1.0,4.15@54" work/auto/dining.png 2560 1440 400
    # the same on Modal GPUs (a temporary app, one container per photo, in parallel; nothing stays deployed):
    uvx modal run modal_photo.py [--rooms dining,bedroom,bath]

- **room and storey**: the room under the camera (rooms.json); the dressing covers that storey.
- **finishes**: wood-coloured floors become the plank scan in their own colour, laid along the room's long axis;
  plaster relief, linen curtains; the light exterior paving seen through the glass becomes stone (ground floor) or
  a timber deck (upper floors).
- **dressing from the kit's own pieces** (their `name` and `footprint`):
  - swaps: rugs become bouclé, plants become CC0 scans, art becomes our print, armchairs become the lounge chair
    with a throw, beds become our bed with cloth-simulated bedding (`made/bedset.py`, cached per size);
  - additions: cushions on sofas; a runner, a rug and eucalyptus at dining tables; a bowl and a board on the
    counter's longest free stretch; a print on the wall the camera sees most, if none is in view;
  - bathrooms (`made/bathset.py`, all ours): an oak caddy with a candle across the tub; a towel draped over its
    front rim (cloth simulation, made per rim width); pump bottles on the basin's free deck; the kit's
    accessories and towel stacks become ours. Rims and decks are found by rays down the piece; a flat bowl
    bottom is rejected because it has walls on most sides.
- **sun**: a real position for the latitude (`LAT`). The pick is the one that puts about 20 % of the open floor
  the camera sees in sun (open: nothing above it within 1.5 m, so not under a table). North rooms get no sun and
  their lights are switched on instead (warm, the bulb hidden from the camera).
- **window view**: the HDRI is turned from the camera's yaw, so the window always shows its wood.
- **grade**: from a 768 px preview, exposure to a room median of 155, then white balance and tint to wall
  b* 4 and a* 0.5 (the north bathroom came out green-grey before the tint).

Same cameras as the hand-tuned rooms, plus a bathroom (SDB, ground floor, north window). Blind, 3 judges each,
same protocol and references as above (the bathroom is paired with the bedroom reference as an anchor). Overall
scores:

| room | auto a1 | auto a2 | hand-tuned | reference |
|---|---|---|---|---|
| dining | 6 / 6 / 6 | 6 / 6 / 7 | 7 / 7 / 7 (r5) | 4-7 |
| bedroom | 5 / 5 / 5 | 5 / 5 / 5 | 5 / 5 / 6 (b4) | 8 |
| bathroom | 4 / 4 / 4 (decor 3) | 5 / 5 / 5 (decor 4-5) | - | 8 |

a2 added the bathroom set, the tint, lamps in rooms without sun, the open-floor sun pick and a bathroom camera
aimed away from the door leaf. Side by side, a1 on the left and a2 on the right: `auto.jpg`.

**Result:** with no per-room settings, the generic version lands within a point of the hand-tuned rooms (dining
6-7 against 7, bedroom 5 against 5-6). The bathroom went from 4 to 5 with one made asset set. The remaining
complaints are the same in every room, and none of them is about generalising:
- flat light: no visible sun inside;
- plain walls and tiles, CG-clean sanitaryware;
- sparse styling.

Timings, Modal T4 (OptiX works there), per photo:
- setup 90-115 s: open the file, dress, rays, sun pick;
- preview and grade 10 s;
- final 2560x1440 at 400 samples: 180-260 s;
- total 5-6 min, the three in parallel in 8.5-14.5 min including the image build and the upload of
  about 480 MB of assets.

Locally on the RTX 3050, a preview takes about 1 min.

Known gaps:
- the sun pick favours patches the shadow rays can reach, not ones that read well in the picture (the bedroom's
  sun lands on the deck outside);
- the HDRI's car still shows in the dining room's right-hand window;
- there are no rules yet for showers, WCs, kitchens without an island, desks, or children's rooms.

## Milestone: listing stills at 8/10 (#88)

The plan, the protocol and the stop rules are on #88; each phase has its own issue (#81-#87).
- **Judging:** `judge_prep.py` makes the blind pairs, `judge_prompt.md` is the prompt, `judge_save.py` and
  `judge_tab.py` unblind and tabulate the replies.
- **Cameras:** in `modal_photo.py`. The living room is the holdout: it is scored at the milestone checks only and
  never tuned on.
- **Contact sheets:** one per scored round, in `rounds/`.
- **Spend:** `work/ledger.csv`.

The table gives the medians of ours, ours against the reference, with columns D = Daylight, M = Materials,
De = Decor, V = View, G = Grade, O = Overall.

| round | what changed | judges | dining D/M/De/V/G/O | bedroom | bathroom | living (holdout) |
|---|---|---|---|---|---|---|
| a2 | the starting point | 3 | 6/6/5/7/7/**6** | 5/5/5/6/5/**5** | 5/5/5/6/6/**5** | - |
| b0 | cars painted out of the HDRI (`hdri_clean.py`), the near-edge sliver turn | 5 | 6/6/6/7/7/**6** | 5/5/5/6/6/**5** | 6/6/6/6/6/**5** | 6/6/5/5/6/**5** |
| g1 | b0 through a tone curve toward the references' percentiles (`post.py` strength 0.5): no gain, left off | 3 | 6/6/5/7/6/**6** | 5/5/5/6/6/**5** | 5/5/5/6/6/**5** | - |
| r1 | the sun picked from thumbnails (patches on the floor and walls), sun 16 instead of 5.5 | 3 | 7/6/5/7/7/**6** | 6/5/5/6/6/**5** | 5/5/5/6/5/**5** | - |
| r2 | rooms without sun: lamps at 40 % and soft (the window leads) | 3 | (= r1) | (= r1) | 6/5/6/6/6/**5** | - |
| r3 | phase 2: the façade's render off the room side of the outside walls (`inner_walls`), relief on the ceilings (`paint`), glazed tiles one by one, glazed sanitaryware, a limewash wall behind the bed | 3 | 7/6/6/7/7/**6** (r3b) | - | 6/5/6/6/6/**5** | - |
| r3b | r3 with the limewash up to the ceiling and the balcony's decking back (per-face paving) | 3 | (above) | - | - | - |
| r4 | stronger glaze on the white tiles | 3 | - | 6/6/5/7/6/**5** | (= r3 by eye, not judged) | - |
| r5 | `CEILING=concrete` (exposed concrete, CC0 scan), the grade's highlight cap (`CLIP_MAX`), a sage tile wall behind the bathtub | 3 | 7/6/6/7/7/**6** | 6/6/5/6/6/**5** | 6/6/6/6/6/**5** | - |
| r6 | r5 + bouclé on the bed and the dining chairs (CC0 scan), a weathered deck (CC0 scan) | 3 | 7/6/6/7/7/**7** | 7/6/6/7/6/**6** | - | - |
| r7 | r6 + the floor laid plank by plank (2.1 x 0.19 m, a CC0 oak scan, tone and gloss per plank, V-joints); wet rooms keep a painted ceiling | 3 | 7/6/6/7/7/**7** | 7/6/5/6/7/**6** | 5/6/6/6/6/**5** | - |
| s1 | phase 3: `style.py` — a rug under the bed, the bedside (lamp to the back, books, a succulent), napkins, candles and a carafe on the dining table, a floor vase of branches in a free corner the camera sees | 3 | 7/6/6/7/7/**6** | 7/6/6/7/6/**6** | 5/5/5/6/6/**5** (no change in the bath) | - |
| s2 | s1 + a bistro set on the balcony or terrace the camera sees through the glass (CC0), a bud vase with a sprig on deep window sills in view | 3 | 7/7/7/7/7/**7** | 6/6/6/6/6/**6** | 5/6/6/6/6/**5** | - |
| s3 | s2 + a pair of prints over the bed (`made/art2.glb`, seed 8), the dining print hung 1.3x when its wall takes it | 3 | 7/6/7/7/7/**7** | 7/6/7/7/6/**6** | 6/6/6/6/6/**5** | - |
| s4 | s3 + a walnut sideboard (CC0) styled under the dining room's ribbon window, the dark foreground plant and the cut-off bath bottle taken out | 3 | 7/6/6/7/7/**7** | 7/6/6/7/7/**6** | 6/6/6/6/6/**6** | - |
| s5 | s4 + clear wine glasses, a flax runner, sage napkins with little sheen; the sideboard's top a potted plant, a jug and books; foreground plants cut by the frame taken out before the rules | 3 | 6/6/6/7/6/**6** | 7/6/7/7/7/**6** | 5/6/6/6/6/**5** | - |
| s6 | s5 + a towel on a black bar on a plain wall the bathroom's camera faces (`made/bathset.py rail`, cloth-simulated); the bath's wall rules keyed on the room the camera sees most of, floor or not; the dining and bedroom images are s5's, judged again | 3 | 7/6/7/7/6/**6** | 6/6/6/7/6/**6** | 5/6/6/6/6/**5** | - |

What the b0 judges named as the weakest point:
- **Every room:** plain surfaces and sparse styling.
- **Bedroom and bathroom:** flat light with no sun.
- **Bathroom:** the flush plate cut off at the left edge, after the edge turn (since then limited to slivers
  under 3 %).
- **Living room:** windows blown to white, a large bare wall, garish cushions.

Phase 1 (#82, light and grade), r1 and r2:
- **The sun pick:** each candidate sun is rendered as a thumbnail twice, with and without the sky, and the pick keeps
  the one whose patches fall on the floor and the walls.
  - The ray count it replaced said "20 % of the room in sun" where the render showed 1 %.
- **The sun's strength:** 16 instead of 5.5, so a patch is 4-8x the sky's light around it instead of 2-3x.
- **`regrade.py`:** regrades a photo from the linear EXR that photo.py now writes, in seconds and without a render.
- **What did not help:** a global tone curve (g1); a brighter or flatter AgX look (judged by eye only).

Phase 2 (#83, surfaces), r3 to r7: **closed as saturated.** The target was a Materials median of 7 in the
three rooms. It ended at 6 / 6 / 6. Over the 3 rooms, the Materials mean rose from 5.3 at the end of phase 1 to 6.1.
The dining room's Materials median stayed at 6 for four scored rounds (r3b, r5, r6, r7). That tripped the old stop
rule, which was then revised on #88: a flat phase is now closed and the work moves on. Sheet: `rounds/r7.jpg` (end of
phase 1 above, end of phase 2 below).
- **What moved:** Materials +1 in the bedroom (r4: the façade's render off its walls) and in the bathroom (r5);
  Overall +1 in the dining room and the bedroom at r6 (bouclé, deck). Single judges gave Materials 7 or 8; never
  two out of three.
- **What did not move Materials:** the concrete ceilings, the sage tile wall, the plank floor (one r7 judge called
  its planks "repetitive"), stronger tile glaze.
- **Where the judges' Materials complaints went in r7:** almost none name a surface any more. They name sparse
  styling, "plain walls, one print, one vase", the crumpled and "synthetic" bedding, the blown-out terrace and
  globe lamps, "flat CG white" sanitaryware with weak contact shadows. Those belong to #84 (styling), #85 (soft
  goods, furniture) and #86 (framing): a judge seems to score Materials on the whole picture's realism, not on the
  floor and walls alone.
- **Denoising is not the "smear":** a crop of the dining chairs and floor at 2560 px with 400 samples (default),
  with the prefilter off, and with 1024 samples at an adaptive threshold of 0.003 looks the same. The "soft,
  over-denoised" look the judges name comes from the soft goods and the plain walls.
- **Concrete ceilings and the grade:** a dark ceiling pushes the auto exposure up and the windows clip. The grade
  now pulls the exposure back by up to half a stop when more than 3 % of the frame is at white (`CLIP_MAX`).
  `CEILING=concrete` stays off by default: the export doesn't know the ceiling's finish.
- **Knobs added:** `CEILING`, `CLIP_MAX`, `FEATURE_TILE` (sage, clay, white), `FLOOR=scan` (the old laminate scan),
  `ADAPTIVE`, `PREFILTER`, `CROP`.

Phase 3 (#84, styling), from s1: `style.py` places props by rules on what the camera sees, nothing per room.
- **Free floor:** `floor_spots` walks a 0.1 m grid of the room's outline, keeps spots against a wall, off door
  swings, on the bare floor (rays down meet the room's floor mesh, not a rug), not in front of glass (a ray toward the
  wall must meet it at every height), with a passage in front, in frame and unoccluded; it prefers corners and the
  sides of the frame.
- **The rules:** a bed gets a rug under its lower two-thirds (sized to the room) and a pair of prints over it; a
  nightstand its lamp moved to the back corner away from the bed, books and a succulent on the bed's side, a carafe
  and a glass on the other if it is large enough; a dining table napkins on its plates, candles, a carafe and a bowl
  of fruit on free spots of its centre line; the living room or kitchen the camera sees most a styled sideboard
  against a plain wall; a bathroom a towel on a bar on a plain wall it faces and a stool with towels where the floor
  shows; the room the camera sees most a floor accent (a tall plant in a corner unless one is in view, else branches
  in a floor vase); deep window sills a bud vase; a terrace or balcony seen through the glass a bistro set; last,
  small props cut by the frame go.
- **s1:** Decor medians 6 / 6 / 5 (from 6 / 5 / 6), the mean over 9 judgments 5.67 as at r7: no gain. The bath
  did not change at all (its camera sees no floor, so no stool) and lost a point: the noise of 3 judges. The judges
  still say "sparse": "bare walls, one small artwork, a minimal table setting"; the small props (napkins, candles,
  books) don't read at the judges' size.
- **s2:** Decor medians 7 / 6 / 6, mean 6.00 (+0.33, a new best). The dining room reached 7 on every line (Overall
  7/7/7). The bistro set: `outdoor_set` takes the outdoor floor the camera rays meet beyond the glass, turns the set
  along the pane it stands behind (the first glass a ray from the camera meets), keeps it 0.45 m clear of the glazing,
  on clear decking and fully in frame, and takes the biggest. Window sills come from the export's `windowSills`
  faces (the 48 x 27 ray grid misses a sill 1.4 % of the frame high). All three bedroom judges name the bedding
  (smeared, melted): that is #85.
- **s3:** Decor medians 7 / 7 / 6, mean 6.56 (+0.56, a new best). The bedroom moved 6 → 7 on the pair of prints:
  `bed_art` takes the print `swap_art` hung over a bed (`H.BEDS`), checks with rays that the wall behind both
  prints is plain (the wall's plane, facing the room, no window, door or piece), and hangs `art.glb` and its pair
  `art2.glb` side by side, together about 0.7 of the bed's width. `hang_art` tries the print at 1.3x before 1x
  (`ART_SCALES`). The judges now name the softness ("over-denoised"), the bedding (#85), the dining room's dark
  foreground plant and "sparse room", and the bath's flat light and cut-off bottles at the left edge.
- **s4:** Decor medians 6 / 6 / 6, mean 6.11 (best 6.56: one round without a gain). `sideboard` walks each wall of
  the most-seen living room or kitchen in 0.1 m steps and keeps the place where the cabinet's whole volume is free
  (rays down), the wall behind it is plain up to its top (rays back; a window above is fine), doors are 0.6 m away,
  0.75 m of passage is in front, and its top is in frame and seen; then a vase with branches toward the frame's edge,
  books and a succulent at the other end, a print leaning on the wall if the wall is plain. `edge_clutter` takes out
  small props cut by a side of the frame, and a plant cut and within 2 m of the lens. **The bedroom was the same
  image as in s3 and went 7/7/7 -> 6/6/7**: a room's 3-judge mean moves about 0.5 on noise alone, so a phase best
  set by one lucky round is hard to beat by 0.3.
- **s5:** Decor medians 6 / 7 / 6, mean 6.22 (best 6.56: the second round without a gain). The table's glassware
  clear (the export's wine glasses had an opaque white "glass"), the runner in flax and the napkins in sage, both with
  a low sheen (a white sheen at the camera's grazing angle reads grey); the sideboard's top a 0.55 m potted plant, a
  ceramic jug and books with a succulent. The judges named none of it: the dining room's "hazy warm-green bloom",
  weak contact shadows and flat rug; the bedroom's duvet ("a messy cloth simulation, not a styled made bed", #85);
  the bath's flat light, CG-white sanitaryware, blown mirror lamp and cut-off flush plate (#86).
- **s6:** Decor medians 7 / 6 / 6, mean 6.22: the third round without a gain, **phase 3 closed as saturated.**
  `towel_rail` walks the bathroom's walls in 0.1 m steps for a flat, plain face (rays back at 20 points over the
  towel's height: no window, door, piece or mirror proud of it), nothing in front of it (rays down), facing the
  camera, the towel whole in frame and seen; it hung over the tub, left of the window. The bath's camera sees almost
  no floor, so no floor-keyed rule had ever run there: `seen_any` counts every surface the camera rays meet, by room.
  A fruit bowl on the dining table (`fruit_bowl`) and a carafe on a nightstand at least 0.45 x 0.36 m are in the
  rules; neither applied here (the table's centre line had two free spots, the nightstand is 0.28 m deep). The
  dining room's s5 image went 6/6/7 -> 7/7/6 on Decor, the bedroom's 6/7/7 -> 6/7/6.

Phase 3 (#84, styling), s1 to s6: **closed as saturated.** The target was a Decor median of 7.5 in the three rooms.
It ended at 7 / 6 / 6 (from 6 / 5 / 6 at r7). The Decor mean over 9 judgments rose from 5.67 at r7 to 6.00-6.56
(best s3, then 6.11, 6.22, 6.22). Sheet: `rounds/s6.jpg` (r7 above, s6 below).
- **What moved:** the bistro set on the terrace and the balcony (s2: the dining room 7 on every line), the pair of
  prints over the bed (s3: the bedroom's Decor 6 -> 7). Both are large and in the middle of the picture.
- **What did not move Decor:** small props (napkins, candles, books, a carafe, glassware, a runner's colour), the
  styled sideboard, the towel rail. At the judges' size they don't read, and the Decor line follows the whole
  picture's impression.
- **Where the judges' complaints are now:** almost none names missing styling (one s6 dining judge: "a large blank
  wall with a curtain fills the left foreground", which is framing). They name the soft, hazy look and blown whites (the grade, #82's line), the duvet's crumpled cloth
  simulation (#85), the bath's flat sunless light and CG-white sanitaryware (#85, #82) and its framing, the flush
  plate cut at the left edge (#86).
- **Judge noise:** the same image scored again moves a room's 3-judge Decor mean by up to 0.67 (bedroom s3 -> s4).
  A 0.3 gain over a phase's best is within that noise; a single lucky round sets a best that is hard to beat.

Spend so far: $6.82 for 17 Modal runs (T4).
