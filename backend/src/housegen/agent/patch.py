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
trailing whitespace, then ignoring all whitespace, then ignoring quote style and trailing
`//` comments. Atomic per file (#23): a file is written only when every hunk of it applies;
the files that fail are reported with the closest line the file has, and the others are
written, so the model resends only the rejected file(s).
"""

from __future__ import annotations

import difflib
import re
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
    # (index in old, index in new) of every context line: the file's own text is kept for
    # them, so a loosely matched context line (other quotes, a comment) is not rewritten
    context: list[tuple[int, int]] = field(default_factory=list)


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
            hunk.context.append((len(hunk.old), len(hunk.new)))
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


_TRAILING_COMMENT = re.compile(r"\s*//[^'\"]*$")


def _loose(s: str) -> str:
    """Quote style and a trailing // comment do not make a different line."""
    return "".join(_TRAILING_COMMENT.sub("", s).replace("'", '"').replace("`", '"').split())


NORMS: list[Callable[[str], str]] = [
    lambda s: s,
    lambda s: s.rstrip(),
    lambda s: "".join(s.split()),
    _loose,
]


def _find(lines: list[str], block: list[str], start: int) -> int:
    """Index of `block` in `lines` at or after `start`, with decreasing strictness; -1 if absent."""
    if not block:
        return start
    for norm in NORMS:
        nb = [norm(s) for s in block]
        n = len(nb)
        for i in range(start, len(lines) - n + 1):
            if [norm(s) for s in lines[i : i + n]] == nb:
                return i
    return -1


def closest_line(lines: list[str], wanted: str) -> tuple[int, str] | None:
    """(1-based line number, text) of the file line most like `wanted`: what the model wrote
    from memory usually differs from it by a detail worth quoting back."""
    best: tuple[float, int] | None = None
    head = wanted.split()[0] if wanted.split() else ""
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        ratio = difflib.SequenceMatcher(None, wanted.strip(), line.strip()).ratio()
        if head and line.split() and line.split()[0] == head:
            ratio += 0.1  # the same leading token (import, const, the function name…)
        if best is None or ratio > best[0]:
            best = (ratio, i)
    if best is None or best[0] < 0.45:
        return None
    return best[1] + 1, lines[best[1]]


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
            hint = closest_line(lines, first)
            where = (
                f"; the closest line the file has is line {hint[0]}: '{hint[1].strip()[:80]}'"
                if hint
                else "; nothing like it is in the file"
            )
            raise PatchError(
                f"{path}, hunk {n}: context not found (starting at '{first.strip()[:60]}'){where}. "
                "The ' ' and '-' lines must match the current file: read it again and resend "
                "this file only"
            )
        replacement = list(h.new)
        for oi, ni in h.context:
            replacement[ni] = lines[at + oi]  # context: the file's line, as it really is
        lines[at : at + len(h.old)] = replacement
        pos = at + len(replacement)
    return "\n".join(lines)


def apply_patch(
    text: str,
    read: Callable[[str], str],
    exists: Callable[[str], bool],
    write: Callable[[str, str], str],
    delete: Callable[[str], str],
) -> str:
    """Parse and apply, atomic per file: every file's result is computed first; the files whose
    hunks all apply are written, the others are reported (#23). A malformed patch applies
    nothing. Raises `PatchError` when any file was rejected, saying which were written.

    `read`/`exists`/`write`/`delete` are the workspace's sandboxed operations (they validate paths).
    """
    patches = parse_patch(text)
    planned: list[tuple[str, str, str]] = []  # (op, path, content)
    rejected: list[str] = []
    for p in patches:
        try:
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
        except PatchError as e:
            rejected.append(str(e))
    results = []
    for op, path, content in planned:
        results.append(write(path, content) if op == "write" else delete(path))
    if rejected:
        applied = ", ".join(path for _, path, _ in planned) or "none"
        raise PatchError(
            f"{len(rejected)} file(s) rejected, the others were written (applied: {applied}). "
            + " | ".join(rejected)
        )
    return "\n".join(results)
