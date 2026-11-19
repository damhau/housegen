"""The cache-miss flag (#28): a builder turn lost the cache when it read less than 90 % of the
previous turn's prompt of the same conversation, not when its own cached share is low."""

from __future__ import annotations

import itertools
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from openai.types.responses import ResponseFunctionToolCall

from housegen.agent.builder import run_builder
from housegen.agent.metrics import RunMetrics, TurnMetric
from housegen.agent.tools import BuilderTools
from housegen.agent.workspace import Workspace
from housegen.core.config import Settings
from housegen.llm.anthropic_provider import AnthropicProvider
from housegen.llm.openai_provider import OpenAIProvider
from housegen.llm.types import (
    Completion,
    ImagePart,
    Message,
    ToolCallPart,
    ToolResultPart,
    Usage,
)

# job 76c18204800d (2026-09-07), the conversation after its first restart: (prompt, cached).
# Step 3 read the whole of step 2's prompt (its new half was tool results); step 9 followed a
# prune (the prompt shrank) and read only the prefix before the first rewritten message.
JOB_76C1 = [
    (26293, 4805),
    (26423, 4805),
    (53967, 26420),
    (65604, 26420),
    (77441, 26420),
    (84468, 77438),
    (84809, 84465),
    (88777, 84806),
    (80417, 26420),
]


class _Renderer:
    async def render(self, *a: Any, **kw: Any) -> Any:
        from housegen.render.renderer import RenderResult

        return RenderResult(images={}, errors=[])


@pytest.fixture
def tools(tmp_path: Path) -> BuilderTools:
    ws = Workspace(tmp_path)
    ws.write("src/scene.js", "export async function buildScene() {}\n")
    return BuilderTools(ws, _Renderer(), "http://x/index.html", tmp_path / "renders")  # type: ignore[arg-type]


class _Provider:
    """Lists the files every turn and reports the scripted (prompt, cached) of each call."""

    name = "fake"

    def __init__(self, usage: list[tuple[int, int]]) -> None:
        self.usage = list(usage)
        self.ids = itertools.count()

    async def complete(self, **kw: Any) -> Completion:
        prompt, cached = self.usage.pop(0)
        call = ToolCallPart(id=f"c{next(self.ids)}", name="list_files", input={})
        return Completion(
            message=Message(role="assistant", content=[call]),
            stop_reason="tool_use",
            usage=Usage(input_tokens=prompt, cache_read_tokens=cached),
        )


async def _turns(
    provider: Any, messages: list[Message], tools: BuilderTools, steps: int, **kw: Any
) -> list[dict[str, Any]]:
    """The `turn` events of one builder pass over `messages`."""
    out: list[dict[str, Any]] = []

    async def on_step(ev: dict[str, Any]) -> None:
        if ev.get("kind") == "turn":
            out.append({k: v for k, v in ev.items() if k != "kind"})

    await run_builder(
        provider,
        "m",
        "sys",
        messages,
        tools,
        max_steps=steps,
        max_tokens=100,
        on_step=on_step,
        **kw,
    )
    return out


def _render_history() -> list[Message]:
    def result(i: int) -> Message:
        img = ImagePart.from_bytes(b"x", "image/jpeg", label=f"r{i}")
        return Message(role="user", content=[ToolResultPart(tool_call_id=f"r{i}", content=[img])])

    return [Message.user("go"), result(1), result(2), result(3)]


async def test_the_eight_steps_of_job_76c1_flag_2_4_5_and_9(tools: BuilderTools) -> None:
    # the prune lands where the job's did: after step 8's 88.8k prompt
    turns = await _turns(
        _Provider(JOB_76C1), _render_history(), tools, len(JOB_76C1), prune_above_tokens=85_000
    )
    assert [t["step"] for t in turns if t["cache_miss"]] == [2, 4, 5, 9]
    step3 = turns[2]
    assert step3["cache_hit"] < 0.5  # the old ratio rule's false positive
    assert step3["prev_input_tokens"] == 26423
    assert [t["pruned_images"] for t in turns] == [0] * 8 + [2]
    metrics = RunMetrics(Settings(MODEL_PRICES={}))
    for t in turns:
        metrics.add(TurnMetric.model_validate(t))
    assert metrics.summary().builder.cache_miss_turns == 4


