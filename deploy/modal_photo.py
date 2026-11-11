"""The listing photo service on Modal (#91): a saved version's rooms and exteriors in Blender Cycles, one GPU per photo.

`prepare` loads the version's scene page on a T4 (headless Chromium, as the render service draws), exports the house
to GLB, imports it into Blender and scores the candidate cameras of every room; then it builds the house in its
surroundings (the project's context files, fetched from the app) and scores the exterior viewpoints (#93). The .blend
files are kept per version on the `housegen-photo` volume. `shoot` renders one photo from them on an RTX PRO 6000
(the owner's choice, 2026-11-07).
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
SHOOT_GPU = os.environ.get("PHOTO_GPU", "RTX-PRO-6000")
PREPARE_GPU = "T4"
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


@app.function(image=image, gpu=PREPARE_GPU, cpu=4.0, memory=16384, timeout=1800, volumes=volumes,
              scaledown_window=GPU_SCALEDOWN_S)
def prepare(scene_url: str, key: str, context_url: str | None = None, site: list[float] | None = None) -> dict:
    """The version's house in Blender, its rooms' cameras and its exteriors', cached on the volume by `key`."""
    import json
    import subprocess
    import sys

    t0 = time.time()
    volume.reload()
    out = Path(DATA, "cache", key)
    if (out / "cameras.json").exists() and (out / "house.blend").exists():
        return {**json.loads((out / "cameras.json").read_text()), "cached": True, "seconds": round(time.time() - t0, 1)}
    tmp = Path("/tmp", key)
    p = subprocess.run([sys.executable, f"{CODE}/export.py", scene_url, str(tmp)], capture_output=True, text=True, check=False)
    if p.returncode:
        raise RuntimeError(f"export failed: {p.stderr[-2000:]}")
    exported = json.loads(p.stdout.strip().splitlines()[-1])
    t1 = time.time()
    out.mkdir(parents=True, exist_ok=True)
    args = ["main.py", "prepare", str(tmp / "house.glb"), str(tmp / "rooms.json"), str(out)]
    if context_url and _fetch_context(context_url, tmp / "context"):
        args += ["--context", str(tmp / "context")]
    if site:
        args += ["--site", f"{site[0]},{site[1]}"]
    _run(args)
    page = {r["name"]: r for r in json.loads((tmp / "rooms.json").read_text())}
    cams = json.loads((out / "cameras.json").read_text())
    for r in cams["rooms"]:  # the page's own figure, the one its SIA 416 report lists
        if page.get(r["name"], {}).get("area"):
            r["area"] = page[r["name"]]["area"]
    (out / "cameras.json").write_text(json.dumps(cams, ensure_ascii=False))
    (out / "rooms.json").write_text((tmp / "rooms.json").read_text())
    volume.commit()
    return {
        **cams,
        "export": exported,
        "cached": False,
        "seconds": {"export": round(t1 - t0, 1), "blender": round(time.time() - t1, 1)},
    }


def _fetch_context(url: str, to: Path) -> bool:
    """The project's surroundings (context.json and the files it names) into `to`; False when it has none."""
    import json
    import urllib.error
    import urllib.request

    base = url if url.endswith("/") else url + "/"
    try:
        meta = json.loads(urllib.request.urlopen(base + "context.json", timeout=60).read())
    except (urllib.error.URLError, ValueError):
        return False
    files = [meta[k]["file"] for k in ("terrain", "photo", "buildings", "trees", "cover", "far") if meta.get(k)]
    files += [p["file"] for p in (meta.get("far") or {}).get("photos", [])]
    to.mkdir(parents=True, exist_ok=True)
    (to / "context.json").write_text(json.dumps(meta))
    for name in files:
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
) -> dict:
    """One photo from the prepared version: the JPEG bytes and what the pipeline decided. `sun` ([azimuth, elevation])
    and `room_lamps` repeat a preview's light instead of searching it again; `remove` hides those dressing pieces;
    `kind` "exterior" shoots the house in its surroundings (outside.blend); `staging` (furnished | light | empty)
    is how much of the furnishing a room shows (#94)."""
    import json

    t0 = time.time()
    volume.reload()
    d = Path(DATA, "cache", key)
    blend = d / ("outside.blend" if kind == "exterior" else "house.blend")
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
    return {"jpeg": out.read_bytes(), "meta": meta, "gpu": SHOOT_GPU, "seconds": round(time.time() - t0, 1)}


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

    @api.get("/health")
    def health() -> dict:
        return {"ok": True, "shoot_gpu": SHOOT_GPU, "prepare_gpu": PREPARE_GPU}

    @api.post("/prepare", dependencies=[Depends(auth)])
    def start_prepare(body: PrepareIn) -> dict:
        return {"call_id": prepare.spawn(body.scene_url, body.key, body.context_url, body.site).object_id}

    @api.post("/shoot", dependencies=[Depends(auth)])
    def start_shoot(body: ShootIn) -> dict:
        call = shoot.spawn(
            body.key, body.camera, body.width, body.height, body.samples, body.sun, body.room_lamps, body.remove,
            body.kind, body.staging,
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
