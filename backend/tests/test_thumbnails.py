"""Extras as thumbnails in the builder's first message; full photos on demand (#5)."""

from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

from housegen.agent.pipeline import EXTRA_THUMB_PX, _first_message, _Inputs
from housegen.agent.tools import BuilderTools, ImageSources
from housegen.agent.workspace import Workspace
from housegen.llm.types import ImagePart, thumbnail_size


def _jpeg(path: Path, w: int, h: int, color: tuple[int, int, int]) -> Path:
    img = Image.new("RGB", (w, h), color)
    # some texture so JPEG does not compress everything to nothing
    px = img.load()
    assert px is not None
    for x in range(0, w, 7):
        for y in range(0, h, 5):
            px[x, y] = (255 - color[0], color[1], 255 - color[2])
    img.save(path, "JPEG", quality=88)
    return path


def _photos(tmp_path: Path) -> tuple[dict[str, Path], list[Path]]:
    d = tmp_path / "photos"
    (d / "orig").mkdir(parents=True)
    facades = {
        side: _jpeg(d / f"{side}.jpg", 1600, 1200, (100 + i * 20, 120, 90))
        for i, side in enumerate(("north", "south", "east", "west"))
    }
    extras = []
    for i in range(34):
        p = _jpeg(d / f"other{i}.jpg", 1600, 1200, (90, 100 + i, 130))
        _jpeg(d / "orig" / p.name, 4000, 3000, (90, 100 + i, 130))  # the original, kept for crops
        extras.append(p)
    return facades, extras


def test_first_message_sends_extras_as_thumbnails_and_facades_in_full(tmp_path: Path) -> None:
    facades, extras = _photos(tmp_path)
    inp = _Inputs(facades, extras, [], "", None)
    parts = _first_message(inp)
    images = [p for p in parts if isinstance(p, ImagePart)]
    assert len(images) == 38
    facade_imgs = [p for p in images if (p.label or "").startswith("Photograph of the")]
    extra_imgs = [p for p in images if (p.label or "").startswith("Additional photograph")]
    assert len(facade_imgs) == 4
    assert len(extra_imgs) == 34
    for p in facade_imgs:
        assert p.size == (1600, 1200)
        assert p.detail == "high"
    for p in extra_imgs:
        assert max(p.size) <= EXTRA_THUMB_PX
        assert p.detail == "low"
    assert "'extra-7'" in (extra_imgs[6].label or "")
    # the message's image bytes are under a quarter of what sending everything in full costs
    total = sum(len(base64.b64decode(p.data)) for p in images)
    full = sum(p.stat().st_size for p in [*facades.values(), *extras])
    assert total < full / 4, (total, full)


async def test_inspect_image_returns_the_full_extra_from_thumbnail_coordinates(
    tmp_path: Path,
) -> None:
    facades, extras = _photos(tmp_path)
    ws = Workspace(tmp_path / "scene")
    tools = BuilderTools(ws, None, "http://x", tmp_path / "renders")  # type: ignore[arg-type]
    tools.images = ImageSources(facades, extras, thumb_px=EXTRA_THUMB_PX)
    tw, th = thumbnail_size(extras[6], EXTRA_THUMB_PX)
    assert (tw, th) == (256, 192)
    # the whole thumbnail → the full photo from the 4000 px original (capped at 1600 for the model)
    content, is_error = await tools.inspect_image(
        {"name": "extra-7", "x": 0, "y": 0, "w": tw, "h": th}
    )
    assert not is_error
    img = content[0]
    assert isinstance(img, ImagePart)
    assert "source 4000x3000" in (img.label or "")
    assert img.size == (1600, 1200)
    # a quarter of the thumbnail → a quarter of the original, at native resolution
    content, _ = await tools.inspect_image(
        {"name": "extra-7", "x": 0, "y": 0, "w": tw // 2, "h": th // 2}
    )
    img = content[0]
    assert isinstance(img, ImagePart)
    with Image.open(io.BytesIO(base64.b64decode(img.data))) as im:
        assert im.size == (1600, 1200)  # 2000x1500 of the original, capped to 1600 wide
    assert "scale 15.6x" in (img.label or "")
    # a labelled façade keeps working-copy coordinates
    content, _ = await tools.inspect_image({"name": "north", "x": 0, "y": 0, "w": 800, "h": 600})
    assert "source 1600x1200" in (content[0].label or "")  # type: ignore[union-attr]
