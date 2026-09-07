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
from housegen.agent.estimate import Estimate, Kind, estimate
from housegen.agent.run_settings import ResolvedRunSettings, RunSettings, resolve
from housegen.agent.schemas import Critique
from housegen.core.config import get_settings
from housegen.core.db import DbSession
from housegen.core.exceptions import ConflictError, InvalidInputError, NotFoundError
from housegen.jobs.manager import job_manager
from housegen.projects import crud
from housegen.projects.models import ChatMessage, Job, Project
from housegen.projects.schemas import (
    ChatMessageOut,
    GenerateRequest,
    IntakeOut,
    JobEventOut,
    JobOut,
    PhotoOut,
    PlanDocumentOut,
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
    # the original feeds inspect_image crops; the working copy is what goes in every LLM call
    orig = path.parent / "orig" / path.name
    orig.parent.mkdir(parents=True, exist_ok=True)
    orig.write_bytes(raw)
    """Normalise an upload: apply EXIF rotation, cap the long side, save as JPEG."""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
    assert img is not None
    img = img.convert("RGB")
    img.thumbnail((MAX_PHOTO_PX, MAX_PHOTO_PX), Image.Resampling.LANCZOS)
    img.save(path, "JPEG", quality=88, optimize=True)


def _run_settings(project: Project) -> RunSettings:
    return RunSettings.model_validate(project.settings) if project.settings else RunSettings()


def _project_out(project: Project) -> ProjectOut:
    st = ProjectStorage(project.id)
    stored = _run_settings(project)
    return ProjectOut(
        id=project.id,
        name=project.name,
        status=project.status,
        created_at=project.created_at,
        plans=[
            PlanDocumentOut(
                id=d.id,
                number=d.number,
                label=d.label,
                original_name=d.original_name,
                pages=d.pages,
                page_urls=[st.plan_page_url(d.number, i + 1) for i in range(d.pages)],
                created_at=d.created_at,
            )
            for d in project.plans
        ],
        plan_pages=project.plan_pages,
        current_version=project.current_version,
        brief=project.brief,
        intake=IntakeOut.model_validate_json(project.intake_json) if project.intake_json else None,
        settings=stored,
        effective_settings=resolve(get_settings(), stored),
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
                suggestions=json.loads(v.suggestions_json) if v.suggestions_json else [],
                questions=json.loads(v.questions_json) if v.questions_json else [],
                created_at=v.created_at,
                scene_url=st.scene_url(v.number),
                render_urls=st.render_urls(v.number),
            )
            for v in project.versions
        ],
        scene_url=st.scene_url(),
        plan_page_urls=[
            st.plan_page_url(d.number, i + 1) for d in project.plans for i in range(d.pages)
        ],
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


def _is_pdf(upload: UploadFile) -> bool:
    return upload.content_type in ("application/pdf", "application/x-pdf") or (
        upload.filename or ""
    ).lower().endswith(".pdf")


async def _add_plan(
    session: DbSession, project: Project, st: ProjectStorage, upload: UploadFile, label: str
) -> None:
    """Store one plan document under plans/<n>/ and rasterise its sheets (#10)."""
    if not _is_pdf(upload):
        raise InvalidInputError(f"'{upload.filename}' is not a PDF")
    n = st.next_plan_document()
    st.plan_dir(n).mkdir(parents=True, exist_ok=True)
    st.plan_pdf(n).write_bytes(await upload.read())
    try:
        pages = await anyio.to_thread.run_sync(st.rasterize_plan, n)
    except Exception as e:
        logger.exception("projects.plan.rasterize_failed", extra={"project_id": project.id})
        shutil.rmtree(st.plan_dir(n), ignore_errors=True)
        raise InvalidInputError(f"could not read the PDF '{upload.filename}': {e}") from e
    await crud.add_plan_document(session, project, n, label, upload.filename or "", pages)


