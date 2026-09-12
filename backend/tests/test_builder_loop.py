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
