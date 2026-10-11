"""Benchmark | before | after, one row per room (pilot comparison sheet)."""
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

out, cols, rows = Path(sys.argv[1]), sys.argv[2].split(","), [r.split(",") for r in sys.argv[3:]]
W, H, top = 640, 480, 34
font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
sheet = Image.new("RGB", (W * len(cols), top + (H + top) * len(rows)), "white")
d = ImageDraw.Draw(sheet)
for i, c in enumerate(cols):
    d.text((i * W + 10, 8), c, fill="black", font=font)
for j, row in enumerate(rows):
    label, files = row[0], row[1:]
    y = top + j * (H + top)
    d.text((10, y + 6), label, fill="black", font=font)
    for i, f in enumerate(files):
        im = Image.open(f).convert("RGB")
        # cover-crop to W x H
        s = max(W / im.width, H / im.height)
        im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
        x0, y0 = (im.width - W) // 2, (im.height - H) // 2
        sheet.paste(im.crop((x0, y0, x0 + W, y0 + H)), (i * W, y + top))
sheet.save(out, quality=90)
print(out)
