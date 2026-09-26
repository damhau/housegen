# Cycles stills (issue #40, step 0), 2026-09-26

Question: can Blender Cycles turn one of our scenes into listing-quality interior photos?
Project: TestVillaGille v3 (dev), ground floor staged with the Martel pack (dining table, shell chairs,
table setting, curtains, modular sofa, rug, coffee tables, pouf, frames, bed).

## Pipeline tried
1. `export_glb.py <scene_dir> <out.glb>` (from `backend/`, the app's Playwright): the scene page in
   headless Chrome, `GLTFExporter` over the scene's `buildScene` group (the proof scene set
   `window.__exportRoot = ctx.group`; the real thing would be a runtime hook). Lamps hidden by the
   light budget are made visible; custom-shader meshes and instanced foliage are left out.
2. `render_villa.py import villa.glb villa.blend`, then `render_villa.py render villa.blend <view> out.png
   1600 1000 512 0` with `uv run --no-project --python 3.11 --with bpy` (bpy 5.0.1 from PyPI).
   Multiple-scattering sky + its sun at the presentation look's position, AgX, OIDN denoiser, level
   camera with lens shift (vertical lines straight), lamps at 40 W.

## Results (RTX 3050 6 GB, CUDA under WSL2)
- Export 57 s (284 MB GLB, textures re-encoded as PNG); import 8 s (3674 objects, 49 lights).
- 1600×1000, 512 samples adaptive: **~200 s per image**; 640×400 × 64 samples: 7 s (for tuning).
- Clearly more photographic than the browser's Final look: bounced daylight, sun patches, contact
  shadows, no lamp hot spots. Comparison sheet: the villa's dining, living and bedroom photo views.

## What broke, and the fixes to make for real
- **Room ceilings sit 2 cm inside the builder's floor slab** (`interior.js` floorPlan: ceiling at
  `y + height + 0.02`): two surfaces 2 cm apart, shaded black in jagged patches where they meet.
  Proof: hide `kind == "ceiling"`. Real fix: the ceiling at the slab's underside, or none under a slab.
- **Instanced foliage** (EXT_mesh_gpu_instancing, tens of thousands of leaf cards): Blender's glTF
  importer makes one object per instance, >10 min and counting. Proof: left out (hedges read as bare
  balls). Real fix: merge each InstancedMesh into one mesh before export.
- OptiX does not initialise under WSL (error 7804): CUDA + OIDN on the CPU. A Linux GPU box has both.
- Blender's sky `sun_rotation` = compass azimuth (0 north, 90 east); glTF's north (-z) = Blender +Y:
  the runtime's sun azimuth maps straight across.
- To tune: exposure per room (north rooms come out dim), white balance (5900 K used), a black
  coffee table exported as glass-like, curtains lost their grey, lamp power.
