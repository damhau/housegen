from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from housegen.accounts.models import Role
from housegen.limits.schemas import LimitsOut


class AdminMemberOut(BaseModel):
    user_id: str
    name: str
    email: str | None
    picture: str | None
    role: Role


class AdminOrganisationOut(BaseModel):
    id: str
    name: str
    # the one the startup migration made for the projects from before accounts
    is_default: bool
    created_at: datetime
    members: list[AdminMemberOut]  # oldest membership first; empty when nobody joined yet
    project_count: int
    limits: LimitsOut  # its spending limits and its use of them now (#107)


class AdminProjectOut(BaseModel):
    id: str
    name: str
    status: str
    # when its current state was reached: its newest version, else its creation
    updated_at: datetime
    organisation_id: str | None
    # the kind of the job at work on it (queued or running), none when idle
    job: str | None = None


class MoveProjectIn(BaseModel):
    organisation_id: str
