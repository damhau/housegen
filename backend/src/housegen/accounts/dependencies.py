"""Who is calling: the signed-in user (the session cookie) and the organisation they work in, or a
token presented instead of a session (#105, #112)."""

from __future__ import annotations

import logging
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from housegen.accounts import agent, crud
from housegen.accounts.models import AGENT_ISSUER, LOCAL_ISSUER, Organisation, Role, User
from housegen.core.config import TOKEN_COOKIE, get_settings
from housegen.core.db import DbSession
from housegen.core.exceptions import ForbiddenError, NotFoundError, UnauthorizedError
from housegen.projects.crud import jobs_created

logger = logging.getLogger(__name__)

SESSION_USER = "uid"
SESSION_ORGANISATION = "org"
SESSION_NEXT = "next"
READ_METHODS = frozenset({"GET", "HEAD"})

TokenName = Literal["service", "readonly", "agent"]


@dataclass(frozen=True)
class Access:
    user: User
    organisation: Organisation
    role: Role


async def optional_user(request: Request, session: DbSession) -> User | None:
    if get_settings().AUTH_DISABLED:
        user, created = await crud.local_user(session)
        if created:
            await session.commit()
        return user
    user_id = request.session.get(SESSION_USER)
    if not user_id:
        return None
    signed_in = await session.get(User, user_id)
    if signed_in is None or signed_in.issuer in (LOCAL_ISSUER, AGENT_ISSUER):
        return None
    return signed_in


OptionalUser = Annotated[User | None, Depends(optional_user)]


async def current_user(user: OptionalUser) -> User:
    if user is None:
        raise UnauthorizedError("sign in first")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def choose_organisation(
    request: Request, organisations: list[crud.OrganisationRole]
) -> crud.OrganisationRole | None:
    """The organisation the session switched to, else the oldest membership."""
    if not organisations:
        return None
    wanted = request.session.get(SESSION_ORGANISATION)
    return next((o for o in organisations if o.organisation.id == wanted), organisations[0])


async def optional_access(
    request: Request, session: DbSession, user: OptionalUser
) -> Access | None:
    if user is None:
        return None
    chosen = choose_organisation(request, await crud.organisations_of(session, user))
    return Access(user, chosen.organisation, chosen.role) if chosen else None


OptionalAccess = Annotated[Access | None, Depends(optional_access)]


async def current_access(user: CurrentUser, access: OptionalAccess) -> Access:
    if access is None:
        raise ForbiddenError("create or join an organisation first", reason="no_organisation")
    return access


CurrentAccess = Annotated[Access, Depends(current_access)]


async def member_role(session: DbSession, user: User, organisation_id: str) -> Role:
    """The caller's role in an organisation named in the path; an organisation they are not in
    does not exist for them."""
    role = await crud.role_in(session, user, organisation_id)
    if role is None:
        raise NotFoundError(f"organisation {organisation_id} not found")
    return role


async def require_owner(session: DbSession, user: User, organisation_id: str) -> None:
    if await member_role(session, user, organisation_id) != "owner":
        raise ForbiddenError("only an owner of the organisation can do this", reason="not_owner")


def _bearer(request: Request) -> str | None:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


def presented_token(request: Request) -> str | None:
    """A token sent instead of a session: `Authorization: Bearer <token>`, else the token cookie."""
    return _bearer(request) or request.cookies.get(TOKEN_COOKIE) or None


def _same(given: str, wanted: str) -> bool:
    return bool(wanted) and secrets.compare_digest(given.encode(), wanted.encode())


def token_name(request: Request, token: str) -> TokenName | None:
    """Which token this is: the service token (headless browsers loading scene pages: this app's
    renderer, the GPU render service, the photo service), the read-only one (Claude's checks) or
    the agent one (Claude's jobs, #112). The agent token counts in the Authorization header only:
    as the cookie, a scene page's own script could have the browser send it with a write."""
    settings = get_settings()
    if _same(token, settings.AUTH_READONLY_TOKEN):
        return "readonly"
    if _same(token, settings.service_token):
        return "service"
    if _same(token, settings.agent_token) and _bearer(request) == token:
        return "agent"
    return None


def _agent_refused(request: Request, route: str | None) -> ForbiddenError:
    logger.warning(
        "auth.agent.denied",
        extra={
            "method": request.method,
            "route": route or request.url.path,
            "project_id": request.path_params.get("project_id"),
        },
    )
    return ForbiddenError("the agent token cannot do this", reason="agent_forbidden")


