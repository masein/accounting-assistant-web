#!/bin/sh
# The whole deep browser test, in order, against a fresh aa-qa built from $WT (default: this checkout).
# Results: scripts/qa/out/results.jsonl (one line per scenario), findings.jsonl, screenshots.
Q=$(cd "$(dirname "$0")" && pwd)
sh "$Q/qa_up.sh"
group() {
  echo "=== $1 $2 $(date +%H:%M:%S)"
  sh "$Q/qa_run.sh" $1 $2 2>&1 | grep -E "^\[|^  !|^  ✗|^RUN THIS|Traceback|Error" | head -120
}
for g in qa_A.py qa_B.py qa_C.py qa_C2.py qa_D.py qa_G.py qa_J.py; do group $g; done
# group K: entries, then the scheduler's reviews job on the server for the days they need, then the checks
job=$(group qa_K.py seed | tee /dev/stderr | sed -n 's/^RUN THIS: docker exec aa-qa /docker exec -w \/app -e PYTHONPATH=\/app aa-qa /p')
[ -n "$job" ] && eval "$job" >/dev/null
group qa_K.py check
group qa_I.py
echo "=== done $(date +%H:%M:%S)"
