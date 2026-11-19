"""The listing photo's white balance reference (#113): the walls and wall tiles in the frame, each held to its own colour
as the export gives it, so the grade keeps the project's finishes.

A room the photo lights with its own warm panel (a hall seen through a doorway, next_rooms_lit) is lit otherwise than
the room photographed: balancing its walls to white turned the bathroom's off-white tiles blue-grey. A wall tile is not
white either (a grey marble, a sage glaze): each surface is compared with its own colour, not with white.
Runs without Blender (numpy only): photo.py measures, this module decides.
"""

import numpy as np

LUMA = np.array([0.2126, 0.7152, 0.0722])
# the grade's warmth over a surface's own colour, CIELAB a*, b*: the kit's plaster (a* +0.8, b* +2.1) shows at a* +0.5,
# b* +4, where listing photos held their white walls before #113
WARMTH = (-0.3, 1.9)
LIGHT = 0.18  # a surface darker than this (linear luminance) is too dark to read its colour shift on
SURFACES = {"wall", "partition", "wallTiles"}
NOT_SURFACES = {"window", "door", "interiorDoor"}


def to_linear(c):
    """sRGB 0-1 to linear."""
    c = np.asarray(c, float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def to_srgb(c):
    """Linear to sRGB 0-1."""
    c = np.clip(np.asarray(c, float), 0.0, 1.0)
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * c ** (1 / 2.4) - 0.055)


def lab(rgb):
    """CIELAB (D65) of an sRGB colour, 0-255."""
    lin = to_linear(np.asarray(rgb, float) / 255.0)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = lin @ m.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.array([116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])])


def shift(seen, colour):
    """How far the photo moves a surface off its own colour: CIELAB (a*, b*) of `seen` (sRGB 0-255) less those of
    `colour` (linear RGB, the export's) brought to the luminance it is seen at."""
    seen = np.asarray(seen, float)
    colour = np.asarray(colour, float)
    y = float(to_linear(seen / 255.0) @ LUMA)
    own = to_srgb(colour * y / max(float(colour @ LUMA), 1e-6)) * 255.0
    return lab(seen)[1:] - lab(own)[1:]


def surface(kinds):
    """Whether a mesh of these kinds (its own and its parents') is a room's wall surface: paint or wall tiles."""
    return bool(kinds & SURFACES) and not kinds & NOT_SURFACES


def reference(samples, lit=()):
    """The samples the white balance reads, of `samples` ({"room": the room a sample lies in or None, "colour": its
    surface's linear RGB or None}): the light surfaces in the rooms of the frame but those `lit` by the photo's own
    lamps."""
    return [s for s in samples if s["room"] is not None and s["room"] not in lit and s["colour"] is not None
            and float(np.asarray(s["colour"], float) @ LUMA) >= LIGHT]
