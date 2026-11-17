"""The kit dresses the rooms (#103): every stand-in kit/dressing.js tags `made` is a prop the listing photos make
(photo/blender/made/bathset.py) or draw (a runner), every catalogue piece it draws is one the photos draw at full
resolution, and a scene drawn by an older kit keeps the photo's own rules."""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
DRESSING = (ROOT / "kit" / "dressing.js").read_text(encoding="utf-8")
# the catalogue roles the dressing draws: by catalogue(), pickObject() (the floor's poufs and baskets, a sideboard in a
# living room without one) and model()
DRESSING_ROLES = {
    "cushion",
    "candle",
    "jar",
    "bowl",
    "vase",
    "basket",
    "pouf",
    "pot-plant",
    "floor-plant",
    "sideboard",
}


@pytest.fixture
def catalogue(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(ROOT / "photo" / "blender"))
    module = importlib.import_module("catalogue")
    monkeypatch.setattr(module, "DRESSED", False)
    return module


def _made_keys() -> set[str]:
    body = DRESSING.split("const MADE = {", 1)[1].split("\n};", 1)[0]
    return set(re.findall(r"^  (\w+)(?:\(|:)", body, re.M))


def _bathset_pieces() -> set[str]:
    src = (ROOT / "photo" / "blender" / "made" / "bathset.py").read_text(encoding="utf-8")
    table = src.split("made = {", 1)[1].split("}[WHAT]", 1)[0]
    return set(re.findall(r'"([\w-]+)":', table))


def _js_rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line.strip().rstrip(","))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith('{"id"')
    ]


def test_every_stand_in_of_the_kit_is_a_prop_the_photo_makes(catalogue: ModuleType) -> None:
    made = _made_keys()
    assert {"books", "carafe", "napkin", "runner", "sofathrow", "drape", "rail"} <= made
    pieces = _bathset_pieces()
    for what in made - {"runner"}:
        todo = catalogue.made_piece(what, "1-2-3")
        assert todo is not None, what
        assert todo[1][0] in pieces, what
    assert catalogue.made_piece("runner", "30-170") == ("runner", (0.3, 1.7))


def test_a_stand_in_is_drawn_with_its_own_arguments(catalogue: ModuleType) -> None:
    assert catalogue.made_piece("sofathrow", "15-62-45", -1) == (
        "prop",
        ("sofathrow", "15-62-45", -1.0),
    )
    assert catalogue.made_piece("sofathrow", "15-62-45") == ("prop", ("sofathrow", "15-62-45", 1.0))
    assert catalogue.made_piece("drape", "0.085") == ("prop", ("drape", "0.085", 1.0))
    assert catalogue.made_piece("books", "4-0-2") == ("prop", ("books", "4-0-2", 1.0))
    assert catalogue.made_piece("carafe") == ("prop", ("carafe", None, 1.0))
    assert catalogue.made_piece("napkin") == ("prop", ("napkin-fold", None, 1.0))
    assert catalogue.made_piece("runner", "wide") is None
    assert catalogue.made_piece("lantern") is None


def test_the_books_take_the_kit_covers_in_its_order() -> None:
    kit = re.search(r"const COVERS = \[(.*?)\];", DRESSING)
    assert kit is not None
    kit_rgb = [
        tuple(float(v) for v in t.split(",")) for t in re.findall(r"\[([\d.,\s]+)\]", kit.group(1))
    ]
    src = (ROOT / "photo" / "blender" / "made" / "bathset.py").read_text(encoding="utf-8")
    cloths = src.split("cloths = [", 1)[1].split("]\n", 1)[0]
    photo_rgb = [
        tuple(float(v) for v in t.split(","))
        for t in re.findall(r'"cover-\w+", \(([\d.,\s]+)\)', cloths)
    ]
    assert kit_rgb == photo_rgb


def test_every_piece_the_dressing_draws_is_drawn_at_full_resolution_in_a_dressed_scene(
    catalogue: ModuleType,
) -> None:
    named = set(
        re.findall(r'(?:catalogue\(ctx, |modelled\(ctx, |pickObject\()"([\w-]+)"', DRESSING)
    )
    assert "sideboard" in named
    assert named <= DRESSING_ROLES, named - DRESSING_ROLES
    library = {
        e["id"]: e["role"]
        for e in json.loads((ROOT / "spikes/archviz/made/objects.json").read_text(encoding="utf-8"))
    }
    pieces = [o for o in _js_rows(ROOT / "kit" / "objects.js") if o["role"] in DRESSING_ROLES]
    assert {o["role"] for o in pieces} == DRESSING_ROLES
    catalogue.DRESSED = True
    for o in pieces:
        assert library.get(o["id"]) in catalogue.full(), (o["id"], library.get(o["id"]))


def test_a_scene_from_an_older_kit_draws_only_its_decor_again(catalogue: ModuleType) -> None:
    assert catalogue.full() == catalogue.FULL
    assert "pillow" not in catalogue.full()
    catalogue.DRESSED = True
    assert {"pillow", "candle", "pouf"} <= catalogue.full()
