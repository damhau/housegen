from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from housegen.accounts.models import (
    AGENT_ISSUER,
    AGENT_NAME,
    LOCAL_ISSUER,
    Invitation,
    Membership,
    Organisation,
    Role,
    User,
)
from housegen.accounts.oidc import Identity
from housegen.core.exceptions import ConflictError, GoneError, NotFoundError
from housegen.limits.service import NO_LIMITS
from housegen.projects.models import Project

logger = logging.getLogger(__name__)

DEFAULT_ORGANISATION_NAME = "Default organisation"


@dataclass(frozen=True)
class OrganisationRole:
    organisation: Organisation
    role: Role


def _now() -> datetime:
    return datetime.now(UTC)


async def local_user(session: AsyncSession) -> tuple[User, bool]:
    """AUTH_DISABLED's implicit user (created on first use). Returns (user, created)."""
    res = await session.execute(
        select(User).where(User.issuer == LOCAL_ISSUER, User.subject == LOCAL_ISSUER)
    )
    user = res.scalar_one_or_none()
    if user is not None:
        return user, False
    user = User(provider="local", issuer=LOCAL_ISSUER, subject=LOCAL_ISSUER, name="Local user")
    session.add(user)
    await session.flush()
    logger.info("accounts.local_user.created", extra={"user_id": user.id})
    return user, True


async def agent_user(session: AsyncSession) -> tuple[User, bool]:
    """The agent token's user (#112), created on first use. Returns (user, created)."""
    res = await session.execute(
        select(User).where(User.issuer == AGENT_ISSUER, User.subject == AGENT_ISSUER)
    )
    user = res.scalar_one_or_none()
    if user is not None:
        return user, False
    user = User(provider="agent", issuer=AGENT_ISSUER, subject=AGENT_ISSUER, name=AGENT_NAME)
    session.add(user)
    await session.flush()
    logger.info("accounts.agent_user.created", extra={"user_id": user.id})
    return user, True


async def upsert_user(session: AsyncSession, identity: Identity) -> tuple[User, bool]:
    """The user of this (issuer, subject), created at the first sign-in; the profile (email,
    name, picture) follows what the provider says now. Returns (user, created)."""
    res = await session.execute(
        select(User).where(User.issuer == identity.issuer, User.subject == identity.subject)
    )
    user = res.scalar_one_or_none()
    created = user is None
    if user is None:
        user = User(provider=identity.provider, issuer=identity.issuer, subject=identity.subject)
        session.add(user)
    user.email = identity.email
    user.email_verified = identity.email_verified
    user.name = identity.name
    user.picture = identity.picture
    user.last_login_at = _now()
    await session.flush()
    return user, created


async def default_organisation(session: AsyncSession) -> tuple[Organisation, bool]:
    """The organisation of the projects from before accounts (made on first use, without
    limits). Returns (organisation, created)."""
    res = await session.execute(
        select(Organisation)
        .where(Organisation.is_default.is_(True))
        .order_by(Organisation.created_at)
    )
    organisation = res.scalars().first()
    if organisation is not None:
        return organisation, False
    organisation = Organisation(
        name=DEFAULT_ORGANISATION_NAME, is_default=True, limits_json=NO_LIMITS
    )
    session.add(organisation)
    await session.flush()
    return organisation, True


async def adopt_unassigned_projects(session: AsyncSession, organisation_id: str) -> int:
    """Give every project without an organisation to this one. Returns how many moved."""
    res = await session.execute(
        update(Project)
        .where(Project.organisation_id.is_(None))
        .values(organisation_id=organisation_id)
    )
    return int(getattr(res, "rowcount", 0) or 0)


def _has_admin_email(user: User, admin_email: str) -> bool:
    wanted = admin_email.strip().lower()
    return bool(wanted) and (user.email or "").strip().lower() == wanted


def is_admin(user: User, admin_email: str) -> bool:
    """The administrator (#108), and the admin bootstrap's rule: the user's email is
    AUTH_ADMIN_EMAIL, in any case, and the provider verified it."""
    return user.email_verified and _has_admin_email(user, admin_email)


