"""A job's pictures are its own (#121): each render event's pictures are kept with the job, so a
restore and the renders of later jobs leave them as that step drew them; events from before
show what is left of theirs, a placeholder for the rest."""

from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from housegen.agent import pipeline
from housegen.core import db
from housegen.core.config import get_settings
from housegen.core.db import session_factory
from housegen.jobs import pictures
from housegen.jobs.manager import JobManager, job_manager
from housegen.llm.types import Completion, Message, ToolCallPart, ToolSpec
from housegen.projects import crud
from housegen.projects.storage import ProjectStorage
from housegen.render.renderer import RenderResult, _to_jpeg, write_replacing

PALETTE = [(220, 40, 40), (40, 200, 60), (40, 70, 220), (230, 200, 30), (200, 40, 200)]


class FakeProvider:
    """Every builder pass renders the north view, then finishes."""

    def __init__(self) -> None:
        self.turns = 0

    async def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        response_schema: dict[str, Any] | None = None,
        max_tokens: int = 16000,
        on_progress: Any = None,
        effort: str | None = None,
        cache_key: str | None = None,
    ) -> Completion:
        self.turns += 1
        if self.turns % 2:
            call = ToolCallPart(
                id=f"r{self.turns}", name="render_views", input={"views": ["north"]}
            )
        else:
            call = ToolCallPart(id=f"f{self.turns}", name="finish", input={"summary": "done"})
        return Completion(message=Message(role="assistant", content=[call]), stop_reason="tool_use")


class PaintingRenderer:
    """Each render call paints its views in the next colour of the palette, written the way the
    real renderer writes them."""

    def __init__(self) -> None:
        self.calls = 0

    async def render(
        self,
        scene_url: str,
        views: list[str],
        out_dir: Path,
        quality: str = "high",
        camera: dict[str, float] | None = None,
    ) -> RenderResult:
        if not views:
            return RenderResult()
        colour = PALETTE[self.calls % len(PALETTE)]
        self.calls += 1
        out_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 — tiny local mkdir
        png = io.BytesIO()
        Image.new("RGB", (32, 24), colour).save(png, "PNG")
        images = {v: _to_jpeg(png.getvalue(), out_dir / f"{v}.jpg", 32, 90) for v in views}
        return RenderResult(images=images)


def _colour(data: bytes) -> tuple[int, int, int]:
    with Image.open(io.BytesIO(data)) as img:
        r, g, b = img.convert("RGB").getpixel((16, 12))  # type: ignore[misc]
    return int(r), int(g), int(b)


def _close(a: tuple[int, int, int], b: tuple[int, int, int]) -> bool:
    return all(abs(x - y) <= 16 for x, y in zip(a, b, strict=True))


