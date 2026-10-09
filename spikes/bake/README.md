# #42 bake spike: Cycles lightmaps for the walk

One storey of TestVillaGille (fa8bd00053e6, ground floor: 9 rooms, 499 meshes) baked with Blender Cycles
under WSL (CUDA on the RTX 3050; OptiX fails there with 7805), then drawn in three.js with the bake alone
(no lights, no occlusion, no captures) from the walk's own viewpoints (#54), next to the live walk.

```bash
S=<scratch dir>; CHROME=<chrome for testing 145>   # GPU only: GALLIUM_DRIVER=d3d12 etc., see walk_sheet.py
# 1. the house as built, from its scene page on dev (headless, kit 2026-10-09-v19): house.glb + rooms.json
cd backend && WALK_CHROME=$CHROME uv run python ../spikes/bake/export.py fa8bd00053e6 $S/bake
# 2. the storey at y=0: xatlas lightmap UVs, the meshes joined, Cycles DIFFUSE (direct + indirect, no colour)
cd $S/bake && LD_LIBRARY_PATH=/usr/lib/wsl/lib uv run --no-project --python 3.11 --with bpy --with xatlas \
    python -u .../spikes/bake/bake.py house.glb rooms.json 0 . 4096 64      # lightmap.exr, storey.glb, bake.json
# 3. Blender's denoiser (OIDN) over the lightmap
uv run --no-project --python 3.11 --with bpy python -u .../spikes/bake/denoise.py lightmap.exr lightmap_dn.exr
# 4. the baked storey at the viewpoints of a walk_sheet.py run, next to its frames
cd backend && LM=lightmap_dn.exr WALK_CHROME=$CHROME uv run python ../spikes/bake/view.py $S/bake <walk JSON> sheet.jpg
```

## Result (2026-10-09)

| step | time |
|---|---|
| glTF import | 7 s |
| lightmap UVs (xatlas, 499 meshes, one 3811² atlas) | 81 s (Blender's smart project + pack: over 15 min, stopped) |
| bake 4096², 64 samples | 28 min: ~17 min of single-threaded Blender set-up, ~4 min on the GPU, the rest the margin |
| denoise (OIDN) | 4 s |
| total | 30 min for one storey of five |

Sizes: lightmap 83 MB (half-float EXR, 44 MB denoised); it would ship as RGBM 8-bit, around 10–20 MB at
4096² or 3–5 MB at 2048² per storey. storey.glb (the storey with its lightmap UVs): 31 MB.

Picture (`docs/walk/42-bake-testvillagille.jpg`): without the denoiser the walls and ceilings are covered in
blotchy noise; with it they are clean. The light falls off across walls and ceilings, contact shadows under
the furniture are soft, there are no lamp glow spots; the frames read greyer and with more contrast than the
live walk (p10 121–165 against 112–178, sat50 0.047–0.075 against 0.059–0.086). The windows show a plain
sky and meadow and the mirrors are dark only because the test page draws neither the surroundings nor the
mirrors: a real integration keeps the live ones.

Traps:
- Blender bakes each selected object as its own Cycles session: 499 objects kept the GPU idle for over ten
  minutes. Join them first.
- three.js `lightMap` needs `lightMapIntensity = π` with a Cycles DIFFUSE bake (radiance, not irradiance/π).
- The EXR reads the right way up with `flipY = false` and the glTF's second UV set (`channel = 1`).