def require_token(request: Request, token: str, allowed: frozenset[TokenName]) -> TokenName:
    """The name of a token that may use this route; anything else is refused, and so is a write,
    but the agent token's through its allow-list."""
    name = token_name(request, token)
    if name == "agent":
        writes = request.method not in READ_METHODS
        if name not in allowed or (writes and not agent.may_write(request)):
            raise _agent_refused(request, agent.route_template(request))
        return name
    if name is None or name not in allowed:
        raise UnauthorizedError("this token is not valid here")
    if request.method not in READ_METHODS:
        raise ForbiddenError("a token can only read", reason="read_only")
    return name


async def agent_guard(request: Request) -> AsyncIterator[None]:
    """Every route of the app (#112): a write with the agent token goes through its allow-list or
    answers 403 `agent_forbidden`, whatever the route asks of other callers (the account and
    admin routes take a session). An allowed write logs the job it started."""
    bearer = _bearer(request)
    if request.method in READ_METHODS or bearer is None or token_name(request, bearer) != "agent":
        yield
        return
    route = agent.route_template(request)
    if (request.method, route) not in agent.AGENT_WRITES:
        raise _agent_refused(request, route)
    started: list[str] = []
    listening = jobs_created.set(started)
    done = False
    try:
        yield
        done = True
    finally:
        jobs_created.reset(listening)
        logger.info(
            "auth.agent.write",
            extra={
                "method": request.method,
                "route": route,
                "project_id": request.path_params.get("project_id"),
                "job_id": started[0] if started else request.path_params.get("job_id"),
                "done": done,
            },
        )


@dataclass(frozen=True)
class Caller:
    """Who calls a protected API route: a member's session, the agent token (#112: its own user,
    no membership, in AUTH_AGENT_ORGANISATION), or the read-only token (no user, every
    organisation, reads only)."""

    user: User | None
    organisation: Organisation | None

    @property
    def organisation_id(self) -> str | None:
        """The organisation whose projects the caller sees; None: all of them."""
        return self.organisation.id if self.organisation else None


async def agent_organisation(session: AsyncSession) -> Organisation:
    """The one organisation the agent token works in; 403 when that id names none."""
    organisation = await session.get(Organisation, get_settings().agent_organisation)
    if organisation is None:
        raise ForbiddenError(
            "the agent token's organisation does not exist", reason="no_organisation"
        )
    return organisation


async def _agent_caller(session: AsyncSession) -> Caller:
    organisation = await agent_organisation(session)
    user, created = await crud.agent_user(session)
    if created:
        await session.commit()
    return Caller(user=user, organisation=organisation)


async def api_caller(request: Request, session: DbSession) -> Caller:
    """Every /api/v1 route but the public ones: a session working in an organisation (401 signed
    out, 403 before the first organisation), the agent token in its organisation, or the
    read-only token on a GET or HEAD."""
    token = presented_token(request)
    if token is not None:
        if require_token(request, token, frozenset({"readonly", "agent"})) == "agent":
            return await _agent_caller(session)
        return Caller(user=None, organisation=None)
    user = await optional_user(request, session)
    if user is None:
        raise UnauthorizedError("sign in first")
    access = await optional_access(request, session, user)
    if access is None:
        raise ForbiddenError("create or join an organisation first", reason="no_organisation")
    return Caller(user=access.user, organisation=access.organisation)


ApiCaller = Annotated[Caller, Depends(api_caller)]


def caller_is_admin(user: User) -> bool:
    """The administrator (#108): the user whose provider-verified email is AUTH_ADMIN_EMAIL, as
    for the admin bootstrap; with AUTH_DISABLED, the implicit local user."""
    settings = get_settings()
    if user.issuer == LOCAL_ISSUER:
        return settings.AUTH_DISABLED
    return crud.is_admin(user, settings.AUTH_ADMIN_EMAIL)


async def admin_caller(request: Request, session: DbSession) -> User | None:
    """The admin routes (#108): the administrator's session, whatever organisation it works in
    (none needed), or the read-only token on a GET or HEAD (None). Anyone else answers 403
    `not_admin`."""
    token = presented_token(request)
    if token is not None:
        require_token(request, token, frozenset({"readonly"}))
        return None
    user = await optional_user(request, session)
    if user is None:
        raise UnauthorizedError("sign in first")
    if not caller_is_admin(user):
        raise ForbiddenError("only the administrator can do this", reason="not_admin")
    return user


AdminCaller = Annotated[User | None, Depends(admin_caller)]
