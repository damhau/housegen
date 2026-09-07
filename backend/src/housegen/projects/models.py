from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from housegen.core.db import Base, UTCDateTime


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> datetime:
    return datetime.now(UTC)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_uid)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="new")  # new|generating|ready|failed
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    plan_filename: Mapped[str | None] = mapped_column(String(300), nullable=True)
    plan_pages: Mapped[int] = mapped_column(Integer, default=0)
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    # what the files cannot say, from the owner: notes at upload + answers to the intake's
    # questions; given to the builder on every pass
    brief: Mapped[str | None] = mapped_column(Text, nullable=True)
    # the intake's reading of the plan set (agent.schemas.Intake + job_id) when there are no photos
    intake_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # agent.run_settings.RunSettings chosen by the owner (#18); unset fields = .env defaults
    settings_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def settings(self) -> dict[str, Any] | None:
        return dict(json.loads(self.settings_json)) if self.settings_json else None

    photos: Mapped[list[Photo]] = relationship(
        back_populates="project", cascade="all, delete-orphan", lazy="selectin"
    )
    plans: Mapped[list[PlanDocument]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="PlanDocument.number",
    )
    versions: Mapped[list[SceneVersion]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="SceneVersion.number",
    )


class Photo(Base):
    __tablename__ = "photos"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_uid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    side: Mapped[str] = mapped_column(String(20))  # north|south|east|west|other
    filename: Mapped[str] = mapped_column(String(300))
    original_name: Mapped[str] = mapped_column(String(300))

    project: Mapped[Project] = relationship(back_populates="photos")


class PlanDocument(Base):
    """One uploaded plan set (PDF) of a project (#10): the 1935 original, the 2024 survey…"""

    __tablename__ = "plan_documents"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_uid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    number: Mapped[int] = mapped_column(Integer)  # 1-based, upload order; plans/<number>/
    label: Mapped[str] = mapped_column(String(200), default="")
    original_name: Mapped[str] = mapped_column(String(300), default="")
    pages: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)

    project: Mapped[Project] = relationship(back_populates="plans")


class SceneVersion(Base):
    __tablename__ = "scene_versions"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_uid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    number: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))  # generation|critique|modification|restore
    label: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text, default="")
    critic_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    critique_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # from the builder's `finish`: optional additions it left out, questions for the owner
    suggestions_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    questions_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)

    project: Mapped[Project] = relationship(back_populates="versions")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_uid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))  # intake|generate|modify
    # queued|running|done|failed|interrupted (a server restart: resumed at the next startup, #7)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    request_text: Mapped[str] = mapped_column(Text, default="")
    # photos attached to a modification request (#8): file names under photos/, JSON list
    attachments_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # agent.metrics.RunSummary: time and tokens by phase and activity, cost (#13)
    metrics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # agent.run_settings.ResolvedRunSettings snapshot taken at start (#18)
    settings_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    @property
    def attachments(self) -> list[str]:
        return list(json.loads(self.attachments_json)) if self.attachments_json else []

    @property
    def metrics(self) -> dict[str, Any] | None:
        return dict(json.loads(self.metrics_json)) if self.metrics_json else None

    @property
    def settings(self) -> dict[str, Any] | None:
        return dict(json.loads(self.settings_json)) if self.settings_json else None


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(40))
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_uid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(10))  # user|assistant
    content: Mapped[str] = mapped_column(Text)
    job_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # photos the user attached to this message (#8): file names under photos/, JSON list
    attachments_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)

    @property
    def attachments(self) -> list[str]:
        return list(json.loads(self.attachments_json)) if self.attachments_json else []
