"""Who may see a project (#105): the members of its organisation, the agent token in its own
(#112), and the tokens that read every organisation. Another organisation's project answers 404,
as one that does not exist."""

from __future__ import annotations

import logging

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from housegen.accounts.dependencies import (
    ApiCaller,
    agent_organisation,
    optional_access,
    optional_user,
    presented_token,
    require_token,
)
from housegen.core.db import DbSession
from housegen.core.exceptions import ForbiddenError, NotFoundError, UnauthorizedError
from housegen.projects.models import Project

logger = logging.getLogger(__name__)


async def _check(session: AsyncSession, project_id: str, organisation_id: str | None) -> None:
    project = await session.get(Project, project_id)
    if project is None or (
        organisation_id is not None and project.organisation_id != organisation_id
    ):
        raise NotFoundError(f"project {project_id} not found")


async def project_in_scope(request: Request, session: DbSession, caller: ApiCaller) -> None:
    """A protected router's guard: the route's `{project_id}`, when it has one, must be a project
    the caller sees. Checked before the route runs, so before any job is enqueued."""
    project_id = request.path_params.get("project_id")
    if project_id is not None:
        await _check(session, str(project_id), caller.organisation_id)


async def check_scene_access(request: Request, session: AsyncSession, project_id: str) -> None:
    """`/scenes/<project>/…`: the service and read-only tokens read any project's files; a
    session, and the agent token (#112), those of the organisation they work in."""
    token = presented_token(request)
    try:
        if token is not None:
            name = require_token(request, token, frozenset({"service", "readonly", "agent"}))
            if name == "agent":
                await _check(session, project_id, (await agent_organisation(session)).id)
            return
        user = await optional_user(request, session)
        if user is None:
            raise UnauthorizedError("sign in first")
        access = await optional_access(request, session, user)
        if access is None:
            raise ForbiddenError("create or join an organisation first", reason="no_organisation")
        await _check(session, project_id, access.organisation.id)
    except (UnauthorizedError, ForbiddenError) as e:
        logger.info(
            "auth.scenes.denied",
            extra={"project_id": project_id, "status": e.status_code, "token": token is not None},
        )
        raise


async def scene_page_access(request: Request, session: DbSession) -> None:
    """The scene page routes (`render.kits.pages_router`), by their `{project_id}`."""
    await check_scene_access(request, session, str(request.path_params.get("project_id", "")))
