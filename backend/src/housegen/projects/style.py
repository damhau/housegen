"""The house style (#101): `src/style.js` in a scene names the style a furnished house draws in
(kit/styles.js), so switching it is a one-line change with no run. A scene whose pieces name their
own finishes keeps them whatever the style says: `literal_values` finds those, as a Restyle must
know before it promises a new look."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Style = Literal["scandinavian", "contemporary"]
STYLES: tuple[Style, ...] = ("scandinavian", "contemporary")
DEFAULT_STYLE: Style = "scandinavian"

STYLE_FILE = "src/style.js"
_STYLE_KEY = re.compile(r"""((?<![\w$-])["']?style["']?\s*:\s*)(["'])([\w-]*)\2""")
_DEFAULT_OBJECT = re.compile(r"export\s+default\s*\{")

# the options of a piece that the house style decides when they are left out, by the role they take
_ROLE_OF = {
    "finish": "fixtures",
    "fittings": "fittings",
    "handles": "handles",
    "bedding": "bedding",
    "throwColor": "bedding",
    "tiles": "wallTile",
}
_KEY = re.compile(r"(?<![\w$.])(" + "|".join(_ROLE_OF) + r")\s*:\s*")
# fixtures whose finish is their own profile (brushed aluminium, a gypsum trough): no style finish
_OWN_FINISH = re.compile(r"""["'](?:mirrorBar|coveStrip|underCabinet)["']""")
_RUG_CALL = re.compile(r"(?<![\w$])rug\s*\(\s*\{")
_CONST = re.compile(r"""\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(["'`{])""")
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_PLAIN = {"true", "false", "null", "undefined"}


@dataclass(frozen=True)
class LiteralValue:
    """A piece that names its own value for a role of the house style."""

    file: str  # src/furniture-ground.js
    line: int
    role: str  # the style's role it overrides: fixtures, fittings, wallTile…
    text: str  # the option as written: finish: "brass"


def read_style(scene_dir: Path) -> str | None:
    """The style src/style.js names (the default when it names none); None without the file."""
    p = scene_dir / STYLE_FILE
    if not p.is_file():
        return None
    m = _STYLE_KEY.search(_strip_comments(p.read_text(encoding="utf-8", errors="replace")))
    return m.group(3) if m else DEFAULT_STYLE


def write_style(scene_dir: Path, style: str) -> None:
    """Make src/style.js name `style`, keeping the owner's overrides (fixtures, rooms…) it holds."""
    p = scene_dir / STYLE_FILE
    text = p.read_text(encoding="utf-8") if p.is_file() else ""
    # found in the code without its comments, which keeps every offset
    code = _strip_comments(text)
    if m := _STYLE_KEY.search(code):
        text = text[: m.start(3)] + style + text[m.end(3) :]
    elif m := _DEFAULT_OBJECT.search(code):
        text = text[: m.end()] + f' style: "{style}",' + text[m.end() :]
    else:
        text = f'export default {{ style: "{style}" }};\n'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@dataclass(frozen=True)
class StyleState:
    style: str | None  # what src/style.js names; None without the file
    literals: list[LiteralValue]
    reason: str | None  # why a Restyle cannot switch the house; None when it can


def style_state(scene_dir: Path, interior: bool, styled: bool = True) -> StyleState:
    """Whether a Restyle can switch a saved scene to another style with no run: it has an
    interior, src/style.js, a renderer with styles (`styled`), and no piece that names its own
    value for a role of the style."""
    style = read_style(scene_dir)
    literals = literal_values(scene_dir) if interior else []
    reason = None
    if not interior:
        reason = "This version has no interior yet: furnish it to choose its style."
    elif not styled:
        reason = (
            "This version draws with a renderer that has no house styles. Furnish it again to "
            "choose its style."
        )
    elif style is None:
        reason = (
            "This house was furnished before styles: its pieces carry their own finishes, so "
            "a style would hardly change it. Furnish it again to choose its style."
        )
    elif literals:
        first = literals[0]
        more = f" and {len(literals) - 1} more" if len(literals) > 1 else ""
        reason = (
            f"Some pieces name their own finish or colour ({first.text} in {first.file}{more}): "
            "a restyle would leave them as they are. Furnish it again to give them the style's."
        )
    return StyleState(style, literals, reason)


def literal_values(scene_dir: Path) -> list[LiteralValue]:
    """Every option of a piece that names its own value where the house style would decide:
    a fixture's finish, the fittings, handles, bedding, a rug's colour, tiles of its own (an object
    or a name, written out or through a constant). Comments do not count; src/style.js is the
    style itself."""
    src = scene_dir / "src"
    if not src.is_dir():
        return []
    files = {
        p.relative_to(scene_dir).as_posix(): _strip_comments(
            p.read_text(encoding="utf-8", errors="replace")
        )
        for p in sorted(src.rglob("*.js"))
        if p.relative_to(scene_dir).as_posix() != STYLE_FILE
    }
    consts: dict[str, str] = {}
    for code in files.values():
        for m in _CONST.finditer(code):
            consts.setdefault(m.group(1), m.group(2))
    found: list[LiteralValue] = []
    for name, code in files.items():
        for m in _KEY.finditer(code):
            key = m.group(1)
            value = _value_at(code, m.end())
            if not _is_literal(value, consts, objects=key == "tiles"):
                continue
            line_start = code.rfind("\n", 0, m.start()) + 1
            line_end = code.find("\n", m.end())
            line = code[line_start : line_end if line_end >= 0 else len(code)]
            if key == "finish" and _OWN_FINISH.search(line):
                continue
            found.append(_literal(name, code, m.start(), _ROLE_OF[key], f"{key}: {value}"))
        for m in _RUG_CALL.finditer(code):
            body = _balanced(code, m.end() - 1)
            colour = re.search(r"(?<![\w$.])color\s*:\s*", body)
            if colour and _is_literal(_value_at(body, colour.end()), consts, objects=False):
                found.append(
                    _literal(
                        name,
                        code,
                        m.end() - 1 + colour.start(),
                        "rug",
                        f"rug color: {_value_at(body, colour.end())}",
                    )
                )
    return sorted(found, key=lambda v: (v.file, v.line))


def _literal(file: str, code: str, offset: int, role: str, text: str) -> LiteralValue:
    return LiteralValue(file, code.count("\n", 0, offset) + 1, role, " ".join(text.split())[:80])


def _is_literal(value: str, consts: dict[str, str], *, objects: bool) -> bool:
    if not value or value in _PLAIN:
        return False
    first = value[0]
    if first in "\"'`":
        return True
    if first == "{":
        return objects
    if _IDENT.fullmatch(value):
        kind = consts.get(value)
        return kind is not None and (kind in "\"'`" or (objects and kind == "{"))
    return False


def _value_at(code: str, i: int) -> str:
    """The value written at `i`: a string, an object (its text), or the expression up to the next
    `,`, `}`, `)` or line end."""
    if i >= len(code):
        return ""
    c = code[i]
    if c in "\"'`":
        end = _string_end(code, i)
        return code[i : end + 1]
    if c == "{":
        return _balanced(code, i)
    m = re.match(r"[^,}\)\n]*", code[i:])
    return m.group(0).strip() if m else ""


def _string_end(code: str, i: int) -> int:
    quote, j = code[i], i + 1
    while j < len(code) and code[j] != quote:
        j += 2 if code[j] == "\\" else 1
    return min(j, len(code) - 1)


def _balanced(code: str, i: int) -> str:
    """The `{…}` starting at `i`, strings skipped; to the end of the code when unbalanced."""
    depth, j = 0, i
    while j < len(code):
        c = code[j]
        if c in "\"'`":
            j = _string_end(code, j)
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return code[i : j + 1]
        j += 1
    return code[i:]


def _strip_comments(code: str) -> str:
    """The code with its comments blanked (line breaks kept, so line numbers hold); strings kept."""
    out: list[str] = []
    i, n = 0, len(code)
    while i < n:
        c = code[i]
        if c in "\"'`":
            end = _string_end(code, i)
            out.append(code[i : end + 1])
            i = end + 1
        elif code.startswith("//", i):
            end = code.find("\n", i)
            end = n if end < 0 else end
            out.append(" " * (end - i))
            i = end
        elif code.startswith("/*", i):
            end = code.find("*/", i + 2)
            end = n if end < 0 else end + 2
            out.append(re.sub(r"[^\n]", " ", code[i:end]))
            i = end
        else:
            out.append(c)
            i += 1
    return "".join(out)
