"""The listing photo service on Modal (#91): a saved version's rooms and exteriors in Blender Cycles, one GPU per photo.

`prepare` reads a version's prepared house from the `housegen-photo` volume, else makes it in two steps (#95):
`export` loads the scene page in headless Chromium on a GPU (T4, else L4, else A10G: the first one free) and writes
the house as GLB; `blender` imports it on a CPU machine and, side by side, scores the candidate cameras of the rooms
a set can use (#96, the app's rule) and builds the exteriors the set asked for (#97): the house in its surroundings
(the project's context files, fetched from the app) or on its own plot. The .blend files are kept per version, the
exterior builds side by side. `shoot` renders one photo from them on an RTX PRO 6000 (the owner's choice, 2026-11-07).
The app calls `web`: POST /prepare and /shoot start a call and return its id, GET /calls/<id> is its result once done,
so a photo of several minutes never has to fit in one HTTP request.

Once (the token is the render service's, so the app needs no new secret):
    uvx modal volume create housegen-photo
    uvx modal run deploy/modal_photo.py::upload          # the scans, the HDRI, the made pieces, the bed cache, the
                                                         # exteriors' plants and sky (spikes/archviz/ph_get.py --blend/--hdri)
    # the objects (#90's catalogue) are the spike's archviz-objects volume

Deploy (again after a change under photo/):
    uvx modal deploy deploy/modal_photo.py
    curl https://<workspace>--housegen-photo-web.modal.run/health

The app finds the service next to the render service (RENDER_SERVICE_URL with housegen-render → housegen-photo) or at
PHOTO_SERVICE_URL, and sends RENDER_SERVICE_TOKEN.
"""


import os
import time
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parent.parent
SPIKE = ROOT / "spikes" / "archviz"
# The owner's choice first (2026-11-07); six previews once waited 10 minutes for one (2026-11-12), so the next
# fastest that has RT cores for OptiX (A100 and H100 have none) take a shoot when no RTX PRO 6000 is free
SHOOT_GPU = os.environ.get("PHOTO_GPU", "RTX-PRO-6000,L40S,A10G").split(",")
# Chrome's export is the only step of a prepare that draws (about a minute): the first of these that is free. Waiting
# for a T4 alone took 19 minutes once.
EXPORT_GPU = ["T4", "L4", "A10G"]
# Blender's import, room scoring and exterior build use the CPU only: 8 cores run the scoring and the surroundings
# side by side in ~70 s on Montelly v14 (measured pinned to 8 cores, 2026-11-12); two .blend files in memory at once
BLENDER_CPU, BLENDER_MEMORY = 8.0, 32768
OUTSIDE = {"surroundings": "outside", "house": "outside-house"}  # photo/blender/main.py OUTSIDE
CODE = "/root/photo"
DATA = "/data"

app = modal.App("housegen-photo")
volume = modal.Volume.from_name("housegen-photo", create_if_missing=True)
objects = modal.Volume.from_name("archviz-objects")
token = modal.Secret.from_name("housegen-render")  # RENDER_SERVICE_TOKEN

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install(
        "libx11-6", "libxrender1", "libxxf86vm1", "libxfixes3", "libxi6", "libxkbcommon0", "libsm6",
        "libgl1", "libegl1", "libgles2", "libglvnd0", "libglib2.0-0",
    )
    .pip_install("bpy==5.0.1", "numpy", "pillow", "playwright==1.62.0", "astral==3.2")
    .run_commands("playwright install --with-deps chromium-headless-shell")
    .env(
        {
            "NVIDIA_DRIVER_CAPABILITIES": "all",  # graphics for Chromium's EGL, not only compute
            "PHOTO_ASSETS": f"{DATA}/assets",
            "PHOTO_MADE": f"{DATA}/made",
            "PHOTO_CACHE": f"{DATA}/cache/made",
            "OBJ_DIR": "/vol/objects",
        }
    )
    .add_local_dir(ROOT / "photo", CODE, ignore=["**/__pycache__/**"])
    .add_local_file(ROOT / "kit" / "finishes.js", "/root/kit/finishes.js")
)
web_image = modal.Image.debian_slim(python_version="3.11").pip_install("fastapi[standard]")
volumes: dict[str | os.PathLike[str], modal.Volume] = {DATA: volume, "/vol": objects}


def _run(args: list[str]) -> None:
    import subprocess
    import sys

    p = subprocess.run([sys.executable, *args], cwd=f"{CODE}/blender", capture_output=True, text=True, check=False)
    print(p.stdout[-20000:])
    if p.returncode:
        print(p.stderr[-20000:])
        raise RuntimeError(f"{args[0]} {args[1]} failed ({p.returncode}): {(p.stderr or p.stdout)[-1500:]}")


