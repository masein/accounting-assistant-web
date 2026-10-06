# The deep browser test (`docs/qa/SCENARIOS.md`)

Playwright scripts that walk the scenarios through the real UI, with fake data, against a
**throwaway** local server. They never touch a real database or service: the server gets a fresh
scratch database on the compose `db`, AI keys and SMTP are blanked, and the run's password is
generated into `scripts/qa/.env` (git-ignored, mode 600) and never printed.

## Once

```bash
docker compose up -d db
docker compose build api
docker build -t aa-api-test --build-arg BASE=accounting-assistant-api -f scripts/qa/api-test.Dockerfile .
docker build -t aa-playwright:1.49.1 -f scripts/qa/playwright.Dockerfile scripts/qa
```

## A run

```bash
sh scripts/qa/qa_all.sh                    # server from this checkout, then groups A → I
WT=/path/to/worktree sh scripts/qa/qa_all.sh   # or another checkout's code
python3 scripts/qa/results_md.py scripts/qa/out/results.jsonl > /tmp/results.md   # the table for RESULTS.md
```

- `qa_up.sh`: starts the server (`aa-qa`, http://127.0.0.1:8899), creates the platform admin (`qa_seed.py`) and the e2e users.
- `qa_run.sh qa_X.py`: one group.
- Groups and their scenarios:

  | Script | Scenarios |
  |---|---|
  | `qa_A.py` | A, platform |
  | `qa_B.py` | B, master data |
  | `qa_C.py`, `qa_C2.py` | C, bookkeeping |
  | `qa_D.py` | D, reports, plus E chat and migration |
  | `qa_G.py` | G (UK), H (personal) |
  | `qa_I.py` | I, every page × language × size, with its sections open; I11, the bell's panel and the account menu open |

  Later groups read the ids that earlier ones saved in `out/state.json`, so run them in order.
- A page's sections fetch what they show when they open: `settle(page)` waits for the page's own requests in flight
  (counted by an init script), since `networkidle` returns at once once a page has been idle. `ux(..., scope="#id")`
  scans one open popup instead of the whole screen again.
- Output goes to `scripts/qa/out/` (git-ignored):
  - `results.jsonl`: one line per scenario, PASS or FAIL with notes;
  - `findings.jsonl`: the automated UI checks (I1–I9);
  - one screenshot per step.

## Adding a scenario

1. Write it in `docs/qa/SCENARIOS.md` first: the next ID in its section, steps, an expected result a screenshot or a read-back can prove, and the PR tag.
2. Add a function to the group's script:
   - `c = Check("C23")`;
   - `c.ok(cond, "what was expected")` for each expectation, and `c.note(...)` for context;
   - `shot(...)` at each step;
   - `ux(page, ...)` on a new page;
   - finish with `c.done()`.
3. Record the run's results in `docs/qa/runs/<date>/`.
