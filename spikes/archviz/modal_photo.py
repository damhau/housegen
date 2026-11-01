"""photo.py on Modal: the same script on a cloud GPU (a T4 by default, the render service's), one container per
photo, in parallel. A test harness: `modal run` makes a temporary app, nothing stays deployed.

    cd spikes/archviz && uvx modal run modal_photo.py [--rooms dining,bedroom,bath,living] [--w 2560 --h 1440 --samples 400]
    uvx modal run modal_photo.py --rooms m-living,m-bedroom      # the blind rooms (#89): a phase's start and end only
    PHOTO_GPU=L4 uvx modal run modal_photo.py --rooms bath

Uploads (cached by Modal between runs): assets/ (CC0 scans, the HDRI), made/, the exported houses (work/house.blend and
work/rooms.json for TestVillaGille, work/montelly/ for Montelly49-Dev4) and the bed cache (work/cache). Writes work/auto/<room>.png (+ -preview, -raw) here, and appends the
run to work/ledger.csv (container seconds and an estimate in $ from Modal's list prices; the image build is not counted).
"""

import os
import time

import modal

HERE = os.path.dirname(os.path.abspath(__file__))
GPU = os.environ.get("PHOTO_GPU", "T4")
R = "/root/archviz"
# $ per container second: the GPU + 4 cores + 16 GiB (Modal list prices, 2025)
PRICE = {"T4": 0.000164, "L4": 0.000222, "A10G": 0.000306}.get(GPU, 0.0003) + 4 * 0.0000131 + 16 * 0.00000222
CAMERAS = {  # TestVillaGille (fa8bd00053e6 v21): the hand-tuned rooms' cameras, and the bathroom from its door, aimed past the door leaf
    "dining": "0.6,1.25,2.3>-4.5,1.0,4.15@54",
    "bedroom": "3.5,4.0,1.95>0.6,3.6,4.3@55",
    "bath": "1.35,1.35,-2.3>0.55,0.95,-4.5@60",
}
CAMERAS["living"] = "4.5,1.3,4.1>-1.2,1.0,1.2@58"  # the holdout up to M2 (#88), an iteration room since #89
HOUSES = {"gille": "work", "montelly": "work/montelly"}  # TestVillaGille (fa8bd00053e6 v21), Montelly49-Dev4 (cdc30a72d1ac)
BLIND = {  # Montelly49-Dev4, framed once from the bare export as an owner would (#89); scored, never previewed or tuned on
    "m-living": "0.2,3.65,3.2>4.5,3.35,0.6@58",  # Salon
    "m-bedroom": "0.3,6.65,1.0>4.8,6.3,3.0@58",  # Ch.1
}
ROOMS = {**{n: ("gille", c) for n, c in CAMERAS.items()}, **{n: ("montelly", c) for n, c in BLIND.items()}}

KNOBS = ("CEILING", "TILE_TONE", "MOTTLE", "LIMEWASH", "SKIP", "THUMBS", "SUN_PICK", "PHOTO_SUN_POWER", "PHOTO_LAMPS",
         "RELIEF", "FLOOR_ROUGH", "CLIP_MAX", "FEATURE_TILE", "ADAPTIVE", "PREFILTER", "FLOOR", "STYLE_SKIP", "ART_SCALES", "NUDGE")  # photo.py's environment knobs, forwarded to the containers

app = modal.App("housegen-photo-test")
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("libx11-6", "libxrender1", "libxxf86vm1", "libxfixes3", "libxi6", "libxkbcommon0", "libsm6", "libgl1",
                 "libegl1", "libglib2.0-0")
    .pip_install("bpy==5.0.1", "numpy", "pillow")
    .env({"NVIDIA_DRIVER_CAPABILITIES": "all"})
    .add_local_dir(os.path.join(HERE, "assets"), f"{R}/assets")
    .add_local_dir(os.path.join(HERE, "made"), f"{R}/made")
    .add_local_dir(os.path.join(HERE, "work", "cache"), f"{R}/work/cache")
    .add_local_file(os.path.join(HERE, "work", "house.blend"), f"{R}/work/house.blend")
    .add_local_file(os.path.join(HERE, "work", "rooms.json"), f"{R}/work/rooms.json")
    .add_local_file(os.path.join(HERE, "work", "montelly", "house.blend"), f"{R}/work/montelly/house.blend")
    .add_local_file(os.path.join(HERE, "work", "montelly", "rooms.json"), f"{R}/work/montelly/rooms.json")
    .add_local_file(os.path.join(HERE, "photo.py"), f"{R}/photo.py")
    .add_local_file(os.path.join(HERE, "still.py"), f"{R}/still.py")
    .add_local_file(os.path.join(HERE, "dress.py"), f"{R}/dress.py")
    .add_local_file(os.path.join(HERE, "post.py"), f"{R}/post.py")
    .add_local_file(os.path.join(HERE, "style.py"), f"{R}/style.py")
)


@app.function(image=image, gpu=GPU, cpu=4.0, memory=16384, timeout=3600)
def shoot(name: str, house: str, camera: str, w: int, h: int, samples: int, env: dict) -> dict:
    import subprocess
    import sys

    out = f"/tmp/{name}.png"
    t = time.time()
    d = f"{R}/{HOUSES[house]}"
    p = subprocess.run([sys.executable, f"{R}/photo.py", f"{d}/house.blend", f"{d}/rooms.json", camera, out,
                        str(w), str(h), str(samples)], capture_output=True, text=True, cwd=R,
                       env={**os.environ, **env})
    text = p.stdout + p.stderr
    log = "\n".join(line for line in text.splitlines() if "PHOTO" in line or "rror" in line or "WARNING" in line)
    res = {"name": name, "seconds": time.time() - t, "log": log, "files": {}}
    if p.returncode:
        res["log"] += "\n" + text[-4000:]
        return res
    for suffix in ("-preview.png", "-raw.png", ".png", "-linear.exr", "-grade.json"):
        with open(out.replace(".png", suffix), "rb") as f:
            res["files"][f"{name}{suffix}"] = f.read()
    return res


@app.local_entrypoint()
def main(rooms: str = "dining,bedroom,bath", w: int = 2560, h: int = 1440, samples: int = 400):
    os.makedirs(os.path.join(HERE, "work", "auto"), exist_ok=True)
    t = time.time()
    env = {k: v for k, v in os.environ.items() if k in KNOBS}  # the experiment's knobs, as set here
    if env:
        print(f"knobs: {env}")
    jobs = [(n, *ROOMS[n], w, h, samples, env) for n in rooms.split(",")]
    seconds = []
    for res in shoot.starmap(jobs):
        seconds.append(res["seconds"])
        print(f"== {res['name']}: {res['seconds']:.0f} s on Modal ({GPU})\n{res['log']}")
        for fname, data in res["files"].items():
            with open(os.path.join(HERE, "work", "auto", fname), "wb") as f:
                f.write(data)
    ledger = os.path.join(HERE, "work", "ledger.csv")
    new = not os.path.exists(ledger)
    with open(ledger, "a") as f:
        if new:
            f.write("date,rooms,size,samples,gpu,container_s,usd\n")
        f.write(f"{time.strftime('%Y-%m-%d %H:%M')},{rooms.replace(',', ' ')},{w}x{h},{samples},{GPU},"
                f"{sum(seconds):.0f},{sum(seconds) * PRICE:.2f}\n")
    with open(ledger) as f:
        total = sum(float(line.rsplit(",", 1)[1]) for line in list(f)[1:])
    print(f"all done in {time.time() - t:.0f} s; about ${sum(seconds) * PRICE:.2f}, ${total:.2f} in the ledger")
