"""OpenAI provider on the Responses API (streamed).

Why Responses and not Chat Completions: reasoning models (gpt-5.x, gpt-6-astra) refuse function
tools on /v1/chat/completions unless reasoning is disabled, and the builder needs both.
Reasoning items are replayed verbatim across turns (stateless, `store=False`) so the model keeps
its chain of thought between tool calls.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, cast

import openai

from housegen.core.exceptions import LLMError
from housegen.llm.types import (
    Completion,
    ImagePart,
    Message,
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

PROVIDER = "openai"


def _image_item(p: ImagePart) -> dict[str, Any]:
    return {
        "type": "input_image",
        "image_url": f"data:{p.media_type};base64,{p.data}",
        "detail": "high",
    }


def _user_content(parts: list[TextPart | ImagePart]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in parts:
        if isinstance(p, TextPart):
            out.append({"type": "input_text", "text": p.text})
        else:
            if p.label:
                out.append({"type": "input_text", "text": p.label})
            out.append(_image_item(p))
    return out


def _to_input_items(messages: list[Message]) -> list[dict[str, Any]]:
    """Map the internal model onto Responses API input items.

    Tool results become `function_call_output` items; images that belong to a tool result
    are emitted right after as a `user` message (function outputs are text-only).
    """
    items: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "assistant":
            if m.raw and m.raw_provider == PROVIDER:
                items.extend(m.raw)  # verbatim replay incl. reasoning items
                continue
            text = m.text
            if text:
                items.append(
                    {"role": "assistant", "content": [{"type": "output_text", "text": text}]}
                )
            for p in m.content:
                if isinstance(p, ToolCallPart):
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": p.id,
                            "name": p.name,
                            "arguments": json.dumps(p.input),
                        }
                    )
            continue

        deferred: list[TextPart | ImagePart] = []
        plain: list[TextPart | ImagePart] = []
        for p in m.content:
            if isinstance(p, ToolResultPart):
                texts = [c.text for c in p.content if isinstance(c, TextPart)]
                images = [c for c in p.content if isinstance(c, ImagePart)]
                body = "\n".join(texts)
                if images:
                    body += f"\n[{len(images)} image(s) for this tool result follow in the next user message]"
                    deferred.append(TextPart(text=f"Images from tool call {p.tool_call_id}:"))
                    deferred.extend(images)
                if p.is_error:
                    body = "ERROR: " + body
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": p.tool_call_id,
                        "output": body or "(empty)",
                    }
                )
            else:
                plain.append(cast(TextPart | ImagePart, p))
        combined = deferred + plain
        if combined:
            items.append({"role": "user", "content": _user_content(combined)})
    return items


class OpenAIProvider:
    name = PROVIDER

    def __init__(self, api_key: str | None, base_url: str | None, timeout: float) -> None:
        self._client = openai.AsyncOpenAI(
            api_key=api_key, base_url=base_url, timeout=timeout, max_retries=3
        )

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
        # Anthropic's "max" has no OpenAI equivalent; xhigh is the top there.
        reasoning_effort = "xhigh" if effort == "max" else (effort or "high")
        kwargs: dict[str, Any] = {
            "model": model,
            "instructions": system,
            "input": _to_input_items(messages),
            "max_output_tokens": max_tokens,
            # summary: "auto" streams short reasoning summaries → shown live in the UI
            "reasoning": {"effort": reasoning_effort, "summary": "auto"},
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                    "strict": False,
                }
                for t in tools
            ]
        if response_schema:
            kwargs["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "response",
                    "schema": response_schema,
                    "strict": False,
                }
            }

        started = time.perf_counter()
        logger.info(
            "llm.openai.start",
            extra={"model": model, "messages": len(messages), "tools": len(tools or [])},
        )
        chars = 0
        terminal: Any = None  # response carried by response.incomplete / response.failed
        first_output_at: float | None = None  # when the first non-reasoning item started
        try:
            async with self._client.responses.stream(**kwargs) as stream:
                async for raw in stream:
                    ev: Any = raw
                    t = ev.type
                    if t in ("response.incomplete", "response.failed"):
                        # the SDK helper only recognises response.completed; keep these ourselves
                        terminal = ev.response
                        continue
                    if (
                        t == "response.output_item.added"
                        and ev.item.type != "reasoning"
                        and first_output_at is None
                    ):
                        first_output_at = time.perf_counter()
                    if on_progress is None:
                        continue
                    if t == "response.output_item.added":
                        it = ev.item.type
                        if it == "reasoning":
                            await on_progress(ProgressEvent(kind="phase", phase="thinking"))
                        elif it == "message":
                            await on_progress(ProgressEvent(kind="phase", phase="writing"))
                        elif it == "function_call":
                            await on_progress(
                                ProgressEvent(
                                    kind="phase", phase="tool_call", tool_name=ev.item.name
                                )
                            )
                    elif t == "response.output_text.delta":
                        chars += len(ev.delta)
                        await on_progress(
                            ProgressEvent(kind="text", text=ev.delta, output_chars=chars)
                        )
                    elif t == "response.function_call_arguments.delta":
                        chars += len(ev.delta)
                        await on_progress(ProgressEvent(kind="tokens", output_chars=chars))
                    elif t == "response.reasoning_summary_text.delta":
                        await on_progress(ProgressEvent(kind="thought", text=ev.delta))
                    elif t == "response.reasoning_summary_text.done":
                        await on_progress(ProgressEvent(kind="thought", text="\n\n"))
                resp: Any = terminal if terminal is not None else await stream.get_final_response()
        except openai.APIStatusError as e:
            logger.exception("llm.openai.failed", extra={"model": model, "status": e.status_code})
            raise LLMError(f"OpenAI API error {e.status_code}: {e.message}") from e
        except openai.APIConnectionError as e:
            logger.exception("llm.openai.connection_failed", extra={"model": model})
            raise LLMError(f"OpenAI connection error: {e}") from e

        if resp.status == "failed":
            err = resp.error
            detail = f"{err.code}: {err.message}" if err is not None else "unknown error"
            logger.error("llm.openai.response_failed", extra={"model": model, "detail": detail})
            raise LLMError(f"OpenAI response failed: {detail}")
        if resp.status == "incomplete":
            reason = resp.incomplete_details.reason if resp.incomplete_details else None
            logger.warning("llm.openai.incomplete", extra={"model": model, "reason": reason})

        parts: list[Part] = []
        refused = False
        raw_items: list[dict[str, Any]] = []
        for item in resp.output:
            raw_items.append(item.model_dump(exclude_none=True, mode="json"))
            if item.type == "message":
                for c in item.content:
                    if c.type == "output_text":
                        parts.append(TextPart(text=c.text))
                    elif c.type == "refusal":
                        refused = True
                        parts.append(TextPart(text=c.refusal))
            elif item.type == "function_call":
                try:
                    args = json.loads(item.arguments or "{}")
                except json.JSONDecodeError:
                    args = {"_raw": item.arguments}
                parts.append(ToolCallPart(id=item.call_id, name=item.name, input=args))

        if refused:
            stop = "refusal"
        elif any(isinstance(p, ToolCallPart) for p in parts):
            stop = "tool_use"
        elif resp.status == "incomplete":
            reason = (
                getattr(resp.incomplete_details, "reason", None)
                if resp.incomplete_details
                else None
            )
            stop = "max_tokens" if reason == "max_output_tokens" else "other"
        else:
            stop = "end_turn"

        u = resp.usage
        cached = 0
        reasoning = 0
        if u is not None and u.input_tokens_details is not None:
            cached = u.input_tokens_details.cached_tokens or 0
        if u is not None and getattr(u, "output_tokens_details", None) is not None:
            reasoning = getattr(u.output_tokens_details, "reasoning_tokens", 0) or 0
        usage = Usage(
            input_tokens=u.input_tokens if u else 0,
            output_tokens=u.output_tokens if u else 0,
            cache_read_tokens=cached,
            reasoning_tokens=reasoning,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        thinking_ms = (
            int((first_output_at - started) * 1000) if first_output_at is not None else duration_ms
        )
        logger.info(
            "llm.openai.done",
            extra={
                "model": model,
                "stop": stop,
                "status": resp.status,
                "in": usage.input_tokens,
                "out": usage.output_tokens,
                "cached": usage.cache_read_tokens,
                "reasoning": usage.reasoning_tokens,
                "duration_ms": duration_ms,
                "thinking_ms": thinking_ms,
            },
        )
        return Completion(
            message=Message(role="assistant", content=parts, raw=raw_items, raw_provider=PROVIDER),
            stop_reason=cast(Any, stop),
            usage=usage,
            model=resp.model,
            raw_stop_reason=resp.status,
            duration_ms=duration_ms,
            thinking_ms=thinking_ms,
        )
