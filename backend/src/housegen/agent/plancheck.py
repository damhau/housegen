"""The plan check of the interior builder: lay the model's floor section over the plan sheet.

The runtime draws a storey as a floor plan (the `plan-section-<n>` view: seen from above, cut at
1.2 m, exterior walls dark blue, partitions red, door leaves green, furniture as orange footprints)
at a framing in metres it reports. Here that section is registered on the sheet (the scale from the
sheet's resolution and the usual plan scales, the position by cross-correlating the model's walls
with the sheet's dark lines, the scale refined by a few percent) and drawn over it: red where the
model has walls. The coverage says how much of the model's walls lies on drawn lines.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

INCH = 0.0254
SCALES = (100, 50, 200)  # the usual scales of floor plans, most likely first


@dataclass
class Registration:
    ppm: float  # sheet pixels per metre
    scale: int  # 1:scale for the sheet's resolution
    x0: float  # sheet pixel of the section's left edge (its bbox x0)
    y0: float  # sheet pixel of the section's top edge (its bbox z0)
    coverage: float  # share of the model's wall pixels within ~5 cm of a dark sheet pixel
    score: float  # the correlation peak, for comparing candidates


def scale_from_label(label: str) -> int | None:
    """'Ground floor — 1:100' → 100."""
    m = re.search(r"1\s*:\s*(\d{2,4})", label or "")
    return int(m.group(1)) if m else None


def wall_mask(section: Image.Image) -> np.ndarray:
    """The model's walls and partitions in a plan-section image (dark blue, red)."""
    a = np.asarray(section.convert("RGB")).astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    blue = (b > 100) & (r < 90) & (g < 110)
    red = (r > 150) & (g < 90) & (b < 90)
    return blue | red


def dark_mask(sheet: Image.Image, threshold: int = 205) -> np.ndarray:
    return np.asarray(sheet.convert("L")) < threshold


def _resize_mask(mask: np.ndarray, factor: float) -> np.ndarray:
    h, w = mask.shape
    img = Image.fromarray(mask.astype(np.uint8) * 255)
    size = (max(1, round(w * factor)), max(1, round(h * factor)))
    return np.asarray(img.resize(size, Image.Resampling.BILINEAR)) > 96


def _blur(mask: np.ndarray, radius: float) -> np.ndarray:
    img = Image.fromarray(mask.astype(np.uint8) * 255).filter(ImageFilter.GaussianBlur(radius))
    return np.asarray(img).astype(np.float32) / 255.0


def _correlate(sheet: np.ndarray, template: np.ndarray) -> tuple[float, int, int]:
    """Best placement (score, x, y) of `template` inside `sheet`, by FFT cross-correlation."""
    sh, sw = sheet.shape
    th, tw = template.shape
    if th > sh or tw > sw:
        return 0.0, 0, 0
    fh, fw = 1 << (sh + th).bit_length(), 1 << (sw + tw).bit_length()
    fs = np.fft.rfft2(sheet, (fh, fw))
    ft = np.fft.rfft2(template[::-1, ::-1], (fh, fw))
    corr = np.fft.irfft2(fs * ft, (fh, fw))
    # valid placements only: the template fully inside the sheet
    valid = corr[th - 1 : sh, tw - 1 : sw]
    idx = int(np.argmax(valid))
    y, x = divmod(idx, valid.shape[1])
    return float(valid[y, x]) / max(1.0, float(template.sum())), x, y


