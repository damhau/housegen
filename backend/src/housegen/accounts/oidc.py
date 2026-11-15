"""The sign-in providers (#104): OpenID Connect through Authlib's Starlette client, configured
from their discovery documents."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

import httpx2
from authlib.integrations.starlette_client import OAuth

from housegen.core.config import Settings

ProviderName = Literal["google", "microsoft"]

GOOGLE_METADATA_URL = "https://accounts.google.com/.well-known/openid-configuration"
GOOGLE_ISSUER = "https://accounts.google.com"
# Google documents both forms of `iss` for its ID tokens
GOOGLE_ISSUERS = (GOOGLE_ISSUER, "accounts.google.com")
# `common`: work and school accounts of any tenant, and personal accounts
MICROSOFT_METADATA_URL = (
    "https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration"
)
# every personal Microsoft account signs in from this tenant, and Microsoft verified its email
MICROSOFT_CONSUMER_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"


def configured_providers(settings: Settings) -> list[ProviderName]:
    """The providers people can sign in with: those with a client id (none while disabled)."""
    if settings.AUTH_DISABLED:
        return []
    names: list[ProviderName] = []
    if settings.AUTH_GOOGLE_CLIENT_ID:
        names.append("google")
    if settings.AUTH_MICROSOFT_CLIENT_ID:
        names.append("microsoft")
    return names


def build_oauth(settings: Settings, transport: httpx2.AsyncBaseTransport | None = None) -> OAuth:
    """The Authlib registry of the configured providers (`transport` = a fake one in tests)."""
    oauth = OAuth()
    credentials = {
        "google": (
            settings.AUTH_GOOGLE_CLIENT_ID,
            settings.AUTH_GOOGLE_CLIENT_SECRET,
            GOOGLE_METADATA_URL,
        ),
        "microsoft": (
            settings.AUTH_MICROSOFT_CLIENT_ID,
            settings.AUTH_MICROSOFT_CLIENT_SECRET,
            MICROSOFT_METADATA_URL,
        ),
    }
    for name in configured_providers(settings):
        client_id, client_secret, metadata_url = credentials[name]
        client_kwargs: dict[str, Any] = {
            "scope": "openid email profile",
            "code_challenge_method": "S256",
        }
        if transport is not None:
            client_kwargs["transport"] = transport
        oauth.register(
            name,
            client_id=client_id,
            client_secret=client_secret,
            server_metadata_url=metadata_url,
            client_kwargs=client_kwargs,
            authorize_params={"prompt": "select_account"},
        )
    return oauth


async def claims_options(provider: str, client: Any) -> dict[str, Any]:
    """How the ID token's `iss` is checked. Microsoft `common` publishes a templated issuer
    (`https://login.microsoftonline.com/{tenantid}/v2.0`) that no token carries: the token's
    `iss` must be that template filled with its own `tid`."""
    if provider == "google":
        return {"iss": {"essential": True, "values": list(GOOGLE_ISSUERS)}}
    metadata = await client.load_server_metadata()
    template = str(metadata["issuer"])

    def tenant_issuer(claims: Mapping[str, Any], value: object) -> bool:
        tid = claims.get("tid")
        return isinstance(tid, str) and bool(tid) and value == template.replace("{tenantid}", tid)

    return {"iss": {"essential": True, "validate": tenant_issuer}}


@dataclass(frozen=True)
class Identity:
    provider: ProviderName
    issuer: str
    subject: str
    email: str | None
    email_verified: bool
    name: str
    picture: str | None


def _true(value: object) -> bool:
    return value is True or value in ("true", "True", "1")


def identity(provider: ProviderName, claims: Mapping[str, Any]) -> Identity:
    """Who signed in, from validated ID token claims. An email counts as verified only when the
    provider vouches for it: Google's `email_verified`; for Microsoft a personal account (the
    consumer tenant) or `xms_edov` (the tenant verified the email's domain)."""
    email = claims.get("email") or None
    if provider == "google":
        return Identity(
            provider=provider,
            issuer=GOOGLE_ISSUER,
            subject=str(claims["sub"]),
            email=email,
            email_verified=bool(email) and _true(claims.get("email_verified")),
            name=str(claims.get("name") or email or ""),
            picture=claims.get("picture") or None,
        )
    verified = claims.get("tid") == MICROSOFT_CONSUMER_TENANT or _true(claims.get("xms_edov"))
    username = str(claims.get("preferred_username") or "")
    return Identity(
        provider=provider,
        issuer=str(claims["iss"]),
        subject=str(claims["sub"]),
        # without the email claim the sign-in name stands in, for display only
        email=email or (username if "@" in username else None),
        email_verified=bool(email) and verified,
        name=str(claims.get("name") or username or email or ""),
        picture=None,
    )
