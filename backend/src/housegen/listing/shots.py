"""Which rooms a listing set photographs, in which order, with which caption (#91); the exterior views' captions (#93)."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any

# kit/plan2d.js FLAT_LABEL, FLAT_FIRST and roomNameParts: the backend reads names the way the viewer does
_FLAT_WORDS = r"(?:appartement|apartment|appart\.?|app\.?|logement|flat|wohnung|unit)"
FLAT_LABEL = re.compile(rf"\b{_FLAT_WORDS}\s*([0-9A-Za-z]+)", re.IGNORECASE)
FLAT_FIRST = re.compile(rf"^{_FLAT_WORDS}\s*(?:[0-9][0-9A-Za-z]*|[A-Za-z])\b", re.IGNORECASE)
_DASH = re.compile(r"\s+[\u2014\u2013]\s+")  # em or en dash

LIVING = ("living", "kitchen-living")
# the uses the photo service dresses a room for (photo/blender objlib.ORDER): a set's "dress as" choices (#142)
DRESSABLE = ("living", "dining", "bedroom", "office", "kitchen", "bath", "hall", "wc", "laundry")
# below these, a room is too tight for a wide shot that reads (m²)
MIN_AREA = {
    "living": 9.0,
    "kitchen-living": 9.0,
    "dining": 6.0,
    "kitchen": 5.0,
    "bedroom": 7.0,
    "bath": 3.5,
}


@dataclass(frozen=True)
class Shot:
    key: str  # the photo's file stem, its place in the set
    unit: str | None  # "Appartement 1", None for a house of one unit
    room: str  # the room's full name in the scene
    title: str  # its name without the unit
    use: str
    area: float
    caption: str
    camera: str  # the best-scored viewpoint
    views: list[str] = field(
        default_factory=list
    )  # the distinct viewpoints worth a preview, best first

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def room_name_parts(name: str) -> tuple[str, str | None]:
    """'Chambre 2 — Appartement 1, étage' → ('Chambre 2', 'Appartement 1, étage'); no dash → (name, None)."""
    parts = _DASH.split(str(name or ""))
    if len(parts) < 2:
        return parts[0], None
    if FLAT_FIRST.match(parts[0].strip()) and not FLAT_LABEL.search(parts[1]):
        return " — ".join(parts[1:]), parts[0].strip()
    return parts[0], parts[1]


def unit_of(name: str) -> str | None:
    """The flat a room belongs to, as written ('Appartement 1'), None when its name gives none."""
    _, flat = room_name_parts(name)
    if not flat or not FLAT_LABEL.search(flat):
        return None
    return flat.split(",")[0].strip()


def caption(title: str, area: float) -> str:
    return f"{title}, {round(area)} m²" if area >= 0.5 else title


def _natural(text: str) -> list[tuple[int, int, str]]:
    return [
        (0, int(t), "") if t.isdigit() else (1, 0, t.lower()) for t in re.split(r"(\d+)", text) if t
    ]


def _unit_order(unit: str | None) -> tuple[int, list[tuple[int, int, str]]]:
    if unit is None:
        return (1, [])
    m = FLAT_LABEL.search(unit)
    return (0, _natural(m.group(1) if m else unit))


def _unit_rooms(rooms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A unit's rooms in listing order: living, dining (else kitchen), main bedroom, bathroom, the other bedrooms."""

    def largest(uses: tuple[str, ...]) -> dict[str, Any] | None:
        found = [r for r in rooms if r["use"] in uses]
        return max(found, key=lambda r: r["area"]) if found else None

    living = largest(LIVING)
    dining = largest(("dining",)) or largest(("kitchen",))
    main = largest(("bedroom",))
    bath = largest(("bath",))
    others = sorted(
        (r for r in rooms if r["use"] == "bedroom" and r is not main),
        key=lambda r: _natural(r["name"]),
    )
    return [r for r in (living, dining, main, bath, *others) if r is not None]


def pick(rooms: list[dict[str, Any]]) -> list[Shot]:
    """The set's shots from the prepared rooms (name, use, area, camera): each unit's rooms in listing order,
    the units in their number's order. A room without a camera, or whose best camera faces a wall (a negative
    score), or too small to photograph is left out."""
    usable = [
        r
        for r in rooms
        if r.get("camera")
        and float(r.get("score", 1)) > 0
        and r.get("use") in MIN_AREA
        and float(r.get("area") or 0) >= MIN_AREA[r["use"]]
    ]
    units: dict[str | None, list[dict[str, Any]]] = {}
    for r in usable:
        units.setdefault(unit_of(r["name"]), []).append({**r, "area": float(r.get("area") or 0)})
    shots: list[Shot] = []
    for unit in sorted(units, key=_unit_order):
        for r in _unit_rooms(units[unit]):
            shots.append(room_shot(r, f"{len(shots) + 1:02d}"))
    return shots


def room_shot(r: dict[str, Any], key: str) -> Shot:
    """A prepared room (name, use, area, camera, views) as the set's shot `key`."""
    title = room_name_parts(r["name"])[0].strip()
    area = float(r.get("area") or 0)
    return Shot(
        key=key,
        unit=unit_of(r["name"]),
        room=r["name"],
        title=title,
        use=r["use"],
        area=round(area, 1),
        caption=caption(title, area),
        camera=r["camera"],
        views=list(r.get("views") or [r["camera"]]),
    )


EXTERIOR = "exterior"  # the building from outside, shot alone as one unit (#142)


def unit_id(shot: dict[str, Any]) -> str:
    """What a one-room shoot names a photo by (#142): its room's name, `exterior` for every view from outside."""
    return EXTERIOR if shot.get("use") == "exterior" else str(shot["room"])


