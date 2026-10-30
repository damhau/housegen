"""Download a Poly Haven asset (CC0) as glTF with its files: ph_get.py <id> [res=2k] -> assets/models/<id>/<id>.gltf
(textures: ph_get.py --tex <id> [res=2k] -> assets/textures/<id>/{diff,nor_gl,rough,disp}.jpg)"""

import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
UA = {"User-Agent": "housegen-archviz-spike"}


def get(url: str) -> bytes:
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120).read()


def model(asset: str, res: str) -> None:
    files = json.loads(get(f"https://api.polyhaven.com/files/{asset}"))
    g = files["gltf"].get(res) or files["gltf"][sorted(files["gltf"])[0]]
    g = g["gltf"]
    out = HERE / "assets" / "models" / asset
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{asset}.gltf").write_bytes(get(g["url"]))
    for rel, f in g.get("include", {}).items():
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_bytes(get(f["url"]))
    print(out / f"{asset}.gltf")


def texture(asset: str, res: str) -> None:
    files = json.loads(get(f"https://api.polyhaven.com/files/{asset}"))
    out = HERE / "assets" / "textures" / asset
    out.mkdir(parents=True, exist_ok=True)
    for key, name in (("Diffuse", "diff"), ("nor_gl", "nor_gl"), ("Rough", "rough"), ("Displacement", "disp"), ("AO", "ao")):
        if key in files and res in files[key]:
            fmt = "jpg" if "jpg" in files[key][res] else sorted(files[key][res])[0]
            p = out / f"{name}.{fmt}"
            if not p.exists():
                p.write_bytes(get(files[key][res][fmt]["url"]))
    print(out, sorted(x.name for x in out.iterdir()))


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[0] == "--tex":
        texture(args[1], args[2] if len(args) > 2 else "2k")
    else:
        model(args[0], args[1] if len(args) > 1 else "2k")
