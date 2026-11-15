"""Everything behind the session (#105): the API and a project's files by organisation, the share
link's own paths signed out, the service and read-only tokens, and the refusal to start without a
sign-in provider."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Iterator
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import AsyncClient

from housegen.accounts.oidc import build_oauth
from housegen.core import db
from housegen.core.config import TOKEN_COOKIE, get_settings
from housegen.core.db import session_factory
from housegen.jobs.manager import job_manager
from housegen.projects import crud
from housegen.projects.storage import ProjectStorage
from housegen.render.renderer import present_token
from test_accounts import FakeProvider, World, _auth_env, google

SERVICE = "service-token-for-the-test"
READONLY = "readonly-token-for-the-test"
PUBLIC = {
    ("GET", "/api/v1/health"),
    ("GET", "/api/v1/me"),
    ("GET", "/api/v1/invitations/{token}"),
    ("GET", "/api/v1/shared/{token}"),
}


@pytest.fixture
async def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[World]:
    _auth_env(
        monkeypatch,
        tmp_path / "data",
        RENDER_SERVICE_TOKEN=SERVICE,
        AUTH_READONLY_TOKEN=READONLY,
    )
    await db.dispose_db()
    from housegen.main import create_app, mount_spa

    app = create_app()
    spa = tmp_path / "spa"
    (spa / "assets").mkdir(parents=True)
    (spa / "index.html").write_text("<!doctype html><title>housegen</title>")
    mount_spa(app, spa)
    provider = FakeProvider()
    app.state.oauth = build_oauth(get_settings(), transport=provider.transport)
    await db.init_db()
    monkeypatch.setattr(job_manager, "submit", lambda *a, **k: None)
    async with AsyncExitStack() as stack:
        yield World(app, provider, stack)
    await db.dispose_db()
    get_settings.cache_clear()


async def _member(world: World, sub: str, organisation: str) -> tuple[AsyncClient, str]:
    browser, _ = await world.sign_in("google", google(sub, f"{sub}@x.ch"))
    r = await browser.post("/api/v1/organisations", json={"name": organisation})
    assert r.status_code == 201, r.text
    return browser, r.json()["id"]


async def _project(organisation_id: str) -> str:
    """A project with a saved version, its pictures, a listing photo, a photo and surroundings."""
    async with session_factory()() as s, s.begin():
        project = await crud.create_project(s, "Villa", organisation_id=organisation_id)
        await crud.add_version(s, project.id, 1, "generation", "v1", "built")
        project.current_version = 1
        await crud.add_photo(s, project, "south", "south.jpg", "south.jpg")
        pid = project.id
    st = ProjectStorage(pid)
    st.ensure()
    version = st.versions_dir / "1"
    for rel, text in {
        "index.html": "<!doctype html><script type=module src='src/scene.js'></script>",
        "src/scene.js": "export const x = 1;",
        "renders/south-photo.jpg": "jpg",
        "listing/01.jpg": "jpg",
        "listing/set.json": "{}",
        "notes.txt": "the owner's notes",
    }.items():
        (version / rel).parent.mkdir(parents=True, exist_ok=True)
        (version / rel).write_text(text)
    (st.scene_dir / "src").mkdir(parents=True, exist_ok=True)
    (st.scene_dir / "src" / "scene.js").write_text("export const y = 2;")
    (st.scene_dir / "index.html").write_text("<!doctype html>")
    st.photos_dir.mkdir(parents=True, exist_ok=True)
    (st.photos_dir / "south.jpg").write_text("jpg")
    (st.photos_dir / "hidden.jpg").write_text("jpg")
    (st.root / "context").mkdir(exist_ok=True)
    (st.root / "context" / "context.json").write_text('{"alignment": {"set": true}}')
    (st.root / "brief.txt").write_text("private")
    return pid


def _api_routes(app: FastAPI) -> list[tuple[str, str]]:
    """Every /api/v1 operation, those left out of the schema too (the job stream, the PDF)."""

    def walk(routes: list[Any], above: str, own: str) -> Iterator[tuple[str, APIRoute]]:
        # a route's path has its own router's prefix already, not those of the routers above it
        for route in routes:
            if isinstance(route, APIRoute):
                yield above + route.path, route
            elif hasattr(route, "original_router"):
                inner = route.original_router
                yield from walk(inner.routes, above + own, inner.prefix)

    found = [
        (method, path)
        for path, route in walk(app.routes, "", "")
        if path.startswith("/api/v1")
        for method in sorted(route.methods - {"HEAD"})
    ]
    schema = {
        (method.upper(), path)
        for path, methods in app.openapi()["paths"].items()
        for method in methods
    }
    assert schema <= set(found), schema - set(found)  # the walk sees what the schema lists
    return found


def _fill(path: str, project_id: str) -> str:
    path = path.replace("{project_id}", project_id)
    return re.sub(r"\{[^}]+\}", "x1", path)


async def test_signed_out_only_the_public_routes_answer(world: World) -> None:
    owner, org = await _member(world, "g-a", "A")
    pid = await _project(org)
    anonymous = await world.browser()
    routes = _api_routes(world.app)
    assert len(routes) > 50
    assert ("POST", "/api/v1/projects/{project_id}/report/pdf") in routes
    for method, path in routes:
        r = await anonymous.request(method, _fill(path, pid))
        if (method, path) in PUBLIC:
            assert r.status_code != 401, (method, path)
        else:
            assert r.status_code == 401, (method, path, r.status_code, r.text)
    for path in (
        "/api/v1/projects",
        f"/api/v1/projects/{pid}/files",
        f"/api/v1/projects/{pid}/jobs/x1/stream",
        f"/scenes/{pid}/scene/index.html",
        f"/scenes/{pid}/versions/1/index.html",
        f"/scenes/{pid}/versions/1/src/scene.js",
        f"/scenes/{pid}/photos/south.jpg",
    ):
        r = await anonymous.get(path)
        assert r.status_code == 401, path
        assert r.json()["error"]["code"] == "unauthorized"
    # the member sees them
    assert (await owner.get(f"/scenes/{pid}/versions/1/src/scene.js")).status_code == 200
    assert (await owner.get(f"/scenes/{pid}/scene/index.html")).status_code == 200
    # the public ones
    assert (await anonymous.get("/api/v1/health")).status_code == 200
    assert (await anonymous.get("/api/v1/me")).status_code == 200
    assert (await anonymous.get("/api/v1/invitations/nope")).status_code == 404
    # the SPA, the kit and the sign-in pages stay public: the gate is the SPA's
    for page in ("/", "/about", "/privacy", "/signin", "/s/abc", f"/projects/{pid}"):
        assert (await anonymous.get(page)).status_code == 200, page
    assert (await anonymous.get("/kit/house.js")).status_code == 200


async def test_another_organisations_project_does_not_exist(world: World) -> None:
    a, org_a = await _member(world, "g-a", "A")
    b, _ = await _member(world, "g-b", "B")
    pid = await _project(org_a)
    assert [p["id"] for p in (await a.get("/api/v1/projects")).json()] == [pid]
    assert (await b.get("/api/v1/projects")).json() == []
    for path in (
        f"/api/v1/projects/{pid}",
        f"/api/v1/projects/{pid}/files",
        f"/api/v1/projects/{pid}/jobs/x1/stream",
        f"/scenes/{pid}/scene/index.html",
        f"/scenes/{pid}/versions/1/index.html",
        f"/scenes/{pid}/versions/1/src/scene.js",
    ):
        assert (await b.get(path)).status_code == 404, path
        assert (await a.get(path)).status_code in (200, 404), path
    assert (await a.get(f"/api/v1/projects/{pid}")).status_code == 200
    assert (await a.get(f"/api/v1/projects/{pid}/files")).status_code == 200
    # a job is never enqueued for it: the check comes before the route
    r = await b.post(f"/api/v1/projects/{pid}/generate")
    assert r.status_code == 404
    r = await b.post(f"/api/v1/projects/{pid}/share")
    assert r.status_code == 404
    async with session_factory()() as s:
        assert await crud.list_jobs(s, pid) == []


async def test_before_the_first_organisation_the_projects_wait(world: World) -> None:
    browser, _ = await world.sign_in("google", google("g-new", "new@x.ch"))
    r = await browser.get("/api/v1/projects")
    assert r.status_code == 403
    assert r.json()["error"]["details"]["reason"] == "no_organisation"
    assert (await browser.get("/api/v1/me")).status_code == 200


async def test_a_share_link_reads_its_own_paths_signed_out(world: World) -> None:
    owner, org = await _member(world, "g-a", "A")
    pid = await _project(org)
    token = (await owner.post(f"/api/v1/projects/{pid}/share")).json()["token"]
    anonymous = await world.browser()
    r = await anonymous.get(f"/api/v1/shared/{token}")
    assert r.status_code == 200, r.text
    body = r.json()
    base = f"/shared/{token}"
    assert body["scene_url"] == f"{base}/scene/index.html"
    assert body["render_urls"]["south-photo"].startswith(f"{base}/scene/renders/south-photo.jpg")
    assert body["photo_urls"] == [f"{base}/photos/south.jpg"]
    assert body["context_url"] == f"{base}/context/"
    allowed = (
        f"{base}/scene/index.html",
        f"{base}/scene/index.html?kit={body['kit']}",
        f"{base}/scene/src/scene.js",
        f"{base}/scene/renders/south-photo.jpg",
        f"{base}/scene/listing/01.jpg",
        f"{base}/context/context.json",
        f"{base}/photos/south.jpg",
    )
    for path in allowed:
        r = await anonymous.get(path)
        assert r.status_code == 200, path
        assert (await anonymous.head(path)).status_code == 200, path
    assert "export const x = 1" in (await anonymous.get(f"{base}/scene/src/scene.js")).text
    refused = (
        f"{base}/scene/notes.txt",
        f"{base}/scene/listing/set.json",
        f"{base}/photos/hidden.jpg",
        f"{base}/scene/src/%2e%2e/%2e%2e/%2e%2e/brief.txt",
        f"{base}/scene/src/..%2f..%2f..%2fbrief.txt",
        f"{base}/context/%2e%2e/brief.txt",
        f"{base}/plans/1/plan.pdf",
        f"{base}/brief.txt",
        "/shared/nope/scene/index.html",
    )
    for path in refused:
        assert (await anonymous.get(path)).status_code == 404, path
    assert (await anonymous.post(f"{base}/scene/index.html")).status_code == 405
    # the project's own paths stay closed
    assert (await anonymous.get(f"/scenes/{pid}/versions/1/src/scene.js")).status_code == 401
    # revoked: the paths die with the link
    assert (await owner.delete(f"/api/v1/projects/{pid}/share")).status_code == 204
    assert (await anonymous.get(f"{base}/scene/index.html")).status_code == 404


async def test_the_service_token_reads_scene_files_only(world: World) -> None:
    _, org = await _member(world, "g-a", "A")
    pid = await _project(org)
    service = await world.browser()
    service.headers["Cookie"] = f"{TOKEN_COOKIE}={SERVICE}"
    for path in (
        f"/scenes/{pid}/scene/index.html",
        f"/scenes/{pid}/versions/1/index.html",
        f"/scenes/{pid}/versions/1/src/scene.js",
        f"/scenes/{pid}/context/context.json",
    ):
        assert (await service.get(path)).status_code == 200, path
    # the API is not for it, and nothing it sends writes
    assert (await service.get("/api/v1/projects")).status_code == 401
    assert (await service.post(f"/scenes/{pid}/versions/1/src/scene.js")).status_code == 403
    # as a bearer header too (the photo service fetches the surroundings so)
    bearer = await world.browser()
    headers = {"Authorization": f"Bearer {SERVICE}"}
    r = await bearer.get(f"/scenes/{pid}/context/context.json", headers=headers)
    assert r.status_code == 200
    # a wrong token is no session either
    wrong = await world.browser()
    wrong.headers["Cookie"] = f"{TOKEN_COOKIE}=nope"
    assert (await wrong.get(f"/scenes/{pid}/scene/index.html")).status_code == 401


async def test_the_readonly_token_reads_every_organisation_and_writes_nothing(
    world: World,
) -> None:
    _, org_a = await _member(world, "g-a", "A")
    _, org_b = await _member(world, "g-b", "B")
    pa, pb = await _project(org_a), await _project(org_b)
    claude = await world.browser()
    claude.headers["Authorization"] = f"Bearer {READONLY}"
    listed = {p["id"] for p in (await claude.get("/api/v1/projects")).json()}
    assert listed == {pa, pb}
    for pid in (pa, pb):
        assert (await claude.get(f"/api/v1/projects/{pid}")).status_code == 200
        assert (await claude.get(f"/api/v1/projects/{pid}/files")).status_code == 200
        assert (await claude.head(f"/scenes/{pid}/versions/1/src/scene.js")).status_code == 200
        assert (await claude.get(f"/scenes/{pid}/versions/1/index.html")).status_code == 200
    assert (await claude.get("/api/v1/kits")).status_code == 200
    for method, path in (
        ("POST", f"/api/v1/projects/{pa}/share"),
        ("POST", f"/api/v1/projects/{pa}/generate"),
        ("DELETE", f"/api/v1/projects/{pa}"),
        ("POST", "/api/v1/projects"),
    ):
        r = await claude.request(method, path)
        assert r.status_code == 403, (method, path, r.text)
        assert r.json()["error"]["details"]["reason"] == "read_only"
    async with session_factory()() as s:
        assert (await crud.get_project(s, pa)).share_token is None


async def test_without_a_sign_in_provider_the_app_does_not_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from housegen.main import SignInMissing, create_app, lifespan

    _auth_env(
        monkeypatch,
        tmp_path / "data",
        AUTH_GOOGLE_CLIENT_ID="",
        AUTH_MICROSOFT_CLIENT_ID="",
    )
    try:
        with pytest.raises(SignInMissing, match="AUTH_DISABLED"):
            async with lifespan(create_app()):
                pass
    finally:
        get_settings.cache_clear()


class _Context:
    def __init__(self) -> None:
        self.cookies: list[dict[str, Any]] = []

    async def add_cookies(self, cookies: list[dict[str, Any]]) -> None:
        self.cookies += cookies


async def test_a_headless_browser_sends_its_token_to_the_scenes_host_only() -> None:
    context = _Context()
    await present_token(context, "http://127.0.0.1:8000/scenes/p/scene/index.html?view=x", "t")  # type: ignore[arg-type]
    await present_token(context, "https://dev.example/scenes/p/versions/1/index.html", "t")  # type: ignore[arg-type]
    await present_token(context, "file:///tmp/scene/index.html", "t")  # type: ignore[arg-type]
    await present_token(context, "http://127.0.0.1:8000/scene/index.html", "")  # type: ignore[arg-type]
    assert [(c["url"], c["secure"]) for c in context.cookies] == [
        ("http://127.0.0.1:8000", False),
        ("https://dev.example", True),
    ]
    assert {c["name"] for c in context.cookies} == {TOKEN_COOKIE}
    assert all(c["httpOnly"] and "domain" not in c for c in context.cookies)