def register(
    section: Image.Image,
    bbox: tuple[float, float, float, float],
    sheet: Image.Image,
    dpi: float,
    scale_hint: int | None = None,
) -> Registration | None:
    """Where the section lies on the sheet, or None when nothing matches."""
    model = wall_mask(section)
    if model.sum() < 50:
        return None
    x0, _, x1, _ = bbox
    model_ppm = section.width / (x1 - x0)
    dark = dark_mask(sheet)
    scales = [scale_hint] if scale_hint else list(SCALES)

    def search(ppm: float, down: int) -> tuple[float, int, int]:
        tmpl = _resize_mask(model, ppm / model_ppm / down)
        target = _blur(_resize_mask(dark, 1 / down), 1.2)
        return _correlate(target, tmpl.astype(np.float32))

    # coarse: each plan scale at a quarter resolution; then the scale refined by ±3 % at half
    best: tuple[float, float, int] | None = None  # (score, ppm, scale)
    for sc in scales:
        ppm = dpi / INCH / sc
        score, _, _ = search(ppm, 4)
        if best is None or score > best[0]:
            best = (score, ppm, sc)
    assert best is not None
    _, ppm0, sc = best
    fine: tuple[float, float, int, int] | None = None
    for f in (0.97, 0.98, 0.99, 1.0, 1.01, 1.02, 1.03):
        score, x, y = search(ppm0 * f, 2)
        if fine is None or score > fine[0]:
            fine = (score, ppm0 * f, x, y)
    assert fine is not None
    score, ppm, x, y = fine
    # exact position at full resolution, near the half-resolution one
    tmpl = _resize_mask(model, ppm / model_ppm)
    th, tw = tmpl.shape
    pad = 6
    ys, xs = max(0, 2 * y - pad), max(0, 2 * x - pad)
    window = _blur(dark[ys : ys + th + 2 * pad, xs : xs + tw + 2 * pad], 1.0)
    _, dx, dy = _correlate(window, tmpl.astype(np.float32))
    px, py = xs + dx, ys + dy
    near = _dilate(dark[py : py + th, px : px + tw], 3)
    placed = tmpl[: near.shape[0], : near.shape[1]]
    coverage = float((placed & near).sum()) / max(1, int(placed.sum()))
    return Registration(ppm=ppm, scale=sc, x0=px, y0=py, coverage=coverage, score=score)


def _dilate(mask: np.ndarray, r: int) -> np.ndarray:
    img = Image.fromarray(mask.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(2 * r + 1))
    return np.asarray(img) > 0


def overlay(
    section: Image.Image,
    sheet: Image.Image,
    reg: Registration,
    bbox: tuple[float, float, float, float],
) -> bytes:
    """The sheet around the storey, faded, with the model drawn over it (JPEG): its walls and
    partitions red, its door leaves green, its furniture footprints orange."""
    x0, z0, x1, z1 = bbox
    w, h = round((x1 - x0) * reg.ppm), round((z1 - z0) * reg.ppm)
    model = section.convert("RGB").resize((w, h), Image.Resampling.NEAREST)
    left, top = round(reg.x0), round(reg.y0)
    base = sheet.convert("L").crop((left, top, left + w, top + h))
    faded = np.asarray(base).astype(np.float32)
    faded = 255 - (255 - faded) * 0.55
    out = np.stack([faded] * 3, -1)
    m = np.asarray(model).astype(np.int16)
    r, g, b = m[..., 0], m[..., 1], m[..., 2]
    walls = wall_mask(model)
    doors = (g > 120) & (r < 90) & (b < 90)
    furniture = (r > 200) & (g > 120) & (g < 220) & (b < 190) & ~walls
    out[furniture] = out[furniture] * 0.6 + np.array([255, 150, 40]) * 0.4
    out[walls] = out[walls] * 0.35 + np.array([230, 20, 20]) * 0.65
    out[doors] = np.array([20, 160, 20])
    img = Image.fromarray(out.clip(0, 255).astype(np.uint8))
    if max(img.size) > 1600:
        img.thumbnail((1600, 1600))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def sheet_dpi(png: Path, pdf: Path, page: int) -> float | None:
    """The resolution the sheet image was rasterised at, from its PDF page's size."""
    try:
        import pymupdf

        doc = pymupdf.open(pdf)  # type: ignore[no-untyped-call]
        try:
            width_pt = float(doc[page - 1].rect.width)
        finally:
            doc.close()  # type: ignore[no-untyped-call]
        with Image.open(png) as im:
            return im.width / (width_pt / 72)
    except Exception:
        return None
