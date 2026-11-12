from __future__ import annotations

import logging
import time
from typing import Any, cast

import anthropic

from housegen.core.exceptions import LLMError
from housegen.llm.types import (
    Completion,
    ImagePart,
    Message,
    ModelInfo,
    Part,
    ProgressCallback,
    ProgressEvent,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
    Usage,
)

logger = logging.getLogger(__name__)

PROVIDER = "anthropic"
# A builder turn at xhigh runs 2-12 minutes and the TTL counts from the request's start: the
# 5-minute default expired between turns and every turn paid the whole prompt again.
_CONVERSATION_CACHE: dict[str, Any] = {"type": "ephemeral", "ttl": "1h"}
_CALL_CACHE: dict[str, Any] = {"type": "ephemeral"}


def _image_block(p: ImagePart) -> dict[str, Any]:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": p.media_type, "data": p.data},
    }


def _content_blocks(parts: list[Part] | list[TextPart | ImagePart]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for p in parts:
        if isinstance(p, TextPart):
            blocks.append({"type": "text", "text": p.text})
        elif isinstance(p, ImagePart):
            if p.label:
                blocks.append({"type": "text", "text": p.label})
            blocks.append(_image_block(p))
        elif isinstance(p, ToolCallPart):
            blocks.append({"type": "tool_use", "id": p.id, "name": p.name, "input": p.input})
        elif isinstance(p, ToolResultPart):
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": p.tool_call_id,
                    "content": _content_blocks(p.content),
                    "is_error": p.is_error,
                }
            )
    return blocks


def _tool_defs(tools: list[ToolSpec], cache: dict[str, Any] = _CALL_CACHE) -> list[dict[str, Any]]:
    """The tools as Messages API tools (the same specs as OpenAI's), the last one marked for the cache
    (the stable tool definitions + the system prompt prefix)."""
    defs: list[dict[str, Any]] = [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in tools
    ]
    if defs:
        defs[-1]["cache_control"] = cache
    return defs


def _message_blocks(m: Message) -> list[dict[str, Any]]:
    if m.role == "assistant" and m.raw and m.raw_provider == PROVIDER:
        return m.raw
    return _content_blocks(m.content)


def _replay_blocks(content: list[Any]) -> list[dict[str, Any]]:
    """The response as request blocks, in its order, thinking blocks unchanged (signature
    included): the model keeps its reasoning from one tool call to the next."""
    blocks: list[dict[str, Any]] = []
    for b in content:
        if b.type == "thinking":
            blocks.append({"type": "thinking", "thinking": b.thinking, "signature": b.signature})
        elif b.type == "redacted_thinking":
            blocks.append({"type": "redacted_thinking", "data": b.data})
        elif b.type == "text":
            blocks.append({"type": "text", "text": b.text})
        elif b.type == "tool_use":
            blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
    return blocks


