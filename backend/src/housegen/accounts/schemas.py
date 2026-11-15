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
    """How people sign in here: the provider buttons to show (none while disabled)."""

    disabled: bool
    providers: list[ProviderName]


class MeOut(BaseModel):
    """The session: who is signed in (null when nobody is), their organisations and the one they
    work in now."""

    user: UserOut | None
    organisation: OrganisationOut | None
    organisations: list[OrganisationOut]
    auth: AuthOut


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


class InvitationOut(BaseModel):
    id: str
    email: str
    url: str  # the link to send: /invite/<token>
    created_at: datetime
    expires_at: datetime


class InvitationPreviewOut(BaseModel):
    """What an invitation link shows before it is accepted."""

    organisation_name: str
    invited_by: str | None
    email: str
    expires_at: datetime
    status: Literal["pending", "accepted", "expired"]
