"""Builder tools: JSON schemas + handlers bound to a workspace and the renderer."""

from __future__ import annotations

import io
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from housegen.agent.workspace import Workspace, WorkspaceError
from housegen.core.exceptions import RenderError
from housegen.llm.types import ImagePart, TextPart, ToolSpec, thumbnail_size
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
    "north-photo",
    "south-photo",
    "east-photo",
    "west-photo",
    "north-elevation",
    "south-elevation",
    "east-elevation",
    "west-elevation",
]
CAMERA_OVERRIDES = ("eye_height", "distance", "azimuth", "fov", "target_height")

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
        description=(
            "Replace one exact, unique occurrence of old_string with new_string in a module. For a "
            "single tiny change; use apply_patch for several edits or several files at once."
        ),
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
        name="apply_patch",
        description=(
            "Apply several edits across one or more modules in ONE call, in the patch grammar:\n"
            "*** Begin Patch\n*** Update File: src/shell.js\n@@ optional anchor line\n context line\n"
            "-old line\n+new line\n*** Add File: src/entrance.js\n+every line of the new file prefixed with +\n"
            "*** Delete File: src/old.js\n*** End Patch\n"
            "Hunks are located by their context lines (exact, then ignoring whitespace). The whole "
            "patch is rejected on the first hunk that does not apply and nothing is written; the "
            "error names the hunk. Prefer this over several edit_file calls: decide all the changes "
            "for a round, apply them in one patch, then render. Paths: src/*.js only."
        ),
        input_schema={
            "type": "object",
            "properties": {"patch": {"type": "string"}},
            "required": ["patch"],
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
            + " or a custom view returned by buildScene. north/south/east/west are elevated wide shots "
            "(eye about 4 m, whole building in frame): use them for massing and roof shape. The '-photo' "
            "views stand at 1.6 m in front of the façade like the photographer: use them to compare "
            "heights, sill/lintel levels, roof visibility and overhangs with a photo. The '-elevation' "
            "views are straight-on and near-orthographic, like an architect's elevation drawing: use them "
            "to compare a façade with its elevation sheet. The optional camera parameters apply to every "
            "side view of this call and replace the preset (aerial/top/elevation/custom views ignore "
            "them): use them to reproduce a photo's viewpoint."
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
                "eye_height": {
                    "type": "number",
                    "description": "camera height in metres above the house base (a person: 1.6)",
                },
                "distance": {
                    "type": "number",
                    "description": "distance from the house centre as a factor of the auto-framing radius (1 = preset; 0.5 = twice as close)",
                },
                "azimuth": {
                    "type": "number",
                    "description": "camera direction in degrees, 0 = looking at the north façade, 90 = at the east façade (clockwise)",
                },
                "fov": {
                    "type": "number",
                    "description": "vertical field of view in degrees (phone about 50 to 60)",
                },
                "target_height": {
                    "type": "number",
                    "description": "height in metres above the house base the camera looks at",
                },
            },
            "required": ["views"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="inspect_image",
        description=(
            "Zoom: return a region of a photo, plan sheet or render at native resolution "
            "(plan sheets are re-rendered from the PDF at 300 dpi). name = a photo label "
            "('north', 'south', 'east', 'west', 'extra-3', 'attached-1' for a photo attached to the "
            "request), a plan sheet ('plan-2') or a render view "
            "('render-north'). x, y, w, h are pixel coordinates in the image as you received it "
            "(for the additional photographs that is the small thumbnail: give the whole thumbnail "
            "to see the photo in full); the result says the crop's scale. Use it to read dimension "
            "strings on the plans and small façade details (window divisions, shutters, cladding "
            "lines) instead of guessing."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "x": {"type": "integer", "minimum": 0},
                "y": {"type": "integer", "minimum": 0},
                "w": {"type": "integer", "minimum": 8},
                "h": {"type": "integer", "minimum": 8},
            },
            "required": ["name", "x", "y", "w", "h"],
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
                },
                "suggestions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 8,
                    "description": (
                        "Optional additions you saw in the photos and deliberately left out, one "
                        "concrete item each, phrased as what you saw ('the blue car on the west "
                        "driveway', 'the hedge on the street side'). The user picks the ones they want."
                    ),
                },
                "questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 4,
                    "description": (
                        "Questions for the owner where the plans and photos left you guessing "
                        "('the north and south photo labels disagree with the plan; which is right?'). "
                        "Say what you assumed meanwhile."
                    ),
                },
            },
            "required": ["summary"],
            "additionalProperties": False,
        },
    ),
]


