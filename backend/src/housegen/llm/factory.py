from functools import lru_cache

from housegen.core.config import get_settings
from housegen.llm.base import Provider


@lru_cache
def get_provider(name: str | None = None) -> Provider:
    """The provider client for `name` (the .env one when None); one instance per provider."""
    s = get_settings()
    if (name or s.LLM_PROVIDER) == "openai":
        from housegen.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(
            api_key=s.OPENAI_API_KEY,
            base_url=s.OPENAI_BASE_URL,
            timeout=s.LLM_TIMEOUT_S,
        )
    from housegen.llm.anthropic_provider import AnthropicProvider

    return AnthropicProvider(api_key=s.ANTHROPIC_API_KEY, timeout=s.LLM_TIMEOUT_S)
