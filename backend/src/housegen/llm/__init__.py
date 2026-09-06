"""Provider-agnostic LLM layer.

Internal message model (see types.py) is mapped to the Anthropic Messages API
and to the OpenAI Responses API by the two providers. Everything above this
package (agents, tools) only speaks the internal model.
"""

from housegen.llm.base import Provider
from housegen.llm.factory import get_provider
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

__all__ = [
    "Completion",
    "ImagePart",
    "Message",
    "Part",
    "ProgressCallback",
    "ProgressEvent",
    "Provider",
    "TextPart",
    "ToolCallPart",
    "ToolResultPart",
    "ToolSpec",
    "Usage",
    "get_provider",
]
