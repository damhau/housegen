"""The listing photo's white balance (#113): the walls and wall tiles in the frame, each held to its own colour, never
a room the photo lit with its own warm panel (photo/blender/balance.py: the decision runs without Blender)."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
# the export's mean colours (sRGB) on Montelly49-Dev4 v25: the kit's plaster and its "marble-grey" wall tiles
PLASTER = (214.7, 210.6, 207.4)
MARBLE = (212.7, 210.7, 207.8)


@pytest.fixture
def balance(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(ROOT / "photo" / "blender"))
    return importlib.import_module("balance")


def _linear(balance: ModuleType, srgb: tuple[float, ...]) -> Any:
    return balance.to_linear(np.array(srgb) / 255.0)


def test_a_surface_seen_in_its_own_colour_is_not_shifted_at_any_exposure(
    balance: ModuleType,
) -> None:
    colour = _linear(balance, MARBLE)
    for k in (0.3, 0.6, 1.0):
        seen = balance.to_srgb(colour * k) * 255.0
        assert np.allclose(balance.shift(seen, colour), 0.0, atol=0.05)


def test_off_white_tiles_seen_blue_grey_are_shifted_blue(balance: ModuleType) -> None:
    # the Bain's tiles in v25's photo 04: #b3bbbd against the export's warm grey
    a, b = balance.shift((178.8, 187.0, 189.0), _linear(balance, MARBLE))
    assert b < -3.0
    assert a < -2.0


def test_the_plaster_keeps_the_warmth_the_white_walls_had(balance: ModuleType) -> None:
    # before #113 the grade held the walls at a* +0.5, b* +4 whatever their colour: the kit's plaster lands there
    _, a, b = balance.lab(PLASTER)
    assert a + balance.WARMTH[0] == pytest.approx(0.5, abs=0.15)
    assert b + balance.WARMTH[1] == pytest.approx(4.0, abs=0.15)


def test_wall_tiles_are_a_surface_doors_and_windows_are_not(balance: ModuleType) -> None:
    assert balance.surface({"wallTiles", None})
    assert balance.surface({"partition", "floorPlan"})
    assert not balance.surface({"interiorDoor", "partition"})
    assert not balance.surface({"window", "wall", "perimeter"})
    assert not balance.surface({"bathtub"})


def test_the_reference_leaves_out_the_rooms_the_photo_lights_and_dark_or_unknown_surfaces(
    balance: ModuleType,
) -> None:
    light, dark = _linear(balance, MARBLE), _linear(balance, (60, 70, 60))
    samples = [
        {"room": "Bain", "colour": light},
        {"room": "Bain", "colour": light},
        {"room": "Hall étage", "colour": light},
        {"room": "Salon", "colour": light},
        {"room": "Bain", "colour": dark},
        {"room": "Bain", "colour": None},
        {"room": None, "colour": light},
    ]
    kept = balance.reference(samples, lit=["Hall étage"])
    assert [s["room"] for s in kept] == ["Bain", "Bain", "Salon"]
