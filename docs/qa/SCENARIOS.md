# Browser QA scenarios (living document)

The scenarios a full browser test walks through, each with its steps and the
result it expects. **Every feature or fix PR adds or updates the scenarios it
touches**, in the section where they belong, so the next run tests it too.
Results of each run go in `runs/<date>/` next to this file: `RESULTS.md` (one
line per scenario) and `FINDINGS.md` (what was wrong, by severity, with the PR
that fixed it).

The release-wide checklist for a production QA pass is `docs/QA_PLAN.md`;
this file is the browser-driven set, with fake data, run on a local
throwaway server.

**Where.** A throwaway local server (a scratch database, never a real one)
running the code under test. Fake data only. AI keys and SMTP are blanked, so
AI and mail take their "not configured" paths. Passwords are generated for
the run and never printed.

**How.** Every scenario is driven through the browser (Playwright/Chromium).
The API is used only to read results back or to make setup data that isn't
under test. Each step takes a screenshot. A scenario passes only if every
expected result holds, and failures are recorded with their evidence. UI/UX
findings are logged separately, by severity.

**Running it.** The Playwright scripts for these scenarios are in `scripts/qa/` (see its README): `sh scripts/qa/qa_all.sh` builds the throwaway server and runs every group.

**Adding a scenario.** Give it the next ID in its section (C23, D9, …), write
the steps as a user would do them and the expected result as something a
screenshot or a read-back can prove. Note the PR in brackets when a scenario
is new or changed, e.g. "[#259]". Keep the expected result in the user's
terms: what they see, in their language and calendar.

## Fake data

