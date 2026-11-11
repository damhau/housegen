"""The `listing` job (#91): a saved version's interior photo set, shot on the photo service."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from housegen.core.config import get_settings
from housegen.core.db import session_factory
from housegen.jobs.manager import JobContext
from housegen.listing.client import PhotoClient
from housegen.listing.shots import Shot, pick
from housegen.projects import crud
from housegen.projects.storage import ProjectStorage
from housegen.render.kits import viewer_kit

logger = logging.getLogger(__name__)

# bump when the export or the Blender import changes what a prepared version holds on the service
PREPARE_REV = "p1"
# $ per container second (Modal list prices 2026-11-07): the GPU + 4 cores + 16 GiB
_HOST = 4 * 0.0000131 + 16 * 0.00000222
PRICE_PER_S = {"T4": 0.000164 + _HOST, "L4": 0.000222 + _HOST, "RTX-PRO-6000": 0.000842 + _HOST}


def listing_dir(storage: ProjectStorage, version: int) -> Path:
    return storage.versions_dir / str(version) / "listing"


def read_set(storage: ProjectStorage, version: int) -> dict[str, Any] | None:
    p = listing_dir(storage, version) / "set.json"
    if not p.exists():
        return None
    data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
    return data


def _write_set(storage: ProjectStorage, version: int, doc: dict[str, Any]) -> None:
    p = listing_dir(storage, version) / "set.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def _cost(gpu: str, seconds: float) -> float:
    return round(PRICE_PER_S.get(gpu, PRICE_PER_S["RTX-PRO-6000"]) * seconds, 4)


def _seconds(value: Any) -> float:
    if isinstance(value, dict):
        return float(sum(float(v) for v in value.values()))
    return float(value or 0)


def scene_page(storage: ProjectStorage, version: int, kit: str) -> str:
    """The version's scene page as the photo service loads it: headless, the viewer's renderer."""
    base = get_settings().render_base_url.rstrip("/")
    return f"{base}{storage.scene_url(version)}?headless=1&quality=medium&kit={kit}"


async def listing(ctx: JobContext) -> None:
    async with session_factory()() as session:
        job = await crud.get_job(session, ctx.job_id)
    req = json.loads(job.request_text or "{}")
    version = int(req["version"])
    s = get_settings()
    st = ProjectStorage(ctx.project_id)
    out = listing_dir(st, version)
    out.mkdir(parents=True, exist_ok=True)
    kit = viewer_kit(s)
    key = f"{ctx.project_id}-v{version}-{kit}-{PREPARE_REV}"
    previous = read_set(st, version)
    resumed = previous is not None and previous.get("job_id") == ctx.job_id
    client = PhotoClient(s)
    t0 = time.time()
    try:
        await ctx.emit("phase", name="prepare", message=f"Exporting version {version} to Blender")
        prepared = await client.prepare(scene_page(st, version, kit), key)
        prepare_s = _seconds(prepared.get("seconds"))
        if previous and (resumed or req.get("reuse")):
            shots = [Shot(**d) for d in previous["shots"]]
        else:
            shots = pick(prepared.get("rooms", []))
        if not shots:
            raise RuntimeError(
                "no room to photograph: the version has no living room, bedroom or bathroom with a camera"
            )
        doc: dict[str, Any] = {
            "version": version,
            "kit": kit,
            "job_id": ctx.job_id,
            "status": "running",
            "created_at": datetime.now(UTC).isoformat(),
            "settings": {
                "width": s.LISTING_WIDTH,
                "height": s.LISTING_HEIGHT,
                "samples": s.LISTING_SAMPLES,
            },
            "shots": [x.as_dict() for x in shots],
            "photos": dict(previous.get("photos", {})) if resumed and previous else {},
            "errors": {},
        }
        doc["cost"] = {
            "prepare_usd": _cost("T4", 0 if prepared.get("cached") else prepare_s),
            "photos_usd": round(sum(float(p.get("usd") or 0) for p in doc["photos"].values()), 4),
        }
        _write_set(st, version, doc)
        await ctx.emit(
            "listing_plan",
            version=version,
            shots=[{"key": x.key, "unit": x.unit, "caption": x.caption} for x in shots],
        )
        sem = asyncio.Semaphore(s.LISTING_PARALLEL)
        lock = asyncio.Lock()

        async def one(shot: Shot) -> None:
            path = out / f"{shot.key}.jpg"
            if shot.key in doc["photos"] and path.exists():
                return
            label = f"{shot.unit} · {shot.title}" if shot.unit else shot.title
            async with sem:
                ctx.emit_transient("listing_progress", key=shot.key, message=f"{label}: rendering")
                try:
                    jpeg, res = await client.shoot(
                        key,
                        shot.camera,
                        s.LISTING_WIDTH,
                        s.LISTING_HEIGHT,
                        s.LISTING_SAMPLES,
                        s.LISTING_SHOT_TIMEOUT_S,
                    )
                except Exception as exc:
                    logger.warning(
                        "listing.shot_failed",
                        extra={"job_id": ctx.job_id, "shot": shot.key, "error": str(exc)[:300]},
                    )
                    async with lock:
                        doc["errors"][shot.key] = str(exc)[:500]
                        _write_set(st, version, doc)
                    await ctx.emit(
                        "listing_shot", key=shot.key, caption=shot.caption, error=str(exc)[:300]
                    )
                    return
            await asyncio.to_thread(path.write_bytes, jpeg)
            meta = res.get("meta") or {}
            seconds = float(res.get("seconds") or 0)
            usd = _cost(str(res.get("gpu") or ""), seconds)
            async with lock:
                doc["photos"][shot.key] = {
                    "file": path.name,
                    "camera": meta.get("camera"),
                    "sun": meta.get("sun"),
                    "lamps": meta.get("lamps"),
                    "gpu": res.get("gpu"),
                    "seconds": seconds,
                    "usd": usd,
                }
                doc["cost"]["photos_usd"] = round(doc["cost"]["photos_usd"] + usd, 4)
                doc["errors"].pop(shot.key, None)
                _write_set(st, version, doc)
            url = f"/scenes/{ctx.project_id}/versions/{version}/listing/{path.name}?t={int(time.time())}"
            await ctx.emit(
                "listing_shot", key=shot.key, caption=shot.caption, url=url, seconds=round(seconds)
            )

        await asyncio.gather(*(one(x) for x in shots))
        done = len(doc["photos"])
        doc["status"] = "done" if done == len(shots) else "partial"
        doc["seconds"] = round(time.time() - t0)
        _write_set(st, version, doc)
        if done == 0:
            raise RuntimeError(
                f"no photo rendered: {next(iter(doc['errors'].values()), 'unknown error')}"
            )
        usd = doc["cost"]["prepare_usd"] + doc["cost"]["photos_usd"]
        logger.info(
            "listing.done",
            extra={"job_id": ctx.job_id, "photos": done, "of": len(shots), "usd": round(usd, 2)},
        )
        await ctx.emit(
            "done",
            version=version,
            summary=f"{done} of {len(shots)} listing photos of version {version}",
        )
    finally:
        await client.close()
