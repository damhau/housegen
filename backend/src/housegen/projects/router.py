from __future__ import annotations

import io
import json
import logging
import shutil
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

import anyio
from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from PIL import Image, ImageOps
from sse_starlette.sse import EventSourceResponse

from housegen.agent import pipeline
from housegen.agent.schemas import Critique
from housegen.core.db import DbSession
from housegen.core.exceptions import ConflictError, InvalidInputError, NotFoundError
from housegen.jobs.manager import job_manager
from housegen.projects import crud
from housegen.projects.models import Job, Project
from housegen.projects.schemas import (
    ChatMessageOut,
    JobEventOut,
    JobOut,
    ModifyRequest,
    PhotoOut,
    ProjectOut,
    ProjectSummaryOut,
    SceneFileOut,
    SceneFilesOut,
    SceneVersionOut,
)
from housegen.projects.storage import ProjectStorage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/projects", tags=["projects"])

SIDES = {"north", "south", "east", "west", "other"}
MAX_PHOTO_PX = 1600  # phone photos are 4000 px; the model never needs more than this


def _store_photo(raw: bytes, path: Path) -> None:
    """Normalise an upload: apply EXIF rotation, cap the long side, save as JPEG."""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
    assert img is not None
    img = img.convert("RGB")
    img.thumbnail((MAX_PHOTO_PX, MAX_PHOTO_PX), Image.Resampling.LANCZOS)
    img.save(path, "JPEG", quality=88, optimize=True)


def _project_out(project: Project) -> ProjectOut:
    st = ProjectStorage(project.id)
    return ProjectOut(
        id=project.id,
        name=project.name,
        status=project.status,
        created_at=project.created_at,
        plan_pages=project.plan_pages,
        current_version=project.current_version,
        photos=[
            PhotoOut(
                id=p.id, side=p.side, original_name=p.original_name, url=st.photo_url(p.filename)
            )
            for p in project.photos
        ],
        versions=[
            SceneVersionOut(
                id=v.id,
                number=v.number,
                kind=v.kind,
                label=v.label,
                summary=v.summary,
                critic_score=v.critic_score,
                critique=Critique.model_validate_json(v.critique_json) if v.critique_json else None,
                created_at=v.created_at,
                scene_url=st.scene_url(v.number),
                render_urls=st.render_urls(v.number),
            )
            for v in project.versions
        ],
        scene_url=st.scene_url(),
        plan_page_urls=st.plan_page_urls(project.plan_pages),
    )


@router.get("")
async def list_projects(session: DbSession) -> list[ProjectSummaryOut]:
    projects = await crud.list_projects(session)
    out = []
    for p in projects:
        st = ProjectStorage(p.id)
        renders = st.render_urls(p.current_version) if p.current_version else {}
        out.append(
            ProjectSummaryOut(
                id=p.id,
                name=p.name,
                status=p.status,
                created_at=p.created_at,
                current_version=p.current_version,
                thumbnail_url=renders.get("aerial") or next(iter(renders.values()), None),
            )
        )
    return out


@router.post("", status_code=201)
async def create_project(
    session: DbSession,
    name: Annotated[str, Form(min_length=1, max_length=200)],
    plan: Annotated[UploadFile, File(description="PDF plan set")],
    photos: Annotated[list[UploadFile], File(description="façade photos")],
    sides: Annotated[
        list[str], Form(description="side per photo, same order: north|south|east|west|other")
    ],
) -> ProjectOut:
    if plan.content_type not in ("application/pdf", "application/x-pdf") and not (
        plan.filename or ""
    ).lower().endswith(".pdf"):
        raise InvalidInputError("plan must be a PDF")
    if len(photos) != len(sides):
        raise InvalidInputError("one side label per photo is required")
    if len(photos) == 0:
        raise InvalidInputError("at least one photo is required")
    for s in sides:
        if s not in SIDES:
            raise InvalidInputError(f"invalid side '{s}'")
    labelled = [s for s in sides if s != "other"]
    if len(labelled) != len(set(labelled)):
        raise InvalidInputError("each façade side may only be given once")

    logger.info("projects.create.requested", extra={"photos": len(photos)})
    project = await crud.create_project(session, name)
    st = ProjectStorage(project.id)
    st.ensure()
    st.plan_pdf.write_bytes(await plan.read())
    for i, (photo, side) in enumerate(zip(photos, sides, strict=True)):
        if not (photo.content_type or "").startswith("image/"):
            raise InvalidInputError(f"'{photo.filename}' is not an image")
        filename = f"{side}{'' if side != 'other' else i}.jpg"
        raw = await photo.read()
        try:
            await anyio.to_thread.run_sync(_store_photo, raw, st.photos_dir / filename)
        except Exception as e:
            logger.exception("projects.photo.store_failed", extra={"name": photo.filename})
            raise InvalidInputError(f"could not read the photo '{photo.filename}': {e}") from e
        await crud.add_photo(session, project, side, filename, photo.filename or filename)
    try:
        project.plan_pages = await anyio.to_thread.run_sync(st.rasterize_plan)
    except Exception as e:
        logger.exception("projects.plan.rasterize_failed", extra={"project_id": project.id})
        await session.rollback()
        shutil.rmtree(st.root, ignore_errors=True)
        raise InvalidInputError(f"could not read the PDF: {e}") from e
    st.init_scene_from_template()
    await session.commit()
    await session.refresh(project)
    logger.info(
        "projects.create.succeeded", extra={"project_id": project.id, "pages": project.plan_pages}
    )
    return _project_out(project)


