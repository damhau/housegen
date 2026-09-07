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
    # the critic judges (high is plenty). OpenAI: none…xhigh; Anthropic: low…max.
    BUILDER_EFFORT: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] = "xhigh"
    CRITIC_EFFORT: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] = "high"
    LLM_TIMEOUT_S: float = 600.0

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
    RENDER_TIMEOUT_MS: int = 30000

    @property
    def database_url(self) -> str:
        return self.DATABASE_URL or f"sqlite+aiosqlite:///{self.DATA_DIR / 'housegen.db'}"

    @property
    def projects_dir(self) -> Path:
        return self.DATA_DIR / "projects"

    @property
    def render_base_url(self) -> str:
        return self.RENDER_BASE_URL or f"http://{self.HOST}:{self.PORT}"

    def resolve_model(self, role: Literal["builder", "critic"]) -> str:
        explicit = {"builder": self.BUILDER_MODEL, "critic": self.CRITIC_MODEL}[role]
        if explicit:
            return explicit
        return "claude-opus-5" if self.LLM_PROVIDER == "anthropic" else "gpt-6-astra"


@lru_cache
def get_settings() -> Settings:
    return Settings()
