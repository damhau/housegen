"""Spending limits per organisation (#107): jobs at once, jobs started per UTC day, and the listing
photos' spend per calendar month. A job over a limit is refused before it is created (429)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from housegen.accounts.models import Organisation
from housegen.core.config import Settings, get_settings
from housegen.core.exceptions import TooManyRequestsError
from housegen.limits.models import UsageRecord
from housegen.limits.schemas import LIMIT_NAMES, LimitName, LimitOut, LimitsIn, LimitsOut
from housegen.projects.crud import UNFINISHED
from housegen.projects.models import Job, Project

logger = logging.getLogger(__name__)

# the default organisation's (the projects from before accounts): every limit lifted
NO_LIMITS = json.dumps(dict.fromkeys(LIMIT_NAMES))
# one process serves the app: holding this from the check to the commit keeps two requests from
# both taking an organisation's last place
_ADMISSION = asyncio.Lock()


def _now() -> datetime:
    return datetime.now(UTC)


def _day_start(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _month_start(now: datetime) -> datetime:
    return _day_start(now).replace(day=1)


def _next_month(now: datetime) -> datetime:
    start = _month_start(now)
    return (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )


def defaults(settings: Settings) -> dict[LimitName, float]:
    return {
        "concurrent_jobs": settings.LIMIT_CONCURRENT_JOBS,
        "jobs_per_day": settings.LIMIT_JOBS_PER_DAY,
        "listing_usd_per_month": settings.LIMIT_LISTING_USD_PER_MONTH,
    }


def effective(organisation: Organisation, name: LimitName, settings: Settings) -> float | None:
    """The limit that applies (None: no limit): the organisation's own, else the default."""
    own = organisation.limits
    return own[name] if name in own else defaults(settings)[name]


@dataclass(frozen=True)
class Usage:
    concurrent_jobs: int
    jobs_per_day: int
    listing_usd_per_month: float

    def of(self, name: LimitName) -> float:
        return float(getattr(self, name))


async def usage(
    session: AsyncSession,
    organisation_id: str,
    now: datetime | None = None,
    replacing: str | None = None,
) -> Usage:
    """What the organisation uses now. `replacing`: a job about to end that starts this one (a
    chat turn handing its change over), left out of the jobs at once."""
    now = now or _now()
    open_jobs = (
        select(func.count())
        .select_from(Job)
        .join(Project, Project.id == Job.project_id)
        .where(Project.organisation_id == organisation_id, Job.status.in_(UNFINISHED))
    )
    if replacing is not None:
        open_jobs = open_jobs.where(Job.id != replacing)
    today = (
        select(func.count())
        .select_from(UsageRecord)
        .where(
            UsageRecord.organisation_id == organisation_id,
            UsageRecord.created_at >= _day_start(now),
            UsageRecord.created_at < _day_start(now) + timedelta(days=1),
        )
    )
    spent = select(func.coalesce(func.sum(UsageRecord.listing_usd), 0.0)).where(
        UsageRecord.organisation_id == organisation_id,
        UsageRecord.kind == "listing",
        UsageRecord.created_at >= _month_start(now),
        UsageRecord.created_at < _next_month(now),
    )
    return Usage(
        concurrent_jobs=int(await session.scalar(open_jobs) or 0),
        jobs_per_day=int(await session.scalar(today) or 0),
        listing_usd_per_month=round(float(await session.scalar(spent) or 0.0), 2),
    )


def _resets_at(name: LimitName, now: datetime) -> datetime | None:
    if name == "jobs_per_day":
        return _day_start(now) + timedelta(days=1)
    if name == "listing_usd_per_month":
        return _next_month(now)
    return None


def limits_out(organisation: Organisation, used: Usage, now: datetime | None = None) -> LimitsOut:
    now = now or _now()
    settings = get_settings()
    own = organisation.limits

    def one(name: LimitName) -> LimitOut:
        value = own.get(name)
        return LimitOut(
            used=used.of(name),
            limit=effective(organisation, name, settings),
            override=None if name not in own else "unlimited" if value is None else value,
            default=defaults(settings)[name],
            resets_at=_resets_at(name, now),
        )

    return LimitsOut(
        concurrent_jobs=one("concurrent_jobs"),
        jobs_per_day=one("jobs_per_day"),
        listing_usd_per_month=one("listing_usd_per_month"),
        listing_estimate_usd=settings.LISTING_ESTIMATE_USD,
    )


async def organisation_limits(session: AsyncSession, organisation: Organisation) -> LimitsOut:
    now = _now()
    return limits_out(organisation, await usage(session, organisation.id, now), now)


async def set_limits(session: AsyncSession, organisation: Organisation, body: LimitsIn) -> None:
    """The organisation's own limits: a number, None (lifted, from "unlimited"), or left out (the
    default). Never stored as a null column, which the startup migration would take for unset."""
    own: dict[str, float | None] = {}
    for name in LIMIT_NAMES:
        value = getattr(body, name)
        if value == "unlimited":
            own[name] = None
        elif value is not None:
            own[name] = value
    organisation.limits_json = json.dumps(own)
    await session.flush()


