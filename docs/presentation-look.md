# The presentation look (2026-09-08)

The runtime has two render paths that must never be confused:

- **Diagnostic** (`?quality=low|medium|high`): what the builder's `render_views` and the critic's
  photo comparisons see, and what the version pictures are rendered with. Frozen with the build
  path (see `build-path-2026-09-07.md`). Even fill, noon-ish sun, a grey card for a sky.
- **Presentation** (`?look=presentation`, and `?look=ultra` on top of it): what the owner sees in
  the interactive viewer, on the project page and on the share page. Never requested by the
  backend, never shown to a model. Because nothing a model sees changes, the look can be worked on
  without the measured-run rule.

Everything below lives in `kit/runtime.js`; the scene code the builder writes is unchanged.

## What presentation does

- A physical sky (three's `Sky` addon) for a sun at 42° elevation, azimuth 200° (early afternoon,
  a little west of south), rendered once into a PMREM environment map. That one map, scaled down,
  is both the light from the sky and the background the camera sees, so they cannot drift apart.
  The fog colour is read from the same sky just above the horizon, opposite the sun.
- The sun light itself: warm, 2.2, placed by the runtime. A hemisphere light at 0.1 is all that is
  left of the diagnostic fill.
- Ground to the horizon and exponential fog, so the plot is no longer an island: a sheet with a
  hole under every mesh the scene tagged as terrain, following the scene's own `groundY` for 40 m
  past them, then easing over 80 m to the mean of that height; the lawn's colour at the plot's
  edge darkening into a meadow over 60 m, under one grain. (Until 2026-09-17 it was one flat disc
  at a single height, with a hole under the first terrain mesh only: on a sloped plot with a
  second, wider context terrain it cut through the lower houses.)
- MSAA on the post-processing target (the diagnostic path has none while the composer runs),
  ambient occlusion at a lighter blend, a light vignette. ACES tone mapping at exposure 1.0.
- The far plane moves from 500 m to 2000 m so the horizon exists.

`look=ultra` replaces the render pass with an accumulation pass: every frame moves the sun to a
point of a 1.5° disc (an area light: soft, distance-dependent penumbrae) and shifts the camera by
a fraction of a pixel (supersampled anti-aliasing), then blends the frame into a running average.
Interactive pages converge while the camera rests (`house:accumulate` messages carry the
progress; any camera move restarts); headless pages finish every sample before `ready`.
Deterministic: Halton sequences. The shadow map drops to 2048 in ultra, the penumbra hides it.

## Two rules learned on the first real project

1. **The runtime owns the look on a presentation page, and applies it after `buildScene`.** The
   scene code of a real project set the background colour, the fog colour, the sun position, the
   environment intensity and the hemisphere intensity from inside `buildScene`, and one of those
   calls (`scene.background.set(...)`) threw once the background was a texture. So the background
   stays a plain colour while `buildScene` runs, and `applyPresentationLook` sets background, fog,
   environment, hemisphere and sun afterwards, whatever the scene did with `ctx.scene` or `ctx.sun`.
2. **The Sky shader is several times brighter than the scene's lights.** Its radiance is written
   for an exposure of about one half. Used at unit intensity as an environment, it overexposes
   everything and the ACES shoulder hides it as a milky, flat image that no longer responds to the
   sun or fill settings, which is what "brighter and flatter" looks like. It has to be scaled down
   as light and as background (0.4 and 0.34 here).

## Calibration

Measured on two real projects and the template, quality=high against presentation, same views,
same 1024 px JPEGs the app produces, luminance percentiles of the whole frame (p10 = the shade,
p50 = the walls, p90 = the sky). Environment and background intensity swept together:

| project, view | quality=high p10/p50/p90 | env 0.2 | env 0.3 | env 0.4 |
|---|---|---|---|---|
| ac386 north (façade in shade) | 65 / 202 / 217 | 59 / 153 / 203 | 77 / 173 / 213 | 91 / 187 / 220 |
| ac386 south-photo (façade in sun) | 67 / 204 / 217 | 51 / 139 / 202 | 61 / 165 / 213 | 73 / 182 / 220 |
| template north | 129 / 142 / 213 | 43 / 106 / 191 | 59 / 117 / 211 | 73 / 126 / 222 |

0.4 puts a shaded white wall a little under its diagnostic brightness and a sunlit one level with
it; the aerial views come out darker overall because the lawn is lit by the sky instead of the
diagnostic path's strong hemisphere fill. The knobs stay available on the URL for the next round
(`p_env`, `p_bg`, `p_sun`, `p_hemi`, `p_expo`, `p_fog`, `p_rayleigh`, `p_turbidity`), and
`window.__house.look` reports the values in force.

