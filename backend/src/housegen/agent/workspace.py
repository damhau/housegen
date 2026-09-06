"""Sandboxed file access to a project's scene workspace (only src/*.js is editable)."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 200_000


class WorkspaceError(Exception):
    pass


class Workspace:
    def __init__(self, scene_dir: Path) -> None:
        self.root = scene_dir
        self.src = scene_dir / "src"
        self.src.mkdir(parents=True, exist_ok=True)

    def _resolve(self, rel: str) -> Path:
        rel = rel.strip().lstrip("/")
        if not rel.startswith("src/"):
            raise WorkspaceError(f"only files under src/ are editable, got '{rel}'")
        if not rel.endswith(".js"):
            raise WorkspaceError("only .js modules are allowed")
        p = (self.root / rel).resolve()
        if self.src.resolve() not in p.parents:
            raise WorkspaceError("path escapes the workspace")
        return p

    def list_files(self) -> list[dict[str, int | str]]:
        out: list[dict[str, int | str]] = []
        for p in sorted(self.src.rglob("*.js")):
            text = p.read_text(encoding="utf-8")
            out.append(
                {
                    "path": str(p.relative_to(self.root)),
                    "lines": text.count("\n") + 1,
                    "bytes": len(text),
                }
            )
        return out

    def read(self, rel: str) -> str:
        p = self._resolve(rel)
        if not p.exists():
            raise WorkspaceError(
                f"{rel} does not exist. Files: {[f['path'] for f in self.list_files()]}"
            )
        return p.read_text(encoding="utf-8")

    def write(self, rel: str, content: str) -> str:
        if len(content.encode()) > MAX_FILE_BYTES:
            raise WorkspaceError(
                f"file too large (> {MAX_FILE_BYTES} bytes); split it into modules"
            )
        p = self._resolve(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        p.write_text(content, encoding="utf-8")
        logger.info(
            "workspace.write", extra={"path": rel, "bytes": len(content), "new": not existed}
        )
        return f"{'updated' if existed else 'created'} {rel} ({content.count(chr(10)) + 1} lines)"

    def edit(self, rel: str, old: str, new: str) -> str:
        p = self._resolve(rel)
        if not p.exists():
            raise WorkspaceError(f"{rel} does not exist")
        text = p.read_text(encoding="utf-8")
        n = text.count(old)
        if n == 0:
            raise WorkspaceError(
                "old_string not found in file (it must match exactly, including whitespace)"
            )
        if n > 1:
            raise WorkspaceError(
                f"old_string is ambiguous ({n} matches); include more surrounding context"
            )
        p.write_text(text.replace(old, new, 1), encoding="utf-8")
        logger.info("workspace.edit", extra={"path": rel})
        return f"edited {rel}"

    def delete(self, rel: str) -> str:
        p = self._resolve(rel)
        if p.name == "scene.js":
            raise WorkspaceError("src/scene.js is the entry point and cannot be deleted")
        if p.exists():
            p.unlink()
        return f"deleted {rel}"

    def dump(self) -> str:
        parts = []
        for f in self.list_files():
            parts.append(f"===== {f['path']} =====\n{self.read(str(f['path']))}")
        return "\n\n".join(parts)
