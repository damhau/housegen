from pathlib import Path

import pytest

from housegen.agent.workspace import Workspace, WorkspaceError


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    w = Workspace(tmp_path)
    w.write("src/scene.js", "export async function buildScene() {}\n")
    return w


def test_write_read_list(ws: Workspace) -> None:
    ws.write("src/shell.js", "export const a = 1;\n")
    assert ws.read("src/shell.js") == "export const a = 1;\n"
    assert [f["path"] for f in ws.list_files()] == ["src/scene.js", "src/shell.js"]


def test_edit_requires_unique_match(ws: Workspace) -> None:
    ws.write("src/a.js", "x = 1;\nx = 1;\n")
    with pytest.raises(WorkspaceError, match="ambiguous"):
        ws.edit("src/a.js", "x = 1;", "x = 2;")
    with pytest.raises(WorkspaceError, match="not found"):
        ws.edit("src/a.js", "nope", "x")
    ws.edit("src/a.js", "x = 1;\nx = 1;", "y = 2;")
    assert ws.read("src/a.js") == "y = 2;\n"


@pytest.mark.parametrize(
    "path", ["index.html", "../evil.js", "src/../index.js", "src/a.css", "/etc/passwd"]
)
def test_paths_outside_src_are_rejected(ws: Workspace, path: str) -> None:
    with pytest.raises(WorkspaceError):
        ws.write(path, "x")


def test_readonly_roots_are_listed_and_readable_but_not_writable(tmp_path: Path) -> None:
    kit = tmp_path / "kit"
    kit.mkdir()
    (kit / "house.js").write_text("export const x = 1;\n")
    ws = Workspace(tmp_path / "scene", readonly={"kit": kit})
    ws.write("src/scene.js", "//\n")
    assert ws.read("kit/house.js") == "export const x = 1;\n"
    listed = {f["path"]: f for f in ws.list_files()}
    assert listed["kit/house.js"]["readonly"] == "yes"
    with pytest.raises(WorkspaceError):
        ws.write("kit/house.js", "hack")
    with pytest.raises(WorkspaceError):
        ws.read("kit/../scene/src/scene.js")


def test_entry_point_cannot_be_deleted(ws: Workspace) -> None:
    with pytest.raises(WorkspaceError):
        ws.delete("src/scene.js")