class ImageSources:
    """Where inspect_image finds the images the model has seen (photos by label, plan sheets).

    `thumb_px`: the additional photographs went into the conversation as thumbnails of that
    size (#5), so the model's coordinates for them are thumbnail coordinates.
    """

    def __init__(
        self,
        photos: dict[str, Path] | None = None,
        extras: list[Path] | None = None,
        plan_pages: list[Path] | None = None,
        plan_pdf: Path | None = None,
        attached: list[Path] | None = None,
        thumb_px: int | None = None,
    ) -> None:
        self.photos = dict(photos or {})
        self.thumbs: set[str] = set()
        for i, p in enumerate(extras or [], 1):
            self.photos[f"extra-{i}"] = p
            if thumb_px:
                self.thumbs.add(f"extra-{i}")
        for i, p in enumerate(attached or [], 1):
            self.photos[f"attached-{i}"] = p
        self.plan_pages = list(plan_pages or [])
        self.plan_pdf = plan_pdf
        self.thumb_px = thumb_px

    def photo_names(self) -> list[str]:
        return list(self.photos)

    def photo(self, name: str) -> Path | None:
        return self.photos.get(name)

    def shown_size(self, name: str, path: Path) -> tuple[int, int]:
        """The pixel size the model saw this photo at (thumbnail or working copy)."""
        if name in self.thumbs and self.thumb_px:
            return thumbnail_size(path, self.thumb_px)
        from PIL import Image

        with Image.open(path) as im:
            return im.size

    def plan_page(self, page: int) -> Path | None:
        if 1 <= page <= len(self.plan_pages):
            return self.plan_pages[page - 1]
        return None

    def plan_clip(self, page: int, x: int, y: int, w: int, h: int, shown: Path) -> ImagePart | None:
        """Re-render a region of the PDF page at 300 dpi (dimension strings become readable)."""
        if self.plan_pdf is None or not self.plan_pdf.exists():
            return None
        import pymupdf
        from PIL import Image

        with Image.open(shown) as im:
            sw, sh = im.size
        doc = pymupdf.open(self.plan_pdf)  # type: ignore[no-untyped-call]
        try:
            pg = doc[page - 1]
            rect = pg.rect
            fx, fy = rect.width / sw, rect.height / sh
            clip = pymupdf.Rect(x * fx, y * fy, (x + w) * fx, (y + h) * fy) & rect  # type: ignore[no-untyped-call]
            if clip.is_empty:
                return None
            zoom = 300 / 72
            pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)  # type: ignore[no-untyped-call]
            data = pix.tobytes("png")  # type: ignore[no-untyped-call]
        finally:
            doc.close()  # type: ignore[no-untyped-call]
        scale = 300 / (72 / fx) if fx else 1.0
        return ImagePart.from_bytes(
            data,
            "image/png",
            label=f"{shown.stem} region x={x} y={y} w={w} h={h} at 300 dpi (scale {scale:.1f}x)",
        )


def crop_image(
    shown: Path | tuple[int, int], source: Path, x: int, y: int, w: int, h: int, label: str
) -> list[TextPart | ImagePart]:
    """Crop `source` (the original, possibly larger than what was shown) using coordinates in
    the image as shown: `shown` is that image's path, or its pixel size."""
    from PIL import Image, ImageOps

    if isinstance(shown, tuple):
        sw, sh = shown
    else:
        with Image.open(shown) as im:
            sw, sh = im.size
    with Image.open(source) as raw:
        src: Image.Image = ImageOps.exif_transpose(raw) or raw
        fx, fy = src.width / sw, src.height / sh
        box = (
            max(0, int(x * fx)),
            max(0, int(y * fy)),
            min(src.width, int((x + w) * fx)),
            min(src.height, int((y + h) * fy)),
        )
        if box[2] - box[0] < 4 or box[3] - box[1] < 4:
            return [TextPart(text=f"region outside the image ({sw}x{sh})")]
        crop = src.crop(box).convert("RGB")
        if max(crop.size) > 1600:
            crop.thumbnail((1600, 1600))
        buf = io.BytesIO()
        crop.save(buf, "JPEG", quality=90)
    return [
        ImagePart.from_bytes(
            buf.getvalue(),
            "image/jpeg",
            label=f"{label} region x={x} y={y} w={w} h={h} (source {src.width}x{src.height}, scale {fx:.1f}x)",
        )
    ]


