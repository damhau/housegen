"""A listing photo of a scene the kit dressed keeps the 3D tour's furniture setup (#136): agencies publish the photos,
so no bed, armchair, plant or print is swapped, nothing added to what the kit's dressing already did; its rugs and its
layered bedding are drawn photographically in their colours and places, and the small staging the 3D lacks comes in
(a rug under a bed and under the dining table, the table laid, desk stationery, the bath's caddy and bottles). A scene
drawn by an older kit keeps the photo's own dressing. A sanitary piece's metal keeps the kit's finish, black and brass
as much as chrome (#130); its towels and throws keep the kit's colours, and a camera spec frames as the 3D's
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
# the photo's own furniture and dressing choices, which a scene the kit dressed has made already
SWAPPING = ("swap_rugs", "swap_plants", "swap_art", "swap_armchairs", "sofa_measure")
# the soft things and the small staging, in every scene
DRESSING = ("swap_beds", "dress_tables", "dress_counter", "dress_tubs", "dress_basins")
# style.py's rules the kit's dressing (kit/dressing.js) plays already, and the nightstands, sofas, sideboards, floor
# corners, bath stools and towel bars it decides on
KIT_PLAYS = (
    "bedsides",
    "table_extras",
    "coffee_table",
    "sofa_dress",
    "stool",
    "sideboard",
    "accent",
    "towel_rail",
)
KIT_PLAYS_LIB = ("nightstand", "dining", "sofa", "sofa_side", "bed_foot", "floor", "wall")


class Restyled(Exception):
    pass


@pytest.fixture
def photo(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    for name in BLENDER:
        monkeypatch.setitem(sys.modules, name, MagicMock(name=name))
    monkeypatch.syspath_prepend(str(BLENDER_DIR))
    before = set(sys.modules)
    module = importlib.import_module("photo")
    module.style.H = module.stage.H = module
    monkeypatch.setattr(module, "log", lambda *a: None)
    monkeypatch.setattr(module.style, "log", lambda *a: None)
    yield module
    # the pipeline's modules hold the stand-ins: the next test imports them afresh (numpy cannot be imported twice)
    for name in set(sys.modules) - before:
        if str(getattr(sys.modules[name], "__file__", "")).startswith(str(BLENDER_DIR)):
            del sys.modules[name]


def _record(called: list[str], name: str, result: Any = 0) -> Any:
    def call(*a: Any, **k: Any) -> Any:
        called.append(name)
        return result

    return call


def _calls(photo: ModuleType, monkeypatch: pytest.MonkeyPatch, dressed: bool) -> list[str]:
    called: list[str] = []
    monkeypatch.setattr(photo.catalogue, "DRESSED", dressed)
    monkeypatch.setattr(photo.catalogue, "OWN", True)
    monkeypatch.setattr(photo.catalogue, "holders", lambda scene, sy: ["holder"])
    monkeypatch.setattr(photo.catalogue, "own", _record(called, "own"))
    monkeypatch.setattr(photo, "storey_pieces", lambda scene, sy: ["piece"])
    for name in (*SWAPPING, *DRESSING, "kit_rugs", "sanitaryware", "rail_towels", "dress_made"):
        monkeypatch.setattr(photo, name, _record(called, name))
    photo.dress_storey(SimpleNamespace(), 0.0, decor=True)
    return called


def test_a_dressed_scene_keeps_the_3d_furniture_and_dresses_its_soft_things(
    photo: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = _calls(photo, monkeypatch, dressed=True)
    assert not set(called) & set(SWAPPING), called
    assert {*DRESSING, "kit_rugs", "own", "sanitaryware", "rail_towels", "dress_made"} <= set(
        called
    )


def test_a_scene_from_an_older_kit_is_still_dressed_by_the_photo(
    photo: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = _calls(photo, monkeypatch, dressed=False)
    assert {*SWAPPING, *DRESSING} <= set(called), called
    assert "kit_rugs" not in called


class _V:
    def __init__(self, v: Any) -> None:
        self.x, self.y, self.z = (float(c) for c in v)

    def __add__(self, o: _V) -> _V:
        return _V((self.x + o.x, self.y + o.y, self.z + o.z))

    def __sub__(self, o: _V) -> _V:
        return _V((self.x - o.x, self.y - o.y, self.z - o.z))

    def __mul__(self, k: float) -> _V:
        return _V((self.x * k, self.y * k, self.z * k))

    def __truediv__(self, k: float) -> _V:
        return self * (1 / k)

    def __getitem__(self, i: int) -> float:
        return (self.x, self.y, self.z)[i]

    @property
    def length(self) -> float:
        return math.sqrt(self.x**2 + self.y**2 + self.z**2)


class _Shift:
    """A frame turned by nothing: a translation, as a bed's or a table's frame along the axes."""

    def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> None:
        self.translation = _V((x, y, z))

    def __matmul__(self, o: Any) -> Any:
        if isinstance(o, _Shift):
            return _Shift(*(self.translation + o.translation))
        return self.translation + o

    def inverted(self) -> _Shift:
        return _Shift(*(self.translation * -1))

    def to_euler(self) -> SimpleNamespace:
        return SimpleNamespace(z=0.0)


class _Matrix:
    @staticmethod
    def Translation(v: Any) -> _Shift:
        return _Shift(*v)

    @staticmethod
    def Rotation(*a: Any) -> _Shift:
        return _Shift()


@pytest.mark.parametrize(
    ("dims", "depth", "role"),
    [
        ((0.78, 0.48, 0.17), 0.32, "pillow"),  # layers() on a 0.9 m bed 8 cm from the head wall
        ((0.46, 0.13, 0.42), 0.39, "upright"),
        ((0.40, 0.12, 0.34), 0.51, "cushion"),
        ((0.72, 0.5, 0.13), 0.4, "pillow"),  # dressBed's
        ((1.1, 0.48, 0.17), 1.66, None),  # layers()'s throw, past the middle
        ((1.06, 1.14, 0.08), 1.6, None),  # the duvet
    ],
)
def test_a_kit_beds_pillows_and_cushions_are_read_by_their_size_at_its_head(
    photo: ModuleType, dims: tuple[float, float, float], depth: float, role: str | None
) -> None:
    assert photo.soft_role(dims, depth) == role


def _span(lo: tuple[float, float, float], hi: tuple[float, float, float]) -> tuple[_V, _V]:
    return _V(lo), _V(hi)


def test_a_layered_beds_mattress_is_found_under_its_duvet(photo: ModuleType) -> None:
    # layers() over a 0.9 x 2.0 mattress 0.1 m from the head wall, its top at 0.5: the duvet 16 cm wider, from 4 % of
    # the length before the middle to 6 cm past the foot, 4 cm over the top; the sheet under it
    sheet, duvet = "sheet", "duvet"
    parts = {"mattress": [], "drapes": [sheet, duvet], "blocks": []}
    spans = {
        sheet: _span((-0.52, -2.15, 0.45), (0.52, -0.86, 0.6)),
        duvet: _span((-0.53, -2.16, 0.3), (0.53, -1.02, 0.6)),
    }
    w, head, foot, top, cx = photo.mattress_of(parts, spans, {sheet: 0.525, duvet: 0.54})
    assert (w, head, foot, top, cx) == pytest.approx((0.9, 0.1, 2.1, 0.5, 0.0))


def test_a_single_layer_beds_mattress_is_found_under_its_duvet(photo: ModuleType) -> None:
    # dressBed over a 1.4 x 2.0 mattress 0.1 m from the head wall: the duvet 8 cm wider, 68 % of the length + 4 cm,
    # 5 cm past the foot, from 2 cm under the top
    duvet, flap = "duvet", "flap"
    parts = {"mattress": [], "drapes": [], "blocks": [duvet, flap]}
    spans = {
        duvet: _span((-0.74, -2.15, 0.48), (0.74, -0.75, 0.54)),
        flap: _span((0.7, -2.15, 0.3), (0.73, -0.75, 0.52)),
    }
    w, head, foot, top, _ = photo.mattress_of(parts, spans, {})
    assert (w, head, foot, top) == pytest.approx((1.4, 0.1, 2.1, 0.5))


def test_a_kit_mattress_is_the_mattress(photo: ModuleType) -> None:
    m = "mattress"
    spans = {m: _span((-0.44, -2.04, 0.3), (0.44, -0.08, 0.5))}
    parts = {"mattress": [m], "drapes": ["duvet"], "blocks": []}
    assert photo.mattress_of(parts, spans, {}) == pytest.approx((0.88, 0.08, 2.04, 0.5, 0.0))


def test_a_dressed_scene_makes_up_every_bed_and_keeps_its_frame(
    photo: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(photo.catalogue, "DRESSED", True)
    monkeypatch.setattr(photo, "BEDS", [])
    bed = _Piece(footprint=[0.96, 2.08])
    monkeypatch.setattr(photo, "name", lambda o: "bed")
    monkeypatch.setattr(photo, "box", lambda o: (_V((0, 0, 0)), _V((1, 2, 1))))
    for helper in ("linen_material", "wool_material"):
        monkeypatch.setattr(photo.dress, helper, MagicMock())
    monkeypatch.setattr(photo, "upholstery_material", MagicMock())
    monkeypatch.setattr(photo, "frame_of", lambda o, z: _Shift())
    monkeypatch.setattr(photo, "Matrix", _Matrix)
    made: list[Any] = []
    monkeypatch.setattr(
        photo, "make_up", lambda scene, o, frame, length, decor: made.append(o) or 0.88
    )
    monkeypatch.setattr(
        photo, "bedset", MagicMock(side_effect=Restyled("a bedset in the kit's frame's place"))
    )
    assert photo.swap_beds(SimpleNamespace(), [bed]) == 1
    assert made == [bed]
    assert [w for _, w in photo.BEDS] == [0.88]


class _Piece(dict[str, Any]):
    def __init__(self, at: tuple[float, float, float] = (0.0, 0.0, 0.0), **tags: Any) -> None:
        super().__init__(**tags)
        self.matrix_world = SimpleNamespace(translation=_V(at))


def _table(
    photo: ModuleType, monkeypatch: pytest.MonkeyPatch, pieces: list[Any], names: dict[int, str]
) -> list[str]:
    done: list[str] = []
    monkeypatch.setattr(photo.catalogue, "DRESSED", True)
    monkeypatch.setattr(photo.objlib, "LIB", False)
    monkeypatch.setattr(photo, "name", lambda o: names.get(id(o), ""))
    monkeypatch.setattr(photo, "box", lambda o: (o["lo"], o["hi"]))
    monkeypatch.setattr(photo, "turn", lambda o: 0.0)
    rug = SimpleNamespace(rotation_euler=[0.0, 0.0, 0.0])
    monkeypatch.setattr(photo.dress, "rug", lambda *a, **k: done.append("rug") or rug)
    monkeypatch.setattr(photo, "lay_places", _record(done, "places"))
    monkeypatch.setattr(photo.style, "table_glass", _record(done, "glass"))
    monkeypatch.setattr(
        photo.objlib, "choose", MagicMock(side_effect=Restyled("the photo's own table setting"))
    )
    assert photo.dress_tables(SimpleNamespace(), pieces) == 1
    return done


def test_a_dressed_scenes_dining_table_gets_a_rug_and_is_laid(
    photo: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = _Piece(footprint=(1.6, 0.9), lo=_V((-0.8, -0.45, 0)), hi=_V((0.8, 0.45, 0.75)))
    assert _table(photo, monkeypatch, [table], {id(table): "dining-table"}) == [
        "rug",
        "places",
        "glass",
    ]


def test_a_dining_table_on_the_kits_rug_gets_no_other(
    photo: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = _Piece(footprint=(1.6, 0.9), lo=_V((-0.8, -0.45, 0)), hi=_V((0.8, 0.45, 0.75)))
    rug = _Piece(lo=_V((-1.5, -1.2, 0)), hi=_V((1.5, 1.2, 0.012)))
    done = _table(photo, monkeypatch, [table, rug], {id(table): "dining-table", id(rug): "rug"})
    assert done == ["places", "glass"]


def _lay(
    photo: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    chairs: list[tuple[float, float]],
    plates: bool = False,
) -> int:
    table = _Piece(footprint=(2.0, 1.0))
    lo, hi = _V((-1.0, -0.5, 0)), _V((1.0, 0.5, 0.75))
    pieces: list[Any] = [table, *(_Piece(at=(u, v, 0)) for u, v in chairs)]
    names = {id(p): "dining-chair" for p in pieces[1:]}
    if plates:
        pieces.append(_Piece(at=(0.3, 0.2, 0.76)))
        names[id(pieces[-1])] = "plate"
    monkeypatch.setattr(photo, "name", lambda o: names.get(id(o), "dining-table"))
    monkeypatch.setattr(photo, "frame_of", lambda o, z: _Shift())
    monkeypatch.setattr(photo, "Matrix", _Matrix)
    monkeypatch.setattr(photo, "Vector", _V)
    monkeypatch.setattr(
        photo, "heights_down", lambda scene, dg, f, pts, start: [(0.75, 1.0)] * len(pts)
    )
    monkeypatch.setattr(photo, "bath_piece", lambda what: what)
    laid: list[Any] = []
    monkeypatch.setattr(
        photo, "in_frame", lambda scene, f, path, at: laid.append((path, f.translation))
    )
    n = photo.lay_places(SimpleNamespace(), table, pieces, lo, hi)
    assert n == len(laid)
    assert all(path == "place" for path, _ in laid)
    return n


def test_each_chair_at_the_table_gets_a_place(
    photo: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    six = [(-0.45, -0.8), (0.45, -0.8), (-0.45, 0.8), (0.45, 0.8), (-1.35, 0.0), (1.35, 0.0)]
    assert _lay(photo, monkeypatch, six) == 6


def test_places_keep_their_distance_and_a_chair_pushed_away_gets_none(
    photo: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _lay(photo, monkeypatch, [(0.0, -0.8), (0.15, -0.8), (0.4, 1.9)]) == 1


def test_a_table_with_the_kits_plates_is_not_laid_again(
    photo: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _lay(photo, monkeypatch, [(0.0, -0.8)], plates=True) == 0


def _style(photo: ModuleType, monkeypatch: pytest.MonkeyPatch, lib: bool) -> list[str]:
    style, stage = photo.style, photo.stage
    called: list[str] = []
    monkeypatch.setattr(style.catalogue, "DRESSED", True)
    monkeypatch.setattr(style.objlib, "LIB", lib)
    monkeypatch.setattr(photo, "storey_pieces", lambda scene, sy: [])
    monkeypatch.setattr(photo, "camera_rays", lambda *a: [])
    rooms = [
        {"name": "Ch.1", "use": "bedroom"},
        {"name": "Salon", "use": "living"},
        {"name": "Hall", "use": "hall"},
    ]
    monkeypatch.setattr(
        style, "seen_rooms", lambda rays, rs, sy: [(rooms[0], 90), (rooms[1], 40), (rooms[2], 7)]
    )
    monkeypatch.setattr(
        style, "edge_clutter", lambda scene, cam, plants: called.append(f"edge {plants}") or 0
    )
    for name in ("bed_rug", "bed_art", "sills", "outdoor_set"):
        monkeypatch.setattr(style, name, _record(called, name))
    monkeypatch.setattr(
        stage, "coffee", lambda *a, keep=False: called.append(f"coffee keep={keep}") or 0
    )
    monkeypatch.setattr(stage, "desk", _record(called, "desk"))
    for name in KIT_PLAYS:
        monkeypatch.setattr(style, name, MagicMock(side_effect=Restyled(name)))
    for name in KIT_PLAYS_LIB:
        monkeypatch.setattr(stage, name, MagicMock(side_effect=Restyled(f"stage.{name}")))
    style.style(SimpleNamespace(), SimpleNamespace(), [], rooms, 0.0, [])
    return called


def test_a_dressed_scene_gets_the_small_staging_the_3d_lacks(
    photo: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = _style(photo, monkeypatch, lib=True)
    assert called == [
        "edge True",
        *("bed_rug", "bed_art", "coffee keep=True", "desk"),  # the bedroom
        *("coffee keep=True", "desk"),  # the living room; the hall is barely seen
        *("sills", "outdoor_set", "edge False"),
    ]


def test_a_desks_stationery_is_put_back_in_its_cup(
    photo: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Poly Haven's stationery_supplies is a product shot, its pens in a row in the air beside their cup (#136)."""
    relaid: list[Any] = []
    monkeypatch.setitem(photo.stage.RELAID, "stationery_supplies", relaid.append)
    monkeypatch.setattr(photo.stage, "log", lambda *a: None)
    monkeypatch.setattr(photo.objlib, "path", lambda e: e["id"])
    monkeypatch.setattr(
        photo, "place", lambda scene, path, *a, **k: ({"file": path}, _V((0.3, 0.08, 0.16)))
    )
    for name in ("stationery_supplies", "office_notepads"):
        photo.stage.put(
            SimpleNamespace(), {"name": "Ch.3"}, {"id": name, "role": "desk"}, (0, 0, 0)
        )
    assert [r["file"] for r in relaid] == ["stationery_supplies"]
    assert relaid[0]["obj_id"] == "stationery_supplies"


