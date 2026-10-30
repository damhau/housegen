"""Images side by side with labels: sheet.py <out.jpg> <cols> <width per image> <label=path> ..."""

import sys

from PIL import Image, ImageDraw

out, cols, w = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
items = [a.split("=", 1) for a in sys.argv[4:]]
ims = []
for label, path in items:
    im = Image.open(path).convert("RGB")
    ims.append((label, im.resize((w, round(im.height * w / im.width)), Image.Resampling.LANCZOS)))
h = max(im.height for _, im in ims) + 22
rows = (len(ims) + cols - 1) // cols
sheet = Image.new("RGB", (cols * w + (cols - 1) * 8, rows * h), "white")
d = ImageDraw.Draw(sheet)
for k, (label, im) in enumerate(ims):
    x, y = (k % cols) * (w + 8), (k // cols) * h
    d.text((x + 2, y + 4), label, fill="black")
    sheet.paste(im, (x, y + 22))
sheet.save(out, quality=90)
