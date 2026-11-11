"""The `listing` job (#91): a saved version's photo set, shot on the photo service: its rooms and, first, the
building from outside (#93)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from housegen.agent.metrics import TurnMetric
from housegen.agent.run_settings import ResolvedRunSettings, resolve
from housegen.core.config import get_settings
from housegen.core.db import session_factory
from housegen.geo import context as geo_context
from housegen.geo.swiss import lv95_to_wgs84
from housegen.jobs.manager import JobContext
from housegen.listing.client import PhotoClient
from housegen.listing.pick import pick_exteriors, pick_view
from housegen.listing.shots import Shot, exterior_shots, language, pick
from housegen.llm import Completion
from housegen.llm.factory import get_provider
from housegen.projects import crud
from housegen.projects.storage import ProjectStorage
from housegen.render.kits import viewer_kit

logger = logging.getLogger(__name__)

# bump when the export or the Blender import changes what a prepared version holds on the service
PREPARE_REV = "p3"
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


def surroundings(st: ProjectStorage) -> tuple[str | None, tuple[float, float] | None]:
    """The project's context folder as the photo service fetches it, and the plot's latitude and longitude;
    (None, None) for a project without surroundings."""
    meta = geo_context.load(st.root)
    if not meta:
        return None, None
    base = get_settings().render_base_url.rstrip("/")
    place = meta.get("place") or {}
    site = None
    if place.get("e") and place.get("n"):
        lon, lat = lv95_to_wgs84(float(place["e"]), float(place["n"]))
        site = (round(lat, 5), round(lon, 5))
    return f"{base}/scenes/{st.project_id}/context/", site


def _spent(records: Any) -> float:
    return round(sum(float(r.get("usd") or 0) for r in records), 4)


def _by_score(shot: Shot, previews: dict[str, Any]) -> dict[str, Any]:
    """The best-scored viewpoint that previewed, with the preview's light; the shot's camera when none did."""
    for i, cam in enumerate(shot.views or [shot.camera], 1):
        p = previews.get(str(i))
        if p:
            return {
                "view": i,
                "camera": cam,
                "sun": p.get("sun"),
                "room_lamps": p.get("room_lamps"),
                "by": "score",
            }
    return {"view": 1, "camera": shot.camera, "sun": None, "room_lamps": None, "by": "score"}


async def listing(ctx: JobContext) -> None:
    async with session_factory()() as session:
        job = await crud.get_job(session, ctx.job_id)
    req = json.loads(job.request_text or "{}")
    version = int(req["version"])
    s = get_settings()
    rs = (
        ResolvedRunSettings.model_validate_json(job.settings_json)
        if job.settings_json
        else resolve(s)
    )
    st = ProjectStorage(ctx.project_id)
    out = listing_dir(st, version)
    (out / "previews").mkdir(parents=True, exist_ok=True)
    kit = viewer_kit(s)
    key = f"{ctx.project_id}-v{version}-{kit}-{PREPARE_REV}"
    previous = read_set(st, version)
    resumed = previous is not None and previous.get("job_id") == ctx.job_id
    reuse = previous is not None and (resumed or bool(req.get("reuse")))
    client = PhotoClient(s)
    t0 = time.time()
    try:
        await ctx.emit("phase", name="prepare", message=f"Exporting version {version} to Blender")
        context_url, site = surroundings(st) if s.LISTING_EXTERIORS else (None, None)
        prepared = await client.prepare(scene_page(st, version, kit), key, context_url, site)
        prepare_s = _seconds(prepared.get("seconds"))
        exteriors = list(prepared.get("exteriors") or []) if s.LISTING_EXTERIORS else []
        if previous and reuse:
            shots = [Shot(**d) for d in previous["shots"]]
        else:
            shots = pick(prepared.get("rooms", []))
        if not shots:
            raise RuntimeError(
                "no room to photograph: the version has no living room, bedroom or bathroom with a camera"
            )
        kept = previous if previous and reuse else {}
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
                "preview": [
                    s.LISTING_PREVIEW_WIDTH,
                    s.LISTING_PREVIEW_HEIGHT,
                    s.LISTING_PREVIEW_SAMPLES,
                ],
                "editor": f"{rs.provider}/{rs.critic_model}/{rs.critic_effort}"
                if s.LISTING_PICK
                else None,
            },
            "shots": [x.as_dict() for x in shots],
            "exteriors": exteriors,
            "previews": dict(kept.get("previews", {})),
            "choices": dict(kept.get("choices", {})),
            "photos": dict(kept.get("photos", {})) if resumed else {},
            "errors": {},
        }
        doc["cost"] = {
            "prepare_usd": _cost("T4", 0 if prepared.get("cached") else prepare_s),
            # a set shot again keeps the earlier job's previews and choices; their cost was that job's
            "previews_usd": _spent(p for v in doc["previews"].values() for p in v.values())
            if resumed
            else 0.0,
            "editor_usd": _spent(doc["choices"].values()) if resumed else 0.0,
            "photos_usd": _spent(doc["photos"].values()),
        }
        _write_set(st, version, doc)
        await ctx.emit(
            "listing_plan",
            version=version,
            shots=[
                {"key": x.key, "unit": x.unit, "caption": x.caption, "views": len(x.views)}
                for x in shots
            ],
        )
        sem = asyncio.Semaphore(s.LISTING_PARALLEL)
        lock = asyncio.Lock()

        async def save(field: str, key_: str, value: Any, usd: float = 0.0, cost: str = "") -> None:
            async with lock:
                if field == "previews":
                    shot_key, view = key_.split("/")
                    doc["previews"].setdefault(shot_key, {})[view] = value
                else:
                    doc[field][key_] = value
                if cost:
                    doc["cost"][cost] = round(doc["cost"][cost] + usd, 4)
                _write_set(st, version, doc)

        def label(shot: Shot) -> str:
            return f"{shot.unit} · {shot.title}" if shot.unit else shot.title

        def editor_cost(completion: Completion) -> float:
            u = completion.usage
            usd = TurnMetric(
                step=0,
                role="critic",
                model=completion.model or rs.critic_model,
                input_tokens=u.input_tokens,
                cached_tokens=u.cache_read_tokens,
                cache_write_tokens=u.cache_write_tokens,
                output_tokens=u.output_tokens,
            ).cost_usd(s)
            return round(usd or 0.0, 4)

        async def preview(shot: Shot, i: int, camera: str) -> None:
            if (
                str(i) in doc["previews"].get(shot.key, {})
                and (out / "previews" / f"{shot.key}-{i}.jpg").exists()
            ):
                return
            async with sem:
                ctx.emit_transient(
                    "listing_progress",
                    key=shot.key,
                    message=f"{label(shot)}: viewpoint {i} of {len(shot.views)}",
                )
                try:
                    jpeg, res = await client.shoot(
                        key,
                        camera,
                        s.LISTING_PREVIEW_WIDTH,
                        s.LISTING_PREVIEW_HEIGHT,
                        s.LISTING_PREVIEW_SAMPLES,
                        s.LISTING_SHOT_TIMEOUT_S,
                    )
                except Exception as exc:
                    logger.warning(
                        "listing.preview_failed",
                        extra={
                            "job_id": ctx.job_id,
                            "shot": shot.key,
                            "view": i,
                            "error": str(exc)[:300],
                        },
                    )
                    return
            await asyncio.to_thread((out / "previews" / f"{shot.key}-{i}.jpg").write_bytes, jpeg)
            meta = res.get("meta") or {}
            seconds = float(res.get("seconds") or 0)
            usd = _cost(str(res.get("gpu") or ""), seconds)
            await save(
                "previews",
                f"{shot.key}/{i}",
                {
                    "camera": camera,
                    "sun": meta.get("sun_deg"),
                    "room_lamps": meta.get("room_lamps"),
                    "pieces": meta.get("pieces") or [],
                    "seconds": seconds,
                    "usd": usd,
                },
                usd,
                "previews_usd",
            )

        async def choose(shot: Shot) -> dict[str, Any]:
            views = shot.views or [shot.camera]
            if not s.LISTING_PICK or len(views) < 2:
                return _by_score(shot, {})
            await asyncio.gather(*(preview(shot, i, cam) for i, cam in enumerate(views, 1)))
            done = doc["previews"].get(shot.key, {})
            shown = [(i, done[str(i)]) for i in range(1, len(views) + 1) if str(i) in done]
            if len(shown) < 2:
                return _by_score(shot, done)
            ctx.emit_transient(
                "listing_progress", key=shot.key, message=f"{label(shot)}: choosing the viewpoint"
            )
            try:
                choice, completion = await pick_view(
                    get_provider(rs.provider),
                    rs.critic_model,
                    shot.room,
                    shot.use,
                    [(out / "previews" / f"{shot.key}-{i}.jpg", p["pieces"]) for i, p in shown],
                    rs.max_tokens,
                    rs.critic_effort,
                )
            except Exception as exc:
                logger.warning(
                    "listing.pick_failed",
                    extra={"job_id": ctx.job_id, "shot": shot.key, "error": str(exc)[:300]},
                )
                return {**_by_score(shot, done), "error": str(exc)[:300]}
            i, p = shown[choice.best - 1]
            return {
                "view": i,
                "camera": p["camera"],
                "sun": p.get("sun"),
                "room_lamps": p.get("room_lamps"),
                "remove": choice.remove,
                "by": "editor",
                "why": choice.why,
                "problem": choice.problem,
                "usd": editor_cost(completion),
            }

        async def room(shot: Shot) -> None:
            choice = doc["choices"].get(shot.key)
            if choice is None:
                choice = await choose(shot)
                await save("choices", shot.key, choice, float(choice.get("usd") or 0), "editor_usd")
            path = out / f"{shot.key}.jpg"
            if shot.key in doc["photos"] and path.exists():
                return
            async with sem:
                ctx.emit_transient(
                    "listing_progress", key=shot.key, message=f"{label(shot)}: rendering"
                )
                try:
                    jpeg, res = await client.shoot(
                        key,
                        choice["camera"],
                        s.LISTING_WIDTH,
                        s.LISTING_HEIGHT,
                        s.LISTING_SAMPLES,
                        s.LISTING_SHOT_TIMEOUT_S,
                        sun=choice.get("sun"),
                        room_lamps=choice.get("room_lamps"),
                        remove=[choice["remove"]] if choice.get("remove") else [],
                        kind="exterior" if shot.use == "exterior" else None,
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
                doc["errors"].pop(shot.key, None)
            await save(
                "photos",
                shot.key,
                {
                    "file": path.name,
                    "camera": meta.get("camera"),
                    "sun": meta.get("sun"),
                    "lamps": meta.get("lamps"),
                    "removed": meta.get("removed") or [],
                    "gpu": res.get("gpu"),
                    "seconds": seconds,
                    "usd": usd,
                },
                usd,
                "photos_usd",
            )
            url = f"/scenes/{ctx.project_id}/versions/{version}/listing/{path.name}?t={int(time.time())}"
            await ctx.emit(
                "listing_shot", key=shot.key, caption=shot.caption, url=url, seconds=round(seconds)
            )

        async def preview_outside(i: int, view: dict[str, Any]) -> None:
            path = out / "previews" / f"E-{i}.jpg"
            if str(i) in doc["previews"].get("E", {}) and path.exists():
                return
            async with sem:
                ctx.emit_transient(
                    "listing_progress",
                    key="E",
                    message=f"The building from outside: viewpoint {i} of {len(exteriors)}",
                )
                try:
                    jpeg, res = await client.shoot(
                        key,
                        view["camera"],
                        s.LISTING_PREVIEW_WIDTH,
                        s.LISTING_PREVIEW_HEIGHT,
                        s.LISTING_PREVIEW_SAMPLES,
                        s.LISTING_SHOT_TIMEOUT_S,
                        sun=view.get("sun"),
                        kind="exterior",
                    )
                except Exception as exc:
                    logger.warning(
                        "listing.preview_failed",
                        extra={
                            "job_id": ctx.job_id,
                            "shot": "E",
                            "view": i,
                            "error": str(exc)[:300],
                        },
                    )
                    return
            await asyncio.to_thread(path.write_bytes, jpeg)
            seconds = float(res.get("seconds") or 0)
            usd = _cost(str(res.get("gpu") or ""), seconds)
            await save(
                "previews",
                f"E/{i}",
                {"camera": view["camera"], "sun": view.get("sun"), "seconds": seconds, "usd": usd},
                usd,
                "previews_usd",
            )

        async def building() -> None:
            """The exterior views that lead the set: each scored viewpoint previewed, the editor keeps 2 to
            LISTING_EXTERIOR_MAX in listing order (the best-scored three without an editor)."""
            if not exteriors or any(x.use == "exterior" for x in shots):
                return
            if s.LISTING_PICK and len(exteriors) > 1:
                await asyncio.gather(*(preview_outside(i, v) for i, v in enumerate(exteriors, 1)))
            done = doc["previews"].get("E", {})
            shown = [i for i in range(1, len(exteriors) + 1) if str(i) in done]
            keep, why, by, usd = list(range(1, min(3, len(exteriors)) + 1)), None, "score", 0.0
            if len(shown) >= 2:
                ctx.emit_transient(
                    "listing_progress", key="E", message="The building from outside: choosing"
                )
                try:
                    choice, completion = await pick_exteriors(
                        get_provider(rs.provider),
                        rs.critic_model,
                        [
                            (
                                out / "previews" / f"E-{i}.jpg",
                                str(exteriors[i - 1].get("side") or ""),
                            )
                            for i in shown
                        ],
                        2,
                        s.LISTING_EXTERIOR_MAX,
                        rs.max_tokens,
                        rs.critic_effort,
                    )
                    keep = [shown[k - 1] for k in choice.keep]
                    why, by, usd = choice.why, "editor", editor_cost(completion)
                except Exception as exc:
                    logger.warning(
                        "listing.pick_failed",
                        extra={"job_id": ctx.job_id, "shot": "E", "error": str(exc)[:300]},
                    )
            names = [str(r.get("name") or "") for r in prepared.get("rooms", [])]
            new = exterior_shots(exteriors, keep, language(names))
            async with lock:
                shots[:0] = new
                doc["shots"][:0] = [x.as_dict() for x in new]
                for n, (x, i) in enumerate(zip(new, keep, strict=True)):
                    doc["choices"][x.key] = {
                        "view": i,
                        "camera": x.camera,
                        "sun": exteriors[i - 1].get("sun"),
                        "room_lamps": None,
                        "by": by,
                        "why": why if n == 0 else None,
                        "usd": usd if n == 0 else 0.0,
                    }
                doc["cost"]["editor_usd"] = round(doc["cost"]["editor_usd"] + usd, 4)
                _write_set(st, version, doc)
            await ctx.emit(
                "listing_plan",
                version=version,
                shots=[
                    {"key": x.key, "unit": x.unit, "caption": x.caption, "views": len(x.views)}
                    for x in shots
                ],
            )
            await asyncio.gather(*(room(x) for x in new))

        await asyncio.gather(building(), *(room(x) for x in list(shots)))
        done = len(doc["photos"])
        doc["status"] = "done" if done == len(shots) else "partial"
        doc["seconds"] = round(time.time() - t0)
        _write_set(st, version, doc)
        if done == 0:
            raise RuntimeError(
                f"no photo rendered: {next(iter(doc['errors'].values()), 'unknown error')}"
            )
        usd = sum(doc["cost"].values())
        logger.info(
            "listing.done",
            extra={
                "job_id": ctx.job_id,
                "photos": done,
                "of": len(shots),
                "previews": sum(len(v) for v in doc["previews"].values()),
                "seconds": doc["seconds"],
                "usd": round(usd, 2),
            },
        )
        await ctx.emit(
            "done",
            version=version,
            summary=f"{done} of {len(shots)} listing photos of version {version}",
        )
    finally:
        await client.close()
