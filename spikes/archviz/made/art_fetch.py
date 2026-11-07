"""Candidate wall art from museums' open-access collections, for picking by eye (the photo pipeline's art library).

Only works the museum itself marks CC0 / public domain, and only by an artist who died in 1955 or earlier (public
domain in Switzerland and the EU: 70 years after death), or anonymous and made before 1880.

    uv run --no-project --with requests --with pillow python made/art_fetch.py cands   -> work/art/cands/*.jpg,
        work/art/cands.json, work/art/sheet-<k>.jpg (numbered contact sheets)
    uv run --no-project --with requests --with pillow python made/art_fetch.py get <k>[=<style>]...   -> made/art/<id>.jpg
        (1280 px on the long side) and their entries in made/art/museum.json

Source: the Cleveland Museum of Art (openaccess-api.clevelandart.org, cc0=1). artic() (the Art Institute of Chicago)
is kept but not used: its IIIF image server refuses scripted downloads (2026-11-02).
"""

import io
import json
import os
import re
import sys
import time

import requests
from PIL import Image, ImageDraw, ImageStat

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "..", "work", "art")
OUT = os.path.join(HERE, "art")
DIED_BY = 1955
UA = {"User-Agent": "housegen-archviz-spike/0.1 (wall art for listing renders)"}

# (query, style): the styles home staging uses; a query may bring works of other kinds, the pick is by eye
QUERIES = [
    ("hokusai", "japanese"), ("hiroshige", "japanese"), ("ohara koson", "japanese"), ("yoshida hiroshi", "japanese"),
    ("kawase hasui", "japanese"),
    ("redoute", "botanical"), ("haeckel", "botanical"), ("anna atkins", "botanical"), ("blossfeldt", "botanical"),
    ("botanical", "botanical"), ("flower study", "botanical"),
    ("kandinsky", "abstract"), ("klee", "abstract"), ("mondrian", "abstract"), ("delaunay", "abstract"),
    ("leger", "abstract"), ("arthur dove", "abstract"), ("taeuber-arp", "abstract"),
    ("matisse", "drawing"), ("seurat", "drawing"), ("cezanne watercolor", "landscape"), ("hammershoi", "interior"),
    ("whistler", "landscape"), ("winslow homer watercolor", "landscape"), ("turner watercolor", "landscape"),
    ("monet", "landscape"), ("corot", "landscape"), ("sargent watercolor", "landscape"),
    ("atget", "photo"), ("stieglitz", "photo"), ("cyanotype", "photo"),
    ("utamaro", "japanese"), ("kuniyoshi landscape", "japanese"), ("japanese bird flower", "japanese"),
    ("nature print", "botanical"), ("leaf", "botanical"), ("fern", "botanical"),
    ("bauhaus", "abstract"), ("composition", "abstract"), ("moholy-nagy", "abstract"),
    ("charles demuth", "landscape"), ("cezanne", "landscape"), ("pissarro", "landscape"), ("boudin", "landscape"),
    ("still life", "drawing"), ("sketch", "drawing"), ("egon schiele landscape", "drawing"),
    ("sea", "landscape"), ("harbor", "landscape"), ("lake", "landscape"),
]


def years(text):
    return [int(y) for y in re.findall(r"\b(1[0-9]{3})\b", text or "")]


def ok_rights(died, made):
    if died is not None:
        return died <= DIED_BY
    return made is not None and made < 1880


def cleveland(q, style, n=36):
    r = requests.get("https://openaccess-api.clevelandart.org/api/artworks/",
                     params={"q": q, "cc0": 1, "has_image": 1, "limit": n}, headers=UA, timeout=30)
    for a in r.json().get("data", []):
        cr = a.get("creators") or []
        died = max((int(c["death_year"]) for c in cr if str(c.get("death_year") or "").isdigit()), default=None)
        if cr and died is None:  # a named artist without a death year: read it from the description "(…, 1760–1849)"
            ys = years(" ".join(c.get("description") or "" for c in cr))
            died = max(ys) if len(ys) >= 2 else None
            if died is None:
                continue
        made = a.get("creation_date_latest")
        if a.get("share_license_status") != "CC0" or not ok_rights(died, made):
            continue
        img = (a.get("images") or {}).get("web") or {}
        big = (a.get("images") or {}).get("print") or img
        if not img.get("url"):
            continue
        yield {"id": f"cma-{a['id']}", "source": "Cleveland Museum of Art", "licence": "CC0",
               "page": a.get("url"), "title": a.get("title"), "artist": "; ".join(c.get("description") or "" for c in cr),
               "date": a.get("creation_date"), "died": died, "type": a.get("type"), "style": style,
               "thumb": img["url"], "full": big.get("url") or img["url"]}


