"""Headless renderer: loads a scene page in Chromium, switches named views and screenshots them."""

from __future__ import annotations

import asyncio
import io
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image
from playwright.async_api import Browser, Playwright, async_playwright

from housegen.core.config import get_settings
from housegen.core.exceptions import RenderError

logger = logging.getLogger(__name__)


@dataclass
class RenderResult:
    images: dict[str, Path] = field(default_factory=dict)  # view -> jpeg path
    errors: list[str] = field(default_factory=list)
    console: list[str] = field(default_factory=list)
    # deterministic plausibility findings from the runtime (window.__house.audit, #3)
    audit: list[str] = field(default_factory=list)
    duration_ms: int = 0


class Renderer:
    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()

    async def _ensure_browser(self) -> Browser:
        if self._browser is not None and self._browser.is_connected():
            return self._browser
        s = get_settings()
        if self._pw is None:
            self._pw = await async_playwright().start()
        args = [
            # software WebGL: no GPU in a server / container
            "--use-gl=angle",
            "--use-angle=swiftshader",
            "--enable-unsafe-swiftshader",
            "--ignore-gpu-blocklist",
            # containers: /dev/shm is tiny and Chrome's own sandbox needs privileges a pod lacks
            "--disable-dev-shm-usage",
            "--no-sandbox",
        ]
        channel = s.BROWSER_CHANNEL or None
        try:
            self._browser = await self._pw.chromium.launch(
                channel=channel, headless=True, args=args
            )
        except Exception as exc:  # channel missing → bundled chromium
            logger.warning(
                "render.browser.channel_failed",
                extra={"channel": s.BROWSER_CHANNEL, "error": str(exc)[:200]},
            )
            self._browser = await self._pw.chromium.launch(headless=True, args=args)
        logger.info("render.browser.started", extra={"channel": s.BROWSER_CHANNEL})
        return self._browser

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    async def render(
        self,
        scene_url: str,
        views: list[str],
        out_dir: Path,
        quality: str = "high",
        camera: dict[str, float] | None = None,
    ) -> RenderResult:
        """Render `views` of the scene served at `scene_url` (absolute) into out_dir/<view>.jpg.

        `camera` holds optional overrides applied to every side view of this render
        (eye_height, distance, azimuth, fov, target_height; see kit/runtime.js).
        """
        s = get_settings()
        out_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 — tiny local mkdir
        result = RenderResult()
        started = time.perf_counter()
        async with self._lock:
            browser = await self._ensure_browser()
            context = await browser.new_context(
                viewport={"width": s.RENDER_WIDTH, "height": s.RENDER_HEIGHT}, device_scale_factor=1
            )
            page = await context.new_page()
            page.on(
                "console",
                lambda m: (
                    result.console.append(f"[{m.type}] {m.text}")
                    if m.type in ("error", "warning")
                    else None
                ),
            )
            page.on("pageerror", lambda e: result.errors.append(f"pageerror: {e}"))
            sep = "&" if "?" in scene_url else "?"
            url = f"{scene_url}{sep}headless=1&quality={quality}&view={views[0] if views else 'southeast'}&w={s.RENDER_WIDTH}&h={s.RENDER_HEIGHT}&t={int(time.time())}"
            for k, v in (camera or {}).items():
                url += f"&{k}={v:g}"
            logger.info("render.start", extra={"url": scene_url, "views": views})
            try:
                await page.goto(url, wait_until="load", timeout=s.RENDER_TIMEOUT_MS)
                try:
                    await page.wait_for_function(
                        "() => window.__house && window.__house.ready", timeout=s.RENDER_TIMEOUT_MS
                    )
                except Exception:
                    js_errors = await page.evaluate(
                        "() => (window.__house && window.__house.errors) || []"
                    )
                    result.errors.append(
                        "scene did not become ready (buildScene never resolved or crashed)"
                    )
                    result.errors.extend(str(e) for e in js_errors)
                    return result
                js_errors = await page.evaluate("() => window.__house.errors")
                result.errors.extend(str(e) for e in js_errors)
                try:
                    found = await page.evaluate(
                        "() => (window.__house.audit ? window.__house.audit() : [])"
                    )
                    result.audit = [str(x) for x in found][:20]
                except Exception as exc:  # the audit must never break a render
                    logger.warning("render.audit_failed", extra={"error": str(exc)[:200]})
                for view in views:
                    ok = await page.evaluate("(v) => window.__house.setView(v)", view)
                    if not ok:
                        continue
                    await page.wait_for_timeout(80)
                    png = await page.screenshot(type="png")
                    result.images[view] = _to_jpeg(
                        png, out_dir / f"{view}.jpg", s.RENDER_IMAGE_WIDTH, s.RENDER_JPEG_QUALITY
                    )
                js_errors = await page.evaluate("() => window.__house.errors")
                for e in js_errors:
                    if str(e) not in result.errors:
                        result.errors.append(str(e))
            except Exception as exc:
                logger.exception("render.failed", extra={"url": scene_url})
                raise RenderError(f"render failed: {exc}") from exc
            finally:
                await context.close()
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "render.done",
            extra={
                "views": list(result.images),
                "errors": len(result.errors),
                "audit": len(result.audit),
                "duration_ms": result.duration_ms,
            },
        )
        return result


def _to_jpeg(png: bytes, path: Path, width: int, quality: int) -> Path:
    img = Image.open(io.BytesIO(png)).convert("RGB")
    if img.width > width:
        img = img.resize((width, int(img.height * width / img.width)), Image.Resampling.LANCZOS)
    img.save(path, "JPEG", quality=quality, optimize=True)
    return path


renderer = Renderer()
