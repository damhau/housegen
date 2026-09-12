# The build path is back at v0.1.0 (2026-09-07)

On 2026-09-07 thirteen issues landed on `main` in one day. The owner's read after the day's
runs was that the results got worse, so everything that changes what the builder, the critic
and the intake **see** (prompts, tool descriptions, message contents, rendered views, response
schemas) was put back to its v0.1.0 state. The features that only add text when they are used
(auto-resume, photos attached to a modification, several plan documents) keep their conditional
text; the day's other work (observability, run settings, share link, plan documents,
`apply_patch` fixes) stays.

This file lists what was removed from the build path, why, and where the code still is, so that
each item can come back **one at a time, after a measured run**, instead of all at once.

## Rule

The default path (one plan PDF, labelled photos, a generate or a modify without attachments)
sends byte-identical prompts, tools, messages and views to v0.1.0. Check it with:

```bash
git diff v0.1.0 -- backend/src/housegen/agent/prompts.py   # only RESUME_ADDENDUM may differ
```

Any change to what the model sees must ship with a real run compared against a v0.1.0 run of
the same project (score, steps, cost from the run summary), and with the critic's findings read
by a person.

## Removed from the build path

| what | where it was | why it went | code |
|---|---|---|---|
| Plausibility audit fed to the builder (`render_views` / `check_scene` results said "fix every line") and to the critic ("every line is a major issue") | `agent/tools.py`, `agent/critic.py`, `agent/pipeline.py` | the audit has false positives by construction (a house on an untagged plinth reads as "wall floats above the terrain", a bench on a terrace as "floats"), and both prompts turned them into mandatory fixes and major findings, forcing extra builder rounds on correct geometry | the audit itself stays in `kit/house.js` (`house.audit`, `window.__house.audit`) and the renderer still collects it (`RenderResult.audit`, `render.done audit=N` in the log); commit `8e5bb99` has the feed |
| Plausibility paragraph in `CRITIC_SYSTEM`, `CRITIC_PLAN_SYSTEM`, `CRITIC_MODIFY_SYSTEM`; `CritiqueIssue.kind` (`fidelity` / `plausibility`) | `agent/prompts.py`, `agent/schemas.py` | same mechanism: "severity major however small the object" blocks `done` and adds a fix round | `8e5bb99` |
| Builder sentence "look at the aerial or top render for objects that intersect or float… fix every line" and the `userData.kind` / `"prop"` tagging lines in the kit reference | `agent/prompts.py` | part of the same feature | `8e5bb99` |
| `top` view rendered for every version and given to the critic next to the aerial | `projects/schemas.py` (`STANDARD_VIEWS`, `PLAN_ONLY_VIEWS`), `agent/critic.py` | one more image per critic call and per snapshot for a check that no longer exists | `8e5bb99` |
| Additional photographs as 256 px thumbnails in the first message, full photo through `inspect_image` | `agent/pipeline.py`, `llm/types.py`, `agent/tools.py` | the one change of the day that took information away from the builder; never measured | `8298721` (reverted in `b9d5cbb`) |
| `SheetInfo.document` in the intake schema, `[document n]` in the sheet map, "prefer the most recent document's elevations" in `INTAKE_SYSTEM` | `agent/schemas.py`, `agent/prompts.py` | changes the intake's JSON schema on every run; the document is already in each sheet's caption when there are several | `c2660b6` |
| `apply_patch` and `inspect_image` tool descriptions (per-file atomicity, `attached-n`) | `agent/tools.py` | tool descriptions are in every request; the behaviours they described still exist and explain themselves in their results (the patch error names the files that were written and the ones that were not; the attachments block of a modify request names `attached-n`) | `81dc68c`, `2939372` |
| Attached-photo sentence in `MODIFY_ADDENDUM` and in `CRITIC_MODIFY_SYSTEM` | `agent/prompts.py` | always sent; the attachments block in the request message carries the same instruction only when photos are attached | `2939372` |
| Rendering track: physical sky, AgX, textures (#16), ez-tree (#15), N8AO / CSM / interior mapping (#17), grass and SSR (#20) | `kit/` | darker and flatter than v0.1.0 on a real project; landed without a render looked at by a person | `7d9414f`, `22060bc`, `b7d6cd4`, `3c325d4`, `ab37fd3` (reverted in `6b7bb2d`) |

## Still in the build path, on purpose

- `RESUME_ADDENDUM` and the "scene as the restart left it" renders: only after a server restart (#7). Known gap: a
  resume with nothing written before the restart should be a fresh generate, not a continuation from the template.
- The attachments block of a modification request and the verifier's "ATTACHED photograph" images: only when the
  owner attaches photos (#8).
- Sheet captions "(document n, page p)" and the "Plan documents" paragraph: only when a project has more than one
  plan document (#10).
- `apply_patch` per-file atomicity and the closest-line hint (#23): failure path only.
- Run settings (#18): the defaults resolve to the same model, effort, rounds, step budget and in-loop render
  quality as the `.env` values; a job only differs when the owner changed the sheet.

## Changes since, each with its measured run

- **2026-09-12** (commit after `3f4e778`): the first build furnishes the fixed planting (trees, hedges, beds,
  planters, pots; only movable things wait as suggestions); in-loop renders at high by default (`RENDER_QUALITY`,
  the GPU service makes them cost seconds); the stale "older screenshots are dropped" sentence corrected (pruning is
  gated on prompt size since `79e2b35`); the shed roof's high edge documented as it is built (+z). Baseline: Montelly49-Dev3
  `db527c7a84f2` v1 on `sha-9838d76`, full preset, 4 façades + 5 extras: score 82 done in one pass, 16 steps, 10
  renders, 14.4 min, $6.45. The comparison run: the same inputs on the new build, same preset.

## Observed on the day's runs, not yet acted on

- OpenAI prompt-cache misses right after every new long prefix (routing); the remedy is `prompt_cache_key` per job.
- Pruning of `inspect_image` crops throws away far more cached tokens than it saves at the current prices; the
  batch-of-3 rule from #5 predates the price table.
- The cache-miss flag in the run summary compares the current prompt's ratio; it should compare `cached` with the
  previous turn's input.
