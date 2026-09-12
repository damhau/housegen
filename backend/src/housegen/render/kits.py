"""Renderer versions: snapshots of the kit (house.js + runtime.js) a scene can be drawn with.

`kit/versions/<name>/` holds one snapshot and `kit/versions/index.json` lists them and names the
one **pinned** for the build path: what the builder renders with and reads as `kit/*.js`, what
the critic compares, what the version pictures are drawn with. `dev` is the working copy in
`kit/`. The viewer shows the newest snapshot by default and lets the owner pick any, side by
side, so a change to how a house is drawn is judged on a saved scene before it is snapshotted
and, later, pinned (after a measured run, see docs/quality-plan-2026-09-12.md).

A scene page is served with `?kit=<name>`: the import map of its `index.html` is rewritten on
the way out to that snapshot's files; the scene sources are untouched, so every saved version
can be drawn with every renderer.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from housegen.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

DEV = "dev"
_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class KitInfo(BaseModel):
    name: str
    date: str
    note: str
    pinned: bool  # the build path draws with it
    dev: bool = False  # the working copy in kit/, not a snapshot


class KitsOut(BaseModel):
    kits: list[KitInfo]  # newest snapshot first, the working copy last
    pinned: str
    latest: str  # what the viewer shows by default: the newest snapshot (dev when none)


def _index(settings: Settings) -> dict[str, object]:
    p = settings.KIT_DIR / "versions" / "index.json"
    if not p.exists():
        return {"pinned": DEV, "versions": []}
    return json.loads(p.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _snapshots(settings: Settings) -> list[dict[str, str]]:
    versions = _index(settings).get("versions", [])
    out: list[dict[str, str]] = []
    for v in versions if isinstance(versions, list) else []:
        name = str(v.get("name", ""))
        if _NAME.match(name) and (settings.KIT_DIR / "versions" / name / "house.js").exists():
            out.append(
                {"name": name, "date": str(v.get("date", "")), "note": str(v.get("note", ""))}
            )
    out.sort(key=lambda v: (v["date"], v["name"]), reverse=True)
    return out


def pinned_kit(settings: Settings | None = None) -> str:
    """The renderer of the build path: RENDER_KIT, else the index's `pinned`, else the working
    copy (with a warning: the build path is then not frozen)."""
    settings = settings or get_settings()
    name = settings.RENDER_KIT or str(_index(settings).get("pinned") or DEV)
    if name != DEV and not (settings.KIT_DIR / "versions" / name / "house.js").exists():
        logger.warning("kit.pinned_missing", extra={"kit": name})
        return DEV
    return name


def kit_dir(name: str, settings: Settings | None = None) -> Path:
    """The directory holding house.js and runtime.js of a renderer; KeyError when unknown."""
    settings = settings or get_settings()
    if name == DEV:
        return settings.KIT_DIR
    d = settings.KIT_DIR / "versions" / name
    if not _NAME.match(name) or not (d / "house.js").exists():
        raise KeyError(name)
    return d


def list_kits(settings: Settings | None = None) -> KitsOut:
    settings = settings or get_settings()
    pinned = pinned_kit(settings)
    snaps = _snapshots(settings)
    kits = [KitInfo(**v, pinned=v["name"] == pinned) for v in snaps]
    kits.append(
        KitInfo(name=DEV, date="", note="the working copy in kit/", pinned=pinned == DEV, dev=True)
    )
    return KitsOut(kits=kits, pinned=pinned, latest=snaps[0]["name"] if snaps else DEV)


def dev_kit_tag(settings: Settings | None = None) -> str:
    """A tag that changes whenever the working copy changes (its files' modification time), so
    a page served with ?kit=dev names URLs a browser has never cached."""
    settings = settings or get_settings()
    stamps = [
        int((settings.KIT_DIR / f).stat().st_mtime)
        for f in ("house.js", "runtime.js")
        if (settings.KIT_DIR / f).exists()
    ]
    return str(max(stamps)) if stamps else "0"


def rewrite_page(html: str, name: str, settings: Settings | None = None) -> str:
    """The scene page with its import map pointing at the renderer's kit files: a snapshot's
    directory, or the working copy tagged with its modification time."""
    if name == DEV:
        tag = dev_kit_tag(settings)
        return html.replace('"/kit/house.js"', f'"/kit/house.js?v={tag}"').replace(
            '"/kit/runtime.js"', f'"/kit/runtime.js?v={tag}"'
        )
    return html.replace('"/kit/house.js"', f'"/kit/versions/{name}/house.js"').replace(
        '"/kit/runtime.js"', f'"/kit/versions/{name}/runtime.js"'
    )


# --------------------------------------------------------------------------
# scene pages: /scenes/<project>/scene/index.html and /scenes/<project>/versions/<n>/index.html
# with ?kit=<name>; everything else under /scenes stays with the static mount
# --------------------------------------------------------------------------

pages_router = APIRouter(include_in_schema=False)
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _serve(page: Path, kit: str | None) -> HTMLResponse:
    if not page.is_file():
        raise HTTPException(404, "no such scene")
    html = page.read_text(encoding="utf-8")
    if kit:
        try:
            kit_dir(kit)
        except KeyError:
            raise HTTPException(404, f"unknown renderer '{kit}'") from None
        html = rewrite_page(html, kit)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@pages_router.get("/scenes/{project_id}/scene/index.html")
def scene_page(project_id: str, kit: str | None = Query(None)) -> HTMLResponse:
    if not _ID.match(project_id):
        raise HTTPException(404, "no such scene")
    return _serve(get_settings().projects_dir / project_id / "scene" / "index.html", kit)


@pages_router.get("/scenes/{project_id}/versions/{number}/index.html")
def version_page(project_id: str, number: int, kit: str | None = Query(None)) -> HTMLResponse:
    if not _ID.match(project_id) or number < 0:
        raise HTTPException(404, "no such scene")
    return _serve(
        get_settings().projects_dir / project_id / "versions" / str(number) / "index.html", kit
    )
