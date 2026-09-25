"""A project's surroundings (#39): fetched once into data/projects/<id>/context/, drawn by the viewer only.

  context.json    where (anchor in LV95, what was searched), the files, the alignment with the scene,
                  the credits
  terrain.bin     altitudes, float32 little-endian, (2R/step + 1)² samples, rows north → south
  photo.jpg       the aerial photo over the same square, north up
  buildings.json  the neighbours: positions (local x, altitude, local z) and roof / wall triangles

The local frame is the kit's: metres, x east, z south, around the anchor. The alignment says where
the scene sits in it: the scene's origin at local (x, z), turned by `rotation` degrees (clockwise
seen from above: the scene's -z points `rotation`° east of north), its y=0 at altitude `ground`.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from housegen.geo import swiss

VERSION = 1
PHOTO_PIXELS = 3600
CREDITS = ["© swisstopo (swissALTI3D, SWISSIMAGE, swissBUILDINGS3D)"]


def context_dir(project_root: Path) -> Path:
    return project_root / "context"


def load(project_root: Path) -> dict[str, Any] | None:
    p = context_dir(project_root) / "context.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def save(project_root: Path, ctx: dict[str, Any]) -> None:
    p = context_dir(project_root) / "context.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(ctx, indent=1), encoding="utf-8")
    tmp.replace(p)


def remove(project_root: Path) -> None:
    shutil.rmtree(context_dir(project_root), ignore_errors=True)


async def build(
    project_root: Path, place: swiss.Place, radius: int = 200, step: float = 1.0
) -> dict[str, Any]:
    """Fetch the surroundings of `place` into the project's context/ (replacing any earlier ones)."""
    e0, n0 = round(place.e), round(place.n)
    async with httpx.AsyncClient(headers=swiss.UA, timeout=60, follow_redirects=True) as client:
        ground_task = asyncio.create_task(swiss.terrain(e0, n0, radius, client, step))
        photo_task = asyncio.create_task(swiss.aerial_photo(e0, n0, radius, client))
        ground = await ground_task
        buildings = await swiss.buildings(e0, n0, radius, client, ground, step)
        photo = await photo_task
    out = context_dir(project_root)
    tmp = out.with_name("context.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    (tmp / "terrain.bin").write_bytes(ground.astype("<f4").tobytes())
    # the WMS's JPEG is heavy (6 MB): 3600 px (11 cm a pixel over 400 m) at a quality the viewer does not tell apart
    img = Image.open(io.BytesIO(photo)).convert("RGB")
    if img.width > PHOTO_PIXELS:
        img = img.resize((PHOTO_PIXELS, PHOTO_PIXELS), Image.Resampling.LANCZOS)
    img.save(tmp / "photo.jpg", "JPEG", quality=75, optimize=True, progressive=True)
    (tmp / "buildings.json").write_text(
        json.dumps({"buildings": [b.__dict__ for b in buildings]}, separators=(",", ":")),
        encoding="utf-8",
    )
    center = float(ground[ground.shape[0] // 2, ground.shape[1] // 2])
    ctx: dict[str, Any] = {
        "version": VERSION,
        "place": {"label": place.label, "kind": place.kind, "e": e0, "n": n0},
        "radius": radius,
        "terrain": {"file": "terrain.bin", "size": int(ground.shape[0]), "step": step},
        "photo": {"file": "photo.jpg", "pixels": img.width},
        "buildings": {"file": "buildings.json", "count": len(buildings)},
        # a first guess: the scene's origin on the searched point, its -z to the north, its y=0 on
        # the ground there. The owner sets it right in the alignment editor.
        "alignment": {
            "x": 0.0,
            "z": 0.0,
            "rotation": 0.0,
            "ground": round(center, 2),
            "set": False,
        },
        "credits": CREDITS,
        "fetched_at": datetime.now(UTC).isoformat(),
    }
    (tmp / "context.json").write_text(json.dumps(ctx, indent=1), encoding="utf-8")
    shutil.rmtree(out, ignore_errors=True)
    tmp.replace(out)
    return ctx


# a plan title block: "BF N°3013 A MONT-SUR-LAUSANNE 1052", "Parcelle n° 412, Commune de Savigny"
_PARCEL = re.compile(
    r"(?:\bBF\b|bien-fonds|biens-fonds|parcelle|Parzelle|Grundstück)\s*(?:n[°o]?\.?\s*|Nr\.?\s*)?(\d{1,6})"
    r"[\s,]+(?:[àaÀA]\s+|de\s+|commune\s+de\s+|in\s+|Gemeinde\s+)?([A-ZÀ-Ÿa-zà-ÿ][A-ZÀ-Ÿa-zà-ÿ' .-]{2,40}?)"
    r"(?=\s+\d{4}\b|\s*\n|\s*$|,)",
    re.IGNORECASE,
)


def suggestion_from_plans(pdfs: list[Path]) -> str | None:
    """'3013 Mont-sur-Lausanne' from a plan's title block, when it names the parcel."""
    try:
        import pymupdf
    except ImportError:  # pragma: no cover
        return None
    for pdf in pdfs:
        try:
            doc = pymupdf.open(pdf)  # type: ignore[no-untyped-call]
        except Exception:
            continue
        try:
            for k in range(min(3, len(doc))):
                page = doc[k]
                m = _PARCEL.search(page.get_text())  # type: ignore[no-untyped-call]
                if m:
                    commune = m.group(2).strip(" .-")
                    small = {
                        "sur",
                        "sous",
                        "de",
                        "des",
                        "du",
                        "la",
                        "le",
                        "les",
                        "en",
                        "am",
                        "an",
                        "im",
                    }
                    words = commune.lower().split("-")
                    commune = "-".join(
                        w if i and w in small else w.capitalize() for i, w in enumerate(words)
                    )
                    return f"{m.group(1)} {commune}"
        finally:
            doc.close()  # type: ignore[no-untyped-call]
    return None
