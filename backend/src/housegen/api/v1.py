"""/api/v1. Every route needs a session working in an organisation (or the read-only token on a GET),
except /health, /me, an invitation link's preview and a share link's view (#105). The account routes
ask for a signed-in user themselves: the first-login wizard runs before any organisation."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from housegen.accounts.dependencies import api_caller
from housegen.accounts.router import router as accounts_router
from housegen.agent.run_settings import Provider, ResolvedRunSettings, resolve
from housegen.core.config import get_settings
from housegen.core.db import DbSession
from housegen.geo.router import router as geo_router
from housegen.listing.router import router as listing_router
from housegen.llm.models import ModelsOut, list_models
from housegen.projects import crud
from housegen.projects.access import project_in_scope
from housegen.projects.router import router as projects_router
from housegen.projects.schemas import SharedProjectOut
from housegen.projects.shared import shared_out
from housegen.render.kits import KitsOut, list_kits
from housegen.report.router import router as report_router

# a route added to one of these routers, or here, is protected without a word: a public one has to
# go on `router` on purpose
protected = APIRouter(dependencies=[Depends(api_caller), Depends(project_in_scope)])
protected.include_router(projects_router)
protected.include_router(geo_router)
protected.include_router(report_router)
protected.include_router(listing_router)

router = APIRouter(prefix="/api/v1")
router.include_router(accounts_router)


@router.get("/shared/{token}", tags=["shared"])
async def shared_project(session: DbSession, token: str) -> SharedProjectOut:
    """The read-only view behind a share link (#24): the scene (current or pinned version),
    its renders and photos, all under the link's own paths (/shared/<token>/…, readable signed
    out). Nothing that could change the project, nothing the owner wrote."""
    return shared_out(await crud.project_by_share_token(session, token))


class HealthOut(BaseModel):
    status: str
    # the running build: image tag ("1.2.3" or "sha-abc1234"), "dev" outside the image
    version: str
    commit: str
    env: str


@router.get("/health", tags=["meta"])
def health() -> HealthOut:
    s = get_settings()
    return HealthOut(status="ok", version=s.APP_VERSION, commit=s.APP_COMMIT, env=s.ENV)


@protected.get("/kits", tags=["meta"])
def kits() -> KitsOut:
    """The renderer snapshots a scene can be drawn with (newest first, the working copy last),
    which one the build path is pinned to and which one the viewer shows by default."""
    return list_kits()


@protected.get("/settings/defaults", tags=["meta"])
def run_defaults() -> ResolvedRunSettings:
    """What a project without run settings of its own runs with (the server's .env defaults),
    for the settings sheet of a project not created yet."""
    return resolve(get_settings())


@protected.get("/models", tags=["meta"])
async def models(provider: Annotated[Provider | None, Query()] = None) -> ModelsOut:
    """The models the provider's API offers now, newest first (the .env provider when none is
    given). Cached for ten minutes; when the provider cannot be asked the list is empty and
    `error` says why."""
    return await list_models(provider or get_settings().LLM_PROVIDER)


router.include_router(protected)
