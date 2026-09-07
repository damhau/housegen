"""The builder agent: a provider-agnostic tool-use loop over the scene workspace."""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from housegen.agent.metrics import ToolCallMetric, TurnMetric
from housegen.agent.progress import LiveProgress
from housegen.agent.tools import TOOL_SPECS, BuilderTools
from housegen.core.exceptions import LLMError
from housegen.llm import (
    Completion,
    ImagePart,
    Message,
    Part,
    Provider,
    TextPart,
    ToolResultPart,
    Usage,
)

logger = logging.getLogger(__name__)


PRUNE_EVERY = 3


def prune_render_images(messages: list[Message], keep_last: int = 1, batch: int = 1) -> int:
    """Replace screenshots in all but the most recent render result(s) with a short note.

    Screenshots are the bulk of the context in a long build; the model's own comments about
    them stay, so nothing it concluded is lost. Returns the number of images removed.

    Rewriting an old message invalidates the provider's prefix cache from that point, which
    costs far more than the images it saves (#5): with `batch` > 1 nothing is pruned until at
    least `batch` stale render results have piled up, then all of them go at once, so the
    cache is lost at most once per `batch` render rounds.
    """

    def has_images(m: Message) -> bool:
        return m.role == "user" and any(
            isinstance(p, ToolResultPart) and any(isinstance(c, ImagePart) for c in p.content)
            for p in m.content
        )

    targets = [i for i, m in enumerate(messages) if has_images(m)]
    if keep_last:
        targets = targets[:-keep_last]
    if len(targets) < batch:
        return 0
    removed = 0
    for i in targets:
        parts: list[Part] = []
        for p in messages[i].content:
            if not isinstance(p, ToolResultPart):
                parts.append(p)
                continue
            content: list[TextPart | ImagePart] = []
            for c in p.content:
                if isinstance(c, ImagePart):
                    removed += 1
                    content.append(
                        TextPart(
                            text=f"[{c.label or 'render'}: image dropped from context; render again to see it]"
                        )
                    )
                else:
                    content.append(c)
            parts.append(
                ToolResultPart(tool_call_id=p.tool_call_id, content=content, is_error=p.is_error)
            )
        messages[i] = Message(role="user", content=parts)
    if removed:
        logger.info("builder.pruned_images", extra={"removed": removed})
    return removed


StepCallback = Callable[[dict[str, Any]], Awaitable[None]]
ProgressFactory = Callable[[int], LiveProgress]


@dataclass
class BuilderRun:
    summary: str = ""
    steps: int = 0
    usage: Usage = field(default_factory=Usage)
    finished: bool = False
    messages: list[Message] = field(default_factory=list)
    # from `finish`: optional additions left out on purpose, and questions for the owner
    suggestions: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)


def _str_list(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out = [str(v).strip() for v in value if str(v).strip()]
    return out[:limit]


def budget_note(step: int, max_steps: int) -> str | None:
    """A short pacing note for the model at half, three quarters and near the end of the budget."""
    if step == max_steps // 2:
        return f"(step {step} of {max_steps}: half of your step budget is spent)"
    if step == max_steps * 3 // 4:
        return f"(step {step} of {max_steps}: three quarters of your step budget are spent)"
    if max_steps - 6 < step < max_steps:
        return f"(step {step} of {max_steps}: {max_steps - step} steps left, finish soon)"
    return None


def _group_tools(calls: list[ToolCallMetric]) -> str:
    """ "edit_file x4, render_views" for a log line."""
    counts: dict[str, int] = {}
    for c in calls:
        counts[c.name] = counts.get(c.name, 0) + 1
    return ", ".join(f"{n} x{k}" if k > 1 else n for n, k in counts.items())


def _arg_preview(name: str, args: dict[str, Any]) -> str:
    if name in ("write_file", "edit_file", "read_file", "delete_file"):
        return str(args.get("path", ""))
    if name == "render_views":
        return ", ".join(str(v) for v in args.get("views", []))
    if name == "apply_patch":
        files = re.findall(
            r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", str(args.get("patch", "")), re.M
        )
        return ", ".join(f.strip() for f in files)[:200]
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

    async def emit_turn(turn: TurnMetric) -> None:
        """One persisted record per step: the call's time and tokens, the tools it ran (#13)."""
        logger.info(
            "llm.turn",
            extra={
                "step": turn.step,
                "duration_ms": turn.duration_ms,
                "thinking_ms": turn.thinking_ms,
                "tools_ms": turn.tools_ms,
                "render_ms": turn.render_ms,
                "in": turn.input_tokens,
                "cached": turn.cached_tokens,
                "out": turn.output_tokens,
                "cache_hit": round(turn.cache_hit, 3),
                "tools": _group_tools(turn.tool_calls),
            },
        )
        if on_step:
            await on_step({"kind": "turn", **turn.event_payload()})

    while run.steps < max_steps:
        run.steps += 1
        prune_render_images(messages, batch=PRUNE_EVERY)
        completion = await complete(run.steps)
        run.usage = run.usage + completion.usage
        messages.append(completion.message)
        turn = TurnMetric(
            step=run.steps,
            role="builder",
            model=completion.model or model,
            duration_ms=completion.duration_ms,
            thinking_ms=completion.thinking_ms,
            input_tokens=completion.usage.input_tokens,
            cached_tokens=completion.usage.cache_read_tokens,
            cache_write_tokens=completion.usage.cache_write_tokens,
            output_tokens=completion.usage.output_tokens,
            reasoning_tokens=completion.usage.reasoning_tokens,
        )
        render_ms_before = tools.render_ms_total
        tools_started = time.perf_counter()

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
            await emit_turn(turn)
            continue
        if not calls:
            # no tool call: the model thinks it is done. Insist on the finish protocol, then accept.
            await emit_turn(turn)
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
                    turn.tool_calls.append(
                        ToolCallMetric(
                            name="finish", args=_arg_preview("finish", call.input), ok=False
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
                run.suggestions = _str_list(call.input.get("suggestions"), 8)
                run.questions = _str_list(call.input.get("questions"), 4)
                run.finished = True
                turn.tool_calls.append(
                    ToolCallMetric(name="finish", args=_arg_preview("finish", call.input))
                )
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

            call_started = time.perf_counter()
            content, is_error = await tools.call(call.name, call.input)
            turn.tool_calls.append(
                ToolCallMetric(
                    name=call.name,
                    args=_arg_preview(call.name, call.input),
                    duration_ms=int((time.perf_counter() - call_started) * 1000),
                    ok=not is_error,
                )
            )
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
        note = budget_note(run.steps, max_steps)
        if note and results and not run.finished:
            results[-1].content.append(TextPart(text=note))
        messages.append(Message(role="user", content=list(results)))
        turn.tools_ms = int((time.perf_counter() - tools_started) * 1000)
        turn.render_ms = tools.render_ms_total - render_ms_before
        await emit_turn(turn)
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
