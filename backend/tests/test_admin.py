"""The administrator's view (#108): every organisation, moving projects between them, and the
spending limits (#107). Signed out, the admin routes answer 401 (test_access.py's sweep)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from housegen.accounts.models import Organisation
from housegen.accounts.oidc import MICROSOFT_CONSUMER_TENANT, build_oauth
from housegen.core import db
from housegen.core.config import get_settings
from housegen.core.db import session_factory
from housegen.jobs.manager import job_manager
from housegen.projects import crud
from housegen.projects.models import Job
from housegen.projects.storage import ProjectStorage
from test_access import READONLY, SERVICE, _member, _project
from test_accounts import ADMIN, FakeProvider, World, _auth_env, google, microsoft

ORGANISATIONS = "/api/v1/admin/organisations"
PROJECTS = "/api/v1/admin/projects"


def _move(project_id: str) -> str:
    return f"/api/v1/admin/projects/{project_id}/organisation"


def _limits(organisation_id: str) -> str:
    return f"/api/v1/admin/organisations/{organisation_id}/limits"


@pytest.fixture
async def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[World]:
    _auth_env(
        monkeypatch,
        tmp_path / "data",
        RENDER_SERVICE_TOKEN=SERVICE,
        AUTH_READONLY_TOKEN=READONLY,
    )
    await db.dispose_db()
    from housegen.main import create_app

    app = create_app()
    provider = FakeProvider()
    app.state.oauth = build_oauth(get_settings(), transport=provider.transport)
    await db.init_db()
    monkeypatch.setattr(job_manager, "submit", lambda *a, **k: None)
    async with AsyncExitStack() as stack:
        yield World(app, provider, stack)
    await db.dispose_db()
    get_settings.cache_clear()


async def _admin(world: World) -> AsyncClient:
    """The administrator, signed in with AUTH_ADMIN_EMAIL in another case; owns the default
    organisation (the bootstrap)."""
    browser, _ = await world.sign_in("google", google("g-admin", ADMIN.upper()))
    return browser


async def test_me_says_who_is_the_administrator(world: World) -> None:
    admin = await _admin(world)
    assert (await admin.get("/api/v1/me")).json()["is_admin"] is True
    unverified, _ = await world.sign_in("google", google("g-fake", ADMIN, verified=False))
    assert (await unverified.get("/api/v1/me")).json()["is_admin"] is False
    member, _ = await _member(world, "g-a", "A")
    assert (await member.get("/api/v1/me")).json()["is_admin"] is False
    anonymous = await world.browser()
    assert (await anonymous.get("/api/v1/me")).json()["is_admin"] is False


async def test_only_the_administrator_opens_the_admin_routes(world: World) -> None:
    member, org = await _member(world, "g-a", "A")
    pid = await _project(org)
    # the admin's email on a work account whose domain is not verified, and unverified on Google
    work, _ = await world.sign_in("microsoft", microsoft("m-admin", ADMIN))
    fake, _ = await world.sign_in("google", google("g-fake", ADMIN, verified=False))
    anonymous = await world.browser()
    calls = (
        ("GET", ORGANISATIONS),
        ("GET", PROJECTS),
        ("PUT", _move(pid)),
        ("PUT", _limits(org)),
    )
    for browser in (member, work, fake):
        for method, path in calls:
            r = await browser.request(method, path, json={"organisation_id": org})
            assert r.status_code == 403, (method, path, r.text)
            assert r.json()["error"]["details"]["reason"] == "not_admin"
    for method, path in calls:
        r = await anonymous.request(method, path, json={"organisation_id": org})
        assert r.status_code == 401, (method, path)


async def test_the_administrator_needs_no_organisation(world: World) -> None:
    await _admin(world)
    # a second account of the admin's verified email: the bootstrap happened once, no organisation
    later, _ = await world.sign_in(
        "microsoft", microsoft("m-admin2", ADMIN, tid=MICROSOFT_CONSUMER_TENANT)
    )
    me = (await later.get("/api/v1/me")).json()
    assert me["organisations"] == []
    assert me["is_admin"] is True
    assert (await later.get(ORGANISATIONS)).status_code == 200
    assert (await later.get(PROJECTS)).status_code == 200
    r = await later.get("/api/v1/projects")
    assert r.json()["error"]["details"]["reason"] == "no_organisation"


async def test_every_organisation_is_listed_with_its_members_and_projects(world: World) -> None:
    admin = await _admin(world)
    _, org_a = await _member(world, "g-a", "A")
    _, org_b = await _member(world, "g-b", "B")
    async with session_factory()() as s, s.begin():
        nobody = Organisation(name="Nobody yet")
        s.add(nobody)
        await s.flush()
        org_empty = nobody.id
    pa1, pa2, pb = await _project(org_a), await _project(org_a), await _project(org_b)

    r = await admin.get(ORGANISATIONS)
    assert r.status_code == 200, r.text
    listed = {o["name"]: o for o in r.json()}
    assert list(listed) == ["Default organisation", "A", "B", "Nobody yet"]
    default = listed["Default organisation"]
    assert default["is_default"] is True
    assert [(m["email"], m["role"]) for m in default["members"]] == [(ADMIN.upper(), "owner")]
    assert listed["A"]["id"] == org_a
    assert [(m["email"], m["role"]) for m in listed["A"]["members"]] == [("g-a@x.ch", "owner")]
    assert listed["A"]["project_count"] == 2
    assert listed["B"]["project_count"] == 1
    assert listed["Nobody yet"] == {
        **listed["Nobody yet"],
        "id": org_empty,
        "members": [],
        "project_count": 0,
    }

    r = await admin.get(PROJECTS)
    assert r.status_code == 200, r.text
    projects = {p["id"]: p for p in r.json()}
    assert {pid: p["organisation_id"] for pid, p in projects.items()} == {
        pa1: org_a,
        pa2: org_a,
        pb: org_b,
    }
    assert projects[pb]["name"] == "Villa"
    assert projects[pb]["status"] == "new"
    assert projects[pb]["updated_at"]


async def test_a_moved_project_belongs_to_its_new_organisation(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    admin = await _admin(world)
    a, org_a = await _member(world, "g-a", "A")
    b, org_b = await _member(world, "g-b", "B")
    pid = await _project(org_a)
    token = (await a.post(f"/api/v1/projects/{pid}/share")).json()["token"]

    caplog.set_level(logging.INFO)
    r = await admin.put(_move(pid), json={"organisation_id": org_b})
    assert r.status_code == 200, r.text
    assert r.json()["organisation_id"] == org_b
    moved = [r.__dict__ for r in caplog.records if r.getMessage() == "admin.project_moved"]
    assert [
        (m["project_id"], m["from_organisation_id"], m["to_organisation_id"]) for m in moved
    ] == [(pid, org_a, org_b)]

    assert [p["id"] for p in (await b.get("/api/v1/projects")).json()] == [pid]
    assert (await a.get("/api/v1/projects")).json() == []
    for path in (
        f"/api/v1/projects/{pid}",
        f"/api/v1/projects/{pid}/files",
        f"/scenes/{pid}/scene/index.html",
        f"/scenes/{pid}/versions/1/src/scene.js",
        f"/scenes/{pid}/photos/south.jpg",
    ):
        assert (await b.get(path)).status_code == 200, path
        assert (await a.get(path)).status_code == 404, path

    # nothing else moved: the files, the versions, the share link
    assert (ProjectStorage(pid).versions_dir / "1" / "src" / "scene.js").is_file()
    project = (await b.get(f"/api/v1/projects/{pid}")).json()
    assert [v["number"] for v in project["versions"]] == [1]
    assert project["share"]["token"] == token
    anonymous = await world.browser()
    assert (await anonymous.get(f"/api/v1/shared/{token}")).status_code == 200
    assert (await anonymous.get(f"/shared/{token}/scene/src/scene.js")).status_code == 200

    # the same organisation again changes nothing
    caplog.clear()
    r = await admin.put(_move(pid), json={"organisation_id": org_b})
    assert r.status_code == 200
    assert "admin.project_moved" not in caplog.text

    assert (await admin.put(_move("nope"), json={"organisation_id": org_a})).status_code == 404
    assert (await admin.put(_move(pid), json={"organisation_id": "nope"})).status_code == 404
    async with session_factory()() as s:
        assert (await crud.get_project(s, pid)).organisation_id == org_b


async def test_a_project_at_work_does_not_move(world: World) -> None:
    admin = await _admin(world)
    _, org_a = await _member(world, "g-a", "A")
    _, org_b = await _member(world, "g-b", "B")
    pid = await _project(org_a)
    async with session_factory()() as s, s.begin():
        job_id = (await crud.create_job(s, pid, "generate")).id
    for status in ("queued", "running", "interrupted"):
        async with session_factory()() as s, s.begin():
            await s.execute(update(Job).where(Job.id == job_id).values(status=status))
        r = await admin.put(_move(pid), json={"organisation_id": org_b})
        assert r.status_code == 409, (status, r.text)
        assert "job" in r.json()["error"]["message"]
    async with session_factory()() as s, s.begin():
        await s.execute(update(Job).where(Job.id == job_id).values(status="done"))
    assert (await admin.put(_move(pid), json={"organisation_id": org_b})).status_code == 200


async def test_the_administrator_sets_an_organisations_limits_and_its_members_see_them(
    world: World,
) -> None:
    admin = await _admin(world)
    member, org = await _member(world, "g-a", "A")
    pid = await _project(org)
    r = await member.post(f"/api/v1/projects/{pid}/generate")
    assert r.status_code == 202, r.text
    listed = {o["id"]: o for o in (await admin.get(ORGANISATIONS)).json()}
    limits = listed[org]["limits"]
    assert limits["concurrent_jobs"] == {**limits["concurrent_jobs"], "used": 1, "limit": 3}
    assert limits["jobs_per_day"]["used"] == 1
    assert limits["jobs_per_day"]["limit"] == 30
    assert limits["listing_usd_per_month"] == {
        **limits["listing_usd_per_month"], "used": 0.0, "limit": 40.0, "override": None,
    }  # fmt: skip
    default = next(o for o in listed.values() if o["is_default"])
    assert default["limits"]["jobs_per_day"]["override"] == "unlimited"

    body = {"concurrent_jobs": "unlimited", "jobs_per_day": 5, "listing_usd_per_month": None}
    r = await admin.put(_limits(org), json=body)
    assert r.status_code == 200, r.text
    assert r.json()["concurrent_jobs"]["limit"] is None
    assert r.json()["jobs_per_day"]["override"] == 5
    assert r.json()["listing_usd_per_month"]["override"] is None
    # the members read them; only the administrator changes them
    seen = (await member.get(f"/api/v1/organisations/{org}/limits")).json()
    assert seen == r.json()
    r = await member.put(_limits(org), json={"jobs_per_day": 1000})
    assert r.status_code == 403
    outsider, _ = await _member(world, "g-b", "B")
    assert (await outsider.get(f"/api/v1/organisations/{org}/limits")).status_code == 404


async def test_the_readonly_token_reads_the_admin_routes_and_moves_nothing(world: World) -> None:
    _, org_a = await _member(world, "g-a", "A")
    _, org_b = await _member(world, "g-b", "B")
    pid = await _project(org_a)
    claude = await world.browser()
    claude.headers["Authorization"] = f"Bearer {READONLY}"
    r = await claude.get(ORGANISATIONS)
    assert r.status_code == 200
    assert {o["id"] for o in r.json()} >= {org_a, org_b}
    assert [p["id"] for p in (await claude.get(PROJECTS)).json()] == [pid]
    r = await claude.put(_move(pid), json={"organisation_id": org_b})
    assert r.status_code == 403
    assert r.json()["error"]["details"]["reason"] == "read_only"
    r = await claude.put(_limits(org_a), json={"jobs_per_day": 1000})
    assert r.status_code == 403
    assert r.json()["error"]["details"]["reason"] == "read_only"
    listed = {o["id"]: o for o in (await claude.get(ORGANISATIONS)).json()}
    assert listed[org_a]["limits"]["jobs_per_day"]["override"] is None
    async with session_factory()() as s:
        assert (await crud.get_project(s, pid)).organisation_id == org_a
    # the service token reads scene files, not the API
    service = await world.browser()
    service.headers["Authorization"] = f"Bearer {SERVICE}"
    assert (await service.get(ORGANISATIONS)).status_code == 401


async def test_with_auth_disabled_the_local_user_is_the_administrator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _auth_env(monkeypatch, tmp_path / "data", AUTH_DISABLED="1")
    await db.dispose_db()
    from housegen.main import create_app

    app = create_app()
    await db.init_db()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://testserver"
        ) as c:
            me = (await c.get("/api/v1/me")).json()
            assert me["is_admin"] is True
            pid = await _project(me["organisation"]["id"])
            second = (await c.post("/api/v1/organisations", json={"name": "Second"})).json()["id"]
            organisations = (await c.get(ORGANISATIONS)).json()
            assert [o["name"] for o in organisations] == ["Default organisation", "Second"]
            r = await c.put(_move(pid), json={"organisation_id": second})
            assert r.status_code == 200, r.text
    finally:
        await db.dispose_db()
        get_settings.cache_clear()
