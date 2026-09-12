"""The render service and its client: the app's RenderClient posts to the service (in-process
through an ASGI transport), which renders with a fake browser and returns the pictures; the
files land where the local renderer would have put them. Plus the token check and the fallback."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image

from housegen.core.config import get_settings
from housegen.core.exceptions import RenderError
from housegen.render import remote, service
from housegen.render.renderer import RenderResult


class FakeBrowserRenderer:
    """Stands in for the service's Playwright renderer: one JPEG per view, plus findings."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.gl = "ANGLE (NVIDIA, Tesla T4, OpenGL ES 3.2)"

    async def render(
        self,
        scene_url: str,
        views: list[str],
        out_dir: Path,
        quality: str = "high",
        camera: dict[str, float] | None = None,
    ) -> RenderResult:
        self.calls.append(
            {"scene_url": scene_url, "views": views, "quality": quality, "camera": camera}
        )
        res = RenderResult(errors=["ReferenceError: x"], console=["[warning] w"], audit=["a1"])
        for v in views:
            p = out_dir / f"{v}.jpg"
            Image.new("RGB", (64, 40), (200, 100, 50)).save(p, "JPEG")
            res.images[v] = p
        res.duration_ms = 42
        return res

    async def probe(self) -> str:
        return self.gl

    async def close(self) -> None:
        pass


@pytest.fixture
def remote_setup(monkeypatch: pytest.MonkeyPatch) -> FakeBrowserRenderer:
    s = get_settings()
    monkeypatch.setattr(s, "RENDER_SERVICE_URL", "http://render.test")
    monkeypatch.setattr(s, "RENDER_SERVICE_TOKEN", "s3cret")
    fake = FakeBrowserRenderer()
    monkeypatch.setattr(service, "renderer", fake)
    return fake


def client_to_service() -> remote.RenderClient:
    c = remote.RenderClient()
    c.remote = remote.RemoteRenderer(transport=httpx.ASGITransport(app=service.app))
    return c


async def test_round_trip(remote_setup: FakeBrowserRenderer, tmp_path: Path) -> None:
    client = client_to_service()
    out = tmp_path / "renders"
    res = await client.render(
        "http://app/scenes/p/scene/index.html",
        ["south-photo", "east-photo"],
        out,
        quality="medium",
        camera={"azimuth": 30.0},
    )
    assert sorted(res.images) == ["east-photo", "south-photo"]
    for view, path in res.images.items():
        assert path == out / f"{view}.jpg"
        assert Image.open(io.BytesIO(path.read_bytes())).size == (64, 40)
    assert res.errors == ["ReferenceError: x"]
    assert res.console == ["[warning] w"]
    assert res.audit == ["a1"]
    # the service rendered what the app asked for
    call = remote_setup.calls[0]
    assert call["views"] == ["south-photo", "east-photo"]
    assert call["quality"] == "medium"
    assert call["camera"] == {"azimuth": 30.0}
    await client.close()


async def test_bad_token_is_refused(remote_setup: FakeBrowserRenderer, tmp_path: Path) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=service.app), base_url="http://render.test"
    ) as http:
        r = await http.post("/render", json={"scene_url": "http://x", "views": ["south-photo"]})
        assert r.status_code == 401
        r = await http.post(
            "/render",
            json={"scene_url": "http://x", "views": ["south-photo"]},
            headers={"Authorization": "Bearer nope"},
        )
        assert r.status_code == 401
        health = await http.get("/health")
        assert health.status_code == 200
        assert health.json()["gl"].startswith("ANGLE (NVIDIA")
    assert remote_setup.calls == []


async def test_no_token_configured_refuses_everything(
    remote_setup: FakeBrowserRenderer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "RENDER_SERVICE_TOKEN", "")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=service.app), base_url="http://render.test"
    ) as http:
        r = await http.post(
            "/render",
            json={"scene_url": "http://x", "views": []},
            headers={"Authorization": "Bearer "},
        )
        assert r.status_code == 503


async def test_unreachable_service_falls_back_to_local(
    remote_setup: FakeBrowserRenderer, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = remote.RenderClient()
    client.remote = remote.RemoteRenderer(transport=httpx.MockTransport(refuse))
    local = FakeBrowserRenderer()
    client.local = local  # type: ignore[assignment]
    res = await client.render("http://x", ["south-photo"], tmp_path)
    assert list(res.images) == ["south-photo"]
    assert local.calls[0]["views"] == ["south-photo"]

    monkeypatch.setattr(get_settings(), "RENDER_SERVICE_FALLBACK", False)
    with pytest.raises(RenderError, match="unreachable"):
        await client.render("http://x", ["south-photo"], tmp_path)
    assert len(local.calls) == 1
    await client.close()


async def test_no_service_url_renders_locally(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(get_settings(), "RENDER_SERVICE_URL", "")
    client = remote.RenderClient()
    local = FakeBrowserRenderer()
    client.local = local  # type: ignore[assignment]

    def never(request: httpx.Request) -> httpx.Response:
        raise AssertionError("the service must not be called")

    client.remote = remote.RemoteRenderer(transport=httpx.MockTransport(never))
    res = await client.render("http://x", ["west-photo"], tmp_path)
    assert list(res.images) == ["west-photo"]
