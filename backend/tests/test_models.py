"""The model list behind the settings sheet: newest first, OpenAI's non-chat models left out,
a provider failure reported instead of raised, and the answer cached."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from housegen.core.config import get_settings
from housegen.llm import models as models_mod
from housegen.llm.openai_provider import is_language_model
from housegen.llm.types import ModelInfo


def test_openai_language_model_filter() -> None:
    assert is_language_model("gpt-6-astra")
    assert is_language_model("gpt-5-codex")
    assert is_language_model("o3-pro")
    for other in (
        "text-embedding-3-large",
        "gpt-4o-mini-tts",
        "whisper-1",
        "gpt-4o-realtime-preview",
        "gpt-4o-transcribe",
        "omni-moderation-latest",
        "gpt-image-1",
        "dall-e-3",
        "gpt-4o-search-preview",
        "gpt-3.5-turbo-instruct",
        "computer-use-preview",
        "sora-2",
        "babbage-002",
    ):
        assert not is_language_model(other), other


class _Provider:
    def __init__(self, models: list[ModelInfo] | None = None, fail: str | None = None) -> None:
        self.models = models or []
        self.fail = fail
        self.calls = 0

    async def list_models(self) -> list[ModelInfo]:
        self.calls += 1
        if self.fail:
            raise RuntimeError(self.fail)
        return self.models


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch):
    get_settings.cache_clear()
    models_mod.clear_cache()
    from housegen.main import create_app

    yield create_app()
    models_mod.clear_cache()
    get_settings.cache_clear()


async def test_models_endpoint_lists_and_caches(app, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Provider(
        [
            ModelInfo(id="claude-opus-5", created_at=datetime(2026, 5, 1, tzinfo=UTC)),
            ModelInfo(id="claude-sonnet-5", created_at=datetime(2026, 6, 1, tzinfo=UTC)),
        ]
    )
    monkeypatch.setattr(models_mod, "get_provider", lambda name: fake)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/models", params={"provider": "anthropic"})
        assert r.status_code == 200
        body = r.json()
        assert body["provider"] == "anthropic"
        assert [m["id"] for m in body["models"]] == ["claude-opus-5", "claude-sonnet-5"]
        assert body["error"] is None
        r = await c.get("/api/v1/models", params={"provider": "anthropic"})
        assert r.status_code == 200
    assert fake.calls == 1  # the second call was served from the cache


async def test_models_endpoint_reports_a_failure(app, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Provider(fail="no API key")
    monkeypatch.setattr(models_mod, "get_provider", lambda name: fake)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/models", params={"provider": "openai"})
        assert r.status_code == 200
        assert r.json()["models"] == []
        assert "no API key" in r.json()["error"]
        await c.get("/api/v1/models", params={"provider": "openai"})
    assert fake.calls == 2  # failures are not cached
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/models", params={"provider": "nope"})
        assert r.status_code == 422
