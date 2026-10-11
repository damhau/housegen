"""Spike "scene as data": the layout checks from interior.json alone, without a browser.

    uv run --project backend --with shapely --with tiktoken python spikes/scene-as-data/checks.py \\
        --variants <dir> [--modules <scene src dir>]

From the floor plans' definitions and each piece's footprint and transform: rooms off the area
printed on the plan, pieces standing in no room, floor pieces overlapping. Footprints are boxes,
so this is coarser than the kit's audit (layout.js casts rays against the meshes); it shows what a
check costs on data. With --modules, also the size of the data against the code it replaces
(tokens, o200k_base).
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

from shapely.affinity import rotate, translate
from shapely.geometry import Point, Polygon, box

# pieces that hang, lie flat or stand on others: not part of the floor overlap check
NOT_ON_FLOOR = (
    "rug",
    "lamp",
    "light",
    "pendant",
    "sconce",
    "curtain",
    "art",
    "print",
    "mirror",
    "frame",
    "shelf",
    "towel",
    "accessor",
    "plant-small",
    "book",
    "vase",
    "cup",
    "plate",
    "glass",
    "teapot",
    "bowl",
    "bottle",
    "toaster",
    "coffee",
    "planter",
    "candle",
    "clock",
    "tile",
)


def yaw(q: list[float]) -> float:
    x, y, z, w = q
    return math.atan2(2 * (w * y + x * z), 1 - 2 * (y * y + x * x))


def footprint(p: dict) -> Polygon | None:
    if not p.get("footprint"):
        return None
    w, d = p["footprint"]
    s = p.get("scale", [1, 1, 1])
    g = box(-w * s[0] / 2, -d * s[2] / 2, w * s[0] / 2, d * s[2] / 2)
    g = rotate(g, -yaw(p["quaternion"]), origin=(0, 0), use_radians=True)
    return translate(g, p["position"][0], p["position"][2])


def run(data: dict) -> dict:
    rooms = []
    for fp in data["floorPlans"]:
        if not fp:
            continue
        for r in fp["rooms"]:
            rooms.append(
                {
                    "name": r["name"],
                    "y": fp["y"],
                    "area": r.get("area"),
                    "poly": Polygon(r["polygon"]),
                }
            )
    findings: list[str] = []
    for r in rooms:
        a = r["poly"].area
        if r["area"] and abs(a - r["area"]) > max(0.05 * r["area"], 0.5):
            findings.append(
                f"room area off the plan: {r['name']} {a:.1f} m² against {r['area']} m²"
            )
    placed = []
    for p in data["pieces"]:
        if p.get("kit") in ("tileWalls", "floorPlan") or p.get("light"):
            continue
        g = footprint(p)
        if g is None:
            continue
        y = p["position"][1]
        # the room on the highest floor below the piece that holds it (a garage at +0.8 m is not
        # the floor of the ground-floor table at +0.77 m)
        here = [
            r
            for r in rooms
            if r["y"] <= y + 0.05 and r["poly"].buffer(0.4).contains(Point(g.centroid))
        ]
        floor = max((r["y"] for r in here), default=None)
        room = min(
            (r for r in here if r["y"] == floor),
            key=lambda r: r["poly"].distance(g.centroid),
            default=None,
        )
        if room is None:
            # a fixture hangs from a ceiling or a wall (the kit's audit does not ask it for a room either)
            if not p.get("role"):
                findings.append(
                    f"in no room: {p['name']} at {p['position'][0]:.2f}, {p['position'][2]:.2f}"
                )
            continue
        name = (
            p["name"] + " " + str(p.get("args", [""])[0] if p.get("kit") == "model" else "")
        ).lower()
        on_floor = y - floor < 0.05 and not any(k in name for k in NOT_ON_FLOOR)
        placed.append((p, g, room["name"], on_floor))
    floor_pieces = [x for x in placed if x[3]]
    for i, (p, g, rn, _) in enumerate(floor_pieces):
        for q, h, rm, _ in floor_pieces[i + 1 :]:
            if rn != rm or not g.intersects(h):
                continue
            inter = g.intersection(h)
            depth = min(inter.bounds[2] - inter.bounds[0], inter.bounds[3] - inter.bounds[1])
            if depth > 0.01:
                findings.append(
                    f"overlap in {rn}: {p['name']} and {q['name']} by {depth * 100:.0f} cm"
                )
    return {
        "rooms": len(rooms),
        "pieces_checked": len(placed),
        "floor_pieces": len(floor_pieces),
        "findings": findings,
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--variants", type=Path, required=True)
    ap.add_argument("--modules", type=Path)
    args = ap.parse_args()
    data = json.loads((args.variants / "interior.json").read_text())
    t0 = time.perf_counter()
    res = run(data)
    ms = (time.perf_counter() - t0) * 1000
    print(
        f"checks in {ms:.0f} ms: {res['rooms']} rooms, {res['pieces_checked']} pieces ({res['floor_pieces']} on the floor)"
    )
    for f in res["findings"]:
        print(" -", f)
    out = {"ms": round(ms, 1), **res}
    if args.modules:
        import tiktoken

        enc = tiktoken.get_encoding("o200k_base")
        replaced = [
            f
            for f in sorted(args.modules.glob("*.js"))
            if f.name.startswith(("furniture", "lighting", "interior-lighting", "scandinavian"))
        ]
        code = "".join(f.read_text() for f in replaced)
        compact = json.dumps(data["pieces"], separators=(",", ":"), ensure_ascii=False)
        out["size"] = {
            "code_files": [f.name for f in replaced],
            "code_tokens": len(enc.encode(code)),
            "data_tokens": len(enc.encode(compact)),
        }
        print(
            f"tokens: the code it replaces {out['size']['code_tokens']}, the data {out['size']['data_tokens']}"
        )
    (args.variants / "checks.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
