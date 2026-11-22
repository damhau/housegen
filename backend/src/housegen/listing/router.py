"""Listing photo sets (#91): start a shoot of a saved version, read its set, download it."""

from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Query
from fastapi.responses import Response

from housegen.agent import pipeline
from housegen.agent.run_settings import RunSettings, resolve
from housegen.core.config import get_settings
from housegen.core.db import DbSession
from housegen.core.exceptions import ConflictError, InvalidInputError, NotFoundError
from housegen.jobs.manager import job_manager
from housegen.limits import service as limits
from housegen.listing.job import (
    bring_back,
    default_exteriors,
    listing_dir,
    read_set,
    remove,
    version_rooms,
)
from housegen.listing.schemas import (
    ListingOut,
    ListingPhotoOut,
    ListingRequest,
    ListingReshootOut,
    ListingRoomOut,
    ListingStatusOut,
    ListingUnitRequest,
)
from housegen.listing.shots import (
    DRESSABLE,
    EXTERIOR,
    caption,
    house_rooms,
    room_name_parts,
    unit_id,
    unit_of,
)
from housegen.projects import crud
from housegen.projects.schemas import JobOut
from housegen.projects.storage import ProjectStorage
from housegen.render.kits import requested_kit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/projects", tags=["listing"])

_UNSAFE = re.compile(r'[\\/:*?"<>|]+')


def set_versions(st: ProjectStorage) -> list[int]:
    if not st.versions_dir.exists():
        return []
    found = [
        int(d.name)
        for d in st.versions_dir.iterdir()
        if d.name.isdigit() and (d / "listing" / "set.json").exists()
    ]
    return sorted(found, reverse=True)


def _room_out(r: dict[str, Any]) -> ListingRoomOut:
    title = room_name_parts(r["name"])[0].strip()
    return ListingRoomOut(
        room=r["name"],
        unit=unit_of(r["name"]),
        title=title,
        use=r["use"],
        area=r["area"],
        caption=caption(title, float(r["area"])),
        storey=r["storey"],
        in_set=r["in_set"],
        dress_as=r.get("dress_as"),
    )


def _rooms(st: ProjectStorage, version: int, doc: dict[str, Any]) -> list[ListingRoomOut]:
    rooms = house_rooms(version_rooms(st, version, doc), doc.get("shots", []))
    dressed = doc.get("dress_as", {})
    return [_room_out({**r, "dress_as": dressed.get(r["name"])}) for r in rooms]


def listing_out(
    st: ProjectStorage, version: int, current: int, base: str | None = None
) -> ListingOut | None:
    """A version's listing set; its photos' URLs under `base` (a share link's folder) or the
    version's own."""
    doc = read_set(st, version)
    if doc is None:
        return None
    folder = base or st.version_url(version)
    previous = doc.get("previous", {})
    photos = []
    for shot in doc.get("shots", []):
        photo = doc.get("photos", {}).get(shot["key"])
        url = None
        if photo and (listing_dir(st, version) / photo["file"]).exists():
            mtime = int((listing_dir(st, version) / photo["file"]).stat().st_mtime)
            url = f"{folder}/listing/{photo['file']}?t={mtime}"
        photos.append(
            ListingPhotoOut(
                key=shot["key"],
                room=shot["room"],
                unit=shot.get("unit"),
                title=shot["title"],
                caption=shot["caption"],
                use=shot["use"],
                area=shot["area"],
                url=url,
                error=None if url else doc.get("errors", {}).get(shot["key"]),
                why=doc.get("choices", {}).get(shot["key"], {}).get("why"),
                previous=bool(previous.get(unit_id(shot))),
            )
        )
    cost = doc.get("cost", {})
    return ListingOut(
        version=version,
        status=str(doc.get("status", "done")),
        out_of_date=version < current,
        created_at=str(doc.get("created_at", "")),
        width=int(doc.get("settings", {}).get("width", 0)),
        height=int(doc.get("settings", {}).get("height", 0)),
        cost_usd=round(sum(float(v or 0) for v in cost.values()), 2),
        staging=doc.get("settings", {}).get("staging") or "furnished",
        exteriors=doc.get("settings", {}).get("exteriors") or default_exteriors(st),
        previews_done=sum(len(v) for v in doc.get("previews", {}).values()),
        previews_total=sum(
            len(x.get("views") or []) for x in doc.get("shots", []) if len(x.get("views") or []) > 1
        )
        + (len(doc.get("exteriors") or []) if len(doc.get("exteriors") or []) > 1 else 0)
        if doc.get("settings", {}).get("editor")
        else 0,
        photos=photos,
        rooms=_rooms(st, version, doc),
        reshoots=[
            ListingReshootOut(
                room=r["room"],
                unit=r.get("unit"),
                caption=r.get("caption"),
                joined=bool(r.get("joined")),
                at=str(r.get("at", "")),
                photos=int(r.get("photos") or 0),
                usd=round(float(r.get("usd") or 0), 2),
                job_id=str(r.get("job_id", "")),
            )
            for r in doc.get("reshoots", [])
        ],
        dress_uses=list(DRESSABLE),
    )


