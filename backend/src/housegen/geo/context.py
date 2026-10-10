"""A project's surroundings (#39): fetched once into data/projects/<id>/context/, drawn by the viewer only.

  context.json    where (anchor in LV95, what was searched), the files, the alignment with the scene,
                  the credits
  terrain.bin     altitudes, float32 little-endian, (2R/step + 1)² samples, rows north → south
  photo.jpg       the aerial photo over the same square, north up
  buildings.json  the neighbours: positions (local x, altitude, local z) and roof / wall triangles;
                  swissBUILDINGS3D's, then the newer ones the lidar has (source "lidar", geo/lidar.py)
  trees.json      the lidar's trees and bushes: [local x, foot altitude, local z, height, crown radius, kind]
  cover.png       the ground by type near the house (geo/landcover.py): masks, R asphalt, G lawn, B paving, A gravel

The local frame is the kit's: metres, x east, z south, around the anchor. The alignment says where
the scene sits in it: the scene's origin at local (x, z), turned by `rotation` degrees (clockwise
seen from above: the scene's -z points `rotation`° east of north), its y=0 at altitude `ground`;
`near` is the radius around the house drawn in 3D (trees, ground by type), the photo beyond (#51).
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from PIL import Image

from housegen.geo import far, landcover, lidar, swiss

logger = logging.getLogger(__name__)

VERSION = 1
PHOTO_PIXELS = 3600
CREDITS = ["© swisstopo (swissALTI3D, SWISSIMAGE, swissBUILDINGS3D)"]
LIDAR_CREDITS = ["© swisstopo (swissALTI3D, SWISSIMAGE, swissBUILDINGS3D, swissSURFACE3D)"]
COVER_CREDIT = "mensuration officielle (geodienste.ch)"
COVER_EXTENT = (
    180  # metres around the searched point: the ground by type, for a 3D radius up to 165 m
)
NEAR = 120  # metres: the default 3D radius around the house (#51)


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
        cloud_task = asyncio.create_task(lidar.cloud(e0, n0, radius, client))
        cover_task = asyncio.create_task(
            landcover.surfaces(e0, n0, min(radius, COVER_EXTENT), client)
        )
        ground = await ground_task
        buildings = await swiss.buildings(e0, n0, radius, client, ground, step)
        photo = await photo_task
        # the newest lidar: the houses swissBUILDINGS3D does not have yet (#66). Optional
        cloud: lidar.Cloud | None = None
        newer: list[swiss.Building] = []
        trees: list[lidar.Tree] = []
        try:
            cloud = await cloud_task
            if cloud is not None:
                newer = await asyncio.to_thread(
                    lidar.new_buildings, cloud, buildings, ground, radius, step
                )
                # and its trees and bushes (#50)
                trees = await asyncio.to_thread(lidar.trees, cloud, ground, radius, step)
        except (httpx.HTTPError, OSError, RuntimeError, ValueError) as e:
            logger.warning("geo.lidar_failed", extra={"error": str(e)})
        buildings = [*buildings, *newer]
        # the ground by type near the house (#50): the cadastral survey's land cover. Optional
        cover: list[landcover.Surface] = []
        try:
            cover = await cover_task
        except (httpx.HTTPError, OSError, RuntimeError, ValueError) as e:
            logger.warning("geo.cover_failed", extra={"error": str(e)})
        # the far landscape (the horizon: the lake, the Alps, the Jura): optional, the rest stands without it
        try:
            far_heights = await far.heights(e0, n0, client, ground, radius, step)
            mid_photo, far_photo = await far.photos(e0, n0, client)
        except (httpx.HTTPError, RuntimeError, ValueError, OSError) as e:
            logger.warning("geo.far_failed", extra={"error": str(e)})
            far_heights = None
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
    cover_meta: dict[str, Any] | None = None
    if cover:
        ext = min(radius, COVER_EXTENT)
        m = landcover.masks(cover, ext)
        (tmp / "cover.png").write_bytes(landcover.cover_png(m))
        cover_meta = {
            "file": "cover.png",
            "extent": ext,
            "cell": landcover.MASK_CELL,
            "channels": list(landcover.NAMES),
            # the photo's mean colour (sRGB) under each: the kit tints its finishes relative to it
            "colours": landcover.class_colours(m, np.asarray(img), ext, radius),
            "surfaces": len(cover),
        }
    if trees:
        (tmp / "trees.json").write_text(
            json.dumps(
                {"trees": [[t.x, t.ground, t.z, t.height, t.radius, t.kind] for t in trees]},
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
    if far_heights is not None:
        (tmp / "far.bin").write_bytes(far_heights.astype("<f4").tobytes())
        (tmp / "far-mid.jpg").write_bytes(mid_photo)
        (tmp / "far.jpg").write_bytes(far_photo)
    center = float(ground[ground.shape[0] // 2, ground.shape[1] // 2])
    ctx: dict[str, Any] = {
        "version": VERSION,
        "place": {"label": place.label, "kind": place.kind, "e": e0, "n": n0},
        "radius": radius,
        "terrain": {"file": "terrain.bin", "size": int(ground.shape[0]), "step": step},
        "photo": {"file": "photo.jpg", "pixels": img.width},
        "buildings": {"file": "buildings.json", "count": len(buildings), "lidar": len(newer)},
        "lidar": {"year": cloud.year} if cloud is not None else None,
        # [local x, foot altitude, local z, height, crown radius, kind]
        "trees": {"file": "trees.json", "count": len(trees)} if trees else None,
        # masks on a 0.25 m grid over ± extent (R asphalt, G lawn, B paving, A gravel)
        "cover": cover_meta,
        # rings by azimuths, azimuth a at x = r cos a, z = r sin a; photos north up over ± their extent
        "far": {
            "file": "far.bin",
            "radii": far.radii().tolist(),
            "azimuths": far.AZIMUTHS,
            "photos": [
                {"file": "far-mid.jpg", "extent": far.MID},
                {"file": "far.jpg", "extent": far.EXTENT},
            ],
        }
        if far_heights is not None
        else None,
        # a first guess: the scene's origin on the searched point, its -z to the north, its y=0 on
        # the ground there. The owner sets it right in the alignment editor.
        "alignment": {
            "x": 0.0,
            "z": 0.0,
            "rotation": 0.0,
            "ground": round(center, 2),
            # the 3D near the house (trees, ground by type), the photo beyond: the owner may change it
            "near": NEAR,
            "set": False,
        },
        "credits": [
            *(LIDAR_CREDITS if cloud is not None else CREDITS),
            *([COVER_CREDIT] if cover else []),
        ],
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
