# Deep browser test, 2026-10-02: retest after the fixes

**What was tested.** `main` at #277, after the 19 fix PRs from the first run (#258, #259 and #261–#277). The setup is the same as the first run (`../2026-10-02/RESULTS.md`): a fresh scratch database, the same three tenants and their data, built through the UI, and Playwright driving every scenario in `docs/qa/SCENARIOS.md`. Results are read back through the API.

**Result: 45 of 46 scenarios pass** (the first run passed 25 of 46). The one failure is C3. The Iranian standard VAT in the rate table is 9%; if it rose to 10% from 1 Farvardin 1403, as I believe the budget law says, Iranian invoices since then charge too little. That is a tax-law decision (finding #15) and needs the owner's confirmation before the rate is changed.

**My scripts, not the app.** To get here, the test scripts were corrected in several places; none of these was an app defect.
- A3 read the company profile from a wrong URL.
- B3 now expects a bank party's code «۳۰۱» to be refused (#276).
- C1 and D6 now wait for the voucher's confirmation dialog, which fetches account names first (#261).
- C15: under the approval threshold, a claim needs no manager. This is by design and was already noted in the first run.
- C19: depreciation starts the month after the asset enters service (Article 149). The scripts now expect 4 months, and count them in the preview.
- C21 reads the Jalali month picker.
- G0 uses the Settings wipe, with the company's name typed back (#259).
- The UI check now skips inputs inside a closed `<details>`: Chromium keeps their boxes but doesn't show them. That is what produced finding #10.

**Automated UI checks** (every page, Persian, English, Spanish and Arabic, desktop, tablet and phone):
- No JS errors, failed requests, slow pages, raw keys or `{placeholders}`.
- No unnamed controls, and no page overflow at any size.
- What remains:
  - the notification bell's count badge at 10.88 px (98 hits, by design);
  - 20 px checkboxes, each in a 32 px label row, which is the tap target (41 hits);
  - English text in Persian or Arabic pages that is data: company and user names (37 hits).

**Findings** (`../2026-10-02/FINDINGS.md`, 47 in all):
- 41 fixed;
- 3 not app bugs (#4 came from the QA seed, #10 from the QA check, and #42 is the browser's own date format);
- 2 questions for the owner (#15 VAT rate; #44 credit notes on a paid invoice);
- 1 waiting on the owner's approval (#36, a dashboard reorganisation).

| ID | Retest | Run 1 | Notes (retest) |
|---|---|---|---|
| A1 | ✅ | ❌ |  |
| A2 | ✅ | ❌ |  |
| A3 | ✅ | ❌ | status «مشخصات شرکت ذخیره شد»; bad logo → 200 «محتوای فایل با نوع اعلام‌شده همخوانی ندارد» |
| A4 | ✅ | ❌ |  |
| A5 | ✅ | ✅ |  |
| A6 | ✅ | ❌ |  |
| B1 | ✅ | ✅ |  |
| B2 | ✅ | ✅ | trial balance → 404 |
| B3 | ✅ | ❌ |  |
| B4 | ✅ | ❌ |  |
| C1 | ✅ | ❌ | ledger page is the account summary; the journal is checked under D1 |
| C3 | ❌ | ❌ | VAT at 10% (Iran, since 1403) = 1,300,000 — got 1170000; due after picking the Net 30 client: 2026-11-01; amount 14170000 subtotal 13000000 tax 1170000 |
| C4 | ✅ | ❌ |  |
| C5 | ✅ | ✅ |  |
| C6 | ✅ | ✅ |  |
| C7 | ✅ | ❌ | credit note: «برگ بستانکار صادر شد.»; after the credit note: partially_paid balance 7000000 |
| C9 | ✅ | ✅ | cheque status now settled |
| C10 | ✅ | ✅ |  |
| C11 | ✅ | ❌ |  |
| C12 | ✅ | ❌ | status parsed rows 5; duplicate asks to confirm: «این فایل با یکی از فایل‌های قبلاً واردشده یکسان است. باز هم وارد شود؟» |
| C13 | ✅ | ✅ |  |
| C15 | ✅ | ❌ | link arman_emp → علی رضایی (200); below the approval threshold: no manager step (by design) |
| C16 | ✅ | ✅ |  |
| C17 | ✅ | ❌ |  |
| C18 | ✅ | ✅ | run → 201 «» |
| C19 | ✅ | ❌ |  |
| C20 | ✅ | ✅ |  |
| C21 | ✅ | ❌ | month picker: ['مهر ۱۴۰۴', 'آبان ۱۴۰۴', 'آذر ۱۴۰۴', 'دی ۱۴۰۴'] |
| C22 | ✅ | ✅ |  |
| D1 | ✅ | ✅ |  |
| D2 | ✅ | ❌ | BS total keys: ['total_nca', 'total_ca', 'total_assets', 'total_equity', 'total_ncl', 'total_cl', 'total_liabilities', 'total_equity_and_liabilities']; BS assets 1932777500 L+E 1932777500; P&L net None |
| D3 | ✅ | ✅ |  |
| D4 | ✅ | ✅ |  |
| D5 | ✅ | ✅ |  |
| D6 | ✅ | ❌ |  |
| D7 | ✅ | ✅ | Moadian rows: «	ARM-1298	1405/07/10	شرکت پارس‌افزار	28,340,000 ریال	1405/07/22 · 12 روز مانده	ارسال‌نشده	 Enter the company's tax memor» |
| D8 | ✅ | ✅ | insights → 200 0 |
| E1 | ✅ | ✅ |  |
| E2 | ✅ | ✅ | intake → 200 «صورتحساب Mellat را خواندم: 2 ردیف (1405/07/12 تا 1405/07/13) و با دفاتر مقایسه کردم. 1 ردیف در دفاتر نیست؛ 1 ردیف احتمالاً همان سند ثبت‌شده است (تاریخ یا شرح فرق دارد). مانده پایانی صورتحساب با دفاتر » |
| E3 | ✅ | ✅ |  |
| F1 | ✅ | ✅ |  |
| F2 | ✅ | ✅ | buttons a viewer sees on the ledger: [] |
| F3 | ✅ | ✅ |  |
| G0 | ✅ | ❌ | chart before: 85 accounts, Persian names: 0; reset → 200 |
| G1 | ✅ | ❌ | vat periods → 200 {'vat_registered': False, 'stagger': '1', 'periods': [{'start': '2026-07-01', 'end': '2026-09-30', 'deadline': '2026-11-07', 'key': '2026-09-30', 'days_left': 36, 'open': False}, {'start': '2026-04-01 |
| H1 | ✅ | ✅ | personal home: ai-accountant |

**45 of 46 pass** (run 1: 25 of 46).
