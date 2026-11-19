"""A listing photo of a scene the kit dressed shows the rooms as the 3D tour does (#122): agencies publish the photos,
so no bed is remade, no rug swapped or added, no table cleared or set, no prop added; only the camera's rules run.
A scene drawn by an older kit keeps the photo's own dressing. A sanitary piece's metal keeps the kit's finish, black
and brass as much as chrome (#130). photo.py and style.py run under Blender: its modules are stand-ins here, the
decisions are not."""

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
BLENDER_DIR = ROOT / "photo" / "blender"
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
    monkeypatch.syspath_prepend(str(BLENDER_DIR))
    before = set(sys.modules)
    module = importlib.import_module("photo")
    module.style.H = module
    monkeypatch.setattr(module, "log", lambda *a: None)
    yield module
    # the pipeline's modules hold the stand-ins: the next test imports them afresh (numpy cannot be imported twice)
    for name in set(sys.modules) - before:
        if str(getattr(sys.modules[name], "__file__", "")).startswith(str(BLENDER_DIR)):
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
    for name in (*RESTYLING, "sanitaryware", "rail_towels", "dress_made", "dress_tables"):
        monkeypatch.setattr(photo, name, record(name))
    photo.dress_storey(SimpleNamespace(), 0.0, decor=True)
    return called


def test_a_dressed_scene_keeps_its_beds_rugs_tables_counters_and_baths(
    photo: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = _calls(photo, monkeypatch, dressed=True)
    assert not set(called) & set(RESTYLING), called
    assert {"own", "sanitaryware", "rail_towels", "dress_made", "dress_tables"} <= set(called)


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


class _Socket:
    def __init__(self, value: Any) -> None:
        self.default_value = value
        self.is_linked = False


def _finish(name: str, colour: str, roughness: float, metallic: float) -> SimpleNamespace:
    """A kit material as Blender imports it: its sRGB colour made linear, on a Principled BSDF."""
    srgb = (int(colour[k : k + 2], 16) / 255 for k in (1, 3, 5))
    rgb = tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb)
    sockets = {
        "Base Color": (*rgb, 1.0),
        "Metallic": metallic,
        "Roughness": roughness,
        "Normal": None,
    }
    bsdf = SimpleNamespace(inputs={k: _Socket(v) for k, v in sockets.items()})
    return SimpleNamespace(name=name, use_nodes=True, node_tree=MagicMock(), bsdf=bsdf)


class _Mesh(dict[str, Any]):
    type = "MESH"

    def __init__(self, name: str, *materials: SimpleNamespace) -> None:
        super().__init__(name=name)
        self.children_recursive: list[Any] = []
        self.material_slots = [SimpleNamespace(material=m) for m in materials]


def _as_drawn(m: SimpleNamespace) -> tuple[Any, Any]:
    return m.bsdf.inputs["Base Color"].default_value, m.bsdf.inputs["Roughness"].default_value


def test_a_sanitary_metal_keeps_the_kits_colour_and_chrome_stays_chrome(
    photo: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    # kit/furnish.js FITTINGS (a black flush plate's buttons, the chrome tap) and kit/lighting-parts.js (brass)
    buttons = _finish("satin black chrome", "#6e6e6e", 0.3, 0.55)
    brass = _finish("brass", "#c9a464", 0.32, 1.0)
    chrome = _finish("Material_135", "#e6e8ea", 0.08, 1.0)
    kit = {m.name: _as_drawn(m) for m in (buttons, brass, chrome)}
    monkeypatch.setattr(photo, "principled", lambda m: m.bsdf)
    nodes: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        photo, "node", lambda nt, kind, **inputs: nodes.append((kind, inputs)) or MagicMock()
    )
    photo.sanitaryware(
        SimpleNamespace(), [_Mesh("wc", buttons), _Mesh("basin", brass), _Mesh("bathtub", chrome)]
    )
    assert _as_drawn(buttons) == kit[buttons.name]
    assert _as_drawn(brass) == kit[brass.name]
    colour, roughness = _as_drawn(chrome)
    assert colour == kit[chrome.name][0]
    assert roughness <= kit[chrome.name][1]
    assert nodes == [("ShaderNodeBevel", {"Radius": 0.003})] * 3
