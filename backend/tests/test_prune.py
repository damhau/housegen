from housegen.agent.builder import prune_render_images
from housegen.llm.types import ImagePart, Message, TextPart, ToolCallPart, ToolResultPart


def _img(label: str) -> ImagePart:
    return ImagePart.from_bytes(b"\x89PNG", "image/png", label=label)


def _render_result(call_id: str, *labels: str) -> Message:
    return Message(
        role="user",
        content=[
            ToolResultPart(
                tool_call_id=call_id, content=[TextPart(text="ok"), *(_img(x) for x in labels)]
            )
        ],
    )


def test_only_the_latest_render_set_keeps_its_images() -> None:
    photos = Message.user("build", _img("Photograph of the north façade"))
    messages = [
        photos,
        Message.assistant(ToolCallPart(id="r1", name="render_views", input={})),
        _render_result("r1", "Render — view 'north'", "Render — view 'south'"),
        Message.assistant("looks off, fixing"),
        Message.assistant(ToolCallPart(id="r2", name="render_views", input={})),
        _render_result("r2", "Render — view 'north'"),
    ]
    removed = prune_render_images(messages)
    assert removed == 2
    # the initial photos are untouched (not a tool result)
    assert any(isinstance(p, ImagePart) for p in messages[0].content)
    # first render set → text notes, keeps its tool_call_id
    first = messages[2].content[0]
    assert isinstance(first, ToolResultPart)
    assert first.tool_call_id == "r1"
    assert not any(isinstance(c, ImagePart) for c in first.content)
    assert any("dropped from context" in c.text for c in first.content if isinstance(c, TextPart))
    # latest render set keeps its image
    last = messages[5].content[0]
    assert isinstance(last, ToolResultPart)
    assert any(isinstance(c, ImagePart) for c in last.content)
    # idempotent
    assert prune_render_images(messages) == 0
