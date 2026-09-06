"""Mapping of the internal message model onto each provider's wire format."""

from housegen.llm.anthropic_provider import _content_blocks
from housegen.llm.openai_provider import _to_input_items
from housegen.llm.types import ImagePart, Message, TextPart, ToolCallPart, ToolResultPart


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