| Tenant | Kind / locale / currency / calendar | UI language | Users |
|---|---|---|---|
| **بازرگانی آرمان** (Arman Trading), Tehran | business / `ir` / IRR / Jalali | Persian | owner `arman_owner`, accountant `arman_acc`, CFO `arman_cfo`, manager `arman_mgr`, employee `arman_emp`, viewer `arman_view` |
| **Thames Studio Ltd**, London | business / `uk` / GBP / Gregorian | English, with Spanish and Arabic spot checks | owner `thames_owner`, accountant `thames_acc` |
| **خانواده سارا** (Sara's household) | personal / `ir` / IRR / Jalali | Persian | personal user `sara` |

**Arman's data**
- Clients: شرکت پارس‌افزار, فروشگاه مهرگان, کافه نارنج (one written with Arabic ي/ك).
- Suppliers: پخش البرز, چاپ سپهر.
- Bank: بانک ملت, current account 1110-01.
- Employees: علی رضایی (monthly), مریم کاظمی (hourly).
- Shareholders: 60% / 40%.
- Products: کاغذ A4 (box), کارتریج (each), خدمات طراحی (hour).
- Amounts: tens of millions of rials. Some are typed with Persian digits and the ٬ separator.

**Thames's data**
- Clients: Brightside Ltd, Ocean & Co.
- Supplier: Paperworks.
- Invoices: VAT 20% and 0%. One bill in USD.

---

## A. Platform and onboarding

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| A1 | Super-admin creates the three tenants | Sign in as super-admin → Companies → create Arman (ir, IRR, business), Thames (uk, GBP), Sara (personal) with their owners | All three listed with locale, currency and kind; each owner can sign in; no English labels in Persian; validation in the user's language; the list names each region (not uk/ir), has a column for the kind (business/personal), shows a logo only where one was uploaded (no 404s), and its AI header reads «هوش مصنوعی (۲۴ ساعت گذشته)» [#271] |
| A2 | First sign-in of a Persian owner | `arman_owner` signs in on a Persian browser | UI in Persian, right-to-left, Jalali calendar on by default; lands on the dashboard; the "what's new" tour (if shown) closes cleanly; no console errors; an owner the super-admin created for an `ir` company starts in Persian, a `uk` one in English [#264] |
| A3 | Company profile | Settings → profile: legal name, national ID, economic code, address and phone (Persian digits), logo PNG, signature PNG | Saved; logo shown in the sidebar and on documents; digits stored as 0–9; a wrong file type is refused in Persian |
| A4 | Team | Settings → users: add accountant, CFO, manager, employee and viewer | Each signs in and lands on their role's home; the nav shows exactly that role's pages (PAGE_ROLES); an invalid username or a weak password is refused in Persian, for each of the four password rules [#261] |
| A5 | Tenant isolation | A Thames user reads Arman data by URL and API ids | 404 or empty everywhere; never Arman's data |
| A6 | Sign-in errors and sign-out | Wrong password, then right; sign out; back button | Persian error; no account enumeration; after sign-out the app is unreachable without signing in again; on a device that never chose a language, the page follows the browser (Persian browser → Persian page and errors; an unsupported language → English); a language picked earlier still wins [#264] |
| A7 | Wiping the books [#259] | Parties page: no reset button. Settings → wipe (demo or empty): type a wrong name, then the company's name | Only the owner sees it, in Settings; a wrong name sends nothing and says «چیزی پاک نشد»; the right name wipes and the company keeps its own chart (a UK company gets the UK chart) |

## B. Master data (Arman, Persian)

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| B1 | Chart of accounts | Add 1110-01 «بانک ملت جاری» under 1110, rename it, try to delete a used account, deactivate an unused one | Tree updates; the code is suggested from its parent; deleting a used account is refused with the reason; inactive accounts leave the pickers; each row keeps «زیرحساب» and puts rename, retire and delete under «⋯» [#277] |
| B2 | Opening balances | Enter cash 500,000,000; bank 1,200,000,000; capital 1,700,000,000 on the opening date (Jalali) | An unbalanced entry is refused; the balanced one posts; the trial balance shows them |
| B3 | Parties | Add 3 clients, 2 suppliers, a bank, 2 employees and 2 shareholders (codes and phones in Persian digits); edit one; delete an unused one | Lists show each type in Persian; search for «كافه» finds «کافه نارنج»; a duplicate name warns; Edit/Delete work; the bank's own account is named «حساب بانکی بانک ملت» [#262]; a code typed «۱۰۱» is stored and found as 101 [#263]; for a bank the code field says it is the bank's ledger account; a typed code that isn't a cash or bank account is refused in Persian (not replaced), a bank sub-account is taken, and an empty code opens one [#276] |
| B4 | Products and stock | 3 products with SKU, unit and price; opening stock (IN) for 2 items; a barcode | Products page and inventory balance agree; valuation report in Persian; tables styled; a barcode or SKU typed in Persian digits is stored in 0–9 and a scan (0–9) finds the item [#263] |

## C. Daily bookkeeping

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| C1 | Manual voucher | Pick the date from the Jalali grid; rent 45,000,000 Dr 6xxx / Cr 1110-01 with a description; attach a receipt image; save | Unbalanced → Persian message; the save confirmation is Persian, with the Jalali date, each account's code and name, and amounts in the company's currency [#261]; balanced → "voucher saved"; it appears in the ledger with its Jalali date and attachment; an account code typed «۶۱۱۲» posts to 6112 and the confirmation names it [#263] |
| C2 | Voucher edit / delete | Link client and bank, edit the amount, then delete | Ledger and balances follow; the audit trail records each step in Persian |
| C3 | Sales invoice | Itemised, 2 lines, VAT, Jalali issue and due dates, client on «Net 30»; also in the evening, Tehran time [#258] | Due = issue + 30 on the user's own calendar day; the party list reads «name — مشتری» [#261]; totals right; it posts; Persian PDF (Jalali, Persian digits, amount in words); the tax-code list offers only Iran's codes (no UK_VAT_…) and the rate form suggests IR_VAT_STANDARD [#266]; the form's totals read like the list (0–9, the currency's own form: «£3,600»), the tax code and treatment fit their columns [#273]; an invoice dated from 1403/01/01 charges VAT at 10% (one dated before at 9%), and the rate list shows 9% closed on 1402/12/29 and 10% from 1403/01/01 [#279] |
| C4 | Payments on it | Record a partial payment (typed «۲۰٬۰۰۰٬۰۰۰»), then the rest | Partially paid → paid, shown in Persian in the list (kind and status) [#261]; AR aging and the client's statement agree |
| C5 | Purchase bill and payment | Bill from پخش البرز, pay it | AP up then down; the supplier's statement agrees |
| C6 | Quote → invoice | Quote, mark sent and accepted, convert | The invoice carries the quote's lines; the quote shows "converted" |
| C7 | Credit note and void | Credit note on C3; void another invoice | Postings reversed; statuses in Persian; each invoice row keeps payment, edit and PDF on the row and the rest under «⋯» (one line, the number unbroken); the menu opens on screen even for the last row of a long list, follows the page as it scrolls, its actions work and Escape closes it [#277]; a credit note on an unpaid invoice leaves it «صادرشده» with the credited amount shown (not «پرداخت جزئی»: nothing was paid); a void invoice can't be credited [#280] |
| C8 | Recurring invoice | Monthly, Jalali calendar, auto-issue | Next date is Jalali; run due → invoice issued once, not twice |
| C9 | Cheques | Received cheque (Sayad ID) → deposit → clear; issued cheque → print preview; one bounces | Each step posts its entry; a bounced cheque is not settled; every refusal in Persian: a duplicate Sayad ID, depositing an issued cheque, returning one at the bank [#261]; cheque layout errors in Persian |
| C10 | Instalments | Loan of 12 instalments | Schedule with Jalali dates; the reminder appears in the bell |
| C11 | Recurring payment rule | Monthly rent, auto-post, run due | One voucher; next run date moves forward; the bank list offers every bank account: the banks' own accounts, the chart's bank account and the ones opened under it (111001), not a retired one [#269] |
| C12 | Bank statement import | Upload a 15-row CSV → map columns → categorise → approve; re-upload it | Rows posted; the list shows the status in Persian and the type in capitals; a row that can't post says why in Persian ("ردیف ۳: …"), and so does the summary after approving [#261]; reconciliation matches the existing rent voucher; the re-upload is flagged as a duplicate; after an upload its rows open at once, titled in the user's words («بانک ملت — file.csv (2 ردیف)»; no "Unknown" when no bank is named) [#274] |
| C13 | Bank SMS | Paste 3 Persian SMS from Mellat | Parsed amount, date, type; posted or proposed |
| C14 | Petty cash | Float for علی; deposit «۵۰۰٬۰۰۰٬۰۰۰»; علی records an expense with a receipt; manager approves | Balance follows; app dialogs only; amounts in IRR; an expense over the float is refused in Persian [#261] |
| C15 | Expenses and mileage | Employee files a mileage claim → manager approves → reimbursed | Statuses in Persian; posting on approval and on reimbursement |
| C16 | Time | Project, rate, 3 billable entries → ready to invoice → invoice | The time is invoiced once; the preview range is Jalali |
| C17 | Purchase order | PO of 2 lines → partial receipt → bill → 3-way match | The order date defaults to today; a short receipt is flagged |
| C18 | Payroll | Profiles for علی (monthly) and مریم (hourly), 1405 rules; run Mehr → review → post → pay; payslip | Net = gross − withholdings; posting balanced; payslip in Persian |
| C19 | Fixed assets | Laptop and van; month-end depreciation; dispose of the laptop | Book value falls; disposal posts gain or loss |
| C20 | Equity | Cap table 60/40; contribution; declare and pay a dividend | Cap table 100%; the hint names IRR; statements show the movements |
| C21 | Budgets | Budgets for 3 expense accounts in Jalali months; overspend one | The alert fires; budget vs actual right; the category field suggests the chart's expense accounts, and a category given as a code reads with its account's name («6112 — سایر هزینه‌های عملیاتی») [#274]; a budget set by code counts that account's spending, and a group code («61») its sub-accounts' too — it read 0 spent, and its overspend alert never fired [#282] |
| C22 | Multi-currency | USD rate; a USD bill; revaluation preview | Base values in IRR; realised gain or loss on payment |
| C23 | Credit note on a paid invoice, and the customer's credit [#280] | Pay invoice X (1,000,000 + 10% VAT = 1,100,000) in full; credit 550,000 of it for a return and choose «keep as credit»; issue invoice Y to the same client and record a payment on it, using the credit; credit another paid invoice and choose «refund now»; credit a whole unpaid invoice; try to credit more than is left | The credit note is allowed on a paid invoice and reverses its VAT share: sales returns 500,000 and VAT 50,000 debited, the customer's credit 550,000 credited; X stays «پرداخت‌شده» and shows the customer's credit of 550,000; Y's payment offers the credit and settles from it with no bank line; «refund now» pays the credit back from the bank (customer credit debited, bank credited); a wholly credited unpaid invoice reads «برگشت‌شده»; more than is left to credit is refused in Persian; the VAT return and TTMS see each credit note once |

## D. Reports, control and compliance

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| D1 | Ledger search | Filter by Jalali range, account, party; text search with Arabic letterforms | Results right; totals right; export works |
| D2 | Financial statements | Trial balance, balance sheet, P&L, cash flow (Iranian formats); PDF and XLSX | TB balances; A = L + E; P&L agrees with the vouchers; preview columns (turnover, balance) and every date in a preview (the general journal's too) in Persian and Jalali [#261]; Persian documents; a trial balance (or general ledger) asked with only an end date takes every posting up to it [#265] |
| D3 | Dashboard and manager reports | Dashboard KPIs and charts; sales by product and client; aging; inventory | Figures agree with C1–C22; charts labelled in Persian; Jalali months; the owner pack is a readable list (not monospace text) dated in the company's calendar [#267] |
| D4 | CEO / CFO | Both pages; then a company that has recorded expenses but no bank or cash balance yet | KPIs, grade, runway; nothing English. With no cash recorded the alert says so and suggests the opening balance — it said «overdrawn» at exactly zero; below zero it still says overdrawn [#294] |
| D5 | Audit | Trail plus full audit | Every action of the run is in the trail; findings in Persian |
| D6 | Period lock | Lock through the end of Shahrivar; try a back-dated voucher | Refused, in Persian, naming the lock date; both dates in the refusal and the lock status itself («قفل تا 1405/06/31») are Jalali, in Persian and in English; a Gregorian company's stay Gregorian [#267] |
| D7 | Tax | TTMS season export; Moadian export of C3 | Files download; amounts agree with the invoices |
| D8 | Forecast and insights | 13-week forecast plus a what-if; insights after a duplicate payment | Forecast weeks in Jalali; the duplicate is flagged; on the dashboard each week's date stays on one line (it broke as «2026-09-» / «28») [#272]; every date an insight names (a weekend round amount, a new supplier's first payment, an outlier, the last statement, a missed recurring entry) is in the company's calendar — it said «on 2026-10-03» in a Jalali company [#284] |
| D9 | The books' language [#262] | After C1–C22, open the general journal, an account's ledger and a party's statement; then do the same in Thames | Every description the app wrote itself (invoice, payment, bill, credit note, void, opening balance, equity, payroll, mileage, time billing, depreciation, petty cash, recurring, FX, fees, statement rows) reads in Persian in Arman, with Jalali dates inside the text; in Thames the same entries read in English; nothing already posted is rewritten |
| D10 | Dashboard tabs [#36] | Open the dashboard as the owner of Arman, in Persian then English, on a laptop and a phone; go through each tab with the mouse, then with ←/→ on the tab bar; reload; record a payment and come back | At the top, always: the KPIs, smart alerts and what changed. Under them four tabs — Cash (13-week forecast, details and what-if), Receivables & payables (AR and AP aging), Spending & profit (expense by category, spend by vendor, profitability by client, budget vs actual), Books (books health and the month-end checklist, the owner pack, missing references, exports and alerts) — one open at a time, so the page is no longer 4,400 px; the tab bar is a real tablist (arrow keys, Home/End, aria-selected, focus ring) and scrolls sideways on a phone without the page scrolling; the open tab is kept after a reload; the budgets and missing references load only when their tab opens, and again when it is next opened after a change; everything in Persian and RTL, nothing English |
| D11 | The bell in the company's calendar [#292] | In Arman (Jalali): an overdue invoice, a Moadian deadline, a payday, a recurring rule and a reminder coming due; open the bell in Persian and English, and receive the same alerts as push notifications | Every date reads as the company writes it («1405/06/17»), in both languages and in the push. It said «2026-09-08» while every page beside it was Jalali. Thames (Gregorian) keeps 2026-09-08 |
| D12 | A part-paid invoice stays overdue [#293] | A sale of 11,000,000 due 10 days ago; the customer pays 4,000,000; a bill due next week, half paid; a third invoice credited down to nothing | The sale stays in the bell as overdue and says 7,000,000 is still open; the bill shows as due soon; the credited one leaves the bell. A part payment used to take the overdue alert away, although the e-mail reminders kept going |
| D13 | A new company's first look [#296] | A new UK company (GBP) with nothing booked yet opens the dashboard, Manager reports and CEO Mode, in Persian and English | Every figure is in GBP: the dashboard said «0 IRR» until the first entry. Each chart with no figures says «هنوز داده‌ای وجود ندارد.» / "No data yet." in the middle of its box; they were blank boxes that looked broken. A chart with any figure draws as before |
| D14 | A report that doesn't load says so [#299] | Open CEO Mode and CFO Mode while their report fails: the server refuses it, or sends something unreadable | An error alert in the page's language (the server's own message when it gave one); the page used to stay blank with only a console warning, which no test noticed |
| D15 | What is owed from over a year ago [#302] | Books with a sale 14 months old still unpaid and a supplier owed since then; then the old sale paid last month | The dashboard's receivables aging shows the sale in its oldest bucket and «Liabilities payable» includes the old debt — both were the last 12 months' movements, so they vanished (a migration with years of history would have shown none of its old balances). Once paid, the sale leaves the aging, with nothing negative left behind |

## E. AI accountant and migration (no AI provider)

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| E1 | Chat without a provider | Ask a question | "The assistant can't answer right now" in Persian; nothing breaks |
| E2 | Statement intake in the chat | Attach the C12 CSV in the chat | The deterministic intake proposes rows without AI, or says what it needs; a CSV or Excel statement is imported and reviewed with no AI at all, even when its name and the message don't say "statement" (its header and dated rows do); a journal export (no running balance) is not taken for one; with no bank named, the reply and card don't say "Unknown" [#268] |
| E3 | Migration | Opening chart and balances from a Sepidar-style Excel; journal import CSV | Preview, map, confirm; balances agree; the closed period is respected |
| E4 | A year's closing in an Iranian export [#303] | Import two fiscal years' journals from Sepidar, oldest first, each with its opening voucher (سند افتتاحیه), the closing of the temporary accounts and the سند اختتامیه | The closings are left out and named in the preview; the first year's opening posts and the second's is left out (the year before is in). The years' sales, receivables and cash stand as entered — posted as they came, the closings wiped each year out (revenue 0, a balance sheet of zeros) and the second opening doubled every balance |

## F. Roles

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| F1 | Employee | `arman_emp`: nav and direct URLs to invoices or settings | Only time, expenses and petty cash; other pages redirect to home |
| F2 | Viewer | `arman_view` tries to save anything | Read-only pages; any write refused in Persian (no blank failure) |
| F3 | Manager / CFO | `arman_mgr` approves claims; `arman_cfo` opens CEO/CFO | Exactly their permissions |

## G. UK company (English) and other languages

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| G1 | UK bookkeeping | Opening balances; VAT 20% invoice; bill; VAT return boxes 1–9; FRS 102 statements; MTD ITSA quarter export | Box figures agree with the invoices; English PDFs; £ everywhere; nothing Iranian on its pages: no Moadian or TTMS panel, no Sayad id on cheques, only UK tax codes in the rate list and the invoice line, and the payroll rules show only the UK's figures [#266]; the invoice line has no Moadian goods-id column [#273] |
| G2 | Spanish | Switch Thames to Spanish; walk every page | No English left; numbers and dates right |
| G3 | Arabic | Switch to Arabic | Right-to-left mirrored; no English; Gregorian dates |

## H. Personal mode (Sara)

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| H1 | Personal dashboard | Income and spending entries; a loan with instalments; a savings goal; a budget alert; gold holding | Personal pages only; reminders; month report card in Persian |
| H2 | Household | Invite a member (link) | Link copy works; shared tenant |

## I. UI/UX review, every page

Each page is checked in Persian and English, at desktop 1280, tablet 768 and phone 375, with data present and with the page empty. The checks are partly automated, plus a screenshot review of each:

| ID | Check |
|---|---|
| I1 | No horizontal page overflow; nothing clipped or overlapping; tables scroll inside their box on a phone; the browser suite checks every page at 768 px too, in English and Persian (the Ledger's toolbar was 49 px too wide) [#275]; the bell's panel fits a phone in Persian and English (it ran 30 px off one side) [#283]; a phone never zooms out (layout no wider than the screen — the overflow check alone can't see a zoomed-out page), Settings included: its users and AI usage tables made it 451 px [#288] |
| I2 | Right-to-left mirrors correctly (icons, arrows, alignment, number and date runs); English stays left-to-right |
| I3 | No untranslated text, raw keys (`msgFoo`) or `{placeholders}`; consistent terms between pages; the lists the server builds read in the page's language too — an invoice's Moadian problems, a bank SMS it couldn't read, a statement's analysis, an upload's or an import's errors, a pay run's warnings (they were English sentences on Persian pages) [#285]; checked in Persian for English sentences mixed with Persian words too («Enter the company's tax memory id (شناسه یکتای حافظه مالیاتی) in the مودیان settings.» passed the check for having Persian in it) [#288] |
| I4 | Every input and button has an accessible name; focus is visible; Tab order follows the reading order; dialogs close on Escape; a Jalali date field is one field to a screen reader (its hidden native input is aria-hidden) [#270]; the bell panel's reminder date and repeat too [#288] |
| I5 | Tap targets at least 32 px on a phone; the font is never under 11 px; checked in the browser suite on a phone in Persian: chips, small buttons, the invoice line's ×, checkboxes; the sidebar's section labels are at least 12 px, with no letter-spacing in Persian or Arabic [#270]; the chat's session rename and delete (shown without hover on a touch screen), the password eye, the daily-digest checkbox and a reminder's delete as well (they were 20×17, 24×24 and 13×20 px) [#283] |
| I6 | Every action gives feedback (success, error, loading); destructive actions confirm; disabled states are clear |
| I7 | Empty states say what to do next; loading states don't flash English or raw data |
| I8 | Money and dates follow the company's currency and calendar everywhere; Persian digit input works in every number field |
| I9 | No console errors, failed requests (4xx except expected 403) or slow pages (> 3 s to idle) |
| I10 | Visual consistency: one style for buttons, cards, tables and badges; spacing rhythm; heading hierarchy |
| I11 | Open states [#288]: on every page, each collapsible section opened and given time to load what it fetches, the first row's ⋯ menu open; once per language and size, the bell's panel and the account menu open — each scanned like a page (I1–I5) and screenshotted. The page scans ran with these closed, which is how the bell's panel running off a phone (#52) and the Moadian panel's English (#53) got past them |
| I12 | Numbers read the same everywhere [#298] | Open CEO Mode, CFO Mode, inventory, the chat's report cards and a bank statement with the browser set to Persian, then Spanish | Every amount has Latin digits and comma groups (74,250,000), as the rest of the app does. 47 places followed the browser's locale instead: Persian digits beside Latin ones on one page, «74.250.000» with dots on a Spanish browser |

## J. Integrity of money movements (security review, 2026-10-06)

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| J1 | One reversal per entry [#289] | Reverse a journal entry from the ledger; reverse it again; then void the invoice it belonged to | The second reversal is refused in the user's language; the void doesn't reverse it a third time; the books show the entry reversed once |
| J2 | Voiding an invoice with a credit note [#289] | Credit a paid invoice, keep the credit, void the invoice; on another, refund its credit and try to void it | The first void reverses the credit note too: no customer credit, receivable or VAT left over. The second is refused until the refund is reversed |
| J3 | A reversed credit use or refund gives the credit back [#289] | Use a customer's credit on their next invoice, then reverse that payment; reverse an overpayment | The credit is available again after the first; the overpayment's credit is gone after the second; the app and the customer-credit account agree |
| J4 | A credit can't be spent twice [#289] | Two refunds of the same credit at the same moment (or a double-click) | One succeeds, the other is refused or waits and then finds nothing left; never two refunds of one credit |
| J5 | Refunds and payments go to a bank, cash or cheque account [#289] | Refund a credit naming a capital or expense account as the bank; pay an invoice with a received cheque | The first is refused in the user's language; the cheque lands in cheques receivable as before |
| J6 | A cheque that overpaid, bounced [#289] | Take a customer's cheque of 12,000,000 for an invoice of 10,000,000; bounce it; deposit it again; then a second one that overpays, whose extra is refunded, and bounce it | After the bounce: the invoice owes 10,000,000 again, no customer credit is left in the app or the books, and the receivable is 10,000,000 (it was 12,000,000). After the deposit: paid, with one credit of 2,000,000 (it became two). The second bounce is refused until the refund is reversed. The same holds for an issued cheque that overpaid a bill, and for an unused cheque handed back |

## K. Performance on a year of books (2026-10-06)

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| K1 | A busy year [#290] | `scripts/perf_bench.py` on a scratch database: 20,000 journals, 2,500 sales invoices and 800 bills over 12 months, most paid, some part-paid, some credited, 100 parties | Every list and page fetch under 1 s, every report under 2 s (median of 3, warm). The month-end close pack, a download of a PDF, a workbook and the journal as CSV, under 4 s: most of it is laying out the PDF |
| K2 | No query per row [#290] | `tests/test_list_performance.py` and `tests/test_report_performance.py`: count each endpoint's SQL statements on small books, then on five times the invoices (or four times the journals) | No endpoint's statement count grows with the books; the batched figures equal the one-invoice-at-a-time ones |

## L. CFO and CEO receivables and payables (2026-10-06)

Receivables are what customers owe: trade receivables and, in Iranian books, cheques received and not yet cleared. Payables are what the company owes its suppliers: trade payables and cheques issued and not yet cleared. VAT, prepayments, payroll and accruals are other balances. Each scenario reads CFO Mode, CEO Mode and `/brain/cfo/report`.

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| L1 | An issued invoice counts once [#291] | Issue a sale of 11,000,000 and a bill of 5,500,000 | Receivables 11,000,000 and payables 5,500,000, the same as the trial balance's trade receivable and payable accounts. They were 22,000,000 and 11,000,000: each invoice was added to the ledger it had already posted to |
| L2 | Part payments and credit notes [#291] | Pay 4,000,000 of the sale; credit 1,000,000 of it | Receivables 6,000,000 |
| L3 | Drafts are not owed [#291] | Save a draft invoice of 3,000,000 | Receivables unchanged |
| L4 | Books kept on a cash basis [#291] | An issued invoice that never posted its receivable (an older invoice), part-paid | Its open balance counts; once paid it drops out |
| L5 | Older than a year [#291] | A sale issued 14 months ago, still unpaid | It counts. The 12-month window used to drop it |
| L6 | One currency at a time [#291] | An IRR company with an unpaid USD 1,000 invoice | The IRR view leaves it out, the USD view shows 1,000, and the combined view counts its rial value. The USD amount used to be added to the rials as a raw number |
| L7 | Only trade balances [#291] | VAT on a sale, a prepaid expense, a month's payroll liabilities; in Iranian books, a customer's cheque in hand and one at the bank, and a cheque issued to a supplier | VAT, the prepayment and payroll don't move either figure. The received cheques stay in receivables until they clear, and the issued cheque stays in payables |
| L8 | Every place says the same [#291] | Read CFO Mode, CEO Mode, the CFO's answer to "cash leaks", and the receivables-growth insight | The same receivables and payables in all three; the insight measures the same accounts. Reading a report adds no accounts to the chart |
| L9 | CEO Mode's balance sheet balances [#297] | Arman before the year is closed: open CEO Mode | Assets = liabilities + equity, and equity includes the period's result as its own line («سود (زیان) دوره جاری»), as the formal balance sheet does. It left the result out: assets 1,970,825,000 against liabilities 107,535,000 and equity 1,900,000,000, a gap of exactly the 36,710,000 loss |
| L10 | The months in order [#297] | Revenue in one month, costs only in the next; then four months of costs entered oldest last | CEO Mode's trends show both months, the second with its loss (it vanished: the months came from revenue only); the burn rate is the mean of the three latest months, not of the last three entered |
| L11 | Quiet months count [#300] | Sales in two of the last four months; costs three months ago and six months ago, none since | Average monthly revenue spreads the sales over all four months (it averaged the two busy ones only), and the burn rate is the last three months' costs, quiet ones as 0 — not the last three months that had any |
| L12 | Month over month, complete months [#300] | Early in a month, before its first sale: sales of 10,000 two months ago and 12,000 last month; then a fall to 5,000, and costs up 40% | No alarm while the month is under way — CFO and CEO Mode compared it, unfinished, with last month and said «revenue declined 100%», raising the risk score, at the start of every month. A real fall between the last two complete months is flagged, naming both months; a cost rise names its month |

## M. Each module against the books and the reports (2026-10-07)

Each module runs a realistic month through the API, then its own figures, the ledger, the statements and the dashboard must agree with numbers worked out by hand (`tests/test_module_crosscheck.py`).

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| M1 | A month of Iranian payroll [#304] | Two employees under the 1405 rules: 200,000,000 and no children; 500,000,000 and one child. Run Shahrivar, post, pay | Payslips: 252,000,000 gross / 17,640,000 insurance / 57,960,000 employer / no tax / 234,360,000 net, and 568,625,550 / 38,640,000 / 126,960,000 / 12,998,555 / 516,986,995. The ledger, the insurance and tax lists, the year summary, the income statement's SG&A (1,005,545,550) and the dashboard's liabilities (254,198,555) all say the same; nothing is left in net pay owed |
| M2 | Inventory costing [#304] | In 10 at 100, in 10 at 200, out 15; switch between weighted average and FIFO | Weighted average: COGS 2,250, 5 left worth 750; FIFO: COGS 2,000, worth 1,000 — on the balance page and the valuation, each showing the other method too. Stock movements don't post to the ledger (no cost of sales on the income statement): a known gap, roadmap §4.4's next step |
| M3 | A fixed asset bought, depreciated and sold [#304] | A computer of 36,000,000 over 36 months and desks of 6,000,000 over 60, bought by bank months ago; run depreciation twice; sell the desks today for 5,000,000 | 1,000,000 and 100,000 a month; the second run posts nothing; the register, the ledger (1210, 1219, 6120) and the balance sheet agree before and after the sale, which takes the months before it first and books the gain or loss |
| M4 | A UK VAT quarter [#304] | July–September 2026: a sale of 1,000 + 20%, a zero-rated sale of 300, a purchase of 400 + 20%, a 120 credit note on the first sale | Boxes 1 = 180, 3 = 180, 4 = 80, 5 = 100 payable, 6 = 1,200, 7 = 400 — and the ledger's VAT accounts (2200, 1400) say the same |
| M5 | The income tax update for that quarter [#304] | Self-employment, 2026-27 quarter 2 | Income 1,200, expenses 400, profit 800 — the same as the profit and loss for those dates |
| M6 | A personal month [#304] | Shahrivar 1405: salary 120,000,000; food, rent and transport 90,000,000; 20,000,000 of gold (10 g, 2,500,000 a gram at month end) | Report card: income 120,000,000, spending 90,000,000, saved 30,000,000 (25%), categories adding up to the spending; net worth 35,000,000 with 5,000,000 unrealised on the gold, the same on the report card |
| M7 | Picking an employee loads their pay profile [#306] | Ali's profile: salaried, 150,000,000, statutory rules, 2 children, seniority, hired 1404/01/15. On the payroll page, pick Ali, change the children to 3, save | Picking Ali fills the form with his profile. After saving, only the children changed: salary, mode, seniority and hire date stand (a blank form was saved over them: salary 0, flat rates at 0%). Picking an employee with no profile resets the form to the defaults. The table gives the hire date in the company's calendar, «از 1404/01/15» (it read «از 04-04-2025», the ISO date reversed by the Persian around it). Through the API, an update changes only the fields it sends (`monthly_standard_hours`, `active` and the currency are no longer reset by a save that doesn't mention them) |
| M8 | A new profile follows the rules in force [#306] | An Iranian company (the 1405 rules) and a UK one (2026/27): add a profile without touching "Tax & insurance" | The form starts on "Statutory rules", so insurance and income tax are computed (it started on flat rates at 0%: gross = net, nobody on the insurance list). With no rule set covering today, it starts on flat rates, as before |

## N. The Android app's server (roadmap `ROADMAP_ANDROID_CHAT.md`, 2026-10-10)

The phone talks to `/api/mobile/v1` with bearer tokens issued per device (`tests/test_mobile_sessions.py`).

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| N1 | Sign in from a phone [#308] | Sign in with a username, password and the device's name; then as a user with two-factor; then with the seeded default password; then into a suspended company | An access token (15 minutes), a refresh token and a device row named after the phone. With two-factor, a challenge first and the tokens after the code. The default password is refused («password_change_required»: change it on the web first); a suspended company is refused. A wrong password counts towards the same limits as the web sign-in |
| N2 | Bearer requests [#308] | Call the API with the access token, without a CSRF header; put a web session token in the `Authorization` header; let the access token expire | The request is served: a bearer token is not ambient, so CSRF does not apply. A web cookie token is refused as a bearer (no device), and an expired one returns 401 with `session_expired`, so the app knows to refresh |
| N3 | Refresh rotates [#308] | Refresh; then present the old refresh token again | A new pair, and the old refresh token stops working. Presenting a used refresh token again (a stolen copy) revokes the device: its access and refresh tokens both stop |
| N4 | Devices [#308] | List my devices; revoke one; sign out on the phone; change the password on the web | The list marks this phone. A revoked device's access token is refused on its next request and its refresh fails. Signing out revokes this device only. A password change ends every device's session |
| N5 | An app too old to talk to the server [#308] | Send `X-App-Version` below the server's minimum | 426 with `upgrade_required` and the minimum version, so the app can ask for an update |
| N6 | The phone's chat answers in blocks [#309] | Ask the balance of 1110 and to record rent of 80,000,000 from the bank | One reply: a figure (the balance, from the ledger), a voucher (title, 80,000,000 IRR, the date in the company's calendar, «29 Shahrivar 1405», and the lines with their account names), then the accountant's sentence, in that order. The blocks are kept on the message |
| N7 | Confirm, cancel and undo from the phone [#309] | Confirm the voucher; confirm it again; undo it. Draft another and cancel it, then try to confirm it; set an approval threshold below a third and confirm that | A stamped receipt with the voucher's reference and two minutes to undo; the second confirm returns the same receipt with no undo; undo takes it back. A cancelled card can't be confirmed (409) and cancelling twice is fine. Above the threshold the answer is «waiting for approval», not a posting |
| N8 | Reopening a thread redraws its cards [#309] | List my threads and open one | The user's words, and each reply's blocks with every voucher's state now (pending, executed, cancelled); the tool-calling steps in between are not shown. Someone else's thread is 404 |
| N9 | The web's Cancel is real [#309] | Cancel a card on the web; a card that creates a party | The server marks the proposal cancelled, so nothing can confirm it later (the web only greyed the card). A card that creates a party now says so: the API passed `new_entities` on at last |
| N10 | Common questions answered from the books [#310] | Ask «موجودی چقدره؟», "how much cash do we have?", "who owes us?", «به کی بدهکاریم؟», "how much did we spend this month?", «بودجه چقدر مونده؟» | Answered without the model, exactly and at once: the read tool's result as a block (the cash figure with each account, the open invoices, the categories) and one sentence in the user's language, Persian digits in Persian, open invoices per currency. The turn is kept in the thread. It works with the AI switched off |
| N11 | …and nothing else is taken from the model [#310] | "record 500 cash for lunch", "how much cash will we have next month?", «پیش‌بینی نقدینگی ماه بعد», a long message that mentions the bank | Each goes to the model as before: an amount means recording, the future is the forecast's job, and a long message isn't a quick question |
| N12 | Signing in on the phone [#311] | Open the app; sign in with a password; then as a user with two-factor; with a wrong password; with the network off; close and reopen the app | The seal and «دفترت را با یک گفتگو نگه دار», then the chat. With two-factor the code field follows the password. A wrong password shows the server's message in the user's language; offline says so and keeps the fields. Reopened, the app goes straight back to the last conversation: the session is sealed with a Keystore key |
| N13 | The chat on the phone [#311] | First run; ask the cash; ask to record a spending; Post it; Undo; draft another and Cancel it; one above the approval threshold | First run offers questions the books answer at once. The figure and the sentence; a voucher with its lines, amount and date, the books' name and colour. Post lands the seal (number, «ثبت شد», date) with a confirm tick and a two-minute undo ring; Undo says the books are as they were. Cancel says cancelled. Above the threshold the voucher says it waits for a second approval. A block this version can't draw shows its sentence |
| N14 | The phone's session over time [#311] | Let the access token expire; revoke the phone from the web; change the password; ship a minimum version above the app's | An expired token is renewed once, silently, and the request goes through. A revoked phone or a changed password brings the app back to sign-in. An app too old is told to update |
| N15 | A photo, a file, a voice note from the phone [#312] | Upload a receipt photo; a file whose bytes aren't the type it claims; an executable; send an empty recording to be transcribed | The photo is stored like a web attachment and its id goes with the chat message; the mislabelled file and the executable are refused; the empty recording is refused by the transcriber before any AI call |
| N16 | The accountant speaks first [#312] | Open the app when something needs attention; then when nothing does | The briefing comes as a text block (`kind: briefing`), kept in the thread so it redraws; with nothing to say there are no blocks |
| N17 | Photos, files and voice notes on the phone [#313] | Take a photo of a receipt; choose a PDF; hold the mic and say «اجارهٔ مهر را ثبت کن»; send | The photo is turned upright, scaled to 1600 px and uploaded; each file waits as a chip and goes with the message, named in the bubble. While the mic is held, voice bars and «در حال شنیدن…»; the words land in the composer to check before sending. The first press asks for the microphone |
| N18 | Share → Accountant [#313] | From the SMS app share a bank SMS; from the bank app share a statement PDF | The SMS text lands in the composer; the statement uploads and waits as a chip; the user sends them as they are or with a word |
| N19 | The account sheet and the app lock [#313] | Tap the avatar; sign the old phone out; turn the lock off and on; leave the app for three minutes; sign out | The user, the books and the role in the user's language; the phones with this one marked; the old phone signed out (its next request fails). With the lock on, a cold start and a return after two minutes ask for the fingerprint, face or PIN. Signing out returns to sign-in |
| N20 | The phone sees the accountant working [#314] | Ask for the bank balance and a voucher, streamed; ask with the AI down; ask «موجودی چقدره؟» | Server-sent events as it happens: «در حال فکر کردن…», «در حال بررسی دفاتر…», «در حال نوشتن پیش‌نویس سند…» (in the user's language), then the reply's blocks, then `done`. With the AI down, an `error` event with the status and `ai_unavailable`. A question the books answer at once streams just its reply |
| N21 | A retry doesn't ask twice [#314] | Send a message with the phone's own id; the connection drops; the phone sends it again with the same id | The second send gets the first reply back (`stop_reason: repeat`); the model is not asked again and nothing is drafted twice |
| N22 | An invoice comes back as a file [#315] | Ask the accountant to invoice Aria for 3 hours of consulting and confirm it; then ask «فاکتور ۱۰۴۲ رو نشون بده» | The stamped receipt carries the invoice's PDF as a file card (name, Open, Share); Share hands the PDF to Telegram, WhatsApp, Eitaa or e-mail. Asking for an invoice answers with its PDF too. The PDF is the one the web prints |

**Run order:** A → B → C → D → E → F → G → H → J → L → M → N; K on its own scratch database; I runs across all of it. Results go in `runs/<date>/RESULTS.md`.
