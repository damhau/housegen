from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx2
from authlib.integrations.starlette_client import OAuthError
from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse
from joserfc.errors import JoseError

from housegen.accounts import crud, emails
from housegen.accounts.dependencies import (
    SESSION_NEXT,
    SESSION_ORGANISATION,
    SESSION_USER,
    CurrentUser,
    OptionalUser,
    caller_is_admin,
    choose_organisation,
    member_role,
    require_owner,
)
from housegen.accounts.models import Invitation, Organisation, Role, User
from housegen.accounts.oidc import ProviderName, claims_options, configured_providers, identity
from housegen.accounts.schemas import (
    AuthOut,
    EmailOutcome,
    InvitationIn,
    InvitationOut,
    InvitationPreviewOut,
    MemberOut,
    MeOut,
    MyInvitationOut,
    OrganisationIn,
    OrganisationOut,
    SwitchOrganisationIn,
    UserOut,
)
from housegen.core.config import get_settings
from housegen.core.db import DbSession
from housegen.core.exceptions import ConflictError, NotFoundError, TooManyRequestsError
from housegen.limits import service as limits
from housegen.limits.schemas import LimitsOut

logger = logging.getLogger(__name__)

# browser navigations (redirects to and from the providers), not part of the API schema
auth_router = APIRouter(prefix="/auth", include_in_schema=False)
router = APIRouter(tags=["accounts"])


def _safe_next(path: str | None) -> str:
    """Only a path on this site: `next` must not turn sign-in into an open redirect. Browsers
    read a backslash as a slash and drop tabs and newlines, which would make `//host` of it."""
    if (
        not path
        or not path.startswith("/")
        or path.startswith("//")
        or "\\" in path
        or any(ord(c) < 32 for c in path)
        or len(path) > 2000
    ):
        return "/"
    return path


def _first(header: str | None) -> str:
    return header.split(",")[0].strip() if header else ""


def _public_url(request: Request) -> str:
    """This app's origin as browsers reach it: AUTH_PUBLIC_URL, else the request's own, from
    X-Forwarded-Proto/-Host behind a proxy."""
    base = get_settings().AUTH_PUBLIC_URL.rstrip("/")
    if base:
        return base
    proto = _first(request.headers.get("x-forwarded-proto")) or request.url.scheme
    host = (
        _first(request.headers.get("x-forwarded-host"))
        or request.headers.get("host")
        or request.url.netloc
    )
    return f"{proto}://{host}"


def _callback_url(request: Request, provider: str) -> str:
    return f"{_public_url(request)}/auth/callback/{provider}"


def _client(request: Request, provider: str) -> tuple[ProviderName, Any]:
    """A configured provider's name and its Authlib client."""
    for name in configured_providers(get_settings()):
        if name == provider:
            client = request.app.state.oauth.create_client(name)
            if client is not None:
                return name, client
    raise NotFoundError(f"no sign-in with '{provider}' here")


def _failed(reason: str) -> RedirectResponse:
    error = "cancelled" if reason == "access_denied" else "failed"
    return RedirectResponse(f"/signin?error={error}", status_code=303)


@auth_router.get("/login/{provider}")
async def login(
    request: Request, provider: str, next_path: Annotated[str, Query(alias="next")] = "/"
) -> RedirectResponse:
    """Off to the provider's sign-in page; back at /auth/callback/<provider>, then to `next`."""
    name, client = _client(request, provider)
    request.session[SESSION_NEXT] = _safe_next(next_path)
    logger.info("auth.login.started", extra={"provider": name})
    try:
        response: RedirectResponse = await client.authorize_redirect(
            request, _callback_url(request, name)
        )
    except httpx2.HTTPError as e:
        # the discovery document could not be fetched
        logger.warning(
            "auth.login.failed",
            extra={"provider": name, "error": type(e).__name__, "description": str(e)},
        )
        return _failed("provider_unreachable")
    return response


