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
npm run dev                      # http://localhost:5173  (proxies /api, /scenes, /shared, /kit, /auth to :8000)
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
2. `shoot` (an RTX PRO 6000, else an L40S; one container per photo, `LISTING_PARALLEL` at a time)
   renders one room in Cycles with `photo/blender/main.py shoot`. The pipeline keeps the scene's finishes (a
   finish's scan at full resolution, its colour kept) and adds the staging: decor, art, objects. The light is a
   listing photographer's: daylight, the room's lamps where the sun does not reach, and a flash bounced off the
   ceiling so that white tiles and ceilings stay white in mixed light.

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

### House styles

A furnished house draws in a named style (`kit/styles.js`, renderers from `2026-11-14-v36`): `scandinavian` (the
default) or `contemporary`. The style decides the fixtures' finish, the bath and kitchen fittings, the handles, the bath
tiles and floor, the bedding, the rugs and the decor; the pieces name roles and leave these values out. The scene keeps
its style in `src/style.js` (`export default { style: "contemporary" }`), with any override for the whole house or one
room.

- **Furnish** asks for the style. The interior job writes `src/style.js` before the builder runs and tells the builder to
  leave the style's values out of the pieces.
- **Style**, in the viewer's toolbar, saves the version on screen in the other style as a new version, with no run
  (`POST /api/v1/projects/{id}/versions/{n}/restyle`). The new version keeps its source's renderer; its room pictures are
  drawn again in the background. Restore brings the old style back.
- A version without `src/style.js`, drawn with a renderer that has no styles, or whose pieces name their own finish or
  colour can't switch: `GET …/versions/{n}/style` says why and lists those pieces, and the viewer offers a Furnish run
  instead.
- To test a renderer before it is the viewer's default, pass `kit=<snapshot>` to Furnish (`POST …/interior`, a form
  field) or to a listing shoot (`POST …/listing`, JSON). An unlisted name answers 422.

### Sign-in and organisations

