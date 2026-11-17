from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from housegen.core.db import Base, UTCDateTime

Role = Literal["owner", "member"]
# the implicit user of AUTH_DISABLED: no provider can mint this issuer, and a session naming
# that user is ignored once sign-in is on
LOCAL_ISSUER = "local"
# the agent token's user (#112): no provider mints this issuer either, a session naming it is
# ignored, and it has no membership, so no member list or admin view shows it
AGENT_ISSUER = "agent"
AGENT_NAME = "Claude (agent token)"


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    """Someone who signed in. The identity is (issuer, subject), never the email: a tenant admin
    can set any email on a Microsoft account (the nOAuth class of problem)."""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("issuer", "subject", name="uq_users_identity"),)

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_uid)
    provider: Mapped[str] = mapped_column(String(20))  # google|microsoft|local
    issuer: Mapped[str] = mapped_column(String(300))
    subject: Mapped[str] = mapped_column(String(300))
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    name: Mapped[str] = mapped_column(String(200), default="")
    picture: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Organisation(Base):
    __tablename__ = "organisations"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_uid)
    name: Mapped[str] = mapped_column(String(200))
    # the one the startup migration made for the projects from before accounts: AUTH_DISABLED's
    # organisation, owned by the first verified sign-in of AUTH_ADMIN_EMAIL
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    # its own spending limits (#107), JSON: a limit left out is the server's default, null lifts
    # it. Null for the whole column only until the startup migration or the admin first set them
    limits_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def limits(self) -> dict[str, float | None]:
        return dict(json.loads(self.limits_json)) if self.limits_json else {}


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "organisation_id", name="uq_memberships_user_organisation"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    organisation_id: Mapped[str] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(10), default="member")  # Role
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class Invitation(Base):
    """A link to join an organisation. The token is what grants it; the email only says whom it
    was meant for."""

    __tablename__ = "invitations"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_uid)
    organisation_id: Mapped[str] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(320))
    token: Mapped[str] = mapped_column(String(64), unique=True)
    invited_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    accepted_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # its email (#107): the last attempt (what "Send again" waits on) and the last one Resend took
    email_attempted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    emailed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
