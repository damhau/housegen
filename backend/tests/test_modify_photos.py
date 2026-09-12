"""Photos attached to a modification request (#8)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from housegen.agent import pipeline
from housegen.core import db
from housegen.core.config import get_settings
from housegen.core.db import session_factory
from housegen.jobs.manager import JobContext, job_manager
from housegen.llm.types import ImagePart
from housegen.projects import crud


def _jpeg(w: int = 640, h: int = 480) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (90, 120, 160)).save(buf, "JPEG")
    return buf.getvalue()


def _pdf() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    doc.new_page()
    return doc.tobytes()


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    await db.dispose_db()
    from housegen.main import create_app

    app = create_app()
    await db.init_db()
    monkeypatch.setattr(job_manager, "submit", lambda *a, **k: None)  # no real run
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    await db.dispose_db()
    get_settings.cache_clear()


async def _ready_project(client: AsyncClient) -> str:
    files = [("plans", ("plan.pdf", _pdf(), "application/pdf"))]
    files.append(("photos", ("n.jpg", _jpeg(), "image/jpeg")))
    files.append(("photos", ("x.jpg", _jpeg(), "image/jpeg")))
    r = await client.post(
        "/api/v1/projects", data={"name": "t", "sides": ["north", "other"]}, files=files
    )
    pid: str = r.json()["id"]
    async with session_factory()() as s, s.begin():
        await crud.add_version(s, pid, 1, "generation", "Initial build")
    return pid


async def test_modify_with_photos_stores_links_and_keeps_them_as_extras(
    client: AsyncClient,
) -> None:
    pid = await _ready_project(client)
    r = await client.post(
        f"/api/v1/projects/{pid}/modify",
        data={"message": "the parapet is like this"},
        files=[
            ("photos", ("a.jpg", _jpeg(), "image/jpeg")),
            ("photos", ("b.jpg", _jpeg(), "image/jpeg")),
        ],
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["id"]
    chat = (await client.get(f"/api/v1/projects/{pid}/chat")).json()
    assert chat[-1]["role"] == "user"
    assert chat[-1]["attachments"] == [
        f"/scenes/{pid}/photos/modify-{job_id}-1.jpg",
        f"/scenes/{pid}/photos/modify-{job_id}-2.jpg",
    ]
    project = (await client.get(f"/api/v1/projects/{pid}")).json()
    extras = [p for p in project["photos"] if p["side"] == "other"]
    assert [p["original_name"] for p in extras] == ["x.jpg", "a.jpg", "b.jpg"]
    stored = Path(get_settings().projects_dir) / pid / "photos" / f"modify-{job_id}-1.jpg"
    assert stored.exists()
    assert (stored.parent / "orig" / stored.name).exists()

    # the job carries them, and for its own pipeline they are attachments, not extras
    async with session_factory()() as s:
        job = await crud.get_job(s, job_id)
        assert job.attachments == [f"modify-{job_id}-1.jpg", f"modify-{job_id}-2.jpg"]
    run = pipeline._Run(JobContext(job_manager, job_id, pid))
    inp = await run.inputs()
    assert [p.name for p in inp.attachments] == job.attachments
    assert [p.name for p in inp.extras] == ["other1.jpg"]
    assert run.tools.images.photo("attached-2") is not None
    assert run.tools.images.photo("extra-1") is not None
    parts = pipeline._modify_message("the parapet", [], inp, {})
    labels = [p.label for p in parts if isinstance(p, ImagePart)]
    assert labels[:2] == ["Attached photograph 1", "Attached photograph 2"]
    assert any("attached to this request" in p for p in parts if isinstance(p, str))


async def test_modify_can_opt_out_of_keeping_the_photos(client: AsyncClient) -> None:
    pid = await _ready_project(client)
    r = await client.post(
        f"/api/v1/projects/{pid}/modify",
        data={"message": "like this", "keep": "false"},
        files=[("photos", ("a.jpg", _jpeg(), "image/jpeg"))],
    )
    assert r.status_code == 202, r.text
    project = (await client.get(f"/api/v1/projects/{pid}")).json()
    assert [p["original_name"] for p in project["photos"]] == ["n.jpg", "x.jpg"]
    chat = (await client.get(f"/api/v1/projects/{pid}/chat")).json()
    assert len(chat[-1]["attachments"]) == 1  # still shown on the bubble


async def test_modify_without_photos_is_still_accepted(client: AsyncClient) -> None:
    pid = await _ready_project(client)
    r = await client.post(f"/api/v1/projects/{pid}/modify", data={"message": "darker roof"})
    assert r.status_code == 202, r.text
    chat = (await client.get(f"/api/v1/projects/{pid}/chat")).json()
    assert chat[-1]["attachments"] == []


async def _reviewed_project(client: AsyncClient) -> str:
    pid = await _ready_project(client)
    async with session_factory()() as s, s.begin():
        await crud.set_version_critique(
            s,
            pid,
            1,
            78,
            {
                "overall_score": 78,
                "summary": "close",
                "done": False,
                "issues": [
                    {
                        "severity": "major",
                        "view": "east",
                        "description": "the stair base is solid",
                        "fix": "open the recess under the stair",
                    }
                ],
            },
        )
    return pid


async def test_modify_can_apply_the_review_with_a_message(client: AsyncClient) -> None:
    pid = await _reviewed_project(client)
    r = await client.post(
        f"/api/v1/projects/{pid}/modify",
        data={"message": "Add these to the scene:\n- the trampoline", "apply_review_of": "1"},
    )
    assert r.status_code == 202, r.text
    text = r.json()["request_text"]
    assert text.startswith(
        "Apply the findings of the independent review of version 1 (score 78/100)"
    )
    assert "open the recess under the stair" in text
    assert text.endswith("Also:\nAdd these to the scene:\n- the trampoline")


async def test_modify_can_apply_the_review_alone(client: AsyncClient) -> None:
    pid = await _reviewed_project(client)
    r = await client.post(f"/api/v1/projects/{pid}/modify", data={"apply_review_of": "1"})
    assert r.status_code == 202, r.text
    assert "1. [major] view=east" in r.json()["request_text"]


async def test_modify_refuses_an_empty_request_and_a_stale_review(client: AsyncClient) -> None:
    pid = await _reviewed_project(client)
    assert (
        await client.post(f"/api/v1/projects/{pid}/modify", data={"message": "  "})
    ).status_code == 422
    r = await client.post(f"/api/v1/projects/{pid}/modify", data={"apply_review_of": "2"})
    assert r.status_code in (404, 409)
