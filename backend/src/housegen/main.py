import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from housegen.accounts.dependencies import agent_guard
from housegen.accounts.migrations import migrate_organisations
from housegen.accounts.models import Organisation
from housegen.accounts.oidc import build_oauth, configured_providers
from housegen.accounts.router import auth_router
from housegen.agent.pipeline import resume_interrupted_jobs, start_all_waiting, start_waiting
from housegen.api.v1 import router as api_router
from housegen.core.config import Settings, get_settings
from housegen.core.db import dispose_db, init_db, session_factory
from housegen.core.exceptions import register_exception_handlers
from housegen.core.logging import RequestLoggingMiddleware, configure_logging
from housegen.jobs.manager import job_manager
from housegen.projects.access import scene_page_access
from housegen.projects.files import RevalidatedStaticFiles, SceneFiles
from housegen.projects.migrations import migrate_plans
from housegen.projects.shared import router as shared_router
from housegen.render import renderer
from housegen.render.kits import pages_router

logger = logging.getLogger(__name__)


class SignInMissing(RuntimeError):
    pass


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL, settings.ENV)
    _log_auth(settings)
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
    await _check_agent_organisation(settings)
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
    """Fail closed (#105): without a sign-in provider nobody could sign in, and an app that
    answered without a session instead would be open to anyone."""
    providers = configured_providers(settings)
    logger.info(
        "auth.config",
        extra={
            "disabled": settings.AUTH_DISABLED,
            "providers": providers,
            "admin_email_set": bool(settings.AUTH_ADMIN_EMAIL),
            "public_url": settings.AUTH_PUBLIC_URL or None,
            "readonly_token_set": bool(settings.AUTH_READONLY_TOKEN),
            "agent_token_set": bool(settings.agent_token),
            "agent_organisation": settings.agent_organisation or None,
            "service_token_set": bool(settings.RENDER_SERVICE_TOKEN),
            "resend_api_key_set": bool(settings.RESEND_API_KEY.strip()),
            "email_from_set": bool(settings.EMAIL_FROM.strip()),
            "invitation_email": settings.email_enabled,
        },
    )
    if bool(settings.RESEND_API_KEY.strip()) != bool(settings.EMAIL_FROM.strip()):
        logger.warning(
            "email.half_configured",
            extra={
                "missing": "EMAIL_FROM" if settings.RESEND_API_KEY.strip() else "RESEND_API_KEY"
            },
        )
    if bool(settings.AUTH_AGENT_TOKEN.strip()) != bool(settings.agent_organisation):
        logger.warning(
            "auth.agent.half_configured",
            extra={
                "missing": "AUTH_AGENT_ORGANISATION"
                if settings.AUTH_AGENT_TOKEN.strip()
                else "AUTH_AGENT_TOKEN"
            },
        )
    if not providers and not settings.AUTH_DISABLED:
        logger.error("auth.no_provider")
        raise SignInMissing(
            "no sign-in provider is configured (AUTH_GOOGLE_CLIENT_ID, AUTH_MICROSOFT_CLIENT_ID): "
            "set one, or AUTH_DISABLED=1 to run without sign-in (local dev only)"
        )
    if providers and not settings.AUTH_SESSION_SECRET:
        logger.warning("auth.session_secret_missing")


async def _check_agent_organisation(settings: Settings) -> None:
    """A mistyped AUTH_AGENT_ORGANISATION shows at startup, not at the agent's first call."""
    if not settings.agent_token:
        return
    async with session_factory()() as session:
        if await session.get(Organisation, settings.agent_organisation) is None:
            logger.error(
                "auth.agent.unknown_organisation",
                extra={"organisation_id": settings.agent_organisation},
            )


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
        # on every route, the routers' own guards after it: the agent token's writes (#112)
        dependencies=[Depends(agent_guard)],
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
    app.include_router(pages_router, dependencies=[Depends(scene_page_access)])
    # a share link's own paths onto the shared version's files, readable signed out
    app.include_router(shared_router)

    settings.projects_dir.mkdir(parents=True, exist_ok=True)
    # scene runtime + component kit + vendored three.js
    app.mount(
        "/kit/vendor/three",
        RevalidatedStaticFiles(directory=settings.KIT_DIR / "node_modules" / "three"),
        name="three",
    )
    app.mount("/kit", RevalidatedStaticFiles(directory=settings.KIT_DIR), name="kit")
    # per-project files: scene working copy, versions, renders, photos, plan pages (by organisation)
    app.mount("/scenes", SceneFiles(directory=settings.projects_dir), name="scenes")
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
