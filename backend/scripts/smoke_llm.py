"""Cheap end-to-end check of the configured LLM provider (a few cents at most).

    cd backend && uv run python scripts/smoke_llm.py

Exercises the three things the agents need: a tool call round-trip (with the
provider's reasoning replay), a plain text answer, and a JSON-schema response.
"""

import asyncio
import logging

from housegen.core.config import get_settings
from housegen.llm import Message, TextPart, ToolResultPart, ToolSpec, get_provider


async def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    s = get_settings()
    p = get_provider()
    model = s.resolve_model("builder")
    print(f"provider={p.name} model={model}")

    tool = ToolSpec(
        name="list_files",
        description="List files",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    )
    msgs = [Message.user("Call list_files, then reply with the single word DONE.")]
    c1 = await p.complete(
        model=model, system="You are terse.", messages=msgs, tools=[tool], max_tokens=2000
    )
    print("turn 1:", c1.stop_reason, "tool calls:", [t.name for t in c1.tool_calls])
    if not c1.tool_calls:
        print("FAIL: the model did not call the tool")
        return
    msgs.append(c1.message)
    msgs.append(
        Message(
            role="user",
            content=[
                ToolResultPart(tool_call_id=t.id, content=[TextPart(text="src/scene.js")])
                for t in c1.tool_calls
            ],
        )
    )
    c2 = await p.complete(
        model=model, system="You are terse.", messages=msgs, tools=[tool], max_tokens=2000
    )
    print(
        "turn 2:",
        c2.stop_reason,
        "text:",
        c2.message.text.strip()[:60],
        "| usage:",
        c2.usage.model_dump(),
    )

    schema = {
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "string"}},
        "required": ["a", "b"],
        "additionalProperties": False,
    }
    c3 = await p.complete(
        model=model,
        system="Answer as JSON.",
        messages=[Message.user("Give a=1 and b='x'.")],
        response_schema=schema,
        max_tokens=500,
    )
    print("json:", c3.message.text.strip())
    print(
        "OK"
        if "DONE" in c2.message.text.upper() and '"a"' in c3.message.text
        else "CHECK OUTPUT ABOVE"
    )


if __name__ == "__main__":
    asyncio.run(main())
