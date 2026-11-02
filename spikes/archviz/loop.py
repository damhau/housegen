"""The photo loop's bookkeeping (#89 follow-up): one preview, a critic picks one fix, the next preview applies it,
three comparers keep it only if it is better. Subagents do the looking; this keeps the state and the files.

    python3 loop.py start <room>...                   after the baseline step (-l0): the sun, the lamps, step 0
    python3 loop.py prompt <room>                     the critic's prompt for the room's current preview
    python3 loop.py propose <room> '<critic json>'    record the critic's fix; writes work/loop/envs.json for the next step
    python3 loop.py pair <room> <step>                the comparers' A/B pair: the current preview against <step>'s
    python3 loop.py cmp-prompt <room> <step> <k>      the k-th comparer's prompt (k = 1..3)
    python3 loop.py votes <room> A|B A|B A|B          the comparers' answers: keep or drop the proposed fix
    python3 loop.py decide <room> <votes for new>     the same, from a count
    python3 loop.py finals <out.json>                 per-room envs for the finals: baseline and loop
    python3 loop.py show                              the state

Files: work/auto/<room>-<step>-preview.png and -loop.json (photo.py), work/loop/state.json, work/loop/<room>/...
"""
import copy
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.join(HERE, "work", "auto")
LOOP = os.path.join(HERE, "work", "loop")
STATE = os.path.join(LOOP, "state.json")
MAX_STEPS = 3


def load():
    return json.load(open(STATE)) if os.path.exists(STATE) else {}


def save(st):
    os.makedirs(LOOP, exist_ok=True)
    json.dump(st, open(STATE, "w"), indent=1, ensure_ascii=False)


def info(room, step):
    return json.load(open(os.path.join(AUTO, f"{room}-{step}-loop.json"), encoding="utf-8"))


def fixes_of(r, extra=None):
    """The FIXES for a step: the base (the baseline's lamps, pinned) + the kept fixes + one proposed."""
    f = copy.deepcopy(r["base"])
    for fix in r["kept"] + ([extra] if extra else []):
        k = fix["kind"]
        if k == "camera":
            f["camera"] = {x: fix.get(x, 0) for x in ("turn", "height", "back", "fov")}
        elif k == "remove":
            f.setdefault("hide", []).append(fix["id"])
        elif k == "ceiling":
            f["ceiling"] = fix["finish"]
        elif k == "exposure":
            f["exposure"] = fix["stops"]
        elif k == "lamps":
            f["lamps"] = fix["state"]
    return f


def jpg(src, dst, w=1280):
    from PIL import Image
    im = Image.open(src).convert("RGB")
    im.resize((w, round(im.height * w / im.width))).save(dst, quality=90)