def artic(q, style, n=24):
    r = requests.get("https://api.artic.edu/api/v1/artworks/search", headers=UA, timeout=30, params={
        "q": q, "limit": n, "query[term][is_public_domain]": "true",
        "fields": "id,title,artist_display,date_display,date_end,image_id,is_public_domain,artwork_type_title"})
    time.sleep(1.1)  # 60 requests a minute
    for a in r.json().get("data", []):
        if not a.get("is_public_domain") or not a.get("image_id"):
            continue
        ys = years((a.get("artist_display") or "").split("\n")[-1])
        died = max(ys) if len(ys) >= 2 else None
        if died is None and re.search(r"[A-Z][a-z]+ [A-Z]", a.get("artist_display") or "") and "unknown" not in (a.get("artist_display") or "").lower():
            continue  # a named artist without dates
        if not ok_rights(died, a.get("date_end")):
            continue
        iiif = f"https://www.artic.edu/iiif/2/{a['image_id']}/full"
        yield {"id": f"aic-{a['id']}", "source": "Art Institute of Chicago", "licence": "CC0",
               "page": f"https://www.artic.edu/artworks/{a['id']}", "title": a.get("title"),
               "artist": (a.get("artist_display") or "").replace("\n", ", "), "date": a.get("date_display"),
               "died": died, "type": a.get("artwork_type_title"), "style": style,
               "thumb": f"{iiif}/400,/0/default.jpg", "full": f"{iiif}/1686,/0/default.jpg"}


def fetch(url):
    for _ in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=60)
            if r.ok:
                return Image.open(io.BytesIO(r.content)).convert("RGB")
        except requests.RequestException:
            pass
        time.sleep(2)
    return None


def cands():
    os.makedirs(os.path.join(WORK, "cands"), exist_ok=True)
    seen, out = set(), []
    for q, style in QUERIES:
        for src in (cleveland,):
            try:
                got = list(src(q, style))
            except (requests.RequestException, ValueError) as e:
                print("skip", src.__name__, q, e)
                continue
            for c in got:
                if c["id"] in seen:
                    continue
                seen.add(c["id"])
                p = os.path.join(WORK, "cands", c["id"] + ".jpg")
                if not os.path.exists(p):
                    im = fetch(c["thumb"])
                    if im is None:
                        continue
                    im.thumbnail((400, 400))
                    im.save(p, quality=85)
                c["k"] = len(out) + 1
                out.append(c)
            print(f"{q}: {len(out)} candidates so far", flush=True)
        json.dump(out, open(os.path.join(WORK, "cands.json"), "w"), indent=1, ensure_ascii=False)
    json.dump(out, open(os.path.join(WORK, "cands.json"), "w"), indent=1, ensure_ascii=False)
    # numbered contact sheets, 48 a sheet, 8 x 6 tiles of 240 px
    T, C, R = 240, 8, 6
    for s in range(0, len(out), C * R):
        sheet = Image.new("RGB", (C * T, R * (T + 18)), "white")
        d = ImageDraw.Draw(sheet)
        for i, c in enumerate(out[s:s + C * R]):
            im = Image.open(os.path.join(WORK, "cands", c["id"] + ".jpg"))
            im.thumbnail((T - 8, T - 8))
            x, y = (i % C) * T, (i // C) * (T + 18)
            sheet.paste(im, (x + (T - im.width) // 2, y + (T - im.height) // 2))
            d.text((x + 4, y + T), f"{c['k']} {c['style']}", fill=(200, 0, 0))
        sheet.save(os.path.join(WORK, f"sheet-{s // (C * R) + 1}.jpg"), quality=85)
    print(len(out), "candidates;", (len(out) + C * R - 1) // (C * R), "sheets in", WORK)


def get(ks):
    allc = {str(c["k"]): c for c in json.load(open(os.path.join(WORK, "cands.json"), encoding="utf-8"))}
    os.makedirs(OUT, exist_ok=True)
    mp = os.path.join(OUT, "museum.json")
    lib = {e["id"]: e for e in (json.load(open(mp, encoding="utf-8")) if os.path.exists(mp) else [])}
    for k in ks:  # "<k>" or "<k>=<style>" (the query's style was wrong for this work)
        k, _, style = k.partition("=")
        c = dict(allc[k], style=style or allc[k]["style"])
        im = fetch(c["full"]) or fetch(c["thumb"])
        if im is None:
            print("failed", k, c["id"])
            continue
        im.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
        im.save(os.path.join(OUT, c["id"] + ".jpg"), quality=88)
        e = {x: c[x] for x in ("id", "source", "licence", "page", "title", "artist", "date", "died", "type", "style")}
        tone = ImageStat.Stat(im.convert("L").resize((64, 64))).mean[0] / 255  # mean brightness (artlib.DARK)
        e.update(file=c["id"] + ".jpg", w=im.width, h=im.height, tone=round(tone, 3))
        lib[c["id"]] = e
        print("got", k, c["id"], c["style"], c["died"], im.size, c["title"], "|", c["artist"][:60])
    json.dump(sorted(lib.values(), key=lambda e: e["id"]), open(mp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)


if __name__ == "__main__":
    {"cands": lambda a: cands(), "get": get}[sys.argv[1]](sys.argv[2:])