# a GPU container idles before it stops (Modal bills it): 60 s by default, a minute of an RTX PRO 6000 for each
# of a set's 16 parallel containers. The job queues its next call within a second, so a short window keeps the reuse.
GPU_SCALEDOWN_S = 10


def _cameras_name(score: dict | None) -> str:
    """The rooms' cameras under a set's rule: every room in cameras.json, else one file per rule."""
    import hashlib
    import json

    if score is None:
        return "cameras.json"
    return f"cameras-{hashlib.sha1(json.dumps(score, sort_keys=True).encode()).hexdigest()[:8]}.json"


def _cameras(d: Path, score: dict | None) -> Path | None:
    """The cameras file that answers `score`: its own, or cameras.json, which scored every room."""
    return next((d / n for n in (_cameras_name(score), "cameras.json") if (d / n).exists()), None)


def _outside(d: Path, exteriors: str | None) -> tuple[str | None, bool]:
    """The exterior build `exteriors` asks for (None for no exteriors) and whether the volume holds it."""
    name = OUTSIDE.get(exteriors or "surroundings")
    return name, bool(name) and (d / f"{name}.blend").exists() and (d / f"{name}.json").exists()


def _ready(d: Path, score: dict | None, exteriors: str | None) -> dict | None:
    import json

    cams, (name, built) = _cameras(d, score), _outside(d, exteriors)
    if cams is None or not (d / "house.blend").exists() or (name and not built):
        return None
    views = json.loads((d / f"{name}.json").read_text())["views"] if name else []
    return {"rooms": json.loads(cams.read_text())["rooms"], "exteriors": views}


@app.function(image=web_image, cpu=0.25, memory=512, timeout=3600, volumes={DATA: volume})
def prepare(
    scene_url: str,
    key: str,
    context_url: str | None = None,
    site: list[float] | None = None,
    exteriors: str | None = None,
    score: dict | None = None,
) -> dict:
    """The prepared house of `key`: its rooms' cameras and the viewpoints of the exterior build `exteriors`
    (surroundings | house | none; None: surroundings when `context_url` is given). From the volume when it holds
    them; else `export` (once per version, on a GPU) and `blender` (what is missing, on the CPU). `score`
    ({use: minimum area}) is the rule of the rooms a set can use: only those are scored."""
    t0 = time.time()
    volume.reload()
    d = Path(DATA, "cache", key)
    ready = _ready(d, score, exteriors)
    if ready:
        return {**ready, "cached": True, "seconds": round(time.time() - t0, 1)}
    exported = None if (d / "house.blend").exists() else export.remote(scene_url, key)
    built = blender.remote(key, context_url, site, exteriors, score)
    return {
        **built,
        "export": exported,
        "cached": False,
        "seconds": {"export": exported["seconds"] if exported else 0.0, "blender": built.pop("seconds")},
    }


def _gpu(device: str, choices: list[str]) -> str:
    return next((g for g in choices if g.replace("-", " ") in device), choices[0])


@app.function(image=image, gpu=EXPORT_GPU, cpu=4.0, memory=16384, timeout=1800, volumes=volumes,
              scaledown_window=GPU_SCALEDOWN_S)
def export(scene_url: str, key: str) -> dict:
    """The version's house as GLB and its rooms (export.py), onto the volume for `blender`."""
    import json
    import shutil
    import subprocess
    import sys

    t0 = time.time()
    tmp = Path("/tmp", key)
    p = subprocess.run([sys.executable, f"{CODE}/export.py", scene_url, str(tmp)], capture_output=True, text=True, check=False)
    if p.returncode:
        raise RuntimeError(f"export failed: {p.stderr[-2000:]}")
    exported = json.loads(p.stdout.strip().splitlines()[-1])
    out = Path(DATA, "cache", key, "export")
    out.mkdir(parents=True, exist_ok=True)
    for name in ("house.glb", "rooms.json"):
        shutil.copyfile(tmp / name, out / name)
    volume.commit()
    return {**exported, "gpu": _gpu(str(exported.get("gl") or ""), EXPORT_GPU), "seconds": round(time.time() - t0, 1)}