@router.get("/{project_id}")
async def get_project(session: DbSession, project_id: str) -> ProjectOut:
    return _project_out(await crud.get_project(session, project_id))


@router.delete("/{project_id}", status_code=204)
async def delete_project(session: DbSession, project_id: str) -> None:
    project = await crud.get_project(session, project_id)
    if await crud.active_job(session, project_id):
        raise ConflictError("a job is running for this project")
    await crud.delete_project(session, project)
    await session.commit()
    shutil.rmtree(ProjectStorage(project_id).root, ignore_errors=True)


async def _start_job(session: DbSession, project_id: str, kind: str, request_text: str = "") -> Job:
    await crud.get_project(session, project_id)
    if await crud.active_job(session, project_id):
        raise ConflictError("a job is already running for this project")
    job = await crud.create_job(session, project_id, kind, request_text)
    if kind == "modify":
        await crud.add_chat_message(session, project_id, "user", request_text, job.id)
    await session.commit()
    job_manager.submit(
        job.id, project_id, pipeline.generate if kind == "generate" else pipeline.modify
    )
    logger.info("jobs.started", extra={"job_id": job.id, "kind": kind})
    return job


@router.post("/{project_id}/generate", status_code=202)
async def generate(session: DbSession, project_id: str) -> JobOut:
    return JobOut.model_validate(await _start_job(session, project_id, "generate"))


@router.post("/{project_id}/modify", status_code=202)
async def modify(session: DbSession, project_id: str, body: ModifyRequest) -> JobOut:
    project = await crud.get_project(session, project_id)
    if project.current_version == 0:
        raise ConflictError("generate the scene before modifying it")
    return JobOut.model_validate(await _start_job(session, project_id, "modify", body.message))


@router.post("/{project_id}/versions/{number}/fix", status_code=202)
async def fix_version(session: DbSession, project_id: str, number: int) -> JobOut:
    """Send the stored review findings of a version to the builder as a modification."""
    project = await crud.get_project(session, project_id)
    if number != project.current_version:
        raise ConflictError("restore this version first: findings apply to the current scene")
    v = await crud.get_version(session, project_id, number)
    if not v.critique_json:
        raise InvalidInputError("this version has no review findings")
    critique = Critique.model_validate_json(v.critique_json)
    if not critique.issues:
        raise InvalidInputError("the review found nothing to fix")
    text = (
        f"Apply the findings of the independent review of version {number} "
        f"(score {critique.overall_score}/100). Fix every point below, most impactful first, "
        "verify with renders from the same sides, and keep everything else as it is.\n\n"
        + critique.as_builder_feedback()
    )
    return JobOut.model_validate(await _start_job(session, project_id, "modify", text))


@router.get("/{project_id}/jobs")
async def list_jobs(session: DbSession, project_id: str) -> list[JobOut]:
    await crud.get_project(session, project_id)
    return [JobOut.model_validate(j) for j in await crud.list_jobs(session, project_id)]


@router.get("/{project_id}/jobs/{job_id}")
async def get_job(session: DbSession, project_id: str, job_id: str) -> JobOut:
    job = await crud.get_job(session, job_id)
    if job.project_id != project_id:
        raise NotFoundError("job not found")
    return JobOut.model_validate(job)


