"""Invitation emails through Resend (#107), its SDK mocked: sent from the owner who invites (replies
go to them), a failed send keeps the invitation, "Send again" waits a minute, and with email off
nothing changes (the link to copy). The logs never carry the address or the key."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import resend
from httpx import AsyncClient
from resend.exceptions import ValidationError
from sqlalchemy import update

from housegen.accounts.models import Invitation
from housegen.accounts.oidc import build_oauth
from housegen.core import db
from housegen.core.config import get_settings
from housegen.core.db import session_factory
from housegen.jobs.manager import job_manager
from housegen.main import _log_auth
from test_accounts import FakeProvider, World, _auth_env, google

KEY = "re_test_secret_key"
FROM = "housegen <housegen@example.ch>"
INVITEE = "b.invitee@example.com"


class FakeResend:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.keys: list[str | None] = []
        self.fail: Exception | None = None

    def send(self, params: dict[str, Any], options: Any = None) -> dict[str, str]:
        self.keys.append(resend.api_key)
        if self.fail is not None:
            raise self.fail
        self.sent.append(params)
        return {"id": f"msg-{len(self.sent)}"}


@pytest.fixture
def fake_resend(monkeypatch: pytest.MonkeyPatch) -> FakeResend:
    fake = FakeResend()
    monkeypatch.setattr(resend.Emails, "send", fake.send)
    return fake


async def _world(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stack: AsyncExitStack, **env: str
) -> World:
    _auth_env(monkeypatch, tmp_path / "data", AUTH_PUBLIC_URL="https://housegen.example", **env)
    await db.dispose_db()
    from housegen.main import create_app

    app = create_app()
    provider = FakeProvider()
    app.state.oauth = build_oauth(get_settings(), transport=provider.transport)
    await db.init_db()
    monkeypatch.setattr(job_manager, "submit", lambda *a, **k: None)
    return World(app, provider, stack)


@pytest.fixture
async def stack() -> AsyncIterator[AsyncExitStack]:
    async with AsyncExitStack() as s:
        yield s
    await db.dispose_db()
    get_settings.cache_clear()


async def _owner(world: World) -> tuple[AsyncClient, str]:
    owner, _ = await world.sign_in("google", google("g-a", "anna@agency.ch", name="Anna Muster"))
    r = await owner.post("/api/v1/organisations", json={"name": "Agence <Lac> SA"})
    assert r.status_code == 201, r.text
    return owner, str(r.json()["id"])


def _invitations(org: str) -> str:
    return f"/api/v1/organisations/{org}/invitations"


async def test_an_invitation_is_emailed_from_the_owner_who_invites(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stack: AsyncExitStack,
    fake_resend: FakeResend,
    caplog: pytest.LogCaptureFixture,
) -> None:
    world = await _world(tmp_path, monkeypatch, stack, RESEND_API_KEY=KEY, EMAIL_FROM=FROM)
    owner, org = await _owner(world)
    assert (await owner.get("/api/v1/me")).json()["auth"]["invitation_email"] is True
    caplog.set_level(logging.INFO)
    r = await owner.post(_invitations(org), json={"email": INVITEE})
    assert r.status_code == 201, r.text
    invitation = r.json()
    assert invitation["email_sent"] == {"sent": True, "error": None}
    assert invitation["emailed_at"]
    assert fake_resend.keys == [KEY]
    [sent] = fake_resend.sent
    assert sent["from"] == FROM
    assert sent["to"] == [INVITEE]
    assert sent["reply_to"] == "anna@agency.ch"
    assert sent["subject"] == "Anna Muster invited you to Agence <Lac> SA on housegen"
    link = f"https://housegen.example{invitation['url']}"
    assert link in sent["text"]
    assert "Anna Muster (anna@agency.ch)" in sent["text"]
    assert "UTC" in sent["text"]
    assert f'href="{link}"' in sent["html"]
    assert "Agence &lt;Lac&gt; SA" in sent["html"]
    assert "<Lac>" not in sent["html"]
    assert "<img" not in sent["html"]

    # inviting the same address again: the same link, no second email
    again = await owner.post(_invitations(org), json={"email": INVITEE})
    assert again.json()["id"] == invitation["id"]
    assert again.json()["email_sent"] is None
    assert len(fake_resend.sent) == 1
    listed = (await owner.get(_invitations(org))).json()
    assert listed[0]["emailed_at"] == invitation["emailed_at"]

    sent_logs = [r for r in caplog.records if r.getMessage() == "email.sent"]
    assert [(r.invitation_id, r.message_id) for r in sent_logs] == [(invitation["id"], "msg-1")]  # type: ignore[attr-defined]
    _no_secrets_in(caplog)


async def test_a_failed_send_keeps_the_invitation_and_says_why(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stack: AsyncExitStack,
    fake_resend: FakeResend,
    caplog: pytest.LogCaptureFixture,
) -> None:
    world = await _world(tmp_path, monkeypatch, stack, RESEND_API_KEY=KEY, EMAIL_FROM=FROM)
    owner, org = await _owner(world)
    caplog.set_level(logging.INFO)
    fake_resend.fail = ValidationError(
        f"Invalid `to` field: {INVITEE} is not allowed", "validation_error", 422
    )
    r = await owner.post(_invitations(org), json={"email": INVITEE})
    assert r.status_code == 201, r.text
    outcome = r.json()["email_sent"]
    assert outcome["sent"] is False
    assert outcome["error"] == "Invalid `to` field: the recipient is not allowed"
    assert r.json()["emailed_at"] is None
    assert r.json()["email_attempted_at"]
    assert [i["id"] for i in (await owner.get(_invitations(org))).json()] == [r.json()["id"]]
    failed = [r for r in caplog.records if r.getMessage() == "email.failed"]
    assert len(failed) == 1
    assert failed[0].invitation_id == r.json()["id"]
    _no_secrets_in(caplog)


async def test_send_again_waits_a_minute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stack: AsyncExitStack, fake_resend: FakeResend
) -> None:
    world = await _world(tmp_path, monkeypatch, stack, RESEND_API_KEY=KEY, EMAIL_FROM=FROM)
    owner, org = await _owner(world)
    invitation = (await owner.post(_invitations(org), json={"email": INVITEE})).json()
    again = f"{_invitations(org)}/{invitation['id']}/send"
    r = await owner.post(again)
    assert r.status_code == 429
    error = r.json()["error"]
    assert error["details"]["reason"] == "rate_limited"
    assert 0 < error["details"]["retry_after"] <= 60
    assert len(fake_resend.sent) == 1
    # a minute later
    async with session_factory()() as s, s.begin():
        await s.execute(
            update(Invitation).values(email_attempted_at=datetime.now(UTC) - timedelta(seconds=61))
        )
    r = await owner.post(again)
    assert r.status_code == 200, r.text
    assert r.json()["email_sent"] == {"sent": True, "error": None}
    assert len(fake_resend.sent) == 2
    # a failed send counts as a try: the next waits too
    async with session_factory()() as s, s.begin():
        await s.execute(
            update(Invitation).values(email_attempted_at=datetime.now(UTC) - timedelta(seconds=61))
        )
    fake_resend.fail = RuntimeError("connection reset")
    r = await owner.post(again)
    assert r.status_code == 200
    assert r.json()["email_sent"] == {
        "sent": False,
        "error": "the email service could not be reached",
    }
    assert (await owner.post(again)).status_code == 429

    # members cannot, an unknown or revoked invitation is not found
    b, _ = await world.sign_in("google", google("g-b", "b@x.ch"))
    assert (await b.post(again)).status_code == 404
    assert (await owner.post(f"{_invitations(org)}/nope/send")).status_code == 404
    await owner.delete(f"{_invitations(org)}/{invitation['id']}")
    assert (await owner.post(again)).status_code == 404


async def test_with_email_off_the_link_is_to_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stack: AsyncExitStack, fake_resend: FakeResend
) -> None:
    world = await _world(tmp_path, monkeypatch, stack, RESEND_API_KEY=KEY, EMAIL_FROM="")
    owner, org = await _owner(world)
    assert (await owner.get("/api/v1/me")).json()["auth"]["invitation_email"] is False
    r = await owner.post(_invitations(org), json={"email": INVITEE})
    assert r.status_code == 201
    assert r.json()["email_sent"] is None
    assert r.json()["emailed_at"] is None
    r = await owner.post(f"{_invitations(org)}/{r.json()['id']}/send")
    assert r.status_code == 409
    assert r.json()["error"]["details"]["reason"] == "email_off"
    assert fake_resend.sent == []
    assert fake_resend.keys == []


@pytest.mark.parametrize(
    ("env", "missing"),
    [
        ({"RESEND_API_KEY": KEY, "EMAIL_FROM": ""}, "EMAIL_FROM"),
        ({"RESEND_API_KEY": "", "EMAIL_FROM": FROM}, "RESEND_API_KEY"),
        ({"RESEND_API_KEY": KEY, "EMAIL_FROM": FROM}, None),
        ({"RESEND_API_KEY": "", "EMAIL_FROM": ""}, None),
    ],
)
def test_half_configured_email_stays_off_with_a_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    env: dict[str, str],
    missing: str | None,
) -> None:
    _auth_env(monkeypatch, tmp_path / "data", **env)
    caplog.set_level(logging.INFO)
    try:
        settings = get_settings()
        _log_auth(settings)
        [config] = [r for r in caplog.records if r.getMessage() == "auth.config"]
        both = bool(env["RESEND_API_KEY"] and env["EMAIL_FROM"])
        assert config.invitation_email is both  # type: ignore[attr-defined]
        assert config.resend_api_key_set is bool(env["RESEND_API_KEY"])  # type: ignore[attr-defined]
        assert config.email_from_set is bool(env["EMAIL_FROM"])  # type: ignore[attr-defined]
        warned = [r for r in caplog.records if r.getMessage() == "email.half_configured"]
        assert [r.missing for r in warned] == ([missing] if missing else [])  # type: ignore[attr-defined]
        assert settings.email_enabled is both
        _no_secrets_in(caplog)
    finally:
        get_settings.cache_clear()


def _no_secrets_in(caplog: pytest.LogCaptureFixture) -> None:
    for record in caplog.records:
        line = f"{record.getMessage()} {record.__dict__}"
        assert INVITEE not in line, record.getMessage()
        assert KEY not in line, record.getMessage()
        assert "housegen@example.ch" not in line, record.getMessage()
