"""The listing photos show the scene's own decor (#102): every decor piece the kit draws from its catalogue is a piece of
the photo library, which draws it at full resolution (photo/blender/catalogue.py); the kit's prints are the library's,
framed the same way; and in a scene that draws its own decor the photo's rules get none of those roles."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
# the kit's roles the listing photos used to choose (kit/decor.js, styles.js "models")
KIT_DECOR = {
    "vase",
    "stems",
    "pot-plant",
    "floor-plant",
    "foliage",
    "bench",
    "mirror",
    "tall-mirror",
    "table-lamp",
    "desk-lamp",
    "floor-lamp",
    "pendant",
    "rug",
    "runner",
}


@pytest.fixture
def photo(monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, ModuleType, ModuleType]:
    monkeypatch.syspath_prepend(str(ROOT / "photo" / "blender"))
    return (
        importlib.import_module("objlib"),
        importlib.import_module("artlib"),
        importlib.import_module("catalogue"),
    )


def _js_rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line.strip().rstrip(","))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith('{"id"')
    ]


def test_every_decor_piece_the_kit_draws_is_one_the_photo_draws_at_full_resolution(
    photo: tuple[ModuleType, ModuleType, ModuleType],
) -> None:
    _, _, catalogue = photo
    library = {
        e["id"]: e
        for e in json.loads((ROOT / "spikes/archviz/made/objects.json").read_text(encoding="utf-8"))
    }
    decor = [o for o in _js_rows(ROOT / "kit" / "objects.js") if o["role"] in KIT_DECOR]
    assert {o["role"] for o in decor} == KIT_DECOR
    for o in decor:
        assert o["id"] in library, o["id"]
        assert library[o["id"]]["role"] in catalogue.FULL, (o["id"], library[o["id"]]["role"])
    lamps = [o for o in decor if o["role"].endswith("lamp") or o["role"] == "pendant"]
    assert all(len(o["light"]) == 3 for o in lamps)
    assert all(o.get("cord") for o in lamps if o["role"] == "pendant")


def test_the_kit_prints_are_the_library_prints_framed_the_same(
    photo: tuple[ModuleType, ModuleType, ModuleType],
) -> None:
    _, artlib, _ = photo
    kit = {e["id"]: e for e in _js_rows(ROOT / "kit" / "art.js")}
    library = {e["id"]: e for style in ("scandinavian", "contemporary") for e in artlib.load(style)}
    assert set(kit) == set(library)
    for i, e in kit.items():
        lib = library[i]
        assert (e["frame"], e["mount"], e["series"], sorted(e["styles"])) == (
            lib["frame"],
            lib["mount"],
            lib["series"],
            sorted(lib["styles"]),
        ), i
        assert (ROOT / "kit" / "assets" / "art" / f"{i}.webp").exists(), i


def test_a_scene_with_its_own_decor_gives_the_rules_none_of_its_roles(
    photo: tuple[ModuleType, ModuleType, ModuleType],
) -> None:
    objlib, _, catalogue = photo
    house = [
        {"name": "Séjour", "use": "living", "y": 0.0},
        {"name": "Chambre", "use": "bedroom", "y": 0.0},
    ]
    # the whole library, as on the photo service's volume (here only the pieces made in the repo are files)
    lib = [
        e
        for e in json.loads(Path(objlib.CATALOGUE).read_text(encoding="utf-8"))
        if objlib.suits(e, "scandinavian")
    ]
    objlib.LIB, objlib.BY = lib, {e["id"]: e for e in lib}
    objlib.ALLOC = objlib.allocate(house, lib, "scandinavian")
    try:
        assert objlib.pick(house[0], "vase") is not None
        objlib.BANNED = catalogue.KIT_ROLES
        for role in catalogue.KIT_ROLES:
            assert objlib.pick(house[1], role) is None, role
            assert objlib.picks(house[1], role) == [], role
        assert objlib.pick(house[1], "candle") is not None, (
            "the props a room is dressed with stay the photo's"
        )
    finally:
        objlib.BANNED = frozenset()
        objlib.USED.clear()
        objlib.LIB, objlib.BY, objlib.ALLOC = [], {}, {}