@router.post("", status_code=201)
async def create_project(
    session: DbSession,
    name: Annotated[str, Form(min_length=1, max_length=200)],
    # one or more PDF plan sets (the 1935 original, the 2024 survey…), with an optional label
    # each, same order. Plain lists with an empty default (not `| None`): the generated
    # client only knows how to put an array of files in the multipart body
    plans: Annotated[list[UploadFile], File(description="PDF plan set(s)")],
    plan_labels: Annotated[
        list[str], Form(description="label per plan document, same order (optional)")
    ] = [],  # noqa: B006
    photos: Annotated[
        list[UploadFile],
        File(description="photos of the house (optional: without any, the plans are read first)"),
    ] = [],  # noqa: B006
    sides: Annotated[
        list[str],
        Form(description="side per photo, same order: north|south|east|west|other"),
    ] = [],  # noqa: B006
    notes: Annotated[
        str,
        Form(
            max_length=4000,
            description="what the files cannot say: changes since the plan, materials, colours",
        ),
    ] = "",
) -> ProjectOut:
    if not plans:
        raise InvalidInputError("at least one PDF plan set is required")
    for up in plans:
        if not _is_pdf(up):
            raise InvalidInputError(f"'{up.filename}' is not a PDF")
    if plan_labels and len(plan_labels) != len(plans):
        raise InvalidInputError("one label per plan document (or none)")
    if len(photos) != len(sides):
        raise InvalidInputError("one side label per photo is required")
    for s in sides:
        if s not in SIDES:
            raise InvalidInputError(f"invalid side '{s}'")
    labelled = [s for s in sides if s != "other"]
    if len(labelled) != len(set(labelled)):
        raise InvalidInputError("each façade side may only be given once")

    logger.info(
        "projects.create.requested", extra={"photos": len(photos), "notes": len(notes.strip())}
    )
    project = await crud.create_project(session, name, brief=notes.strip() or None)
    st = ProjectStorage(project.id)
    st.ensure()
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
        for k, up in enumerate(plans):
            await _add_plan(session, project, st, up, plan_labels[k] if plan_labels else "")
    except InvalidInputError:
        await session.rollback()
        shutil.rmtree(st.root, ignore_errors=True)
        raise
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


@router.post("/{project_id}/plans", status_code=201)
async def add_plan_document(
    session: DbSession,
    project_id: str,
    plan: Annotated[UploadFile, File(description="PDF plan set")],
    label: Annotated[str, Form(max_length=200)] = "",
) -> ProjectOut:
    """Add a plan document later (the extension drawings, a survey). The next run sees it;
    when documents disagree the builder trusts the most recent one for today's state."""
    project = await crud.get_project(session, project_id)
    st = ProjectStorage(project_id)
    st.ensure()
    await _add_plan(session, project, st, plan, label)
    await session.commit()
    await session.refresh(project)
    return _project_out(project)


@router.delete("/{project_id}", status_code=204)
async def delete_project(session: DbSession, project_id: str) -> None:
    project = await crud.get_project(session, project_id)
    if await crud.active_job(session, project_id):
        raise ConflictError("a job is running for this project")
    await crud.delete_project(session, project)
    await session.commit()
    shutil.rmtree(ProjectStorage(project_id).root, ignore_errors=True)


_PIPELINES = {"intake": pipeline.intake, "generate": pipeline.generate, "modify": pipeline.modify}


async def _start_job(
    session: DbSession,
    project_id: str,
    kind: str,
    request_text: str = "",
    photos: list[UploadFile] | None = None,
    keep_photos: bool = True,
) -> Job:
    project = await crud.get_project(session, project_id)
    if await crud.active_job(session, project_id):
        raise ConflictError("a job is already running for this project")
    # the job runs with a snapshot of the project's settings taken now (#18)
    resolved = resolve(get_settings(), _run_settings(project))
    job = await crud.create_job(
        session, project_id, kind, request_text, settings=resolved.model_dump()
    )
    # photos attached to a modification request (#8): stored like uploads, named after the
    # job, given to the builder and the verifier as ground truth for this request and,
    # unless the user opted out, kept as project extras for every later pass
    attachments: list[str] = []
    st = ProjectStorage(project_id)
    for i, photo in enumerate(photos or [], 1):
        if not (photo.content_type or "").startswith("image/"):
            raise InvalidInputError(f"'{photo.filename}' is not an image")
        filename = f"modify-{job.id}-{i}.jpg"
        raw = await photo.read()
        try:
            await anyio.to_thread.run_sync(_store_photo, raw, st.photos_dir / filename)
        except Exception as e:
            logger.exception("projects.photo.store_failed", extra={"name": photo.filename})
            raise InvalidInputError(f"could not read the photo '{photo.filename}': {e}") from e
        attachments.append(filename)
        if keep_photos:
            await crud.add_photo(session, project, "other", filename, photo.filename or filename)
    if attachments:
        await crud.update_job(session, job.id, attachments=attachments)
    if request_text:
        # the request is the user's turn of the conversation (a modification, or the answers
        # to the intake's questions that start a build)
        await crud.add_chat_message(
            session, project_id, "user", request_text, job.id, attachments=attachments
        )
    await session.commit()
    job_manager.submit(job.id, project_id, _PIPELINES[kind])
    logger.info(
        "jobs.started", extra={"job_id": job.id, "kind": kind, "attachments": len(attachments)}
    )
    return job


