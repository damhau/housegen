"""The builder agent: a provider-agnostic tool-use loop over the scene workspace."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from housegen.agent.progress import LiveProgress
from housegen.agent.tools import TOOL_SPECS, BuilderTools
from housegen.core.exceptions import LLMError
from housegen.llm import Completion, Message, Provider, TextPart, ToolResultPart, Usage

logger = logging.getLogger(__name__)

StepCallback = Callable[[dict[str, Any]], Awaitable[None]]
ProgressFactory = Callable[[int], LiveProgress]


@dataclass
class BuilderRun:
    summary: str = ""
    steps: int = 0
    usage: Usage = field(default_factory=Usage)
    finished: bool = False
    messages: list[Message] = field(default_factory=list)


def _arg_preview(name: str, args: dict[str, Any]) -> str:
    if name in ("write_file", "edit_file", "read_file", "delete_file"):
        return str(args.get("path", ""))
    if name == "render_views":
        return ", ".join(str(v) for v in args.get("views", []))
    if name == "finish":
        return str(args.get("summary", ""))[:200]
    return json.dumps(args)[:120]


async def run_builder(
    provider: Provider,
    model: str,
    system: str,
    messages: list[Message],
    tools: BuilderTools,
    *,
    max_steps: int,
    max_tokens: int,
    on_step: StepCallback | None = None,
    progress: ProgressFactory | None = None,
    require_checks: bool = True,
    effort: str | None = None,
) -> BuilderRun:
    """Drive the tool loop until `finish` is called or the step budget is spent.

    `messages` is mutated in place so the caller can continue the same conversation
    (e.g. feed critic feedback) in a later call. `progress(step)` returns a LiveProgress
    scope used to stream the model's progress for that step.
    """
    run = BuilderRun(messages=messages)
    nudges = 0

    async def complete(step: int) -> Completion:
        if progress is None:
            return await provider.complete(
                model=model,
                system=system,
                messages=messages,
                tools=TOOL_SPECS,
                max_tokens=max_tokens,
                effort=effort,
            )
        async with progress(step) as live:
            return await provider.complete(
                model=model,
                system=system,
                messages=messages,
                tools=TOOL_SPECS,
                max_tokens=max_tokens,
                on_progress=live.on_event,
                effort=effort,
            )

    while run.steps < max_steps:
        run.steps += 1
        completion = await complete(run.steps)
        run.usage = run.usage + completion.usage
        messages.append(completion.message)

        if completion.stop_reason == "refusal":
            raise LLMError("the model refused to continue building the scene")

        text = completion.message.text.strip()
        if text and on_step:
            await on_step({"kind": "text", "text": text[:2000], "step": run.steps})

        calls = completion.tool_calls
        if calls and completion.stop_reason == "max_tokens":
            # the tool call(s) arrived truncated: do not execute, ask for smaller pieces
            cut = [
                ToolResultPart(
                    tool_call_id=c.id,
                    content=[
                        TextPart(
                            text=(
                                "This tool call was cut off by the output token limit and was NOT "
                                "executed. Re-issue it in smaller pieces: write the module in parts "
                                "(write_file for a first part, then edit_file to append)."
                            )
                        )
                    ],
                    is_error=True,
                )
                for c in calls
            ]
            messages.append(Message(role="user", content=list(cut)))
            if on_step:
                await on_step(
                    {
                        "kind": "tool",
                        "tool": calls[0].name,
                        "args": _arg_preview(calls[0].name, calls[0].input),
                        "result": "truncated by the output limit, not executed",
                        "is_error": True,
                        "step": run.steps,
                    }
                )
            continue
        if not calls:
            # no tool call: the model thinks it is done. Insist on the finish protocol, then accept.
            if completion.stop_reason == "max_tokens":
                messages.append(
                    Message.user(
                        "Your last message was cut off by the token limit. Continue where you stopped."
                    )
                )
                continue
            nudges += 1
            if nudges > 2:
                run.summary = text or "(no summary)"
                break
            messages.append(
                Message.user(
                    "When you are done, call the `finish` tool with a summary. "
                    "Otherwise keep working with the tools."
                )
            )
            continue

        results: list[ToolResultPart] = []
        for call in calls:
            if call.name == "finish":
                if require_checks and not tools.last_check_ok:
                    results.append(
                        ToolResultPart(
                            tool_call_id=call.id,
                            content=[
                                TextPart(
                                    text=(
                                        "Not finished: run check_scene (it must report zero errors) "
                                        "after your last edit, then call finish again."
                                    )
                                )
                            ],
                            is_error=True,
                        )
                    )
                    if on_step:
                        await on_step(
                            {
                                "kind": "tool",
                                "tool": "finish",
                                "args": _arg_preview("finish", call.input),
                                "result": "rejected: check_scene required",
                                "step": run.steps,
                            }
                        )
                    continue
                run.summary = str(call.input.get("summary", "")).strip() or text
                run.finished = True
                results.append(ToolResultPart(tool_call_id=call.id, content=[TextPart(text="ok")]))
                if on_step:
                    await on_step(
                        {
                            "kind": "tool",
                            "tool": "finish",
                            "args": _arg_preview("finish", call.input),
                            "result": "finished",
                            "step": run.steps,
                        }
                    )
                continue

            content, is_error = await tools.call(call.name, call.input)
            results.append(ToolResultPart(tool_call_id=call.id, content=content, is_error=is_error))
            if on_step:
                first_text = next((c.text for c in content if isinstance(c, TextPart)), "")
                await on_step(
                    {
                        "kind": "tool",
                        "tool": call.name,
                        "args": _arg_preview(call.name, call.input),
                        "result": first_text[:300],
                        "is_error": is_error,
                        "step": run.steps,
                    }
                )
        messages.append(Message(role="user", content=list(results)))
        if run.finished:
            break

    if not run.finished:
        logger.warning("builder.budget_exhausted", extra={"steps": run.steps})
        run.summary = run.summary or (
            "Builder stopped: step budget exhausted before `finish` was called."
        )
    logger.info(
        "builder.done",
        extra={
            "steps": run.steps,
            "finished": run.finished,
            "in": run.usage.input_tokens,
            "out": run.usage.output_tokens,
        },
    )
    return run