@app.function(image=image, cpu=BLENDER_CPU, memory=BLENDER_MEMORY, timeout=1800, volumes=volumes)
def blender(
    key: str,
    context_url: str | None = None,
    site: list[float] | None = None,
    exteriors: str | None = None,
    score: dict | None = None,
) -> dict:
    """What `prepare` found missing: house.blend from the export, the rooms' cameras under `score`, the exterior
    build. The scoring and the exterior build run side by side: each opens house.blend and writes its own files."""
    import json
    import shutil
    from concurrent.futures import ThreadPoolExecutor

    t0 = time.time()
    volume.reload()
    d = Path(DATA, "cache", key)
    phases: dict[str, float] = {}
    if not (d / "house.blend").exists():
        glb = d / "export" / "house.glb"
        if not glb.exists():
            raise RuntimeError(f"version {key} is not exported")
        _run(["main.py", "import", str(glb), str(d)])
        shutil.copyfile(d / "export" / "rooms.json", d / "rooms.json")
        phases["import"] = round(time.time() - t0, 1)
    tmp = Path("/tmp", key)
    tmp.mkdir(parents=True, exist_ok=True)
    steps: dict[str, list[str]] = {}
    if _cameras(d, score) is None:
        steps["rooms"] = ["main.py", "rooms", str(d / "house.blend"), str(d / "rooms.json"), str(tmp / "cameras.json")]
        if score is not None:
            steps["rooms"] += ["--score", json.dumps(score)]
    name, built = _outside(d, exteriors)
    if name and not built:
        steps["outside"] = ["main.py", "outside", str(d / "house.blend"), str(d), "--exteriors", exteriors or "surroundings"]
        # house alone needs only the context's alignment (north); the surroundings need its files
        if context_url and _fetch_context(context_url, tmp / "context", files=exteriors != "house"):
            steps["outside"] += ["--context", str(tmp / "context")]
        if site:
            steps["outside"] += ["--site", f"{site[0]},{site[1]}"]

    def step(item: tuple[str, list[str]]) -> None:
        t = time.time()
        _run(item[1])
        phases[item[0]] = round(time.time() - t, 1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(step, steps.items()))
    if "rooms" in steps:
        page = {r["name"]: r for r in json.loads((d / "rooms.json").read_text())}
        cams = json.loads((tmp / "cameras.json").read_text())
        for r in cams["rooms"]:  # the page's own figure, the one its SIA 416 report lists
            if page.get(r["name"], {}).get("area"):
                r["area"] = page[r["name"]]["area"]
        (d / _cameras_name(score)).write_text(json.dumps(cams, ensure_ascii=False))
    shutil.rmtree(d / "export", ignore_errors=True)  # house.blend holds it now
    volume.commit()
    ready = _ready(d, score, exteriors)
    if ready is None:
        raise RuntimeError(f"version {key}: the prepare left files missing")
    return {**ready, "phases": phases, "seconds": round(time.time() - t0, 1)}


def _fetch_context(url: str, to: Path, files: bool = True) -> bool:
    """The project's surroundings (context.json and, with `files`, the files it names) into `to`; False when it has
    none."""
    import json
    import urllib.error
    import urllib.request

    base = url if url.endswith("/") else url + "/"
    try:
        meta = json.loads(urllib.request.urlopen(base + "context.json", timeout=60).read())
    except (urllib.error.URLError, ValueError):
        return False
    names = [meta[k]["file"] for k in ("terrain", "photo", "buildings", "trees", "cover", "far") if meta.get(k)]
    names += [p["file"] for p in (meta.get("far") or {}).get("photos", [])]
    to.mkdir(parents=True, exist_ok=True)
    (to / "context.json").write_text(json.dumps(meta))
    for name in names if files else []:
        (to / name).write_bytes(urllib.request.urlopen(base + name, timeout=120).read())
    return True


@app.function(image=image, gpu=SHOOT_GPU, cpu=4.0, memory=16384, timeout=3600, volumes=volumes,
              scaledown_window=GPU_SCALEDOWN_S)
