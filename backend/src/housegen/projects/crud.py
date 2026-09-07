from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from housegen.core.exceptions import NotFoundError
from housegen.projects.models import (
    ChatMessage,
    Job,
    JobEvent,
    Photo,
    PlanDocument,
    Project,
    SceneVersion,
)

logger = logging.getLogger(__name__)


async def get_project(session: AsyncSession, project_id: str) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    return project


async def list_projects(session: AsyncSession) -> list[Project]:
    res = await session.execute(select(Project).order_by(Project.created_at.desc()))
    return list(res.scalars().all())


async def create_project(session: AsyncSession, name: str, brief: str | None = None) -> Project:
    project = Project(name=name, brief=brief or None)
    session.add(project)
    await session.flush()
    logger.info("projects.created", extra={"project_id": project.id})
    return project


async def add_photo(
    session: AsyncSession, project: Project, side: str, filename: str, original_name: str
) -> Photo:
    photo = Photo(project_id=project.id, side=side, filename=filename, original_name=original_name)
    session.add(photo)
    await session.flush()
    return photo


async def add_plan_document(
    session: AsyncSession,
    project: Project,
    number: int,
    label: str,
    original_name: str,
    pages: int,
) -> PlanDocument:
    doc = PlanDocument(
        project_id=project.id,
        number=number,
        label=label.strip() or original_name or f"Plans {number}",
        original_name=original_name,
        pages=pages,
    )
    session.add(doc)
    project.plan_pages = (project.plan_pages or 0) + pages
    await session.flush()
    logger.info(
        "plans.added",
        extra={"project_id": project.id, "document": number, "pages": pages, "label": doc.label},
    )
    return doc


async def projects_without_plan_documents(session: AsyncSession) -> list[Project]:
    """Projects from before #10: a plan set but no PlanDocument row (the migration adds one)."""
    res = await session.execute(select(Project).where(Project.plan_pages > 0))
    return [p for p in res.scalars().all() if not p.plans]


async def delete_project(session: AsyncSession, project: Project) -> None:
    await session.delete(project)
    await session.flush()
    logger.info("projects.deleted", extra={"project_id": project.id})


async def set_status(session: AsyncSession, project_id: str, status: str) -> None:
    project = await get_project(session, project_id)
    project.status = status
    await session.flush()


async def settle_project_status(session: AsyncSession, project_id: str) -> None:
    """After a job failed for good: a project must not stay `generating` forever (#7).

    With a version it is still usable (`ready`); without one the build failed (`failed`).
    Other statuses (`new` after a failed intake) are left alone.
    """
    project = await get_project(session, project_id)
    if project.status != "generating":
        return
    project.status = "ready" if project.current_version > 0 else "failed"
    await session.flush()


async def set_brief(session: AsyncSession, project_id: str, brief: str | None) -> Project:
    project = await get_project(session, project_id)
    project.brief = brief.strip() if brief and brief.strip() else None
    await session.flush()
    return project


async def set_project_settings(
    session: AsyncSession, project_id: str, settings: dict[str, Any] | None
) -> Project:
    project = await get_project(session, project_id)
    project.settings_json = json.dumps(settings) if settings else None
    await session.flush()
    return project


async def set_intake(
    session: AsyncSession, project_id: str, intake: dict[str, Any] | None
) -> Project:
    project = await get_project(session, project_id)
    project.intake_json = json.dumps(intake, ensure_ascii=False) if intake else None
    await session.flush()
    return project


async def add_version(
    session: AsyncSession,
    project_id: str,
    number: int,
    kind: str,
    label: str,
    summary: str = "",
    critic_score: int | None = None,
    suggestions: list[str] | None = None,
    questions: list[str] | None = None,
) -> SceneVersion:
    project = await get_project(session, project_id)
    v = SceneVersion(
        project_id=project_id,
        number=number,
        kind=kind,
        label=label,
        summary=summary,
        critic_score=critic_score,
        suggestions_json=json.dumps(suggestions, ensure_ascii=False) if suggestions else None,
        questions_json=json.dumps(questions, ensure_ascii=False) if questions else None,
    )
    session.add(v)
    project.current_version = number
    await session.flush()
    logger.info(
        "versions.created", extra={"project_id": project_id, "version": number, "kind": kind}
    )
    return v


async def get_version(session: AsyncSession, project_id: str, number: int) -> SceneVersion:
    res = await session.execute(
        select(SceneVersion).where(
            SceneVersion.project_id == project_id, SceneVersion.number == number
        )
    )
    v = res.scalar_one_or_none()
    if v is None:
        raise NotFoundError(f"version {number} not found")
    return v


