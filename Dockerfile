# syntax=docker/dockerfile:1
#
# Single image: FastAPI backend + headless Chromium (Playwright) + the scene kit,
# serving the built React frontend from "/".
#
#   docker build -t ghcr.io/damhau/housegen .
#   docker run -p 8000:8000 -v housegen-data:/data --env-file backend/.env ghcr.io/damhau/housegen

# ---------------------------------------------------------------------------
# Stage 1 — build the React frontend and vendor three.js for the kit
# ---------------------------------------------------------------------------
# Node 24 = npm 11, the npm that writes package-lock.json on the developer machine: npm 10
# (Node 22) reads the same lock differently and `npm ci` refuses it.
FROM node:24-alpine AS web
WORKDIR /web

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# `npm run build` = vite build (generates src/routeTree.gen.ts) then tsc; outputs /web/dist
RUN npm run api:gen && npm run build

WORKDIR /kit
COPY kit/package.json kit/package-lock.json* ./
RUN npm install --omit=dev                  # node_modules/three, @dgreenheck/ez-tree


# ---------------------------------------------------------------------------
# Stage 2 — Python backend + Chromium headless shell; also serves the SPA
# ---------------------------------------------------------------------------
# A slim Debian Python image plus only what the renderer runs: Playwright's Chromium
# headless shell (what launch(headless=True) uses) and its system libraries. The
# official Playwright image would add Firefox, WebKit, the full Chromium and their
# libraries: about 1 GB compressed that nothing here uses.
FROM python:3.12-slim-bookworm AS app
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Install dependencies first (cached layer), then the browser the pinned Playwright
# wants (cached with it), then the project itself.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
# --with-deps = the apt packages for this browser only. World-readable so the
# unprivileged user below can run it (BROWSER_CHANNEL empty = this bundled browser).
RUN /app/.venv/bin/playwright install --with-deps chromium-headless-shell \
    && rm -rf /var/lib/apt/lists/* \
    && chmod -R a+rX /ms-playwright
COPY backend/src ./src
RUN uv sync --frozen --no-dev

# Scene kit (runtime + components + template) and vendored three.js
COPY kit/house.js kit/runtime.js ./kit/
COPY kit/template ./kit/template
COPY kit/assets ./kit/assets
COPY --from=web /kit/node_modules/three ./kit/node_modules/three
COPY --from=web /kit/node_modules/@dgreenheck/ez-tree/build ./kit/node_modules/@dgreenheck/ez-tree/build

# Built frontend, served from "/" by the API process (STATIC_DIR)
COPY --from=web /web/dist ./web/dist

ENV PATH="/app/.venv/bin:$PATH" \
    ENV=prod \
    LOG_LEVEL=INFO \
    HOST=0.0.0.0 \
    PORT=8000 \
    DATA_DIR=/data \
    KIT_DIR=/app/kit \
    STATIC_DIR=/app/web/dist \
    BROWSER_CHANNEL="" \
    RENDER_BASE_URL=http://127.0.0.1:8000

# The build that is running, shown by /api/v1/health and in the UI header. Set by the
# workflow to the image tag ("1.2.3" on a v* tag, "sha-abc1234" from main) and the commit;
# a local `docker build` gets "dev". Last, so a new version never invalidates the layers above.
ARG APP_VERSION=dev
ARG APP_COMMIT=""
ENV APP_VERSION=${APP_VERSION} \
    APP_COMMIT=${APP_COMMIT}

# Same user name and uid as the Playwright image had, so files on an existing /data
# volume keep their owner. /app stays root-owned and read-only for it: a recursive
# chown would rewrite the whole .venv into one more layer.
RUN groupadd --gid 1001 pwuser \
    && useradd --uid 1001 --gid pwuser --create-home --shell /usr/sbin/nologin pwuser \
    && mkdir -p /data && chown pwuser:pwuser /data
USER pwuser
VOLUME ["/data"]
EXPOSE 8000

# Single process: jobs run in-process and state lives in SQLite + /data, so do NOT
# scale to multiple workers or replicas without adding a job queue + shared storage.
CMD ["uvicorn", "housegen.main:app", "--host", "0.0.0.0", "--port", "8000"]
