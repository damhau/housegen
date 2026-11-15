import logging
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import Response
from starlette.staticfiles import PathLike
from starlette.types import Scope

from housegen.accounts.migrations import migrate_organisations
from housegen.accounts.oidc import build_oauth, configured_providers
from housegen.accounts.router import auth_router
from housegen.agent.pipeline import resume_interrupted_jobs, start_all_waiting, start_waiting
from housegen.api.v1 import router as api_router
from housegen.core.config import Settings, get_settings
from housegen.core.db import dispose_db, init_db
from housegen.core.exceptions import register_exception_handlers
from housegen.core.logging import RequestLoggingMiddleware, configure_logging
from housegen.jobs.manager import job_manager
from housegen.projects.migrations import migrate_plans
from housegen.render import renderer
from housegen.render.kits import pages_router

logger = logging.getLogger(__name__)


class RevalidatedStaticFiles(StaticFiles):
    """Static files the browser must revalidate on every load (Cache-Control: no-cache, answered
    by a 304 when unchanged). The kit's working copy and a project's scene sources change under
    the same URL, and a browser's heuristic caching would keep showing the old ones: a renderer
    switch in the viewer or a build in progress then looks like nothing happened."""

    def file_response(
        self,
        full_path: PathLike,
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = "no-cache"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL, settings.ENV)
    settings.projects_dir.mkdir(parents=True, exist_ok=True)
    await init_db()
    logger.info(
        "app.startup",
        extra={
            "env": settings.ENV,
            "version": settings.APP_VERSION,
            "commit": settings.APP_COMMIT,
            "provider": settings.LLM_PROVIDER,
            "data_dir": str(settings.DATA_DIR),
        },
    )
    await migrate_plans(settings.projects_dir)  # one-off: plan.pdf + plan/ → plans/1/ (#10)
    await migrate_organisations()  # one-off: the projects from before accounts (#104)
    _log_auth(settings)
    # jobs the previous process left behind (deploy, reload, crash) continue with the same id
    resumed = await resume_interrupted_jobs(job_manager)
    if resumed:
        logger.info("app.resumed_jobs", extra={"count": resumed})
    # a change asked in the chat while a job ran starts when the project's jobs end (#35)
    job_manager.on_finished = lambda project_id: start_waiting(job_manager, project_id)
    await start_all_waiting(job_manager)
    yield
    await job_manager.shutdown()
    await renderer.close()
    await dispose_db()
    logger.info("app.shutdown")


def _log_auth(settings: Settings) -> None:
    providers = configured_providers(settings)
    logger.info(
        "auth.config",
        extra={
            "disabled": settings.AUTH_DISABLED,
            "providers": providers,
            "admin_email_set": bool(settings.AUTH_ADMIN_EMAIL),
            "public_url": settings.AUTH_PUBLIC_URL or None,
        },
    )
    if providers and not settings.AUTH_SESSION_SECRET:
        logger.warning("auth.session_secret_missing")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="housegen",
        # the API contract's version, deliberately not the build's (APP_VERSION, see
        # /health): it is written into every generated client file
        version="0.1.0",
        lifespan=lifespan,
        # short operationIds → clean generated hook names (useListProjects, …)
        generate_unique_id_function=lambda route: route.name,
    )
    app.add_middleware(RequestLoggingMiddleware)
    # the session cookie: who signed in, the organisation they work in, Authlib's login state
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.AUTH_SESSION_SECRET or secrets.token_hex(32),
        session_cookie="housegen_session",
        max_age=settings.AUTH_SESSION_DAYS * 24 * 3600,
        same_site="lax",
        https_only=True,
    )
    app.state.oauth = build_oauth(settings)
    register_exception_handlers(app)
    app.include_router(api_router)
    app.include_router(auth_router)
    # scene pages served with a chosen renderer snapshot (?kit=): before the /scenes mount
    app.include_router(pages_router)

    settings.projects_dir.mkdir(parents=True, exist_ok=True)
    # scene runtime + component kit + vendored three.js
    app.mount(
        "/kit/vendor/three",
        RevalidatedStaticFiles(directory=settings.KIT_DIR / "node_modules" / "three"),
        name="three",
    )
    app.mount("/kit", RevalidatedStaticFiles(directory=settings.KIT_DIR), name="kit")
    # per-project files: scene working copy, versions, renders, photos, plan pages
    app.mount("/scenes", RevalidatedStaticFiles(directory=settings.projects_dir), name="scenes")
    if settings.STATIC_DIR is not None:
        mount_spa(app, settings.STATIC_DIR)
    return app


def mount_spa(app: FastAPI, static_dir: Path) -> None:
    """Serve the built SPA from "/" (prod: one process for the API and the frontend).

    Must come last: the history-fallback route catches everything the API, the kit and
    the scene mounts did not.
    """
    static_dir = static_dir.resolve()
    index = static_dir / "index.html"
    if not index.is_file():
        logger.warning("spa.missing", extra={"static_dir": str(static_dir)})
        return
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="spa-assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        candidate = (static_dir / path).resolve()
        if path and candidate.is_file() and static_dir in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)


app = create_app()
