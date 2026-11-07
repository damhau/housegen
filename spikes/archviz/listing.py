"""The listing-set check (listing_prompt.md): two sets of one house's rooms, A and B shuffled per judge.

    python3 listing.py pair <round> <tag A> <tag B> <room>...   work/auto/<room><tag>-preview.png -> work/judge/<round>/j<k>/
    python3 listing.py prompt <round> <k>                        the k-th judge's prompt
    python3 listing.py tab <round> <answer file>...              unblind (j1's answer first) and print the medians

Needs Pillow (uv run --no-project --with pillow python listing.py ...).
"""
import json
import os
import random
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.join(HERE, "work", "auto")
JUDGE = os.path.join(HERE, "work", "judge")
JUDGES = 3


def main():
    cmd, rnd, *args = sys.argv[1:]
    d = os.path.join(JUDGE, rnd)
    if cmd == "pair":
        from PIL import Image
        ta, tb, *rooms = args
        for k in range(1, JUDGES + 1):
            jd = os.path.join(d, f"j{k}")
            os.makedirs(jd, exist_ok=True)
            first = random.choice((ta, tb))  # the tag shown as A
            for letter, tag in (("A", first), ("B", tb if first == ta else ta)):
                for n, room in enumerate(rooms, 1):
                    im = Image.open(os.path.join(AUTO, f"{room}{tag}-preview.png")).convert("RGB")
                    im.resize((1280, round(im.height * 1280 / im.width))).save(os.path.join(jd, f"{letter}{n}.jpg"), quality=90)
            json.dump({"A": first, "B": tb if first == ta else ta, "rooms": rooms}, open(os.path.join(jd, "key.json"), "w"))
        print(d)
    elif cmd == "prompt":
        tpl = open(os.path.join(HERE, "listing_prompt.md")).read().split("```")[1].strip("\n")
        print(tpl.replace("{dir}", os.path.join(d, f"j{args[0]}")))
    elif cmd == "tab":
        scores = {}
        for k, path in enumerate(args, 1):
            key = json.load(open(os.path.join(d, f"j{k}", "key.json")))
            text = open(path).read()
            for line, a, b in re.findall(r"\|\s*(\w+)\s*\|\s*(\d+(?:\.\d+)?)\s*\|\s*(\d+(?:\.\d+)?)\s*\|", text):
                scores.setdefault(line, {}).setdefault(key["A"], []).append(float(a))
                scores[line].setdefault(key["B"], []).append(float(b))
            print(f"j{k} (A={key['A']}, B={key['B']}):")
            print("  " + "\n  ".join(ln for ln in text.splitlines() if ln.startswith(("Repeats", "Wall art"))))
        for line, by in scores.items():
            print(f"{line:8}", "  ".join(f"{t}: {statistics.median(v):.1f} {v}" for t, v in by.items()))


if __name__ == "__main__":
    main()