def test_a_dressed_scene_without_the_library_clears_no_coffee_table(
    photo: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = _style(photo, monkeypatch, lib=False)
    assert "coffee keep=True" not in called
    assert "desk" not in called
    assert {"bed_rug", "bed_art", "sills", "outdoor_set"} <= set(called)


def test_a_scene_from_an_older_kit_is_still_styled(
    photo: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    style = photo.style
    monkeypatch.setattr(style.catalogue, "DRESSED", False)
    monkeypatch.setattr(style.H, "storey_pieces", MagicMock(side_effect=Restyled("the rules ran")))
    with pytest.raises(Restyled):
        style.style(SimpleNamespace(), SimpleNamespace(), [], [], 0.0, [])


class _Empty:
    type = "EMPTY"
    hide_render = False


class _Shown:
    """A mesh shown in the scene, its bounding box from `lo` to `hi`, its matrix the identity."""

    type = "MESH"
    hide_render = False

    def __init__(self, lo: tuple[float, float, float], hi: tuple[float, float, float]) -> None:
        self.bound_box = [
            (x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])
        ]
        self.matrix_world = _Shift()


def test_a_coffee_table_with_things_on_it_is_dressed_already(
    photo: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    style = photo.style
    table = _Empty()
    monkeypatch.setattr(style, "Vector", _V)
    monkeypatch.setattr(photo, "box", lambda o: (_V((-0.6, -0.3, 0.0)), _V((0.6, 0.3, 0.42))))
    monkeypatch.setattr(photo, "family", lambda o: [o])
    rug = _Shown((-1.2, -0.9, 0.0), (1.2, 0.9, 0.012))
    vase = _Shown((0.2, -0.1, 0.42), (0.32, 0.02, 0.7))
    assert not style.stands_on(SimpleNamespace(objects=[table, rug]), table)
    assert style.stands_on(SimpleNamespace(objects=[table, rug, vase]), table)


def test_a_beds_rug_goes_as_far_as_the_room_lets_it(photo: ModuleType) -> None:
    def free(x: float, y: float) -> bool:
        return True

    def against_the_right_wall(x: float, y: float) -> bool:
        return x <= 0.45 + 0.12 and y >= -2.5

    assert photo.style.rug_fit(free, 0.9, 2.0, 1.3) == (0.6, 0.6, 0.7)
    assert photo.style.rug_fit(against_the_right_wall, 0.9, 2.0, 1.3) == (0.6, 0.1, 0.5)
    assert photo.style.rug_fit(lambda x, y: abs(x) < 0.5, 0.9, 2.0, 1.3) is None


def _bed_rug(photo: ModuleType, monkeypatch: pytest.MonkeyPatch, kit_rugs: list[Any]) -> list[Any]:
    style = photo.style
    drawn: list[Any] = []
    monkeypatch.setattr(style.catalogue, "DRESSED", True)
    monkeypatch.setattr(style, "Vector", _V)
    monkeypatch.setattr(photo, "BEDS", [(_Shift(), 0.9)])
    monkeypatch.setattr(photo, "KIT_RUGS", kit_rugs)
    monkeypatch.setattr(photo, "inside", lambda poly, x, z: True)
    monkeypatch.setattr(photo, "hide", lambda o: setattr(o, "hide_render", True))
    monkeypatch.setattr(style, "our_rugs", MagicMock(side_effect=Restyled("the photo's rugs")))
    monkeypatch.setattr(style.stage, "rug", lambda *a: drawn.append(("library", None)))
    monkeypatch.setattr(
        photo.dress, "rug", lambda *a, rgb=None, **k: drawn.append(("rug", rgb)) or MagicMock()
    )
    style.bed_rug(SimpleNamespace(), {"polygon": []}, 0.0)
    return drawn


def _kit_rug(
    at: tuple[float, float], size: tuple[float, float], rgb: tuple[float, float, float]
) -> Any:
    return (None, _Shift(*at), size, SimpleNamespace(hide_render=False), rgb)


def test_a_bed_on_the_kits_rug_keeps_it(photo: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    under = _kit_rug((0.0, -1.9), (2.0, 1.4), (0.16, 0.16, 0.14))
    assert _bed_rug(photo, monkeypatch, [under]) == []
    assert not under[3].hide_render


def test_a_beds_rug_takes_the_place_and_colour_of_the_kits_runner(
    photo: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _kit_rug((0.85, -1.2), (0.7, 1.6), (0.4, 0.3, 0.2))
    elsewhere = _kit_rug((4.0, 3.0), (1.6, 2.3), (0.16, 0.16, 0.14))
    monkeypatch.setattr(photo.objlib, "LIB", True)
    assert _bed_rug(photo, monkeypatch, [runner, elsewhere]) == [("rug", (0.4, 0.3, 0.2))]
    assert runner[3].hide_render
    assert not elsewhere[3].hide_render


def test_a_dressed_scenes_bare_bed_gets_the_librarys_rug(
    photo: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(photo.objlib, "LIB", True)
    assert _bed_rug(photo, monkeypatch, []) == [("library", None)]


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
