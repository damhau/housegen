"""Who is calling: the signed-in user (the session cookie) and the organisation they work in.

Nothing requires them yet (#104); #105 puts `CurrentAccess` on every project route."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from housegen.accounts import crud
from housegen.accounts.models import LOCAL_ISSUER, Organisation, Role, User
from housegen.core.config import get_settings
from housegen.core.db import DbSession
from housegen.core.exceptions import ForbiddenError, NotFoundError, UnauthorizedError

SESSION_USER = "uid"
SESSION_ORGANISATION = "org"
SESSION_NEXT = "next"


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