async def set_version_critique(
    session: AsyncSession, project_id: str, number: int, score: int, critique: dict[str, Any]
) -> SceneVersion:
    v = await get_version(session, project_id, number)
    v.critic_score = score
    v.critique_json = json.dumps(critique, ensure_ascii=False)
    await session.flush()
    return v


# ---- jobs ----


async def create_job(
    session: AsyncSession,
    project_id: str,
    kind: str,
    request_text: str = "",
    settings: dict[str, Any] | None = None,
) -> Job:
    job = Job(
        project_id=project_id,
        kind=kind,
        request_text=request_text,
        settings_json=json.dumps(settings) if settings else None,
    )
    session.add(job)
    await session.flush()
    logger.info("jobs.created", extra={"project_id": project_id, "job_id": job.id, "kind": kind})
    return job


async def get_job(session: AsyncSession, job_id: str) -> Job:
    job = await session.get(Job, job_id)
    if job is None:
        raise NotFoundError(f"job {job_id} not found")
    return job


async def list_jobs(session: AsyncSession, project_id: str) -> list[Job]:
    res = await session.execute(
        select(Job).where(Job.project_id == project_id).order_by(Job.created_at.desc())
    )
    return list(res.scalars().all())


# a job that is not over: queued, running, or interrupted by a server restart and waiting
# to be resumed (#7). No second job may start on the project meanwhile.
UNFINISHED = ("queued", "running", "interrupted")


async def active_job(session: AsyncSession, project_id: str) -> Job | None:
    res = await session.execute(
        select(Job).where(Job.project_id == project_id, Job.status.in_(UNFINISHED))
    )
    return res.scalars().first()


async def list_unfinished_jobs(session: AsyncSession) -> list[Job]:
    """Every job a previous process left behind: to resume at startup (#7)."""
    res = await session.execute(
        select(Job).where(Job.status.in_(UNFINISHED)).order_by(Job.created_at)
    )
    return list(res.scalars().all())


async def update_job(
    session: AsyncSession,
    job_id: str,
    *,
    status: str | None = None,
    error: str | None = None,
    result_version: int | None = None,
    attachments: list[str] | None = None,
    metrics: dict[str, Any] | None = None,
) -> Job:
    job = await get_job(session, job_id)
    if attachments is not None:
        job.attachments_json = json.dumps(attachments)
    if metrics is not None:
        job.metrics_json = json.dumps(metrics)
    if status:
        job.status = status
        if status in ("done", "failed"):
            job.finished_at = datetime.now(UTC)
    if error is not None:
        job.error = error
    if result_version is not None:
        job.result_version = result_version
    await session.flush()
    return job


async def add_job_event(
    session: AsyncSession, job_id: str, seq: int, type_: str, payload: dict[str, Any]
) -> JobEvent:
    ev = JobEvent(
        job_id=job_id,
        seq=seq,
        type=type_,
        payload_json=json.dumps(payload, ensure_ascii=False, default=str),
    )
    session.add(ev)
    await session.flush()
    return ev


async def list_job_events(session: AsyncSession, job_id: str, after_seq: int = 0) -> list[JobEvent]:
    res = await session.execute(
        select(JobEvent)
        .where(JobEvent.job_id == job_id, JobEvent.seq > after_seq)
        .order_by(JobEvent.seq)
    )
    return list(res.scalars().all())


async def last_event_seq(session: AsyncSession, job_id: str) -> int:
    """The highest persisted seq of a job (0 when none): a resumed job continues from it."""
    res = await session.execute(select(func.max(JobEvent.seq)).where(JobEvent.job_id == job_id))
    return int(res.scalar_one() or 0)


async def count_job_events(session: AsyncSession, job_id: str, type_: str) -> int:
    res = await session.execute(
        select(func.count()).where(JobEvent.job_id == job_id, JobEvent.type == type_)
    )
    return int(res.scalar_one() or 0)


# ---- chat ----


async def add_chat_message(
    session: AsyncSession,
    project_id: str,
    role: str,
    content: str,
    job_id: str | None = None,
    version_number: int | None = None,
    attachments: list[str] | None = None,
) -> ChatMessage:
    m = ChatMessage(
        project_id=project_id,
        role=role,
        content=content,
        job_id=job_id,
        version_number=version_number,
        attachments_json=json.dumps(attachments) if attachments else None,
    )
    session.add(m)
    await session.flush()
    return m


async def set_chat_attachments(
    session: AsyncSession, message_id: str, attachments: list[str]
) -> None:
    m = await session.get(ChatMessage, message_id)
    if m is not None:
        m.attachments_json = json.dumps(attachments) if attachments else None
        await session.flush()


async def list_chat(session: AsyncSession, project_id: str) -> list[ChatMessage]:
    res = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.project_id == project_id)
        .order_by(ChatMessage.created_at)
    )
    return list(res.scalars().all())