def shoot(
    key: str,
    camera: str,
    width: int,
    height: int,
    samples: int,
    sun: list[float] | None = None,
    room_lamps: bool | None = None,
    remove: list[str] | None = None,
    kind: str | None = None,
    staging: str | None = None,
    exterior: str | None = None,
) -> dict:
    """One photo from the prepared version: the JPEG bytes and what the pipeline decided. `sun` ([azimuth, elevation])
    and `room_lamps` repeat a preview's light instead of searching it again; `remove` hides those dressing pieces;
    `kind` "exterior" shoots the house from outside, in the build `exterior` names (surroundings | house, #97);
    `staging` (furnished | light | empty) is how much of the furnishing a room shows (#94)."""
    import json

    t0 = time.time()
    volume.reload()
    d = Path(DATA, "cache", key)
    blend = d / (f"{OUTSIDE.get(exterior or 'surroundings', 'outside')}.blend" if kind == "exterior" else "house.blend")
    if not blend.exists():
        raise RuntimeError(f"version {key} is not prepared")
    out = Path("/tmp", f"{key}-{int(t0 * 1000)}.jpg")
    spec = {"blend": str(blend), "rooms": str(d / "rooms.json"), "camera": camera, "out": str(out),
            "width": width, "height": height, "samples": samples, "sun": sun, "room_lamps": room_lamps,
            "remove": remove or [], "kind": kind, "staging": staging}
    spec_path = out.with_suffix(".spec.json")
    spec_path.write_text(json.dumps(spec))
    _run(["main.py", "shoot", str(spec_path)])
    meta_path = Path(f"{out}.json")
    meta = json.loads((meta_path if meta_path.exists() else out.with_suffix(".json")).read_text())
    gpu = _gpu(str(meta.get("device") or ""), SHOOT_GPU)
    return {"jpeg": out.read_bytes(), "meta": meta, "gpu": gpu, "seconds": round(time.time() - t0, 1)}


@app.function(image=web_image, secrets=[token], scaledown_window=300)
@modal.concurrent(max_inputs=50)
@modal.asgi_app()
def web():
    import base64
    import hmac

    from fastapi import Depends, FastAPI, Header, HTTPException
    from pydantic import BaseModel

    api = FastAPI(title="housegen photo service")

    def auth(authorization: str = Header("")) -> None:
        want = os.environ.get("RENDER_SERVICE_TOKEN", "")
        if not want or not hmac.compare_digest(authorization, f"Bearer {want}"):
            raise HTTPException(401, "bad token")

    class PrepareIn(BaseModel):
        scene_url: str
        key: str
        context_url: str | None = None
        site: list[float] | None = None
        exteriors: str | None = None
        score: dict[str, float] | None = None

    class ShootIn(BaseModel):
        key: str
        camera: str
        width: int = 1920
        height: int = 1080
        samples: int = 256
        sun: list[float] | None = None
        room_lamps: bool | None = None
        remove: list[str] = []
        kind: str | None = None
        staging: str | None = None
        exterior: str | None = None

    @api.get("/health")
    def health() -> dict:
        return {"ok": True, "shoot_gpu": SHOOT_GPU, "export_gpu": EXPORT_GPU, "blender_cpu": BLENDER_CPU}

    @api.post("/prepare", dependencies=[Depends(auth)])
    def start_prepare(body: PrepareIn) -> dict:
        call = prepare.spawn(body.scene_url, body.key, body.context_url, body.site, body.exteriors, body.score)
        return {"call_id": call.object_id}

    @api.post("/shoot", dependencies=[Depends(auth)])
    def start_shoot(body: ShootIn) -> dict:
        call = shoot.spawn(
            body.key, body.camera, body.width, body.height, body.samples, body.sun, body.room_lamps, body.remove,
            body.kind, body.staging, body.exterior,
        )
        return {"call_id": call.object_id}

    @api.post("/calls/{call_id}/cancel", dependencies=[Depends(auth)])
    def cancel(call_id: str) -> dict:
        modal.FunctionCall.from_id(call_id).cancel()
        return {"cancelled": call_id}

    @api.get("/calls/{call_id}", dependencies=[Depends(auth)])
    def result(call_id: str) -> dict:
        call = modal.FunctionCall.from_id(call_id)
        try:
            res = call.get(timeout=0)
        except TimeoutError:
            return {"status": "running"}
        except Exception as exc:  # noqa: BLE001 - whatever the call raised is its failure, reported to the app
            return {"status": "failed", "error": str(exc)[-2000:]}
        if isinstance(res, dict) and isinstance(res.get("jpeg"), bytes):
            res = {**res, "jpeg": base64.b64encode(res["jpeg"]).decode()}
        return {"status": "done", "result": res}

    return api


@app.local_entrypoint()
def upload() -> None:
    """The spike's local assets onto the volume: scans, HDRI, Poly Haven models, the made pieces, the bed cache."""
    pairs = [
        (SPIKE / "assets" / "textures", "/assets/textures"),
        (SPIKE / "assets" / "models", "/assets/models"),
        (SPIKE / "assets" / "hdri", "/assets/hdri"),
        (SPIKE / "made", "/made"),
        (SPIKE / "work" / "cache", "/cache/made"),
        (SPIKE / "assets" / "outside", "/assets/outside"),
    ]
    with volume.batch_upload(force=True) as batch:
        for local, remote in pairs:
            if local.is_dir():
                batch.put_directory(str(local), remote)
                print(f"{local} -> {remote}")
