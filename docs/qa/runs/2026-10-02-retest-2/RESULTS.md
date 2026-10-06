# Deep browser test, 2026-10-02: retest 2

**What was tested.** `main` at #282, after the owner's three decisions were built: Iran's VAT at 10% (#279), credit notes on paid invoices (#280) and the dashboard in tabs (#281), plus the budget-by-code fix (#282). The setup is the same as before: a fresh scratch database, the three tenants and their data built through the UI, Playwright driving every scenario in `docs/qa/SCENARIOS.md`, and results read back through the API. Run with `WT=<checkout> sh scripts/qa/qa_all.sh`, three times from a clean database (see "The harness" below).

**Result: 48 of 48 scenarios pass.** Retest 1 passed 45 of 46, and the first run 25 of 46.
- C3 now passes: Iran's standard VAT is 10% from 1 Farvardin 1403.
- Two scenarios are new: C23 (a credit note on a paid invoice: kept as credit, used on the next invoice, refunded) and D10 (the dashboard's tabs, on a laptop and on a phone).
- C7 and C21 were extended:
  - C7: a paid invoice now takes a credit note, and a partial credit note is not a payment.
  - C21: a budget set by account code counts that account's spending, a group code counts its sub-accounts' spending, and the overspend alert fires in Persian.

**The harness.** It gained C23, D10 and the extended C7 and C21. Two of my first attempts failed on the harness itself, not the app:
- C23 clicked the invoice's History button, which sits under the row's ⋯ menu (#277).
- D10 clicked the sidebar's nav on a phone, where the nav sits in the closed drawer.

Each was fixed and the whole run repeated from a clean database. C23 also provokes a refusal on purpose (a further credit note on a fully credited invoice), so its check for failed requests allows that one 400.

**Automated UI checks** (every page, Persian, English, Spanish and Arabic, desktop, tablet and phone). Nothing failed beyond the known residuals:
- the notification bell's 10.88 px count badge (by design);
- 20 px checkboxes, each in a 32 px label row, which is the tap target;
- English text in Persian or Arabic pages that is data: company and user names, and the UK chart's account names.

The counts rose only because there are more pages and invoices now (the dashboard's four tabs, C23's invoices).

**New findings from this retest** (`../2026-10-02/FINDINGS.md`):

| # | What | Fixed in |
|---|---|---|
| 51 | Phone tap targets the first pass missed: the chat's session rename and delete (20×17 px, and hidden until hover), the password eye (24×24), the daily-digest checkbox (13×20), a reminder's pause and delete (21×17, unnamed) | #283 |
| 52 | The bell's panel ran 30 px off a phone's screen: off the right in Persian, off the left in English (seen in the screenshots for #51) | #283 |
| 53 | The lists the server builds reached Persian pages in English: Moadian problems, an unread bank SMS, a statement's analysis, upload and import errors, pay-run warnings (in D7's notes) | #285 |
| 54 | An insight's dates stayed Gregorian in a Jalali company («on 2026-10-03»). The insight only fires on a weekend, so the browser suite failed on a Saturday | #284 |

The phone checks scan each page with its panels closed, which is why #52 and part of #51 needed the screenshots. With these four, the run's findings number 54. Three are not app bugs (#4, #10, #42), and every other one is fixed.

| ID | Retest 2 | Retest 1 | Notes (retest 2) |
|---|---|---|---|
| A1 | ✅ | ✅ |  |
| A2 | ✅ | ✅ |  |
| A3 | ✅ | ✅ | status «مشخصات شرکت ذخیره شد»; bad logo → 200 «محتوای فایل با نوع اعلام‌شده همخوانی ندارد» |
| A4 | ✅ | ✅ |  |
| A5 | ✅ | ✅ |  |
| A6 | ✅ | ✅ |  |
| B1 | ✅ | ✅ |  |
| B2 | ✅ | ✅ | trial balance → 404 |
| B3 | ✅ | ✅ |  |
| B4 | ✅ | ✅ |  |
| C1 | ✅ | ✅ | ledger page is the account summary; the journal is checked under D1 |
| C3 | ✅ | ❌ | due after picking the Net 30 client: 2026-11-01; amount 14300000 subtotal 13000000 tax 1300000 |
| C4 | ✅ | ✅ |  |
| C5 | ✅ | ✅ |  |
| C6 | ✅ | ✅ |  |
| C7 | ✅ | ✅ | credit note: «برگ بستانکار صادر شد.»; after the credit note: issued balance 7000000 |
| C9 | ✅ | ✅ | cheque status now settled |
| C10 | ✅ | ✅ |  |
| C11 | ✅ | ✅ |  |
| C12 | ✅ | ✅ | status parsed rows 5; duplicate asks to confirm: «این فایل با یکی از فایل‌های قبلاً واردشده یکسان است. باز هم وارد شود؟» |
| C13 | ✅ | ✅ |  |
| C15 | ✅ | ✅ | link arman_emp → علی رضایی (200); below the approval threshold: no manager step (by design) |
| C16 | ✅ | ✅ |  |
| C17 | ✅ | ✅ |  |
| C18 | ✅ | ✅ | run → 201 «» |
| C19 | ✅ | ✅ |  |
| C20 | ✅ | ✅ |  |
| C21 | ✅ | ✅ | month picker: ['مهر ۱۴۰۴', 'آبان ۱۴۰۴', 'آذر ۱۴۰۴', 'دی ۱۴۰۴']; 1405-07: 6112 actual 110000000 of 50000000 |
| C22 | ✅ | ✅ |  |
| C23 | ✅ | — | ARM-1: paid total 14300000 VAT 1300000 |
| D1 | ✅ | ✅ |  |
| D2 | ✅ | ✅ | BS total keys: ['total_nca', 'total_ca', 'total_assets', 'total_equity', 'total_ncl', 'total_cl', 'total_liabilities', 'total_equity_and_liabilities']; BS assets 1929025000 L+E 1929025000; P&L net None |
| D3 | ✅ | ✅ |  |
| D4 | ✅ | ✅ |  |
| D5 | ✅ | ✅ |  |
| D6 | ✅ | ✅ |  |
| D7 | ✅ | ✅ | Moadian rows: «	ARM-1921	1405/07/10	شرکت پارس‌افزار	28,600,000 ریال	1405/07/22 · 12 روز مانده	ارسال‌نشده	 Enter the company's tax memor» |
| D8 | ✅ | ✅ | insights → 200 0 |
| D10 | ✅ | — |  |
| E1 | ✅ | ✅ |  |
| E2 | ✅ | ✅ | intake → 200 «صورتحساب Mellat را خواندم: 2 ردیف (1405/07/12 تا 1405/07/13) و با دفاتر مقایسه کردم. 1 ردیف در دفاتر نیست؛ 1 ردیف احتمالاً همان سند ثبت‌شده است (تاریخ یا شرح فرق دارد). مانده پایانی صورتحساب با دفاتر » |
| E3 | ✅ | ✅ |  |
| F1 | ✅ | ✅ |  |
| F2 | ✅ | ✅ | buttons a viewer sees on the ledger: [] |
| F3 | ✅ | ✅ |  |
| G0 | ✅ | ✅ | chart before: 85 accounts, Persian names: 0; reset → 200 |
| G1 | ✅ | ✅ | vat periods → 200 {'vat_registered': False, 'stagger': '1', 'periods': [{'start': '2026-07-01', 'end': '2026-09-30', 'deadline': '2026-11-07', 'key': '2026-09-30', 'days_left': 36, 'open': False}, {'start': '2026-04-01 |
| H1 | ✅ | ✅ | personal home: ai-accountant |

**48 of 48 pass** (retest 1: 45 of 46; the first run: 25 of 46).
