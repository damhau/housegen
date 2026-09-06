import logging
import sys
import time
import uuid
from contextvars import ContextVar
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

try:
    from pythonjsonlogger.json import JsonFormatter
except ImportError:  # pragma: no cover
    JsonFormatter = None  # type: ignore[assignment,misc]

request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get() or "-"
        return True


_STANDARD_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()
    | {"message", "asctime", "request_id", "taskName"}
)


class _DevFormatter(logging.Formatter):
    """Readable line + every `extra={...}` field appended as key=value."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS}
        if not extras:
            return base
        return base + "  " + " ".join(f"{k}={v}" for k, v in extras.items())


def configure_logging(level: str = "INFO", env: str = "dev") -> None:
    """Idempotent — safe to call from lifespan."""
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.addFilter(_RequestIdFilter())
    if env == "prod" and JsonFormatter is not None:
        fmt: logging.Formatter = JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(request_id)s %(message)s",
            rename_fields={"asctime": "ts", "levelname": "level"},
        )
    else:
        fmt = _DevFormatter("%(asctime)s %(levelname)-7s %(name)s [%(request_id)s] %(message)s")
    handler.setFormatter(fmt)
    root.addHandler(handler)
    for noisy in ("uvicorn.access", "httpx", "httpcore", "openai", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Any) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_ctx.set(rid)
        logger = logging.getLogger("housegen.request")
        started = time.perf_counter()
        try:
            response: Response = await call_next(request)
        except Exception:
            logger.exception(
                "request.failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                },
            )
            raise
        finally:
            request_id_ctx.reset(token)
        if not request.url.path.startswith(("/kit/", "/scenes/")):
            logger.info(
                "request.completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                },
            )
        response.headers["x-request-id"] = rid
        return response
