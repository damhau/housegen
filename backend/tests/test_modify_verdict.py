"""A modification's verifier proposes, it does not fix (2026-09-13): its findings are stored on
the version as a review the owner can apply on request, the job ends on that version with the
verifier's score, and no builder pass runs on findings nobody has read."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from housegen.agent import pipeline
from housegen.agent.schemas import Critique, CritiqueIssue
from housegen.core import db
from housegen.core.config import get_settings
from housegen.core.db import session_factory
from housegen.jobs.manager import JobContext, job_manager
from housegen.projects import crud


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (90, 120, 160)).save(buf, "JPEG")
    return buf.getvalue()


def _pdf() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    doc.new_page()
    return doc.tobytes()


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
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


async def test_verifier_findings_become_a_review_and_no_fix_pass_runs(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = [
        ("plans", ("plan.pdf", _pdf(), "application/pdf")),
        ("photos", ("n.jpg", _jpeg(), "image/jpeg")),
    ]
    r = await client.post("/api/v1/projects", data={"name": "t", "sides": ["north"]}, files=files)
    pid: str = r.json()["id"]
    async with session_factory()() as s, s.begin():
        await crud.add_version(s, pid, 1, "modification", "darker roof")
    r = await client.post(f"/api/v1/projects/{pid}/modify", data={"message": "darker roof"})
    assert r.status_code == 202, r.text
    job_id = r.json()["id"]

    async def no_pass(*a: Any, **k: Any) -> Any:
        raise AssertionError("the verifier's findings must not start a builder pass")

    monkeypatch.setattr(pipeline._Run, "build_and_render", no_pass)
    run = await pipeline._Run.create(JobContext(job_manager, job_id, pid))
    verdict = Critique(
        overall_score=60,
        summary="the roof is unchanged",
        done=False,
        issues=[
            CritiqueIssue(
                severity="major",
                view="south",
                description="roof still light",
                fix="darken the roof material",
            )
        ],
    )
    await pipeline._modify_after_verdict(run, verdict, 1, "Darkened the roof")

    # the findings sit on the version the verifier inspected, with its score
    project = (await client.get(f"/api/v1/projects/{pid}")).json()
    v1 = next(v for v in project["versions"] if v["number"] == 1)
    assert v1["critic_score"] == 60
    assert [i["description"] for i in v1["critique"]["issues"]] == ["roof still light"]
    # the job ended there: an assistant message on that version, done with that version and score
    chat = (await client.get(f"/api/v1/projects/{pid}/chat")).json()
    assert chat[-1]["role"] == "assistant"
    assert chat[-1]["content"] == "Darkened the roof"
    async with session_factory()() as s:
        events = await crud.list_job_events(s, job_id)
    by_type = {e.type: json.loads(e.payload_json) for e in events}
    assert by_type["done"]["version"] == 1
    assert by_type["done"]["score"] == 60
    assert "phase" not in by_type  # no "Fixing what the verifier flagged"
    # the review is applied on request, like a critic's, through the existing button (the job
    # runner is stubbed here: mark the job done the way the manager does when a run ends)
    async with session_factory()() as s, s.begin():
        await crud.update_job(s, job_id, status="done")
        await crud.settle_project_status(s, pid)
    r = await client.post(f"/api/v1/projects/{pid}/modify", data={"apply_review_of": "1"})
    assert r.status_code == 202, r.text
    assert "darken the roof material" in r.json()["request_text"]
