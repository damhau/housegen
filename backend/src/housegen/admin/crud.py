from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from housegen.accounts import crud as accounts
from housegen.accounts.models import Membership, Organisation, User
from housegen.core.exceptions import ConflictError
from housegen.projects import crud as projects
from housegen.projects.models import Project

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OrganisationOverview:
    organisation: Organisation
    members: list[tuple[Membership, User]]
    project_count: int


async def organisations(session: AsyncSession) -> list[OrganisationOverview]:
    """Every organisation, oldest first, with its members and how many projects it holds."""
    res = await session.execute(select(Organisation).order_by(Organisation.created_at))
    found = list(res.scalars().all())
    members: dict[str, list[tuple[Membership, User]]] = defaultdict(list)
    rows = await session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .order_by(Membership.created_at)
    )
    for membership, user in rows.tuples().all():
        members[membership.organisation_id].append((membership, user))
    counts = await session.execute(
        select(Project.organisation_id, func.count()).group_by(Project.organisation_id)
    )
    count_of = {org: int(n) for org, n in counts.tuples().all()}
    return [OrganisationOverview(o, members[o.id], count_of.get(o.id, 0)) for o in found]


async def move_project(
    session: AsyncSession, project_id: str, organisation_id: str, by: str | None
) -> Project:
    """Give a project to another organisation. Only `organisation_id` changes: its files,
    versions, listing sets and share link are stored by project id. Refused while a job is not
    over, so nobody watches a job of a project they just lost."""
    project = await projects.get_project(session, project_id)
    organisation = await accounts.get_organisation(session, organisation_id)
    source = project.organisation_id
    if source == organisation.id:
        return project
    if await projects.active_job(session, project_id):
        raise ConflictError("a job is running for this project: move it once the job is over")
    project.organisation_id = organisation.id
    await session.flush()
    logger.info(
        "admin.project_moved",
        extra={
            "project_id": project_id,
            "from_organisation_id": source,
            "to_organisation_id": organisation.id,
            "by": by,
        },
    )
    return project
