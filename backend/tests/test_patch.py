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


def test_failing_hunk_rejects_only_its_file(tmp_path: Path) -> None:
    """Atomic per file (#23): the good files are written, the bad one is reported with the
    closest line the file really has, so the model resends that file only."""
    ws = make_ws(tmp_path)
    with pytest.raises(WorkspaceError) as exc:
        ws.apply_patch(
            "*** Begin Patch\n"
            "*** Add File: src/new.js\n"
            "+export const n = 2;\n"
            "*** Update File: src/shell.js\n"
            "-  const x = 999;\n"
            "+  const x = 2;\n"
            "*** End Patch\n"
        )
    msg = str(exc.value)
    assert "applied: src/new.js" in msg
    assert "src/shell.js, hunk 1" in msg
    assert "closest line the file has is line 2: 'const x = 1;'" in msg
    assert "resend this file only" in msg
    assert ws.exists("src/new.js")  # the good file went in
    assert ws.read("src/shell.js") == SHELL  # the bad one is untouched


def test_context_matches_across_quote_style_and_trailing_comment() -> None:
    """The scene.js import line of #23: the model wrote it from memory with the other quotes."""
    text = 'import * as THREE from "three"; // three.js\nimport * as house from "housekit";\nexport function a() {}\n'
    ps = parse_patch(
        "*** Begin Patch\n*** Update File: src/scene.js\n"
        " import * as THREE from 'three';\n"
        "-import * as house from 'housekit';\n"
        "+import * as house from 'housekit';\n+import { buildGarden } from './garden.js';\n"
        "*** End Patch\n"
    )
    out = apply_hunks(text, ps[0].hunks, "src/scene.js")
    assert out.startswith('import * as THREE from "three"; // three.js\n')
    assert "buildGarden" in out
    # a genuinely different line is still rejected, and the closest line is quoted back
    ps = parse_patch(
        "*** Begin Patch\n*** Update File: src/scene.js\n"
        " import { Mesh } from 'three';\n-export function a() {}\n+export function b() {}\n*** End Patch\n"
    )
    with pytest.raises(PatchError, match="closest line the file has is line 1"):
        apply_hunks(text, ps[0].hunks, "src/scene.js")


def test_path_escape_and_non_src_rejected(tmp_path: Path) -> None:
    ws = make_ws(tmp_path)
    for bad in ("kit/house.js", "src/../../etc/x.js", "src/a.txt"):
        with pytest.raises(WorkspaceError):
            ws.apply_patch(f"*** Begin Patch\n*** Add File: {bad}\n+x\n*** End Patch\n")


def test_delete_then_add_rewrites_the_file(tmp_path: Path) -> None:
    """The model's "rewrite from scratch": a Delete File followed by an Add File of the same
    path in one patch must leave the new content, not a missing file (the Add used to be
    rejected against the pre-patch disk while the Delete went through)."""
    ws = make_ws(tmp_path)
    out = ws.apply_patch(
        "*** Begin Patch\n"
        "*** Delete File: src/shell.js\n"
        "*** Add File: src/shell.js\n"
        "+export function a() {\n"
        "+  return 2;\n"
        "+}\n"
        "*** End Patch\n"
    )
    assert ws.read("src/shell.js") == "export function a() {\n  return 2;\n}\n"
    assert "src/shell.js" in out


def test_add_then_update_and_add_then_delete_in_one_patch(tmp_path: Path) -> None:
    ws = make_ws(tmp_path)
    ws.apply_patch(
        "*** Begin Patch\n"
        "*** Add File: src/new.js\n"
        "+export const n = 1;\n"
        "*** Update File: src/new.js\n"
        "-export const n = 1;\n"
        "+export const n = 2;\n"
        "*** Add File: src/tmp.js\n"
        "+export const t = 1;\n"
        "*** Delete File: src/tmp.js\n"
        "*** End Patch\n"
    )
    assert ws.read("src/new.js") == "export const n = 2;\n"
    assert not ws.exists("src/tmp.js")


def test_second_add_of_the_same_path_is_still_rejected(tmp_path: Path) -> None:
    ws = make_ws(tmp_path)
    with pytest.raises(WorkspaceError) as e:
        ws.apply_patch(
            "*** Begin Patch\n"
            "*** Add File: src/new.js\n"
            "+export const n = 1;\n"
            "*** Add File: src/new.js\n"
            "+export const n = 2;\n"
            "*** End Patch\n"
        )
    assert "already exists" in str(e.value)
    assert ws.read("src/new.js") == "export const n = 1;\n"  # the first Add was written
