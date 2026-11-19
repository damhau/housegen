"""A listing photo of a scene the kit dressed shows the rooms as the 3D tour does (#122): agencies publish the photos,
so no bed is remade, no rug swapped or added, no table cleared or set, no prop added; only the camera's rules run.
A scene drawn by an older kit keeps the photo's own dressing. photo.py and style.py run under Blender: its modules are
stand-ins here, the decisions are not."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
BLENDER = ("bpy", "bmesh", "addon_utils", "mathutils", "bpy_extras", "bpy_extras.object_utils")
RESTYLING = (
    "swap_rugs",
    "swap_plants",
    "swap_art",
    "swap_armchairs",
    "swap_beds",
    "sofa_measure",
    "dress_counter",
    "dress_tubs",
    "dress_basins",
)


class Restyled(Exception):
    pass


@pytest.fixture
def photo(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    for name in BLENDER:
        monkeypatch.setitem(sys.modules, name, MagicMock(name=name))
    monkeypatch.syspath_prepend(str(ROOT / "photo" / "blender"))
    before = set(sys.modules)
    module = importlib.import_module("photo")
    module.style.H = module
    monkeypatch.setattr(module, "log", lambda *a: None)
    yield module
    for name in (
        set(sys.modules) - before
    ):  # drawn with the stand-ins: the next import gets the real modules
        del sys.modules[name]


def _calls(photo: ModuleType, monkeypatch: pytest.MonkeyPatch, dressed: bool) -> list[str]:
    called: list[str] = []

    def record(name: str, result: Any = 0) -> Any:
        def call(*a: Any, **k: Any) -> Any:
            called.append(name)
            return result

        return call

    monkeypatch.setattr(photo.catalogue, "DRESSED", dressed)
    monkeypatch.setattr(photo.catalogue, "OWN", True)
    monkeypatch.setattr(photo.catalogue, "holders", lambda scene, sy: ["holder"])
    monkeypatch.setattr(photo.catalogue, "own", record("own"))
    monkeypatch.setattr(photo, "storey_pieces", lambda scene, sy: ["piece"])
    for name in (*RESTYLING, "sanitaryware", "dress_made", "dress_tables"):
        monkeypatch.setattr(photo, name, record(name))
    photo.dress_storey(SimpleNamespace(), 0.0, decor=True)
    return called


def test_a_dressed_scene_keeps_its_beds_rugs_tables_counters_and_baths(
    photo: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = _calls(photo, monkeypatch, dressed=True)
    assert not set(called) & set(RESTYLING), called
    assert {"own", "sanitaryware", "dress_made", "dress_tables"} <= set(called)


def test_a_scene_from_an_older_kit_is_still_dressed_by_the_photo(
    photo: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = _calls(photo, monkeypatch, dressed=False)
    assert set(RESTYLING) <= set(called), called


def test_a_dressed_scene_lays_its_table_with_no_rug_added(
    photo: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(photo.catalogue, "DRESSED", True)
    table = _Piece(footprint=(1.6, 0.9))
    monkeypatch.setattr(photo, "name", lambda o: "dining-table" if o is table else "")
    monkeypatch.setattr(photo, "box", lambda o: ("lo", "hi"))
    monkeypatch.setattr(photo.stage, "rug", MagicMock(side_effect=Restyled("a library rug")))
    monkeypatch.setattr(photo.dress, "rug", MagicMock(side_effect=Restyled("a dining rug")))
    laid: list[Any] = []
    monkeypatch.setattr(photo.style, "table_glass", lambda pieces, lo, hi: laid.append(pieces))
    pieces = [table]
    assert photo.dress_tables(SimpleNamespace(), pieces) == 1
    assert laid == [pieces]


class _Piece(dict[str, Any]):
    matrix_world = SimpleNamespace(translation=SimpleNamespace(x=0.0, y=0.0, z=0.0))


def test_a_dressed_scene_keeps_only_the_cameras_rules(
    photo: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    style = photo.style
    monkeypatch.setattr(style.catalogue, "DRESSED", True)
    edges: list[bool] = []
    monkeypatch.setattr(style, "edge_clutter", lambda scene, cam, plants: edges.append(plants) or 0)
    monkeypatch.setattr(style.H, "storey_pieces", MagicMock(side_effect=Restyled("the rules ran")))
    style.style(SimpleNamespace(), SimpleNamespace(), [], [], 0.0, [])
    assert sorted(edges) == [False, True]


def test_a_scene_from_an_older_kit_is_still_styled(
    photo: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    style = photo.style
    monkeypatch.setattr(style.catalogue, "DRESSED", False)
    monkeypatch.setattr(style.H, "storey_pieces", MagicMock(side_effect=Restyled("the rules ran")))
    with pytest.raises(Restyled):
        style.style(SimpleNamespace(), SimpleNamespace(), [], [], 0.0, [])