class AnthropicProvider:
    name = PROVIDER

    def __init__(self, api_key: str | None, timeout: float) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=3)

    async def list_models(self) -> list[ModelInfo]:
        # every model the endpoint lists is a chat model; the API returns them newest first
        models = [
            ModelInfo(id=m.id, display_name=m.display_name, created_at=m.created_at)
            async for m in self._client.models.list(limit=100)
        ]
        models.sort(key=lambda m: m.created_at.timestamp() if m.created_at else 0, reverse=True)
        return models

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
        cache_key: str | None = None,
    ) -> Completion:
        output_config: dict[str, Any] = {}
        if effort:
            # OpenAI's none/minimal have no Anthropic equivalent; low is the floor there.
            output_config["effort"] = "low" if effort in ("none", "minimal") else effort
        cache = _CONVERSATION_CACHE if cache_key else _CALL_CACHE
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": cache}],
            "messages": [{"role": m.role, "content": _message_blocks(m)} for m in messages],
            # adaptive thinking with a readable summary → shown live in the UI
            "thinking": {"type": "adaptive", "display": "summarized"},
        }
        if cache_key:
            # the breakpoint follows the conversation's last block: each turn reads the turns before
            kwargs["cache_control"] = cache
        if tools:
            kwargs["tools"] = _tool_defs(tools, cache)
        if response_schema:
            output_config["format"] = {"type": "json_schema", "schema": response_schema}
        if output_config:
            kwargs["output_config"] = output_config

        started = time.perf_counter()
        logger.info(
            "llm.anthropic.start",
            extra={"model": model, "messages": len(messages), "tools": len(tools or [])},
        )
        chars = 0
        first_output_at: float | None = None  # when the first non-thinking block started
        try:
            async with self._client.messages.stream(**kwargs) as stream:
                async for raw in stream:
                    ev: Any = raw
                    t = ev.type
                    if (
                        t == "content_block_start"
                        and ev.content_block.type != "thinking"
                        and first_output_at is None
                    ):
                        first_output_at = time.perf_counter()
                    if on_progress is None:
                        continue
                    if t == "content_block_start":
                        bt = ev.content_block.type
                        if bt == "thinking":
                            await on_progress(ProgressEvent(kind="phase", phase="thinking"))
                        elif bt == "tool_use":
                            await on_progress(
                                ProgressEvent(
                                    kind="phase", phase="tool_call", tool_name=ev.content_block.name
                                )
                            )
                        elif bt == "text":
                            await on_progress(ProgressEvent(kind="phase", phase="writing"))
                    elif t == "text":
                        chars += len(ev.text)
                        await on_progress(
                            ProgressEvent(kind="text", text=ev.text, output_chars=chars)
                        )
                    elif t == "input_json":
                        chars += len(ev.partial_json)
                        await on_progress(ProgressEvent(kind="tokens", output_chars=chars))
                    elif t == "thinking":
                        await on_progress(ProgressEvent(kind="thought", text=ev.thinking))
                    elif t == "message_delta":
                        await on_progress(
                            ProgressEvent(
                                kind="tokens",
                                output_tokens=ev.usage.output_tokens,
                                output_chars=chars,
                            )
                        )
                resp = await stream.get_final_message()
        except anthropic.APIStatusError as e:
            logger.exception(
                "llm.anthropic.failed", extra={"model": model, "status": e.status_code}
            )
            raise LLMError(f"Anthropic API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            logger.exception("llm.anthropic.connection_failed", extra={"model": model})
            raise LLMError(f"Anthropic connection error: {e}") from e

        parts: list[Part] = []
        for block in resp.content:
            if block.type == "text":
                parts.append(TextPart(text=block.text))
            elif block.type == "tool_use":
                parts.append(
                    ToolCallPart(
                        id=block.id, name=block.name, input=cast(dict[str, Any], block.input)
                    )
                )
        stop_map = {
            "end_turn": "end_turn",
            "tool_use": "tool_use",
            "max_tokens": "max_tokens",
            "refusal": "refusal",
        }
        stop = stop_map.get(resp.stop_reason or "", "other")
        # Anthropic's input_tokens is the uncached remainder only: add the cache traffic back
        # so input_tokens means "the whole prompt" like on OpenAI (see Usage)
        cache_read = resp.usage.cache_read_input_tokens or 0
        cache_write = resp.usage.cache_creation_input_tokens or 0
        usage = Usage(
            input_tokens=resp.usage.input_tokens + cache_read + cache_write,
            output_tokens=resp.usage.output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        thinking_ms = (
            int((first_output_at - started) * 1000) if first_output_at is not None else duration_ms
        )
        logger.info(
            "llm.anthropic.done",
            extra={
                "model": model,
                "stop": resp.stop_reason,
                "in": usage.input_tokens,
                "out": usage.output_tokens,
                "cached": usage.cache_read_tokens,
                "cache_write": usage.cache_write_tokens,
                "duration_ms": duration_ms,
                "thinking_ms": thinking_ms,
            },
        )
        # a turn cut off by max_tokens may end in an unsigned thinking block: replay its parts only
        replay = resp.stop_reason != "max_tokens"
        return Completion(
            message=Message(
                role="assistant",
                content=parts,
                raw=_replay_blocks(resp.content) if replay else None,
                raw_provider=PROVIDER if replay else None,
                raw_bound_to_history=replay,
            ),
            stop_reason=cast(Any, stop),
            usage=usage,
            model=resp.model,
            raw_stop_reason=resp.stop_reason,
            duration_ms=duration_ms,
            thinking_ms=thinking_ms,
        )