def _pdf() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    doc.new_page()
    return doc.tobytes()


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (90, 120, 160)).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CRITIC_MAX_ITERATIONS", "0")
    get_settings.cache_clear()
    await db.dispose_db()
    from housegen.main import create_app

    app = create_app()
    await db.init_db()
    monkeypatch.setattr(job_manager, "submit", lambda *a, **k: None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    await db.dispose_db()
    get_settings.cache_clear()


async def _run(pid: str, kind: str, body: Any, request: str = "") -> str:
    async with session_factory()() as s, s.begin():
        job_id = (await crud.create_job(s, pid, kind, request)).id
    manager = JobManager()
    manager.submit(job_id, pid, body)
    deadline = asyncio.get_running_loop().time() + 20
    while asyncio.get_running_loop().time() < deadline:
        async with session_factory()() as s:
            job = await crud.get_job(s, job_id)
        if job.status in ("done", "failed"):
            break
        await asyncio.sleep(0.02)
    assert job.status == "done", job.error
    return job_id


async def _pictures(client: AsyncClient, pid: str, job_id: str) -> list[dict[str, str]]:
    r = await client.get(f"/api/v1/projects/{pid}/jobs/{job_id}/events")
    assert r.status_code == 200, r.text
    return [e["payload"]["renders"] for e in r.json() if e["type"] == "render"]


async def test_a_jobs_pictures_survive_a_restore_and_later_renders(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = [
        ("plans", ("plan.pdf", _pdf(), "application/pdf")),
        ("photos", ("n.jpg", _jpeg(), "image/jpeg")),
    ]
    r = await client.post("/api/v1/projects", data={"name": "t", "sides": ["north"]}, files=files)
    pid: str = r.json()["id"]
    painter = PaintingRenderer()
    monkeypatch.setattr(pipeline, "renderer", painter)
    monkeypatch.setattr(pipeline, "get_provider", lambda *_: FakeProvider())

    first = await _run(pid, "generate", pipeline.generate)
    # the builder's north (colour 0), then the version's pictures (colour 1)
    shown = await _pictures(client, pid, first)
    assert len(shown) == 2
    assert set(shown[0]) == {"north"}
    assert len(shown[1]) > 1
    before = {}
    for urls in shown:
        for url in urls.values():
            assert url.startswith(f"/scenes/{pid}/jobs/{first}/renders/"), url
            r = await client.get(url)
            assert r.status_code == 200, url
            before[url] = r.content
    assert _close(_colour(before[shown[0]["north"]]), PALETTE[0])
    assert _close(_colour(before[shown[1]["north"]]), PALETTE[1])

    r = await client.post(f"/api/v1/projects/{pid}/versions/1/restore")
    assert r.status_code == 200, r.text
    second = await _run(pid, "modify", pipeline.modify, "a darker roof")

    # the first job's pictures are as its steps drew them
    assert await _pictures(client, pid, first) == shown
    for url, data in before.items():
        assert (await client.get(url)).content == data
    # the second one starts from the restored version's pictures and draws its own over them
    later = await _pictures(client, pid, second)
    assert _close(_colour((await client.get(later[0]["north"])).content), PALETTE[1])
    assert _close(_colour((await client.get(later[-1]["north"])).content), PALETTE[3])
    kept = ProjectStorage(pid).job_renders_dir(first)
    assert sorted(p.name for p in kept.iterdir()) == sorted(
        url.rsplit("/", 1)[1] for urls in shown for url in urls.values()
    )


def test_pictures_are_links_and_a_new_render_never_writes_into_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    st = ProjectStorage("p1")
    renders = st.scene_dir / "renders"
    renders.mkdir(parents=True)
    write_replacing(renders / "north.jpg", b"first")
    urls, lost = pictures.keep(st, "j1", 7, {"north": renders / "north.jpg"})
    assert urls == {"north": "/scenes/p1/jobs/j1/renders/7-north.jpg"}
    assert lost == []
    kept = st.job_renders_dir("j1") / "7-north.jpg"
    assert os.path.samefile(kept, renders / "north.jpg")
    write_replacing(renders / "north.jpg", b"second")
    assert kept.read_bytes() == b"first"
    assert not list(renders.glob(".*"))  # no temporary file left behind
    # where the file system has no hard links, a copy
    write_replacing(renders / "south.jpg", b"south")

    def no_link(*_: Any) -> None:
        raise OSError("hard links not supported")

    monkeypatch.setattr(pictures.os, "link", no_link)
    urls, lost = pictures.keep(st, "j1", 8, {"south": renders / "south.jpg"})
    assert (st.job_renders_dir("j1") / "8-south.jpg").read_bytes() == b"south"
    urls, lost = pictures.keep(st, "j1", 9, {"east": renders / "east.jpg"})
    assert urls == {}
    assert lost == ["east"]
    get_settings.cache_clear()


def _legacy(seq: int, type_: str, **payload: Any) -> dict[str, Any]:
    return {"seq": seq, "type": type_, "payload": payload, "created_at": "2026-11-17T10:00:00"}


def test_events_from_before_show_what_is_left_and_say_what_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    st = ProjectStorage("p1")
    for n in (17, 18, 19):
        (st.versions_dir / str(n) / "renders").mkdir(parents=True)
        (st.versions_dir / str(n) / "renders" / "north.jpg").write_bytes(b"v")
    (st.scene_dir / "renders").mkdir(parents=True)
    current = st.scene_dir / "renders" / "east.jpg"
    current.write_bytes(b"latest")
    mtime = int(current.stat().st_mtime)

    def old(*views: str, t: int = 1763370000) -> dict[str, str]:
        return {v: f"/scenes/p1/scene/renders/{v}.jpg?t={t}" for v in views}

    events = [
        _legacy(1, "phase", name="render", message="The current state: version 17's pictures"),
        _legacy(2, "render", renders=old("north", "south"), errors=[]),
        _legacy(3, "builder_text", text="looking", step=1),
        _legacy(4, "render", renders=old("north"), errors=[]),
        _legacy(5, "builder_step", tool="render_views", step=1),
        _legacy(6, "render", renders=old("east", t=mtime), errors=[]),
        _legacy(7, "builder_done", summary="done"),
        _legacy(8, "render", renders=old("north", "south"), errors=[]),
        _legacy(9, "version", number=18, kind="modification"),
        _legacy(10, "render", renders=old("north"), errors=[]),
        _legacy(11, "version", number=19, kind="restore"),
        _legacy(12, "render", renders={"north": "/scenes/p1/jobs/j/renders/12-north.jpg"}),
    ]
    out = [e["payload"] for e in pictures.readable(st, events)]
    # a modification's starting pictures: version 17's; south was never one of them
    assert out[1]["renders"] == {"north": "/scenes/p1/versions/17/renders/north.jpg"}
    assert out[1]["unavailable"] == ["south"]
    # the builder's own render, overwritten since: gone
    assert out[3]["renders"] == {}
    assert out[3]["unavailable"] == ["north"]
    # still the working copy's file as it was drawn: shown from there
    assert out[5]["renders"] == old("east", t=mtime)
    assert "unavailable" not in out[5]
    # the render version 18 was saved with
    assert out[7]["renders"] == {"north": "/scenes/p1/versions/18/renders/north.jpg"}
    # a restore is another job's pictures
    assert out[9]["unavailable"] == ["north"]
    # a kept picture is left as it is
    assert out[11] == events[11]["payload"]
    get_settings.cache_clear()


async def test_the_stream_replays_the_same_pictures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    await db.dispose_db()
    await db.init_db()
    async with session_factory()() as s, s.begin():
        pid = (await crud.create_project(s, "t")).id
        job_id = (await crud.create_job(s, pid, "modify", "x", status="done")).id
        for seq, kind, payload in (
            (1, "render", {"renders": {"north": f"/scenes/{pid}/scene/renders/north.jpg?t=1"}}),
            (2, "version", {"number": 1, "kind": "modification"}),
        ):
            await crud.add_job_event(s, job_id, seq, kind, payload)
    st = ProjectStorage(pid)
    (st.versions_dir / "1" / "renders").mkdir(parents=True)
    (st.versions_dir / "1" / "renders" / "north.jpg").write_bytes(b"v")
    replay = [
        e async for e in JobManager().stream(job_id, 1, lambda evs: pictures.readable(st, evs))
    ]
    assert [e["seq"] for e in replay] == [2]
    whole = [
        e async for e in JobManager().stream(job_id, 0, lambda evs: pictures.readable(st, evs))
    ]
    assert whole[0]["payload"]["renders"] == {
        "north": f"/scenes/{pid}/versions/1/renders/north.jpg"
    }
    assert json.dumps(whole)  # what the SSE route sends
    await db.dispose_db()
    get_settings.cache_clear()