def _count(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _until(then: datetime, now: datetime) -> str:
    minutes = max(1, round((then - now).total_seconds() / 60))
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"


def _refused(
    name: LimitName, used: float, limit: float, now: datetime, estimate: float = 0.0
) -> TooManyRequestsError:
    resets_at = _resets_at(name, now)
    if limit == 0:
        label = {
            "concurrent_jobs": "jobs at once",
            "jobs_per_day": "jobs per day",
            "listing_usd_per_month": "listing photos per month",
        }[name]
        message = (
            f"Limit reached: {label}. This organisation's limit is "
            f"{'$0.00' if name == 'listing_usd_per_month' else '0'}: ask the administrator of "
            "this server to raise it."
        )
    elif name == "concurrent_jobs":
        message = (
            f"Limit reached: jobs at once. This organisation has {_count(used)} jobs queued or "
            f"running, its limit is {_count(limit)}. Start this one when one of them ends."
        )
    elif name == "jobs_per_day":
        assert resets_at is not None
        message = (
            f"Limit reached: jobs per day. This organisation started {_count(used)} jobs today, "
            f"its limit is {_count(limit)}. The count starts again at midnight UTC, in "
            f"{_until(resets_at, now)}."
        )
    else:
        assert resets_at is not None
        message = (
            f"Limit reached: listing photos per month. This organisation spent ${used:.2f} of "
            f"its ${limit:.2f} this month, and a set needs about ${estimate:.2f}. The budget "
            f"starts again on {resets_at.day} {resets_at:%B} (UTC)."
        )
    return TooManyRequestsError(
        message,
        reason="limit",
        limit=name,
        used=used,
        allowed=limit,
        resets_at=resets_at.isoformat() if resets_at else None,
    )


async def check(
    session: AsyncSession,
    organisation_id: str,
    kind: str,
    replacing: str | None = None,
    waiting: bool = False,
) -> None:
    """Refuse (429) a job of `kind` the organisation's limits leave no room for. A change that
    waits for the project's running job (#35) is not one more job at once."""
    organisation = await session.get(Organisation, organisation_id)
    if organisation is None:
        return
    settings = get_settings()
    now = _now()
    used = await usage(session, organisation_id, now, replacing)
    refused: TooManyRequestsError | None = None
    at_once = effective(organisation, "concurrent_jobs", settings)
    per_day = effective(organisation, "jobs_per_day", settings)
    per_month = effective(organisation, "listing_usd_per_month", settings)
    estimate = settings.LISTING_ESTIMATE_USD
    if not waiting and at_once is not None and used.concurrent_jobs >= at_once:
        refused = _refused("concurrent_jobs", used.concurrent_jobs, at_once, now)
    elif per_day is not None and used.jobs_per_day >= per_day:
        refused = _refused("jobs_per_day", used.jobs_per_day, per_day, now)
    elif (
        kind == "listing"
        and per_month is not None
        and used.listing_usd_per_month + estimate > per_month + 1e-9
    ):
        refused = _refused(
            "listing_usd_per_month", used.listing_usd_per_month, per_month, now, estimate
        )
    if refused is not None:
        logger.info(
            "limits.refused",
            extra={
                "organisation_id": organisation_id,
                "kind": kind,
                "limit": refused.details["limit"],
                "used": refused.details["used"],
                "allowed": refused.details["allowed"],
            },
        )
        raise refused


class Admission:
    """A job the limits let in: record it once created, before the commit."""

    def __init__(self, session: AsyncSession, organisation_id: str | None, kind: str) -> None:
        self._session = session
        self._organisation_id = organisation_id
        self._kind = kind

    def record(self, job: Job) -> None:
        if self._organisation_id is None:
            return
        self._session.add(
            UsageRecord(
                job_id=job.id,
                organisation_id=self._organisation_id,
                kind=self._kind,
                created_at=_now(),
                listing_usd=get_settings().LISTING_ESTIMATE_USD
                if self._kind == "listing"
                else None,
            )
        )


@asynccontextmanager
async def admission(
    session: AsyncSession,
    organisation_id: str | None,
    kind: str,
    replacing: str | None = None,
    waiting: bool = False,
) -> AsyncIterator[Admission]:
    """Every job-creating path goes through here: the limits check (429 when one is reached),
    held until the caller has created the job, recorded it and committed."""
    async with _ADMISSION:
        if organisation_id is not None:
            await check(session, organisation_id, kind, replacing, waiting)
        yield Admission(session, organisation_id, kind)


async def record_listing_spend(session: AsyncSession, job_id: str, usd: float) -> None:
    """A listing shoot's cost once it is over, in place of its estimate."""
    await session.execute(
        update(UsageRecord).where(UsageRecord.job_id == job_id).values(listing_usd=round(usd, 4))
    )
