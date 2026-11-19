"""A render check on Modal's RTX PRO 6000, with this checkout's kit, backend and scripts: a kit change judged before it
is pushed, without loading the owner's PC.

    uvx modal run deploy/modal_sheet.py --scene <dir with index.html + src/> --out /tmp/sheet \\
        --args "--scene {scene} --look look=presentation --views room-1 room-2 --out {out}/sheet.png"

`--args` go to backend/scripts/look_sheet.py (`--script` picks another one); `{scene}` and `{out}` stand for the uploaded
scene and the output directory, whose files are written to `--out`. Without `--scene` the script gets no scene (the
kit's template for look_sheet). The uploaded scene is not under projects/<id>/: add `--seed <project id>` to look_sheet's
args, or a furnished room draws other catalogue pieces than the viewer (#117).

The app image (ghcr.io/damhau/housegen:latest, as the render service) brings the browser, the Python environment and
the kit's assets; this checkout's kit modules, renderer snapshots, styles and template, backend/src and backend/scripts
replace the image's. Assets only in this checkout (not yet in an image) go with `--assets <dir under kit/assets>,...`.

Modal pulls a registry tag once and keeps that build: a `latest` pulled long ago serves 404 for every model added since
(the page warns `housekit: licensed/…: 404` and draws the parametric fallbacks). Name the release dev runs instead:
HOUSEGEN_IMAGE=ghcr.io/damhau/housegen:<version> (`kubectl … get deploy housegen-dev -o jsonpath=…image`).

Scripts that read dev's pages (walk_sheet.py, look_sheet.py --scene-url) get the read-only token: AUTH_READONLY_TOKEN, else
~/.config/housegen/readonly-token.
"""

from __future__ import annotations

import io
import os
import shlex
import shutil
import subprocess
import tarfile
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[1]
IMAGE = os.environ.get("HOUSEGEN_IMAGE", "ghcr.io/damhau/housegen:latest")
GPU = os.environ.get("SHEET_GPU", "RTX-PRO-6000")
TOKEN_FILE = Path.home() / ".config" / "housegen" / "readonly-token"

app = modal.App("housegen-sheet")

image = (
    modal.Image.from_registry(IMAGE)
    .env(
        {
            # as in modal_render.py: Modal's runtime goes into the system Python, the scripts run from /app/.venv
            "PATH": "/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin",
            "RENDER_ANGLE": "gl-egl",
            "BROWSER_CHANNEL": "",
            "NVIDIA_DRIVER_CAPABILITIES": "all",
            "LOG_LEVEL": "INFO",  # render.browser names the GL the page drew with
        }
    )
    .add_local_dir(ROOT / "kit", "/work/kit", ignore=["node_modules", "assets", "showroom", "**/*.png", "**/*.jpg"])
    .add_local_dir(ROOT / "backend" / "src", "/work/src", ignore=["**/__pycache__"])
    .add_local_dir(ROOT / "backend" / "scripts", "/work/scripts", ignore=["**/__pycache__"])
)


def _tar(paths: list[tuple[Path, str]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, name in paths:
            tar.add(path, arcname=name)
    return buf.getvalue()


@app.function(image=image, gpu=GPU, timeout=1800)
def sheet(
    script: str, argv: list[str], scene: bytes | None, assets: bytes | None, token: str = ""
) -> dict[str, bytes]:
    if token:
        os.environ["AUTH_READONLY_TOKEN"] = token
    kit = Path("/app/kit")
    for f in Path("/work/kit").glob("*.js"):
        shutil.copy(f, kit / f.name)
    for d in ("versions", "styles", "template", "sky"):
        if (Path("/work/kit") / d).is_dir():
            shutil.copytree(Path("/work/kit") / d, kit / d, dirs_exist_ok=True)
    shutil.copytree("/work/src", "/app/src", dirs_exist_ok=True)
    shutil.copytree("/work/scripts", "/app/scripts", dirs_exist_ok=True)
    if assets:
        with tarfile.open(fileobj=io.BytesIO(assets)) as tar:
            tar.extractall(kit / "assets", filter="data")
    work = Path("/work/scene")
    if scene:
        with tarfile.open(fileobj=io.BytesIO(scene)) as tar:
            tar.extractall(work, filter="data")
    out = Path("/work/out")
    out.mkdir(parents=True, exist_ok=True)
    argv = [a.replace("{scene}", str(work)).replace("{out}", str(out)) for a in argv]
    subprocess.run(["/app/.venv/bin/python", f"/app/scripts/{script}", *argv], cwd="/app", check=True)
    return {str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()}


@app.local_entrypoint()
def main(out: str, args: str, scene: str = "", script: str = "look_sheet.py", assets: str = "") -> None:
    packed = None
    if scene:
        src = Path(scene)
        packed = _tar([(p, p.name) for p in src.iterdir() if p.name in ("index.html", "src")])
    extra = None
    if assets:
        extra = _tar([(ROOT / "kit" / "assets" / a, a) for a in assets.split(",")])
    token = os.environ.get("AUTH_READONLY_TOKEN", "")
    if not token and TOKEN_FILE.is_file():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    files = sheet.remote(script, shlex.split(args), packed, extra, token)
    dest = Path(out)
    for name, data in files.items():
        (dest / name).parent.mkdir(parents=True, exist_ok=True)
        (dest / name).write_bytes(data)
        print(dest / name)
