"""A listing photo of a scene the kit dressed shows the rooms as the 3D tour does (#122): agencies publish the photos,
so no bed is remade, no rug swapped or added, no table cleared or set, no prop added; only the camera's rules run.
A scene drawn by an older kit keeps the photo's own dressing. A sanitary piece's metal keeps the kit's finish, black
and brass as much as chrome (#130); its towels and throws keep the kit's colours, and a camera spec frames as the 3D's
photographer frame does (#133). photo.py and style.py run under Blender: its modules are stand-ins here, the
decisions are not."""

from __future__ import annotations

import importlib
import math
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


def _made(name: str, rgb: tuple[float, float, float]) -> SimpleNamespace:
    """A made/bathset.py material as the glTF import gives it: its linear colour on a Principled BSDF."""
    return SimpleNamespace(
        name=name, bsdf=SimpleNamespace(inputs={"Base Color": _Socket((*rgb, 1.0))})
    )


def _kit(rgb: tuple[float, float, float], textured: bool = False) -> SimpleNamespace:
    """A kit stand-in's material as the glTF import gives it: a colour, or a scan times its colour factor."""
    socket = _Socket((*rgb, 1.0))
    if textured:
        scan = SimpleNamespace(type="RGBA", is_linked=True)
        factor = SimpleNamespace(type="RGBA", is_linked=False, default_value=(*rgb, 1.0))
        socket.is_linked = True
        socket.links = [  # type: ignore[attr-defined]
            SimpleNamespace(from_node=SimpleNamespace(blend_type="MULTIPLY", inputs=[scan, factor]))
        ]
    bsdf = SimpleNamespace(bl_idname="ShaderNodeBsdfPrincipled", inputs={"Base Color": socket})
    return SimpleNamespace(name="Material", node_tree=SimpleNamespace(nodes=[bsdf]))


def _piece(*meshes: _Mesh) -> SimpleNamespace:
    return SimpleNamespace(type="EMPTY", children_recursive=list(meshes))


def _drawn(piece: SimpleNamespace) -> list[Any]:
    return [s.material for m in piece.children_recursive for s in m.material_slots]


