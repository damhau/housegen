"""apply_patch: parser, hunk matching with drifted context, add/delete, atomicity, sandbox."""

from pathlib import Path

import pytest

from housegen.agent.patch import PatchError, apply_hunks, parse_patch
from housegen.agent.workspace import Workspace, WorkspaceError

SHELL = "export function a() {\n  const x = 1;\n  return x;\n}\n"


def make_ws(tmp_path: Path) -> Workspace:
    ws = Workspace(tmp_path / "scene")
    ws.write("src/shell.js", SHELL)
    ws.write("src/old.js", "export const old = 1;\n")
    return ws


def test_parse_round_trip_sections() -> None:
    patch = (
        "*** Begin Patch\n"
        "*** Update File: src/shell.js\n"
        "@@ export function a() {\n"
        "   const x = 1;\n"
        "-  return x;\n"
        "+  return x + 1;\n"
        "*** Add File: src/new.js\n"
        "+export const n = 2;\n"
        "*** Delete File: src/old.js\n"
        "*** End Patch\n"
    )
    ps = parse_patch(patch)
    assert [(p.op, p.path) for p in ps] == [
        ("update", "src/shell.js"),
        ("add", "src/new.js"),
        ("delete", "src/old.js"),
    ]
    h = ps[0].hunks[0]
    assert h.anchor == "export function a() {"
    assert h.old == ["  const x = 1;", "  return x;"]
    assert h.new == ["  const x = 1;", "  return x + 1;"]
    assert ps[1].content == "export const n = 2;\n"


def test_parse_rejects_missing_markers() -> None:
    with pytest.raises(PatchError):
        parse_patch("*** Update File: src/a.js\n-x\n+y\n")
    with pytest.raises(PatchError):
        parse_patch("*** Begin Patch\n*** End Patch\n")


def test_hunk_matches_with_drifted_whitespace() -> None:
    text = "function f() {\n\tconst  y = 2;   \n\treturn y;\n}\n"
    ps = parse_patch(
        "*** Begin Patch\n*** Update File: src/x.js\n"
        "   const y = 2;\n-  return y;\n+  return y * 2;\n*** End Patch\n"
    )
    out = apply_hunks(text, ps[0].hunks, "src/x.js")
    assert "return y * 2;" in out
    assert "return y;\n" not in out


def test_apply_add_update_delete(tmp_path: Path) -> None:
    ws = make_ws(tmp_path)
    out = ws.apply_patch(
        "*** Begin Patch\n"
        "*** Update File: src/shell.js\n"
        "@@ export function a() {\n"
        "-  const x = 1;\n"
        "+  const x = 2;\n"
        "*** Add File: src/new.js\n"
        "+export const n = 2;\n"
        "*** Delete File: src/old.js\n"
        "*** End Patch\n"
    )
    for name in ("src/shell.js", "src/new.js", "src/old.js"):
        assert name in out
    assert "const x = 2;" in ws.read("src/shell.js")
    assert ws.read("src/new.js") == "export const n = 2;\n"
    assert not ws.exists("src/old.js")


def test_failing_hunk_rejects_whole_patch(tmp_path: Path) -> None:
    ws = make_ws(tmp_path)
    with pytest.raises(WorkspaceError, match="hunk 1"):
        ws.apply_patch(
            "*** Begin Patch\n"
            "*** Add File: src/new.js\n"
            "+export const n = 2;\n"
            "*** Update File: src/shell.js\n"
            "-  const x = 999;\n"
            "+  const x = 2;\n"
            "*** End Patch\n"
        )
    assert not ws.exists("src/new.js")  # nothing written
    assert ws.read("src/shell.js") == SHELL


def test_path_escape_and_non_src_rejected(tmp_path: Path) -> None:
    ws = make_ws(tmp_path)
    for bad in ("kit/house.js", "src/../../etc/x.js", "src/a.txt"):
        with pytest.raises(WorkspaceError):
            ws.apply_patch(f"*** Begin Patch\n*** Add File: {bad}\n+x\n*** End Patch\n")
