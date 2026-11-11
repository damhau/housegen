"""What a listing photo shows of the project's furnishing (#94).

    furnished  the project's pieces, dressed and staged (the default, for agencies)
    light      its furniture without the decor: no accessories, art or plants, nothing added by the staging
    empty      the architecture alone: finishes and light, with what is fitted (kitchen, bathrooms, wardrobes,
               radiators) and the fixed lights
"""

import re

LEVELS = ("furnished", "light", "empty")

FITTED = re.compile(
    r"kitchen|island|\bsink\b|oven|fridge|refrigerator|dishwasher|washer|dryer|\bwc\b|toilet|bidet|basin|vanity|"
    r"bath ?tub|\btub\b|shower|towel ?rail|radiator|wardrobe|closet|built ?in|fireplace|stove"
)
FIXED_LIGHT = re.compile(
    r"pendant|downlight|ceiling|wall ?l(ight|amp)|sconce|chandelier|track|cove|step ?light|under ?cabinet|picture ?light|"
    r"mirror ?light"
)
MOVABLE_LIGHT = re.compile(r"(table|floor|desk|bedside|reading) ?lamp")
DECOR = re.compile(
    r"vase|plant|ficus|ivy|palm|herbs?\b|flower|stems|branch|bouquet|\bbooks?\b|photo ?frame|picture ?frame|\bart\b|"
    r"painting|poster|\bprints?\b|cushion|pillow|throw|tray|candle|jar|clock|basket|bowl|plate|\bcups?\b|mug|bottle|"
    r"carafe|canister|teapot|kettle|toaster|ornament|sculpture|figurine|decor|towel ?stack|accessor|wall ?shelf|magazine|"
    r"fruit|blanket|\btoys?\b|coats?\b"
)
WET = ("bath", "wc", "shower")


def words(label):
    """`bathAccessories`, `towel-rail_2` -> `bath accessories`, `towel rail 2`."""
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(label or ""))
    return re.sub(r"[-_.]+", " ", s).lower()


def what(label):
    """fitted | light | decor | mirror | furniture: what a kit piece is for the staging."""
    n = words(label)
    if FITTED.search(n):
        return "fitted"
    if FIXED_LIGHT.search(n) and not MOVABLE_LIGHT.search(n):
        return "light"
    if "mirror" in n:
        return "mirror"
    if DECOR.search(n):
        return "decor"
    return "furniture"


def keeps(level, kind, wet=False):
    """Whether a piece of this kind stays at `level`; a mirror stays empty in a bathroom (it is fitted there)."""
    if level == "furnished" or kind in ("fitted", "light"):
        return True
    if kind == "mirror":
        return level == "light" or wet
    if level == "light":
        return kind != "decor"
    return False


def strip(pieces, level, label, wet, hide):
    """Hide the pieces `level` leaves out; returns their labels. `label(o)` names a piece, `wet(o)` says whether it
    stands in a bathroom or a WC, `hide(o)` takes it out of the render."""
    gone = []
    for o in pieces:
        if not keeps(level, what(label(o)), wet(o)):
            hide(o)
            gone.append(label(o))
    return gone