def _check_unit(st: ProjectStorage, version: int, body: ListingRequest) -> None:
    """A one-room shoot (#142) names a unit of an existing set and keeps the set's settings."""
    doc = read_set(st, version)
    if doc is None:
        raise InvalidInputError("this version has no listing set: shoot the set first")
    settings = doc.get("settings", {})
    exteriors = settings.get("exteriors") or default_exteriors(st)
    if body.reuse:
        raise InvalidInputError("`reuse` shoots the whole set again: leave it out with `room`")
    if (
        body.staging not in (None, settings.get("staging") or "furnished")
        or body.exteriors not in (None, exteriors)
        or body.kit not in (None, doc.get("kit"))
    ):
        raise InvalidInputError("a room shot alone keeps its set's staging, exteriors and renderer")
    if body.room == EXTERIOR:
        if body.dress_as is not None:
            raise InvalidInputError("the views from outside are not dressed")
        if exteriors == "none" or not get_settings().LISTING_EXTERIORS:
            raise InvalidInputError("this set has no views from outside")
        return
    found = next((r for r in _rooms(st, version, doc) if r.room == body.room), None)
    if found is None and not any(x["room"] == body.room for x in doc.get("shots", [])):
        raise InvalidInputError(f"no room named {body.room!r} in this version")
    if body.dress_as not in (None, *DRESSABLE, found.use if found else None):
        raise InvalidInputError(f"a room cannot be dressed as {body.dress_as!r}")


@router.get("/{project_id}/listing")
async def get_listing(
    session: DbSession,
    project_id: str,
    version: int | None = Query(None, description="the version's set (default: the newest set)"),
) -> ListingStatusOut:
    """The project's listing photo sets: which versions have one, and one set in listing order."""
    project = await crud.get_project(session, project_id)
    st = ProjectStorage(project_id)
    sets = set_versions(st)
    chosen = version if version is not None else (sets[0] if sets else None)
    return ListingStatusOut(
        available=bool(get_settings().photo_service_url),
        surroundings=default_exteriors(st) == "surroundings",
        sets=sets,
        listing=listing_out(st, chosen, project.current_version) if chosen is not None else None,
    )


