from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_REPO_ROOT = _BACKEND_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ENV: Literal["dev", "prod"] = "dev"
    # What is running: the image tag the workflow built ("1.2.3" on a v* tag, "sha-abc1234"
    # from main) and the full commit, baked into the image as env vars; "dev" outside it.
    APP_VERSION: str = "dev"
    APP_COMMIT: str = ""
    LOG_LEVEL: str = "INFO"
    HOST: str = "127.0.0.1"
    PORT: int = 8000

    # storage
    DATA_DIR: Path = _BACKEND_ROOT / "data"
    DATABASE_URL: str = ""  # defaults to sqlite file under DATA_DIR
    KIT_DIR: Path = _REPO_ROOT / "kit"
    # built frontend (Vite dist/) served from "/" by this process; unset in dev (Vite dev server)
    STATIC_DIR: Path | None = None

    # LLM
    LLM_PROVIDER: Literal["anthropic", "openai"] = "anthropic"
    ANTHROPIC_API_KEY: str | None = None
    OPENAI_API_KEY: str | None = None
    OPENAI_BASE_URL: str | None = None  # any OpenAI-compatible endpoint
    # Model per role. Defaults depend on provider (see resolve_model).
    BUILDER_MODEL: str | None = None
    CRITIC_MODEL: str | None = None
    # Per-turn output cap. Reasoning tokens count against it on the OpenAI Responses API,
    # so keep it generous; streaming means there is no timeout reason to keep it small.
    LLM_MAX_TOKENS: int = 96000
    # Reasoning effort per role. The builder designs (xhigh, like the reference Astra run);
    # the critic compares pictures by eye (medium: at high it paced itself against the output
    # cap and tried to measure renders). OpenAI: none…xhigh; Anthropic: low…max.
    BUILDER_EFFORT: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] = "xhigh"
    CRITIC_EFFORT: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] = "medium"
    LLM_TIMEOUT_S: float = 600.0
    # USD per million tokens, by model: input (uncached), cached input (cache read), output;
    # optional cache_write. Seeded from public list prices (#13); override in .env with a JSON
    # object, e.g. MODEL_PRICES='{"gpt-6-astra": {"input": 2, "cached": 0.2, "output": 12}}'.
    # Models not listed cost 0 in the estimates (the summary then says "no price").
    MODEL_PRICES: dict[str, dict[str, float]] = Field(
        default_factory=lambda: {
            # Anthropic list price (2026-06): $5 / $25 per MTok; cache reads 0.1x, writes 1.25x
            "claude-opus-5": {"input": 5.0, "cached": 0.5, "output": 25.0, "cache_write": 6.25},
            # OpenAI list price (developers.openai.com/api/docs/pricing, 2026-09-07):
            # $10 / $1 cached / $50 per MTok
            "gpt-6-astra": {"input": 10.0, "cached": 1.0, "output": 50.0},
        }
    )

    # agent loop
    BUILDER_MAX_STEPS: int = 60
    # independent critic rounds after the builder is done (0 disables the critic)
    CRITIC_MAX_ITERATIONS: int = 2
    CRITIC_SCORE_THRESHOLD: int = 80
    RENDER_IMAGE_WIDTH: int = 1024
    RENDER_JPEG_QUALITY: int = 82
    PLAN_MAX_PAGES: int = 6
    PLAN_DPI: int = 150  # scanned sheets at 1:100 need this for dimension strings to stay legible

    # renderer
    RENDER_BASE_URL: str = ""  # defaults to http://HOST:PORT
    BROWSER_CHANNEL: str | None = Field(
        default="chrome", description="'chrome', 'msedge' or None for bundled chromium"
    )
    RENDER_WIDTH: int = 1280
    RENDER_HEIGHT: int = 800
    RENDER_TIMEOUT_MS: int = 30000  # page load
    # the scene's first frame: buildScene plus the shadow map, drawn in software on the server.
    # A planted scene at quality=high takes well over 30 s; a version render that runs out of
    # this budget is the "scene did not become ready" failure
    RENDER_READY_TIMEOUT_MS: int = 180000
    # ANGLE backend for WebGL. swiftshader = software (any machine, slow); gl-egl = the NVIDIA
    # driver through EGL, vulkan = its Vulkan ICD (a GPU with NVIDIA_DRIVER_CAPABILITIES=all).
    # The renderer logs what it got at browser start (render.browser.started gl=…).
    RENDER_ANGLE: Literal["swiftshader", "gl-egl", "gl", "vulkan"] = "swiftshader"

    # remote render service (housegen.render.service on a GPU box, e.g. deploy/modal_render.py).
    # Empty = render in this process. The service loads the scene from RENDER_BASE_URL, which
    # must then be reachable from there (the public URL of this app).
    RENDER_SERVICE_URL: str = ""
    RENDER_SERVICE_TOKEN: str = ""  # bearer token, the same value on both sides
    RENDER_SERVICE_TIMEOUT_S: float = 400.0  # one render call: page load + views, plus a cold start
    # when the service cannot be reached (down, cold-start timeout): render locally instead of
    # failing the job. Off = the call raises RenderError.
    RENDER_SERVICE_FALLBACK: bool = True

    @property
    def database_url(self) -> str:
        return self.DATABASE_URL or f"sqlite+aiosqlite:///{self.DATA_DIR / 'housegen.db'}"

    @property
    def projects_dir(self) -> Path:
        return self.DATA_DIR / "projects"

    @property
    def render_base_url(self) -> str:
        return self.RENDER_BASE_URL or f"http://{self.HOST}:{self.PORT}"

    def price_for(self, model: str) -> dict[str, float] | None:
        """The price row of a model, matched by exact name then by prefix ("gpt-6-astra-2026…")."""
        if model in self.MODEL_PRICES:
            return self.MODEL_PRICES[model]
        for name, row in self.MODEL_PRICES.items():
            if model.startswith(name):
                return row
        return None

    def resolve_model(self, role: Literal["builder", "critic"]) -> str:
        explicit = {"builder": self.BUILDER_MODEL, "critic": self.CRITIC_MODEL}[role]
        if explicit:
            return explicit
        return "claude-opus-5" if self.LLM_PROVIDER == "anthropic" else "gpt-6-astra"


@lru_cache
def get_settings() -> Settings:
    return Settings()
