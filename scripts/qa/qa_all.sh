#!/bin/sh
# The whole deep browser test, in order, against a fresh aa-qa built from $WT (default: this checkout).
# Results: scripts/qa/out/results.jsonl (one line per scenario), findings.jsonl, screenshots.
Q=$(cd "$(dirname "$0")" && pwd)
sh "$Q/qa_up.sh"
for g in qa_A.py qa_B.py qa_C.py qa_C2.py qa_D.py qa_G.py qa_I.py; do
  echo "=== $g $(date +%H:%M:%S)"
  sh "$Q/qa_run.sh" $g 2>&1 | grep -E "^\[|^  !|Traceback|Error" | head -120
done
echo "=== done $(date +%H:%M:%S)"
