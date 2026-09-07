from __future__ import annotations

import uuid
from datetime import UTC, datetime

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

    photos: Mapped[list[Photo]] = relationship(
        back_populates="project", cascade="all, delete-orphan", lazy="selectin"
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
    kind: Mapped[str] = mapped_column(String(20))  # generate|modify
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|done|failed
    request_text: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


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
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
