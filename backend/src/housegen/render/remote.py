"""Client of the render service (`housegen.render.service`) and the dispatcher the app renders with.

The service runs the same `Renderer` on a machine with a GPU and returns the pictures over
HTTP. `RenderClient` sends every render there when RENDER_SERVICE_URL is set and otherwise
(or when the service cannot be reached, with RENDER_SERVICE_FALLBACK) draws in this process.
"""

from __future__ import annotations

import base64
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from housegen.core.config import get_settings
from housegen.core.exceptions import RenderError
from housegen.render.renderer import Renderer, RenderResult

logger = logging.getLogger(__name__)


class RemoteRenderer:
    """`POST {RENDER_SERVICE_URL}/render`; the JPEGs come back base64-encoded in the JSON body
    and are written to `out_dir/<view>.jpg`, like the local renderer does."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport  # tests: an ASGITransport onto the service app
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            s = get_settings()
            self._client = httpx.AsyncClient(
                base_url=s.RENDER_SERVICE_URL.rstrip("/"),
                headers={"Authorization": f"Bearer {s.RENDER_SERVICE_TOKEN}"},
                timeout=httpx.Timeout(s.RENDER_SERVICE_TIMEOUT_S, connect=30.0),
                transport=self._transport,
            )
        return self._client

    async def render(
        self,
        scene_url: str,
        views: list[str],
        out_dir: Path,
        quality: str = "high",
        camera: dict[str, float] | None = None,
    ) -> RenderResult:
        s = get_settings()
        out_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 — tiny local mkdir
        body: dict[str, Any] = {
            "scene_url": scene_url,
            "views": views,
            "quality": quality,
            "camera": camera or {},
            "width": s.RENDER_WIDTH,
            "height": s.RENDER_HEIGHT,
            "image_width": s.RENDER_IMAGE_WIDTH,
            "jpeg_quality": s.RENDER_JPEG_QUALITY,
        }
        started = time.perf_counter()
        try:
            r = await self._http().post("/render", json=body)
        except httpx.HTTPError as exc:  # connection refused, timeout, cold start too slow
            raise RenderError(f"render service unreachable: {exc}") from exc
        if r.status_code != 200:
            raise RenderError(f"render service returned {r.status_code}: {r.text[:300]}")
        data = r.json()
        result = RenderResult(
            errors=[str(e) for e in data.get("errors", [])],
            console=[str(c) for c in data.get("console", [])],
            audit=[str(a) for a in data.get("audit", [])],
        )
        for view, b64 in data.get("images", {}).items():
            path = out_dir / f"{view}.jpg"
            path.write_bytes(base64.b64decode(b64))
            result.images[str(view)] = path
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "render.remote.done",
            extra={
                "views": list(result.images),
                "errors": len(result.errors),
                "duration_ms": result.duration_ms,
                "service_ms": data.get("duration_ms"),
                "gl": data.get("gl"),
            },
        )
        return result

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class RenderClient:
    """What the app renders with: the service when configured, else the local browser."""

    def __init__(self) -> None:
        self.local = Renderer()
        self.remote = RemoteRenderer()

    async def render(
        self,
        scene_url: str,
        views: list[str],
        out_dir: Path,
        quality: str = "high",
        camera: dict[str, float] | None = None,
    ) -> RenderResult:
        s = get_settings()
        if not s.RENDER_SERVICE_URL:
            return await self.local.render(scene_url, views, out_dir, quality, camera)
        try:
            return await self.remote.render(scene_url, views, out_dir, quality, camera)
        except RenderError as exc:
            if not s.RENDER_SERVICE_FALLBACK:
                raise
            logger.warning(
                "render.remote.fallback",
                extra={"url": s.RENDER_SERVICE_URL, "error": str(exc)[:300]},
            )
            return await self.local.render(scene_url, views, out_dir, quality, camera)

    async def close(self) -> None:
        await self.remote.close()
        await self.local.close()
