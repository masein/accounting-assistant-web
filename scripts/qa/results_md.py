"""results.jsonl (one line per finished scenario check) → a RESULTS table:
the last record per scenario id, its status and notes, compared with run 1."""
import json
import sys
from pathlib import Path

new = Path(sys.argv[1]); old = Path(sys.argv[2]) if len(sys.argv) > 2 else None


def load(p):
    out = {}
    if p and p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            out[d["id"]] = d
    return out


a, b = load(new), load(old)
mark = {"PASS": "✅", "FAIL": "❌", "ERROR": "⚠️"}
print("| ID | Run 2 | Run 1 | Notes (run 2) |\n|---|---|---|---|")
for sid in sorted(a, key=lambda s: (s[0], int("".join(ch for ch in s[1:] if ch.isdigit()) or 0), s)):
    d = a[sid]
    note = (d.get("notes") or "").replace("|", "/").replace("\n", " ")[:260]
    print(f"| {sid} | {mark.get(d['status'], d['status'])} | {mark.get(b.get(sid, {}).get('status'), '—')} | {note} |")
n = len(a); ok = sum(1 for d in a.values() if d["status"] == "PASS")
print(f"\n**{ok} of {n} pass** (run 1: {sum(1 for d in b.values() if d['status'] == 'PASS')} of {len(b)}).")