People sign in with Google or Microsoft (OpenID Connect, `/auth/login/<provider>`); projects belong to an
organisation and its members share them (#80). A provider is offered when its client id is set. The session is a
signed cookie (`housegen_session`: Secure, HttpOnly, SameSite=Lax) holding the user and the organisation they work
in. The identity is the provider's issuer and subject, never the email: an invitation is accepted through its link
(`/invite/<token>`) by whoever opens it while signed in. The email matters in one place: the first sign-in of
`AUTH_ADMIN_EMAIL` with an email the provider verified (Google `email_verified`; Microsoft a personal account or the
`xms_edov` claim) becomes the owner of the **default organisation**, which the startup migration created for every
project from before accounts.

Everything is behind the session (#105). Every `/api/v1/**` route, the job event streams included, answers 401
signed out and 403 (`no_organisation`) before the first organisation. The public routes are `/api/v1/health`,
`/api/v1/me`, an invitation link's preview (`GET /api/v1/invitations/<token>`), a share link's view
(`GET /api/v1/shared/<token>`) and `/auth/*`. A project's routes and its files (`/scenes/<id>/…`: the scene, the
versions, the renders, the photos, the plan sheets, the surroundings) are those of the organisation the session works
in; another organisation's project answers 404, as one that does not exist. The check runs before a route does, so
before any job is enqueued. `/kit/**` and the SPA's pages stay public: the SPA sends a signed-out visitor to sign in.

A share link (`/s/<token>`) works signed out through paths of its own, which map onto the shared version's files and
nothing else: `/shared/<token>/scene/index.html` (with `src/…`, `renders/…` and the listing photos next to it, so
the page's relative URLs work), `/shared/<token>/context/…` (the surroundings) and `/shared/<token>/photos/<file>`.
Revoking the link closes them.

Two tokens open the app without a session, both for reads only (GET and HEAD; a write answers 403 `read_only`). Each
is sent as `Authorization: Bearer <token>` or as the `housegen_token` cookie:

- `RENDER_SERVICE_TOKEN`, the service token, reads `/scenes/**` of every organisation. The GPU render service, the
  listing photo service and this app's own headless renderer present it when they load a scene page. A headless
  browser presents it as the cookie, set for the scene page's host only, so the page's requests to other hosts (map
  tiles, fonts) never carry it. Without `RENDER_SERVICE_TOKEN`, the app's own renderer uses a token of its process.
- `AUTH_READONLY_TOKEN` reads `/api/v1/**` and `/scenes/**` of every organisation, for checks on a deployed app.
  Its copy for those checks is in `~/.config/housegen/readonly-token`:

  ```bash
  curl -H "Authorization: Bearer $(cat ~/.config/housegen/readonly-token)" \
      https://housegen-dev.apps.dhconsulting.ch/api/v1/projects
  ```

A third token, the agent token (#112), lets Claude start jobs on dev. It is set on dev only. It is on when both
`AUTH_AGENT_TOKEN` and `AUTH_AGENT_ORGANISATION` (an organisation id) are set; with only one of them, startup logs
`auth.agent.half_configured` and the token stays off. It counts only as `Authorization: Bearer <token>`, never as the
cookie, so a scene page's own script can't have a browser send it.

- Reads (GET and HEAD) work like a member's session in that organisation: its projects through `/api/v1/**` and their
  files under `/scenes/**`. Another organisation's project answers 404.
- Writes go through an allow-list of route templates (`backend/src/housegen/accounts/agent.py`): `modify`, `chat`,
  `interior` (Furnish), `versions/{number}/fix`, `versions/{number}/restore`, `versions/{number}/restyle`,
  `jobs/{job_id}/cancel` and `listing`,
  under `/api/v1/projects/{project_id}/`. Every other write answers 403 `agent_forbidden`, including a route added
  later, until someone adds it to the list.
- The organisation's limits apply to its jobs. The token acts as its own user, "Claude (agent token)", who is not a
  member and can't sign in. Its writes log `auth.agent.write` (route, project, job) and its refusals
  `auth.agent.denied`.

Its copy for Claude is in `~/.config/housegen/agent-token`:

```bash
curl -X POST -H "Authorization: Bearer $(cat ~/.config/housegen/agent-token)" -F message="…" \
    https://housegen-dev.apps.dhconsulting.ch/api/v1/projects/<id>/chat
```

The app fails closed: without a sign-in provider and without `AUTH_DISABLED=1`, it does not start.

In the app, a signed-out visitor goes to `/signin`, with one button per configured provider; share links (`/s/…`) and
invitation links stay open. A signed-in user without an organisation lands on the wizard at `/welcome`: join an
organisation that invited your verified email (`GET /api/v1/me/invitations`), or create one and invite coworkers.
Each invitation is a link. With `RESEND_API_KEY` and `EMAIL_FROM` set, housegen also emails it through Resend
(#107), in English, from `EMAIL_FROM` with the inviting owner as Reply-To: who invites, to which organisation, the link
and its expiry, as plain text and simple HTML, without images or tracking. The response's `email_sent` says how the send
went, and every invitation carries `emailed_at` (the last email that went out) and `email_attempted_at` (the last try),
so a page opened later still shows a failed send. A failed send keeps the invitation and the UI offers its link to
copy. **Send again** on a pending invitation
(`POST /api/v1/organisations/<id>/invitations/<id>/send`) waits `EMAIL_RESEND_AFTER_S` (60 s) between two emails of the
same invitation (429 `rate_limited`). Without both settings, nothing is emailed and you copy the link yourself; with
only one of them, the startup log warns `email.half_configured` and email stays off. The logs are `email.sent` and
`email.failed` with the invitation id and Resend's message id, never the address or the key. The organisation menu in
the header switches between organisations and opens the settings (`/organisation`: name, members, pending
invitations, the organisation's limits). With `AUTH_DISABLED=1`, the app shows none of this.

Each organisation has three spending limits (#107), checked before a job is created:

- **Jobs at once**: jobs queued or running (or interrupted, waiting to resume). A chat change that waits for the
  project's running job does not count until it starts.
- **Jobs per day**: jobs started since midnight UTC, every kind (builds, changes, chat turns, the interior, listing
  photos, intake).
- **Listing photos per month**: the Modal spend of the listing sets started this calendar month (UTC), in USD. A new
  set needs room for `LISTING_ESTIMATE_USD` ($8, a little over the dearest set measured on dev); a running set counts at
  that estimate and at its own cost once it ends.

A job over a limit answers 429 with `details: {"reason": "limit", "limit": "concurrent_jobs" | "jobs_per_day" |
"listing_usd_per_month", "used", "allowed", "resets_at"}` and a message that names the limit, the use and when it
resets; the UI shows it where you started the job. A change the chat hands to the builder is checked the same way,
and the chat answers with the message when it is refused. The counts come from a ledger of started jobs
(`usage_records`), so deleting a project does not give its jobs back. The defaults are `LIMIT_CONCURRENT_JOBS`,
`LIMIT_JOBS_PER_DAY` and `LIMIT_LISTING_USD_PER_MONTH`; the administrator gives an organisation its own from the
admin view (`PUT /api/v1/admin/organisations/<id>/limits`: a number, `"unlimited"`, or `null` for the default). The
default organisation has no limits (the startup migration lifts them once). Members read their organisation's limits
and use on `/organisation` (`GET /api/v1/organisations/<id>/limits`).

The administrator is the user whose provider-verified email is `AUTH_ADMIN_EMAIL`, in any case, with whichever
provider (#108); with `AUTH_DISABLED=1`, the local user. Their account menu links to the admin view, `/admin`: every
organisation (those nobody has joined yet included) with its members, roles and projects, and a **Move to…** action
per project that gives it to another organisation after a confirmation, and each organisation's limits next to its use
of them, with **Edit limits**. A move changes only who sees the project: its
files, versions, listing sets and share link are stored by project id and stay as they are. A move is refused (409)
while a job is queued, running or interrupted on the project, and logged as `admin.project_moved`. The API is under
`/api/v1/admin/` (`GET organisations`, `GET projects`, `PUT projects/<id>/organisation`,
`PUT organisations/<id>/limits`). It answers 403 `not_admin`
to anyone else, needs no organisation of the administrator's own, and the read-only token may read it.

To set it up:

1. Run `deploy/auth_apps.sh` with your own `az` login. It creates the Microsoft app (work and personal accounts,
   the `email` and `xms_edov` claims), lists the Google console pages to fill in, and writes
   `~/.config/housegen/auth-{dev,prod,local}.env`.
2. Create the `housegen-auth` secret from the env file of each environment (the script prints the commands). The
   deployment reads it through an optional `secretRef`.
3. For local sign-in, append `auth-local.env` to `backend/.env` and open the app through Vite
   (`http://localhost:5173`, which proxies `/auth`). Without it, set `AUTH_DISABLED=1`: one implicit user who owns
   the default organisation. The app does not start with neither.

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
| `RENDER_SERVICE_URL`, `RENDER_SERVICE_TOKEN` | | render on the GPU service instead of in-process (see above); `RENDER_SERVICE_FALLBACK=false` fails the render instead of drawing locally when it is unreachable. The services send the token back to read the scene pages (see "Sign-in and organisations") |
| `PHOTO_SERVICE_URL`, `PHOTO_SERVICE_TOKEN` | next to the render service | the listing photo service (see above); empty = `RENDER_SERVICE_URL` with `housegen-photo-web`, `RENDER_SERVICE_TOKEN` |
| `LISTING_WIDTH`, `LISTING_HEIGHT`, `LISTING_SAMPLES` | `3840`, `2160`, `512` | a listing photo's size and Cycles samples |
| `LISTING_PICK` | `true` | preview every viewpoint of a room and let the picture editor choose (#92) |
| `LISTING_PREVIEW_WIDTH`, `LISTING_PREVIEW_HEIGHT`, `LISTING_PREVIEW_SAMPLES` | `1280`, `720`, `64` | a preview's size and samples |
| `LISTING_PARALLEL` | `16` | photos and previews in flight at once, one GPU container each |
| `LISTING_EXTERIORS`, `LISTING_EXTERIOR_MAX` | `true`, `4` | lead the set with the building from outside, at most this many views (#93) |
| `RENDER_BASE_URL` | `http://HOST:PORT` | where the renderer loads scenes from; the app's public URL when a render service is used |
| `RENDER_KIT` | pinned in `kit/versions/index.json` | the renderer snapshot the build path draws with (builder, critic, version pictures); the viewer picks its own |
| `AUTH_GOOGLE_CLIENT_ID`, `AUTH_GOOGLE_CLIENT_SECRET` | | sign-in with Google, offered when the id is set |
| `AUTH_MICROSOFT_CLIENT_ID`, `AUTH_MICROSOFT_CLIENT_SECRET` | | sign-in with Microsoft (`common`: work and personal accounts) |
| `AUTH_SESSION_SECRET` | random per process | signs the session cookie; without it every restart signs everyone out |
| `AUTH_ADMIN_EMAIL` | | the administrator (the admin view, `/admin`) when the provider verified it; their first sign-in owns the default organisation |
| `AUTH_DISABLED` | `false` | no sign-in: one implicit user in the default organisation (local dev, the tests). Without it, the app needs a provider to start |
| `AUTH_READONLY_TOKEN` | | a bearer token that reads `/api/v1/**` and `/scenes/**` of every organisation, GET and HEAD only (checks on a deployed app) |
| `AUTH_AGENT_TOKEN`, `AUTH_AGENT_ORGANISATION` | | the agent token (dev only): a bearer token that reads like a member of that organisation (its id) and writes through an allow-list of routes; on only when both are set |
| `AUTH_PUBLIC_URL` | the request's origin | where the providers send the browser back (`https://host`); by default from `X-Forwarded-Proto`/`-Host` |
| `AUTH_SESSION_DAYS`, `AUTH_INVITATION_DAYS` | `14`, `14` | how long a session and an invitation link last |
| `RESEND_API_KEY`, `EMAIL_FROM` | | email invitations through Resend when both are set (`EMAIL_FROM`: `housegen <housegen@your.domain>`, a domain verified in Resend) |
| `EMAIL_RESEND_AFTER_S` | `60` | seconds between two emails of the same invitation (**Send again**) |
| `LIMIT_CONCURRENT_JOBS`, `LIMIT_JOBS_PER_DAY`, `LIMIT_LISTING_USD_PER_MONTH` | `3`, `30`, `40` | an organisation's limits unless the administrator set its own (see "Sign-in and organisations") |
| `LISTING_ESTIMATE_USD` | `8` | what a listing set is counted at against the month's budget until its own cost is known |
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
