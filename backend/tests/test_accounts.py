"""Sign-in with Google and Microsoft, organisations and invitations (#104), against a fake OpenID
provider: discovery, keys and token endpoints answered in process, no network."""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import sqlite3
import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import httpx2
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from joserfc import jwt
from joserfc.jwk import RSAKey

from housegen.accounts.migrations import migrate_organisations
from housegen.accounts.oidc import (
    GOOGLE_METADATA_URL,
    MICROSOFT_CONSUMER_TENANT,
    MICROSOFT_METADATA_URL,
    build_oauth,
)
from housegen.core import db
from housegen.core.config import get_settings
from housegen.core.db import session_factory
from housegen.jobs.manager import job_manager
from housegen.projects.models import Project

GOOGLE_ID = "google-client"
MICROSOFT_ID = "microsoft-client"
ADMIN = "admin@agency.ch"
WORK_TENANT = "72f988bf-86f1-41af-91ab-2d7cd011db47"
MS = "https://login.microsoftonline.com"


class FakeProvider:
    """Google and Microsoft `common` as far as Authlib can tell: discovery documents, a key set
    and token endpoints that hand out ID tokens signed with a key made for the test."""

    def __init__(self) -> None:
        self.key = RSAKey.generate_key(2048, parameters={"kid": "test-key"}, private=True)
        self.codes: dict[str, dict[str, Any]] = {}
        self.transport = httpx2.MockTransport(self._handle)
        self.metadata = {
            GOOGLE_METADATA_URL: {
                "issuer": "https://accounts.google.com",
                "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
                "token_endpoint": "https://oauth2.googleapis.com/token",
                "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
                "id_token_signing_alg_values_supported": ["RS256"],
            },
            MICROSOFT_METADATA_URL: {
                "issuer": f"{MS}/{{tenantid}}/v2.0",
                "authorization_endpoint": f"{MS}/common/oauth2/v2.0/authorize",
                "token_endpoint": f"{MS}/common/oauth2/v2.0/token",
                "jwks_uri": f"{MS}/common/discovery/v2.0/keys",
                "id_token_signing_alg_values_supported": ["RS256"],
            },
        }

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if url in self.metadata:
            return httpx2.Response(200, json=self.metadata[url])
        if url in {m["jwks_uri"] for m in self.metadata.values()}:
            return httpx2.Response(200, json={"keys": [self.key.as_dict(private=False)]})
        if url in {m["token_endpoint"] for m in self.metadata.values()}:
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            grant = self.codes.pop(form["code"], None)
            if grant is None:
                return httpx2.Response(400, json={"error": "invalid_grant"})
            verifier = form.get("code_verifier", "")
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            assert challenge == grant["code_challenge"], "PKCE verifier does not match"
            assert form["redirect_uri"] == grant["redirect_uri"]
            id_token = jwt.encode({"alg": "RS256", "kid": "test-key"}, grant["claims"], self.key)
            return httpx2.Response(
                200,
                json={
                    "access_token": "at",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "id_token": id_token,
                },
            )
        raise AssertionError(f"unexpected request to {url}")

    def authorize(self, location: str, claims: dict[str, Any]) -> str:
        """The provider's sign-in page: the user agrees, and comes back to the callback with a
        code for these claims. Returns that callback URL's path and query."""
        query = {k: v[0] for k, v in parse_qs(urlparse(location).query).items()}
        now = int(time.time())
        code = secrets.token_hex(8)
        self.codes[code] = {
            "code_challenge": query["code_challenge"],
            "redirect_uri": query["redirect_uri"],
            "claims": {
                "aud": query["client_id"],
                "iat": now,
                "exp": now + 600,
                "nonce": query["nonce"],
                **claims,
            },
        }
        callback = urlparse(query["redirect_uri"])
        return f"{callback.path}?{urlencode({'code': code, 'state': query['state']})}"