async def test_the_previous_turn_is_the_previous_turn_of_the_same_conversation(
    tools: BuilderTools,
) -> None:
    # a resumed build starts a conversation: its first turn is never judged
    first = await _turns(_Provider([(22309, 0), (30292, 26000)]), [Message.user("go")], tools, 2)
    conversation = [Message.user("resumed")]
    resumed = await _turns(_Provider([(26293, 4805)]), conversation, tools, 1)
    assert [t["cache_miss"] for t in first + resumed] == [False, False, False]
    assert resumed[0]["prev_input_tokens"] == 0
    # a critic round continues it: its step 1 is judged against the pass before
    conversation.append(Message.user("the critic's findings"))
    kept = await _turns(_Provider([(27400, 26290)]), conversation, tools, 1)
    assert kept[0]["step"] == 1
    assert kept[0]["prev_input_tokens"] == 26293
    assert not kept[0]["cache_miss"]
    lost = await _turns(_Provider([(28100, 4805)]), conversation, tools, 1)
    assert lost[0]["prev_input_tokens"] == 27400
    assert lost[0]["cache_miss"]


def test_critic_and_intake_calls_are_never_cache_misses() -> None:
    for role in ("critic", "intake"):
        turn = TurnMetric(
            step=2, role=role, input_tokens=90_000, cached_tokens=0, prev_input_tokens=80_000
        )
        assert not turn.cache_miss


class _Stream:
    def __init__(self, resp: Any) -> None:
        self.resp = resp

    async def __aenter__(self) -> _Stream:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def __aiter__(self) -> _Stream:
        return self

    async def __anext__(self) -> Any:
        raise StopAsyncIteration

    async def get_final_message(self) -> Any:
        return self.resp

    async def get_final_response(self) -> Any:
        return self.resp


def _anthropic(usage: list[tuple[int, int, int]]) -> AnthropicProvider:
    """Anthropic reports (uncached input, cache reads, cache writes) per call."""
    ids = itertools.count()

    def stream(**kw: Any) -> _Stream:
        uncached, read, write = usage.pop(0)
        block = SimpleNamespace(type="tool_use", id=f"t{next(ids)}", name="list_files", input={})
        return _Stream(
            SimpleNamespace(
                content=[block],
                stop_reason="tool_use",
                usage=SimpleNamespace(
                    input_tokens=uncached,
                    cache_read_input_tokens=read,
                    cache_creation_input_tokens=write,
                    output_tokens=50,
                ),
                model="claude-opus-5-5",
            )
        )

    provider = AnthropicProvider(api_key="test", timeout=10)
    provider._client = SimpleNamespace(messages=SimpleNamespace(stream=stream))  # type: ignore[assignment]
    return provider


def _openai(usage: list[tuple[int, int]]) -> OpenAIProvider:
    """OpenAI reports (whole prompt, cached tokens) per call."""
    ids = itertools.count()

    def stream(**kw: Any) -> _Stream:
        prompt, cached = usage.pop(0)
        call = ResponseFunctionToolCall(
            type="function_call", call_id=f"c{next(ids)}", name="list_files", arguments="{}"
        )
        return _Stream(
            SimpleNamespace(
                status="completed",
                output=[call],
                incomplete_details=None,
                usage=SimpleNamespace(
                    input_tokens=prompt,
                    output_tokens=50,
                    input_tokens_details=SimpleNamespace(cached_tokens=cached),
                    output_tokens_details=SimpleNamespace(reasoning_tokens=0),
                ),
                model="gpt-x",
            )
        )

    provider = OpenAIProvider(api_key="test", base_url=None, timeout=10)
    provider._client = SimpleNamespace(responses=SimpleNamespace(stream=stream))  # type: ignore[assignment]
    return provider


@pytest.mark.parametrize("lost", [False, True])
async def test_both_providers_flag_the_same_traffic(tools: BuilderTools, lost: bool) -> None:
    """A 30k prompt, then 34k: read back whole, or only the 4.8k of system and tools."""
    anthropic = _anthropic([(0, 0, 30_000), (0, 4_800, 29_200) if lost else (0, 30_000, 4_000)])
    openai = _openai([(30_000, 0), (34_000, 4_736 if lost else 29_952)])
    for provider in (anthropic, openai):
        turns = await _turns(provider, [Message.user("go")], tools, 2, cache_key="job")
        assert [t["input_tokens"] for t in turns] == [30_000, 34_000]
        assert turns[1]["prev_input_tokens"] == 30_000
        assert [t["cache_miss"] for t in turns] == [False, lost]
