"""On-disk layout of a project.

data/projects/<id>/
    plans/<k>/plan.pdf     plan document k (1-based, upload order), #10
    plans/<k>/page-N.png   its rasterised sheets
    photos/<side>.jpg
    scene/                 working copy (index.html + src/*.js)
    versions/<n>/          snapshot of scene/ + renders/<view>.jpg

Projects created before #10 had plan.pdf + plan/page-N.png; `migrate_plan_layout` moves them
to plans/1/ once at startup so there is a single layout.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from housegen.core.config import get_settings
from housegen.projects.schemas import STANDARD_VIEWS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlanSheet:
    """One rasterised sheet: its position in the whole set and in its document."""

    index: int  # 1-based over all documents in order: the 'plan-N' the model knows
    document: int  # document number (1-based, upload order)
    page: int  # 1-based page within the document
    png: Path
    pdf: Path


class ProjectStorage:
    def __init__(self, project_id: str) -> None:
        s = get_settings()
        self.settings = s
        self.project_id = project_id
        self.root = s.projects_dir / project_id
        self.plans_dir = self.root / "plans"
        self.photos_dir = self.root / "photos"
        self.scene_dir = self.root / "scene"
        self.versions_dir = self.root / "versions"

    def ensure(self) -> None:
        for d in (self.root, self.plans_dir, self.photos_dir, self.scene_dir, self.versions_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ---- urls (served by main.py static mounts) ----
    def scene_url(self, version: int | None = None) -> str:
        if version is None:
            return f"/scenes/{self.project_id}/scene/index.html"
        return f"/scenes/{self.project_id}/versions/{version}/index.html"

    def version_kit(self, version: int) -> str | None:
        """The renderer snapshot a version's pictures were drawn with (None before 2026-09-12)."""
        p = self.versions_dir / str(version) / "kit.txt"
        return p.read_text(encoding="utf-8").strip() or None if p.exists() else None

    def render_urls(self, version: int) -> dict[str, str]:
        d = self.versions_dir / str(version) / "renders"
        if not d.exists():
            return {}
        return {
            p.stem: f"/scenes/{self.project_id}/versions/{version}/renders/{p.name}"
            for p in sorted(d.glob("*.jpg"))
        }

    def photo_url(self, filename: str) -> str:
        return f"/scenes/{self.project_id}/photos/{filename}"

    def plan_page_url(self, document: int, page: int) -> str:
        return f"/scenes/{self.project_id}/plans/{document}/page-{page}.png"

    def plan_page_urls(self, document: int | None = None) -> list[str]:
        """Sheet URLs in set order (all documents), or those of one document."""
        return [
            self.plan_page_url(sh.document, sh.page)
            for sh in self.plan_sheets()
            if document is None or sh.document == document
        ]

    # ---- plan documents ----
    def plan_dir(self, document: int) -> Path:
        return self.plans_dir / str(document)

    def plan_pdf(self, document: int) -> Path:
        return self.plan_dir(document) / "plan.pdf"

    def plan_documents(self) -> list[int]:
        """Document numbers present on disk, in order."""
        if not self.plans_dir.exists():
            return []
        return sorted(
            int(p.name) for p in self.plans_dir.iterdir() if p.is_dir() and p.name.isdigit()
        )

    def next_plan_document(self) -> int:
        docs = self.plan_documents()
        return (max(docs) + 1) if docs else 1

    def plan_sheets(self) -> list[PlanSheet]:
        """Every rasterised sheet of every document, set order."""
        out: list[PlanSheet] = []
        for doc in self.plan_documents():
            d = self.plan_dir(doc)
            pages = sorted(d.glob("page-*.png"), key=lambda p: int(p.stem.split("-")[1]))
            for png in pages:
                out.append(
                    PlanSheet(
                        index=len(out) + 1,
                        document=doc,
                        page=int(png.stem.split("-")[1]),
                        png=png,
                        pdf=d / "plan.pdf",
                    )
                )
        return out

    def plan_page_paths(self) -> list[Path]:
        """The sheets' PNGs in set order (what the agents receive as 'plan-N')."""
        return [sh.png for sh in self.plan_sheets()]

    # ---- inputs ----
    def rasterize_plan(self, document: int) -> int:
        """Render the PDF pages of a document to PNG. Returns the number of pages rendered."""
        s = self.settings
        pdf = self.plan_pdf(document)
        doc = pymupdf.open(pdf)
        n = min(len(doc), s.PLAN_MAX_PAGES)
        zoom = s.PLAN_DPI / 72
        for i in range(n):
            page = doc[i]
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            pix.save(pdf.parent / f"page-{i + 1}.png")
        doc.close()
        logger.info(
            "plan.rasterized",
            extra={"project_id": self.project_id, "document": document, "pages": n},
        )
        return n

    # ---- scene workspace ----
    def init_scene_from_template(self, force: bool = False) -> None:
        template = self.settings.KIT_DIR / "template"
        if force and self.scene_dir.exists():
            shutil.rmtree(self.scene_dir)
        if not (self.scene_dir / "index.html").exists():
            shutil.copytree(template, self.scene_dir, dirs_exist_ok=True)
            logger.info("scene.initialized", extra={"project_id": self.project_id})

    def snapshot(self, number: int, kit: str | None = None) -> Path:
        """Copy the working scene to versions/<number>; `kit` names the renderer snapshot its
        pictures are drawn with (kept in kit.txt, shown as the version's renderer)."""
        dst = self.versions_dir / str(number)
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(self.scene_dir, dst, ignore=shutil.ignore_patterns("renders"))
        (dst / "renders").mkdir(exist_ok=True)
        if kit:
            (dst / "kit.txt").write_text(kit + "\n", encoding="utf-8")
        logger.info("scene.snapshot", extra={"project_id": self.project_id, "version": number})
        return dst

    def restore(self, number: int) -> None:
        src = self.versions_dir / str(number)
        if not src.exists():
            raise FileNotFoundError(f"version {number} not found")
        shutil.rmtree(self.scene_dir)
        shutil.copytree(src, self.scene_dir, ignore=shutil.ignore_patterns("renders"))
        logger.info("scene.restored", extra={"project_id": self.project_id, "version": number})

    def next_version_number(self) -> int:
        nums = [int(p.name) for p in self.versions_dir.iterdir() if p.is_dir() and p.name.isdigit()]
        return (max(nums) + 1) if nums else 1

    def scene_files(self, version: int | None = None) -> dict[str, str]:
        base = self.scene_dir if version is None else self.versions_dir / str(version)
        out: dict[str, str] = {}
        for p in sorted((base / "src").rglob("*.js")):
            out[str(p.relative_to(base))] = p.read_text(encoding="utf-8")
        return out

    @staticmethod
    def standard_views() -> list[str]:
        return list(STANDARD_VIEWS)


def migrate_plan_layout(projects_dir: Path) -> list[str]:
    """One-off: move the pre-#10 layout (plan.pdf + plan/page-N.png) of every project under
    plans/1/. Returns the ids of the projects moved. Safe to run at every startup."""
    moved: list[str] = []
    if not projects_dir.exists():
        return moved
    for root in projects_dir.iterdir():
        old_pdf = root / "plan.pdf"
        old_dir = root / "plan"
        if not root.is_dir() or (root / "plans").exists() or not old_pdf.exists():
            continue
        dst = root / "plans" / "1"
        dst.mkdir(parents=True)
        shutil.move(str(old_pdf), str(dst / "plan.pdf"))
        if old_dir.exists():
            for png in old_dir.glob("page-*.png"):
                shutil.move(str(png), str(dst / png.name))
            shutil.rmtree(old_dir, ignore_errors=True)
        moved.append(root.name)
        logger.info("plan.layout_migrated", extra={"project_id": root.name})
    return moved
