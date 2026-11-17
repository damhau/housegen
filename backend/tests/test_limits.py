"""Spending limits per organisation (#107): jobs at once, jobs per UTC day, listing spend per
calendar month; a job over a limit answers 429 and is not created. The admin routes and the
read-only token are in test_admin.py, the chat's hand-over in test_chat.py."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from housegen.accounts import crud as accounts_crud
from housegen.accounts.migrations import migrate_organisations
from housegen.accounts.models import Organisation
from housegen.core import db
from housegen.core.config import get_settings
from housegen.core.db import session_factory
from housegen.jobs.manager import job_manager
from housegen.limits import service as limits
from housegen.limits.models import UsageRecord
from housegen.limits.schemas import LimitsIn
from housegen.projects import crud
from housegen.projects.models import Job
from housegen.projects.storage import ProjectStorage

NOW = datetime.now(UTC)


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PHOTO_SERVICE_URL", "http://photo")
    get_settings.cache_clear()
    await db.dispose_db()
    from housegen.main import create_app

    app = create_app()
    await db.init_db()
    monkeypatch.setattr(job_manager, "submit", lambda *a, **k: None)  # jobs stay queued
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as c:
        yield c
    await db.dispose_db()
    get_settings.cache_clear()


async def _default() -> str:
    async with session_factory()() as s, s.begin():
        organisation, _ = await accounts_crud.default_organisation(s)
        return organisation.id


async def _project(organisation_id: str, furnished: bool = False) -> str:
    """A project of the organisation with a saved version (furnished: an interior in it)."""
    async with session_factory()() as s, s.begin():
        project = await crud.create_project(s, "Villa", organisation_id=organisation_id)
        await crud.add_version(s, project.id, 1, "generation", "v1", "built")
        project.current_version = 1
        pid = project.id
    st = ProjectStorage(pid)
    st.ensure()
    src = st.versions_dir / "1" / "src"
    src.mkdir(parents=True)
    body = "import { floorPlan } from 'housekit/interior';\n" if furnished else "export {}\n"
    (src / "scene.js").write_text(body)
    return pid


async def _limits(client: AsyncClient, organisation_id: str, **values: Any) -> dict[str, Any]:
    body = {"concurrent_jobs": None, "jobs_per_day": None, "listing_usd_per_month": None}
    r = await client.put(
        f"/api/v1/admin/organisations/{organisation_id}/limits", json={**body, **values}
    )
    assert r.status_code == 200, r.text
    return dict(r.json())


async def _jobs() -> int:
    async with session_factory()() as s:
        return int(await s.scalar(select(func.count()).select_from(Job)) or 0)


def _refused(r: Any, limit: str) -> str:
    assert r.status_code == 429, r.text
    error = r.json()["error"]
    assert error["code"] == "too_many_requests"
    assert error["details"]["reason"] == "limit"
    assert error["details"]["limit"] == limit
    return str(error["message"])


async def test_the_default_organisation_has_no_limits(client: AsyncClient) -> None:
    org = await _default()
    got = (await client.get(f"/api/v1/organisations/{org}/limits")).json()
    for name in ("concurrent_jobs", "jobs_per_day", "listing_usd_per_month"):
        assert got[name]["limit"] is None
        assert got[name]["override"] == "unlimited"
    assert got["jobs_per_day"]["default"] == 30
    for _ in range(4):
        pid = await _project(org)
        assert (await client.post(f"/api/v1/projects/{pid}/generate")).status_code == 202
    got = (await client.get(f"/api/v1/organisations/{org}/limits")).json()
    assert got["concurrent_jobs"]["used"] == 4
    assert got["jobs_per_day"]["used"] == 4
    # another organisation has the server's defaults
    async with session_factory()() as s, s.begin():
        other = Organisation(name="Agency")
        s.add(other)
        await s.flush()
        out = await limits.organisation_limits(s, other)
    assert out.concurrent_jobs.limit == 3
    assert out.concurrent_jobs.override is None
    assert out.listing_usd_per_month.limit == 40


async def test_jobs_at_once(client: AsyncClient) -> None:
    org = await _default()
    await _limits(client, org, concurrent_jobs=2)
    p1, p2, p3 = await _project(org), await _project(org), await _project(org)
    assert (await client.post(f"/api/v1/projects/{p1}/generate")).status_code == 202
    assert (
        await client.post(f"/api/v1/projects/{p2}/chat", data={"message": "hi"})
    ).status_code == 202
    before = await _jobs()
    message = _refused(await client.post(f"/api/v1/projects/{p3}/generate"), "concurrent_jobs")
    assert "jobs at once" in message
    assert "2 jobs queued or running" in message
    assert "when one of them ends" in message
    r = await client.post(f"/api/v1/projects/{p3}/modify", data={"message": "a pergola"})
    _refused(r, "concurrent_jobs")
    assert await _jobs() == before
    # a change waiting for its project's job is not running
    async with session_factory()() as s, s.begin():
        await crud.create_job(s, p1, "modify", "x", status="waiting")
        for job in await crud.list_jobs(s, p1):
            if job.status == "queued":
                await crud.update_job(s, job.id, status="done")
    assert (await client.post(f"/api/v1/projects/{p3}/generate")).status_code == 202


async def test_jobs_per_day(client: AsyncClient) -> None:
    org = await _default()
    await _limits(client, org, jobs_per_day=3)
    async with session_factory()() as s, s.begin():
        s.add(UsageRecord(job_id="yesterday", organisation_id=org, kind="generate",
                          created_at=limits._day_start(NOW) - timedelta(seconds=1)))  # fmt: skip
    p1, p2 = await _project(org), await _project(org)
    assert (await client.post(f"/api/v1/projects/{p1}/generate")).status_code == 202
    assert (
        await client.post(f"/api/v1/projects/{p2}/chat", data={"message": "hi"})
    ).status_code == 202
    async with session_factory()() as s, s.begin():
        for job in await crud.list_jobs(s, p1) + await crud.list_jobs(s, p2):
            await crud.update_job(s, job.id, status="done")
    assert (await client.post(f"/api/v1/projects/{p2}/interior")).status_code == 202
    async with session_factory()() as s, s.begin():
        for job in await crud.list_jobs(s, p2):
            await crud.update_job(s, job.id, status="done")
    before = await _jobs()
    message = _refused(await client.post(f"/api/v1/projects/{p1}/generate"), "jobs_per_day")
    assert "jobs per day" in message
    assert "started 3 jobs today" in message
    assert "midnight UTC" in message
    assert await _jobs() == before
    # deleting a project does not give the day back
    assert (await client.delete(f"/api/v1/projects/{p2}")).status_code == 204
    _refused(await client.post(f"/api/v1/projects/{p1}/generate"), "jobs_per_day")
    r = (await client.get(f"/api/v1/organisations/{org}/limits")).json()["jobs_per_day"]
    assert r["used"] == 3
    assert r["limit"] == 3
    assert r["resets_at"] == (limits._day_start(NOW) + timedelta(days=1)).isoformat().replace(
        "+00:00", "Z"
    )


async def test_listing_spend_this_month(client: AsyncClient) -> None:
    org = await _default()
    await _limits(client, org, listing_usd_per_month=20)
    month = limits._month_start(NOW)
    async with session_factory()() as s, s.begin():
        s.add(UsageRecord(job_id="last-month", organisation_id=org, kind="listing",
                          created_at=month - timedelta(seconds=1), listing_usd=30.0))  # fmt: skip
        s.add(UsageRecord(job_id="this-month", organisation_id=org, kind="listing",
                          created_at=month, listing_usd=10.0))  # fmt: skip
    p1, p2 = await _project(org, furnished=True), await _project(org, furnished=True)
    r = await client.post(f"/api/v1/projects/{p1}/listing")
    assert r.status_code == 202, r.text
    first = r.json()["id"]
    got = (await client.get(f"/api/v1/organisations/{org}/limits")).json()
    assert got["listing_usd_per_month"]["used"] == 18.0  # the shoot in progress at its estimate
    assert got["listing_estimate_usd"] == 8.0
    before = await _jobs()
    message = _refused(await client.post(f"/api/v1/projects/{p2}/listing"), "listing_usd_per_month")
    assert "$18.00 of its $20.00" in message
    assert "about $8.00" in message
    assert f"on {limits._next_month(NOW).day} {limits._next_month(NOW):%B}" in message
    assert await _jobs() == before
    # the first shoot ends: its cost replaces the estimate, and the next one fits
    async with session_factory()() as s, s.begin():
        await limits.record_listing_spend(s, first, 1.5)
    assert (await client.post(f"/api/v1/projects/{p2}/listing")).status_code == 202


async def test_the_month_sum_stops_at_the_month_boundary(client: AsyncClient) -> None:
    org = await _default()
    edge = datetime(2026, 11, 1, tzinfo=UTC)
    async with session_factory()() as s, s.begin():
        for job_id, at, usd in (
            ("oct", edge - timedelta(microseconds=1), 4.0),
            ("nov-first", edge, 2.5),
            ("nov-last", datetime(2026, 11, 30, 23, 59, 59, tzinfo=UTC), 1.25),
            ("dec", datetime(2026, 12, 1, tzinfo=UTC), 100.0),
        ):
            s.add(UsageRecord(job_id=job_id, organisation_id=org, kind="listing",
                              created_at=at, listing_usd=usd))  # fmt: skip
        s.add(UsageRecord(job_id="a-build", organisation_id=org, kind="generate", created_at=edge))
    async with session_factory()() as s:
        nov = await limits.usage(s, org, datetime(2026, 11, 15, tzinfo=UTC))
        oct_ = await limits.usage(s, org, datetime(2026, 10, 31, 23, 0, tzinfo=UTC))
        first_day = await limits.usage(s, org, edge + timedelta(hours=1))
    assert nov.listing_usd_per_month == 3.75
    assert oct_.listing_usd_per_month == 4.0
    assert first_day.jobs_per_day == 2  # nov-first and a-build, not October's


async def test_an_override_and_no_limit(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LIMIT_JOBS_PER_DAY", "1")
    get_settings.cache_clear()
    org = await _default()
    p1, p2, p3 = await _project(org), await _project(org), await _project(org)
    assert (await client.post(f"/api/v1/projects/{p1}/generate")).status_code == 202
    # back to the defaults: one job a day, used already
    got = await _limits(client, org)
    assert got["jobs_per_day"] == {
        "used": 1, "limit": 1, "override": None, "default": 1,
        "resets_at": got["jobs_per_day"]["resets_at"],
    }  # fmt: skip
    assert got["concurrent_jobs"]["override"] is None
    assert got["concurrent_jobs"]["limit"] == 3
    _refused(await client.post(f"/api/v1/projects/{p2}/generate"), "jobs_per_day")
    got = await _limits(client, org, jobs_per_day=2, listing_usd_per_month=12.5)
    assert got["jobs_per_day"]["override"] == 2
    assert got["jobs_per_day"]["limit"] == 2
    assert got["listing_usd_per_month"]["limit"] == 12.5
    assert (await client.post(f"/api/v1/projects/{p2}/generate")).status_code == 202
    _refused(await client.post(f"/api/v1/projects/{p3}/generate"), "jobs_per_day")
    got = await _limits(client, org, jobs_per_day="unlimited")
    assert got["jobs_per_day"]["override"] == "unlimited"
    assert got["jobs_per_day"]["limit"] is None
    assert (await client.post(f"/api/v1/projects/{p3}/generate")).status_code == 202
    await _limits(client, org, jobs_per_day=0, concurrent_jobs="unlimited")
    p4 = await _project(org)
    message = _refused(await client.post(f"/api/v1/projects/{p4}/generate"), "jobs_per_day")
    assert message == (
        "Limit reached: jobs per day. This organisation's limit is 0: ask the administrator of "
        "this server to raise it."
    )
    for bad in ({"jobs_per_day": -1}, {"jobs_per_day": "lots"}, {"listing_usd_per_month": -0.5}):
        r = await client.put(f"/api/v1/admin/organisations/{org}/limits", json=bad)
        assert r.status_code == 422, bad
    r = await client.put("/api/v1/admin/organisations/nope/limits", json={})
    assert r.status_code == 404


async def test_limits_left_unset_are_stored_as_the_defaults() -> None:
    organisation = Organisation(name="x", limits_json=limits.NO_LIMITS)

    class Session:
        async def flush(self) -> None:
            pass

    await limits.set_limits(Session(), organisation, LimitsIn())  # type: ignore[arg-type]
    assert organisation.limits_json == "{}"
    await limits.set_limits(Session(), organisation, LimitsIn(concurrent_jobs="unlimited"))  # type: ignore[arg-type]
    assert organisation.limits == {"concurrent_jobs": None}


async def test_the_migration_lifts_the_default_organisations_limits_once(
    client: AsyncClient,
) -> None:
    async with session_factory()() as s, s.begin():
        # the default organisation as #104 made it: no limits column yet (NULL after the ALTER)
        s.add(Organisation(name="Default organisation", is_default=True))
    org = await migrate_organisations()
    got = (await client.get(f"/api/v1/organisations/{org}/limits")).json()
    assert {n: got[n]["override"] for n in ("concurrent_jobs", "jobs_per_day")} == {
        "concurrent_jobs": "unlimited",
        "jobs_per_day": "unlimited",
    }
    # the administrator gives it the defaults: the next startup keeps them
    await _limits(client, org)
    assert await migrate_organisations() == org
    got = (await client.get(f"/api/v1/organisations/{org}/limits")).json()
    assert got["concurrent_jobs"]["override"] is None
    assert got["concurrent_jobs"]["limit"] == 3
