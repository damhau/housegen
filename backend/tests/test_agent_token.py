"""The agent token (#112): Claude starts jobs on dev in one organisation. It reads like a member of
that organisation, writes through an allow-list of route templates and nowhere else, and answers
to the organisation's limits. The signed-out sweep is test_access.py's."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from housegen.accounts.agent import AGENT_WRITES
from housegen.accounts.models import AGENT_NAME, Organisation, User
from housegen.accounts.oidc import build_oauth
from housegen.core import db
from housegen.core.config import TOKEN_COOKIE, get_settings
from housegen.core.db import session_factory
from housegen.jobs.manager import job_manager
from housegen.limits import service as limits
from housegen.limits.schemas import LimitsIn
from housegen.main import _log_auth
from housegen.projects import crud
from housegen.projects.models import Job
from housegen.projects.storage import ProjectStorage
from test_access import READONLY, SERVICE, _api_routes, _fill, _member, _project
from test_accounts import ADMIN, FakeProvider, World, _auth_env, google

AGENT = "agent-token-for-the-test"
# the issue's table (#112): a change to the allow-list is a change to this test too
ALLOWED = {
    ("POST", "/api/v1/projects/{project_id}/modify"),
    ("POST", "/api/v1/projects/{project_id}/chat"),
    ("POST", "/api/v1/projects/{project_id}/interior"),
    ("POST", "/api/v1/projects/{project_id}/versions/{number}/fix"),
    ("POST", "/api/v1/projects/{project_id}/versions/{number}/restore"),
    ("POST", "/api/v1/projects/{project_id}/jobs/{job_id}/cancel"),
    ("POST", "/api/v1/projects/{project_id}/listing"),
}
CRITIQUE = {
    "overall_score": 61,
    "summary": "the roof is too low",
    "done": False,
    "issues": [{"view": "south-photo", "severity": "major", "description": "low", "fix": "raise"}],
}


@pytest.fixture
async def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[World]:
    _auth_env(
        monkeypatch,
        tmp_path / "data",
        RENDER_SERVICE_TOKEN=SERVICE,
        AUTH_READONLY_TOKEN=READONLY,
        AUTH_AGENT_TOKEN=AGENT,
        AUTH_AGENT_ORGANISATION="",
        PHOTO_SERVICE_URL="http://photo",
    )
    await db.dispose_db()
    from housegen.main import create_app

    app = create_app()
    provider = FakeProvider()
    app.state.oauth = build_oauth(get_settings(), transport=provider.transport)
    await db.init_db()
    monkeypatch.setattr(job_manager, "submit", lambda *a, **k: None)  # jobs stay queued
    async with AsyncExitStack() as stack:
        yield World(app, provider, stack)
    await db.dispose_db()
    get_settings.cache_clear()


async def _agent(
    world: World, monkeypatch: pytest.MonkeyPatch, organisation_id: str
) -> AsyncClient:
    """Claude with the agent token, the token working in `organisation_id`."""
    monkeypatch.setenv("AUTH_AGENT_ORGANISATION", organisation_id)
    get_settings.cache_clear()
    claude = await world.browser()
    claude.headers["Authorization"] = f"Bearer {AGENT}"
    return claude


async def _jobs(project_id: str) -> list[Job]:
    async with session_factory()() as s:
        return await crud.list_jobs(s, project_id)


async def _prepare(route: str, project_id: str) -> dict[str, Any]:
    """What one allowed route needs to find, and the request it gets."""
    st = ProjectStorage(project_id)
    if route.endswith("/fix"):
        async with session_factory()() as s, s.begin():
            version = await crud.get_version(s, project_id, 1)
            version.critique_json = json.dumps(CRITIQUE)
    if route.endswith("/cancel"):
        async with session_factory()() as s, s.begin():
            job = await crud.create_job(s, project_id, "modify", "later", status="waiting")
        return {"path": f"/api/v1/projects/{project_id}/jobs/{job.id}/cancel"}
    if route.endswith("/listing"):
        (st.versions_dir / "1" / "src" / "scene.js").write_text(
            "import { floorPlan } from 'housekit/interior';\n"
        )
        return {"json": {"version": 1}}
    if route.endswith(("/modify", "/chat")):
        return {"data": {"message": "make the shutters green"}}
    return {}


def _template(path: str, project_id: str) -> str:
    return path.replace("{project_id}", project_id).replace("{number}", "1")


async def test_the_allow_list_is_the_issues_table(world: World) -> None:
    assert AGENT_WRITES == ALLOWED
    # every entry names a route that exists: a renamed route must not drop off silently
    assert set(_api_routes(world.app)) >= ALLOWED


@pytest.mark.parametrize(("method", "route"), sorted(ALLOWED))
async def test_every_allowed_route_does_its_work(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    method: str,
    route: str,
) -> None:
    _, org = await _member(world, "g-a", "A")
    pid = await _project(org)
    claude = await _agent(world, monkeypatch, org)
    request = await _prepare(route, pid)
    path = request.pop("path", _template(route, pid))
    before = {j.id for j in await _jobs(pid)}
    caplog.set_level(logging.INFO, logger="housegen.accounts.dependencies")

    r = await claude.request(method, path, **request)

    assert r.status_code in (200, 202), r.text
    jobs = {j.id: j for j in await _jobs(pid)}
    started = [j for i, j in jobs.items() if i not in before]
    if route.endswith("/restore"):
        assert r.json()["current_version"] == 2
        assert started == []
    elif route.endswith("/cancel"):
        assert r.json()["status"] == "cancelled"
        assert started == []
    else:
        kind = route.rsplit("/", 1)[1]
        expected = {"fix": "modify", "interior": "interior", "listing": "listing"}.get(kind, kind)
        assert [j.kind for j in started] == [expected]
        assert r.json()["id"] == started[0].id
    writes = [rec for rec in caplog.records if rec.message == "auth.agent.write"]
    assert len(writes) == 1
    write: Any = writes[0]
    assert write.route == route
    assert write.project_id == pid
    assert write.done is True
    if started:
        assert write.job_id == started[0].id
    elif route.endswith("/cancel"):
        assert write.job_id == path.split("/")[-2]


async def test_every_other_write_answers_agent_forbidden(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    owner, org = await _member(world, "g-a", "A")
    pid = await _project(org)
    claude = await _agent(world, monkeypatch, org)
    caplog.set_level(logging.INFO, logger="housegen.accounts.dependencies")
    writes = [(m, p) for m, p in _api_routes(world.app) if m not in ("GET", "HEAD")]
    refused = [(m, p) for m, p in writes if (m, p) not in ALLOWED] + [("POST", "/auth/logout")]
    # one of every group the issue never allows
    assert {
        ("POST", "/api/v1/projects"),
        ("DELETE", "/api/v1/projects/{project_id}"),
        ("POST", "/api/v1/projects/{project_id}/plans"),
        ("POST", "/api/v1/projects/{project_id}/share"),
        ("PATCH", "/api/v1/projects/{project_id}/settings"),
        ("POST", "/api/v1/projects/{project_id}/surroundings"),
        ("PUT", "/api/v1/projects/{project_id}/report"),
        ("POST", "/api/v1/projects/{project_id}/generate"),
        ("POST", "/api/v1/organisations"),
        ("DELETE", "/api/v1/organisations/{organisation_id}/members/{user_id}"),
        ("POST", "/api/v1/organisations/{organisation_id}/invitations"),
        ("POST", "/api/v1/invitations/{token}/accept"),
        ("PUT", "/api/v1/admin/organisations/{organisation_id}/limits"),
        ("PUT", "/api/v1/admin/projects/{project_id}/organisation"),
        ("PUT", "/api/v1/me/organisation"),
    } <= set(refused)
    for method, path in refused:
        r = await claude.request(method, _fill(path, pid))
        assert r.status_code == 403, (method, path, r.status_code, r.text)
        assert r.json()["error"]["details"]["reason"] == "agent_forbidden", (method, path)
    denied = [rec for rec in caplog.records if rec.message == "auth.agent.denied"]
    assert len(denied) == len(refused)
    assert not [rec for rec in caplog.records if rec.message == "auth.agent.write"]
    # the admin's reads are not the agent's either
    r = await claude.get("/api/v1/admin/organisations")
    assert r.status_code == 403
    assert r.json()["error"]["details"]["reason"] == "agent_forbidden"
    # nothing changed
    assert await _jobs(pid) == []
    async with session_factory()() as s:
        assert (await crud.get_project(s, pid)).share_token is None
        assert len(await crud.list_projects(s, org)) == 1
    assert (await owner.get(f"/api/v1/projects/{pid}")).status_code == 200


async def test_the_agent_reads_its_organisation_only(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, org_a = await _member(world, "g-a", "A")
    _, org_b = await _member(world, "g-b", "B")
    pa, pb = await _project(org_a), await _project(org_b)
    claude = await _agent(world, monkeypatch, org_a)
    assert [p["id"] for p in (await claude.get("/api/v1/projects")).json()] == [pa]
    for path in (
        "/api/v1/projects/{}",
        "/api/v1/projects/{}/files",
        "/api/v1/projects/{}/jobs",
        "/api/v1/projects/{}/chat",
        "/scenes/{}/versions/1/index.html",
        "/scenes/{}/versions/1/src/scene.js",
        "/scenes/{}/scene/index.html",
    ):
        assert (await claude.get(path.format(pa))).status_code == 200, path
        assert (await claude.get(path.format(pb))).status_code == 404, path
    assert (await claude.head(f"/scenes/{pa}/versions/1/src/scene.js")).status_code == 200
    assert (await claude.get("/api/v1/kits")).status_code == 200
    # another organisation's project, written to: 404, and no job
    r = await claude.post(f"/api/v1/projects/{pb}/modify", data={"message": "red roof"})
    assert r.status_code == 404
    assert await _jobs(pb) == []
    # a scene file is read-only to it, as to anyone
    r = await claude.post(f"/scenes/{pa}/versions/1/src/scene.js")
    assert r.status_code == 403
    assert r.json()["error"]["details"]["reason"] == "agent_forbidden"


async def test_the_organisations_limits_apply_to_the_agent(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _, org = await _member(world, "g-a", "A")
    pid = await _project(org)
    async with session_factory()() as s, s.begin():
        organisation = await s.get(Organisation, org)
        assert organisation is not None
        await limits.set_limits(s, organisation, LimitsIn(jobs_per_day=0))
    claude = await _agent(world, monkeypatch, org)
    caplog.set_level(logging.INFO, logger="housegen.accounts.dependencies")
    r = await claude.post(f"/api/v1/projects/{pid}/modify", data={"message": "red roof"})
    assert r.status_code == 429, r.text
    assert r.json()["error"]["details"]["limit"] == "jobs_per_day"
    assert await _jobs(pid) == []
    # the attempt is logged, as not done and without a job
    write: Any = next(rec for rec in caplog.records if rec.message == "auth.agent.write")
    assert write.done is False
    assert write.job_id is None


async def test_the_agent_is_a_user_nobody_sees_among_the_members(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, org = await _member(world, "g-a", "A")
    pid = await _project(org)
    admin, _ = await world.sign_in("google", google("g-admin", ADMIN))
    claude = await _agent(world, monkeypatch, org)
    r = await claude.post(f"/api/v1/projects/{pid}/chat", data={"message": "hello"})
    assert r.status_code == 202, r.text
    async with session_factory()() as s:
        res = await s.execute(select(User).where(User.name == AGENT_NAME))
        agent = res.scalar_one()
    assert agent.provider == "agent"
    members = (await owner.get(f"/api/v1/organisations/{org}/members")).json()
    assert agent.id not in {m["user_id"] for m in members}
    overview = (await admin.get("/api/v1/admin/organisations")).json()
    assert agent.id not in {m["user_id"] for o in overview for m in o["members"]}
    # the token is no session: /me knows nobody, and the account routes ask for a sign-in
    assert (await claude.get("/api/v1/me")).json()["user"] is None
    assert (await claude.get("/api/v1/me/invitations")).status_code == 401


async def test_the_agent_token_counts_as_the_bearer_header_only(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, org = await _member(world, "g-a", "A")
    pid = await _project(org)
    await _agent(world, monkeypatch, org)
    browser = await world.browser()
    browser.headers["Cookie"] = f"{TOKEN_COOKIE}={AGENT}"
    assert (await browser.get("/api/v1/projects")).status_code == 401
    r = await browser.post(f"/api/v1/projects/{pid}/modify", data={"message": "red roof"})
    assert r.status_code == 401
    assert (await browser.get(f"/scenes/{pid}/scene/index.html")).status_code == 401
    assert await _jobs(pid) == []


async def test_an_organisation_that_does_not_exist_opens_nothing(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, org = await _member(world, "g-a", "A")
    pid = await _project(org)
    claude = await _agent(world, monkeypatch, "nope")
    r = await claude.get("/api/v1/projects")
    assert r.status_code == 403
    assert r.json()["error"]["details"]["reason"] == "no_organisation"
    assert (await claude.get(f"/scenes/{pid}/scene/index.html")).status_code == 403
    r = await claude.post(f"/api/v1/projects/{pid}/modify", data={"message": "red roof"})
    assert r.status_code == 403
    assert await _jobs(pid) == []


@pytest.mark.parametrize(
    ("token", "with_organisation", "missing"),
    [(AGENT, False, "AUTH_AGENT_ORGANISATION"), ("", True, "AUTH_AGENT_TOKEN")],
)
async def test_a_half_configured_agent_token_stays_off(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    token: str,
    with_organisation: bool,
    missing: str,
) -> None:
    _, org = await _member(world, "g-a", "A")
    await _project(org)
    monkeypatch.setenv("AUTH_AGENT_TOKEN", token)
    monkeypatch.setenv("AUTH_AGENT_ORGANISATION", org if with_organisation else "")
    get_settings.cache_clear()
    caplog.set_level(logging.INFO, logger="housegen.main")
    _log_auth(get_settings())
    config: Any = next(rec for rec in caplog.records if rec.message == "auth.config")
    assert config.agent_token_set is False
    assert AGENT not in caplog.text
    half: Any = next(rec for rec in caplog.records if rec.message == "auth.agent.half_configured")
    assert half.missing == missing
    claude = await world.browser()
    claude.headers["Authorization"] = f"Bearer {AGENT}"
    assert (await claude.get("/api/v1/projects")).status_code == 401


async def test_the_startup_log_says_the_agent_token_is_on(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("AUTH_AGENT_ORGANISATION", "org-1")
    get_settings.cache_clear()
    caplog.set_level(logging.INFO, logger="housegen.main")
    _log_auth(get_settings())
    config: Any = next(rec for rec in caplog.records if rec.message == "auth.config")
    assert config.agent_token_set is True
    assert config.agent_organisation == "org-1"
    assert AGENT not in caplog.text
    assert "auth.agent.half_configured" not in caplog.text