@pytest.fixture
def cloths(photo: ModuleType, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """bath_materials with its Cycles materials recorded as (recipe, colour)."""
    monkeypatch.setattr(photo, "BATH_MATS", {})
    monkeypatch.setattr(photo, "principled", lambda m: m.bsdf)
    monkeypatch.setattr(photo, "terry", lambda rgb: ("terry", tuple(rgb)))
    monkeypatch.setattr(photo.dress, "wool_material", lambda tint: ("wool", tuple(tint)))
    monkeypatch.setattr(photo, "oak_wood", lambda: ("oak", None))
    monkeypatch.setattr(photo, "clear_glass", lambda *a: ("glass", None))
    monkeypatch.setattr(photo, "cloth_weave", lambda rgb, *a: ("weave", tuple(rgb)))
    return photo


# kit/dressing.js MADE, linear as Blender imports them: TOWEL, SAND, the drape's #ece8e0, the throw's #c4bfb7 wool
KIT_TOWEL, KIT_SAND = (0.8469, 0.8388, 0.807), (0.6172, 0.552, 0.4621)
KIT_DRAPE, KIT_WOOL, KIT_OAK = (0.8388, 0.807, 0.7454), (0.552, 0.521, 0.4735), (0.5, 0.36, 0.22)
# made/bathset.py
OWN_WHITE, OWN_SAND, OWN_WOOL = (0.85, 0.84, 0.81), (0.62, 0.55, 0.46), (0.55, 0.52, 0.47)


def test_a_dressed_scenes_towels_and_throw_keep_the_kits_colours(cloths: ModuleType) -> None:
    stool = _Mesh("stooltowels", _kit(KIT_OAK, textured=True), _kit(KIT_TOWEL), _kit(KIT_SAND))
    drape = _Mesh("drape", _kit(KIT_DRAPE))
    throw = _Mesh("sofathrow", _kit(KIT_WOOL, textured=True))
    cases = [
        (
            stool,
            [
                _made("oak", (0.4, 0.3, 0.2)),
                _made("towel-white", OWN_WHITE),
                _made("towel-sand.001", OWN_SAND),
            ],
        ),
        (drape, [_made("towel-white", OWN_WHITE)]),
        (throw, [_made("throw-wool", OWN_WOOL)]),
    ]
    drawn = []
    for kit, made in cases:
        piece = _piece(_Mesh("made", *made))
        colours = cloths.stand_in_colours(SimpleNamespace(children_recursive=[kit]))
        cloths.bath_materials(piece, colours)
        drawn.append(_drawn(piece))
    oak, white, sand = drawn[0]
    assert oak == ("oak", None)
    assert white == ("terry", pytest.approx(KIT_TOWEL))
    assert sand == ("terry", pytest.approx(KIT_SAND))
    assert drawn[1] == [("terry", pytest.approx(KIT_DRAPE))]
    # the 3D's throw is the wool scan times its colour, and so is the photo's
    assert drawn[2] == [("wool", pytest.approx(KIT_WOOL))]


def test_a_made_cloth_without_a_kit_colour_keeps_its_own(cloths: ModuleType) -> None:
    piece = _piece(_Mesh("made", _made("towel-sand", OWN_SAND)))
    cloths.bath_materials(piece, [])
    assert _drawn(piece) == [("terry", pytest.approx(OWN_SAND))]


def test_a_scene_from_an_older_kit_keeps_the_photos_towels_and_camel_throw(
    cloths: ModuleType,
) -> None:
    piece = _piece(_Mesh("made", _made("towel-sand", OWN_SAND), _made("throw-wool", OWN_WOOL)))
    cloths.bath_materials(piece)
    assert _drawn(piece) == [("terry", (0.52, 0.45, 0.36)), ("wool", (1.15, 0.86, 0.62))]


@pytest.mark.parametrize("dressed", [True, False])
def test_only_a_dressed_scenes_made_props_take_the_stand_ins_colours(
    photo: ModuleType, monkeypatch: pytest.MonkeyPatch, dressed: bool
) -> None:
    monkeypatch.setattr(photo.catalogue, "DRESSED", dressed)
    monkeypatch.setattr(photo.style, "prop", lambda what, arg: what)
    monkeypatch.setattr(photo, "adopt", lambda o, h: None)
    given: list[Any] = []

    def in_frame(scene: Any, f: Any, path: str, at: Any, scale: Any, kit: Any) -> dict[str, Any]:
        given.append(None if kit is None else [tuple(float(v) for v in c) for c in kit])
        return {}

    monkeypatch.setattr(photo, "in_frame", in_frame)
    holder = _Holder(made="drape", arg="0.085")
    holder.children_recursive = [_Mesh("drape", _kit(KIT_DRAPE))]
    assert photo.dress_made(SimpleNamespace(), [holder]) == 1
    assert given == ([[pytest.approx(KIT_DRAPE)]] if dressed else [None])


class _Holder(dict[str, Any]):
    def __init__(self, **tags: Any) -> None:
        super().__init__(**tags)
        self.matrix_world = MagicMock()
        self.children: list[Any] = []
        self.children_recursive: list[Any] = []


class _V:
    def __init__(self, v: Any) -> None:
        self.x, self.y, self.z = (float(c) for c in v)

    def __add__(self, o: _V) -> _V:
        return _V((self.x + o.x, self.y + o.y, self.z + o.z))

    def __sub__(self, o: _V) -> _V:
        return _V((self.x - o.x, self.y - o.y, self.z - o.z))

    def __mul__(self, k: float) -> _V:
        return _V((self.x * k, self.y * k, self.z * k))


class _Camera(dict[str, Any]):
    shift_y = 0.0
    angle_y = 0.0


class _Object:
    def __init__(self, name: str, data: _Camera) -> None:
        self.data, self.location, self.rotation_euler = data, _V((0, 0, 0)), (0.0, 0.0, 0.0)

    @property
    def matrix_world(self) -> SimpleNamespace:
        return SimpleNamespace(translation=self.location)


def _shoot(photo: ModuleType, monkeypatch: pytest.MonkeyPatch, spec: str, legacy: bool) -> Any:
    still = photo.still
    monkeypatch.setattr(still, "Vector", _V)
    monkeypatch.setattr(photo, "Vector", _V)
    cameras = SimpleNamespace(new=lambda name: _Camera())
    data = SimpleNamespace(cameras=cameras, objects=SimpleNamespace(new=_Object))
    monkeypatch.setattr(still, "bpy", SimpleNamespace(data=data))
    collection = SimpleNamespace(objects=SimpleNamespace(link=lambda o: None))
    scene = SimpleNamespace(collection=collection, camera=None)
    still.camera(scene, spec, 1920, 1080, legacy)
    return scene.camera


# Montelly49 v25's salon: the target 0.30 m below the eye, 3.74 m ahead
SALON = "-0.15,3.65,0.25>3.04,3.35,2.21@58"
SALON_RISE = -0.30 / math.hypot(3.19, 1.96)


def test_a_specs_target_sits_at_the_frames_centre_as_in_the_3d(
    photo: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    # under a vertical sensor fit Blender reads shift_y in frame heights; kit/runtime.js photoFrame centres the target
    cam = _shoot(photo, monkeypatch, SALON, legacy=False).data
    assert cam.shift_y * 2 * math.tan(cam.angle_y / 2) == pytest.approx(SALON_RISE, abs=1e-9)


def test_a_set_shot_before_133_keeps_its_framing(
    photo: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    cam = _shoot(photo, monkeypatch, SALON, legacy=True).data
    before = SALON_RISE / (2 * math.tan(math.radians(29))) * 1080 / 1920
    assert cam.shift_y == pytest.approx(before, abs=1e-9)


@pytest.mark.parametrize("legacy", [True, False])
def test_a_final_photos_spec_shoots_its_camera_again(
    photo: ModuleType, monkeypatch: pytest.MonkeyPatch, legacy: bool
) -> None:
    spec = "4.266,6.600,-2.901>2.852,6.400,-1.333@64"  # Montelly49 v25's bath, as its set saved it
    cam = _shoot(photo, monkeypatch, spec, legacy)
    assert photo.camera_spec(None, cam, math.hypot(2.852 - 4.266, -1.333 + 2.901)) == spec
