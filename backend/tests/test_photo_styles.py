"""The listing photos' objects and wall art by house style (#100): every piece that can come in names the styles it
suits, every role a room may ask for has two pieces per style but the thin ones, and a house's allocation draws only
its style's pieces and accents (photo/blender/objlib.py, artlib.py: the allocation runs without Blender)."""

from __future__ import annotations

import importlib
import json
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
CATALOGUE = ROOT / "spikes" / "archviz" / "made" / "objects.json"
STYLES = ("scandinavian", "contemporary")
# a role and a style with fewer than two pieces, and how many (the library holds no more: a basket in dark rattan or
# black, a black or stone canister, a small modern clock, a light wooden tray); no rule asks for a book_row
THIN = {
    "basket": {"contemporary": 1},
    "canister": {"contemporary": 1},
    "clock_small": {"scandinavian": 1, "contemporary": 1},
    "tray": {"scandinavian": 1},
    "book_row": {"scandinavian": 1, "contemporary": 1},
}
HOUSE = [
    {"name": n, "use": u, "y": 0.0}
    for n, u in [
        ("Séjour", "living"),
        ("Coin repas", "dining"),
        ("Cuisine", "kitchen"),
        ("Chambre 1", "bedroom"),
        ("Chambre 2", "bedroom"),
        ("Bureau", "office"),
        ("Bain", "bath"),
        ("Hall", "hall"),
        ("WC", "wc"),
    ]
]


@pytest.fixture
def libs(monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, ModuleType]:
    monkeypatch.syspath_prepend(str(ROOT / "photo" / "blender"))
    return importlib.import_module("objlib"), importlib.import_module("artlib")


def _library() -> list[dict[str, Any]]:
    return [e for e in json.loads(CATALOGUE.read_text(encoding="utf-8")) if not e.get("off")]


def test_every_library_object_names_the_styles_it_suits() -> None:
    for e in _library():
        styles = e.get("styles")
        assert isinstance(styles, list), e["id"]
        assert len(set(styles)) == len(styles), e["id"]
        assert set(styles) <= set(STYLES), e["id"]


def test_every_role_has_two_objects_per_style_but_the_thin_ones() -> None:
    count: dict[str, Counter[str]] = {}
    for e in _library():
        count.setdefault(e["role"], Counter()).update(e["styles"])
    for role, per in count.items():
        for style in STYLES:
            thin = THIN.get(role, {}).get(style)
            if thin:
                assert per[style] == thin, f"{role} in {style}: {per[style]}, THIN says {thin}"
            else:
                assert per[style] >= 2, f"{role} in {style}: {per[style]}"


def test_every_print_names_its_styles_and_every_room_has_two_per_style(
    libs: tuple[ModuleType, ModuleType],
) -> None:
    _, artlib = libs
    raw = [
        e
        for f in ("museum.json", "gen.json")
        for e in json.loads((Path(artlib.DIR) / f).read_text(encoding="utf-8"))
        if e.get("tone", 1.0) >= artlib.DARK
    ]
    for e in raw:
        assert isinstance(e.get("styles"), list), e["id"]
        assert set(e["styles"]) <= set(STYLES), e["id"]
    for style in STYLES:
        prints = artlib.load(style)
        assert all(style in e["styles"] for e in prints)
        for use in artlib.ORDER:
            assert sum(artlib.suits(e, use) for e in prints) >= 2, f"{use} in {style}"


def test_a_house_draws_only_its_style_objects_and_accents(
    libs: tuple[ModuleType, ModuleType],
) -> None:
    objlib, _ = libs
    drawn = {}
    for style in STYLES:
        lib = [e for e in _library() if objlib.suits(e, style)]
        alloc = objlib.allocate(HOUSE, lib, style)
        ids = {
            i
            for slots in alloc.values()
            for role, v in slots.items()
            if role != "accent"
            for i in v
        }
        assert ids <= {e["id"] for e in lib}
        assert {slots["accent"] for slots in alloc.values()} <= set(objlib.ACCENTS[style])
        drawn[style] = ids
    assert drawn["scandinavian"] != drawn["contemporary"]
    assert set(objlib.ACCENTS["contemporary"]) == {"green", "warm", None}  # olive, sand, grey
    assert objlib.house_style(None) == objlib.house_style("baroque") == "scandinavian"
    assert objlib.house_style("contemporary") == "contemporary"


def test_a_house_hangs_only_its_style_prints(libs: tuple[ModuleType, ModuleType]) -> None:
    _, artlib = libs
    for style in STYLES:
        artlib.init(HOUSE, style)
        hung = {i for slots in artlib.ALLOC.values() for v in slots.values() for i in v if i}
        assert hung
        assert all(style in artlib.BY[i]["styles"] for i in hung)
    artlib.init(HOUSE, None)
    assert all("scandinavian" in e["styles"] for e in artlib.LIB)
