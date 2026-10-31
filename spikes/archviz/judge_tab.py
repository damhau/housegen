"""Unblind a judged round and tabulate it: per-line medians and means, ours against the reference.

    judge_tab.py <round dir> [<round dir> ...]

Reads <round>/key.json (which letter was ours) and <round>/j<k>/answer.md (the judge's reply, saved verbatim).
Prints one markdown table per round plus the weakest points named for ours.
"""

import json
import re
import statistics
import sys
from pathlib import Path

LINES = ["Daylight", "Materials", "Decor", "View", "Grade", "Overall"]


def parse(text: str) -> tuple[dict, dict]:
    scores: dict[str, dict[str, int]] = {"A": {}, "B": {}}
    for line in LINES:
        m = re.search(rf"\|\s*{line}\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|", text)
        if m:
            scores["A"][line], scores["B"][line] = int(m[1]), int(m[2])
    weak = {k: (re.search(rf"{k} weakest point:\s*(.+)", text) or [None, ""])[1].strip() for k in "AB"}
    return scores, weak


def tab(rd: Path) -> None:
    key = json.loads((rd / "key.json").read_text())
    ours: dict[str, list[int]] = {k: [] for k in LINES}
    ref: dict[str, list[int]] = {k: [] for k in LINES}
    weak = []
    for j, letter in sorted(key.items()):
        f = rd / j / "answer.md"
        if not f.exists():
            print(f"  {rd.name}/{j}: no answer.md")
            continue
        scores, w = parse(f.read_text())
        other = "B" if letter == "A" else "A"
        for k in LINES:
            if k in scores[letter]:
                ours[k].append(scores[letter][k])
                ref[k].append(scores[other][k])
        weak.append(f"{j}: {w[letter]}")
    n = len(ours["Overall"])
    print(f"\n### {rd.name} ({n} judges)\n")
    print("| line | ours | median | mean | reference median |")
    print("|---|---|---|---|---|")
    for k in LINES:
        if ours[k]:
            print(f"| {k} | {' / '.join(map(str, ours[k]))} | {statistics.median(ours[k]):g} | "
                  f"{statistics.mean(ours[k]):.1f} | {statistics.median(ref[k]):g} |")
    print("\nWeakest point named for ours:")
    for w in weak:
        print(f"- {w}")


for arg in sys.argv[1:]:
    tab(Path(arg))
