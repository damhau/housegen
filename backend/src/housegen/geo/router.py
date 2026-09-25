"""The surroundings of a project (#39): search a place, fetch its surroundings, align the scene on them."""

from __future__ import annotations

import logging
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from housegen.core.db import DbSession
from housegen.core.exceptions import InvalidInputError, NotFoundError
from housegen.geo import context, swiss
from housegen.projects import crud
from housegen.projects.storage import ProjectStorage

logger = logging.getLogger(__name__)
router = APIRouter(tags=["surroundings"])


class PlaceOut(BaseModel):
    label: str
    kind: str = Field(description='"parcel", "address", or a commune / postcode')
    e: float = Field(description="LV95 easting (EPSG:2056)")
    n: float = Field(description="LV95 northing")


class AlignmentIn(BaseModel):
    x: float = Field(description="where the scene's origin lies, metres east of the searched point")
    z: float = Field(description="… metres south of it")
    rotation: float = Field(
        ge=-360, le=360, description="degrees the scene is turned, clockwise seen from above"
    )
    ground: float = Field(description="altitude (m) of the scene's y = 0")


class AlignmentOut(AlignmentIn):
    set: bool = Field(description="false until the owner aligned the scene")


class SurroundingsOut(BaseModel):
    exists: bool
    suggestion: str | None = Field(
        None, description="what the plans' title block names (parcel and commune)"
    )
    place: PlaceOut | None = None
    radius: int | None = None
    url: str | None = Field(
        None, description="the folder the scene page loads them from (?context=)"
    )
    photo_url: str | None = None
    buildings: int | None = None
    alignment: AlignmentOut | None = None
    credits: list[str] = []
    fetched_at: str | None = None


def _out(st: ProjectStorage, ctx: dict | None, suggestion: str | None = None) -> SurroundingsOut:  # type: ignore[type-arg]
    if not ctx:
        return SurroundingsOut(exists=False, suggestion=suggestion)
    base = f"/scenes/{st.project_id}/context/"
    return SurroundingsOut(
        exists=True,
        suggestion=suggestion,
        place=PlaceOut(**ctx["place"]),
        radius=ctx["radius"],
        url=base,
        photo_url=base + ctx["photo"]["file"] + f"?t={ctx['fetched_at']}",
        buildings=ctx["buildings"]["count"],
        alignment=AlignmentOut(**ctx["alignment"]),
        credits=ctx.get("credits", []),
        fetched_at=ctx.get("fetched_at"),
    )


@router.get("/geo/search")
async def search_places(q: Annotated[str, Query(min_length=2, max_length=200)]) -> list[PlaceOut]:
    """Swiss addresses and parcels matching the text (geo.admin.ch), in LV95."""
    async with httpx.AsyncClient(headers=swiss.UA, timeout=20) as client:
        try:
            places = await swiss.search(q, client)
        except httpx.HTTPError as e:
            raise InvalidInputError(f"the Swiss geodata search did not answer: {e}") from e
    return [PlaceOut(**p.__dict__) for p in places]


@router.get("/projects/{project_id}/surroundings")
async def get_surroundings(session: DbSession, project_id: str) -> SurroundingsOut:
    await crud.get_project(session, project_id)
    st = ProjectStorage(project_id)
    ctx = context.load(st.root)
    suggestion = (
        None
        if ctx
        else context.suggestion_from_plans([st.plan_pdf(d) for d in st.plan_documents()])
    )
    return _out(st, ctx, suggestion)


class FetchIn(BaseModel):
    place: PlaceOut
    radius: Literal[100, 200, 300] = 200


@router.post("/projects/{project_id}/surroundings")
async def fetch_surroundings(session: DbSession, project_id: str, body: FetchIn) -> SurroundingsOut:
    """Fetch the terrain, the aerial photo and the buildings around the place (10 to 30 s). Replaces
    earlier surroundings; the viewer shows them in the Final and Ultra looks, never the builder."""
    await crud.get_project(session, project_id)
    st = ProjectStorage(project_id)
    old = context.load(st.root)
    try:
        ctx = await context.build(
            st.root, swiss.Place(**body.place.model_dump()), radius=body.radius
        )
    except (httpx.HTTPError, RuntimeError) as e:
        logger.warning("geo.fetch_failed", extra={"project_id": project_id, "error": str(e)})
        raise InvalidInputError(f"the surroundings could not be fetched: {e}") from e
    # the same place fetched again keeps the alignment the owner set
    if (
        old
        and old.get("alignment", {}).get("set")
        and old["place"]["e"] == ctx["place"]["e"]
        and old["place"]["n"] == ctx["place"]["n"]
    ):
        ctx["alignment"] = old["alignment"]
        context.save(st.root, ctx)
    logger.info(
        "geo.fetched", extra={"project_id": project_id, "buildings": ctx["buildings"]["count"]}
    )
    return _out(st, ctx)


@router.patch("/projects/{project_id}/surroundings")
async def align_surroundings(
    session: DbSession, project_id: str, body: AlignmentIn
) -> SurroundingsOut:
    """Where the scene sits in its surroundings (the alignment editor)."""
    await crud.get_project(session, project_id)
    st = ProjectStorage(project_id)
    ctx = context.load(st.root)
    if not ctx:
        raise NotFoundError("no surroundings yet: fetch them first")
    ctx["alignment"] = {**body.model_dump(), "set": True}
    context.save(st.root, ctx)
    return _out(st, ctx)


@router.delete("/projects/{project_id}/surroundings", status_code=204)
async def delete_surroundings(session: DbSession, project_id: str) -> None:
    await crud.get_project(session, project_id)
    context.remove(ProjectStorage(project_id).root)
