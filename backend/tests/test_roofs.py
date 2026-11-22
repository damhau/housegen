"""The neighbours' roofs (#143): the choice rules, the registers on the houses, the solar modules."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
from PIL import Image
from shapely.geometry import Polygon

from housegen.geo import context, roofs

Y = 500.0


def _gable(
    cx: float, cz: float, length: float, width: float, pitch: float = 35.0
) -> dict[str, Any]:
    """A gable house, its ridge along x (east-west): a north face and a south face."""
    x0, x1, z0, z1 = cx - length / 2, cx + length / 2, cz - width / 2, cz + width / 2
    ridge = Y + math.tan(math.radians(pitch)) * width / 2
    pts = [
        (x0, Y, z0),
        (x1, Y, z0),
        (x1, ridge, cz),
        (x0, ridge, cz),
        (x0, Y, z1),
        (x1, Y, z1),
    ]
    return {
        "id": f"gable-{cx}-{cz}",
        "positions": [v for p in pts for v in p],
        "roof": [0, 1, 2, 0, 2, 3, 3, 2, 5, 3, 5, 4],
        "wall": [],
    }


def _flat(
    cx: float, cz: float, length: float, width: float, turn: float = 0.0, source: str = "x"
) -> dict[str, Any]:
    c, s = math.cos(math.radians(turn)), math.sin(math.radians(turn))
    corners = [(-length / 2, -width / 2), (length / 2, -width / 2), (length / 2, width / 2)]
    corners.append((-length / 2, width / 2))
    pts = [(cx + u * c - v * s, Y, cz + u * s + v * c) for u, v in corners]
    b = {
        "id": f"flat-{cx}-{cz}",
        "positions": [v for p in pts for v in p],
        "roof": [0, 2, 1, 0, 3, 2],
        "wall": [],
    }
    if source == "lidar":
        b["source"] = "lidar"
    return b


def _south(b: dict[str, Any]) -> roofs.Face:
    return max(roofs.faces(b["positions"], b["roof"]), key=lambda f: f.south)


def _quads(panels: list[list[list[float]]]) -> np.ndarray:
    return np.asarray(panels, dtype=np.float64).reshape(-1, 4, 3)


def _front(q: np.ndarray) -> np.ndarray:
    n = np.cross(q[:, 1] - q[:, 0], q[:, 3] - q[:, 0])
    return n / np.linalg.norm(n, axis=1)[:, None]


@pytest.mark.parametrize(
    ("id_", "hash_", "tints"),
    [
        # kit/neighbours.js houseHash and roofTint, run in node
        (
            "B1D3921A-33E9-4ECC-8BB8-24D38B16B0AA",
            1377134745,
            ["#744738", "#74432f", "#56585c", "#414346"],
        ),
        ("lidar-12", 1132219421, ["#875340", "#6c3e2e", "#676661", "#4b4d50"]),
        ("old", 318656, ["#734737", "#73422f", "#55575b", "#404246"]),
    ],
)
def test_the_roof_colours_are_the_kits(id_: str, hash_: int, tints: list[str]) -> None:
    assert roofs.house_hash(id_) == hash_
    assert [roofs.roof_tint(f, hash_) for f in roofs.FAMILIES] == tints


@pytest.mark.parametrize(
    ("median", "family"),
    [
        ((0.62, 0.45, 0.4), "terracotta"),
        ((0.4, 0.3, 0.27), "brown"),
        ((0.55, 0.55, 0.56), "grey"),
        ((0.3, 0.3, 0.31), "anthracite"),
        ((0.5, 0.47, 0.45), "grey"),  # a hint of warmth under the haze is not clay yet
        ((0.6, 0.52, 0.5), "terracotta"),
    ],
)
def test_a_photographed_roof_is_snapped_to_a_family(
    median: tuple[float, float, float], family: str
) -> None:
    assert roofs.roof_family(median) == family


@pytest.mark.parametrize(
    ("family", "year", "cover", "why"),
    [
        ("terracotta", 1905, "clay-beaver", "year"),
        ("brown", 1919, "clay-beaver", "year"),
        ("terracotta", 1930, "clay-interlocking", "year"),
        ("brown", 1959, "clay-interlocking", "year"),
        ("terracotta", None, "clay-interlocking", "colour"),
        ("grey", 1950, "fibre-cement", "year"),
        ("anthracite", 1925, "fibre-cement", "year"),
        ("grey", None, "fibre-cement", "colour"),
    ],
)
def test_the_cover_follows_the_colour_and_the_age(
    family: str, year: int | None, cover: str, why: str
) -> None:
    assert roofs.cover_for(family, year, 12345) == (cover, why)


@pytest.mark.parametrize(
    ("family", "year", "covers"),
    [
        ("terracotta", 1985, {"clay-interlocking", "concrete-tile"}),
        ("grey", 1975, {"fibre-cement", "concrete-tile"}),
        ("anthracite", 1990, {"fibre-cement", "concrete-tile"}),
        ("anthracite", 2010, {"metal-seam", "fibre-cement"}),
        ("grey", 2015, {"concrete-tile", "fibre-cement", "metal-seam"}),
    ],
)
def test_the_houses_hash_breaks_ties_so_a_street_is_not_uniform(
    family: str, year: int, covers: set[str]
) -> None:
    picked = {roofs.cover_for(family, year, roofs.house_hash(f"house-{k}")) for k in range(60)}
    assert {c for c, _ in picked} == covers
    assert {w for _, w in picked} == {"hash"}


def _pixels(rgb: tuple[int, int, int], n: int = 200, jitter: int = 4) -> np.ndarray:
    rng = np.random.default_rng(1)
    px = np.asarray(rgb, dtype=np.float32) + rng.integers(-jitter, jitter + 1, (n, 3))
    return np.clip(px, 0, 255) / 255


def test_a_flat_roof_is_green_membrane_or_gravel() -> None:
    assert roofs.flat_kind(_pixels((95, 120, 60))) == "green"
    assert roofs.flat_kind(_pixels((50, 50, 52))) == "membrane"
    assert roofs.flat_kind(_pixels((150, 148, 140), jitter=30)) == "gravel"
    assert roofs.flat_kind(_pixels((50, 50, 52), jitter=40)) == "gravel"  # dark but uneven
    assert roofs.flat_kind(_pixels((95, 120, 60), n=10)) == "gravel"  # too little to tell


def test_the_module_count_follows_the_power() -> None:
    assert [roofs.module_count(kw) for kw in (2.31, 4.6, 13.05)] == [7, 15, 42]


@pytest.mark.parametrize("kw", [2.31, 4.6, 8.0])
def test_modules_lie_flush_on_the_south_face_inside_its_edges(kw: float) -> None:
    house = _gable(0, 0, 14, 10)
    face = _south(house)
    panels = roofs.modules(roofs.faces(house["positions"], house["roof"]), roofs.module_count(kw))
    q = _quads(panels)
    assert len(q) == roofs.module_count(kw)
    # 1.0 m across, 1.7 m up the slope, facing out of the south face
    assert np.allclose(np.linalg.norm(q[:, 1] - q[:, 0], axis=1), roofs.MODULE_W, atol=1e-3)
    assert np.allclose(np.linalg.norm(q[:, 3] - q[:, 0], axis=1), roofs.MODULE_H, atol=1e-3)
    assert np.allclose(_front(q) @ face.normal, 1.0, atol=1e-4)
    assert (q[:, 1, 1] == q[:, 0, 1]).all()  # the low edge level, along the eaves
    # LIFT off the roof, all within EDGE of the face's edges (the eaves at z = 5, the ridge at 0)
    off = (q - face.centre) @ face.normal
    assert np.allclose(off, roofs.LIFT, atol=1e-3)
    on_roof = q - face.normal * roofs.LIFT
    assert on_roof[:, :, 0].min() >= -7 + roofs.EDGE - 1e-3
    assert on_roof[:, :, 0].max() <= 7 - roofs.EDGE + 1e-3
    slope = roofs.EDGE * math.cos(math.radians(35))
    assert on_roof[:, :, 2].min() >= 0 + slope - 1e-3
    assert on_roof[:, :, 2].max() <= 5 - slope + 1e-3
    # never on top of one another
    right, up = roofs._frame(face)
    cells = [Polygon([((p - face.centre) @ right, (p - face.centre) @ up) for p in m]) for m in q]
    assert all(a.intersection(b).area < 1e-6 for i, a in enumerate(cells) for b in cells[i + 1 :])


def test_a_large_plant_covers_at_most_seventy_percent_of_the_face() -> None:
    house = _gable(0, 0, 14, 10)
    face = _south(house)
    panels = roofs.modules(roofs.faces(house["positions"], house["roof"]), roofs.module_count(60))
    assert 0 < len(panels) <= math.floor(roofs.SHARE * face.area / roofs.MODULE_AREA)
    # the north face takes none
    assert (_front(_quads(panels))[:, 2] > 0).all()


def test_the_survey_s_doubled_triangles_and_split_planes_make_one_face() -> None:
    house = _gable(0, 0, 14, 10)
    n = len(house["positions"]) // 3
    # every triangle twice, and the south face's eastern half 0.3 m lower
    pos = list(house["positions"])
    pos += [v - 0.3 if k % 3 == 1 else v for k, v in enumerate(house["positions"])]
    roof = house["roof"] + house["roof"]
    roof += [3, 2 + n, 5 + n]
    fs = roofs.faces(pos, roof)
    south = max(fs, key=lambda f: f.south)
    assert south.area == pytest.approx(14 * 5 / math.cos(math.radians(35)), rel=0.02)


def test_a_slope_meshed_on_a_grid_at_its_altitude_is_one_face() -> None:
    # the lidar's roofs: a 0.5 m grid, its heights a few millimetres off, 600 m up
    rng = np.random.default_rng(3)
    xs, zs = np.meshgrid(np.arange(0, 12.01, 0.5), np.arange(0, 6.01, 0.5))
    ys = 604.0 - 0.7 * zs + rng.uniform(-0.003, 0.003, xs.shape)
    pos = np.stack([xs, ys, zs], axis=-1).reshape(-1, 3)
    w = xs.shape[1]
    roof = []
    for r in range(xs.shape[0] - 1):
        for c in range(w - 1):
            a, b, d, e = r * w + c, r * w + c + 1, (r + 1) * w + c, (r + 1) * w + c + 1
            roof += [a, d, b, b, d, e]
    (face,) = roofs.faces(pos.ravel().tolist(), roof)
    assert face.area == pytest.approx(12 * 6 / math.cos(math.atan(0.7)), rel=0.01)
    assert len(roofs.pitched_modules(face, 20)) == 20


def test_an_entry_that_is_walls_alone_takes_no_plant() -> None:
    walls = {"id": "walls", "positions": [-6, Y, -5, 6, Y, -5, 6, Y, 5, -6, Y, 5], "roof": []}
    house = _flat(0, 0, 10, 8)
    fps = roofs.footprints([walls, house])
    held = roofs.match_records(fps, [roofs.Record("1", 0.0, 0.0, 1990, 1020, 2, 80)])
    found = [roofs.Plant("1", 0.0, 0.0, 4.0), roofs.Plant(None, 0.0, 4.5, 2.0)]
    assert sorted(roofs.match_plants(fps, held, found)) == [0]
    on = roofs.match_plants(fps, held, found, roofed=[False, True])
    assert sorted(on) == [1]
    assert [pl.kw for pl in on[1]] == [4.0, 2.0]


def test_modules_on_a_flat_roof_stand_in_rows_tilted_to_the_south() -> None:
    roof = _flat(0, 0, 20, 14)
    face = roofs.faces(roof["positions"], roof["roof"])[0]
    count = roofs.module_count(6.0)
    q = _quads(roofs.flat_modules(face, count))
    assert len(q) == count
    front = _front(q)
    tilt = np.degrees(np.arccos(front[:, 1]))
    assert np.allclose(tilt, roofs.FLAT_TILT, atol=0.05)
    assert np.allclose(front[:, 0], 0, atol=1e-6)  # due south: the building's sides run east-west
    assert (front[:, 2] > 0).all()
    low, high = q[:, [0, 1], 1], q[:, [2, 3], 1]
    assert np.allclose(low, Y + roofs.LIFT, atol=1e-3)
    assert np.allclose(high - low, roofs.MODULE_W * math.sin(math.radians(12)), atol=1e-3)
    # the rows far enough apart for the winter sun to clear them
    rows = sorted({round(float(z), 2) for z in q[:, 0, 2]})
    assert len(rows) >= 2
    depth = roofs.MODULE_W * math.cos(math.radians(12))
    shade = roofs.MODULE_W * math.sin(math.radians(12)) / math.tan(math.radians(20))
    assert min(np.diff(rows)) >= depth + shade - 0.01
    # within FLAT_EDGE of the roof's edges
    assert np.abs(q[:, :, 0]).max() <= 10 - roofs.FLAT_EDGE + 1e-3
    assert np.abs(q[:, :, 2]).max() <= 7 - roofs.FLAT_EDGE + 1e-3


def test_rows_on_a_turned_flat_roof_follow_its_sides() -> None:
    roof = _flat(0, 0, 20, 14, turn=30)
    face = roofs.faces(roof["positions"], roof["roof"])[0]
    q = _quads(roofs.flat_modules(face, 10))
    along = q[:, 0] - q[:, 1]
    along /= np.linalg.norm(along, axis=1)[:, None]
    side = np.array([math.cos(math.radians(30)), 0, math.sin(math.radians(30))])
    assert np.allclose(np.abs(along @ side), 1.0, atol=1e-4)
    assert (_front(q)[:, 2] > 0).all()


def test_a_register_entry_gives_its_year_to_the_building_that_holds_it() -> None:
    a, annex, b = _flat(0, 0, 10, 8), _flat(7.2, 0, 4, 4), _flat(30, 0, 10, 8)
    fps = roofs.footprints([a, annex, b])
    recs = [
        roofs.Record("1", 1.0, 1.0, 1955, 1020, 2, 80),
        roofs.Record("2", 30.0, 6.0, 1990, 1020, 2, 80),  # 2 m off b's façade
        roofs.Record("3", 50.0, 0.0, 2001, 1020, 2, 80),  # by nothing
    ]
    held = roofs.match_records(fps, recs)
    assert [r.egid for r in held[0]] == ["1"]
    assert [r.egid for r in held[1]] == ["1"]  # the annex touching it
    assert [r.egid for r in held[2]] == ["2"]


def test_a_plant_lands_on_its_registered_building_else_the_nearest_house() -> None:
    a, b = _flat(0, 0, 10, 8), _flat(30, 0, 10, 8)
    fps = roofs.footprints([a, b])
    held = roofs.match_records(fps, [roofs.Record("1", 1.0, 1.0, 1955, 1020, 2, 80)])
    on = roofs.match_plants(
        fps,
        held,
        [
            roofs.Plant("1", 14.0, 9.0, 4.6),  # the address point, off the house
            roofs.Plant("1", 0.0, 0.0, 2.0),  # a second plant on it
            roofs.Plant(None, 30.0, 10.0, 3.0),  # 6 m off b
            roofs.Plant(None, 60.0, 0.0, 9.0),  # 25 m from anything
        ],
    )
    assert sorted(on) == [0, 1]
    assert [pl.kw for pl in on[0]] == [4.6, 2.0]
    assert (on[0][0].x, on[0][0].z) == (1.0, 1.0)  # at the building's point
    assert [pl.kw for pl in on[1]] == [3.0]


def _identify(pages: list[list[dict[str, Any]]], asked: list[dict[str, str]]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        asked.append(params)
        page = int(params["offset"]) // roofs.PAGE
        return httpx.Response(200, json={"results": pages[page] if page < len(pages) else []})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _gwr(egid: int, **attrs: Any) -> dict[str, Any]:
    base = {"egid": str(egid), "gstat": 1004, "gkode": 2536250.0, "gkodn": 1152550.0}
    return {"featureId": f"{egid}_0", "attributes": {**base, **attrs}}


async def test_the_building_register_is_read_page_by_page_one_entry_a_building() -> None:
    first = [_gwr(k, gbauj=1950 + k % 50) for k in range(roofs.PAGE - 1)]
    first.append(_gwr(0, gbauj=1999))  # its second entrance
    second = [
        _gwr(500, gbauj=None, gbaup=8013, gastw=3, gkat=1020, garea=120),
        _gwr(501, gstat=1007, gbauj=1960),  # demolished
    ]
    asked: list[dict[str, str]] = []
    async with _identify([first, second], asked) as client:
        recs = await roofs.records(2536240, 1152557, 200, client)
    assert [a["offset"] for a in asked] == ["0", str(roofs.PAGE)]
    assert asked[0]["layers"] == f"all:{roofs.GWR}"
    by = {r.egid: r for r in recs}
    assert len(recs) == roofs.PAGE
    assert by["0"].year == 1950
    # no year: the middle of its construction period
    assert (by["500"].year, by["500"].storeys, by["500"].category) == (1953, 3, 1020)
    assert (by["500"].x, by["500"].z) == (10.0, 7.0)
    assert "501" not in by


async def test_the_plants_register_keeps_the_photovoltaic_ones() -> None:
    def plant(egid: int | None, power: str, kind: str = "Photovoltaic") -> dict[str, Any]:
        return {
            "geometry": {"type": "Point", "coordinates": [2536290.1, 1152474.9]},
            "properties": {"egid": egid, "total_power": power, "sub_category_en": kind},
        }

    found = [plant(3180859, "13.05 kW"), plant(886627, "2.31 kW"), plant(None, "30 kW", "Hydro")]
    async with _identify([found], []) as client:
        plants = await roofs.plants(2536240, 1152557, 200, client)
    assert [(p.egid, p.kw) for p in plants] == [("3180859", 13.05), ("886627", 2.31)]
    assert (plants[0].x, plants[0].z) == (50.1, 82.1)


@pytest.mark.parametrize(
    ("text", "kw"),
    [("13.05 kW", 13.05), ("1'250.5 kW", 1250.5), ("13,05 kW", 13.05), ("1.2 MW", 1200.0)],
)
def test_the_plants_power_is_read_from_its_text(text: str, kw: float) -> None:
    assert roofs.power_kw(text) == pytest.approx(kw)


R = 20.0
PX = 400  # 0.1 m a pixel over ± R
CLAY = (165, 105, 85)
FIELD = (110, 140, 70)
PANEL = (40, 60, 110)


def _photo(fill: tuple[int, int, int]) -> np.ndarray:
    return np.tile(np.asarray(fill, dtype=np.uint8), (PX, PX, 1))


def _paint(photo: np.ndarray, x0: float, z0: float, x1: float, z1: float, rgb: Any) -> None:
    def to(v: float) -> int:
        return round((v + R) / (2 * R) * PX)

    photo[to(z0) : to(z1), to(x0) : to(x1)] = rgb


def test_the_photo_places_modules_only_where_it_clearly_shows_them() -> None:
    house = _gable(0, 0, 14, 10)
    south = _south(house)
    north = min(roofs.faces(house["positions"], house["roof"]), key=lambda f: f.south)
    photo = _photo(CLAY)
    _paint(photo, -5, 1, 5, 4.5, PANEL)
    panels = roofs.photo_modules(south, photo, R)
    assert 4 <= len(panels) <= math.floor(10 * 3.5 / math.cos(math.radians(35)) / 1.7)
    # a shadow's dark blue, or blue on the north face: none
    dark = _photo(CLAY)
    _paint(dark, -5, 1, 5, 4.5, (15, 20, 40))
    assert roofs.photo_modules(south, dark, R) == []
    shaded = _photo(CLAY)
    _paint(shaded, -5, -4.5, 5, -1, PANEL)
    assert roofs.photo_modules(north, shaded, R) == []
    # a few blue pixels are not a plant
    spots = _photo(CLAY)
    _paint(spots, -1, 2, 0, 3, PANEL)
    assert roofs.photo_modules(south, spots, R) == []
    # a pale blue-grey roof (Le Mont's D1315214) or a tarp: too bright for modules
    pale = _photo((162, 179, 192))
    assert roofs.photo_modules(south, pale, R) == []


def test_each_building_gets_its_roof() -> None:
    house = _gable(-8, 0, 10, 8)
    new = _flat(9, 0, 8, 8, source="lidar")
    photo = _photo(FIELD)
    _paint(photo, -13, -4, -3, 4, CLAY)
    _paint(photo, -12, 1, -4, 3.5, PANEL)
    recs = [roofs.Record("1", -8.0, 0.0, 1912, 1020, 2, 80)]
    old, newer = roofs.decide([house, new], photo, R, recs, [])
    assert old["family"] == "terracotta"
    assert (old["cover"], old["year"]) == ("clay-beaver", 1912)
    assert old["why"] == {"colour": "photo", "cover": "year", "panels": "photo"}
    assert old["panels"]
    assert old["pv_kw"] is None
    assert old["colour"] == roofs.roof_tint("terracotta", roofs.house_hash(house["id"]))
    assert old["egid"] == "1"
    # the photo shows a field where the lidar's house stands: its neighbours' colours, a new house's cover
    assert newer["why"]["colour"] == "neighbours"
    assert newer["family"] == "terracotta"
    assert newer["year"] is None
    assert newer["why"]["cover"] == "hash"
    assert (newer["flat"], newer["panels"]) == ("gravel", [])
    assert "flat_colour" not in newer
    # a registered plant: its power, its modules, no other
    old, _ = roofs.decide([house, new], photo, R, recs, [roofs.Plant("1", -8.0, 6.0, 2.31)])
    assert (old["pv_kw"], len(old["panels"]), old["why"]["panels"]) == (2.31, 7, "bfe")
    # without the registers: no year, no plant, still a roof
    old, _ = roofs.decide([house, new], photo, R, None, None)
    assert (old["year"], old["pv_kw"], old["why"]["cover"]) == (None, None, "colour")


def test_one_entry_of_two_houses_takes_its_modules_on_the_plant_s_house() -> None:
    a, b = _gable(-10, 0, 8, 8), _gable(10, 0, 8, 8)
    n = len(a["positions"]) // 3
    both = {
        "id": "two",
        "positions": a["positions"] + b["positions"],
        "roof": a["roof"] + [i + n for i in b["roof"]],
        "wall": [],
    }
    (spec,) = roofs.decide([both], _photo(CLAY), R, [], [roofs.Plant(None, 12.0, 3.0, 2.0)])
    q = _quads(spec["panels"])
    assert len(q) == roofs.module_count(2.0)
    assert (q[:, :, 0] > 6).all()


def _stored(root: Path, altitudes: str | None = context.ALTITUDES) -> None:
    d = context.context_dir(root)
    d.mkdir(parents=True)
    house = _gable(0, 0, 10, 8)
    (d / "buildings.json").write_text(json.dumps({"buildings": [house]}))
    Image.fromarray(_photo(CLAY)).save(d / "photo.jpg")
    meta: dict[str, Any] = {"file": "buildings.json", "count": 1}
    if altitudes:
        meta["altitudes"] = altitudes
    ctx = {
        "place": {"e": 2536240, "n": 1152557},
        "radius": R,
        "photo": {"file": "photo.jpg", "pixels": PX},
        "buildings": meta,
        "fetched_at": "2026-11-12T16:28:29+00:00",
    }
    (d / "context.json").write_text(json.dumps(ctx))


async def test_surroundings_fetched_before_get_their_roofs_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stored(tmp_path / "p1")
    _stored(tmp_path / "p2", altitudes=None)  # its survey's altitudes first (#140)
    (tmp_path / "p3").mkdir()
    asked = []

    async def records(e0: float, n0: float, radius: float, client: Any) -> list[roofs.Record]:
        asked.append((e0, n0, radius))
        return [roofs.Record("7", 0.0, 0.0, 1931, 1020, 2, 80)]

    async def plants(*a: Any) -> list[roofs.Plant]:
        return [roofs.Plant("7", 0.0, 5.0, 2.31)]

    monkeypatch.setattr(roofs, "records", records)
    monkeypatch.setattr(roofs, "plants", plants)
    assert await context.fill_roofs(tmp_path) == 1
    assert asked == [(2536240, 1152557, R)]
    (b,) = json.loads((tmp_path / "p1/context/buildings.json").read_text())["buildings"]
    assert b["roof"] == [0, 1, 2, 0, 2, 3, 3, 2, 5, 3, 5, 4]  # the triangles stay
    spec = b["roof_spec"]
    assert (spec["year"], spec["cover"], spec["pv_kw"], len(spec["panels"])) == (
        1931,
        "clay-interlocking",
        2.31,
        7,
    )
    ctx = context.load(tmp_path / "p1")
    assert ctx is not None
    assert ctx["buildings"]["roofs"] == context.ROOFS
    assert "roof_spec" not in (tmp_path / "p2/context/buildings.json").read_text()
    assert await context.fill_roofs(tmp_path) == 0
    assert len(asked) == 1


async def test_surroundings_whose_registers_cannot_be_read_wait_for_the_next_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stored(tmp_path / "p1")

    async def offline(*a: Any) -> list[Any]:
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(roofs, "records", offline)
    assert await context.fill_roofs(tmp_path) == 0
    ctx = context.load(tmp_path / "p1")
    assert ctx is not None
    assert "roofs" not in ctx["buildings"]
    assert "roof_spec" not in (tmp_path / "p1/context/buildings.json").read_text()


def test_a_mesh_the_modules_cannot_follow_keeps_the_rest_of_its_roof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*a: Any) -> list[Any]:
        raise ValueError("a self-intersecting roof")

    monkeypatch.setattr(roofs, "modules", broken)
    house = _gable(0, 0, 10, 8)
    recs = [roofs.Record("1", 0.0, 0.0, 1931, 1020, 2, 80)]
    (spec,) = roofs.decide([house], _photo(CLAY), R, recs, [roofs.Plant("1", 0.0, 2.0, 3.0)])
    assert (spec["cover"], spec["year"], spec["pv_kw"]) == ("clay-interlocking", 1931, 3.0)
    assert (spec["panels"], spec["why"]["panels"]) == ([], None)
