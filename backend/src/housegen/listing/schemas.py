from __future__ import annotations

from pydantic import BaseModel, Field


class ListingPhotoOut(BaseModel):
    key: str
    unit: str | None
    title: str
    caption: str
    use: str
    area: float
    url: str | None  # None until rendered, or when it failed
    error: str | None = None


class ListingOut(BaseModel):
    version: int
    status: str  # running | done | partial
    out_of_date: bool  # a later version exists: the set shows an older state of the project
    created_at: str
    width: int
    height: int
    cost_usd: float
    photos: list[ListingPhotoOut]  # in listing order, grouped by unit


class ListingStatusOut(BaseModel):
    available: bool  # a photo service is configured
    sets: list[int]  # the versions that have a set, newest first
    listing: ListingOut | None  # the asked version's set, else the newest


class ListingRequest(BaseModel):
    version: int | None = Field(
        None, description="the saved version to photograph (default: the current one)"
    )
    reuse: bool = Field(False, description="shoot again from the cameras of the version's last set")
