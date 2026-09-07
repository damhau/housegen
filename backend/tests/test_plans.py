"""Several plan documents per project (#10): upload, add later, migration, sheet mapping."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from housegen.agent.pipeline import _first_message, _Inputs, plan_documents_text, sheet_label
from housegen.agent.tools import ImageSources
from housegen.core import db
from housegen.core.config import get_settings
from housegen.core.db import session_factory
from housegen.llm.types import ImagePart
from housegen.projects import crud
from housegen.projects.migrations import migrate_plans
from housegen.projects.storage import PlanSheet, ProjectStorage, migrate_plan_layout


def _pdf(pages: int = 1, text: str = "") -> bytes:
    import pymupdf

    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), f"{text} {i + 1}")
    return doc.tobytes()


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    await db.dispose_db()
    from housegen.main import create_app

    app = create_app()
    await db.init_db()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    await db.dispose_db()
    get_settings.cache_clear()


async def test_upload_several_documents_and_add_one_later(client: AsyncClient) -> None:
    files = [
        ("plans", ("original.pdf", _pdf(2), "application/pdf")),
        ("plans", ("extension.pdf", _pdf(1), "application/pdf")),
    ]
    r = await client.post(
        "/api/v1/projects",
        data={"name": "t", "plan_labels": ["original 1935", "2000 extension"]},
        files=files,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    pid = body["id"]
    assert [(d["number"], d["label"], d["pages"]) for d in body["plans"]] == [
        (1, "original 1935", 2),
        (2, "2000 extension", 1),
    ]
    assert body["plan_pages"] == 3
    assert body["plan_page_urls"] == [
        f"/scenes/{pid}/plans/1/page-1.png",
        f"/scenes/{pid}/plans/1/page-2.png",
        f"/scenes/{pid}/plans/2/page-1.png",
    ]
    assert body["plans"][1]["page_urls"] == [f"/scenes/{pid}/plans/2/page-1.png"]
    st = ProjectStorage(pid)
    assert st.plan_pdf(2).exists()
    assert [(sh.index, sh.document, sh.page) for sh in st.plan_sheets()] == [
        (1, 1, 1),
        (2, 1, 2),
        (3, 2, 1),
    ]

    r = await client.post(
        f"/api/v1/projects/{pid}/plans",
        data={"label": "2024 survey"},
        files=[("plan", ("survey.pdf", _pdf(1), "application/pdf"))],
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert [d["label"] for d in body["plans"]] == ["original 1935", "2000 extension", "2024 survey"]
    assert body["plan_pages"] == 4
    assert len(body["plan_page_urls"]) == 4
    assert st.plan_sheets()[3].document == 3

    # a label defaults to the file name; a non-PDF is refused
    r = await client.post(
        f"/api/v1/projects/{pid}/plans", files=[("plan", ("more.pdf", _pdf(1), "application/pdf"))]
    )
    assert r.json()["plans"][-1]["label"] == "more.pdf"
    r = await client.post(
        f"/api/v1/projects/{pid}/plans", files=[("plan", ("x.jpg", b"xx", "image/jpeg"))]
    )
    assert r.status_code == 422


async def test_old_layout_is_moved_at_startup_and_gets_a_document_row(
    client: AsyncClient, tmp_path: Path
) -> None:
    async with session_factory()() as s, s.begin():
        project = await crud.create_project(s, "old")
        project.plan_filename = "Plans-1935.pdf"
        project.plan_pages = 2
        pid = project.id
    root = get_settings().projects_dir / pid
    (root / "plan").mkdir(parents=True)
    (root / "plan.pdf").write_bytes(_pdf(2))
    for i in (1, 2):
        Image.new("RGB", (20, 20)).save(root / "plan" / f"page-{i}.png")

    assert migrate_plan_layout(get_settings().projects_dir) == [pid]
    assert (root / "plans" / "1" / "plan.pdf").exists()
    assert (root / "plans" / "1" / "page-2.png").exists()
    assert not (root / "plan").exists()
    assert migrate_plan_layout(get_settings().projects_dir) == []  # idempotent
    assert await migrate_plans(get_settings().projects_dir) == 1
    assert await migrate_plans(get_settings().projects_dir) == 0
    body = (await client.get(f"/api/v1/projects/{pid}")).json()
    assert [(d["number"], d["label"], d["pages"]) for d in body["plans"]] == [(1, "Plans-1935", 2)]
    assert body["plan_pages"] == 2
    assert body["plan_page_urls"] == [f"/scenes/{pid}/plans/1/page-{i}.png" for i in (1, 2)]


def test_sheets_are_captioned_with_their_document(tmp_path: Path) -> None:
    pngs = []
    for i in range(3):
        p = tmp_path / f"s{i}.png"
        Image.new("RGB", (40, 30), (255, 255, 255)).save(p)
        pngs.append(p)
    sheets = [
        PlanSheet(1, 1, 1, pngs[0], tmp_path / "a.pdf"),
        PlanSheet(2, 1, 2, pngs[1], tmp_path / "a.pdf"),
        PlanSheet(3, 2, 1, pngs[2], tmp_path / "b.pdf"),
    ]
    docs = {1: "original 1935", 2: "2024 survey"}
    assert sheet_label(3, 3, sheets, docs) == "Plan sheet 3 of 3 (document 2 “2024 survey”, page 1)"
    assert sheet_label(1, 3, sheets, {1: "only"}) == "Plan sheet 1 of 3"
    assert plan_documents_text({1: "x"}) == []
    text = plan_documents_text(docs)[0]
    assert "document 2: 2024 survey" in text
    assert "the most recent one (the last) describes the house as it is today" in text
    inp = _Inputs({}, [], pngs, "", None, sheets=sheets, documents=docs)
    parts = _first_message(inp)
    labels = [p.label for p in parts if isinstance(p, ImagePart)]
    assert labels[0] == "Plan sheet 1 of 3 (document 1 “original 1935”, page 1)"
    assert any("## Plan documents" in p for p in parts if isinstance(p, str))


def test_inspect_image_resolves_plan_n_to_its_document_page(tmp_path: Path) -> None:
    import pymupdf

    pdf_a, pdf_b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    pdf_a.write_bytes(_pdf(2, "alpha"))
    pdf_b.write_bytes(_pdf(1, "beta"))
    pngs = []
    for k, (pdf, page) in enumerate(((pdf_a, 1), (pdf_a, 2), (pdf_b, 1)), 1):
        doc = pymupdf.open(pdf)
        pix = doc[page - 1].get_pixmap(matrix=pymupdf.Matrix(1, 1), alpha=False)
        p = tmp_path / f"page-{k}.png"
        pix.save(p)
        doc.close()
        pngs.append(p)
    sheets = [
        PlanSheet(1, 1, 1, pngs[0], pdf_a),
        PlanSheet(2, 1, 2, pngs[1], pdf_a),
        PlanSheet(3, 2, 1, pngs[2], pdf_b),
    ]
    src = ImageSources(plan_sheets=sheets)
    assert src.plan_page(3) == pngs[2]
    assert src.plan_page(4) is None
    clip = src.plan_clip(3, 0, 0, 200, 100, pngs[2])
    assert clip is not None
    assert "page-3 region" in (clip.label or "")
    assert clip.size[0] > 200  # re-rendered at 300 dpi, larger than the 72 dpi sheet region
