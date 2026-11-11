"""Client of the listing photo service (deploy/modal_photo.py): start a call, then poll it until it is done."""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Any

import httpx

from housegen.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

POLL_S = 5.0
RETRIES = 5  # transient HTTP failures in a row before a call is given up


class PhotoServiceError(RuntimeError):
    pass


class _TookTooLong(PhotoServiceError):
    pass


class PhotoClient:
    def __init__(
        self, settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        s = settings or get_settings()
        if not s.photo_service_url:
            raise PhotoServiceError("no photo service: set RENDER_SERVICE_URL or PHOTO_SERVICE_URL")
        self._http = httpx.AsyncClient(
            base_url=s.photo_service_url,
            headers={"Authorization": f"Bearer {s.photo_service_token}"},
            timeout=httpx.Timeout(60.0, connect=30.0),
            transport=transport,
        )
        self.poll_s = POLL_S

    async def close(self) -> None:
        await self._http.aclose()

    async def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        for attempt in range(RETRIES):
            try:
                r = await self._http.request(method, path, json=body)
            except httpx.TransportError as exc:
                if attempt == RETRIES - 1:
                    raise PhotoServiceError(f"photo service unreachable: {exc}") from exc
                await asyncio.sleep(2**attempt)
                continue
            if r.status_code >= 500 and attempt < RETRIES - 1:
                await asyncio.sleep(2**attempt)
                continue
            if r.status_code >= 400:
                raise PhotoServiceError(
                    f"photo service {path}: HTTP {r.status_code} {r.text[:300]}"
                )
            data: dict[str, Any] = r.json()
            return data
        raise PhotoServiceError(f"photo service {path}: no answer")

    async def start(self, path: str, body: dict[str, Any]) -> str:
        return str((await self._request("POST", path, body))["call_id"])

    async def wait(self, call_id: str, timeout_s: float) -> dict[str, Any]:
        """The call's result once done; raises PhotoServiceError when it failed or took longer than `timeout_s`.
        A call still running when the job stops or the time is up is cancelled: its GPU stops billing."""
        try:
            return await self._wait(call_id, timeout_s)
        except (asyncio.CancelledError, _TookTooLong):
            await asyncio.shield(self._cancel(call_id))
            raise

    async def _cancel(self, call_id: str) -> None:
        try:
            await self._http.post(f"/calls/{call_id}/cancel", timeout=10.0)
        except Exception as exc:
            logger.warning(
                "listing.cancel_failed", extra={"call_id": call_id, "error": str(exc)[:200]}
            )

    async def _wait(self, call_id: str, timeout_s: float) -> dict[str, Any]:
        t0 = time.monotonic()
        while True:
            res = await self._request("GET", f"/calls/{call_id}")
            if res["status"] == "done":
                out: dict[str, Any] = res["result"]
                return out
            if res["status"] == "failed":
                raise PhotoServiceError(str(res.get("error") or "the photo call failed"))
            if time.monotonic() - t0 > timeout_s:
                raise _TookTooLong(f"the photo call took more than {timeout_s:.0f} s")
            await asyncio.sleep(self.poll_s)

    async def prepare(
        self,
        scene_url: str,
        key: str,
        context_url: str | None = None,
        site: tuple[float, float] | None = None,
        timeout_s: float = 1800.0,
    ) -> dict[str, Any]:
        """The prepared version: its rooms' viewpoints and its exteriors' (#93). `context_url`: the project's
        surroundings (a folder with context.json); `site`: the plot's latitude and longitude, for the sun."""
        body: dict[str, Any] = {"scene_url": scene_url, "key": key}
        if context_url:
            body["context_url"] = context_url
        if site:
            body["site"] = list(site)
        call = await self.start("/prepare", body)
        return await self.wait(call, timeout_s)

    async def shoot(
        self,
        key: str,
        camera: str,
        width: int,
        height: int,
        samples: int,
        timeout_s: float,
        sun: list[float] | None = None,
        room_lamps: bool | None = None,
        remove: list[str] | None = None,
        kind: str | None = None,
    ) -> tuple[bytes, dict[str, Any]]:
        """The photo's JPEG and what the service reports with it (meta, gpu, seconds). `sun` and `room_lamps`
        repeat a preview's light; `remove` hides those dressing pieces; `kind` "exterior" shoots the house from
        outside (#93)."""
        body: dict[str, Any] = {
            "key": key,
            "camera": camera,
            "width": width,
            "height": height,
            "samples": samples,
        }
        if sun is not None:
            body["sun"] = sun
        if room_lamps is not None:
            body["room_lamps"] = room_lamps
        if remove:
            body["remove"] = remove
        if kind:
            body["kind"] = kind
        call = await self.start("/shoot", body)
        res = await self.wait(call, timeout_s)
        return base64.b64decode(res.pop("jpeg")), res
