"""The administrator's view (#108): every organisation, and moving projects between them. Not on
the API's `protected` router: the administrator needs no organisation of their own, and a project
of any organisation is theirs to move."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends

from housegen.accounts.dependencies import AdminCaller, admin_caller
from housegen.admin import crud
from housegen.admin.schemas import (
    AdminMemberOut,
    AdminOrganisationOut,
    AdminProjectOut,
    MoveProjectIn,
)
from housegen.core.db import DbSession
from housegen.projects import crud as projects
from housegen.projects.models import Project

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(admin_caller)])


def _project_out(
    project: Project, saved: dict[str, datetime], jobs: dict[str, str]
) -> AdminProjectOut:
    return AdminProjectOut(
        id=project.id,
        name=project.name,
        status=project.status,
        updated_at=saved.get(project.id, project.created_at),
        organisation_id=project.organisation_id,
        job=jobs.get(project.id),
    )


@router.get("/organisations")
async def admin_organisations(session: DbSession) -> list[AdminOrganisationOut]:
    """Every organisation, oldest first, those nobody joined yet included: members and roles,
    how many projects it holds."""
    return [
        AdminOrganisationOut(
            id=o.organisation.id,
            name=o.organisation.name,
            is_default=o.organisation.is_default,
            created_at=o.organisation.created_at,
            members=[
                AdminMemberOut(
                    user_id=u.id,
                    name=u.name,
                    email=u.email,
                    picture=u.picture,
                    role="owner" if m.role == "owner" else "member",
                )
                for m, u in o.members
            ],
            project_count=o.project_count,
        )
        for o in await crud.organisations(session)
    ]


@router.get("/projects")
async def admin_projects(session: DbSession) -> list[AdminProjectOut]:
    """Every organisation's projects, newest first."""
    saved = await projects.latest_version_times(session)
    jobs = await projects.active_job_kinds(session)
    return [_project_out(p, saved, jobs) for p in await projects.list_projects(session, None)]


@router.put("/projects/{project_id}/organisation")
async def move_project(
    session: DbSession, admin: AdminCaller, project_id: str, body: MoveProjectIn
) -> AdminProjectOut:
    """Give a project to another organisation: its members see it from now on, the old one's do
    not. Nothing else changes (files, versions, listing sets, share link). 404 for an unknown
    project or organisation, 409 while a job is not over; the same organisation changes nothing."""
    project = await crud.move_project(
        session, project_id, body.organisation_id, admin.id if admin else None
    )
    await session.commit()
    saved = await projects.latest_version_times(session)
    return _project_out(project, saved, await projects.active_job_kinds(session))