Cost with software GL (the server's renderer): a presentation view renders in about the time of a
quality=high view; an ultra view with 16 samples took about 7 s per sample on the first real
project, so ultra is for the owner's GPU, not for the server.

## The photographed sky (2026-09-25)

The analytic sky (three's Sky) read washed out: a pale blue-white sky with no cloud. The presentation
look now draws a photographed one: Poly Haven's `kloofendal_48d_partly_cloudy_puresky` (CC0, deep blue,
scattered cumulus), built by `kit/scripts/make_sky.py` into `kit/sky/` (committed, 2.3 MB):

- the lighting: its 1k HDR with the sun's disc taken out (the directional light is the sun), turned so
  the photographed sun stands at the scene sun's azimuth, scaled so a level surface receives what the
  analytic sky gave it (`ANALYTIC_SKY_IRRADIANCE` = 1.1266, measured in the browser for the default sun);
- the sky the camera sees: its 4k picture (zenith to 12° below the horizon, linear radiance / `scale`,
  sRGB-encoded) on a dome at the far plane, tone-mapped with the frame, so it also holds when the effects
  are dropped and the frame is drawn straight to the canvas.

Calibration on TestVillaGille, the same shaded plaster patch as the analytic look: the photographed
sky's horizon is darker and bluer, so walls came out grey-blue (175, 175, 179 against 195, 197, 193).
`photoSky.env` 2.4 (fill) and `photoSky.envSaturation` 0.4 (the fill's blue toned down, the sky kept)
put it back at 194, 194, 195; `photoSky.saturation` 1.35 gives the dome back the blue tone mapping takes.
Knobs: `p_skyenv`, `p_skyenvsat`; `?sky=analytic` draws the old sky, for comparison.

The same day, the sky the camera sees was redone: a measured sky's blue, tone-mapped, came out grey and
mauve (130, 143, 175, saturation 0.22, against 124, 164, 230, 0.68 on a daylight photograph), so the blue is
now designed: a gradient from `photoSky.zenith` #5180d6 to `photoSky.horizon` #9bbeee, the HDRI's clouds laid
over it (a cloud = where the photograph is grey-white instead of blue), all drawn through an inverse of three's
ACES so the frame shows exactly these colours. Measured from the ground: 123, 162, 229, saturation 0.67. The
picture is the 8k HDRI developed for the screen (908 KB, no banding once scaled), with mipmaps and the wrap
seam handled. Knobs: `p_zenith`, `p_horizon` (hex without #); `?skymode=clear` leaves the clouds out.

## A finding about the renderer itself

Two headless renders of the same scene with the same code differ: about 0.4 % of the template's
pixels and 4 % of a leafy project's differ by more than 8 levels, along leaf and shadow edges.
Byte-identical pictures are therefore not a usable regression test; the check used here was that
the old-code-vs-new-code difference equals the same-code-vs-same-code difference (template: mean
absolute difference 0.250 vs 0.252; ac386: 1.44 vs 1.48 across the four views).

## Frame cost on the viewer (2026-11-24, renderer v47, #147)

Measured on a Modal T4 at the owner's screen (a 1964 x 1166 viewer at 150 %, 5.15 MP drawn), on Montelly
(cdc30a72d1ac, version 30), against v45:

- The composer's passes ran at the drawing size times the pixel ratio (11.6 MP instead of 5.15 MP). Each chain
  now sets its composer's pixel ratio to 1 and its size in drawing pixels.
- Glass is see-through alpha instead of transmission (`?glass=half|full` brings back transmission at half or
  full resolution; ultra keeps it). Transmission drew every opaque object a second time: 29 ms of a 94 ms
  outdoor frame's GPU time, about 300 MB. In the side-by-side views (façade, conservatory roof, porthole,
  glazed doors from inside), the panes look the same.
- Outdoors with the sun above 8°, the lamps' stand-ins are hidden: twelve lights in every shader cost a third
  of an outdoor frame. At dusk and at night (sun study), they light the windows again. Both light sets are
  compiled before they are needed: the outdoor set when the walk starts, the lit one in the background when
  the sun study opens.
- While the camera moves (two pose changes less than 1 s apart), frames come from a second chain at
  2.4 MP at most (`?adapt_mp`, `?adapt=0`), stretched to the canvas. It has no frame-at-rest pass, and its
  ambient occlusion is at half its width and height (`?ao_scale`), with a depth-aware stretch. The full
  chain comes back 300 ms after the last move. The second chain is allocated and drawn once at load, so the
  first move does not hitch. It exists only on canvases above about 3 MP.
- At rest, the ambient occlusion stays at full size: at half size, its grain showed along every edge (door
  frames, bookcases, room corners), even with the depth-aware stretch.

| T4, ms per frame (CPU p50 / GPU p50) | v45 | v47 |
|---|---|---|
| orbit outside | 43.9 / 82.6 | 30.3 / 26.2 |
| opening view at rest | 38.9 / 92.5 | 28.5 / 33.1 |
| turning in Salon, Réserve, Ch.1 | 9.1, 7.0, 5.2 / 65.6, 76.6, 89.6 | 10.0, 9.3, 6.6 / 12.5, 13.2, 11.0 |
| at rest in Salon, Réserve, Ch.1 | 7.3, 15.5, 7.5 / 61.2, 77.4, 66.4 | 8.6, 9.4, 8.9 / 33.4, 42.4, 36.9 |

Still open: walking into a second or third room stalls one frame for 2.3 to 2.6 s on the T4 (v45 too, where
it compiled 79 and 21 programs). v47 compiles four there, material variants made for that room's captured
light (box-projected `env-box`, planar mirrors), which the walk's precompile, done in the first room, does
not make. Walking out of the house costs one frame of about 350 ms without compiling anything.

## Not done yet, in the order to try them

Textures with grain, then trees, then something behind the glass, each on a presentation page,
each looked at on a real project before it lands. The reverted commits of 2026-09-07 hold working
code for all three (`7d9414f`, `22060bc`, `b7d6cd4`), minus the calibration this note describes.
