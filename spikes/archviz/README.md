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
was bought. `made/` is ours. The reference renders are third-party marketing images, kept in `reference/`
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
