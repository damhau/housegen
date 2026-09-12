"""Exercise a deployed render service: health (which GL draws), then one real render of a scene
the app serves, timed, with the pictures written to disk so you can LOOK at them.

    cd backend && uv run python scripts/render_service_check.py \\
        --url https://<workspace>--housegen-render-web.modal.run --token $RENDER_SERVICE_TOKEN \\
        --scene https://housegen-dev.apps.dhconsulting.ch/scenes/<project-id>/versions/<n>/index.html \\
        --views south-photo east-photo --out /tmp/render-check

Run it twice: the first call includes the cold start, the second is what a build pays per call.
"""

from __future__ import annotations

import argparse
import base64
import sys
import time
from pathlib import Path

import httpx


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--url", required=True, help="the service, e.g. https://…modal.run")
    ap.add_argument("--token", required=True, help="RENDER_SERVICE_TOKEN")
    ap.add_argument("--scene", help="absolute URL of a scene page the service can reach")
    ap.add_argument("--views", nargs="*", default=["south-photo"], help="views to render")
    ap.add_argument("--quality", default="high")
    ap.add_argument("--out", type=Path, default=Path("render-check"))
    a = ap.parse_args()

    http = httpx.Client(
        base_url=a.url.rstrip("/"),
        headers={"Authorization": f"Bearer {a.token}"},
        timeout=httpx.Timeout(600.0, connect=60.0),
    )
    t0 = time.perf_counter()
    h = http.get("/health")
    print(f"health {h.status_code} in {time.perf_counter() - t0:.1f}s: {h.text}")
    if h.status_code != 200:
        return 1
    gl = h.json().get("gl", "")
    if "swiftshader" in gl.lower() or "no webgl" in gl.lower():
        print("!! software rendering: the GPU path is not in use (try RENDER_ANGLE=vulkan)")
    if not a.scene:
        return 0

    t0 = time.perf_counter()
    r = http.post("/render", json={"scene_url": a.scene, "views": a.views, "quality": a.quality})
    wall = time.perf_counter() - t0
    if r.status_code != 200:
        print(f"render failed {r.status_code}: {r.text[:500]}")
        return 1
    d = r.json()
    print(
        f"render: {len(d['images'])} image(s) in {wall:.1f}s wall, {d['duration_ms']} ms on the service"
        f" ({d['gl']})"
    )
    a.out.mkdir(parents=True, exist_ok=True)
    for view, b64 in d["images"].items():
        (a.out / f"{view}.jpg").write_bytes(base64.b64decode(b64))
    for label, items in (("errors", d["errors"]), ("console", d["console"]), ("audit", d["audit"])):
        for line in items:
            print(f"  {label}: {line}")
    print(f"pictures: {a.out.resolve()}")
    return 0 if d["images"] else 1


if __name__ == "__main__":
    sys.exit(main())