def google(sub: str, email: str, verified: bool = True, **extra: Any) -> dict[str, Any]:
    return {
        "iss": "https://accounts.google.com",
        "sub": sub,
        "email": email,
        "email_verified": verified,
        "name": email.split("@")[0].title(),
        "picture": f"https://example.com/{sub}.png",
        **extra,
    }


def microsoft(sub: str, email: str | None, tid: str = WORK_TENANT, **extra: Any) -> dict[str, Any]:
    claims: dict[str, Any] = {
        "iss": f"{MS}/{tid}/v2.0",
        "tid": tid,
        "sub": sub,
        "name": f"Person {sub}",
        "preferred_username": email or f"{sub}@example.com",
        **extra,
    }
    if email:
        claims["email"] = email
    return claims


class World:
    def __init__(self, app: FastAPI, provider: FakeProvider, stack: AsyncExitStack) -> None:
        self.app = app
        self.provider = provider
        self.stack = stack

    async def browser(self) -> AsyncClient:
        """A browser of its own (its own cookie jar), on the same app."""
        return await self.stack.enter_async_context(
            AsyncClient(transport=ASGITransport(app=self.app), base_url="https://testserver")
        )

    async def sign_in(
        self, provider: str, claims: dict[str, Any], next_path: str = "/projects"
    ) -> tuple[AsyncClient, httpx.Response]:
        browser = await self.browser()
        r = await browser.get(f"/auth/login/{provider}", params={"next": next_path})
        assert r.status_code == 302, r.text
        r = await browser.get(self.provider.authorize(r.headers["location"], claims))
        return browser, r


