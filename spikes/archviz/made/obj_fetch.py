"""Fetch the objects of made/objects.json into assets/objects/<id>.glb (git-ignored), textures shrunk so a run stays small.

Amazon Berkeley Objects (CC BY 4.0, amazon-berkeley-objects.s3.amazonaws.com): one .glb each, single files, no login.
Poly Haven (CC0): through ph_get.py, 1k. `obj_fetch.py [role...]` limits it to some roles.
Needs `npx @gltf-transform/cli` (sharp) for the textures."""

import concurrent.futures as cf
import json
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "assets", "objects")
ABO = "https://amazon-berkeley-objects.s3.amazonaws.com/3dmodels/original/"
TEX = {"rug": 2048, "runner": 2048, "sideboard": 2048}  # by role; the rest 1024: small on screen at 2560 px


def shrink(raw, out, size):
    tmp = out + ".tmp.glb"
    subprocess.run(["npx", "-y", "@gltf-transform/cli@4", "resize", raw, tmp, "--width", str(size), "--height", str(size)],
                   check=True, capture_output=True, timeout=600)
    os.replace(tmp, out)


def fetch(e):
    out = os.path.join(OUT, f"{e['id']}.glb")
    if os.path.exists(out):
        return e["id"], os.path.getsize(out), "have"
    raw = out + ".raw"
    try:
        if not os.path.exists(raw):
            req = urllib.request.Request(ABO + e["abo"], headers={"User-Agent": "housegen-archviz-spike"})
            with urllib.request.urlopen(req, timeout=300) as r, open(raw + ".part", "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            os.replace(raw + ".part", raw)
        shrink(raw, out, TEX.get(e["role"], 1024))
        size = os.path.getsize(raw)
        os.remove(raw)
        return e["id"], os.path.getsize(out), f"from {size >> 20} MB"
    except Exception as ex:  # one bad file doesn't stop the rest; it is reported and left out
        return e["id"], 0, f"FAILED {str(ex)[:120]}"


def main():
    cat = json.load(open(os.path.join(HERE, "objects.json"), encoding="utf-8"))
    roles, limit = [], None
    for a in sys.argv[1:]:
        if a.startswith("limit="):
            limit = int(a[6:])
        else:
            roles.append(a)
    todo = [e for e in cat if e["src"] == "abo" and (not roles or e["role"] in roles)]
    todo = todo[:limit] if limit else todo
    os.makedirs(OUT, exist_ok=True)
    total = 0
    with cf.ThreadPoolExecutor(6) as ex:
        for i, (eid, size, how) in enumerate(ex.map(fetch, todo)):
            total += size
            print(f"{i + 1}/{len(todo)} {eid} {size >> 10} KB ({how})", flush=True)
    print(f"{total >> 20} MB in all")


if __name__ == "__main__":
    main()
