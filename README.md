# housegen

Upload a house's plan set (PDF) and, if you have them, photos of the façades; an agentic workflow
turns them into an interactive three.js exterior model that you can then modify by chat. Without
photos the agent reads the plans first and asks you the few questions the drawings cannot answer.

The key design decision: **the LLM writes and edits scene code, and looks at renders of its own
work.** A deterministic spec-to-mesh generator would give a correct but dead box; letting the model
compose a small parametric kit keeps the open-ended expressiveness while a render-and-critique loop
turns a one-shot lottery into a converging process.

```
 plan pages ─┐   ┌─────────┐  edits   ┌────────────┐
 photos     ─┴─▶ │ builder │ ───────▶ │ workspace  │  src/*.js
                 │ (tools) │ ◀─────── │ (housekit) │
                 └────┬────┘  renders └─────┬──────┘
                      │ ▲                   │ headless Chrome
                      │ │ critique          ▼
                      │ │            ┌────────────┐
                      │ └─────────── │   critic   │ ◀── photos
                      ▼              │  (vision)  │
                  version n          └────────────┘
```

* **intake** (no photos only) — one structured call over the plan sheets before the build: what the house is as drawn, which sheet is what (the elevation sheets become the critic's ground truth), and up to six questions for the owner, each with a suggested default. The answers join the project's **brief**, which every builder pass receives.
* **builder** — gets the plan sheets and photos directly and a goal, not a recipe. Tool-use loop: `read/write/edit_file`, `apply_patch`, `render_views`, `inspect_image`, `check_scene`, `finish`. Every render is its own quality check against the photos, or against the elevation drawings through the straight-on `<side>-elevation` views.
* **critic** — independent model call that only sees reference/render pairs (photo and photo-like render, or elevation sheet and elevation render) and returns a score + concrete geometric fixes. One round by default (`CRITIC_MAX_ITERATIONS`, 0 disables); further rounds send the findings back to the builder. Skipped when there is neither a photo nor an elevation sheet.
* **modify** — same builder, request as ground truth, before/after renders verified by the critic; its findings
  are stored on the version as a review to apply on request, nothing is fixed unasked.
* Both providers stream: the UI shows the model's phase, its reasoning summary, a token counter and, for the builder, the code as it is written.

Every pass is snapshotted as a version (code + renders); restore is one click. The LLM layer is
provider-agnostic: **Anthropic** (Messages API) or **OpenAI-compatible** (Chat Completions).

## Layout

```
backend/     FastAPI + SQLite + Playwright   (uv)
frontend/    Vite + React 19 + TS + Tailwind v4 + shadcn-style UI + TanStack Router/Query (orval client)
kit/         housekit: runtime.js (scene boot, views, headless hooks) + house.js (parametric components) + template/
photo/       listing photos: export.py (scene page → GLB) + blender/ (the Cycles pipeline, Python 3.11 + bpy, on Modal)
deploy/      k8s.yaml, modal_render.py (GPU render service), modal_photo.py (listing photo service)
```

## Run

```bash
# 1. backend
cd backend
cp .env.example .env            # set LLM_PROVIDER and the matching API key
uv sync
uv run playwright install chromium   # or set BROWSER_CHANNEL=chrome to use an installed Google Chrome
uv run uvicorn housegen.main:app --reload

# 2. kit (vendors three.js, served by the backend at /kit)
cd ../kit && npm install

# 3. frontend
cd ../frontend
npm install
npm run api:gen                  # regenerate the typed client from ../frontend/openapi.json
npm run dev                      # http://localhost:5173  (proxies /api, /scenes, /kit to :8000)
```

Regenerate `openapi.json` after changing the API:

```bash
cd backend && uv run python -c "import json; from housegen.main import app; print(json.dumps(app.openapi()))" > ../frontend/openapi.json
```

## Deploy (one image: API + UI + headless Chromium)

The root `Dockerfile` builds the frontend, vendors three.js and packages the backend on a slim
Python image with only Playwright's Chromium headless shell and its system libraries (about
0.5 GB compressed; the official Playwright image with its three browser engines was 1.4 GB).
`STATIC_DIR` (set in the image) makes `housegen.main` serve the built SPA from the same process.
State lives in `/data` (SQLite + project files): run **one** replica.

```bash
docker compose up --build            # http://localhost:8000, key from backend/.env
# or
docker run -p 8000:8000 -v housegen-data:/data --env-file backend/.env --shm-size 1g ghcr.io/damhau/housegen:latest
```

