"""The render service: the headless renderer behind one HTTP endpoint, for a machine with a GPU.

    uvicorn housegen.render.service:app --host 0.0.0.0 --port 8000

Runs from the same image as the app (deploy/modal_render.py deploys it on Modal). The app
calls it through `housegen.render.remote.RemoteRenderer` when RENDER_SERVICE_URL is set. The
scene is loaded from the URL the app sends (its public RENDER_BASE_URL), so the service holds
no project data: a request in, JPEGs out.

Every call needs `Authorization: Bearer <RENDER_SERVICE_TOKEN>`; the service refuses to run
without a token configured.
"""

from __future__ import annotations

import base64
import logging
import secrets
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from housegen.core.config import get_settings
from housegen.core.exceptions import RenderError
from housegen.core.logging import configure_logging
from housegen.render.renderer import Renderer

logger = logging.getLogger(__name__)

renderer = Renderer()  # this process's browser (the service never calls another service)


class RenderIn(BaseModel):
    scene_url: str
    views: list[str] = Field(default_factory=list, max_length=12)
    quality: str = "high"
    camera: dict[str, float] = Field(default_factory=dict)
    width: int = Field(default=1280, ge=320, le=4096)
    height: int = Field(default=800, ge=200, le=4096)
    image_width: int = Field(default=1024, ge=256, le=4096)
    jpeg_quality: int = Field(default=82, ge=30, le=100)


class RenderOut(BaseModel):
    images: dict[str, str]  # view -> base64 JPEG
    errors: list[str]
    console: list[str]
    audit: list[str]
    duration_ms: int
    gl: str | None  # the WebGL renderer that drew it


class HealthOut(BaseModel):
    status: str
    version: str
    angle: str
    gl: str  # "ANGLE (NVIDIA, …" on a GPU, "… SwiftShader …" in software


def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
    token = get_settings().RENDER_SERVICE_TOKEN
    if not token:
        raise HTTPException(503, "RENDER_SERVICE_TOKEN is not set on the render service")
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not secrets.compare_digest(given, token):
        raise HTTPException(401, "bad or missing bearer token")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    configure_logging(s.LOG_LEVEL, s.ENV)
    logger.info("render_service.startup", extra={"angle": s.RENDER_ANGLE, "version": s.APP_VERSION})
    yield
    await renderer.close()


app = FastAPI(title="housegen render service", lifespan=lifespan)


@app.get("/health")
async def health() -> HealthOut:
    """Starts the browser if needed: the `gl` field is the proof of a GPU (or of its absence)."""
    s = get_settings()
    return HealthOut(
        status="ok", version=s.APP_VERSION, angle=s.RENDER_ANGLE, gl=await renderer.probe()
    )


@app.post("/render", dependencies=[Depends(require_token)])
async def render(inp: RenderIn) -> RenderOut:
    s = get_settings()
    # the caller's sizes for this call (the settings are per process; one caller per service)
    s.RENDER_WIDTH, s.RENDER_HEIGHT = inp.width, inp.height
    s.RENDER_IMAGE_WIDTH, s.RENDER_JPEG_QUALITY = inp.image_width, inp.jpeg_quality
    with tempfile.TemporaryDirectory(prefix="housegen-render-") as tmp:
        try:
            res = await renderer.render(
                inp.scene_url, inp.views, Path(tmp), quality=inp.quality, camera=inp.camera or None
            )
        except RenderError as exc:
            raise HTTPException(500, exc.message) from exc
        images = {
            view: base64.b64encode(path.read_bytes()).decode("ascii")
            for view, path in res.images.items()
        }
    return RenderOut(
        images=images,
        errors=res.errors,
        console=res.console,
        audit=res.audit,
        duration_ms=res.duration_ms,
        gl=renderer.gl,
    )