@auth_router.get("/callback/{provider}")
async def callback(request: Request, session: DbSession, provider: str) -> RedirectResponse:
    name, client = _client(request, provider)
    next_path = _safe_next(request.session.pop(SESSION_NEXT, "/"))
    try:
        token = await client.authorize_access_token(
            request, claims_options=await claims_options(name, client)
        )
    except OAuthError as e:
        logger.warning(
            "auth.callback.failed",
            extra={"provider": provider, "error": e.error, "description": e.description},
        )
        return _failed(str(e.error))
    except (JoseError, httpx2.HTTPError) as e:
        logger.warning(
            "auth.callback.failed",
            extra={"provider": provider, "error": type(e).__name__, "description": str(e)},
        )
        return _failed("invalid_token")
    claims = token.get("userinfo")
    if not claims:
        logger.warning("auth.callback.failed", extra={"provider": provider, "error": "no_id_token"})
        return _failed("no_id_token")

    user, created = await crud.upsert_user(session, identity(name, claims))
    await crud.claim_default_organisation(session, user, get_settings().AUTH_ADMIN_EMAIL)
    chosen = choose_organisation(request, await crud.organisations_of(session, user))
    await session.commit()
    request.session.clear()
    request.session[SESSION_USER] = user.id
    if chosen is not None:
        request.session[SESSION_ORGANISATION] = chosen.organisation.id
    logger.info(
        "auth.signed_in",
        extra={
            "provider": provider,
            "user_id": user.id,
            "new_user": created,
            "organisation_id": chosen.organisation.id if chosen else None,
        },
    )
    return RedirectResponse(next_path, status_code=303)


@auth_router.api_route("/logout", methods=["GET", "POST"])
async def logout(request: Request) -> RedirectResponse:
    user_id = request.session.get(SESSION_USER)
    request.session.clear()
    logger.info("auth.signed_out", extra={"user_id": user_id})
    return RedirectResponse("/", status_code=303)


def _organisation_out(organisation: Organisation, role: Role) -> OrganisationOut:
    return OrganisationOut(
        id=organisation.id, name=organisation.name, role=role, created_at=organisation.created_at
    )


async def _me(request: Request, session: DbSession, user: User | None) -> MeOut:
    settings = get_settings()
    auth = AuthOut(
        disabled=settings.AUTH_DISABLED,
        providers=configured_providers(settings),
        invitation_email=settings.email_enabled,
    )
    if user is None:
        return MeOut(user=None, organisation=None, organisations=[], auth=auth, is_admin=False)
    organisations = await crud.organisations_of(session, user)
    chosen = choose_organisation(request, organisations)
    await session.commit()  # the default organisation, when this was its first use
    return MeOut(
        user=UserOut.model_validate(user),
        organisation=_organisation_out(chosen.organisation, chosen.role) if chosen else None,
        organisations=[_organisation_out(o.organisation, o.role) for o in organisations],
        auth=auth,
        is_admin=caller_is_admin(user),
    )


@router.get("/me")
async def me(request: Request, session: DbSession, user: OptionalUser) -> MeOut:
    """Who is signed in. Always answers: `user` is null without a session, and `auth` lists the
    sign-in providers."""
    return await _me(request, session, user)


@router.put("/me/organisation")
async def switch_organisation(
    request: Request, session: DbSession, user: CurrentUser, body: SwitchOrganisationIn
) -> MeOut:
    """Work in another of one's organisations (kept in the session)."""
    await member_role(session, user, body.organisation_id)
    request.session[SESSION_ORGANISATION] = body.organisation_id
    logger.info(
        "accounts.organisation.switched",
        extra={"user_id": user.id, "organisation_id": body.organisation_id},
    )
    return await _me(request, session, user)


