# Interior spike A: the room layout from the PDF (issue #33), 2026-09-23

Question: can the rooms, partitions and doors of a floor be written as code from a permit plan
precisely enough for an interior walkthrough, and can that be checked automatically?

Project: `test2` on dev (`df4cb51b6685`), sheet 3 (ground floor, 1:100, raster 2481 px wide = 58.82 px/m).
The transcription was done by Claude (standing in for the builder), from the sheet with a metric grid over it.

## The check

- `plan.html` (served next to a scene's `src/`): an orthographic camera straight down, the model cut at
  `cut` m, the cut solids filled with a stencil pass. Exterior walls blue, partitions red, door leaves green,
  at the sheet's scale so it lays over the sheet pixel for pixel.
- `plan_render.py <out.png> "cut=1.5"`: renders it with the system Chrome (SwiftShader), prints the room
  areas, writes the partitions next to the PNG (`.json`).
- `score.py <sheet.png> <render.png> <overlay.png> [partitions.json]`: the sheet's wall poché (grey 191 masonry,
  228 light partitions) against the render, inside the footprint. Recall/precision at 3/5/10 cm, an overlay
  (grey = agree, orange = on the sheet only, magenta = in the model only) and, per partition, where the
  sheet's band actually is ("centre 0.05 m south").

The sheet registration (px/m, the outer NW corner) was found by hand from the poché's bounding box;
automating it is a translation search of the exterior walls' render over the sheet.

## Results

| pass | partition precision@5cm | recall@5cm | precision@10cm |
|---|---|---|---|
| 1, read by eye (`test2-ground-floor.pass1.js`) | 0.906 | 0.909 | 0.963 |
| 2, after the per-partition report (`test2-ground-floor.js`) | 0.962 | 0.920 | 0.965 |

- Masonry walls (25 cm) were within 1–2 cm on the first pass; light partitions were 4–10 cm off, and one
  "partition" was a stair outline. One pass of the report put every partition within 0–2 cm.
- Room areas against the sheet: kitchen-living 26.97/26.95, salon 19.44/19.35 (pass 1), chambre 14.56/14.60.
- What is left is mostly the **exterior** scene (built by the v0.1.0 builder): the bedroom's east window is
  1.20 m where the sheet has 2.35 m, and there is a salon window on the east façade the sheet does not have.
  The interior job will need to own or correct the openings of the floors it furnishes.
- Rendered inside (runtime, `in-*` views): the geometry holds. The presentation look is wrong indoors: the
  sky environment lights every room unoccluded (blue walls, olive oak). Interiors need their own lighting
  (ceiling lights, bounced light: path-traced stills, baked light for the walk).

## What the builder would need

1. The sheet at full resolution with a metric grid, and crops on demand.
2. The plan check above as a tool: overlay + per-partition report after each write.
3. `kit/interior.js` (`floorPlan`, `partition`, `interiorDoor`) in its reference.

# Spike B: furniture, walk, one path-traced still, 2026-09-23

Same project, ground floor furnished following the furniture drawn on sheet 3 (`test2-furniture.js`).

## Furniture (`kit/furnish.js`)

- Scanned models from Poly Haven (CC0, glTF, 1k textures, 14 MB for 9 models), fetched by
  `kit/scripts/fetch_models.mjs` into `kit/assets/polyhaven/` (git-ignored). Poly Haven is mostly antique and
  industrial furniture: usable in a Scandinavian interior are the oak/leather armchair, the oak side table, the
  cube shelving, a coffee table, a sideboard, plants, a vase, a globe pendant. **No modern sofa, bed, dining
  table or chairs.**
- Parametric pieces in the same style: sofa, bed, nightstand, chair, dining set, wardrobe, kitchen run (tall
  units, sink, hob, wall units), WC, basin, bathtub, rug, flush ceiling light. They size themselves to the room,
  which a fixed model cannot.
- Placement: `onWall(room, edge, at, piece)` (back to the wall, facing in) and `place(piece, [x, z], rotY)`.
- The pendant is a real light (a warm point light at the globe); flush discs are emissive.

## Walk (`kit/walk.js`, `?walk=1`)

- Drag to look, WASD, click/tap the floor to glide; `__house.rooms / jumpTo(name) / walkTo(x, z)` for the app.
- Where one can walk: ONE top-down render of the storey between knee and head height into a 5 cm grid, a
  distance transform, walkable = 25 cm clearance, only inside the rooms. Steps slide on it; a glide is A* over
  it (straightened), through the doors; an unreachable target ends at the nearest reachable point.
- Checks it gives for free: `blockedDoorways()` (a door side where nobody can stand) and `reach()` (the walkable
  areas and the rooms in each). On test2 it caught three furnishing mistakes of mine: an armchair in front of the
  hall door, a 2.30 m sofa leaving 34 cm at each end (the salon cut in two), shelving closing the passage past the
  sofa. After the fixes: two areas, one per flat, as it should be.
- A partition door is now built as solid pieces + a lintel (no hole in one shape: the extruded bottom face of a
  shape whose hole touches the floor lay across every doorway, invisible in 3D but a wall from above).

## Path-traced still (`pt.html`, three-gpu-pathtracer 0.0.24)

- Runs on the scene as built (no conversion): sun + gradient sky through the windows, the lamps, bounced light.
- On this machine (SwiftShader, CPU) ~10 s per sample at 640×400; on the Modal GPU it is a matter of seconds.
- Salon at 640×400, 96 samples: 342 s here. The light reads right (daylight from the sliders bouncing off the
  ceiling and walls, soft shadows under the furniture, the pendant glowing), unlike the realtime render. At 96
  samples it is still grainy: a broker still needs ~500+ samples or a denoiser, i.e. the GPU service.

# Textures (points 1 and 2), 2026-09-23

- `kit/finishes.js`: Poly Haven textures (CC0, 1k JPEG colour + normal + roughness, 16 MB for 8) laid at their
  real size (the API gives it: 1.70 m for the oak planks), so geometry carries UVs in metres (`metricUV`, a box
  projection; the floors' extruded caps already are). `await loadFinishes()` before building; without them every
  finish falls back to its plain colour (node tests, a missing fetch).
- Floors: light oak planks (living, bedroom), large beige stone tiles (halls, bath, WC). Partitions: fine white
  plaster. Furniture oak: a light veneer tinted honey. Sofa: grey mélange (the texture's colour, tinted). Bedding:
  white with the weave's relief. Throw: herringbone wool. Rug: cotton weave. Kitchen: oak worktop.
- What it gives: the kitchen, floors, tables and wood now read as real materials; the sofa reads as upholstery.
  The bed still reads as a 3D model (pillows, duvet): soft goods are where scanned models (a pack) are needed.
- Not done yet: skirting boards, window reveals and sills, handles, sockets, curtains (point 4); the inner face
  of the exterior walls still uses the exterior render material.
