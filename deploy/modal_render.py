"""The render service on Modal: the app image, a T4 (L4, A10G when none is free), one HTTPS URL, nothing running when idle.

The app keeps rendering with the same code; only the machine changes. SwiftShader on the
k8s pod draws a render call in ~45 s; the GPU in a few seconds. The image brings the browser and
the Python environment; this checkout's backend/src replaces the image's (as in modal_sheet.py), so
a change to the renderer reaches the service with a deploy from the checkout, image or not.

The app's scene pages need a session or a token (#105): the renderer sends RENDER_SERVICE_TOKEN back
as the app's token cookie, set for the scene URL's host only (render/renderer.py `present_token`).

Once:
    uv tool install modal && modal setup
    modal secret create housegen-render RENDER_SERVICE_TOKEN=$(openssl rand -hex 24)
    # private GHCR image only: modal secret create ghcr REGISTRY_USERNAME=… REGISTRY_PASSWORD=<PAT read:packages>

Deploy (again after a change to the renderer, from the checkout that has it):
    modal deploy deploy/modal_render.py                      # ghcr.io/damhau/housegen:latest
    HOUSEGEN_IMAGE=ghcr.io/damhau/housegen:1.2.3 modal deploy deploy/modal_render.py

Check that the GPU draws (a cold start: 20-60 s the first time):
    curl https://<workspace>--housegen-render-web.modal.run/health
    → {"gl": "ANGLE (NVIDIA, Tesla T4 …"}      OK
    → {"gl": "… SwiftShader …"} or "no webgl2"  the driver path failed: try RENDER_ANGLE=vulkan below

Then in the app's environment (the k8s secret):
    RENDER_SERVICE_URL=https://<workspace>--housegen-render-web.modal.run
    RENDER_SERVICE_TOKEN=<the token above>
    RENDER_BASE_URL=https://housegen-dev.apps.dhconsulting.ch     # where the service loads scenes from
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[1]
IMAGE = os.environ.get("HOUSEGEN_IMAGE", "ghcr.io/damhau/housegen:latest")
# the ghcr secret when the image is private (REGISTRY_USERNAME / REGISTRY_PASSWORD)
IMAGE_SECRET = os.environ.get("HOUSEGEN_IMAGE_SECRET", "")

app = modal.App("housegen-render")

image = modal.Image.from_registry(
    IMAGE,
    # No add_python: the image is python:3.12-slim, with pip, at /usr/local (add_python collides
    # with it). Modal installs its runtime into that interpreter; the service runs from /app/.venv
    # by absolute path.
    secret=modal.Secret.from_name(IMAGE_SECRET) if IMAGE_SECRET else None,
).env(
    {
        # the image puts /app/.venv/bin first, whose `python` has no pip: let Modal see the
        # system one. The service is started by absolute path, so this changes nothing for it.
        "PATH": "/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin",
        "RENDER_ANGLE": "gl-egl",  # the NVIDIA driver through EGL; "vulkan" is the other GPU path
        "BROWSER_CHANNEL": "",  # the bundled headless shell
        "NVIDIA_DRIVER_CAPABILITIES": "all",  # graphics, not only compute
        "LOG_LEVEL": "INFO",
    }
).add_local_dir(ROOT / "backend" / "src", "/work/src", ignore=["**/__pycache__"])


@app.function(
    image=image,
    # in that order, the first one free: on 2026-11-12 no T4 was free for 23 min, and a build's
    # render waited all that time before Modal dropped the request
    gpu=["T4", "L4", "A10G"],
    secrets=[modal.Secret.from_name("housegen-render")],  # RENDER_SERVICE_TOKEN
    scaledown_window=300,  # stays warm between the render calls of one build, then stops
    timeout=600,
    max_containers=2,
)
@modal.concurrent(max_inputs=4)  # the renderer serialises them on one browser
@modal.web_server(port=8000, startup_timeout=180)
def web() -> None:
    # the image installs the backend editable from /app/src
    shutil.copytree("/work/src", "/app/src", dirs_exist_ok=True)
    subprocess.Popen(
        [
            "/app/.venv/bin/uvicorn",
            "housegen.render.service:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ],
        cwd="/app",
    )
