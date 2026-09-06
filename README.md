# housegen

Upload a house's plan set (PDF) and one photo per façade; an agentic workflow turns them into an
interactive three.js exterior model that you can then modify by chat.

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

* **builder** — gets the plan sheets and photos directly and a goal, not a recipe. Tool-use loop: `read/write/edit_file`, `render_views`, `check_scene`, `finish`. Every render is its own quality check against the photos.
* **critic** — independent model call that only sees photo/render pairs and returns a score + concrete geometric fixes. One round by default (`CRITIC_MAX_ITERATIONS`, 0 disables); further rounds send the findings back to the builder.
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

The root `Dockerfile` builds the frontend, vendors three.js and packages the backend on the
Playwright Python image, which ships Chromium. `deploy/serve.py` serves the built SPA from the
same process. State lives in `/data` (SQLite + project files): run **one** replica.

```bash
docker compose up --build            # http://localhost:8000, key from backend/.env
# or
docker run -p 8000:8000 -v housegen-data:/data --env-file backend/.env --shm-size 1g ghcr.io/damhau/housegen:latest
```

`.github/workflows/docker-publish.yml` pushes `ghcr.io/damhau/housegen` on every push to `main`
(`latest` + `sha-…`) and on `v*` tags (semver). `deploy/k8s.yaml` is a single-replica manifest
with the `/dev/shm` volume Chromium needs.

## Configuration (backend/.env)

| var | default | meaning |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | `anthropic` or `openai` |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | | credentials |
| `OPENAI_BASE_URL` | | any OpenAI-compatible endpoint |
| `BUILDER_MODEL`, `CRITIC_MODEL` | `claude-opus-5` / `gpt-6-astra` | per-role model |
| `BUILDER_EFFORT`, `CRITIC_EFFORT` | `xhigh`, `high` | reasoning effort per role (OpenAI none…xhigh, Anthropic low…max) |
| `LLM_MAX_TOKENS` | 96000 | per-turn output cap (reasoning tokens count against it on OpenAI) |
| `BUILDER_MAX_STEPS` | 60 | tool calls per builder pass |
| `CRITIC_MAX_ITERATIONS` | 1 | independent critic rounds (0 disables) |
| `CRITIC_SCORE_THRESHOLD` | 80 | stop when reached with no major issue |
| `BROWSER_CHANNEL` | `chrome` | `chrome`, `msedge`, or empty for Playwright's Chromium |

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
* Camera views are named after the façade they **look at** (`north` = camera north of the house).

## Adding a component to the kit

Add a function to `kit/house.js`, export it in the default object, and document one line in
`KIT_REFERENCE` (`agent/prompts.py`). The builder can use it on the next job.
