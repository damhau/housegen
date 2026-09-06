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
FROM node:22-alpine AS web
WORKDIR /web

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# vite build first: it generates src/routeTree.gen.ts (git-ignored) that the type check needs
RUN npm run api:gen && npx vite build && npx tsc --noEmit -p tsconfig.app.json   # outputs /web/dist

WORKDIR /kit
COPY kit/package.json kit/package-lock.json* ./
RUN npm install --omit=dev                  # node_modules/three


# ---------------------------------------------------------------------------
# Stage 2 — Python backend + Chromium; also serves the SPA
# ---------------------------------------------------------------------------
# The Playwright image ships Chromium and every system library it needs, so the
# renderer uses the bundled browser (BROWSER_CHANNEL empty).
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble AS app
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Install dependencies first (cached layer), then the project itself.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/src ./src
RUN uv sync --frozen --no-dev

# Scene kit (runtime + components + template) and vendored three.js
COPY kit/house.js kit/runtime.js ./kit/
COPY kit/template ./kit/template
COPY --from=web /kit/node_modules/three ./kit/node_modules/three

# Built frontend, served from "/" by deploy/serve.py
COPY --from=web /web/dist ./web/dist
COPY deploy/serve.py ./serve.py

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

RUN mkdir -p /data && chown -R pwuser:pwuser /data /app
USER pwuser
VOLUME ["/data"]
EXPOSE 8000

# Single process: jobs run in-process and state lives in SQLite + /data, so do NOT
# scale to multiple workers or replicas without adding a job queue + shared storage.
CMD ["uvicorn", "serve:app", "--host", "0.0.0.0", "--port", "8000"]
