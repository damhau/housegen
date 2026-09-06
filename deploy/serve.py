"""Production entry point: the housegen API plus the built frontend from one process.

Kept outside the backend package so it can exist without touching the app code;
fold into housegen.main when convenient. STATIC_DIR points at the Vite `dist/`.

    uvicorn serve:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse

from housegen.main import app

STATIC_DIR = Path(os.environ.get("STATIC_DIR", "/app/web/dist"))
INDEX = STATIC_DIR / "index.html"

if STATIC_DIR.is_dir():
    # hashed bundles
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="spa-assets")

    # everything else that is not an API route, the kit or a scene: the SPA (history fallback)
    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        candidate = STATIC_DIR / path
        if path and candidate.is_file() and STATIC_DIR in candidate.resolve().parents:
            return FileResponse(candidate)
        return FileResponse(INDEX)
