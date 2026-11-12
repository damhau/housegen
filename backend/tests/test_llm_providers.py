"""Mapping of the internal message model onto each provider's wire format."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from housegen.agent.critic import Critique, Verification
from housegen.agent.intake import Intake
from housegen.listing.pick import ExteriorChoice, ViewChoice
from housegen.llm.anthropic_provider import AnthropicProvider, _content_blocks, _message_blocks
from housegen.llm.openai_provider import _to_input_items
from housegen.llm.types import (
    ImagePart,
    Message,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
)


def _img() -> ImagePart:
    return ImagePart.from_bytes(b"\x89PNG", "image/png", label="Render — view 'north'")


def test_anthropic_tool_result_carries_images_inline() -> None:
    msg = Message(
        role="user",
        content=[
            ToolResultPart(tool_call_id="t1", content=[TextPart(text="ok"), _img()], is_error=False)
        ],
    )
    blocks = _content_blocks(msg.content)
    assert blocks[0]["type"] == "tool_result"
    inner = blocks[0]["content"]
    assert [b["type"] for b in inner] == ["text", "text", "image"]  # text, label, image
    assert inner[2]["source"]["media_type"] == "image/png"


def test_openai_tool_result_images_are_deferred_to_user_message() -> None:
    history = [
        Message.user("build it"),
        Message.assistant(
            ToolCallPart(id="call_1", name="render_views", input={"views": ["north"]})
        ),
        Message(
            role="user",
            content=[
                ToolResultPart(tool_call_id="call_1", content=[TextPart(text="rendered"), _img()])
            ],
        ),
    ]
    items = _to_input_items(history)
    kinds = [i.get("type") or i.get("role") for i in items]
    assert kinds == ["user", "function_call", "function_call_output", "user"]
    assert items[1]["call_id"] == "call_1"
    assert items[2]["call_id"] == "call_1"
    assert "follow in the next user message" in items[2]["output"]
    parts = items[3]["content"]
    assert parts[-1]["type"] == "input_image"
    assert parts[-1]["image_url"].startswith("data:image/png;base64,")


def test_openai_replays_raw_items_verbatim() -> None:
    raw = [
        {"type": "reasoning", "id": "rs_1", "encrypted_content": "abc", "summary": []},
        {"type": "function_call", "call_id": "call_9", "name": "list_files", "arguments": "{}"},
    ]
    history = [
        Message.user("go"),
        Message(
            role="assistant",
            content=[ToolCallPart(id="call_9", name="list_files", input={})],
            raw=raw,
            raw_provider="openai",
        ),
    ]
    items = _to_input_items(history)
    assert items[1:] == raw  # reasoning item preserved, nothing reconstructed


def test_openai_raw_from_other_provider_is_ignored() -> None:
    history = [
        Message(
            role="assistant",
            content=[TextPart(text="hi")],
            raw=[{"type": "thinking"}],
            raw_provider="anthropic",
        )
    ]
    items = _to_input_items(history)
    assert items == [{"role": "assistant", "content": [{"type": "output_text", "text": "hi"}]}]


def test_openai_error_results_are_flagged() -> None:
    history = [
        Message.user("x"),
        Message.assistant(ToolCallPart(id="call_1", name="read_file", input={"path": "src/a.js"})),
        Message(
            role="user",
            content=[
                ToolResultPart(
                    tool_call_id="call_1", content=[TextPart(text="missing")], is_error=True
                )
            ],
        ),
    ]
    items = _to_input_items(history)
    assert items[-1]["type"] == "function_call_output"
    assert items[-1]["output"].startswith("ERROR:")


class _FakeStream:
    def __init__(self, resp: Any) -> None:
        self._resp = resp

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def __aiter__(self) -> _FakeStream:
        return self

    async def __anext__(self) -> Any:
        raise StopAsyncIteration

    async def get_final_message(self) -> Any:
        return self._resp


class _FakeMessages:
    def __init__(self, resp: Any) -> None:
        self._resp = resp
        self.calls: list[dict[str, Any]] = []

    def stream(self, **kwargs: Any) -> _FakeStream:
        self.calls.append(kwargs)
        return _FakeStream(self._resp)


def _anthropic(stop: str = "tool_use") -> tuple[AnthropicProvider, _FakeMessages]:
    resp = SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking="the roof first", signature="sig-1"),
            SimpleNamespace(type="text", text="Reading the files."),
            SimpleNamespace(type="tool_use", id="t1", name="list_files", input={}),
        ],
        stop_reason=stop,
        usage=SimpleNamespace(
            input_tokens=10,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
            output_tokens=5,
        ),
        model="claude-opus-5-5",
    )
    provider = AnthropicProvider(api_key="test", timeout=10)
    fake = _FakeMessages(resp)
    provider._client = SimpleNamespace(messages=fake)  # type: ignore[assignment]
    return provider, fake


async def test_anthropic_replays_thinking_blocks_unchanged() -> None:
    """The model keeps its reasoning from one tool call to the next (preserved thinking)."""
    provider, fake = _anthropic()
    first = await provider.complete(
        model="claude-opus-5-5", system="s", messages=[Message.user("go")]
    )
    assert [p.type for p in first.message.content] == ["text", "tool_call"]
    history = [Message.user("go"), first.message, Message.user("next")]
    await provider.complete(model="claude-opus-5-5", system="s", messages=history)
    replayed = fake.calls[1]["messages"][1]["content"]
    assert replayed == [
        {"type": "thinking", "thinking": "the roof first", "signature": "sig-1"},
        {"type": "text", "text": "Reading the files."},
        {"type": "tool_use", "id": "t1", "name": "list_files", "input": {}},
    ]


async def test_anthropic_turn_cut_off_replays_without_thinking() -> None:
    provider, _ = _anthropic(stop="max_tokens")
    out = await provider.complete(
        model="claude-opus-5-5", system="s", messages=[Message.user("go")]
    )
    assert out.message.raw is None
    assert not out.message.raw_bound_to_history


async def test_anthropic_caches_a_conversation_for_an_hour() -> None:
    """A conversation (cache_key) is cached up to its last block, with the 1-hour TTL."""
    tools = [ToolSpec(name="list_files", description="d", input_schema={"type": "object"})]
    provider, fake = _anthropic()
    await provider.complete(
        model="claude-opus-5-5",
        system="s",
        messages=[Message.user("go")],
        tools=tools,
        cache_key="job",
    )
    kw = fake.calls[0]
    hour = {"type": "ephemeral", "ttl": "1h"}
    assert kw["cache_control"] == hour
    assert kw["system"][0]["cache_control"] == hour
    assert kw["tools"][-1]["cache_control"] == hour


async def test_anthropic_one_off_call_caches_only_its_prefix() -> None:
    tools = [ToolSpec(name="list_files", description="d", input_schema={"type": "object"})]
    provider, fake = _anthropic()
    await provider.complete(
        model="claude-opus-5-5", system="s", messages=[Message.user("go")], tools=tools
    )
    kw = fake.calls[0]
    assert "cache_control" not in kw
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_raw_from_other_provider_is_ignored() -> None:
    m = Message(
        role="assistant",
        content=[TextPart(text="hi")],
        raw=[{"type": "reasoning", "id": "rs_1"}],
        raw_provider="openai",
    )
    assert _message_blocks(m) == [{"type": "text", "text": "hi"}]


def _objects(schema: Any) -> list[dict[str, Any]]:
    if isinstance(schema, list):
        return [o for s in schema for o in _objects(s)]
    if not isinstance(schema, dict):
        return []
    found = [schema] if schema.get("type") == "object" else []
    return found + [o for v in schema.values() for o in _objects(v)]


@pytest.mark.parametrize("model", [Critique, Verification, Intake, ViewChoice, ExteriorChoice])
async def test_anthropic_response_schemas_close_every_object(model: type[BaseModel]) -> None:
    provider, fake = _anthropic(stop="end_turn")
    await provider.complete(
        model="claude-opus-5-5",
        system="s",
        messages=[Message.user("go")],
        response_schema=model.model_json_schema(),
    )
    schema = fake.calls[0]["output_config"]["format"]["schema"]
    objects = _objects(schema)
    assert objects
    assert all(o["additionalProperties"] is False for o in objects)
