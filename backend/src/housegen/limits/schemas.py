from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

LimitName = Literal["concurrent_jobs", "jobs_per_day", "listing_usd_per_month"]
LIMIT_NAMES: tuple[LimitName, ...] = ("concurrent_jobs", "jobs_per_day", "listing_usd_per_month")


class LimitOut(BaseModel):
    # open jobs now (queued or running), jobs started today (UTC), or USD spent on listing photos
    # this month (a shoot in progress counts at its estimate)
    used: float
    limit: float | None  # what applies: the organisation's own, else the default; null: no limit
    # the organisation's own: a number, "unlimited", or null for the server's default
    override: float | Literal["unlimited"] | None
    default: float
    resets_at: datetime | None  # when `used` starts again from 0; null for open jobs


class LimitsOut(BaseModel):
    """An organisation's spending limits and how much of each it uses now."""

    concurrent_jobs: LimitOut
    jobs_per_day: LimitOut
    listing_usd_per_month: LimitOut
    # what a listing shoot must find left of the month, and holds until its cost is known
    listing_estimate_usd: float


class LimitsIn(BaseModel):
    """An organisation's own limits, each a number, "unlimited", or null for the server's default."""

    concurrent_jobs: Annotated[int, Field(ge=0)] | Literal["unlimited"] | None = None
    jobs_per_day: Annotated[int, Field(ge=0)] | Literal["unlimited"] | None = None
    listing_usd_per_month: Annotated[float, Field(ge=0)] | Literal["unlimited"] | None = None