class BuilderTools:
    def __init__(
        self,
        workspace: Workspace,
        renderer: Renderer,
        scene_url: str,
        renders_dir: Path,
        on_render: Callable[[dict[str, Path], list[str]], Awaitable[None]] | None = None,
        images: ImageSources | None = None,
    ) -> None:
        self.ws = workspace
        self.images = images or ImageSources()
        self.renderer = renderer
        self.scene_url = scene_url
        self.renders_dir = renders_dir
        self.on_render = on_render
        self.last_render_errors: list[str] = []
        self.last_audit: list[str] = []
        self.rendered_views: set[str] = set()
        self.last_check_ok = False
        self.render_ms_total = 0  # every render this instance ran (per-turn deltas, #13)
        self.default_quality = "medium"  # in-loop renders when the model gives no quality (#18)
        self.handlers: dict[str, Handler] = {
            "list_files": self.list_files,
            "read_file": self.read_file,
            "write_file": self.write_file,
            "edit_file": self.edit_file,
            "apply_patch": self.apply_patch,
            "delete_file": self.delete_file,
            "render_views": self.render_views,
            "inspect_image": self.inspect_image,
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

    async def apply_patch(self, a: dict[str, Any]) -> ToolOutput:
        self.last_check_ok = False
        return [TextPart(text=self.ws.apply_patch(str(a["patch"])))], False

    async def delete_file(self, a: dict[str, Any]) -> ToolOutput:
        self.last_check_ok = False
        return [TextPart(text=self.ws.delete(str(a["path"])))], False

    async def render_views(self, a: dict[str, Any]) -> ToolOutput:
        views = [str(v) for v in a.get("views", [])][:6] or ["southeast"]
        # medium is plenty to judge massing and openings and renders 2-3x faster than high;
        # the version snapshot the user and the critic see is rendered at high by the pipeline
        quality = str(a.get("quality") or self.default_quality)
        camera = {k: float(a[k]) for k in CAMERA_OVERRIDES if a.get(k) is not None}
        res = await self.renderer.render(
            self.scene_url, views, self.renders_dir, quality=quality, camera=camera or None
        )
        self.last_render_errors = res.errors
        self.last_audit = res.audit
        self.render_ms_total += res.duration_ms
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
        if res.audit:
            content.append(TextPart(text=audit_text(res.audit)))
        for view, path in res.images.items():
            content.append(ImagePart.from_file(path, label=f"Render — view '{view}'"))
        if not res.images:
            content.append(TextPart(text="No image could be produced."))
        return content, bool(res.errors) and not res.images

    async def inspect_image(self, a: dict[str, Any]) -> ToolOutput:
        name = str(a["name"]).strip().lower()
        x, y, w, h = (int(a[k]) for k in ("x", "y", "w", "h"))
        if name.startswith("render-"):
            src = self.renders_dir / f"{name.removeprefix('render-')}.jpg"
            if not src.exists():
                return [TextPart(text=f"no render named '{name}'; render it first")], True
            return [*crop_image(src, src, x, y, w, h, label=name)], False
        if name.startswith("plan-"):
            try:
                page = int(name.removeprefix("plan-"))
            except ValueError:
                return [TextPart(text="plan sheets are named plan-1, plan-2, …")], True
            shown = self.images.plan_page(page)
            if shown is None:
                return [TextPart(text=f"no plan sheet {page}")], True
            hi = self.images.plan_clip(page, x, y, w, h, shown)
            if hi is not None:
                return [hi], False
            return [*crop_image(shown, shown, x, y, w, h, label=name)], False
        shown = self.images.photo(name)
        if shown is None:
            return [
                TextPart(
                    text=f"no photo named '{name}'. Photos: {', '.join(self.images.photo_names())}"
                )
            ], True
        orig = shown.parent / "orig" / shown.name
        size = self.images.shown_size(name, shown)
        return [*crop_image(size, orig if orig.exists() else shown, x, y, w, h, label=name)], False

    async def check_scene(self, _: dict[str, Any]) -> ToolOutput:
        res = await self.renderer.render(self.scene_url, [], self.renders_dir, quality="low")
        self.last_render_errors = res.errors
        self.last_audit = res.audit
        self.render_ms_total += res.duration_ms
        self.last_check_ok = not res.errors
        if res.errors:
            return [TextPart(text="Errors:\n" + "\n".join(res.errors))], True
        if res.audit:
            return [
                TextPart(text="OK — scene loaded with no errors.\n" + audit_text(res.audit))
            ], False
        return [
            TextPart(text="OK — scene loaded with no errors; the plausibility audit found nothing.")
        ], False


def audit_text(lines: list[str]) -> str:
    """The runtime's plausibility findings as one block for a tool result or the critic."""
    return "Plausibility audit (deterministic, from bounding boxes; fix every line):\n" + "\n".join(
        f"- {x}" for x in lines
    )