@router.get("/me/invitations")
async def my_invitations(session: DbSession, user: CurrentUser) -> list[MyInvitationOut]:
    """The pending invitations sent to the caller's email, only when the provider verified it
    (none otherwise), leaving out the organisations they are already in."""
    return [
        MyInvitationOut(
            id=r.invitation.id,
            organisation_id=r.invitation.organisation_id,
            organisation_name=r.organisation_name,
            invited_by=r.invited_by,
            token=r.invitation.token,
            expires_at=r.invitation.expires_at,
        )
        for r in await crud.invitations_for(session, user)
    ]


@router.post("/organisations", status_code=201)
async def create_organisation(
    request: Request, session: DbSession, user: CurrentUser, body: OrganisationIn
) -> OrganisationOut:
    """A new organisation, owned by its creator, who now works in it."""
    organisation = await crud.create_organisation(session, user, body.name.strip())
    await session.commit()
    request.session[SESSION_ORGANISATION] = organisation.id
    return _organisation_out(organisation, "owner")


@router.patch("/organisations/{organisation_id}")
async def rename_organisation(
    session: DbSession, user: CurrentUser, organisation_id: str, body: OrganisationIn
) -> OrganisationOut:
    await require_owner(session, user, organisation_id)
    organisation = await crud.get_organisation(session, organisation_id)
    organisation.name = body.name.strip()
    await session.commit()
    logger.info("accounts.organisation.renamed", extra={"organisation_id": organisation_id})
    return _organisation_out(organisation, "owner")


@router.get("/organisations/{organisation_id}/limits")
async def organisation_limits(
    session: DbSession, user: CurrentUser, organisation_id: str
) -> LimitsOut:
    """The organisation's spending limits and its use of them (members; only the administrator
    changes them): jobs at once, jobs started today (UTC), listing photos' spend this month."""
    await member_role(session, user, organisation_id)
    organisation = await crud.get_organisation(session, organisation_id)
    return await limits.organisation_limits(session, organisation)


@router.get("/organisations/{organisation_id}/members")
async def list_members(
    session: DbSession, user: CurrentUser, organisation_id: str
) -> list[MemberOut]:
    await member_role(session, user, organisation_id)
    return [
        MemberOut(
            user_id=u.id,
            name=u.name,
            email=u.email,
            picture=u.picture,
            role="owner" if m.role == "owner" else "member",
            joined_at=m.created_at,
        )
        for m, u in await crud.members(session, organisation_id)
    ]


@router.delete("/organisations/{organisation_id}/members/{user_id}", status_code=204)
async def remove_member(
    session: DbSession, user: CurrentUser, organisation_id: str, user_id: str
) -> None:
    """An owner removes a member; anyone may leave (their own user id). The last owner stays."""
    if user_id != user.id:
        await require_owner(session, user, organisation_id)
    else:
        await member_role(session, user, organisation_id)
    await crud.remove_member(session, organisation_id, user_id)
    await session.commit()
    logger.info(
        "accounts.member.removed",
        extra={"organisation_id": organisation_id, "user_id": user_id, "by": user.id},
    )


def _invitation_out(invitation: Invitation, outcome: emails.Outcome | None = None) -> InvitationOut:
    return InvitationOut(
        id=invitation.id,
        email=invitation.email,
        url=f"/invite/{invitation.token}",
        created_at=invitation.created_at,
        expires_at=invitation.expires_at,
        emailed_at=invitation.emailed_at,
        email_attempted_at=invitation.email_attempted_at,
        email_sent=EmailOutcome(sent=outcome.sent, error=outcome.error) if outcome else None,
    )


async def _email(
    request: Request, session: DbSession, invitation: Invitation, inviter: User
) -> emails.Outcome:
    """Send the invitation's email, the attempt committed first: a second "Send again" at once
    finds it and waits."""
    invitation.email_attempted_at = datetime.now(UTC)
    await session.commit()
    organisation = await crud.get_organisation(session, invitation.organisation_id)
    outcome = await emails.send_invitation(
        invitation,
        organisation,
        inviter,
        f"{_public_url(request)}/invite/{invitation.token}",
        invitation.email_attempted_at,
    )
    await session.commit()
    return outcome


