"""The builder loop against a scripted fake provider (no network, no browser)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from housegen.agent.builder import run_builder
from housegen.agent.tools import BuilderTools
from housegen.agent.workspace import Workspace
from housegen.llm.types import Completion, Message, TextPart, ToolCallPart, ToolSpec


class FakeProvider:
    name = "fake"

    def __init__(self, turns: list[list[ToolCallPart] | str]) -> None:
        self.turns = list(turns)
        self.calls: list[list[Message]] = []

    async def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        response_schema: dict[str, Any] | None = None,
        max_tokens: int = 16000,
        on_progress: Any = None,
        effort: str | None = None,
        cache_key: str | None = None,
    ) -> Completion:
        self.calls.append(list(messages))
        turn = self.turns.pop(0)
        if isinstance(turn, str):
            return Completion(message=Message.assistant(turn), stop_reason="end_turn")
        return Completion(
            message=Message(role="assistant", content=list(turn)), stop_reason="tool_use"
        )


class FakeRenderer:
    def __init__(self) -> None:
        self.calls = 0

    async def render(
        self, scene_url: str, views: list[str], out_dir: Path, quality: str = "high"
    ) -> Any:
        from housegen.render.renderer import RenderResult

        self.calls += 1
        return RenderResult(images={}, errors=[])


@pytest.fixture
def tools(tmp_path: Path) -> BuilderTools:
    ws = Workspace(tmp_path)
    ws.write("src/scene.js", "export async function buildScene() {}\n")
    return BuilderTools(ws, FakeRenderer(), "http://x/index.html", tmp_path / "renders")  # type: ignore[arg-type]


async def test_finish_is_rejected_until_check_scene_passes(tools: BuilderTools) -> None:
    provider = FakeProvider(
        [
            [
                ToolCallPart(
                    id="1",
                    name="write_file",
                    input={"path": "src/shell.js", "content": "export const x = 1;"},
                )
            ],
            [ToolCallPart(id="2", name="finish", input={"summary": "too early"})],
            [ToolCallPart(id="3", name="check_scene", input={})],
            [ToolCallPart(id="4", name="finish", input={"summary": "done: a box"})],
        ]
    )
    steps: list[dict[str, Any]] = []

    async def on_step(ev: dict[str, Any]) -> None:
        steps.append(ev)

    run = await run_builder(
        provider,
        "m",
        "sys",
        [Message.user("go")],
        tools,
        max_steps=10,
        max_tokens=100,
        on_step=on_step,
    )
    assert run.finished
    assert run.summary == "done: a box"
    assert run.steps == 4
    rejected = [
        s for s in steps if s.get("tool") == "finish" and "rejected" in str(s.get("result"))
    ]
    assert len(rejected) == 1
    # the rejection reached the model as an error tool result
    third_call_messages = provider.calls[2]
    last = third_call_messages[-1]
    assert last.role == "user"
    assert any(getattr(p, "is_error", False) for p in last.content)


async def test_budget_exhaustion_returns_unfinished(tools: BuilderTools) -> None:
    provider = FakeProvider(
        [[ToolCallPart(id=str(i), name="list_files", input={})] for i in range(5)]
    )
    run = await run_builder(
        provider, "m", "sys", [Message.user("go")], tools, max_steps=3, max_tokens=100
    )
    assert not run.finished
    assert run.steps == 3
    assert "budget" in run.summary


async def test_plain_text_ending_is_nudged_then_accepted(tools: BuilderTools) -> None:
    # two nudges toward `finish`, then the third plain-text answer is accepted as the summary
    provider = FakeProvider(["I think it is done.", "Really done.", "Done done."])
    run = await run_builder(
        provider, "m", "sys", [Message.user("go")], tools, max_steps=10, max_tokens=100
    )
    assert not run.finished
    assert run.summary == "Done done."
    assert run.steps == 3
    assert any(
        isinstance(p, TextPart) and "finish" in p.text for m in provider.calls[1] for p in m.content
    )


class SizedFakeProvider(FakeProvider):
    """A fake whose every call reports the same prompt size, to drive the prune trigger."""

    def __init__(self, turns: list[list[ToolCallPart] | str], prompt_tokens: int) -> None:
        super().__init__(turns)
        self.prompt_tokens = prompt_tokens

    async def complete(self, **kw: Any) -> Completion:
        c = await super().complete(**kw)
        c.usage.input_tokens = self.prompt_tokens
        return c


def _render_history() -> list[Message]:
    """A conversation with two stale render results (images) and one current one."""
    from housegen.llm.types import ImagePart, ToolResultPart

    def result(i: int) -> Message:
        img = ImagePart.from_bytes(b"x", "image/jpeg", label=f"r{i}")
        return Message(role="user", content=[ToolResultPart(tool_call_id=f"c{i}", content=[img])])

    return [Message.user("go"), result(1), result(2), result(3)]


def _images_in(messages: list[Message]) -> int:
    from housegen.llm.types import ImagePart, ToolResultPart

    return sum(
        isinstance(c, ImagePart)
        for m in messages
        for p in m.content
        if isinstance(p, ToolResultPart)
        for c in p.content
    )


async def test_history_is_not_pruned_under_the_token_threshold(tools: BuilderTools) -> None:
    # prompts of 80k tokens, threshold 200k: the screenshots stay, the prefix cache is kept (#5)
    provider = SizedFakeProvider(["a", "b", "c"], prompt_tokens=80_000)
    messages = _render_history()
    await run_builder(provider, "m", "sys", messages, tools, max_steps=10, max_tokens=100)
    assert _images_in(messages) == 3


async def test_history_is_pruned_once_a_prompt_exceeds_the_threshold(
    tools: BuilderTools,
) -> None:
    provider = SizedFakeProvider(["a", "b", "c"], prompt_tokens=250_000)
    messages = _render_history()
    await run_builder(provider, "m", "sys", messages, tools, max_steps=10, max_tokens=100)
    # the first call reports the size, the prune happens before the second: only the most
    # recent render result keeps its image
    assert _images_in(provider.calls[0]) == 3
    assert _images_in(provider.calls[1]) == 1
    assert _images_in(messages) == 1


async def test_prune_threshold_zero_never_prunes(tools: BuilderTools) -> None:
    provider = SizedFakeProvider(["a", "b", "c"], prompt_tokens=900_000)
    messages = _render_history()
    await run_builder(
        provider, "m", "sys", messages, tools, max_steps=10, max_tokens=100, prune_above_tokens=0
    )
    assert _images_in(messages) == 3