def main():
    cmd, *args = sys.argv[1:]
    st = load()
    if cmd == "start":
        for room in args:
            i = info(room, "l0")
            os.makedirs(os.path.join(LOOP, room), exist_ok=True)
            st[room] = {"sun": i["sun"], "base": {"lamps": "on" if i["lamps_on"] else "off"}, "kept": [],
                        "rejected": [], "steps": [], "current": "l0", "proposed": None, "done": False}
        save(st)
    elif cmd == "prompt":
        room = args[0]
        r = st[room]
        i = info(room, r["current"])
        prev = os.path.join(LOOP, room, f"{r['current']}.jpg")
        jpg(os.path.join(AUTO, f"{room}-{r['current']}-preview.png"), prev)
        f = fixes_of(r)
        pieces = "\n".join(f"  {p['id']} | {p['name']} | {p['share'] * 100:.1f} % | x {p['x'][0]:.2f}-{p['x'][1]:.2f}, "
                           f"y {p['y'][0]:.2f}-{p['y'][1]:.2f} | {', '.join(p['cut_by']) or 'not cut'} | "
                           f"{p['distance_m']} m" for p in i["pieces"]) or "  (none)"
        tpl = open(os.path.join(HERE, "loop_critic.md")).read().split("```")[1].strip("\n")
        cam = f.get("camera", {"turn": 0, "height": 0, "back": 0, "fov": 0})
        out = (tpl.replace("{preview}", prev).replace("{room}", i["room"]).replace("{use}", i["use"])
               .replace("{ceiling}", i["ceiling"]).replace("{lamps}", "on" if i["lamps_on"] else "off")
               .replace("{exposure}", f"{f.get('exposure', 0):+.2f}").replace("{camera}", json.dumps(cam))
               .replace("{pieces}", pieces).replace("{rejected}", json.dumps(r["rejected"]) if r["rejected"] else "none"))
        print(out)
    elif cmd == "propose":
        room, text = args
        r = st[room]
        ans = json.loads(text[text.index("{"):text.rindex("}") + 1])
        r["steps"].append({"from": r["current"], "problem": ans["problem"], "fix": ans["fix"], "why": ans.get("why")})
        if ans["fix"]["kind"] == "none" or len(r["steps"]) > MAX_STEPS:
            r["done"], r["proposed"] = True, None
        else:
            r["proposed"] = ans["fix"]
        save(st)
        envs = {n: {"SUN_PICK": x["sun"], "THUMBS": "0", "PREVIEW_ONLY": "1", "PREVIEW_W": "1280",
                    "PREVIEW_SAMPLES": "64", "FIXES": json.dumps(fixes_of(x, x["proposed"]))}
                for n, x in st.items() if x["proposed"]}
        json.dump(envs, open(os.path.join(LOOP, "envs.json"), "w"), indent=1)
        print(f"{room}: {'done' if r['done'] else 'next FIXES ' + envs[room]['FIXES']}")
    elif cmd == "pair":
        room, step = args
        r = st[room]
        d = os.path.join(LOOP, room, f"cmp-{step}")
        for k in (1, 2, 3):
            jd = os.path.join(d, f"j{k}")
            os.makedirs(jd, exist_ok=True)
            new_is_a = random.random() < 0.5
            jpg(os.path.join(AUTO, f"{room}-{r['current']}-preview.png"), os.path.join(jd, "B.jpg" if new_is_a else "A.jpg"))
            jpg(os.path.join(AUTO, f"{room}-{step}-preview.png"), os.path.join(jd, "A.jpg" if new_is_a else "B.jpg"))
            json.dump({"new": "A" if new_is_a else "B"}, open(os.path.join(jd, "key.json"), "w"))
        r["pending"] = step
        save(st)
        print(d)
    elif cmd == "cmp-prompt":  # the comparer's prompt for one of the pair's judges
        room, step, k = args
        tpl = open(os.path.join(HERE, "loop_critic.md")).read().split("```")[3].strip("\n")
        print(tpl.replace("{dir}", os.path.join(LOOP, room, f"cmp-{step}", f"j{k}")))
    elif cmd in ("votes", "decide"):
        room = args[0]
        r = st[room]
        if cmd == "votes":  # votes <room> <choice of j1> <j2> <j3>: A/B as the comparers answered
            d = os.path.join(LOOP, room, f"cmp-{r['pending']}")
            keys = [json.load(open(os.path.join(d, f"j{k}", "key.json")))["new"] for k in (1, 2, 3)]
            votes = sum(c.strip().upper() == key for c, key in zip(args[1:], keys))
        else:
            votes = int(args[1])
        step, fix = r.pop("pending"), r["proposed"]
        r["steps"][-1]["to"], r["steps"][-1]["votes_for_new"] = step, votes
        if votes >= 2:
            r["kept"].append(fix)
            r["current"] = step
            r["steps"][-1]["result"] = "kept"
        else:
            r["rejected"].append(fix)
            r["steps"][-1]["result"] = "dropped"
        r["proposed"] = None
        if len(r["steps"]) >= MAX_STEPS:
            r["done"] = True
        save(st)
        print(f"{room}: {r['steps'][-1]['result']} ({votes}/3); kept {r['kept']}; done {r['done']}")
    elif cmd == "finals":
        out = {}
        for n, x in st.items():
            base = {"SUN_PICK": x["sun"], "THUMBS": "0", "PREVIEW_W": "1280", "PREVIEW_SAMPLES": "64"}
            out[n] = {**base, "FIXES": json.dumps(fixes_of(x))}
        json.dump(out, open(args[0], "w"), indent=1)
        print(json.dumps(out, indent=1))
    elif cmd == "show":
        print(json.dumps(st, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
