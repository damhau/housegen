"""Builder tools: JSON schemas + handlers bound to a workspace and the renderer."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from housegen.agent.workspace import Workspace, WorkspaceError
from housegen.core.exceptions import RenderError
from housegen.llm.types import ImagePart, TextPart, ToolSpec
from housegen.render.renderer import Renderer

logger = logging.getLogger(__name__)

ToolOutput = tuple[list[TextPart | ImagePart], bool]  # (content, is_error)
Handler = Callable[[dict[str, Any]], Awaitable[ToolOutput]]

VALID_VIEWS = [
    "north",
    "south",
    "east",
    "west",
    "northeast",
    "northwest",
    "southeast",
    "southwest",
    "aerial",
    "top",
]

TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="list_files",
        description=(
            "List the JavaScript modules in the scene workspace (src/) and the read-only kit "
            "sources (kit/house.js, kit/runtime.js)."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ToolSpec(
        name="read_file",
        description=(
            "Read a module from the workspace, or a kit source (kit/house.js, kit/runtime.js) "
            "when you need to know exactly how a component or the runtime behaves."
        ),
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "e.g. src/shell.js"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="write_file",
        description="Create or fully replace a module in src/. Use for new files or substantial rewrites.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="edit_file",
        description="Replace one exact, unique occurrence of old_string with new_string in a module.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
            },
            "required": ["path", "old_string", "new_string"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="delete_file",
        description="Delete a module from src/ (not scene.js).",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="render_views",
        description=(
            "Render the current scene headless from the given named views and return the screenshots plus any "
            "JavaScript errors. Views: "
            + ", ".join(VALID_VIEWS)
            + " or a custom view returned by buildScene."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "views": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 6,
                },
                "quality": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "default medium (fast); use high only for a final look",
                },
            },
            "required": ["views"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="check_scene",
        description="Load the scene headless and report JavaScript errors without returning images (fast).",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ToolSpec(
        name="finish",
        description="End your turn. Call only after check_scene reports zero errors.",
        input_schema={
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "What the scene contains and known deviations.",
                }
            },
            "required": ["summary"],
            "additionalProperties": False,
        },
    ),
]


class BuilderTools:
    def __init__(
        self,
        workspace: Workspace,
        renderer: Renderer,
        scene_url: str,
        renders_dir: Path,
        on_render: Callable[[dict[str, Path], list[str]], Awaitable[None]] | None = None,
    ) -> None:
        self.ws = workspace
        self.renderer = renderer
        self.scene_url = scene_url
        self.renders_dir = renders_dir
        self.on_render = on_render
        self.last_render_errors: list[str] = []
        self.rendered_views: set[str] = set()
        self.last_check_ok = False
        self.handlers: dict[str, Handler] = {
            "list_files": self.list_files,
            "read_file": self.read_file,
            "write_file": self.write_file,
            "edit_file": self.edit_file,
            "delete_file": self.delete_file,
            "render_views": self.render_views,
            "check_scene": self.check_scene,
        }

    async def call(self, name: str, args: dict[str, Any]) -> ToolOutput:
        handler = self.handlers.get(name)
        if handler is None:
            return [TextPart(text=f"unknown tool {name}")], True
        try:
            return await handler(args)
        except WorkspaceError as e:
            return [TextPart(text=f"workspace error: {e}")], True
        except RenderError as e:
            return [TextPart(text=f"render error: {e}")], True
        except Exception as e:  # keep the loop alive; the model can react
            logger.exception("tool.failed", extra={"tool": name})
            return [TextPart(text=f"tool {name} failed: {e}")], True

    async def list_files(self, _: dict[str, Any]) -> ToolOutput:
        files = self.ws.list_files()
        return [
            TextPart(
                text="\n".join(
                    f"{f['path']}  ({f['lines']} lines){'  [read-only]' if f.get('readonly') else ''}"
                    for f in files
                )
                or "(empty)"
            )
        ], False

    async def read_file(self, a: dict[str, Any]) -> ToolOutput:
        return [TextPart(text=self.ws.read(str(a["path"])))], False

    async def write_file(self, a: dict[str, Any]) -> ToolOutput:
        self.last_check_ok = False
        return [TextPart(text=self.ws.write(str(a["path"]), str(a["content"])))], False

    async def edit_file(self, a: dict[str, Any]) -> ToolOutput:
        self.last_check_ok = False
        return [
            TextPart(text=self.ws.edit(str(a["path"]), str(a["old_string"]), str(a["new_string"])))
        ], False

    async def delete_file(self, a: dict[str, Any]) -> ToolOutput:
        self.last_check_ok = False
        return [TextPart(text=self.ws.delete(str(a["path"])))], False

    async def render_views(self, a: dict[str, Any]) -> ToolOutput:
        views = [str(v) for v in a.get("views", [])][:6] or ["southeast"]
        # medium is plenty to judge massing and openings and renders 2-3x faster than high;
        # the version snapshot the user and the critic see is rendered at high by the pipeline
        quality = str(a.get("quality") or "medium")
        res = await self.renderer.render(self.scene_url, views, self.renders_dir, quality=quality)
        self.last_render_errors = res.errors
        self.rendered_views.update(res.images)
        if self.on_render:
            await self.on_render(res.images, res.errors)
        content: list[TextPart | ImagePart] = []
        if res.errors:
            content.append(TextPart(text="JavaScript errors:\n" + "\n".join(res.errors)))
        if res.console:
            content.append(
                TextPart(text="Console warnings/errors:\n" + "\n".join(res.console[:20]))
            )
        for view, path in res.images.items():
            content.append(ImagePart.from_file(path, label=f"Render — view '{view}'"))
        if not res.images:
            content.append(TextPart(text="No image could be produced."))
        return content, bool(res.errors) and not res.images

    async def check_scene(self, _: dict[str, Any]) -> ToolOutput:
        res = await self.renderer.render(self.scene_url, [], self.renders_dir, quality="low")
        self.last_render_errors = res.errors
        self.last_check_ok = not res.errors
        if res.errors:
            return [TextPart(text="Errors:\n" + "\n".join(res.errors))], True
        return [TextPart(text="OK — scene loaded with no errors.")], False
