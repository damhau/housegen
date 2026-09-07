"""The health endpoint reports the running build (from the image's env vars)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from housegen.core.config import get_settings


@pytest.fixture
def _version_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_VERSION", "sha-abc1234")
    monkeypatch.setenv("APP_COMMIT", "abc1234def")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.usefixtures("_version_env")
async def test_health_reports_version_and_commit() -> None:
    from housegen.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json() == {
        "status": "ok",
        "version": "sha-abc1234",
        "commit": "abc1234def",
        "env": "dev",
    }
