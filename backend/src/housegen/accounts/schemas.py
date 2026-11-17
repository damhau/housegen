from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from housegen.accounts.models import Role
from housegen.accounts.oidc import ProviderName


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    provider: str  # google|microsoft|local
    name: str
    email: str | None
    email_verified: bool
    picture: str | None


class OrganisationOut(BaseModel):
    id: str
    name: str
    role: Role  # the caller's
    created_at: datetime


class AuthOut(BaseModel):
    """How people sign in here: the provider buttons to show (none while disabled), and whether
    invitations go out by email (else: a link to copy)."""

    disabled: bool
    providers: list[ProviderName]
    invitation_email: bool = False


class MeOut(BaseModel):
    """The session: who is signed in (null when nobody is), their organisations and the one they
    work in now."""

    user: UserOut | None
    organisation: OrganisationOut | None
    organisations: list[OrganisationOut]
    auth: AuthOut
    # the administrator (#108): every organisation at /admin
    is_admin: bool


class SwitchOrganisationIn(BaseModel):
    organisation_id: str


class OrganisationIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class MemberOut(BaseModel):
    user_id: str
    name: str
    email: str | None
    picture: str | None
    role: Role
    joined_at: datetime


class InvitationIn(BaseModel):
    email: str = Field(max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EmailOutcome(BaseModel):
    """This request's invitation email."""

    sent: bool
    error: str | None = None  # why Resend did not take it


class InvitationOut(BaseModel):
    id: str
    email: str
    url: str  # the link to send: /invite/<token>
    created_at: datetime
    expires_at: datetime
    emailed_at: datetime | None = None  # the last time its email went out
    # the last time housegen tried to email it: later than emailed_at, that try failed
    email_attempted_at: datetime | None = None
    # the email this request sent (creating it, or "Send again"); null when none was sent
    email_sent: EmailOutcome | None = None


class MyInvitationOut(BaseModel):
    """An invitation sent to the signed-in user's verified email: the first-login wizard's Join
    list. Joining goes through its token, as the link does."""

    id: str
    organisation_id: str
    organisation_name: str
    invited_by: str | None
    token: str
    expires_at: datetime


class InvitationPreviewOut(BaseModel):
    """What an invitation link shows before it is accepted."""

    organisation_name: str
    invited_by: str | None
    email: str
    expires_at: datetime
    status: Literal["pending", "accepted", "expired"]
