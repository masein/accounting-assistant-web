# Performance on a year of books, 2026-10-06 (scenarios K1, K2)

**Why.** The first performance pass (§2.6, #152) measured the ledger reports on 20,000 journals and no invoices. Most pages since then read invoices, credits and notifications, and nobody had timed them on a busy company.

**The bench.** `scripts/perf_bench.py` on a scratch database (`aa_scratch_bench`):
- 20,000 journals over 12 months, 100 parties;
- 2,500 sales invoices and 800 bills made the app's own way (`create_invoice`, `add_payment`, `add_credit_note`): 70 % paid, 15 % part-paid, 5 % with a reduction note;
- each endpoint fetched three times as the owner; the table shows the median, the slowest (usually the cold first call) and the SQL statements per call.

`--only` (repeatable) picks endpoints, e.g. `--only close-pack --only accounts-`.

## Before → after

| Endpoint | Median ms | Slowest ms | Queries | What was wrong |
|---|---|---|---|---|
| `/invoices` | 12,693 → **582** | 12,848 → 638 | 25,171 → **17** | about eight queries per invoice: payments, reductions, its credit, its party's credit |
| `/invoices?kind=purchase` | 2,619 → **233** | 2,906 → 282 | 5,724 → **12** | the same |
| `/moadian/invoices?state=all` | 5,909 → **448** | 6,089 → 500 | 17,508 → **12** | settings, profile, party and totals per invoice |
| `/insights` (cold) | — | 10,629 → **2,664** | 6 → 6 (cached) | each detector read the ledger as ORM objects on its own |
| `/brain/cfo/report` | 3,314 → **504** | 3,457 → 569 | 71 → **17** | 200,000 ORM objects for one monthly sum |
| `/brain/ceo/report` | 6,590 → **476** | 6,803 → 503 | 133 → **19** | the CFO data, loaded twice |
| `/manager-reports/operational/accounts-receivable` | 582 → **40** | 778 → 134 | 1,495 → **5** | totals per invoice |
| `/manager-reports/operational/accounts-payable` | 225 → **21** | 259 → 22 | 497 → **5** | the same |
| `/manager-reports/close-pack` | 4,273 → **2,944** | 4,961 → 3,434 | 2,029 → **83** | the aging tables, per invoice |
| `/notifications/feed` | 393 → **100** | 415 → 369 | 513 → **35** | one lookup per notification on every bell poll |

Everything else on the bench was already within K1: the owner dashboard at ~600 ms (34 queries), the Iranian statements, tax pages and lists all under 300 ms.

**K1: pass, with one stated exception.**
- Every list and page fetch is under 1 s, and every report under 2 s.
- The close pack takes ~3 s, which is under its 4-second bar. It is a download (a ZIP of a 15-page PDF, a workbook and the month's journal as CSV). About 2 s of that is WeasyPrint laying out the PDF's tables, 0.8 s building the tables and 0.3 s the CSV.

**K2: pass.** `tests/test_list_performance.py` grows the invoices fivefold and checks that no statement count grows:
- the invoice lists, the Moadian list, both aging reports, the close pack and the feed;
- run against `main` before these changes, each of those checks fails (the invoice list went 39 → 203 queries, the Moadian list 32 → 144).

The same file checks that the batched figures equal the one-invoice-at-a-time ones (the list, both agings). It also checks that the plain-row reads (the insights window, the CFO data) stay inside the company.

## What changed

- **Invoices** (`app/api/invoices.py`):
  - `_to_read_many` reads payment and reduction sums, own credits and party credits for the whole list in a few grouped queries;
  - `totals_for` does the same for the aging reports.
  - `IN` lists go in chunks of 5,000 (psycopg's statement limit is 65,535 parameters).
- **Credits** (`app/services/credits.py`): `by_invoice` and `by_party`, the list forms of `for_invoice` and `for_party`, with the same live-entry arithmetic (#289).
- **Moadian** (`app/services/moadian/builder.py`): `Shared` carries the settings, the company's ids, the parties and the totals once per list.
- **Insights** (`app/services/insight_service.py`):
  - one shared 400-day window of plain rows per run, which the expense, vendor and recurring detectors read;
  - the receivables balance is a SQL sum.
  - The same 8 insights come out on the bench.
- **CFO and CEO** (`app/services/cfo_intelligence.py`): one plain-row query with the same arithmetic, and the CEO report reuses the CFO's data. Output is identical on the bench (compared field by field).
- **Notifications** (`app/services/notification_service.py`): a refresh collects its notifications, then reads the existing rows for those keys in one chunked query and writes them. A dismissed row still stays dismissed. A race with another refresh still ends in the retry from #237.
- **FX** (`app/services/fx_base.py`): `fill_pending` reads the base currency once, not once per journal. The bench's first start had spent ~10 minutes in it.

## Noted, not changed here

The CFO and CEO reports' receivables and payables look wrong, separately from performance:
- they add open invoices to the ledger's balances, so an invoice that posted its receivable (the app's own flow) counts twice;
- drafts count as owed;
- the gross amount is used, and part-paid invoices are left out;
- invoices in every currency are added as raw numbers: in the screenshots, a GBP company's 11,000,000-rial invoice showed as "Accounts Receivable 11,000,000 GBP";
- the ledger part covers 12 months, not the balance to date.

The addition exists for cash-basis books, which never post a receivable. It needs its own look and tests.
