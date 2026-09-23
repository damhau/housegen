"""Score a plan-section render against the sheet's wall poché, and draw the overlay.

truth = sheet pixels of the wall fill (grey 191 = masonry, 228 = light partitions)
model = render pixels (blue = exterior walls, red = partitions)
recall@d    = share of the truth wall pixels within d of a model wall pixel
precision@d = share of the model wall pixels within d of a truth wall pixel
"""
import json, sys
import numpy as np
from PIL import Image
from scipy.ndimage import binary_closing, distance_transform_edt

sheet, render, out = sys.argv[1], sys.argv[2], sys.argv[3]
S, X0, Z0 = 58.82, 1136, 554          # sheet px per metre, px of the outer NW corner (-5.5, -5.0)
bx0, bz0, bx1, bz1 = -6, -5.5, 6, 5.5  # region rendered by plan.html
r = np.asarray(Image.open(render).convert("RGBA")).astype(int)
H, W = r.shape[:2]
sx0 = X0 + (bx0 + 5.5) * S; sz0 = Z0 + (bz0 + 5.0) * S
sh = Image.open(sheet).convert("L").transform((W, H), Image.Transform.EXTENT, (sx0, sz0, sx0 + W, sz0 + H), Image.Resampling.NEAREST)
a = np.asarray(sh).astype(int)
truth = (a == 191) | (a == 228)
# dimension labels punch white boxes (~0.35 m) into the poché: close gaps under 0.4 m along
# each axis (openings are 0.6 m and more)
k = int(0.4 * S)
truth = binary_closing(truth, np.ones((1, k), bool)) | binary_closing(truth, np.ones((k, 1), bool))
# score inside the house footprint only (window wells and the garage are outside)
zz, xx = np.mgrid[0:H, 0:W]
inside = (xx / S + bx0 >= -5.52) & (xx / S + bx0 <= 5.52) & (zz / S + bz0 >= -5.02) & (zz / S + bz0 <= 5.02)
truth &= inside
opaque = r[..., 3] > 0
m_ext = opaque & (r[..., 2] > 200) & (r[..., 0] < 60)
m_par = opaque & (r[..., 0] > 200) & (r[..., 1] < 60)
m_ext &= inside; m_par &= inside
model = m_ext | m_par
dt_model = distance_transform_edt(~model) / S  # metres to the nearest model wall pixel
dt_truth = distance_transform_edt(~truth) / S
res = {}
for d in (0.03, 0.05, 0.10):
    res[f"recall@{int(d*100)}cm"] = round(float((dt_model[truth] <= d).mean()), 3)
    res[f"precision@{int(d*100)}cm"] = round(float((dt_truth[model] <= d).mean()), 3)
res["exterior precision@5cm"] = round(float((dt_truth[m_ext] <= 0.05).mean()), 3)
res["partition precision@5cm"] = round(float((dt_truth[m_par] <= 0.05).mean()), 3)
res["truth m2"] = round(truth.sum() / S / S, 2); res["model m2"] = round(model.sum() / S / S, 2)
print(json.dumps(res))

# per partition (from the scene's plan data): the shift across its line that best fits the sheet
if len(sys.argv) > 4:
    parts = json.load(open(sys.argv[4]))
    tm = truth
    for i, p in enumerate(parts):
        (fx, fz), (tx, tz) = p["from"], p["to"]
        L = max(1e-6, ((tx - fx) ** 2 + (tz - fz) ** 2) ** 0.5)
        ux, uz = (tx - fx) / L, (tz - fz) / L; nx, nz = -uz, ux
        t = p.get("thickness", 0.1)
        offs = []; covered = n = 0
        for s_ in np.arange(0.05, L - 0.05, 0.02):
            if any(o["offset"] - 0.03 <= s_ <= o["offset"] + o["width"] + 0.03 for o in p.get("openings", [])):
                continue
            n += 1
            hits = []
            for k2 in range(-15, 16):
                x = fx + ux * s_ + nx * k2 * 0.01; z = fz + uz * s_ + nz * k2 * 0.01
                hits.append(bool(tm[int((z - bz0) * S), int((x - bx0) * S)]))
            runs, st = [], None
            for i2, h in enumerate(hits + [False]):
                if h and st is None: st = i2
                if not h and st is not None: runs.append((st, i2 - 1)); st = None
            if runs:
                a_, b_ = min(runs, key=lambda r_: abs((r_[0] + r_[1]) / 2 - 15))
                covered += 1
                offs.append((a_ + b_) / 2 * 0.01 - 0.15)
        cov = covered / max(1, n)
        med = float(np.median(offs)) if offs else float("nan")
        side = ("south" if nz * med > 0 else "north") if abs(nz) > 0.5 else ("east" if nx * med > 0 else "west")
        flag = "" if cov > 0.8 and abs(med) <= 0.03 else "  <--"
        print(f"partition {i:2d} {p['from']}→{p['to']} t={t}: on the sheet along {cov:4.0%} of its length, centre {abs(med):.2f} m {side}{flag}")

# overlay: sheet faded; agreement dark grey; truth only = orange; model only = magenta
base = np.stack([a] * 3, -1).astype(float)
base = 255 - (255 - base) * 0.35
ok_t = truth & (dt_model <= 0.05); miss = truth & (dt_model > 0.05)
extra = model & (dt_truth > 0.05)
base[ok_t] = (110, 110, 110)
base[miss] = (255, 140, 0)
base[extra] = (230, 0, 200)
d = r[..., 1] > 150  # door leaves (green)
base[d & opaque & (r[..., 0] < 60)] = (0, 160, 0)
img = Image.fromarray(base.astype(np.uint8)).resize((W * 2, H * 2), Image.Resampling.NEAREST)
img.save(out)