async def claim_default_organisation(
    session: AsyncSession, user: User, admin_email: str
) -> Organisation | None:
    """The admin bootstrap: the first sign-in of AUTH_ADMIN_EMAIL, with an email the provider
    verified, owns the default organisation while it has no owner. Returns it when claimed."""
    if not _has_admin_email(user, admin_email):
        return None
    organisation, _ = await default_organisation(session)
    owners = await session.scalar(
        select(func.count())
        .select_from(Membership)
        .where(Membership.organisation_id == organisation.id, Membership.role == "owner")
    )
    if owners:
        return None
    if not is_admin(user, admin_email):
        logger.warning(
            "accounts.admin_bootstrap.unverified",
            extra={"user_id": user.id, "provider": user.provider},
        )
        return None
    await _add_member(session, organisation.id, user.id, "owner")
    logger.info(
        "accounts.admin_bootstrap.claimed",
        extra={"user_id": user.id, "organisation_id": organisation.id},
    )
    return organisation


async def _add_member(
    session: AsyncSession, organisation_id: str, user_id: str, role: Role
) -> Membership:
    membership = await _membership(session, organisation_id, user_id)
    if membership is None:
        membership = Membership(organisation_id=organisation_id, user_id=user_id, role=role)
        session.add(membership)
    elif role == "owner":
        membership.role = role
    await session.flush()
    return membership


async def _membership(
    session: AsyncSession, organisation_id: str, user_id: str
) -> Membership | None:
    res = await session.execute(
        select(Membership).where(
            Membership.organisation_id == organisation_id, Membership.user_id == user_id
        )
    )
    return res.scalar_one_or_none()


async def organisations_of(session: AsyncSession, user: User) -> list[OrganisationRole]:
    """The organisations a user belongs to, oldest membership first. The implicit local user
    owns the default organisation without a membership row, so that the admin bootstrap still
    finds it unowned once sign-in is turned on."""
    res = await session.execute(
        select(Organisation, Membership.role)
        .join(Membership, Membership.organisation_id == Organisation.id)
        .where(Membership.user_id == user.id)
        .order_by(Membership.created_at)
    )
    out = [OrganisationRole(org, _role(role)) for org, role in res.tuples().all()]
    if user.issuer == LOCAL_ISSUER and not any(o.organisation.is_default for o in out):
        organisation, _ = await default_organisation(session)
        out.insert(0, OrganisationRole(organisation, "owner"))
    return out


async def role_in(session: AsyncSession, user: User, organisation_id: str) -> Role | None:
    membership = await _membership(session, organisation_id, user.id)
    if membership is not None:
        return _role(membership.role)
    if user.issuer == LOCAL_ISSUER:
        organisation = await session.get(Organisation, organisation_id)
        if organisation is not None and organisation.is_default:
            return "owner"
    return None


def _role(value: str) -> Role:
    return "owner" if value == "owner" else "member"


async def get_organisation(session: AsyncSession, organisation_id: str) -> Organisation:
    organisation = await session.get(Organisation, organisation_id)
    if organisation is None:
        raise NotFoundError(f"organisation {organisation_id} not found")
    return organisation


async def create_organisation(session: AsyncSession, user: User, name: str) -> Organisation:
    organisation = Organisation(name=name, created_by=user.id)
    session.add(organisation)
    await session.flush()
    await _add_member(session, organisation.id, user.id, "owner")
    logger.info(
        "accounts.organisation.created",
        extra={"organisation_id": organisation.id, "user_id": user.id},
    )
    return organisation


async def members(session: AsyncSession, organisation_id: str) -> list[tuple[Membership, User]]:
    res = await session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.organisation_id == organisation_id)
        .order_by(Membership.created_at)
    )
    return list(res.tuples().all())


async def remove_member(session: AsyncSession, organisation_id: str, user_id: str) -> None:
    """Remove someone from an organisation; its last owner cannot go."""
    membership = await _membership(session, organisation_id, user_id)
    if membership is None:
        raise NotFoundError(f"user {user_id} is not a member of {organisation_id}")
    if membership.role == "owner":
        owners = await session.scalar(
            select(func.count())
            .select_from(Membership)
            .where(Membership.organisation_id == organisation_id, Membership.role == "owner")
        )
        if (owners or 0) <= 1:
            raise ConflictError("the last owner cannot leave the organisation")
    await session.delete(membership)
    await session.flush()


InvitationStatus = Literal["pending", "accepted", "expired"]


def invitation_status(invitation: Invitation) -> InvitationStatus:
    if invitation.accepted_at is not None:
        return "accepted"
    if invitation.expires_at <= _now():
        return "expired"
    return "pending"


