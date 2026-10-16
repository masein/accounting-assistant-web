# Deep browser test, 2026-10-16

**What was tested.** Everything since the 2026-10-07 run (retest 3), on `main` at #374 with this PR's fixes:
- pence (#345–#351);
- stock in the books (#355, #358, #366);
- a party's transactions window (#359);
- parties from accounts (#363, #364);
- bank SMS from the phone (#367);
- a person's books currency (#368, #371);
- what changed on the balance sheet (#370);
- reply, copy and share (#372);
- the web chat's cards (#373, #374).

Same setup as before: a fresh scratch database, the three tenants (Arman, `ir`, Persian; Thames, `uk`, English; Sara, personal) built through the UI, Playwright driving the scenarios in `docs/qa/SCENARIOS.md`, and results read back through the API.

**Result: 81 of 81 scenarios pass.** That is the 53 of retest 3 plus 28 new ones in a new group, `scripts/qa/qa_J.py`. The new ones by section:
- **Q2, Q3, Q7:** £7.50 through the ledger and the trial balance; too many places refused, in words, in both currencies; an invoice to the penny, paid in two parts.
- **S2:** the voucher form clears after saving.
- **T1, T12, T15, T20:** stock posting switched on, a purchase and a sale at weighted average, goods back on a credit note, a count. The books equal the valuation after each.
- **U1, U2:** a party's window newest first, and its search.
- **X1, X2, X6:** a debtor sub-account offered, made a party, and refused to a viewer.
- **Y1–Y3:** bank SMS from the phone, including who may send them and what is not filed.
- **Z3:** a new person's books in Omani rials, fixed once they have entries.
- **AA1, AA2, AA6–AA8:** what changed on the balance sheet, in Persian and in UK books.
- **AC1, AC3, AC4:** copy, share and reply on the web.
- **AE1–AE3:** a voucher the phone made from a statement, shown on the web as the card itself, confirmed there, posted when opened again, and posted on the phone too.

**Automated UI checks.** 198 page views across the language, size and tenant combinations, every section open: **0 page errors.** Before the fixes the checks found the phone tap targets (findings 4 and 5). Three flags remain, and they are not the app's: English names the test gave its own items and file, on Arabic and Persian pages.

**Findings.** Eight, in [FINDINGS.md](FINDINGS.md):
- six fixed in this PR, one of them P1 (a UK balance sheet ignored the dates chosen);
- one question for the owner (which side a party's sale is listed on);
- one note.

**The first run** had 12 failures among the regression scenarios, none of them the app's:
- eleven checks looked for Latin digits on Persian pages, which have used Persian digits since #338;
- one did not wait for the credit note's amount prompt, which since #366 opens after a request.

## Screenshots reviewed

| Page | What it shows |
|---|---|
| Arman, a phone's statement voucher on the web (AE1, AE2) | The card itself: «۶۱۱۲ سایر هزینه‌های عملیاتی — بد ۲٬۶۰۳٬۰۰۰», «۱۱۱۱ حساب بانکی بانک ملت — بس». After Confirm and a reload: «ثبت شد، ۱۲ مهر ۱۴۰۵», no buttons |
| Thames, the balance sheet's assets window (AA6, AA7) | Assets 0.00 on 2026-09-16 → 11,156.96 on 2026-10-15: the dates chosen, not today against a year before. The Liabilities bar now stands at the VAT and creditors owed |
| Arman, a party's transactions (U1) | Newest first, references on one line, the top row's balance «۶۳٬۴۷۸٬۵۰۰ ریال» is today's |
| Arman, the web chat on a phone (I) | Reply, Copy and Share have room to tap. The briefing's counts are fixed (finding 6) |

## Results

| ID | Result | Before | Notes |
|---|---|---|---|
| A1 | ✅ | — |  |
| AA1 | ✅ | — |  |
| AC1 | ✅ | — |  |
| AE1 | ✅ | — |  |
| A2 | ✅ | — |  |
| AA2 | ✅ | — |  |
| AE2 | ✅ | — |  |
| A3 | ✅ | — | status «مشخصات شرکت ذخیره شد»; bad logo → 200 «محتوای فایل با نوع اعلام‌شده همخوانی ندارد» |
| AC3 | ✅ | — |  |
| AE3 | ✅ | — |  |
| A4 | ✅ | — |  |
| AC4 | ✅ | — | the reply → 502 (no AI on the QA server) |
| A5 | ✅ | — |  |
| A6 | ✅ | — |  |
| AA6 | ✅ | — |  |
| AA7 | ✅ | — | asked as_of=2026-10-15&comparative_as_of=2026-09-16&currency=GBP&page=1&page_size=120 |
| AA8 | ✅ | — |  |
| B1 | ✅ | — |  |
| B2 | ✅ | — |  |
| B3 | ✅ | — |  |
| B4 | ✅ | — |  |
| C1 | ✅ | — | ledger page is the account summary; the journal is checked under D1 |
| C3 | ✅ | — | due after picking the Net 30 client: 2026-11-15; amount 14300000 subtotal 13000000 tax 1300000 |
| C4 | ✅ | — |  |
| C5 | ✅ | — |  |
| C6 | ✅ | — |  |
| C7 | ✅ | — | credit note: «برگ بستانکار صادر شد.»; after the credit note: issued balance 7000000 |
| C9 | ✅ | — | cheque status now settled |
| C10 | ✅ | — |  |
| C11 | ✅ | — |  |
| C12 | ✅ | — | status parsed rows 5; duplicate asks to confirm: «این فایل با یکی از فایل‌های قبلاً واردشده یکسان است. باز هم وارد شود؟» |
| C13 | ✅ | — |  |
| C15 | ✅ | — | link arman_emp → علی رضایی (200); below the approval threshold: no manager step (by design) |
| C16 | ✅ | — |  |
| C17 | ✅ | — |  |
| C18 | ✅ | — | run → 201 «» |
| C19 | ✅ | — |  |
| C20 | ✅ | — |  |
| C21 | ✅ | — | month picker: ['مهر ۱۴۰۴', 'آبان ۱۴۰۴', 'آذر ۱۴۰۴', 'دی ۱۴۰۴']; 1405-07: 6112 actual 110000000 of 50000000 |
| C22 | ✅ | — |  |
| C23 | ✅ | — | ARM-1: paid total 14300000 VAT 1300000 |
| D1 | ✅ | — |  |
| D2 | ✅ | — | BS total keys: ['total_nca', 'total_ca', 'total_assets', 'total_equity', 'total_ncl', 'total_cl', 'total_liabilities', 'total_equity_and_liabilities']; BS assets 1959825000 L+E 1959825000; P&L net None |
| D3 | ✅ | — |  |
| D4 | ✅ | — |  |
| D5 | ✅ | — |  |
| D6 | ✅ | — |  |
| D7 | ✅ | — | Moadian rows: «	ARM-1761	۱۴۰۵/۰۷/۱۲	فروشگاه مهرگان (شعبه ۲)	۱۴٬۳۰۰٬۰۰۰ ریال	۱۴۰۵/۰۷/۲۴ · 0 روز مانده	ارسال‌نشده	 شناسه یکتای حافظه مالی» |
| D8 | ✅ | — | insights → 200 0 |
| D10 | ✅ | — |  |
| D11 | ✅ | — |  |
| D12 | ✅ | — |  |
| E1 | ✅ | — |  |
| E2 | ✅ | — | intake → 200 «صورتحساب ملت را خواندم: ۲ ردیف (۱۲ مهر ۱۴۰۵ تا ۱۳ مهر ۱۴۰۵) و با دفاتر مقایسه کردم. ۲ ردیف در دفاتر نیست. مانده پایانی صورتحساب با دفاتر ۱۰۵٬۰۰۰٬۰۰۰ اختلاف دارد. برای رفع قدم‌به‌قدم روی «رفع قدم‌به‌قد» |
| E3 | ✅ | — |  |
| F1 | ✅ | — |  |
| F2 | ✅ | — | buttons a viewer sees on the ledger: [] |
| F3 | ✅ | — |  |
| G0 | ✅ | — | chart before: 85 accounts, Persian names: 0; reset → 200 |
| G1 | ✅ | — | vat periods → 200 {'vat_registered': False, 'stagger': '1', 'periods': [{'start': '2026-07-01', 'end': '2026-09-30', 'deadline': '2026-11-07', 'key': '2026-09-30', 'days_left': 22, 'open': False}, {'start': '2026-04-01 |
| H1 | ✅ | — | personal home: ai-accountant |
| L1 | ✅ | — | CFO receivables 63,225,000, payables 0; ledger 63,225,000 / 0 |
| L8 | ✅ | — |  |
| L9 | ✅ | — |  |
| Q2 | ✅ | — |  |
| Q3 | ✅ | — |  |
| Q7 | ✅ | — |  |
| S2 | ✅ | — | saved: «Voucher saved. Ledger updated.» |
| T1 | ✅ | — |  |
| T12 | ✅ | — | P&L cost of sales -750 |
| T15 | ✅ | — |  |
| T20 | ✅ | — |  |
| U1 | ✅ | — |  |
| U2 | ✅ | — |  |
| X1 | ✅ | — |  |
| X2 | ✅ | — |  |
| X6 | ✅ | — |  |
| Y1 | ✅ | — | statement: Mellat (SMS 1405/07) |
| Y2 | ✅ | — |  |
| Y3 | ✅ | — |  |
| Z3 | ✅ | — | first entry → 201 |

**81 of 81 pass.**
