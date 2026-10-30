"""Blind pairs for the judges: ours scaled to the reference's size, both re-saved as JPEG q92, A/B at random.

    judge_prep.py <ours.png> <reference.jpg> <round dir> <n judges>   -> <round>/j<k>/{A,B}.jpg, <round>/key.json
"""

import json
import random
import sys
from pathlib import Path

from PIL import Image

ours, ref, out, n = sys.argv[1], sys.argv[2], Path(sys.argv[3]), int(sys.argv[4])
r = Image.open(ref).convert("RGB")
o = Image.open(ours).convert("RGB")
o = o.resize((r.width, round(o.height * r.width / o.width)), Image.Resampling.LANCZOS)
top = max(0, (o.height - r.height) // 2)
o = o.crop((0, top, r.width, top + r.height))
key = {}
for k in range(1, n + 1):
    d = out / f"j{k}"
    d.mkdir(parents=True, exist_ok=True)
    a_is_ours = random.random() < 0.5
    (o if a_is_ours else r).save(d / "A.jpg", quality=92)
    (r if a_is_ours else o).save(d / "B.jpg", quality=92)
    key[f"j{k}"] = "A" if a_is_ours else "B"
(out / "key.json").write_text(json.dumps(key))
print(out, "ready")