async def create_invitation(
    session: AsyncSession, organisation_id: str, email: str, invited_by: User, days: int
) -> tuple[Invitation, bool]:
    """An invitation for this email, or the one still pending for it (inviting twice gives the
    same link). Returns (invitation, created)."""
    email = email.strip().lower()
    for invitation in await pending_invitations(session, organisation_id):
        if invitation.email == email:
            return invitation, False
    invitation = Invitation(
        organisation_id=organisation_id,
        email=email,
        token=secrets.token_urlsafe(32),
        invited_by=invited_by.id,
        expires_at=_now() + timedelta(days=days),
    )
    session.add(invitation)
    await session.flush()
    logger.info(
        "accounts.invitation.created",
        extra={"organisation_id": organisation_id, "invitation_id": invitation.id},
    )
    return invitation, True


async def pending_invitation(
    session: AsyncSession, organisation_id: str, invitation_id: str
) -> Invitation:
    """One of the organisation's invitations that can still be accepted."""
    invitation = await session.get(Invitation, invitation_id)
    if invitation is None or invitation.organisation_id != organisation_id:
        raise NotFoundError(f"invitation {invitation_id} not found")
    status = invitation_status(invitation)
    if status == "accepted":
        raise ConflictError("the invitation was already accepted")
    if status == "expired":
        raise GoneError("this invitation has expired", reason="expired")
    return invitation


async def pending_invitations(session: AsyncSession, organisation_id: str) -> list[Invitation]:
    res = await session.execute(
        select(Invitation)
        .where(
            Invitation.organisation_id == organisation_id,
            Invitation.accepted_at.is_(None),
            Invitation.expires_at > _now(),
        )
        .order_by(Invitation.created_at)
    )
    return list(res.scalars().all())


@dataclass(frozen=True)
class ReceivedInvitation:
    invitation: Invitation
    organisation_name: str
    invited_by: str | None  # the inviter's name


async def invitations_for(session: AsyncSession, user: User) -> list[ReceivedInvitation]:
    """The pending invitations sent to the user's email, when the provider verified that email:
    whoever reads that mailbox would get the links anyway. Leaves out the organisations the user
    is already in."""
    if not user.email or not user.email_verified:
        return []
    already = select(Membership.organisation_id).where(Membership.user_id == user.id)
    inviter = aliased(User)
    res = await session.execute(
        select(Invitation, Organisation.name, inviter.name)
        .join(Organisation, Organisation.id == Invitation.organisation_id)
        .outerjoin(inviter, inviter.id == Invitation.invited_by)
        .where(
            func.lower(Invitation.email) == user.email.strip().lower(),
            Invitation.accepted_at.is_(None),
            Invitation.expires_at > _now(),
            Invitation.organisation_id.not_in(already),
        )
        .order_by(Invitation.created_at)
    )
    return [ReceivedInvitation(i, org, by) for i, org, by in res.tuples().all()]


async def revoke_invitation(
    session: AsyncSession, organisation_id: str, invitation_id: str
) -> None:
    invitation = await session.get(Invitation, invitation_id)
    if invitation is None or invitation.organisation_id != organisation_id:
        raise NotFoundError(f"invitation {invitation_id} not found")
    if invitation.accepted_at is not None:
        raise ConflictError("the invitation was already accepted")
    await session.delete(invitation)
    await session.flush()
    logger.info(
        "accounts.invitation.revoked",
        extra={"organisation_id": organisation_id, "invitation_id": invitation_id},
    )


async def invitation_by_token(session: AsyncSession, token: str) -> Invitation:
    res = await session.execute(select(Invitation).where(Invitation.token == token))
    invitation = res.scalar_one_or_none()
    if invitation is None:
        raise NotFoundError("invitation not found")
    return invitation


async def accept_invitation(session: AsyncSession, invitation: Invitation, user: User) -> Role:
    """Join the invitation's organisation through its link. A link works once and until it
    expires; whose email it was sent to does not matter. Returns the user's role there."""
    if invitation.accepted_at is not None:
        if invitation.accepted_by == user.id:
            role = await role_in(session, user, invitation.organisation_id)
            if role is not None:
                return role
        raise GoneError("this invitation was already used", reason="accepted")
    if invitation.expires_at <= _now():
        raise GoneError("this invitation has expired", reason="expired")
    role = await role_in(session, user, invitation.organisation_id)
    if role is None:
        await _add_member(session, invitation.organisation_id, user.id, "member")
        role = "member"
    invitation.accepted_at = _now()
    invitation.accepted_by = user.id
    await session.flush()
    logger.info(
        "accounts.invitation.accepted",
        extra={
            "organisation_id": invitation.organisation_id,
            "invitation_id": invitation.id,
            "user_id": user.id,
        },
    )
    return role
