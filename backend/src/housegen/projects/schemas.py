from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from housegen.agent.schemas import Critique, Intake

Side = Literal["north", "south", "east", "west", "other"]
# elevated wide shots (massing, roof) + photo-like eye-level views (what the critic pairs with the photos)
STANDARD_VIEWS = [
    "north",
    "south",
    "east",
    "west",
    "aerial",
    "north-photo",
    "south-photo",
    "east-photo",
    "west-photo",
]
# no photographs: straight-on elevation views instead, paired with the elevation drawings
PLAN_ONLY_VIEWS = [
    "north",
    "south",
    "east",
    "west",
    "aerial",
    "north-elevation",
    "south-elevation",
    "east-elevation",
    "west-elevation",
]


def standard_views(has_photos: bool) -> list[str]:
    """The views rendered for every saved version of a project."""
    return list(STANDARD_VIEWS if has_photos else PLAN_ONLY_VIEWS)


class PhotoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    side: Side
    original_name: str
    url: str


class SceneVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    number: int
    kind: str
    label: str
    summary: str
    critic_score: int | None
    critique: Critique | None
    # the builder's optional additions and questions for the owner (from `finish`)
    suggestions: list[str] = []
    questions: list[str] = []
    created_at: datetime
    scene_url: str
    render_urls: dict[str, str]


class IntakeOut(Intake):
    """The intake's reading of the plan set, with the job that produced it."""

    job_id: str


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    status: str
    created_at: datetime
    plan_pages: int
    current_version: int
    # the owner's notes and intake answers, given to the builder on every pass
    brief: str | None
    # present when the plans were read before building (projects without photos)
    intake: IntakeOut | None
    photos: list[PhotoOut]
    versions: list[SceneVersionOut]
    scene_url: str
    plan_page_urls: list[str]


class ProjectSummaryOut(BaseModel):
    id: str
    name: str
    status: str
    created_at: datetime
    current_version: int
    thumbnail_url: str | None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str
    kind: str
    status: str
    request_text: str
    error: str | None
    result_version: int | None
    created_at: datetime
    finished_at: datetime | None


class JobEventOut(BaseModel):
    seq: int
    type: str
    payload: dict[str, Any]
    created_at: datetime


# (the modify request is multipart since #8: `message` + `photos[]` + `keep`, see the router)


class IntakeAnswer(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    answer: str = Field(max_length=2000)


class GenerateRequest(BaseModel):
    """Optional: the owner's answers to the intake's questions (and anything else to say),
    appended to the project's brief before the build starts."""

    answers: list[IntakeAnswer] = Field(default_factory=list, max_length=12)
    notes: str = Field(default="", max_length=4000)

    def as_text(self) -> str:
        lines = [f"Q: {a.question}\nA: {a.answer.strip() or '(no answer)'}" for a in self.answers]
        if self.notes.strip():
            lines.append(self.notes.strip())
        return "\n\n".join(lines)


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    role: str
    content: str
    job_id: str | None
    version_number: int | None
    # photos attached to a modification request (#8), as URLs
    attachments: list[str] = []
    created_at: datetime


class SceneFileOut(BaseModel):
    path: str
    content: str


class SceneFilesOut(BaseModel):
    version: int
    files: list[SceneFileOut]
