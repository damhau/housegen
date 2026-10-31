"""Save judges' replies as <round>/j<k>/answer.md, from the subagents' transcripts (the final hand-back message).

    judge_save.py <round dir>/j<k>=<agent transcript .output or .jsonl> [...]
"""

import json
import sys
from pathlib import Path

for arg in sys.argv[1:]:
    dest, src = arg.split("=", 1)
    reply = ""
    for line in open(src):
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        m = d.get("message", {})
        if isinstance(m, dict) and isinstance(m.get("content"), list):
            for c in m["content"]:
                if c.get("type") == "tool_use" and c.get("name") == "SubagentHandback":
                    reply = c["input"].get("message", reply)
                elif c.get("type") == "text" and "| Overall |" in c.get("text", ""):
                    reply = c["text"]
    if not reply:
        print(f"{dest}: no reply found in {src}")
        continue
    Path(dest).mkdir(parents=True, exist_ok=True)
    (Path(dest) / "answer.md").write_text(reply.strip() + "\n")
    print(f"{dest}: saved")