@router.post("/{project_id}/listing", status_code=202)
async def start_listing(
    session: DbSession, project_id: str, body: ListingRequest | None = None
) -> JobOut:
    """Shoot a saved version's interior photo set on the photo service (a few minutes, run beside
    the scene's own jobs). `reuse` keeps the cameras of the version's last set; `room` shoots one
    room of the set, or its views from outside, alone."""
    body = body or ListingRequest()
    project = await crud.get_project(session, project_id)
    if not get_settings().photo_service_url:
        raise ConflictError("no photo service is configured")
    version = body.version or project.current_version
    if not any(v.number == version for v in project.versions):
        raise NotFoundError(f"version {version} not found")
    st = ProjectStorage(project_id)
    if not pipeline.has_interior(st.versions_dir / str(version)):
        raise InvalidInputError("this version has no interior: furnish it first")
    if body.reuse and read_set(st, version) is None:
        raise InvalidInputError("this version has no set to shoot again")
    if body.room is not None:
        _check_unit(st, version, body)
    elif body.dress_as is not None:
        raise InvalidInputError("`dress_as` goes with the `room` it dresses")
    if body.kit:
        requested_kit(body.kit)
    if await crud.active_job(session, project_id, ("listing",)):
        raise ConflictError("a listing set is already being shot")
    request: dict[str, Any] = {
        "version": version,
        "reuse": body.reuse,
        "staging": body.staging,
        "exteriors": body.exteriors,
        "kit": body.kit,
    }
    if body.room is not None:
        request["room"] = body.room
    if body.dress_as is not None:
        request["dress_as"] = body.dress_as
    stored = RunSettings.model_validate(project.settings) if project.settings else RunSettings()
    estimate = get_settings().LISTING_ROOM_ESTIMATE_USD if body.room is not None else None
    async with limits.admission(
        session, project.organisation_id, "listing", estimate=estimate
    ) as admitted:
        job = await crud.create_job(
            session,
            project_id,
            "listing",
            json.dumps(request),
            settings=resolve(get_settings(), stored).model_dump(),
        )
        admitted.record(job)
        await session.commit()
    job_manager.submit(job.id, project_id, pipeline.JOB_BODIES["listing"])
    logger.info(
        "listing.started",
        extra={
            "job_id": job.id,
            "version": version,
            "reuse": body.reuse,
            "staging": body.staging,
            "exteriors": body.exteriors,
            "kit": body.kit,
            "room": body.room,
        },
    )
    return JobOut.model_validate(job)


@router.post("/{project_id}/listing/{version}/previous")
async def bring_back_listing_photo(
    session: DbSession, project_id: str, version: int, body: ListingUnitRequest
) -> ListingOut:
    """Bring back the photo a one-room shoot replaced (#142), or the views from outside it replaced;
    the replaced ones are kept in turn, so a second call swaps them back."""
    project = await crud.get_project(session, project_id)
    if await crud.active_job(session, project_id, ("listing",)):
        raise ConflictError("a listing set is being shot: wait for it to end")
    st = ProjectStorage(project_id)
    if not bring_back(st, version, body.room):
        raise NotFoundError("the set keeps no previous photo of this room")
    logger.info(
        "listing.brought_back",
        extra={"project_id": project_id, "version": version, "room": body.room},
    )
    out = listing_out(st, version, project.current_version)
    assert out is not None
    return out


@router.post("/{project_id}/listing/{version}/remove")
async def remove_listing_room(
    session: DbSession, project_id: str, version: int, body: ListingUnitRequest
) -> ListingOut:
    """Take a room out of the set (#142): its photo and the one it replaced are deleted, and the
    room can be shot again from the list of the house's rooms."""
    project = await crud.get_project(session, project_id)
    if await crud.active_job(session, project_id, ("listing",)):
        raise ConflictError("a listing set is being shot: wait for it to end")
    st = ProjectStorage(project_id)
    if not remove(st, version, body.room):
        raise NotFoundError("the set has no photo of this room")
    logger.info(
        "listing.removed",
        extra={"project_id": project_id, "version": version, "room": body.room},
    )
    out = listing_out(st, version, project.current_version)
    assert out is not None
    return out


@router.get("/{project_id}/listing/{version}/zip")
async def listing_zip(session: DbSession, project_id: str, version: int) -> Response:
    """The version's listing photos in one ZIP, named in listing order with their captions."""
    project = await crud.get_project(session, project_id)
    st = ProjectStorage(project_id)
    out = listing_out(st, version, project.current_version)
    if out is None or not any(p.url for p in out.photos):
        raise NotFoundError("no listing photo for this version")
    buf = io.BytesIO()
    rooms = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for p in out.photos:
            # numbered by place: a room that joined the set later keeps its key wherever it stands (#142)
            rooms += p.use != "exterior"
            if not p.url:
                continue
            number = p.key if p.use == "exterior" else f"{rooms:02d}"
            name = _UNSAFE.sub(
                "-", f"{number} {p.unit + ' - ' if p.unit else ''}{p.caption}"
            ).strip()
            z.write(listing_dir(st, version) / f"{p.key}.jpg", f"{name}.jpg")
    filename = f"{project.name} v{version} listing.zip"
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
