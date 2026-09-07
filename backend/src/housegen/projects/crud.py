from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from housegen.core.exceptions import NotFoundError
from housegen.projects.models import ChatMessage, Job, JobEvent, Photo, Project, SceneVersion

logger = logging.getLogger(__name__)


async def get_project(session: AsyncSession, project_id: str) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    return project


async def list_projects(session: AsyncSession) -> list[Project]:
    res = await session.execute(select(Project).order_by(Project.created_at.desc()))
    return list(res.scalars().all())


async def create_project(session: AsyncSession, name: str) -> Project:
    project = Project(name=name)
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


async def delete_project(session: AsyncSession, project: Project) -> None:
    await session.delete(project)
    await session.flush()
    logger.info("projects.deleted", extra={"project_id": project.id})


async def set_status(session: AsyncSession, project_id: str, status: str) -> None:
    project = await get_project(session, project_id)
    project.status = status
    await session.flush()


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
    session: AsyncSession, project_id: str, kind: str, request_text: str = ""
) -> Job:
    job = Job(project_id=project_id, kind=kind, request_text=request_text)
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


async def active_job(session: AsyncSession, project_id: str) -> Job | None:
    res = await session.execute(
        select(Job).where(Job.project_id == project_id, Job.status.in_(["queued", "running"]))
    )
    return res.scalars().first()


async def update_job(
    session: AsyncSession,
    job_id: str,
    *,
    status: str | None = None,
    error: str | None = None,
    result_version: int | None = None,
) -> Job:
    job = await get_job(session, job_id)
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


# ---- chat ----


async def add_chat_message(
    session: AsyncSession,
    project_id: str,
    role: str,
    content: str,
    job_id: str | None = None,
    version_number: int | None = None,
) -> ChatMessage:
    m = ChatMessage(
        project_id=project_id,
        role=role,
        content=content,
        job_id=job_id,
        version_number=version_number,
    )
    session.add(m)
    await session.flush()
    return m


async def list_chat(session: AsyncSession, project_id: str) -> list[ChatMessage]:
    res = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.project_id == project_id)
        .order_by(ChatMessage.created_at)
    )
    return list(res.scalars().all())
