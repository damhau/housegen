"""The administrator's view (#108): every organisation, moving projects between them, and each
organisation's spending limits (#107). Not on the API's `protected` router: the administrator needs
no organisation of their own, and a project of any organisation is theirs to move."""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends

from housegen.accounts import crud as accounts
from housegen.accounts.dependencies import AdminCaller, admin_caller
from housegen.admin import crud
from housegen.admin.schemas import (
    AdminMemberOut,
    AdminOrganisationOut,
    AdminProjectOut,
    MoveProjectIn,
)
from housegen.core.db import DbSession
from housegen.limits import service as limits
from housegen.limits.schemas import LimitsIn, LimitsOut
from housegen.projects import crud as projects
from housegen.projects.models import Project

logger = logging.getLogger(__name__)
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
    how many projects it holds, its spending limits and its use of them."""
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
            limits=await limits.organisation_limits(session, o.organisation),
        )
        for o in await crud.organisations(session)
    ]


@router.put("/organisations/{organisation_id}/limits")
async def set_organisation_limits(
    session: DbSession, admin: AdminCaller, organisation_id: str, body: LimitsIn
) -> LimitsOut:
    """Set an organisation's own limits, each a number, "unlimited", or null for the server's
    default. Jobs already started go on; the next one is checked against the new limits."""
    organisation = await accounts.get_organisation(session, organisation_id)
    await limits.set_limits(session, organisation, body)
    await session.commit()
    logger.info(
        "admin.limits_set",
        extra={
            "organisation_id": organisation_id,
            "limits": organisation.limits,
            "by": admin.id if admin else None,
        },
    )
    return await limits.organisation_limits(session, organisation)


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
