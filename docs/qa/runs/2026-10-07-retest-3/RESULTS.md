# Deep browser test, 2026-10-07: retest 3

**What was tested.** `main` after #289–#294:
- money-movement integrity (#289);
- the performance pass (#290);
- CFO and CEO receivables (#291);
- the bell's dates, part-paid invoices in the bell, and "no cash" at zero (#292–#294).

Same setup as before: a fresh scratch database, the three tenants and their data built through the UI, Playwright driving every scenario in `docs/qa/SCENARIOS.md`, and results read back through the API. Run with `WT=<checkout> sh scripts/qa/qa_all.sh`, three times from a clean database.

**Result: 52 of 52 scenarios pass.** Retest 2 passed 48 of 48. Four scenarios are new:
- **D11:** the bell's dates are in the company calendar, in Persian and English.
- **D12:** a part-paid overdue invoice stays in the bell with what is still open.
- **L1:** CFO receivables and payables equal the ledger's trade accounts (63,225,000 and 0 in Arman).
- **L8:** CEO Mode shows the same figures, on both pages.

**Automated UI checks.** 181 page views across eight combinations, every section open: **0 findings, 0 page errors**. The combinations:
- Persian on desktop, tablet and phone;
- English on desktop and phone;
- Spanish and Arabic on desktop;
- the personal tenant on a phone.

## The harness

Three changes, none of them an app fault:

- **B2 now checks the trial balance.** It called `/reports/trial-balance`, a 404 it only noted, so "the trial balance shows them" was never checked. It now reads `/manager-reports/books/trial-balance` and checks every figure:
  - cash 500,000,000 and bank 1,200,000,000 on the debit side;
  - capital 1,700,000,000 on the credit side;
  - debits equal credits.
- **Screenshots of pages with charts.** A full-page capture resizes the window, which clears every chart's canvas, so CEO Mode's shots looked chartless. Measured in the trend chart:
  - 35,673 painted pixels before a full-page shot;
  - 19,364 right after it;
  - 35,673 again 1.5 s later.

  `shot()` now grows the window to the page, lets the charts redraw, then captures without a resize. L8's CEO shot shows all four charts with Arman's figures.
- **Four new checks:** D11, D12, L1 and L8 (above).

## Findings from this run

| What | Where |
|---|---|
| A chart with no figures drew a blank box that looked broken (a new company's CEO Mode: four of them) | fixed in #296 |
| With nothing booked yet, a new UK company's dashboard said «0 IRR» (the reports' currency took the server's empty-books default) | fixed in #296 |
| CEO Mode's balance-sheet summary leaves the period's result out of equity, so it doesn't balance: assets 1,970,825,000; liabilities 107,535,000 and equity 1,900,000,000 add up to 2,007,535,000. The gap is the 36,710,000 loss. The formal balance sheet (D2) balances | fixed in #297 |
| On Persian pages, tiles show amounts in Persian digits while server-written text (the CFO narrative, the bell) and some tiles (runway 7.2, risk 30/100, −49.4%) use Latin digits | noted; older and cosmetic |

Found in this round's screenshots before the run, and already merged: the CFO receivables counted twice (#291), the bell's Gregorian dates (#292), part-paid invoices leaving the bell (#293), and «overdrawn» at exactly zero cash (#294).

## Scenarios

| ID | Result | Notes |
|---|---|---|
| A1 | ✅ |  |
| A2 | ✅ |  |
| A3 | ✅ | status «مشخصات شرکت ذخیره شد»; bad logo → 200 «محتوای فایل با نوع اعلام‌شده همخوانی ندارد» |
| A4 | ✅ |  |
| A5 | ✅ |  |
| A6 | ✅ |  |
| B1 | ✅ |  |
| B2 | ✅ |  |
| B3 | ✅ |  |
| B4 | ✅ |  |
| C1 | ✅ | ledger page is the account summary; the journal is checked under D1 |
| C3 | ✅ | due after picking the Net 30 client: 2026-11-06; amount 14300000 subtotal 13000000 tax 1300000 |
| C4 | ✅ |  |
| C5 | ✅ |  |
| C6 | ✅ |  |
| C7 | ✅ | credit note: «برگ بستانکار صادر شد.»; after the credit note: issued balance 7000000 |
| C9 | ✅ | cheque status now settled |
| C10 | ✅ |  |
| C11 | ✅ |  |
| C12 | ✅ | status parsed rows 5; duplicate asks to confirm: «این فایل با یکی از فایل‌های قبلاً واردشده یکسان است. باز هم وارد شود؟» |
| C13 | ✅ |  |
| C15 | ✅ | link arman_emp → علی رضایی (200); below the approval threshold: no manager step (by design) |
| C16 | ✅ |  |
| C17 | ✅ |  |
| C18 | ✅ | run → 201 «» |
| C19 | ✅ |  |
| C20 | ✅ |  |
| C21 | ✅ | month picker: ['مهر ۱۴۰۴', 'آبان ۱۴۰۴', 'آذر ۱۴۰۴', 'دی ۱۴۰۴']; 1405-07: 6112 actual 110000000 of 50000000 |
| C22 | ✅ |  |
| C23 | ✅ | ARM-1: paid total 14300000 VAT 1300000 |
| D1 | ✅ |  |
| D2 | ✅ | BS total keys: ['total_nca', 'total_ca', 'total_assets', 'total_equity', 'total_ncl', 'total_cl', 'total_liabilities', 'total_equity_and_liabilities']; BS assets 1959825000 L+E 1959825000; P&L net None |
| D3 | ✅ |  |
| D4 | ✅ |  |
| D5 | ✅ |  |
| D6 | ✅ |  |
| D7 | ✅ | Moadian rows: «	ARM-1859	1405/07/12	فروشگاه مهرگان (شعبه ۲)	14,300,000 ریال	1405/07/24 · 9 روز مانده	ارسال‌نشده	 شناسه یکتای حافظه مالی» |
| D8 | ✅ | insights → 200 0 |
| D10 | ✅ |  |
| D11 | ✅ |  |
| D12 | ✅ |  |
| E1 | ✅ |  |
| E2 | ✅ | intake → 200 «صورتحساب Mellat را خواندم: 2 ردیف (1405/07/12 تا 1405/07/13) و با دفاتر مقایسه کردم. 1 ردیف در دفاتر نیست؛ 1 ردیف احتمالاً همان سند ثبت‌شده است (تاریخ یا شرح فرق دارد). مانده پایانی صورتحساب با دفاتر » |
| E3 | ✅ |  |
| F1 | ✅ |  |
| F2 | ✅ | buttons a viewer sees on the ledger: [] |
| F3 | ✅ |  |
| G0 | ✅ | chart before: 85 accounts, Persian names: 0; reset → 200 |
| G1 | ✅ | vat periods → 200 {'vat_registered': False, 'stagger': '1', 'periods': [{'start': '2026-07-01', 'end': '2026-09-30', 'deadline': '2026-11-07', 'key': '2026-09-30', 'days_left': 31, 'open': False}, {'start': '2026-04-01 |
| H1 | ✅ | personal home: ai-accountant |
| L1 | ✅ | CFO receivables 63,225,000, payables 0; ledger 63,225,000 / 0 |
| L8 | ✅ |  |

## Rerun after the fixes (#296–#300)

The whole harness ran again on `main` with #296–#300: **52 of 52 pass**, and the automated UI checks found **0 findings and 0 page errors** in 181 page views. The screenshots reviewed for this round:

| Page | What it shows |
|---|---|
| Arman, CEO Mode (L8) | All four charts drawn with Arman's figures; receivables 63,225,000, the same as CFO Mode |
| Thames, CEO Mode | The balance sheet balances: assets 3,600 = liabilities 600 (VAT owed) + equity 3,000 (the period's result). Receivables 3,600 GBP. "Top Expenses" says "No data yet." |
| Thames, CFO Mode | The cost-change tile reads 0% with no "↓ 0%" line, and there is no "revenue down 100%" alarm at the start of the month |
| Thames, dashboard | Figures in GBP; the forecast expects invoice TS-0001's 3,600 in the week of 2026-10-12, its due date |
| Sara (personal), phone | The personal dashboard and the monthly report card read cleanly |

Fixed since the first run: empty charts and a new company's currency (#296), CEO Mode's balance sheet, trends, burn rate and drill-down (#297), numbers on every browser plus release notes 2026.10.07 (#298), report failures shown (#299), and CFO Mode's months (#300).
