"""The `listing` job (#91): a saved version's photo set, shot on the photo service: its rooms and, first, the
building from outside (#93); or one room of the set, or its exteriors, shot alone (#142)."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from dataclasses import replace
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
from housegen.limits import service as limits
from housegen.listing.client import PhotoClient
from housegen.listing.pick import pick_exteriors, pick_view
from housegen.listing.shots import (
    EXTERIOR,
    MIN_AREA,
    Shot,
    exterior_shots,
    language,
    listable,
    next_key,
    pick,
    place,
    room_shot,
    score_rule,
    unit_id,
)
from housegen.llm import Completion
from housegen.llm.factory import get_provider
from housegen.projects import crud
from housegen.projects.storage import ProjectStorage
from housegen.render.kits import viewer_kit

logger = logging.getLogger(__name__)

# bump when the export or the Blender import changes what a prepared version holds on the service
PREPARE_REV = "p4"
# how a camera spec frames (#133): its target at the frame's centre, as the 3D tour's photographer frame; a set
# without one was shot with the earlier lens shift ("legacy") and keeps it when shot again, as its previews show
FRAMING = "centred"
# a room shot alone works in its own folder, so the set changes only once its photo is there (#142); the photos
# it replaced wait in PREVIOUS for the panel to bring them back
RESHOOT, PREVIOUS = "reshoot", "previous"
# $ per container second (Modal list prices 2026-11-07): the GPU + 4 cores + 16 GiB; "CPU" is the prepare's
# Blender machine (#95), 8 cores + 32 GiB without a GPU
_CORE_S, _GIB_S = 0.0000131, 0.00000222
_HOST = 4 * _CORE_S + 16 * _GIB_S
PRICE_PER_S = {
    "T4": 0.000164 + _HOST,
    "L4": 0.000222 + _HOST,
    "A10G": 0.000306 + _HOST,
    "L40S": 0.000542 + _HOST,
    "RTX-PRO-6000": 0.000842 + _HOST,
    "CPU": 8 * _CORE_S + 32 * _GIB_S,
}


def listing_dir(storage: ProjectStorage, version: int) -> Path:
    return storage.versions_dir / str(version) / "listing"


def _read_json(p: Path) -> dict[str, Any] | None:
    if not p.exists():
        return None
    data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
    return data


def _write_json(p: Path, doc: dict[str, Any]) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def read_set(storage: ProjectStorage, version: int) -> dict[str, Any] | None:
    return _read_json(listing_dir(storage, version) / "set.json")


def _write_set(storage: ProjectStorage, version: int, doc: dict[str, Any]) -> None:
    _write_json(listing_dir(storage, version) / "set.json", doc)


def _cost(gpu: str, seconds: float) -> float:
    return round(PRICE_PER_S.get(gpu, PRICE_PER_S["RTX-PRO-6000"]) * seconds, 4)


def _prepare_cost(prepared: dict[str, Any]) -> float:
    """The prepare's machines: Chrome's export on the GPU it got, Blender on the CPU machine (#95)."""
    seconds = prepared.get("seconds")
    if prepared.get("cached") or not isinstance(seconds, dict):
        return 0.0
    gpu = str((prepared.get("export") or {}).get("gpu") or "T4")
    return round(
        _cost(gpu, float(seconds.get("export") or 0))
        + _cost("CPU", float(seconds.get("blender") or 0)),
        4,
    )


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


def default_exteriors(st: ProjectStorage) -> str:
    """A set's exteriors when none is asked (#97): the house in its surroundings when the project has them."""
    return "surroundings" if geo_context.load(st.root) else "house"


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


def _status(doc: dict[str, Any]) -> str:
    return "done" if all(x["key"] in doc["photos"] for x in doc["shots"]) else "partial"


def version_rooms(st: ProjectStorage, version: int, doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Every room of the version (its report.json, kept with every save), with what the set's last prepare found
    in it: a set only stores the rooms a prepare scored, and sets from before #142 none."""
    seen = {r["name"]: r for r in doc.get("rooms", [])}
    report = _read_json(st.versions_dir / str(version) / "report.json") or {}
    rooms = listable(list(report.get("rooms") or []))
    if not rooms:
        return list(seen.values())
    # a room no prepare scored yet: whether it has a viewpoint is unknown
    return [{**r, "camera": seen.get(r["name"], {}).get("camera")} for r in rooms]


def _rule(doc: dict[str, Any], room: str, rooms: list[dict[str, Any]]) -> dict[str, float]:
    """The scoring rule a one-room shoot prepares with: the set's, widened to a room it does not take."""
    if any(x["room"] == room for x in doc["shots"]):
        return dict(MIN_AREA)
    found = next((r for r in rooms if r["name"] == room), None)
    return score_rule(found) if found else dict(MIN_AREA)


def _dressed(
    st: ProjectStorage,
    version: int,
    doc: dict[str, Any] | None,
    room: str | None,
    asked: str | None,
) -> dict[str, str]:
    """What the set dresses rooms as (#142), with a one-room shoot's choice: the room's own use dresses it as
    itself again."""
    uses = dict((doc or {}).get("dress_as", {}))
    if doc is None or not room or room == EXTERIOR or not asked:
        return uses
    own = next((r["use"] for r in version_rooms(st, version, doc) if r["name"] == room), None)
    if asked == own:
        uses.pop(room, None)
    else:
        uses[room] = asked
    return uses


def _unit_shots(doc: dict[str, Any], rooms: list[dict[str, Any]], unit: str) -> list[Shot]:
    """What a one-room shoot starts from: nothing for the exteriors (the building's views are chosen again), the
    set's shot of the room with the viewpoints the prepare finds in it now, or a room the set left out."""
    if unit == EXTERIOR:
        return []
    found = next((r for r in rooms if r.get("name") == unit and r.get("camera")), None)
    held = next((Shot(**d) for d in doc["shots"] if d["room"] == unit), None)
    if held and found:
        return [
            replace(
                held, camera=found["camera"], views=list(found.get("views") or [found["camera"]])
            )
        ]
    if held:
        return [held]
    if found is None:
        raise RuntimeError(f"{unit}: the photo service finds no viewpoint in this room")
    return [room_shot(found, next_key([Shot(**d) for d in doc["shots"]]))]


def _replace_unit(
    shots: list[dict[str, Any]], unit: str, new: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The set's shots with the unit's own replaced by `new`, in the unit's place; a room that joins the set
    goes where listing order puts it."""
    at = next((i for i, x in enumerate(shots) if unit_id(x) == unit), None)
    rest = [x for x in shots if unit_id(x) != unit]
    if at is None:
        at = 0 if unit == EXTERIOR or not new else place([Shot(**x) for x in rest], Shot(**new[0]))
    return rest[:at] + new + rest[at:]


def _shown(out: Path, doc: dict[str, Any], unit: str) -> dict[str, dict[str, Any]]:
    """The unit's photos that are on disk, by key."""
    return {
        x["key"]: doc["photos"][x["key"]]
        for x in doc["shots"]
        if unit_id(x) == unit
        and x["key"] in doc["photos"]
        and (out / doc["photos"][x["key"]]["file"]).exists()
    }


def _merge(st: ProjectStorage, version: int, work_doc: dict[str, Any], unit: str) -> dict[str, Any]:
    """A one-room shoot's photos into its set: they take the unit's place, keys and captions, and what the unit
    showed until now waits in PREVIOUS for the panel to bring it back. Returns the shoot's record."""
    out = listing_dir(st, version)
    work = out / RESHOOT
    doc = read_set(st, version)
    if doc is None:
        raise RuntimeError(f"version {version}'s listing set is gone")
    old = [x for x in doc["shots"] if unit_id(x) == unit]
    shown = _shown(out, doc, unit)
    if shown:
        (out / PREVIOUS).mkdir(exist_ok=True)
        for photo in doc.get("previous", {}).get(unit, {}).get("photos", {}).values():
            (out / PREVIOUS / photo["file"]).unlink(missing_ok=True)
        for photo in shown.values():
            (out / photo["file"]).replace(out / PREVIOUS / photo["file"])
        doc.setdefault("previous", {})[unit] = {
            "shots": [x for x in old if x["key"] in shown],
            "photos": shown,
            "choices": {k: doc["choices"][k] for k in shown if k in doc["choices"]},
            "at": datetime.now(UTC).isoformat(),
        }
    for x in old:
        for field in ("photos", "choices", "errors"):
            doc[field].pop(x["key"], None)
        (out / f"{x['key']}.jpg").unlink(missing_ok=True)
    groups = {"E"} if unit == EXTERIOR else {x["key"] for x in old}
    for g in groups | set(work_doc["previews"]):
        doc["previews"].pop(g, None)
        for p in (out / "previews").glob(f"{g}-*.jpg"):
            p.unlink()
    (out / "previews").mkdir(exist_ok=True)
    for g, views in work_doc["previews"].items():
        doc["previews"][g] = views
        for p in (work / "previews").glob(f"{g}-*.jpg"):
            p.replace(out / "previews" / p.name)
    for x in work_doc["shots"]:
        k = x["key"]
        if k in work_doc["photos"] and (work / f"{k}.jpg").exists():
            (work / f"{k}.jpg").replace(out / f"{k}.jpg")
            doc["photos"][k] = work_doc["photos"][k]
        for field in ("choices", "errors"):
            if k in work_doc[field]:
                doc[field][k] = work_doc[field][k]
    doc["shots"] = _replace_unit(doc["shots"], unit, work_doc["shots"])
    if unit == EXTERIOR:
        doc["exteriors"] = work_doc["exteriors"]
    if work_doc.get("rooms"):
        doc["rooms"] = work_doc["rooms"]
    doc["dress_as"] = work_doc.get("dress_as", {})
    for bucket, usd in work_doc["cost"].items():
        doc["cost"][bucket] = round(float(doc["cost"].get(bucket) or 0) + float(usd or 0), 4)
    first = work_doc["shots"][0] if work_doc["shots"] and unit != EXTERIOR else None
    record = {
        "job_id": work_doc["job_id"],
        "room": unit,
        "unit": first["unit"] if first else None,
        "caption": first["caption"] if first else None,
        "joined": not old,
        "at": datetime.now(UTC).isoformat(),
        "photos": len(work_doc["photos"]),
        "seconds": work_doc.get("seconds"),
        "usd": round(sum(float(v or 0) for v in work_doc["cost"].values()), 4),
    }
    doc.setdefault("reshoots", []).append(record)
    doc["status"] = _status(doc)
    _write_set(st, version, doc)
    shutil.rmtree(work, ignore_errors=True)
    return record


def bring_back(st: ProjectStorage, version: int, unit: str) -> bool:
    """Swap a unit's photos with the ones its last one-room shoot replaced (#142): a second call swaps them back.
    False when the set holds no previous photo of the unit."""
    doc = read_set(st, version)
    prev = (doc or {}).get("previous", {}).get(unit)
    out = listing_dir(st, version)
    if not doc or not prev:
        return False
    if not all((out / PREVIOUS / p["file"]).exists() for p in prev["photos"].values()):
        return False
    shown = _shown(out, doc, unit)
    current = {
        "shots": [x for x in doc["shots"] if x["key"] in shown],
        "photos": shown,
        "choices": {k: doc["choices"][k] for k in shown if k in doc["choices"]},
        "at": datetime.now(UTC).isoformat(),
    }
    swap = out / "swap"
    shutil.rmtree(swap, ignore_errors=True)
    swap.mkdir()
    for photo in shown.values():
        (out / photo["file"]).replace(swap / photo["file"])
    for photo in prev["photos"].values():
        (out / PREVIOUS / photo["file"]).replace(out / photo["file"])
    for f in swap.iterdir():
        f.replace(out / PREVIOUS / f.name)
    swap.rmdir()
    for x in doc["shots"]:
        if unit_id(x) == unit:
            for field in ("photos", "choices", "errors"):
                doc[field].pop(x["key"], None)
    doc["shots"] = _replace_unit(doc["shots"], unit, prev["shots"])
    doc["photos"].update(prev["photos"])
    doc["choices"].update(prev["choices"])
    if shown:
        doc["previous"][unit] = current
    else:
        del doc["previous"][unit]
    doc["status"] = _status(doc)
    _write_set(st, version, doc)
    return True


def remove(st: ProjectStorage, version: int, room: str) -> bool:
    """Take a room out of its set (#142): its photo, its previews and the photo a one-room shoot replaced go with it,
    and the panel's list offers it again. False when the set has no photo of the room."""
    doc = read_set(st, version)
    old = [x for x in (doc or {}).get("shots", []) if x["room"] == room and x["use"] != "exterior"]
    if not doc or not old:
        return False
    out = listing_dir(st, version)
    for x in old:
        for field in ("photos", "choices", "errors", "previews"):
            doc.get(field, {}).pop(x["key"], None)
        (out / f"{x['key']}.jpg").unlink(missing_ok=True)
        for p in (out / "previews").glob(f"{x['key']}-*.jpg"):
            p.unlink()
    for photo in doc.get("previous", {}).pop(room, {}).get("photos", {}).values():
        (out / PREVIOUS / photo["file"]).unlink(missing_ok=True)
    doc["shots"] = [x for x in doc["shots"] if x not in old]
    doc["status"] = _status(doc)
    _write_set(st, version, doc)
    return True


def _summary(record: dict[str, Any], version: int) -> str:
    if record["room"] == EXTERIOR:
        return f"The building from outside shot again: {record['photos']} photos in version {version}'s set"
    name = f"{record['unit']} · {record['caption']}" if record.get("unit") else record["caption"]
    if record["joined"]:
        return f"{name} added to version {version}'s listing set"
    return f"{name} shot again in version {version}'s listing set"


async def listing(ctx: JobContext) -> None:
    async with session_factory()() as session:
        job = await crud.get_job(session, ctx.job_id)
    req = json.loads(job.request_text or "{}")
    version = int(req["version"])
    # one unit of the version's set shot alone (#142): a room's name, or EXTERIOR for the building's views
    only = str(req.get("room") or "") or None
    s = get_settings()
    rs = (
        ResolvedRunSettings.model_validate_json(job.settings_json)
        if job.settings_json
        else resolve(s)
    )
    st = ProjectStorage(ctx.project_id)
    out = listing_dir(st, version)
    previous = read_set(st, version)
    if only:
        if previous is None:
            await _record_spend(ctx.job_id, 0.0)
            raise RuntimeError(f"version {version} has no listing set to shoot a room of")
        record = next(
            (r for r in previous.get("reshoots", []) if r.get("job_id") == ctx.job_id), None
        )
        if record:  # resumed after its photos joined the set: nothing is left to do
            await _record_spend(ctx.job_id, float(record.get("usd") or 0))
            await ctx.emit("done", version=version, summary=_summary(record, version), room=only)
            return
        work = out / RESHOOT
        own = _read_json(work / "set.json")
        if own is not None and own.get("job_id") != ctx.job_id:
            own = None
            shutil.rmtree(work, ignore_errors=True)
    else:
        work, own = out, previous
    uses = _dressed(st, version, previous, only, req.get("dress_as"))
    (work / "previews").mkdir(parents=True, exist_ok=True)
    # a room shot alone draws with its set's renderer
    asked_kit = (previous or {}).get("kit") if only else req.get("kit")
    kit = str(asked_kit or viewer_kit(s))
    key = f"{ctx.project_id}-v{version}-{kit}-{PREPARE_REV}"
    resumed = own is not None and own.get("job_id") == ctx.job_id
    reuse = previous is not None and (resumed or bool(req.get("reuse")) or only is not None)
    # a room shot alone keeps its set's staging and exteriors whatever the request says
    asked: dict[str, Any] = {} if only else req
    # a set shot again keeps its staging unless another is asked (#94)
    kept_staging = (previous or {}).get("settings", {}).get("staging") if reuse else None
    staging = str(asked.get("staging") or kept_staging or "furnished")
    # and its exteriors (#97); a set from before the choice was shot with the default
    context_url, site = surroundings(st)
    default = "surroundings" if context_url else "house"
    kept_exterior = (
        ((previous or {}).get("settings", {}).get("exteriors") or default) if reuse else None
    )
    exterior = str(asked.get("exteriors") or kept_exterior or default)
    framing = (
        str((previous or {}).get("settings", {}).get("framing") or "legacy") if reuse else FRAMING
    )
    legacy_shift = framing == "legacy"
    if not s.LISTING_EXTERIORS:
        exterior = "none"
    elif exterior == "surroundings" and not context_url:
        exterior = "house"
    if only == EXTERIOR and exterior == "none":
        await _record_spend(ctx.job_id, 0.0)
        raise RuntimeError("this set has no views from outside to shoot again")
    # a room shot alone needs no exterior build, and its prepare scores it even when it is small
    outside = exterior != "none" and only in (None, EXTERIOR)
    rule = (
        _rule(previous, only, version_rooms(st, version, previous))
        if previous and only and only != EXTERIOR
        else MIN_AREA
    )
    client = PhotoClient(s)
    t0 = time.time()
    doc: dict[str, Any] = {}

    def write() -> None:
        _write_json(work / "set.json", doc)

    try:
        await ctx.emit(
            "phase",
            name="prepare",
            message=f"Exporting version {version} to Blender",
            **({"room": only} if only else {}),
        )
        prepared = await client.prepare(
            scene_page(st, version, kit),
            key,
            context_url if outside else None,
            site if outside else None,
            exteriors=exterior if only in (None, EXTERIOR) else "none",
            score=rule,
        )
        logger.info(
            "listing.prepared",
            extra={
                "job_id": ctx.job_id,
                "cached": bool(prepared.get("cached")),
                "seconds": prepared.get("seconds"),
                "phases": prepared.get("phases"),
                "gpu": (prepared.get("export") or {}).get("gpu"),
                "exteriors": exterior,
                "room": only,
            },
        )
        exteriors = list(prepared.get("exteriors") or []) if outside else []
        if only:
            assert previous is not None
            kept = dict(own) if own else {}
            shots = (
                [Shot(**d) for d in own["shots"]]
                if own
                else _unit_shots(previous, prepared.get("rooms", []), only)
            )
        else:
            kept = dict(previous) if previous and reuse else {}
            if previous and reuse:
                shots = [Shot(**d) for d in previous["shots"]]
                # other exteriors: the building's shots are chosen again
                if kept_exterior != exterior:
                    gone = {x.key for x in shots if x.use == "exterior"}
                    shots = [x for x in shots if x.key not in gone]
                    kept["choices"] = {
                        k: v for k, v in kept.get("choices", {}).items() if k not in gone
                    }
                    kept["previews"] = {
                        k: v for k, v in kept.get("previews", {}).items() if k != "E"
                    }
            else:
                shots = pick(prepared.get("rooms", []))
            if not shots:
                raise RuntimeError(
                    "no room to photograph: the version has no living room, bedroom or bathroom with a camera"
                )
        doc = {
            "version": version,
            "kit": kit,
            "job_id": ctx.job_id,
            "status": "running",
            "created_at": datetime.now(UTC).isoformat(),
            "settings": {
                "width": s.LISTING_WIDTH,
                "height": s.LISTING_HEIGHT,
                "samples": s.LISTING_SAMPLES,
                "staging": staging,
                "exteriors": exterior,
                "framing": framing,
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
            # the rooms a set may photograph, for the ones it leaves out (#142)
            "rooms": listable(prepared.get("rooms", [])),
            "dress_as": uses,
            "previews": dict(kept.get("previews", {})),
            "choices": dict(kept.get("choices", {})),
            "photos": dict(kept.get("photos", {})) if resumed else {},
            "errors": {},
        }
        if only:
            doc["room"] = only
        doc["cost"] = {
            # a resumed job's prepare is cached on the service: the first one's cost stays
            "prepare_usd": _prepare_cost(prepared)
            + (float((own or {}).get("cost", {}).get("prepare_usd") or 0) if resumed else 0.0),
            # a set shot again keeps the earlier job's previews and choices; their cost was that job's
            "previews_usd": _spent(p for v in doc["previews"].values() for p in v.values())
            if resumed
            else 0.0,
            "editor_usd": _spent(doc["choices"].values()) if resumed else 0.0,
            "photos_usd": _spent(doc["photos"].values()),
        }
        if not only and not resumed:
            # the photos one-room shoots replaced belong to the set this one replaces
            for folder in (PREVIOUS, RESHOOT):
                shutil.rmtree(out / folder, ignore_errors=True)
        write()
        plan = {"room": only} if only else {}
        await ctx.emit(
            "listing_plan",
            version=version,
            shots=[
                {"key": x.key, "unit": x.unit, "caption": x.caption, "views": len(x.views)}
                for x in shots
            ],
            **plan,
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
                write()

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
                and (work / "previews" / f"{shot.key}-{i}.jpg").exists()
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
                        staging=staging,
                        legacy_shift=legacy_shift,
                        uses=uses,
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
            await asyncio.to_thread((work / "previews" / f"{shot.key}-{i}.jpg").write_bytes, jpeg)
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
                    [(work / "previews" / f"{shot.key}-{i}.jpg", p["pieces"]) for i, p in shown],
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
            path = work / f"{shot.key}.jpg"
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
                        staging=None if shot.use == "exterior" else staging,
                        exterior=exterior,
                        legacy_shift=legacy_shift,
                        uses=None if shot.use == "exterior" else uses,
                    )
                except Exception as exc:
                    logger.warning(
                        "listing.shot_failed",
                        extra={"job_id": ctx.job_id, "shot": shot.key, "error": str(exc)[:300]},
                    )
                    async with lock:
                        doc["errors"][shot.key] = str(exc)[:500]
                        write()
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
                    # the scene's own decor in the frame against what the photo added (#102)
                    "catalogue": meta.get("catalogue"),
                    "gpu": res.get("gpu"),
                    "seconds": seconds,
                    "usd": usd,
                },
                usd,
                "photos_usd",
            )
            # where the photo is once the job is over: a room shot alone joins its set at the end
            url = f"/scenes/{ctx.project_id}/versions/{version}/listing/{path.name}?t={int(time.time())}"
            await ctx.emit(
                "listing_shot", key=shot.key, caption=shot.caption, url=url, seconds=round(seconds)
            )

        async def preview_outside(i: int, view: dict[str, Any]) -> None:
            path = work / "previews" / f"E-{i}.jpg"
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
                        exterior=exterior,
                        legacy_shift=legacy_shift,
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
                                work / "previews" / f"E-{i}.jpg",
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
                write()
            await ctx.emit(
                "listing_plan",
                version=version,
                shots=[
                    {"key": x.key, "unit": x.unit, "caption": x.caption, "views": len(x.views)}
                    for x in shots
                ],
                **plan,
            )
            await asyncio.gather(*(room(x) for x in new))

        await asyncio.gather(building(), *(room(x) for x in list(shots)))
        done = len(doc["photos"])
        doc["status"] = "done" if done == len(shots) else "partial"
        doc["seconds"] = round(time.time() - t0)
        write()
        if done == 0:
            raise RuntimeError(
                f"no photo rendered: {next(iter(doc['errors'].values()), 'unknown error')}"
            )
        usd = sum(doc["cost"].values())
        summary = f"{done} of {len(shots)} listing photos of version {version}"
        if only:
            summary = _summary(_merge(st, version, doc, only), version)
        logger.info(
            "listing.done",
            extra={
                "job_id": ctx.job_id,
                "room": only,
                "photos": done,
                "of": len(shots),
                "previews": sum(len(v) for v in doc["previews"].values()),
                "seconds": doc["seconds"],
                "usd": round(usd, 2),
            },
        )
        await ctx.emit("done", version=version, summary=summary, **plan)
    except Exception:
        # a failed job does not resume: the set keeps its photo and nothing waits in RESHOOT
        if only:
            shutil.rmtree(work, ignore_errors=True)
        raise
    finally:
        await client.close()
        await _record_spend(ctx.job_id, sum(float(v or 0) for v in doc.get("cost", {}).values()))


async def _record_spend(job_id: str, usd: float) -> None:
    """What the shoot cost, for the organisation's monthly limit (#107), however it ended."""
    try:
        async with session_factory()() as session, session.begin():
            await limits.record_listing_spend(session, job_id, usd)
    except Exception:
        logger.exception("listing.spend_not_recorded", extra={"job_id": job_id, "usd": usd})