def _auth_env(monkeypatch: pytest.MonkeyPatch, data_dir: Path, **overrides: str) -> None:
    env = {
        "DATA_DIR": str(data_dir),
        "AUTH_DISABLED": "0",
        "AUTH_GOOGLE_CLIENT_ID": GOOGLE_ID,
        "AUTH_GOOGLE_CLIENT_SECRET": "google-secret",
        "AUTH_MICROSOFT_CLIENT_ID": MICROSOFT_ID,
        "AUTH_MICROSOFT_CLIENT_SECRET": "microsoft-secret",
        "AUTH_SESSION_SECRET": "test-session-secret",
        "AUTH_ADMIN_EMAIL": ADMIN,
        "AUTH_PUBLIC_URL": "",
        **overrides,
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()


@pytest.fixture
async def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[World]:
    _auth_env(monkeypatch, tmp_path / "data")
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


async def _add_project(name: str, organisation_id: str | None = None) -> str:
    async with session_factory()() as s, s.begin():
        project = Project(name=name, organisation_id=organisation_id)
        s.add(project)
        await s.flush()
        return project.id


async def test_me_without_a_session_lists_the_providers(world: World) -> None:
    browser = await world.browser()
    r = await browser.get("/api/v1/me")
    assert r.status_code == 200
    assert r.json() == {
        "user": None,
        "organisation": None,
        "organisations": [],
        "auth": {"disabled": False, "providers": ["google", "microsoft"]},
    }
    # the projects need a session (#105)
    assert (await browser.get("/api/v1/projects")).status_code == 401
    # the account routes need a session
    r = await browser.post("/api/v1/organisations", json={"name": "A"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


async def test_google_sign_in(world: World) -> None:
    browser = await world.browser()
    r = await browser.get("/auth/login/google", params={"next": "/projects/abc"})
    assert r.status_code == 302
    location = urlparse(r.headers["location"])
    query = parse_qs(location.query)
    assert location.netloc == "accounts.google.com"
    assert query["client_id"] == [GOOGLE_ID]
    assert query["redirect_uri"] == ["https://testserver/auth/callback/google"]
    assert query["scope"] == ["openid email profile"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["prompt"] == ["select_account"]
    assert "nonce" in query

    r = await browser.get(
        world.provider.authorize(r.headers["location"], google("g-1", "ann@x.ch"))
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/projects/abc"
    cookie = r.headers["set-cookie"].lower()
    assert "housegen_session=" in cookie
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=lax" in cookie

    me = (await browser.get("/api/v1/me")).json()
    assert me["user"]["provider"] == "google"
    assert me["user"]["email"] == "ann@x.ch"
    assert me["user"]["email_verified"] is True
    assert me["user"]["picture"] == "https://example.com/g-1.png"
    assert me["organisation"] is None  # the first-login wizard's case (#106)
    assert me["organisations"] == []

    # the same (issuer, subject) is the same user, even with another email and the short `iss`
    again, r = await world.sign_in("google", google("g-1", "ann@new.ch", iss="accounts.google.com"))
    assert r.status_code == 303
    me2 = (await again.get("/api/v1/me")).json()
    assert me2["user"]["id"] == me["user"]["id"]
    assert me2["user"]["email"] == "ann@new.ch"

    # sign out
    r = await browser.post("/auth/logout")
    assert r.status_code == 303
    assert (await browser.get("/api/v1/me")).json()["user"] is None


async def test_microsoft_issuer_is_checked_against_the_tokens_tenant(world: World) -> None:
    browser, r = await world.sign_in("microsoft", microsoft("m-1", "bob@corp.ch"))
    assert r.status_code == 303, r.headers
    me = (await browser.get("/api/v1/me")).json()
    assert me["user"]["provider"] == "microsoft"
    assert me["user"]["email"] == "bob@corp.ch"
    assert me["user"]["email_verified"] is False  # a work account without xms_edov

    # a token whose iss names another tenant than its tid is refused
    forged = microsoft("m-2", "eve@corp.ch", iss=f"{MS}/00000000-0000-0000-0000-000000000000/v2.0")
    browser, r = await world.sign_in("microsoft", forged)
    assert r.status_code == 303
    assert r.headers["location"] == "/signin?error=failed"
    assert (await browser.get("/api/v1/me")).json()["user"] is None

    # and a token without a tid cannot fill the template at all
    no_tid = microsoft("m-3", "eve@corp.ch")
    del no_tid["tid"]
    browser, r = await world.sign_in("microsoft", no_tid)
    assert r.headers["location"] == "/signin?error=failed"

    # Google: only Google's issuer
    browser, r = await world.sign_in(
        "google", google("g-9", "eve@x.ch", iss="https://evil.example")
    )
    assert r.headers["location"] == "/signin?error=failed"
    assert (await browser.get("/api/v1/me")).json()["user"] is None


async def test_a_failed_or_cancelled_sign_in_goes_back_to_sign_in(world: World) -> None:
    browser = await world.browser()
    r = await browser.get("/auth/login/google")
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    r = await browser.get(
        "/auth/callback/google", params={"error": "access_denied", "state": state}
    )
    assert r.headers["location"] == "/signin?error=cancelled"
    # a callback that this browser did not start (state unknown)
    other = await world.browser()
    r = await other.get("/auth/callback/google", params={"code": "x", "state": state})
    assert r.headers["location"] == "/signin?error=failed"
    # a provider that is not configured
    assert (await other.get("/auth/login/github")).status_code == 404


async def test_next_stays_on_this_site(world: World) -> None:
    unsafe = ("https://evil.example/x", "//evil.example/x", "/\\evil.example", "/\t/evil.example")
    for i, path in enumerate(unsafe):
        _, r = await world.sign_in("google", google(f"g-{i}", "ann@x.ch"), next_path=path)
        assert r.headers["location"] == "/"


async def test_an_unreachable_provider_fails_the_sign_in(world: World) -> None:
    def down(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route to host", request=request)

    world.app.state.oauth = build_oauth(get_settings(), transport=httpx2.MockTransport(down))
    browser = await world.browser()
    r = await browser.get("/auth/login/microsoft")
    assert r.status_code == 303
    assert r.headers["location"] == "/signin?error=failed"


async def test_admin_bootstrap_needs_a_verified_email(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    organisation_id = await migrate_organisations()
    project_id = await _add_project("Villa")
    await migrate_organisations()

    # a work account claiming the admin's email, its domain not verified: nothing
    caplog.set_level(logging.WARNING)
    browser, _ = await world.sign_in("microsoft", microsoft("m-admin", ADMIN))
    me = (await browser.get("/api/v1/me")).json()
    assert me["organisations"] == []
    assert "accounts.admin_bootstrap.unverified" in caplog.text

    # an unverified Google email: nothing either
    browser, _ = await world.sign_in("google", google("g-fake", ADMIN, verified=False))
    assert (await browser.get("/api/v1/me")).json()["organisations"] == []

    # the verified one owns the default organisation and its projects
    admin, r = await world.sign_in("google", google("g-admin", ADMIN.upper()))
    assert r.status_code == 303
    me = (await admin.get("/api/v1/me")).json()
    assert me["organisation"]["id"] == organisation_id
    assert me["organisation"]["role"] == "owner"
    async with session_factory()() as s:
        project = await s.get(Project, project_id)
        assert project is not None
        assert project.organisation_id == organisation_id

    # a later verified account with the same email gets nothing: the bootstrap happens once
    later, _ = await world.sign_in(
        "microsoft", microsoft("m-admin2", ADMIN, tid=MICROSOFT_CONSUMER_TENANT)
    )
    me = (await later.get("/api/v1/me")).json()
    assert me["user"]["email_verified"] is True
    assert me["organisations"] == []


async def test_microsoft_verified_domain_claims_the_admin_bootstrap(world: World) -> None:
    admin, _ = await world.sign_in("microsoft", microsoft("m-admin", ADMIN, xms_edov=True))
    me = (await admin.get("/api/v1/me")).json()
    assert me["user"]["email_verified"] is True
    assert [o["role"] for o in me["organisations"]] == ["owner"]


async def test_an_invitation_is_accepted_by_its_link(world: World) -> None:
    owner, _ = await world.sign_in("google", google("g-a", "a@agency.ch"))
    r = await owner.post("/api/v1/organisations", json={"name": "Agency A"})
    assert r.status_code == 201, r.text
    org = r.json()
    assert org["role"] == "owner"
    assert (await owner.get("/api/v1/me")).json()["organisation"]["id"] == org["id"]

    r = await owner.post(
        f"/api/v1/organisations/{org['id']}/invitations", json={"email": "B@Example.com"}
    )
    assert r.status_code == 201, r.text
    invitation = r.json()
    assert invitation["email"] == "b@example.com"
    assert invitation["url"].startswith("/invite/")
    token = invitation["url"].removeprefix("/invite/")
    # inviting the same address again gives the same link
    again = await owner.post(
        f"/api/v1/organisations/{org['id']}/invitations", json={"email": "b@example.com"}
    )
    assert again.json()["id"] == invitation["id"]
    assert [
        i["id"] for i in (await owner.get(f"/api/v1/organisations/{org['id']}/invitations")).json()
    ] == [invitation["id"]]

    # what the link shows, signed out
    anonymous = await world.browser()
    preview = (await anonymous.get(f"/api/v1/invitations/{token}")).json()
    assert preview["organisation_name"] == "Agency A"
    assert preview["invited_by"] == "A"
    assert preview["status"] == "pending"
    assert (await anonymous.post(f"/api/v1/invitations/{token}/accept")).status_code == 401

    # b signs in with Microsoft, with an email that is not the invited one: the link is what counts
    b, _ = await world.sign_in("microsoft", microsoft("m-b", "b.other@corp.ch"))
    assert (await b.get("/api/v1/me")).json()["organisations"] == []
    # an organisation b is not in does not exist for b
    assert (await b.get(f"/api/v1/organisations/{org['id']}/members")).status_code == 404
    r = await b.post(f"/api/v1/invitations/{token}/accept")
    assert r.status_code == 200, r.text
    assert r.json() == {**org, "role": "member"}
    me = (await b.get("/api/v1/me")).json()
    assert me["organisation"]["id"] == org["id"]
    # accepting twice is harmless; anyone else is too late
    assert (await b.post(f"/api/v1/invitations/{token}/accept")).status_code == 200
    c, _ = await world.sign_in("google", google("g-c", "c@x.ch"))
    r = await c.post(f"/api/v1/invitations/{token}/accept")
    assert r.status_code == 410
    assert r.json()["error"]["details"] == {"reason": "accepted"}
    assert (await anonymous.get(f"/api/v1/invitations/{token}")).json()["status"] == "accepted"
    assert (await anonymous.get("/api/v1/invitations/nope")).status_code == 404

    members = (await owner.get(f"/api/v1/organisations/{org['id']}/members")).json()
    assert [(m["name"], m["role"]) for m in members] == [("A", "owner"), ("Person m-b", "member")]

    # owners only: rename, invite, list invitations, remove someone else
    assert (
        await b.patch(f"/api/v1/organisations/{org['id']}", json={"name": "B's"})
    ).status_code == 403
    assert (
        await b.post(f"/api/v1/organisations/{org['id']}/invitations", json={"email": "d@x.ch"})
    ).status_code == 403
    r = await owner.patch(f"/api/v1/organisations/{org['id']}", json={"name": "Agency A SA"})
    assert r.json()["name"] == "Agency A SA"

    # revoke a pending invitation: its link stops working
    r = await owner.post(f"/api/v1/organisations/{org['id']}/invitations", json={"email": "d@x.ch"})
    d_token = r.json()["url"].removeprefix("/invite/")
    r = await owner.delete(f"/api/v1/organisations/{org['id']}/invitations/{r.json()['id']}")
    assert r.status_code == 204
    assert (await c.post(f"/api/v1/invitations/{d_token}/accept")).status_code == 404

    # switching to an organisation one is not in is refused
    r = await c.put("/api/v1/me/organisation", json={"organisation_id": org["id"]})
    assert r.status_code == 404

    # the last owner cannot leave; a member can, and an owner can remove one
    owner_id = (await owner.get("/api/v1/me")).json()["user"]["id"]
    b_id = (await b.get("/api/v1/me")).json()["user"]["id"]
    r = await owner.delete(f"/api/v1/organisations/{org['id']}/members/{owner_id}")
    assert r.status_code == 409
    r = await owner.delete(f"/api/v1/organisations/{org['id']}/members/{b_id}")
    assert r.status_code == 204
    assert (await b.get("/api/v1/me")).json()["organisation"] is None


async def test_an_expired_invitation_is_gone(world: World) -> None:
    owner, _ = await world.sign_in("google", google("g-a", "a@agency.ch"))
    org = (await owner.post("/api/v1/organisations", json={"name": "A"})).json()
    r = await owner.post(f"/api/v1/organisations/{org['id']}/invitations", json={"email": "b@x.ch"})
    token = r.json()["url"].removeprefix("/invite/")
    async with session_factory()() as s, s.begin():
        from sqlalchemy import update

        from housegen.accounts.models import Invitation

        await s.execute(update(Invitation).values(expires_at=Invitation.created_at))
    b, _ = await world.sign_in("google", google("g-b", "b@x.ch"))
    r = await b.post(f"/api/v1/invitations/{token}/accept")
    assert r.status_code == 410
    assert r.json()["error"]["details"] == {"reason": "expired"}
    assert (await owner.get(f"/api/v1/organisations/{org['id']}/invitations")).json() == []


async def _invite(owner: AsyncClient, organisation_id: str, email: str) -> str:
    r = await owner.post(
        f"/api/v1/organisations/{organisation_id}/invitations", json={"email": email}
    )
    assert r.status_code == 201, r.text
    return str(r.json()["url"].removeprefix("/invite/"))


async def test_my_invitations_are_those_sent_to_my_verified_email(world: World) -> None:
    from sqlalchemy import update

    from housegen.accounts.models import Invitation

    owner, _ = await world.sign_in("google", google("g-a", "a@agency.ch"))
    names = ("Agency A", "Agency B", "Expired", "Taken")
    orgs = {
        n: (await owner.post("/api/v1/organisations", json={"name": n})).json()["id"] for n in names
    }
    tokens = {n: await _invite(owner, orgs[n], "B@X.ch") for n in names}
    await _invite(owner, orgs["Agency A"], "someone.else@x.ch")
    async with session_factory()() as s, s.begin():
        await s.execute(
            update(Invitation)
            .where(Invitation.token == tokens["Expired"])
            .values(expires_at=Invitation.created_at)
        )
    # another account opened the "Taken" link first
    c, _ = await world.sign_in("google", google("g-c", "c@x.ch"))
    assert (await c.post(f"/api/v1/invitations/{tokens['Taken']}/accept")).status_code == 200

    anonymous = await world.browser()
    assert (await anonymous.get("/api/v1/me/invitations")).status_code == 401

    # the provider vouches for the email (any case): the pending, unexpired ones
    b, _ = await world.sign_in("google", google("g-b", "b@x.CH"))
    found = (await b.get("/api/v1/me/invitations")).json()
    assert [(i["organisation_name"], i["invited_by"]) for i in found] == [
        ("Agency A", "A"),
        ("Agency B", "A"),
    ]
    assert found[0]["token"] == tokens["Agency A"]
    assert found[0]["organisation_id"] == orgs["Agency A"]

    # the same email unverified (Google says so, or a Microsoft work account): nothing
    unverified, _ = await world.sign_in("google", google("g-u", "b@x.ch", verified=False))
    assert (await unverified.get("/api/v1/me/invitations")).json() == []
    work, _ = await world.sign_in("microsoft", microsoft("m-b", "b@x.ch"))
    assert (await work.get("/api/v1/me/invitations")).json() == []

    # joining goes through the token, as the link does; an accepted one leaves the list, and so
    # does a new invitation to an organisation the user is already in
    r = await b.post(f"/api/v1/invitations/{found[0]['token']}/accept")
    assert r.status_code == 200
    await _invite(owner, orgs["Agency A"], "b@x.ch")
    found = (await b.get("/api/v1/me/invitations")).json()
    assert [i["organisation_name"] for i in found] == ["Agency B"]


async def test_switching_between_organisations(world: World) -> None:
    user, _ = await world.sign_in("google", google("g-a", "a@x.ch"))
    first = (await user.post("/api/v1/organisations", json={"name": "First"})).json()
    second = (await user.post("/api/v1/organisations", json={"name": "Second"})).json()
    me = (await user.get("/api/v1/me")).json()
    assert me["organisation"]["id"] == second["id"]
    assert [o["name"] for o in me["organisations"]] == ["First", "Second"]
    r = await user.put("/api/v1/me/organisation", json={"organisation_id": first["id"]})
    assert r.status_code == 200
    assert r.json()["organisation"]["id"] == first["id"]
    assert (await user.get("/api/v1/me")).json()["organisation"]["id"] == first["id"]


async def test_a_project_created_while_signed_in_belongs_to_the_organisation(world: World) -> None:
    import pymupdf

    doc = pymupdf.open()
    doc.new_page()
    pdf = doc.tobytes()
    user, _ = await world.sign_in("google", google("g-a", "a@x.ch"))
    org = (await user.post("/api/v1/organisations", json={"name": "A"})).json()
    r = await user.post(
        "/api/v1/projects",
        data={"name": "Villa"},
        files=[("plans", ("p.pdf", pdf, "application/pdf"))],
    )
    assert r.status_code == 201, r.text
    anonymous = await world.browser()
    r = await anonymous.post(
        "/api/v1/projects",
        data={"name": "Chalet"},
        files=[("plans", ("p.pdf", pdf, "application/pdf"))],
    )
    assert r.status_code == 401, r.text
    async with session_factory()() as s:
        from sqlalchemy import select

        rows = dict((await s.execute(select(Project.name, Project.organisation_id))).tuples().all())
    assert rows == {"Villa": org["id"]}


async def test_no_provider_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _auth_env(
        monkeypatch,
        tmp_path / "data",
        AUTH_GOOGLE_CLIENT_ID="",
        AUTH_MICROSOFT_CLIENT_ID="",
        AUTH_SESSION_SECRET="",
    )
    await db.dispose_db()
    from housegen.main import create_app

    app = create_app()
    await db.init_db()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://testserver"
        ) as c:
            r = await c.get("/api/v1/me")
            assert r.status_code == 200
            assert r.json()["user"] is None
            assert r.json()["auth"] == {"disabled": False, "providers": []}
            assert (await c.get("/auth/login/google")).status_code == 404
            # nobody can sign in, and nothing opens without a session
            assert (await c.get("/api/v1/projects")).status_code == 401
    finally:
        await db.dispose_db()
        get_settings.cache_clear()


async def test_auth_disabled_is_one_implicit_user_in_the_default_organisation(
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
            assert me["auth"] == {"disabled": True, "providers": []}
            assert me["user"]["provider"] == "local"
            assert me["organisation"]["role"] == "owner"
            assert (await c.get("/api/v1/me")).json() == me
            assert (await c.get("/auth/login/google")).status_code == 404
        organisation_id = await migrate_organisations()
        assert me["organisation"]["id"] == organisation_id
    finally:
        await db.dispose_db()
        get_settings.cache_clear()

    # sign-in on again: a session naming the local user is nobody, and the default organisation
    # is still unowned, waiting for the admin
    _auth_env(monkeypatch, tmp_path / "data", AUTH_DISABLED="0")
    app = create_app()
    provider = FakeProvider()
    app.state.oauth = build_oauth(get_settings(), transport=provider.transport)
    await db.init_db()
    try:
        async with AsyncExitStack() as stack:
            world = World(app, provider, stack)
            admin, _ = await world.sign_in("google", google("g-admin", ADMIN))
            me = (await admin.get("/api/v1/me")).json()
            assert me["organisation"] == {**me["organisations"][0], "id": organisation_id}
            members = (await admin.get(f"/api/v1/organisations/{organisation_id}/members")).json()
            assert [m["email"] for m in members] == [ADMIN]
    finally:
        await db.dispose_db()
        get_settings.cache_clear()


def _old_database(path: Path, projects: int) -> None:
    """A database as v0.49.0 left it (dev's, before this change), with a few projects."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript((Path(__file__).parent / "fixtures" / "schema-v0.49.sql").read_text())
    con.executemany(
        "INSERT INTO projects (id, name, status, created_at, plan_pages, current_version) "
        "VALUES (?, ?, 'ready', '2026-10-01 10:00:00', 1, 1)",
        [(f"p{i}", f"Project {i}") for i in range(projects)],
    )
    con.commit()
    con.close()


async def test_migration_on_a_database_from_before_accounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    data = tmp_path / "data"
    _old_database(data / "housegen.db", projects=3)
    _auth_env(monkeypatch, data)
    await db.dispose_db()
    caplog.set_level(logging.INFO)
    try:
        await db.init_db()
        organisation_id = await migrate_organisations()
        await db.init_db()  # the next startup
        assert await migrate_organisations() == organisation_id
    finally:
        await db.dispose_db()
        get_settings.cache_clear()

    con = sqlite3.connect(data / "housegen.db")
    assert {r[0] for r in con.execute("SELECT organisation_id FROM projects")} == {organisation_id}
    assert con.execute("SELECT name, is_default, created_by FROM organisations").fetchall() == [
        ("Default organisation", 1, None)
    ]
    assert con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='ix_projects_organisation_id'"
    ).fetchone()
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"users", "organisations", "memberships", "invitations"} <= tables
    con.close()
    moved = [
        r.__dict__.get("projects_moved")
        for r in caplog.records
        if r.msg == "accounts.default_organisation"
    ]
    assert moved == [3, 0]