@router.patch("/{project_id}/settings")
async def update_settings(session: DbSession, project_id: str, body: RunSettings) -> ProjectOut:
    """Set the project's run settings (model, effort, critic rounds, step budget, in-loop
    render quality). Unset fields keep the .env defaults. A running job is not affected: it
    took its snapshot at start."""
    project = await crud.get_project(session, project_id)
    stored = body.model_dump(exclude_none=True)
    await crud.set_project_settings(session, project_id, stored or None)
    await session.commit()
    await session.refresh(project)
    logger.info("projects.settings", extra={"project_id": project_id, "settings": stored})
    return _project_out(project)


@router.get("/{project_id}/settings/effective")
async def effective_settings(session: DbSession, project_id: str) -> ResolvedRunSettings:
    """What a run started now would use (the stored settings over the .env defaults, effort
    values mapped onto the provider)."""
    project = await crud.get_project(session, project_id)
    return resolve(get_settings(), _run_settings(project))


@router.get("/{project_id}/estimate")
async def run_estimate(session: DbSession, project_id: str, kind: Kind = "generate") -> Estimate:
    """Rough duration and cost of the next run of `kind`, from this project's finished runs
    of that kind (or a typical profile before the first) and the price table."""
    project = await crud.get_project(session, project_id)
    jobs = await crud.list_jobs(session, project_id)
    return estimate(get_settings(), resolve(get_settings(), _run_settings(project)), kind, jobs)


@router.post("/{project_id}/intake", status_code=202)
async def intake(session: DbSession, project_id: str) -> JobOut:
    """Read the plan set before building (projects without photos): what the house is as
    drawn, which sheet is what, and the questions the drawings cannot answer."""
    project = await crud.get_project(session, project_id)
    if project.plan_pages == 0:
        raise InvalidInputError("the plan set has no sheets to read")
    return JobOut.model_validate(await _start_job(session, project_id, "intake"))


@router.post("/{project_id}/generate", status_code=202)
async def generate(
    session: DbSession, project_id: str, body: GenerateRequest | None = None
) -> JobOut:
    """Build from scratch. The optional body carries the owner's answers to the intake's
    questions (and any notes): they are added to the project's brief for this and every
    later pass, and shown as the request that started the build."""
    text = body.as_text() if body else ""
    if text:
        project = await crud.get_project(session, project_id)
        await crud.set_brief(
            session, project_id, "\n\n".join(x for x in (project.brief or "", text) if x.strip())
        )
    return JobOut.model_validate(await _start_job(session, project_id, "generate", text))


@router.post("/{project_id}/modify", status_code=202)
async def modify(
    session: DbSession,
    project_id: str,
    message: Annotated[str, Form(min_length=1, max_length=4000)],
    photos: Annotated[
        list[UploadFile],
        File(
            description="photos of the detail to change (optional): ground truth for this request"
        ),
    ] = [],  # noqa: B006
    keep: Annotated[
        bool,
        Form(description="also keep the attached photos as reference photos of the project"),
    ] = True,
) -> JobOut:
    """Ask for a change, optionally with photographs of the detail to change."""
    project = await crud.get_project(session, project_id)
    if project.current_version == 0:
        raise ConflictError("generate the scene before modifying it")
    if len(photos) > 12:
        raise InvalidInputError("at most 12 photos per request")
    return JobOut.model_validate(
        await _start_job(session, project_id, "modify", message, photos, keep)
    )


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


def _chat_out(m: ChatMessage, st: ProjectStorage) -> ChatMessageOut:
    return ChatMessageOut(
        id=m.id,
        role=m.role,
        content=m.content,
        job_id=m.job_id,
        version_number=m.version_number,
        attachments=[st.photo_url(f) for f in m.attachments],
        created_at=m.created_at,
    )


@router.get("/{project_id}/chat")
async def chat_history(session: DbSession, project_id: str) -> list[ChatMessageOut]:
    await crud.get_project(session, project_id)
    st = ProjectStorage(project_id)
    return [_chat_out(m, st) for m in await crud.list_chat(session, project_id)]


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
