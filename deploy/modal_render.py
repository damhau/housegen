"""The render service on Modal: the app image, a T4, one HTTPS URL, nothing running when idle.

The app keeps rendering with the same code; only the machine changes. SwiftShader on the
k8s pod draws a render call in ~45 s; the GPU in a few seconds.

Once:
    uv tool install modal && modal setup
    modal secret create housegen-render RENDER_SERVICE_TOKEN=$(openssl rand -hex 24)
    # private GHCR image only: modal secret create ghcr REGISTRY_USERNAME=… REGISTRY_PASSWORD=<PAT read:packages>

Deploy (again after every image that changes the renderer or the kit):
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
import subprocess

import modal

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
)


@app.function(
    image=image,
    gpu="T4",
    secrets=[modal.Secret.from_name("housegen-render")],  # RENDER_SERVICE_TOKEN
    scaledown_window=300,  # stays warm between the render calls of one build, then stops
    timeout=600,
    max_containers=2,
)
@modal.concurrent(max_inputs=4)  # the renderer serialises them on one browser
@modal.web_server(port=8000, startup_timeout=180)
def web() -> None:
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
