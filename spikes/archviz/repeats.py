"""How much a set of rooms repeats itself, from the `PHOTO seen:` lines of a Modal run's log (stage.report): the objects
each frame shows, and those in more than one frame. The building's fixed furniture counts as it is seen.

    python3 repeats.py work/auto/<tag>.log [work/auto/<other>.log...] [--least 0.05] [--rooms a,b,c]

--least: an object under this percent of the frame doesn't count as seen.
--rooms: only these frames (one house: a repeat only matters within one listing).
"""

import collections
import re
import sys


def frames(path, least):
    out, room = {}, None
    for line in open(path, encoding="utf-8", errors="replace"):
        m = re.match(r"== (\S+):", line)
        if m:
            room = m.group(1)
            out[room] = {}
        m = re.match(r"PHOTO seen: ([\d.]+) (.+)$", line.strip())
        if m and room and float(m.group(1)) >= least:
            out[room][m.group(2)] = float(m.group(1))
    return out


def main():
    args = sys.argv[1:]
    least = 0.05
    if "--least" in args:
        k = args.index("--least")
        least = float(args[k + 1])
        del args[k:k + 2]
    only = None
    if "--rooms" in args:
        k = args.index("--rooms")
        only = set(args[k + 1].split(","))
        del args[k:k + 2]
    for path in args:
        fr = {r: f for r, f in frames(path, least).items() if only is None or r in only}
        seen = collections.Counter(k for f in fr.values() for k in f)
        twice = {k: c for k, c in seen.items() if c > 1}
        total = sum(seen.values())
        print(f"{path}: {len(fr)} frames, {total} objects seen, {len(seen)} different, {len(twice)} in more than one frame "
              f"({sum(twice.values())} of the {total} sightings)")
        for room, f in fr.items():
            print(f"  {room}: {len(f)} objects, {sum(1 for k in f if seen[k] > 1)} also elsewhere")
        for k, c in sorted(twice.items(), key=lambda t: (-t[1], t[0])):
            print(f"    {c}x {k}")


if __name__ == "__main__":
    main()