Releases are cut from [Conventional Commits](https://www.conventionalcommits.org/) by
`.github/workflows/release.yml` on every push to `main`: `feat:` bumps the minor, `fix:`/`perf:` the patch, a
`!`/`BREAKING CHANGE` the minor while 0.x, anything else (`docs:`, `chore:`, a free-form message) releases
nothing. A release tags the pushed commit `vX.Y.Z` (no bump commit: the version lives in the tag only), publishes
a GitHub Release and builds `ghcr.io/damhau/housegen:X.Y.Z` (+ `X.Y`, `sha-<short>`, `latest`); a push without a
release builds `sha-<short>` (+ `latest`). Either build is rolled out to the **dev** environment by bumping the
image tag in the GitOps repo (`damhau/k8s-argocd`, `base/applications/housegen-dev`) for Argo CD to sync
(`deploy.yml`, which also asks Argo CD to sync now and waits for healthy). **Prod** is a manual step: Actions →
"Deploy to prod" → the version (`deploy-prod.yml` checks that the image exists, then rolls it out to
`base/applications/housegen`). Never push `v*` tags by hand.
`deploy/k8s.yaml` is a standalone single-replica manifest with the `/dev/shm` volume Chromium needs.
The image carries its own version (`APP_VERSION` = the image tag, `APP_COMMIT`): `/api/v1/health` returns it and
the UI header shows it; a local build or `uvicorn` says `dev`.

### GPU render service (optional)

Rendering is half the wall clock of a build: the pod draws WebGL in software (SwiftShader), about
45 s per `render_views` call. `housegen.render.service` is the same renderer behind one HTTP endpoint,
to run from the same image on a machine with a GPU; the app sends every render there when
`RENDER_SERVICE_URL` is set and falls back to the local browser when the service cannot be reached.
`deploy/modal_render.py` deploys it on Modal (a T4 that scales to zero, per-second billing: cents per
build); its docstring is the runbook. The service loads scenes from the app's `RENDER_BASE_URL`, which
must then be the app's public URL. `GET /health` on the service reports the WebGL renderer string:
`ANGLE (NVIDIA, …` means the GPU draws, `SwiftShader` means it does not.

### Listing photos (optional)

The **Listing** tab shoots a saved, furnished version as photos for a real-estate listing (#91): the building from
outside first (#93), then each flat's living room, dining room (or kitchen), main bedroom, bathroom and other
bedrooms, in that order, captioned with the room's area ("Salon, 19 m²"). A `listing` job runs beside the scene's own jobs and calls the photo
service, `deploy/modal_photo.py` on Modal:

1. `prepare` returns the version's house from the volume's cache when it has it (a "Shoot again" waits for no GPU);
   otherwise `export` (a T4, else an L4 or an A10G: the first one free) loads the version's scene page headless
   (`photo/export.py`) and exports the house to GLB, about a minute, and `blender` (8 CPU cores, no GPU, #95)
   imports it. Two steps then run side by side on that machine. The first finds up to six distinct viewpoints in
   each room a set can use (a living room, dining room, kitchen, bedroom or bathroom above its `MIN_AREA` in
   `listing/shots.py`, #96): its corners, along its long axis, from its doorways, with a lower, wider lens in a
   small room. Door leaves are left out of the scoring: the shoot swings a leaf that stands in the frame out of it.
   The second, `photo/blender/outside.py`, builds the set's exteriors (#97): the house in its surroundings (the
   project's context files, fetched from the app: the land, the aerial photo, the neighbouring buildings, the lidar
   trees, the far landscape) or the house alone on its plot, and scores up to six exterior viewpoints around it,
   each with a sun at a real date and hour at the plot that lights the façades in view. The `.blend` files are
   cached per version on the `housegen-photo` volume, the two exterior builds side by side: switching a set's
   exteriors builds only the missing one.
2. `shoot` (an RTX PRO 6000, one container per photo, `LISTING_PARALLEL` at a time) renders one room in Cycles
   with `photo/blender/main.py shoot`. The pipeline keeps the scene's finishes (a finish's scan at full
   resolution, its colour kept) and adds the staging: decor, art, objects. The light is a listing
   photographer's: daylight, the room's lamps where the sun does not reach, and a flash bounced off the ceiling
   so that white tiles and ceilings stay white in mixed light.

Each exterior viewpoint is previewed, and one vision call keeps two to `LISTING_EXTERIOR_MAX` of them in listing
order, the cover photo first ("Vue extérieure, côté sud-ouest", in the language of the room names). Without an
answer, the three best-scored viewpoints lead the set. The **Exteriors** choice next to the staging picks them: the
house in its surroundings (the default when the project has them), the house alone on its plot (the default
without, under the plot's sun, or a Swiss one), or no exteriors at all. A set shot again keeps its choice; another
choice previews and picks the building's views again.

A room with several viewpoints is previewed from each of them, fully dressed and lit (1280 px, 64 samples). One
vision call per room (the project's critic model and effort, either provider) picks the viewpoint an agency would
publish and may name one piece to take out of it (#92). The chosen one is then shot at full size with the
preview's sun and lamps. Without an answer, the best-scored viewpoint is shot; `LISTING_PICK=false` skips the
previews and the call.

The set lands in `versions/<n>/listing/` (`<k>.jpg`, `previews/<k>-<i>.jpg` and `set.json`: unit, room, caption,
viewpoints, the editor's choice and reason, camera, sun, cost) and shows in the tab, as a ZIP
(`GET /api/v1/projects/<id>/listing/<n>/zip`) and on the share page. A later version marks the set out of date;
**Shoot again** reuses its choices (viewpoint, light, removed piece).

A set has a staging (#94), picked next to the shoot buttons and stored with the set, so **Shoot again** keeps it
unless you pick another:

- **Furnished** (the default, for agencies): the project's pieces, dressed and staged as above.
- **Light**: the furniture without the decor. Accessories, art, plants and cushions are left out, and the
  pipeline adds no staging.
- **Empty**: the architecture alone, its finishes and light. Only what is fitted stays: the kitchen, the
  bathrooms, wardrobes, radiators and the fixed lights.

`photo/blender/staging.py` sorts the pieces by their names. The exteriors are shot the same way at every level.
The kit's catalogue furniture (#90) is kept as the builder placed it: the pipeline swaps its own bed and armchair
only for the kit's parametric and named pieces. A catalogue bed keeps its frame and pillows, and gets the same
cloth-simulated duvet and throw as the pipeline's own beds instead of the kit's block bedding.

The service uses the render service's token, so the app needs no new secret: it derives the URL from
`RENDER_SERVICE_URL` (`housegen-render-web` → `housegen-photo-web`) unless `PHOTO_SERVICE_URL` is set.
The runbook (the volume, the asset upload, the deploy) is the docstring of `deploy/modal_photo.py`.

### Renderer versions

`kit/versions/<name>/` holds snapshots of `house.js` + `runtime.js`; `kit/versions/index.json` lists them and names
the one the build path is **pinned** to. A scene page served with `?kit=<name>` loads that snapshot (its import map is
rewritten, the scene sources are untouched), so every saved version can be drawn with every renderer: the viewer has a
renderer selector and a Compare button (two renderers side by side), `look_sheet.py` has `--kit`, and each version
records the renderer its pictures were drawn with. `kit/` itself is the working copy ("dev"). Workflow: change `kit/`,
compare it with the newest snapshot on a real version, copy it to a new snapshot when it is right, move the pin only
after a measured run.

## Configuration (backend/.env)

The model, effort, critic rounds, step budget and in-loop render quality below are the defaults; each project can
override them from the run settings sheet (the gear button on the project page, `PATCH /projects/{id}/settings`), and
every job snapshots the settings it started with. To give a project its own settings from the start, use **Change**
under Run settings on the new project form (the `settings` field of `POST /projects`); `GET /settings/defaults`
returns the defaults a project without settings runs with.

| var | default | meaning |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | `anthropic` or `openai` |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | | credentials |
| `OPENAI_BASE_URL` | | any OpenAI-compatible endpoint |
| `BUILDER_MODEL`, `CRITIC_MODEL` | `claude-opus-5` / `gpt-6-astra` | per-role model |
| `BUILDER_EFFORT`, `CRITIC_EFFORT` | `xhigh`, `medium` | reasoning effort per role (OpenAI none…xhigh, Anthropic low…max) |
| `LLM_MAX_TOKENS` | 96000 | per-turn output cap (reasoning tokens count against it on OpenAI) |
| `BUILDER_MAX_STEPS` | 60 | tool calls per builder pass |
| `BUILDER_PRUNE_ABOVE_TOKENS` | 200000 | drop old render screenshots from the builder's history once a prompt exceeds this (0 = never); a prune costs a prompt-cache miss, so only for the context window |
| `BUILDER_PRUNE_ABOVE_TOKENS_ANTHROPIC` | 600000 | the same for Claude runs: a prune there also drops the thinking of every later turn, and the models take 1M tokens; set 200000 for a 200k-window model such as Haiku 4.5 |
| `CRITIC_MAX_ITERATIONS` | 1 | independent critic rounds (0 disables) |
| `CRITIC_SCORE_THRESHOLD` | 80 | stop when reached with no major issue |
| `CRITIC_FIRST_FIX` | `true` | a first build always gets one fix pass on the critic's findings (2+ rounds); the score near the threshold is noise, the findings are not |
| `RENDER_QUALITY` | `high` | in-loop render quality (`render_views` without a quality); `medium` was the software-rendering compromise |
| `PLAN_MAX_PAGES` | `40` | sheets per plan document rasterised and shown to the intake and the builder; a longer PDF is kept but cut, and the plans panel says "N of M sheets" (each sheet: ~1-2.5k tokens per builder call, its PNG bytes on every call) |
| `PLAN_DPI` | `150` | rasterisation of the sheets (dimension strings on a 1:100 sheet stay legible; `inspect_image` zooms at 300 dpi) |
| `BROWSER_CHANNEL` | `chrome` | `chrome`, `msedge`, or empty for Playwright's Chromium |
| `RENDER_ANGLE` | `swiftshader` | WebGL backend: `swiftshader` (software), `gl-egl` or `vulkan` (an NVIDIA GPU) |
| `RENDER_SERVICE_URL`, `RENDER_SERVICE_TOKEN` | | render on the GPU service instead of in-process (see above); `RENDER_SERVICE_FALLBACK=false` fails the render instead of drawing locally when it is unreachable |
| `PHOTO_SERVICE_URL`, `PHOTO_SERVICE_TOKEN` | next to the render service | the listing photo service (see above); empty = `RENDER_SERVICE_URL` with `housegen-photo-web`, `RENDER_SERVICE_TOKEN` |
| `LISTING_WIDTH`, `LISTING_HEIGHT`, `LISTING_SAMPLES` | `3840`, `2160`, `512` | a listing photo's size and Cycles samples |
| `LISTING_PICK` | `true` | preview every viewpoint of a room and let the picture editor choose (#92) |
| `LISTING_PREVIEW_WIDTH`, `LISTING_PREVIEW_HEIGHT`, `LISTING_PREVIEW_SAMPLES` | `1280`, `720`, `64` | a preview's size and samples |
| `LISTING_PARALLEL` | `16` | photos and previews in flight at once, one GPU container each |
| `LISTING_EXTERIORS`, `LISTING_EXTERIOR_MAX` | `true`, `4` | lead the set with the building from outside, at most this many views (#93) |
| `RENDER_BASE_URL` | `http://HOST:PORT` | where the renderer loads scenes from; the app's public URL when a render service is used |
| `RENDER_KIT` | pinned in `kit/versions/index.json` | the renderer snapshot the build path draws with (builder, critic, version pictures); the viewer picks its own |
| `MODEL_PRICES` | list prices for `claude-fable-5-2`, `claude-opus-5-5`, `claude-opus-5`, `claude-sonnet-5-5`, `gpt-6-astra` | JSON `{model: {input, cached, output[, cache_write]}}` in USD per million tokens, for the cost in each run's summary |

## Quality gates

```bash
cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy src/ && uv run pytest -x
cd frontend && npm run typecheck && npm run build
```

## Adding a tool to the agent

1. Add a `ToolSpec` to `backend/src/housegen/agent/tools.py` and a handler on `BuilderTools`.
2. Mention it in `BUILDER_SYSTEM` (`agent/prompts.py`).
3. Both providers pick it up automatically (the loop is provider-agnostic).

## Kit highlights

* `perimeterWalls` builds a storey's walls with openings, auto-oriented; `windowUnit`/`door`/`slidingDoor` fill them.
* `terrain` (spot heights or a function) registers itself so `groundY`, `ribbon` (draped paths), `pebbleStrip`,
  `leafTree`/`leafBush` (instanced leaves) and the props (`swingSet`, `bench`, `bicycle`) sit on the ground.
* The runtime gives every scene the same look: sun + shadows, environment light, ambient occlusion and anti-aliasing at
  final quality, a lawn with grain, fog, named camera views. Headless renders reuse the shadow map across views.

## Kit conventions (the ones that bite)

* Units are metres. `+x` east, `+z` south, `+y` up, ground at `y = 0`.
* A wall's exterior is on your **right** when walking `from → to`. With north up that means
  going **counter-clockwise** around the footprint. `perimeterWalls` handles it for you.
* Opening `offset` is measured from the **left end of the façade as seen from outside**.
* Camera views are named after the façade they **look at** (`north` = camera north of the house). `north-photo` stands
  where the photographer stood; `north-elevation` is straight-on and near-orthographic, like the elevation drawing.

## Adding a component to the kit

Add a function to `kit/house.js`, export it in the default object, and document one line in
`KIT_REFERENCE` (`agent/prompts.py`). The builder can use it on the next job.
