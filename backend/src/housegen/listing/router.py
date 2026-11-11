"""Listing photo sets (#91): start a shoot of a saved version, read its set, download it."""

from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from urllib.parse import quote

from fastapi import APIRouter, Query
from fastapi.responses import Response

from housegen.agent import pipeline
from housegen.core.config import get_settings
from housegen.core.db import DbSession
from housegen.core.exceptions import ConflictError, InvalidInputError, NotFoundError
from housegen.jobs.manager import job_manager
from housegen.listing.job import listing_dir, read_set
from housegen.listing.schemas import ListingOut, ListingPhotoOut, ListingRequest, ListingStatusOut
from housegen.projects import crud
from housegen.projects.schemas import JobOut
from housegen.projects.storage import ProjectStorage

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


def listing_out(st: ProjectStorage, version: int, current: int) -> ListingOut | None:
    doc = read_set(st, version)
    if doc is None:
        return None
    photos = []
    for shot in doc.get("shots", []):
        photo = doc.get("photos", {}).get(shot["key"])
        url = None
        if photo and (listing_dir(st, version) / photo["file"]).exists():
            mtime = int((listing_dir(st, version) / photo["file"]).stat().st_mtime)
            url = f"/scenes/{st.project_id}/versions/{version}/listing/{photo['file']}?t={mtime}"
        photos.append(
            ListingPhotoOut(
                key=shot["key"],
                unit=shot.get("unit"),
                title=shot["title"],
                caption=shot["caption"],
                use=shot["use"],
                area=shot["area"],
                url=url,
                error=None if url else doc.get("errors", {}).get(shot["key"]),
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
        cost_usd=round(float(cost.get("prepare_usd", 0)) + float(cost.get("photos_usd", 0)), 2),
        photos=photos,
    )


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
        sets=sets,
        listing=listing_out(st, chosen, project.current_version) if chosen is not None else None,
    )


@router.post("/{project_id}/listing", status_code=202)
async def start_listing(
    session: DbSession, project_id: str, body: ListingRequest | None = None
) -> JobOut:
    """Shoot a saved version's interior photo set on the photo service (a few minutes, run beside
    the scene's own jobs). `reuse` keeps the cameras of the version's last set."""
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
    if await crud.active_job(session, project_id, ("listing",)):
        raise ConflictError("a listing set is already being shot")
    request = json.dumps({"version": version, "reuse": body.reuse})
    job = await crud.create_job(session, project_id, "listing", request)
    await session.commit()
    job_manager.submit(job.id, project_id, pipeline.JOB_BODIES["listing"])
    logger.info(
        "listing.started", extra={"job_id": job.id, "version": version, "reuse": body.reuse}
    )
    return JobOut.model_validate(job)


@router.get("/{project_id}/listing/{version}/zip")
async def listing_zip(session: DbSession, project_id: str, version: int) -> Response:
    """The version's listing photos in one ZIP, named in listing order with their captions."""
    project = await crud.get_project(session, project_id)
    st = ProjectStorage(project_id)
    out = listing_out(st, version, project.current_version)
    if out is None or not any(p.url for p in out.photos):
        raise NotFoundError("no listing photo for this version")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for p in out.photos:
            if not p.url:
                continue
            name = _UNSAFE.sub(
                "-", f"{p.key} {p.unit + ' - ' if p.unit else ''}{p.caption}"
            ).strip()
            z.write(listing_dir(st, version) / f"{p.key}.jpg", f"{name}.jpg")
    filename = f"{project.name} v{version} listing.zip"
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
