"""On-disk layout of a project.

data/projects/<id>/
    plan.pdf
    plan/page-1.png ...
    photos/<side>.jpg
    scene/                 working copy (index.html + src/*.js)
    versions/<n>/          snapshot of scene/ + renders/<view>.jpg
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pymupdf

from housegen.core.config import get_settings
from housegen.projects.schemas import STANDARD_VIEWS

logger = logging.getLogger(__name__)


class ProjectStorage:
    def __init__(self, project_id: str) -> None:
        s = get_settings()
        self.settings = s
        self.project_id = project_id
        self.root = s.projects_dir / project_id
        self.plan_pdf = self.root / "plan.pdf"
        self.plan_dir = self.root / "plan"
        self.photos_dir = self.root / "photos"
        self.scene_dir = self.root / "scene"
        self.versions_dir = self.root / "versions"

    def ensure(self) -> None:
        for d in (self.root, self.plan_dir, self.photos_dir, self.scene_dir, self.versions_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ---- urls (served by main.py static mounts) ----
    def scene_url(self, version: int | None = None) -> str:
        if version is None:
            return f"/scenes/{self.project_id}/scene/index.html"
        return f"/scenes/{self.project_id}/versions/{version}/index.html"

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

    def plan_page_urls(self, pages: int) -> list[str]:
        return [f"/scenes/{self.project_id}/plan/page-{i + 1}.png" for i in range(pages)]

    def plan_page_paths(self) -> list[Path]:
        return sorted(self.plan_dir.glob("page-*.png"), key=lambda p: int(p.stem.split("-")[1]))

    # ---- inputs ----
    def rasterize_plan(self) -> int:
        """Render PDF pages to PNG. Returns the number of pages rendered."""
        s = self.settings
        doc = pymupdf.open(self.plan_pdf)
        n = min(len(doc), s.PLAN_MAX_PAGES)
        zoom = s.PLAN_DPI / 72
        for i in range(n):
            page = doc[i]
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            pix.save(self.plan_dir / f"page-{i + 1}.png")
        doc.close()
        logger.info("plan.rasterized", extra={"project_id": self.project_id, "pages": n})
        return n

    # ---- scene workspace ----
    def init_scene_from_template(self, force: bool = False) -> None:
        template = self.settings.KIT_DIR / "template"
        if force and self.scene_dir.exists():
            shutil.rmtree(self.scene_dir)
        if not (self.scene_dir / "index.html").exists():
            shutil.copytree(template, self.scene_dir, dirs_exist_ok=True)
            logger.info("scene.initialized", extra={"project_id": self.project_id})

    def snapshot(self, number: int) -> Path:
        dst = self.versions_dir / str(number)
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(self.scene_dir, dst, ignore=shutil.ignore_patterns("renders"))
        (dst / "renders").mkdir(exist_ok=True)
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
