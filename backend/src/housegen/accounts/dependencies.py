"""Who is calling: the signed-in user (the session cookie) and the organisation they work in, or a
token presented instead of a session (#105)."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, Request

from housegen.accounts import crud
from housegen.accounts.models import LOCAL_ISSUER, Organisation, Role, User
from housegen.core.config import TOKEN_COOKIE, get_settings
from housegen.core.db import DbSession
from housegen.core.exceptions import ForbiddenError, NotFoundError, UnauthorizedError

SESSION_USER = "uid"
SESSION_ORGANISATION = "org"
SESSION_NEXT = "next"
READ_METHODS = frozenset({"GET", "HEAD"})

TokenName = Literal["service", "readonly"]


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
    if signed_in is None or signed_in.issuer == LOCAL_ISSUER:
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


def presented_token(request: Request) -> str | None:
    """A token sent instead of a session: `Authorization: Bearer <token>`, else the token cookie."""
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return request.cookies.get(TOKEN_COOKIE) or None


def _same(given: str, wanted: str) -> bool:
    return bool(wanted) and secrets.compare_digest(given.encode(), wanted.encode())


def token_name(token: str) -> TokenName | None:
    """Which token this is: the service token (headless browsers loading scene pages: this app's
    renderer, the GPU render service, the photo service) or the read-only one (Claude's checks)."""
    settings = get_settings()
    if _same(token, settings.AUTH_READONLY_TOKEN):
        return "readonly"
    if _same(token, settings.service_token):
        return "service"
    return None


def require_token(request: Request, token: str, allowed: frozenset[TokenName]) -> TokenName:
    """The name of a token that may read here; anything else is refused, and so is a write."""
    name = token_name(token)
    if name is None or name not in allowed:
        raise UnauthorizedError("this token is not valid here")
    if request.method not in READ_METHODS:
        raise ForbiddenError("a token can only read", reason="read_only")
    return name


@dataclass(frozen=True)
class Caller:
    """Who calls a protected API route: a member working in an organisation, or the read-only
    token (`access` None: every organisation, reads only)."""

    access: Access | None

    @property
    def organisation_id(self) -> str | None:
        """The organisation whose projects the caller sees; None: all of them."""
        return self.access.organisation.id if self.access else None


async def api_caller(request: Request, session: DbSession) -> Caller:
    """Every /api/v1 route but the public ones: a session working in an organisation (401 signed
    out, 403 before the first organisation), or the read-only token on a GET or HEAD."""
    token = presented_token(request)
    if token is not None:
        require_token(request, token, frozenset({"readonly"}))
        return Caller(access=None)
    user = await optional_user(request, session)
    if user is None:
        raise UnauthorizedError("sign in first")
    access = await optional_access(request, session, user)
    if access is None:
        raise ForbiddenError("create or join an organisation first", reason="no_organisation")
    return Caller(access=access)


ApiCaller = Annotated[Caller, Depends(api_caller)]
