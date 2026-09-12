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
* **modify** — same builder, request as ground truth, before/after renders verified by the critic.
* Both providers stream: the UI shows the model's phase, its reasoning summary, a token counter and, for the builder, the code as it is written.

Every pass is snapshotted as a version (code + renders); restore is one click. The LLM layer is
provider-agnostic: **Anthropic** (Messages API) or **OpenAI-compatible** (Chat Completions).

## Layout

```
backend/     FastAPI + SQLite + Playwright   (uv)
frontend/    Vite + React 19 + TS + Tailwind v4 + shadcn-style UI + TanStack Router/Query (orval client)
kit/         housekit: runtime.js (scene boot, views, headless hooks) + house.js (parametric components) + template/
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

`.github/workflows/docker-publish.yml` pushes `ghcr.io/damhau/housegen` on every push to `main`
(`latest` + `sha-…`) and on `v*` tags (semver), then bumps the image tag in the GitOps repo
(`damhau/k8s-argocd`) for Argo CD to roll out: `main` → the **dev** environment
(`base/applications/housegen-dev`, `sha-<short>` tags), `v*` → **prod** (`base/applications/housegen`).
`deploy/k8s.yaml` is a standalone single-replica manifest with the `/dev/shm` volume Chromium needs.
The image carries its own version (`APP_VERSION` = that tag, `APP_COMMIT`): `/api/v1/health` returns it and
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

## Configuration (backend/.env)

The model, effort, critic rounds, step budget and in-loop render quality below are the defaults; each project can
override them from the run settings sheet (the gear button on the project page, `PATCH /projects/{id}/settings`), and
every job snapshots the settings it started with.

| var | default | meaning |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | `anthropic` or `openai` |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | | credentials |
| `OPENAI_BASE_URL` | | any OpenAI-compatible endpoint |
| `BUILDER_MODEL`, `CRITIC_MODEL` | `claude-opus-5` / `gpt-6-astra` | per-role model |
| `BUILDER_EFFORT`, `CRITIC_EFFORT` | `xhigh`, `medium` | reasoning effort per role (OpenAI none…xhigh, Anthropic low…max) |
| `LLM_MAX_TOKENS` | 96000 | per-turn output cap (reasoning tokens count against it on OpenAI) |
| `BUILDER_MAX_STEPS` | 60 | tool calls per builder pass |
| `CRITIC_MAX_ITERATIONS` | 1 | independent critic rounds (0 disables) |
| `CRITIC_SCORE_THRESHOLD` | 80 | stop when reached with no major issue |
| `BROWSER_CHANNEL` | `chrome` | `chrome`, `msedge`, or empty for Playwright's Chromium |
| `RENDER_ANGLE` | `swiftshader` | WebGL backend: `swiftshader` (software), `gl-egl` or `vulkan` (an NVIDIA GPU) |
| `RENDER_SERVICE_URL`, `RENDER_SERVICE_TOKEN` | | render on the GPU service instead of in-process (see above); `RENDER_SERVICE_FALLBACK=false` fails the render instead of drawing locally when it is unreachable |
| `RENDER_BASE_URL` | `http://HOST:PORT` | where the renderer loads scenes from; the app's public URL when a render service is used |
| `MODEL_PRICES` | list prices for `claude-opus-5`, `gpt-6-astra` | JSON `{model: {input, cached, output[, cache_write]}}` in USD per million tokens, for the cost in each run's summary |

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
