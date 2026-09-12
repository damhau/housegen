"""The models a provider offers, for the settings sheet (newest first).

Each provider's `/models` is asked once per `MODELS_CACHE_S` and the answer cached in the process;
a failure (no key, network) is returned as `error` with an empty list, never raised: the sheet keeps
its free-text model field either way.
"""

from __future__ import annotations

import logging
import time

from pydantic import BaseModel

from housegen.llm.factory import get_provider
from housegen.llm.types import ModelInfo

logger = logging.getLogger(__name__)

MODELS_CACHE_S = 600.0

_cache: dict[str, tuple[float, ModelsOut]] = {}


class ModelsOut(BaseModel):
    provider: str
    # newest first
    models: list[ModelInfo]
    # why the list is empty, when the provider could not be asked
    error: str | None = None


async def list_models(provider_name: str) -> ModelsOut:
    cached = _cache.get(provider_name)
    now = time.monotonic()
    if cached and now - cached[0] < MODELS_CACHE_S:
        return cached[1]
    try:
        models = await get_provider(provider_name).list_models()
        out = ModelsOut(provider=provider_name, models=models)
    except Exception as e:
        logger.warning(
            "llm.models.failed", extra={"provider": provider_name, "error": str(e)[:300]}
        )
        out = ModelsOut(provider=provider_name, models=[], error=str(e)[:300])
    if out.error is None:
        _cache[provider_name] = (now, out)
    logger.info("llm.models.listed", extra={"provider": provider_name, "count": len(out.models)})
    return out


def clear_cache() -> None:
    _cache.clear()