def listable(rooms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The prepared rooms of the house, whatever their use or size (#142): name, use, area, floor level and whether
    a viewpoint was found in them (None: the prepare did not score the room)."""
    return [
        {
            "name": r["name"],
            "use": r.get("use") or "",
            "area": round(float(r.get("area") or 0), 1),
            "y": r.get("y"),
            "camera": bool(r.get("camera")) if r.get("scored", True) else None,
        }
        for r in rooms
        if r.get("name")
    ]


def score_rule(room: dict[str, Any]) -> dict[str, float]:
    """The prepare's scoring rule (#96), widened to a room under its use's MIN_AREA, or of a use a set does not
    photograph, so that it gets viewpoints."""
    use, area = room["use"], float(room.get("area") or 0)
    if use in MIN_AREA and area >= MIN_AREA[use]:
        return dict(MIN_AREA)
    # a tenth below the rounded area the set stored: the service compares its own, unrounded figure
    return {**MIN_AREA, use: max(0.0, math.floor(area * 10) / 10 - 0.1)}


# a room's slot in its unit's listing order (_unit_rooms): living, dining or kitchen, main bedroom, bathroom,
# the other bedrooms
_SLOT = {"living": 0, "kitchen-living": 0, "dining": 1, "kitchen": 1, "bedroom": 2, "bath": 3}


def _ranks(shots: list[Shot]) -> list[tuple[Any, ...]]:
    """Each shot's place in listing order: a unit's first room of a slot holds it, the others follow it."""
    held: set[tuple[str | None, int]] = set()
    out: list[tuple[Any, ...]] = []
    for n, x in enumerate(shots):
        if x.use == "exterior":
            out.append((0, n))
            continue
        slot = _SLOT.get(x.use, 4)
        if slot == 2 and (x.unit, 2) in held:
            slot = 4
        follows = int((x.unit, slot) in held) if slot != 4 else 0
        held.add((x.unit, slot))
        out.append((1, _unit_order(x.unit), slot, follows, _natural(x.room)))
    return out


def place(shots: list[Shot], new: Shot) -> int:
    """Where a room the set left out joins it, in listing order; the set's own photos keep theirs."""
    ranks = _ranks([*shots, new])
    return next((i for i, r in enumerate(ranks[:-1]) if r > ranks[-1]), len(shots))


def next_key(shots: list[Shot]) -> str:
    """A key no photo of the set uses: a room that joins the set keeps it wherever it is placed."""
    return f"{max((int(x.key) for x in shots if x.key.isdigit()), default=0) + 1:02d}"


def house_rooms(rooms: list[dict[str, Any]], shots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every room of the house a room can be shot alone from (#142), floor by floor from the lowest, in listing
    order: `storey` counts the floors from 0, `in_set` marks the rooms the set has a photo of. A room scored
    without a viewpoint is not offered."""
    held = {x["room"] for x in shots}
    levels = sorted({round(float(r["y"]), 1) for r in rooms if r.get("y") is not None})
    found = [
        {
            **r,
            "storey": levels.index(round(float(r["y"]), 1)) if r.get("y") is not None else 0,
            "in_set": r["name"] in held,
        }
        for r in rooms
        if r["name"] in held or r.get("camera") is not False
    ]
    return sorted(
        found,
        key=lambda r: (
            r["storey"],
            _unit_order(unit_of(r["name"])),
            _SLOT.get(r["use"], 4),
            _natural(r["name"]),
        ),
    )


_FRENCH = re.compile(
    r"\b(?:salon|séjour|sejour|chambre|cuisine|salle|bain|sdb|douche|cave|entrée|buanderie)\b",
    re.IGNORECASE,
)
_GERMAN = re.compile(
    r"\b(?:wohnen|wohnzimmer|zimmer|küche|kueche|bad|schlafen|keller|eingang|essen)\b",
    re.IGNORECASE,
)
_SIDES = {
    "fr": {
        "north": "nord",
        "north-east": "nord-est",
        "east": "est",
        "south-east": "sud-est",
        "south": "sud",
        "south-west": "sud-ouest",
        "west": "ouest",
        "north-west": "nord-ouest",
    },
    "de": {
        "north": "Norden",
        "north-east": "Nordosten",
        "east": "Osten",
        "south-east": "Südosten",
        "south": "Süden",
        "south-west": "Südwesten",
        "west": "Westen",
        "north-west": "Nordwesten",
    },
}


def language(room_names: list[str]) -> str:
    """The language the plans name their rooms in: fr, de or en (the captions follow it)."""
    text = " ".join(room_names)
    fr, de = len(_FRENCH.findall(text)), len(_GERMAN.findall(text))
    if fr == de == 0:
        return "en"
    return "fr" if fr >= de else "de"


def exterior_caption(side: str, lang: str) -> str:
    """'View from the south-west', in the rooms' language."""
    if lang == "fr":
        return f"Vue extérieure, côté {_SIDES['fr'].get(side, side)}"
    if lang == "de":
        return f"Aussenansicht von {_SIDES['de'].get(side, side)}"
    return f"View from the {side}"


def exterior_shots(views: list[dict[str, Any]], keep: list[int], lang: str) -> list[Shot]:
    """The set's exterior shots E1… from the kept viewpoints (numbered from 1 in `views`), in that order."""
    out = []
    for n, i in enumerate(keep, 1):
        v = views[i - 1]
        title = exterior_caption(str(v.get("side") or ""), lang)
        out.append(
            Shot(
                key=f"E{n}",
                unit=None,
                room="exterior",
                title=title,
                use="exterior",
                area=0.0,
                caption=title,
                camera=v["camera"],
                views=[v["camera"]],
            )
        )
    return out
