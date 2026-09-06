# Backend image: FastAPI + headless Chromium (Playwright) + the scene kit.
# The Playwright base image ships Chromium and every system library it needs,
# so BROWSER_CHANNEL stays empty here and Playwright's own browser is used.
#
# Build from the repo root:  docker build -f backend/Dockerfile -t housegen-backend .

FROM mcr.microsoft.com/playwright/python:v1.62.0-noble AS base

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# --- kit: vendor three.js once (node is present in the playwright image) ---
WORKDIR /app/kit
COPY kit/package.json kit/package-lock.json* ./
RUN npm ci --omit=dev 2>/dev/null || npm install --omit=dev
COPY kit/ ./

# --- backend deps (cached layer) ---
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# --- backend source ---
COPY backend/src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/opt/venv/bin:$PATH" \
    ENV=prod \
    HOST=0.0.0.0 \
    PORT=8000 \
    DATA_DIR=/data \
    KIT_DIR=/app/kit \
    BROWSER_CHANNEL="" \
    RENDER_BASE_URL=http://127.0.0.1:8000

VOLUME ["/data"]
EXPOSE 8000
USER pwuser
CMD ["uvicorn", "housegen.main:app", "--host", "0.0.0.0", "--port", "8000"]
