from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# how much of the furnishing the rooms show (#94): dressed and staged, the furniture without the decor, the
# architecture alone (finishes, fitted kitchen and bathrooms, fixed lights)
Staging = Literal["furnished", "light", "empty"]
# the set's photos from outside (#97): the house in its surroundings (the project's context), the house alone on
# its plot with its own garden, or none (interior photos only)
Exteriors = Literal["surroundings", "house", "none"]


class ListingPhotoOut(BaseModel):
    key: str
    unit: str | None
    title: str
    caption: str
    use: str
    area: float
    url: str | None  # None until rendered, or when it failed
    error: str | None = None
    why: str | None = None  # why the picture editor chose this viewpoint (#92)


class ListingOut(BaseModel):
    version: int
    status: str  # running | done | partial
    out_of_date: bool  # a later version exists: the set shows an older state of the project
    created_at: str
    width: int
    height: int
    cost_usd: float
    staging: Staging = "furnished"
    exteriors: Exteriors = "surroundings"
    previews_done: int = 0  # viewpoints previewed so far for the picture editor (#92)
    previews_total: int = 0  # 0: the set was shot without previews
    photos: list[ListingPhotoOut]  # in listing order, grouped by unit


class ListingStatusOut(BaseModel):
    available: bool  # a photo service is configured
    surroundings: bool = False  # the project has surroundings: a set can show the house in them
    sets: list[int]  # the versions that have a set, newest first
    listing: ListingOut | None  # the asked version's set, else the newest


class ListingRequest(BaseModel):
    version: int | None = Field(
        None, description="the saved version to photograph (default: the current one)"
    )
    reuse: bool = Field(False, description="shoot again from the cameras of the version's last set")
    staging: Staging | None = Field(
        None,
        description="furnished (dressed and staged), light (the furniture without the decor) or empty (the "
        "architecture, its finishes and light); default: the set's own when shot again, else furnished",
    )
    exteriors: Exteriors | None = Field(
        None,
        description="surroundings (the house in the project's surroundings), house (alone on its plot) or none "
        "(interior photos only); default: the set's own when shot again, else surroundings when the project has "
        "them, house without",
    )