@router.get("/organisations/{organisation_id}/invitations")
async def list_invitations(
    session: DbSession, user: CurrentUser, organisation_id: str
) -> list[InvitationOut]:
    """The invitations nobody has accepted yet, unexpired (owners only)."""
    await require_owner(session, user, organisation_id)
    return [_invitation_out(i) for i in await crud.pending_invitations(session, organisation_id)]


@router.post("/organisations/{organisation_id}/invitations", status_code=201)
async def create_invitation(
    request: Request,
    session: DbSession,
    user: CurrentUser,
    organisation_id: str,
    body: InvitationIn,
) -> InvitationOut:
    """An invitation link, emailed when this server sends email (`email_sent` says how that
    went: a failed send keeps the invitation, its link to copy). The link is what lets someone
    in, whatever account they sign in with; inviting the same address again gives the same link,
    and no second email ("Send again" does that)."""
    await require_owner(session, user, organisation_id)
    invitation, created = await crud.create_invitation(
        session, organisation_id, body.email, user, get_settings().AUTH_INVITATION_DAYS
    )
    await session.commit()
    outcome = None
    if created and get_settings().email_enabled:
        outcome = await _email(request, session, invitation, user)
    return _invitation_out(invitation, outcome)


@router.post("/organisations/{organisation_id}/invitations/{invitation_id}/send")
async def send_invitation(
    request: Request,
    session: DbSession,
    user: CurrentUser,
    organisation_id: str,
    invitation_id: str,
) -> InvitationOut:
    """Email a pending invitation again (owners only), from the owner who sends it: replies go to
    them. 409 when this server sends no email, 429 within a minute of the last email; a failed
    send answers 200 with `email_sent.error`."""
    await require_owner(session, user, organisation_id)
    settings = get_settings()
    if not settings.email_enabled:
        raise ConflictError("this server sends no email: copy the link", reason="email_off")
    invitation = await crud.pending_invitation(session, organisation_id, invitation_id)
    if invitation.email_attempted_at is not None:
        elapsed = (datetime.now(UTC) - invitation.email_attempted_at).total_seconds()
        wait = math.ceil(settings.EMAIL_RESEND_AFTER_S - elapsed)
        if wait > 0:
            raise TooManyRequestsError(
                f"Wait {wait} s before sending this invitation again.",
                reason="rate_limited",
                retry_after=wait,
            )
    return _invitation_out(invitation, await _email(request, session, invitation, user))


@router.delete("/organisations/{organisation_id}/invitations/{invitation_id}", status_code=204)
async def revoke_invitation(
    session: DbSession, user: CurrentUser, organisation_id: str, invitation_id: str
) -> None:
    await require_owner(session, user, organisation_id)
    await crud.revoke_invitation(session, organisation_id, invitation_id)
    await session.commit()


@router.get("/invitations/{token}")
async def get_invitation(session: DbSession, token: str) -> InvitationPreviewOut:
    """What an invitation link is for, signed in or not (the token is the secret)."""
    invitation = await crud.invitation_by_token(session, token)
    organisation = await crud.get_organisation(session, invitation.organisation_id)
    inviter = await session.get(User, invitation.invited_by) if invitation.invited_by else None
    return InvitationPreviewOut(
        organisation_name=organisation.name,
        invited_by=inviter.name if inviter else None,
        email=invitation.email,
        expires_at=invitation.expires_at,
        status=crud.invitation_status(invitation),
    )


@router.post("/invitations/{token}/accept")
async def accept_invitation(
    request: Request, session: DbSession, user: CurrentUser, token: str
) -> OrganisationOut:
    """Join the organisation of an invitation link while signed in; it becomes the one the
    session works in."""
    invitation = await crud.invitation_by_token(session, token)
    role = await crud.accept_invitation(session, invitation, user)
    organisation = await crud.get_organisation(session, invitation.organisation_id)
    await session.commit()
    request.session[SESSION_ORGANISATION] = organisation.id
    return _organisation_out(organisation, role)
