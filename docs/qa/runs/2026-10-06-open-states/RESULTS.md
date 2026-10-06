# Deep browser test, 2026-10-06: open states

**Why.** Retest 2 (`../2026-10-02-retest-2/`) passed 48 of 48 scenarios, but two of its findings got past the automated page checks:
- #52: the bell's panel ran off a phone's screen, and the pages were scanned with it closed.
- #53: the Moadian panel's English problems. The sections were opened but scanned 200 ms later, before they had fetched anything. The English check also skipped any text that held a Persian word.

**What changed in the harness** (`scripts/qa/`, scenario I11 and an extended I3):
- **`settle(page)`:** after a page's sections are opened, the run waits until the page's own requests in flight are done. An init script counts them, since `networkidle` returns at once once a page has been idle.
- **I11:** once per language and size, the bell's panel and the account menu are opened, screenshotted and scanned on their own (`ux(..., scope=…)`).
- **I3, in Persian runs:** an English sentence counts even with Persian words in it. A sentence is five or more English words including a function word (the, to, of, is, no…), which tells it apart from names. Persian runs use the Iranian tenants, whose data is Persian.
- **Less noise, so real findings stand out:**
  - a checkbox inside a label row of at least 28 px counts that row as its tap target;
  - the bell's count badge (small by design) is no longer flagged as tiny text.

**Result.** `main` at #286, a fresh scratch database, the whole run: **48 of 48 scenarios pass.** The new checks found:

| # | What | Fixed here |
|---|---|---|
| 55 | The new reminder's date field and repeat menu in the bell's panel had no accessible name, in every language and size | names in all four languages |
| — | On a phone, a checkbox with no label row around it was a 20 px target: a Moadian invoice's pick, the time page's "billable" box | 28 px on phones |
| — | Data in Latin script on Persian and Arabic pages wasn't isolated: the signed-in username, the company badge and the sidebar's company name, the profile summary's name, an item's code «(TON-12)», the ledger's top accounts («1100 — Trade debtors») | each in a `<bdi>`, so its order and punctuation hold in right-to-left text |

A second pass added one more check, prompted by the phone screenshots of Settings: they looked shrunk, with the sidebar drawer showing. The page had made the phone **zoom out**. The overflow check can't see that, because a zoomed-out page's `innerWidth` grows with its content. The new I1 check compares the layout with the screen and names what is too wide:

| # | What | Fixed here |
|---|---|---|
| 56 | The Iranian owner's Settings zoomed a phone out to a 451 px layout. Its users table (584 px, a role menu on every row) and AI usage table (489 px) didn't scroll in place, and the browser suite's phone pages left Settings out | whatever holds a results table scrolls it sideways in place; Settings joins the phone test's pages |

**After the fixes, the automated UI checks find nothing** across all eight combinations: Persian on desktop, tablet and phone; English on desktop and phone; Spanish and Arabic on desktop; the personal tenant on a phone. That covers every page with its sections open, plus the bell's panel and the account menu.
