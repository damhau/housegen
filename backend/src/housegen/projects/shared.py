"""What a share link (#24) shows, signed out, through paths of its own (#105).

    /shared/<token>/scene/index.html   the shared version's scene page (?kit= as on /scenes)
    /shared/<token>/scene/src/…        its sources, which the page loads by relative URL
    /shared/<token>/scene/renders/…    its pictures, and scene/listing/… its listing photos
    /shared/<token>/context/…          the project's surroundings
    /shared/<token>/photos/<file>      the project's photos

The project's own path, /scenes/<project>/…, needs a session of its organisation.
"""

from __future__ import annotations

import posixpath
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.responses import Response

from housegen.core.db import DbSession
from housegen.core.exceptions import NotFoundError
from housegen.geo import context as geo_context
from housegen.listing.router import listing_out
from housegen.projects import crud
from housegen.projects.files import RevalidatedStaticFiles
from housegen.projects.models import Project
from housegen.projects.schemas import SharedProjectOut
from housegen.projects.storage import ProjectStorage
from housegen.render.kits import serve_page, viewer_kit

router = APIRouter(include_in_schema=False)

_SCENE_FOLDERS = ("src/", "renders/")
_PICTURES = (".jpg", ".jpeg", ".png", ".webp")


def shared_version(project: Project) -> tuple[int, bool]:
    """The version a share link shows (0: the working copy, before the first version) and
    whether the owner pinned it."""
    pinned = project.share_version is not None and any(
        v.number == project.share_version for v in project.versions
    )
    version = project.share_version if pinned and project.share_version else project.current_version
    return version, pinned


def shared_out(project: Project) -> SharedProjectOut:
    folder = f"/shared/{project.share_token}"
    scene = f"{folder}/scene"
    st = ProjectStorage(project.id)
    version, pinned = shared_version(project)
    ctx = geo_context.load(st.root)
    aligned = bool(ctx and ctx.get("alignment", {}).get("set"))
    return SharedProjectOut(
        name=project.name,
        version=version,
        pinned=pinned,
        scene_url=f"{scene}/index.html",
        render_urls=st.render_urls(version, scene) if version else {},
        photo_urls=[f"{folder}/photos/{p.filename}" for p in project.photos],
        created_at=project.created_at,
        context_url=f"{folder}/context/" if aligned else None,
        listing=listing_out(st, version, project.current_version, scene) if version else None,
        kit=viewer_kit(),
    )


def _not_found() -> HTTPException:
    return HTTPException(404, "not found")


@router.api_route("/shared/{token}/{path:path}", methods=["GET", "HEAD"])
async def shared_file(
    request: Request,
    session: DbSession,
    token: str,
    path: str,
    kit: Annotated[str | None, Query()] = None,
) -> Response:
    try:
        project = await crud.project_by_share_token(session, token)
    except NotFoundError:
        raise _not_found() from None
    st = ProjectStorage(project.id)
    area, _, rest = path.partition("/")
    rest = posixpath.normpath(rest) if rest else ""
    if rest.startswith(("/", "..")):
        raise _not_found()
    if area == "scene":
        version, _ = shared_version(project)
        folder = st.versions_dir / str(version) if version else st.scene_dir
        if rest == "index.html":
            return serve_page(folder / "index.html", kit, project.id)
        listing_photo = rest.startswith("listing/") and rest.lower().endswith(_PICTURES)
        if not (rest.startswith(_SCENE_FOLDERS) or listing_photo):
            raise _not_found()
    elif area == "context":
        folder = geo_context.context_dir(st.root)
    elif area == "photos" and rest in {p.filename for p in project.photos}:
        folder = st.photos_dir
    else:
        raise _not_found()
    files = RevalidatedStaticFiles(directory=folder, check_dir=False)
    return await files.get_response(rest, request.scope)
