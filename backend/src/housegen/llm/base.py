from __future__ import annotations

from typing import Any, Protocol

from housegen.llm.types import Completion, Message, ModelInfo, ProgressCallback, ToolSpec


class Provider(Protocol):
    name: str

    async def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        response_schema: dict[str, Any] | None = None,
        max_tokens: int = 16000,
        on_progress: ProgressCallback | None = None,
        effort: str | None = None,
    ) -> Completion:
        """One model turn, streamed under the hood.

        `effort` is the reasoning effort (none…xhigh on OpenAI, low…max on Anthropic);
        each provider maps values it does not have onto its nearest level.

        When `response_schema` is given the assistant text is guaranteed JSON.
        `on_progress` receives phase changes, text deltas and token counts while the
        model is working; the returned Completion is always the full final message.
        """
        ...

    async def list_models(self) -> list[ModelInfo]:
        """The models this provider's API offers, newest first; the language models only."""
        ...
