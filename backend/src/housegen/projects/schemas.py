from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Side = Literal["north", "south", "east", "west", "other"]
STANDARD_VIEWS = ["north", "south", "east", "west", "aerial"]


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
    created_at: datetime
    scene_url: str
    render_urls: dict[str, str]


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    status: str
    created_at: datetime
    plan_pages: int
    current_version: int
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


class ModifyRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    role: str
    content: str
    job_id: str | None
    version_number: int | None
    created_at: datetime


class SceneFileOut(BaseModel):
    path: str
    content: str


class SceneFilesOut(BaseModel):
    version: int
    files: list[SceneFileOut]
