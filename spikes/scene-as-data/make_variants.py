"""Spike "scene as data": the two variants of a pulled scene's modules.

    uv run --project backend python spikes/scene-as-data/make_variants.py \\
        --scene <dir with src/> --out <dir> \\
        --piece scandinavian-details.js:roundMirror,sideboard,print --piece furniture-linen.js:linenBed \\
        --post interior-structure.js:trimRoof \\
        --calls "await furnishHouse(ctx);" "lightInteriors(ctx);"

- <out>/rec/src: the recorder. The scene's imports of housekit/furnish and housekit/interior go
  through rec-furnish.js / rec-interior.js, the scene's own piece functions (--piece) and
  post-processing steps (--post) are wrapped, and scene.js writes window.__interiorRecord after
  the furnishing calls (--calls, in scene.js, in order).
- <out>/data/src: the interpreter. The original modules, scene.js with those calls replaced by
  replay(ctx, data) over interior-data.js (written by record.py), replay-code.js naming the scene's
  own functions the data refers to.
"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
JS = HERE / "js"
KIT_IMPORT = re.compile(r"""from\s*(["'])housekit/(furnish|interior)\1""")


def parse_fns(items: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for item in items:
        module, names = item.split(":")
        out.setdefault(module, []).extend(n for n in names.split(",") if n)
    return out


def wrap_functions(text: str, module: str, names: list[str], kind: str) -> str:
    for name in names:
        # exported or not: the module's own calls go through the wrapper too
        pat = re.compile(rf"(export\s+)?(async\s+)?function\s+{name}\s*\(")
        if not pat.search(text):
            raise SystemExit(f"{module}: no function {name}")
        text = pat.sub(lambda m, n=name: f"{m.group(2) or ''}function {n}__raw(", text, count=1)
        if kind == "piece":
            spec = f'{{ code: {{ module: "{module}", fn: "{name}" }} }}'
            text += f"\nexport const {name} = __rec.wrap({spec}, {name}__raw);"
        else:
            text += f'\nexport const {name} = __rec.post("{name}", {name}__raw);'
    return 'import * as __rec from "./rec-tag.js";\n' + text


def export_missing(text: str, names: list[str]) -> str:
    """The interpreter calls the scene's functions by name: export those the module keeps private."""
    hidden = [n for n in names if not re.search(rf"export\s+(async\s+)?function\s+{n}\s*\(", text)]
    return text + (f"\nexport {{ {', '.join(hidden)} }};\n" if hidden else "")


def edit_scene(text: str, calls: list[str], first: str, last: str, head: str) -> str:
    for c in calls:
        if text.count(c) != 1:
            raise SystemExit(f"scene.js: {c!r} found {text.count(c)} times")
    i = text.index(calls[0])
    j = text.index(calls[-1]) + len(calls[-1])
    return head + text[:i] + first + text[i:j] + last + text[j:]


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--scene", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--piece", action="append", default=[])
    ap.add_argument("--post", action="append", default=[])
    ap.add_argument("--calls", nargs="+", required=True)
    args = ap.parse_args()
    src = args.scene / "src"
    pieces, posts = parse_fns(args.piece), parse_fns(args.post)

    rec = args.out / "rec" / "src"
    shutil.rmtree(rec, ignore_errors=True)
    rec.mkdir(parents=True)
    for f in sorted(src.glob("*.js")):
        text = KIT_IMPORT.sub(lambda m: f'from "./rec-{m.group(2)}.js"', f.read_text())
        if f.name in pieces:
            text = wrap_functions(text, f.name, pieces[f.name], "piece")
        if f.name in posts:
            text = wrap_functions(text, f.name, posts[f.name], "post")
        if f.name == "scene.js":
            text = edit_scene(
                text,
                args.calls,
                "const __before = new Set(ctx.group.children);\n  ",
                "\n  window.__interiorRecord = record(ctx.group, __before);",
                'import { record } from "./record.js";\n',
            )
        (rec / f.name).write_text(text)
    for name in ("rec-tag.js", "rec-furnish.js", "rec-interior.js", "record.js"):
        shutil.copy2(JS / name, rec / name)

    data = args.out / "data" / "src"
    shutil.rmtree(data, ignore_errors=True)
    data.mkdir(parents=True)
    for f in sorted(src.glob("*.js")):
        text = f.read_text()
        if f.name in pieces or f.name in posts:
            text = export_missing(text, pieces.get(f.name, []) + posts.get(f.name, []))
        (data / f.name).write_text(text)
    scene = (data / "scene.js").read_text()
    i = scene.index(args.calls[0])
    j = scene.index(args.calls[-1]) + len(args.calls[-1])
    scene = (
        'import { replay } from "./replay.js";\nimport data from "./interior-data.js";\n'
        + scene[:i]
        + "await replay(ctx, data);"
        + scene[j:]
    )
    (data / "scene.js").write_text(scene)
    mods = sorted(set(pieces) | set(posts))
    lines = [f'import * as m{k} from "./{m}";' for k, m in enumerate(mods)]
    lines.append(
        "export const CODE = { " + ", ".join(f'"{m}": m{k}' for k, m in enumerate(mods)) + " };"
    )
    post_items = [(m, n) for m, ns in posts.items() for n in ns]
    lines.append(
        "export const POST = { "
        + ", ".join(f"{n}: m{mods.index(m)}.{n}" for m, n in post_items)
        + " };"
    )
    (data / "replay-code.js").write_text("\n".join(lines) + "\n")
    for name in ("replay.js", "edits.js"):
        if (JS / name).exists():
            shutil.copy2(JS / name, data / name)
    print(f"rec: {rec}\ndata: {data}")


if __name__ == "__main__":
    main()
