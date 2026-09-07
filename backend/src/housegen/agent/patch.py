"""`apply_patch`: a multi-file edit in the Codex/OpenAI patch grammar.

    *** Begin Patch
    *** Update File: src/shell.js
    @@ optional anchor line (searched for before the hunk)
     context line
    -old line
    +new line
    *** Add File: src/entrance.js
    +whole file, every line prefixed with +
    *** Delete File: src/old.js
    *** End Patch

Hunks are located by their context (the ' ' and '-' lines), exact first, then ignoring
trailing whitespace, then ignoring all whitespace. The whole patch is rejected on the first
hunk that does not apply and nothing is written (`PatchError` says which hunk and why).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

BEGIN = "*** Begin Patch"
END = "*** End Patch"
UPDATE = "*** Update File: "
ADD = "*** Add File: "
DELETE = "*** Delete File: "
EOF_MARK = "*** End of File"


class PatchError(Exception):
    pass


@dataclass
class Hunk:
    anchor: str | None
    old: list[str]  # context + removed lines, in order
    new: list[str]  # context + added lines, in order


@dataclass
class FilePatch:
    op: str  # update | add | delete
    path: str
    hunks: list[Hunk] = field(default_factory=list)
    content: str = ""  # add


def parse_patch(text: str) -> list[FilePatch]:
    lines = text.replace("\r\n", "\n").split("\n")
    # tolerate a fenced block and blank lines around the markers
    while lines and (not lines[0].strip() or lines[0].strip().startswith("```")):
        lines.pop(0)
    while lines and (not lines[-1].strip() or lines[-1].strip().startswith("```")):
        lines.pop()
    if not lines or lines[0].strip() != BEGIN:
        raise PatchError(f"patch must start with '{BEGIN}'")
    if lines[-1].strip() != END:
        raise PatchError(f"patch must end with '{END}'")
    body = lines[1:-1]

    patches: list[FilePatch] = []
    cur: FilePatch | None = None
    hunk: Hunk | None = None

    def close_hunk() -> None:
        nonlocal hunk
        if hunk is not None and (hunk.old or hunk.new):
            assert cur is not None
            cur.hunks.append(hunk)
        hunk = None

    for raw in body:
        if raw.startswith(UPDATE) or raw.startswith(ADD) or raw.startswith(DELETE):
            close_hunk()
            op = "update" if raw.startswith(UPDATE) else "add" if raw.startswith(ADD) else "delete"
            path = raw.split(": ", 1)[1].strip()
            if not path:
                raise PatchError(f"missing path after '{raw.strip()}'")
            cur = FilePatch(op=op, path=path)
            patches.append(cur)
            continue
        if cur is None:
            if not raw.strip():
                continue
            raise PatchError(f"line outside of a file section: '{raw}'")
        if raw.startswith(EOF_MARK):
            continue
        if cur.op == "add":
            if raw.startswith("+"):
                cur.content += raw[1:] + "\n"
            elif not raw.strip():
                cur.content += "\n"
            else:
                raise PatchError(
                    f"Add File {cur.path}: every line must start with '+', got '{raw[:40]}'"
                )
            continue
        if cur.op == "delete":
            if raw.strip():
                raise PatchError(f"Delete File {cur.path}: no content is expected after it")
            continue
        # update
        if raw.startswith("@@"):
            close_hunk()
            anchor = raw[2:].strip() or None
            hunk = Hunk(anchor=anchor, old=[], new=[])
            continue
        if hunk is None:
            hunk = Hunk(anchor=None, old=[], new=[])
        if raw.startswith("-"):
            hunk.old.append(raw[1:])
        elif raw.startswith("+"):
            hunk.new.append(raw[1:])
        elif raw.startswith(" ") or raw == "":
            hunk.old.append(raw[1:])
            hunk.new.append(raw[1:])
        else:
            raise PatchError(
                f"Update File {cur.path}: hunk lines must start with ' ', '-' or '+', got '{raw[:40]}'"
            )
    close_hunk()
    if not patches:
        raise PatchError("empty patch: no '*** Update File', '*** Add File' or '*** Delete File'")
    for p in patches:
        if p.op == "update" and not p.hunks:
            raise PatchError(f"Update File {p.path}: no hunk")
    return patches


def _find(lines: list[str], block: list[str], start: int) -> int:
    """Index of `block` in `lines` at or after `start`, with decreasing strictness; -1 if absent."""
    if not block:
        return start
    norms: list[Callable[[str], str]] = [
        lambda s: s,
        lambda s: s.rstrip(),
        lambda s: "".join(s.split()),
    ]
    for norm in norms:
        nb = [norm(s) for s in block]
        n = len(nb)
        for i in range(start, len(lines) - n + 1):
            if [norm(s) for s in lines[i : i + n]] == nb:
                return i
    return -1


def apply_hunks(text: str, hunks: list[Hunk], path: str) -> str:
    lines = text.split("\n")
    pos = 0
    for n, h in enumerate(hunks, 1):
        search_from = pos
        if h.anchor:
            a = _find(lines, [h.anchor], pos)
            if a < 0:
                a = _find(lines, [h.anchor], 0)
            if a < 0:
                raise PatchError(f"{path}, hunk {n}: anchor '@@ {h.anchor}' not found in the file")
            search_from = a
        at = _find(lines, h.old, search_from)
        if at < 0 and search_from > 0:
            at = _find(lines, h.old, 0)
        if at < 0:
            first = next((s for s in h.old if s.strip()), "")
            raise PatchError(
                f"{path}, hunk {n}: context not found (starting at '{first[:60]}'); "
                "the ' ' and '-' lines must match the current file, read it again"
            )
        lines[at : at + len(h.old)] = h.new
        pos = at + len(h.new)
    return "\n".join(lines)


def apply_patch(
    text: str,
    read: Callable[[str], str],
    exists: Callable[[str], bool],
    write: Callable[[str, str], str],
    delete: Callable[[str], str],
) -> str:
    """Parse and apply atomically: everything is computed first, written only if all hunks apply.

    `read`/`exists`/`write`/`delete` are the workspace's sandboxed operations (they validate paths).
    """
    patches = parse_patch(text)
    planned: list[tuple[str, str, str]] = []  # (op, path, content)
    for p in patches:
        if p.op == "add":
            if exists(p.path):
                raise PatchError(f"Add File {p.path}: it already exists; use Update File")
            planned.append(("write", p.path, p.content))
        elif p.op == "delete":
            if not exists(p.path):
                raise PatchError(f"Delete File {p.path}: it does not exist")
            planned.append(("delete", p.path, ""))
        else:
            if not exists(p.path):
                raise PatchError(f"Update File {p.path}: it does not exist; use Add File")
            planned.append(("write", p.path, apply_hunks(read(p.path), p.hunks, p.path)))
    results = []
    for op, path, content in planned:
        results.append(write(path, content) if op == "write" else delete(path))
    return "\n".join(results)
