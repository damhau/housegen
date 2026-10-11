# Spike: the interior as data (2026-10-11)

**The question.** Should a furnished house keep its interior as code (the LLM's modules) or as data
(`interior.json`), the code only generating it? The hybrid tested here: the builder's code runs once,
the kit records what it builds, and from then on the house is drawn from the data, which is what
edits, styles and checks work on.

**The house.** TestVillaGille (`fa8bd00053e6`), version 21: the lighting-only re-furnish with the
v31 light fixtures. 27 modules, 24 rooms toured, renderer v31, GPU (RTX 3050, WSLg d3d12).

## How it works

1. **`make_variants.py`** makes two copies of the scene's modules:
   - **the recorder:** the scene's imports of `housekit/furnish` and `housekit/interior` go through
     `js/rec-furnish.js` and `js/rec-interior.js`, and the scene's own piece functions are wrapped.
     Each object they build carries its call (`userData.spec`).
   - **the interpreter:** `scene.js` with the furnishing and lighting calls replaced by
     `replay(ctx, data)`.
2. **`record.py`** loads the recorder and writes `interior.json`: one entry per piece, with its call,
   transform, footprint and role.
3. **`walk_sheet.py --src`** walks the house built from the data. Its frames are compared with the
   original's.
4. **`edits.py`** times the edits in the open page. **`checks.py`** checks the layout from the JSON
   in Python.

## Results

| | Code (today) | Data (spike) |
|---|---|---|
| Pieces | 360 built by 10 modules | 360 entries: 323 kit calls (99 fixtures, 135 models), 37 calls to 7 functions of the scene's own code, 0 unknown |
| Fidelity, 24 rooms | — | 17 identical, 6 within noise (mean difference ≤ 0.54/255, no pixel off by more than 40). Worst: 0.29 % of one attic bedroom's pixels, a pendant's canopy the code had bent to the roof slope |
| Page ready | 32.0 s | 32.2 s (rebuilding the 360 pieces: 8.0 s, mostly loading models, the same work as the code) |
| Move a piece | the LLM patches a module, then the page reloads to check (a small chat change: ~60 s, #35/#72) | 0 ms + 29 ms frame |
| Replace a model (sofa) | same | 438 ms (download) + 20 ms frame |
| All 99 fixtures in black | a builder job | 64 ms + 31 ms frame |
| Layout audit after a change | a page load per check (20–40 s on the render service) | 0.9 s in the same page (layout.js); 0.1–0.2 s in Python from the JSON (`checks.py`, footprints as boxes) |
| Size for the LLM (o200k tokens) | 11.8k (the 10 modules replaced) | 34.9k as recorded; 20.9k compact (yaw instead of quaternion, millimetres, no defaults) |

**The Python checks against the renderer's audit:**
- **Rooms off the plan's areas:** the same 5 rooms.
- **Floor overlaps:** the same main ones (the hall's wardrobe, bench and coat hooks; an armchair and a
  plant; beds and nightstands), with coarser depths.
- **What they miss:** what needs the meshes (curtains against a kitchen run, floating pieces).

## What stays code

- **The structure:** `buildInteriors` (slabs with stair openings, the stairs tread by tread, ceiling
  patches, the attic walls trimmed to the roof, the roof lights) and `refineRoomDetails` (the skirting
  reworked). That's 269 lines in 4 modules. Data would need vocabulary for stairs, openings in a
  ceiling and knee walls under a roof.
- **Hand-built pieces:** 7 functions of the scene's code (`linenBed`, `shelving`, `plantShelf`,
  `print`, `sideboard`, `roundMirror`, a heat pump). The data names them (`{ code: { module, fn } }`);
  they are 10 % of the pieces.
- **Geometry edited after a call:** the attic pendant's canopy following the slope (lost), and the
  attic tiles trimmed to the roof (kept as a named post-processing step, `post: ["trimRoof"]`).

## What this means

- **The data is faithful and the edits are instant.** Changing one piece, one finish everywhere or
  one model costs milliseconds and no LLM. Checks drop from a page load to under a second, or 0.2 s
  without a browser. That is what the chat, a style picker (#76), Restyle (#75) and dragging a sofa in
  the viewer need.
- **Code is more compact than data (1.8×).** Loops and helpers become one entry per piece: a laid
  table is 16 entries. So the builder should not read the whole file. It should query and edit it
  through tools, a room at a time (a room is 1–2k tokens). Richer vocabulary (a laid table, a pair of
  nightstands) compresses it again.
- **The edits shown run on the orbit view.** In the walk, the static meshes are merged by room, so an
  edit must merge that room again (the whole house takes 90 ms, v17). A rebuilt fixture's lights must
  be registered again with the runtime's lamp pools (the spike hid them, since the places were the same).
- **Not measured:** whether the LLM builds a better first interior by writing code that produces data,
  or by calling tools. That needs real Furnish runs with an API key (stage 2).

## Found on the way

- **The first pull was taken mid-job.** The first pull of TestVillaGille (`/api/v1/projects/<id>/files`, the working
  copy) was made at 15:41 UTC, while the re-lighting job was still in its repair round. It snapshotted
  version 21 at 15:46:48. The spike then used version 21's own files (`/scenes/<id>/versions/21/src/`).
- **The re-lighting itself (v31 fixtures, by day):** the builder used the fixtures well, but the rooms
  came out darker and greyer, with mid-tones 178 → 154 on average over 24 rooms. The old ceiling
  lights were filling in for daylight. Sheets: `spikes/interior-pilot/sheets/relight-tvg-*.jpg`.
