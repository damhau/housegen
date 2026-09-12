from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from housegen.agent.run_settings import Provider
from housegen.core.config import get_settings
from housegen.core.db import DbSession
from housegen.llm.models import ModelsOut, list_models
from housegen.projects import crud
from housegen.projects.router import router as projects_router
from housegen.projects.schemas import SharedProjectOut
from housegen.projects.storage import ProjectStorage
from housegen.render.kits import KitsOut, list_kits

router = APIRouter(prefix="/api/v1")
router.include_router(projects_router)


@router.get("/shared/{token}", tags=["shared"])
async def shared_project(session: DbSession, token: str) -> SharedProjectOut:
    """The read-only view behind a share link (#24): the scene (current or pinned version),
    its renders and photos. Nothing that could change the project, nothing the owner wrote."""
    project = await crud.project_by_share_token(session, token)
    st = ProjectStorage(project.id)
    pinned = project.share_version is not None and any(
        v.number == project.share_version for v in project.versions
    )
    version = project.share_version if pinned else project.current_version
    return SharedProjectOut(
        name=project.name,
        version=version,
        pinned=pinned,
        scene_url=st.scene_url(version) if version else st.scene_url(),
        render_urls=st.render_urls(version) if version else {},
        photo_urls=[st.photo_url(p.filename) for p in project.photos],
        created_at=project.created_at,
    )


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


@router.get("/kits", tags=["meta"])
def kits() -> KitsOut:
    """The renderer snapshots a scene can be drawn with (newest first, the working copy last),
    which one the build path is pinned to and which one the viewer shows by default."""
    return list_kits()


@router.get("/models", tags=["meta"])
async def models(provider: Annotated[Provider | None, Query()] = None) -> ModelsOut:
    """The models the provider's API offers now, newest first (the .env provider when none is
    given). Cached for ten minutes; when the provider cannot be asked the list is empty and
    `error` says why."""
    return await list_models(provider or get_settings().LLM_PROVIDER)