@router.get("/{project_id}/jobs/{job_id}/events")
async def job_events(
    session: DbSession, project_id: str, job_id: str, after: int = 0
) -> list[JobEventOut]:
    job = await crud.get_job(session, job_id)
    if job.project_id != project_id:
        raise NotFoundError("job not found")
    return [
        JobEventOut(
            seq=e.seq, type=e.type, payload=json.loads(e.payload_json), created_at=e.created_at
        )
        for e in await crud.list_job_events(session, job_id, after)
    ]


@router.get(
    "/{project_id}/jobs/{job_id}/stream", response_class=StreamingResponse, include_in_schema=False
)
async def job_stream(
    request: Request,
    session: DbSession,
    project_id: str,
    job_id: str,
    after: Annotated[int, Query()] = 0,
) -> EventSourceResponse:
    job = await crud.get_job(session, job_id)
    if job.project_id != project_id:
        raise NotFoundError("job not found")

    async def gen() -> AsyncIterator[dict[str, str]]:
        async for ev in job_manager.stream(job_id, after):
            if await request.is_disconnected():
                break
            msg = {"event": ev["type"], "data": json.dumps(ev, default=str)}
            if not ev.get("transient"):
                msg["id"] = str(ev["seq"])
            yield msg
        yield {"event": "end", "data": "{}"}

    return EventSourceResponse(gen())


@router.get("/{project_id}/chat")
async def chat_history(session: DbSession, project_id: str) -> list[ChatMessageOut]:
    await crud.get_project(session, project_id)
    return [ChatMessageOut.model_validate(m) for m in await crud.list_chat(session, project_id)]


@router.post("/{project_id}/versions/{number}/restore")
async def restore_version(session: DbSession, project_id: str, number: int) -> ProjectOut:
    project = await crud.get_project(session, project_id)
    if await crud.active_job(session, project_id):
        raise ConflictError("a job is running for this project")
    await crud.get_version(session, project_id, number)
    st = ProjectStorage(project_id)
    st.restore(number)
    n = st.next_version_number()
    dst = st.snapshot(n)
    src_renders = st.versions_dir / str(number) / "renders"
    if src_renders.exists():
        shutil.copytree(src_renders, dst / "renders", dirs_exist_ok=True)
    await crud.add_version(
        session,
        project_id,
        n,
        "restore",
        f"Restored version {number}",
        f"Restored from version {number}.",
    )
    await session.commit()
    await session.refresh(project)
    logger.info("versions.restored", extra={"project_id": project_id, "from": number, "to": n})
    return _project_out(project)


@router.get("/{project_id}/files")
async def scene_files(
    session: DbSession, project_id: str, version: int | None = None
) -> SceneFilesOut:
    project = await crud.get_project(session, project_id)
    st = ProjectStorage(project_id)
    if version is not None:
        await crud.get_version(session, project_id, version)
    files = st.scene_files(version)
    return SceneFilesOut(
        version=version or project.current_version,
        files=[SceneFileOut(path=k, content=v) for k, v in files.items()],
    )


@router.get("/{project_id}/export", response_class=StreamingResponse, include_in_schema=False)
async def export_zip(
    session: DbSession, project_id: str, version: int | None = None
) -> StreamingResponse:
    """Zip of a self-contained scene (index.html + src + kit + three.js) that runs from any static server."""
    project = await crud.get_project(session, project_id)
    st = ProjectStorage(project_id)
    base = st.scene_dir if version is None else st.versions_dir / str(version)
    if not base.exists():
        raise NotFoundError("version not found")
    kit = st.settings.KIT_DIR
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        html = (
            (base / "index.html")
            .read_text(encoding="utf-8")
            .replace('"/kit/', '"./kit/')
            .replace('"./src/', '"./src/')
        )
        z.writestr("index.html", html)
        for p in (base / "src").rglob("*.js"):
            z.write(p, f"src/{p.relative_to(base / 'src')}")
        z.write(kit / "house.js", "kit/house.js")
        z.write(kit / "runtime.js", "kit/runtime.js")
        three = kit / "node_modules" / "three"
        z.write(three / "build" / "three.module.js", "kit/vendor/three/build/three.module.js")
        z.write(three / "build" / "three.core.js", "kit/vendor/three/build/three.core.js")
        for p in (three / "examples" / "jsm").rglob("*.js"):
            z.write(p, f"kit/vendor/three/examples/jsm/{p.relative_to(three / 'examples' / 'jsm')}")
    buf.seek(0)
    name = f"{project.name.replace(' ', '_')}-v{version or project.current_version}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
