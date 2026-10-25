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
| C2 | Voucher edit / delete | Link client and bank, edit the amount, then delete | Ledger and balances follow; the audit trail records each step in Persian; each row's details read as words and dates («۱۴۰۵/۰۷/۲۱ · X-875 · هزینهٔ هفته»), never JSON with its Persian escaped (deep test 2026-10-20) |
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
| E2 | Statement intake in the chat | Attach the C12 CSV in the chat | The deterministic intake proposes rows without AI, or says what it needs; a CSV or Excel statement is imported and reviewed with no AI at all, even when its name and the message don't say "statement" (its header and dated rows do); a journal export (no running balance) is not taken for one; with no bank named, the reply and card don't say "Unknown" [#268]. In Persian the reply writes «۳٬۵۰۰ ردیف (۱ شهریور ۱۴۰۵ تا ۳۰ شهریور ۱۴۰۵)», or the Gregorian months in Persian for a Gregorian company, never "3500" or an ISO date that turns round inside the sentence; Arabic likewise in its own digits [#331] |
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
| I12 | Numbers read the same everywhere [#298, #338] | Open CEO Mode, CFO Mode, inventory, the chat's report cards and a bank statement with the app in Persian, then Spanish, then Arabic | In Persian every number on every page is in Persian digits with «٬» groups (۷۴٬۲۵۰٬۰۰۰), as the chat and the phone write them (the owner's call, 2026-10-12); in Spanish, Arabic and English, Latin digits with comma groups (74,250,000). Never a mix on one page, and never the browser's own locale: 47 places once followed it, giving Persian digits beside Latin ones and «74.250.000» with dots on a Spanish browser [#298] |
| I13 | Persian digits on the web, safely [#338] | With the app in Persian: the dashboard, reports, invoices and their print, CEO and CFO Mode with their charts, inventory, payroll, the chart of accounts, the ledger and the chat; pick an account from a list; edit an invoice whose amount the form shows; type an amount in Persian, then in Latin digits; export a report to CSV and Excel; switch to English | Every figure, rate, percentage, count, date and account code shown is in Persian digits, chart axes, labels and tooltips too; the account a list picks is saved under its usual code. A form that shows an amount saves it unchanged, and a field takes Persian or Latin digits alike. CSV and Excel exports keep Latin digits (they are read by other programs). In English nothing changes |

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
| M7 | Picking an employee loads their pay profile [#306] | Ali's profile: salaried, 150,000,000, statutory rules, 2 children, seniority, hired 1404/01/15. On the payroll page, pick Ali, change the children to 3, save | Picking Ali fills the form with his profile. After saving, only the children changed: salary, mode, seniority and hire date stand (a blank form was saved over them: salary 0, flat rates at 0%). Picking an employee with no profile resets the form to the defaults. The table gives the hire date in the company's calendar, «از 1404/01/15» (it read «از 04-04-2025», the ISO date reversed by the Persian around it). Through the API, an update changes only the fields it sends (`monthly_standard_hours`, `active` and the currency are no longer reset by a save that doesn't mention them) The employee list appears only once the profiles are loaded, so an employee picked at once on a slow connection gets their profile, never the blank defaults [#319] |
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
| N10 | Common questions answered from the books [#310] | Ask «موجودی چقدره؟», "how much cash do we have?", "who owes us?", «به کی بدهکاریم؟», "how much did we spend this month?", «بودجه چقدر مونده؟» | Answered without the model, exactly and at once: the read tool's result as a block (the cash figure with each account, the open invoices, the categories) and one sentence in the user's language, Persian digits in Persian, open invoices per currency. The turn is kept in the thread. It works with the AI switched off Personal books are offered a person's questions (how much money I have, this month's spending, my budget, my net worth), each answered the same way, in all four languages [#335] |
| N11 | …and nothing else is taken from the model [#310] | "record 500 cash for lunch", "how much cash will we have next month?", «پیش‌بینی نقدینگی ماه بعد», a long message that mentions the bank | Each goes to the model as before: an amount means recording, the future is the forecast's job, and a long message isn't a quick question |
| N12 | Signing in on the phone [#311] | Open the app; sign in with a password; then as a user with two-factor; with a wrong password; with the network off; close and reopen the app | The seal and «دفترت را با یک گفتگو نگه دار», then the chat. With two-factor the code field follows the password. A wrong password shows the server's message in the user's language; offline says so and keeps the fields. Reopened, the app goes straight back to the last conversation: the session is sealed with a Keystore key |
| N13 | The chat on the phone [#311] | First run; ask the cash; ask to record a spending; Post it; Undo; draft another and Cancel it; one above the approval threshold | First run offers questions the books answer at once. The figure and the sentence; a voucher with its lines, amount and date, the books' name and colour. Post lands the seal (number, «ثبت شد», date) with a confirm tick and a two-minute undo ring; Undo says the books are as they were. Cancel says cancelled. Above the threshold the voucher says it waits for a second approval. A block this version can't draw shows its sentence |
| N14 | The phone's session over time [#311] | Let the access token expire; revoke the phone from the web; change the password; ship a minimum version above the app's | An expired token is renewed once, silently, and the request goes through. A revoked phone or a changed password brings the app back to sign-in. An app too old is told to update |
| N15 | A photo, a file, a voice note from the phone [#312] | Upload a receipt photo; a file whose bytes aren't the type it claims; an executable; send an empty recording to be transcribed | The photo is stored like a web attachment and its id goes with the chat message; the mislabelled file and the executable are refused; the empty recording is refused by the transcriber before any AI call |
| N16 | The accountant speaks first [#312] | Open the app when something needs attention; then when nothing does | The briefing comes as a text block (`kind: briefing`), kept in the thread so it redraws; with nothing to say there are no blocks The briefing names the month in the reader's words and, in Persian, writes its figures in Persian digits: «درآمد در شهریور ۱۴۰۵ ۱۰۰٪ کم شد», never «1405-06» [#337] |
| N17 | Photos, files and voice notes on the phone [#313] | Take a photo of a receipt; choose a PDF; hold the mic and say «اجارهٔ مهر را ثبت کن»; send | The photo is turned upright, scaled to 1600 px and uploaded; each file waits as a chip and goes with the message, named in the bubble. While the mic is held, voice bars and «در حال شنیدن…»; the words land in the composer to check before sending. The first press asks for the microphone |
| N18 | Share → Accountant [#313] | From the SMS app share a bank SMS; from the bank app share a statement PDF | The SMS text lands in the composer; the statement uploads and waits as a chip; the user sends them as they are or with a word |
| N19 | The account sheet and the app lock [#313] | Tap the avatar; sign the old phone out; turn the lock off and on; leave the app for three minutes; sign out | The user, the books and the role in the user's language; the phones with this one marked; the old phone signed out (its next request fails). With the lock on, a cold start and a return after two minutes ask for the fingerprint, face or PIN. Signing out returns to sign-in Signing out and in as someone else (the owner, then a manager) shows the newcomer none of the last person's conversation, cards or Undo [#326]. On a phone with a screen lock the app opens to the prompt and never closes itself; on Android 9 the prompt is the phone's own PIN screen [#329] |
| N20 | The phone sees the accountant working [#314] | Ask for the bank balance and a voucher, streamed; ask with the AI down; ask «موجودی چقدره؟» | Server-sent events as it happens: «در حال فکر کردن…», «در حال بررسی دفاتر…», «در حال نوشتن پیش‌نویس سند…» (in the user's language), then the reply's blocks, then `done`. With the AI down, an `error` event with the status and `ai_unavailable`. A question the books answer at once streams just its reply |
| N21 | A retry doesn't ask twice [#314] | Send a message with the phone's own id; the connection drops; the phone sends it again with the same id | The second send gets the first reply back (`stop_reason: repeat`); the model is not asked again and nothing is drafted twice |
| N22 | An invoice comes back as a file [#315] | Ask the accountant to invoice Aria for 3 hours of consulting and confirm it; then ask «فاکتور ۱۰۴۲ رو نشون بده» | The stamped receipt carries the invoice's PDF as a file card (name, Open, Share); Share hands the PDF to Telegram, WhatsApp, Eitaa or e-mail. Asking for an invoice answers with its PDF too. The PDF is the one the web prints |
| N23 | Conversations on the phone [#316] | Open the conversations; open an older one; start a new one | The threads, most recent first, the current one marked, each with its message count and date (Jalali in Persian, «۱۸ مهر»; the year when it isn't this one). An older thread opens with its cards redrawn; a new one starts on the suggestions |
| N24 | The app in Arabic and Spanish [#316] | Set the phone to Arabic, then Spanish; tap the first-run questions | Every word of the app in that language, Arabic laid out right to left; «كم لدينا من المال؟» and "¿Cuánto dinero tenemos?" are answered from the books at once, like their English and Persian twins Changing the phone's language later, the accountant answers in the new one and the first-run questions follow; a statement sent again comes back once, in the language, with its card; a refused message is drawn once [#334] Arabic writes Western digits everywhere, the figures, dates and replies alike («£2,500», «10 أكتوبر», «0 GBP») [#336] |
| N25 | Typed with no network [#317] | Airplane mode on; type «۱۲۰ هزار تومان چای»; take a photo of a receipt and send it; close the app; airplane mode off | Each message stays under its bubble: "Waiting for the network. It sends itself." with Don't send. Nothing is lost and nothing is typed twice. With the network back, it sends itself even with the app closed: the photo is uploaded once, and each message is answered once (opening the app shows the drafts in the thread). A message the accountant couldn't answer (AI down) says why, with Try again and Don't send. Signing out leaves nothing queued for the next person |
| N26 | A long conversation, and coming back to it [#317] | Open a conversation of 200 messages; tap Earlier messages; leave the app, add a message from the web chat, come back | The latest messages first, with Earlier messages at the top bringing the page before, in place. Back in the app, the web's turn appears without reloading, and nothing the phone already shows is drawn twice |
| N27 | Change a draft from its card [#318] | Ask for the October rent, 80,000,000; on the card tap Edit, change the amount to 85,000,000, the date to «۱۴۰۵/۰۶/۲۵» and the words; save. Then try a date in the future, and Edit on a voucher of three lines | The old card says it was changed and the new draft follows it, with the new amount on both lines, the Jalali date and the words; confirming it posts. The old card can no longer be posted. A future date is refused in the sheet, which stays open; a voucher of several lines keeps its amount field locked ("tell the accountant the new amounts"). Above the approval limit, the new draft says it needs approval |
| N28 | Approve from the phone [#318] | Set an approval limit; the accountant confirms a voucher above it; the CFO opens the app | The CFO's briefing says «۱ سند منتظر تأیید شماست» with the card: who asked, the amount, the lines, Approve and Reject. Approve posts it, stamped, with the two-minute undo; both threads redraw it as posted. Reject asks for an optional reason, which the asker can read. The asker can't approve their own; someone without approval rights gets no cards. Opening the app again the same day doesn't repeat the briefing |
| N29 | One contract for every client [#320] | Change what a block builder sends (rename a field of the figure block); run the server tests and the Android tests | `tests/test_mobile_contract.py` names the recorded conversation and the field that changed, and the schema check fails if a client would break. Once reviewed and rewritten, the Android contract test parses the new recordings; a field the app reads that is gone fails there too. A new `/api/mobile/v1` route fails until it is added to the contract's table with its roles |
| N30 | When the app crashes [#321] | Make the app crash (a debug build's forced crash); open it again; look at the server log; then turn off Send crash reports in the account sheet | On the next launch the crash reaches the server: the exception's class, its causes' classes, the stack frames, the app and Android versions and the phone model, logged as `mobile_crash` (and in Sentry/GlitchTip when set). No message, amount or typed word is in it. Turned off, nothing is kept or sent, and what was kept is deleted |
| N31 | A bank statement, one difference at a time [#322] | Share Mellat's Shahrivar statement into the app; on its card tap Fix step by step; confirm the voucher; tap Next difference until none is left. Try it with the AI switched off too | The card shows the bank as the reader writes it («ملت» in Persian, in the summary sentence too), the period in the company's calendar, the rows, what matched and what differs (not in the books, to confirm, amounts that differ, not in the bank, duplicates) and the balance gap. Each tap brings the next row the books don't have as a voucher with the bank's date and words, then says how many are left; no model is asked, so it works with the AI off. When none is left it says so. Confirming posts the row from the statement |
| N32 | Will we have enough cash? [#323] | Ask «پیش‌بینی نقدینگی», then "will we have enough cash?"; then "what if the Mellat cheque bounces?" | Answered from the books at once, no model: a chart of cash from today through the next 13 weeks, drawn to scale and running with the reading direction, zero dashed, the stretch below zero in red, the lowest week marked; under it the balance after 13 weeks, the lowest and the week cash goes below zero, said as an estimate. A what-if still goes to the accountant, which runs the scenario |
| N33 | The home screen [#324] | Sign in; long-press the app icon; pick Cash, then Receipt, then Spending. Place the Accountant widget; post a voucher; turn the app lock on; sign out | Three shortcuts (Receipt, Spending, Cash) appear only once signed in. Cash asks «موجودی چقدره؟» and the answer comes back; Receipt opens the camera; Spending opens the chat with the keyboard up. The widget shows cash and bank with its date and this month's budget left, refreshes after the posting, shows no figures while the app lock is on ("Open the app to see"), and after sign-out shows "Sign in to see your books" and the shortcuts are gone |
| N34 | Payroll in the chat [#325] | Ask "last month's payroll?", then «فیش حقوقی سارا»; open the payslip, then share it | The recent pay runs as a table: each month in the company's calendar, posted or paid in the reader's language, the net. Sara's pay month by month with her name above it, and her latest payslip as a PDF card: Open shows the same payslip the web prints, Share hands it to Telegram, WhatsApp or e-mail. Only the owner, the CFO and the accountant can fetch a payslip |
| N35 | Told without a push provider [#327] | Turn on Notifications in the account sheet (allow it); the accountant sends a voucher above the limit for approval; wait for the hourly sync (or run it); turn the app lock on and repeat; sign out | Android asks once; the first sync says nothing about what was already there. The CFO is then told «سندی منتظر تأیید شماست» with the voucher, the amount and who asked, once; the books' warnings likewise. On the lock screen, and while the app lock is on, a notification says only that something needs attention, no figures. No Firebase or Pushe is involved. After sign-out the next person starts silent |
| N36 | What am I worth? [#328] | In personal books with 10 g of gold bought for 30,000,000 and gold at 5,000,000 a gram today, ask «دارایی خالص من چقدره؟»; then the same question in a company's books | Answered at once, no model: «دارایی خالص شما امروز: …» with what is owned and owed, and what gold and currency add at today's rates; a figure, the last twelve months as a line ending today with the change («+… در ۱۲ ماه», the sign kept in front), and the holdings with their quantity («۱۰ گرم طلا»), today's value and the gain. In a company's books the question goes to the accountant (its worth is its equity) The twelve-month line counts the gold from its purchase, and each month gone by at a rate of its time: it starts at 0 a year before, not at today's value of gold bought since [#337] |
| N37 | A big file on a poor line [#330] | On a slow line (the emulator behind a 24 KB/s proxy), pick a 760 KB CSV bank statement from Downloads and send it; partway through, turn airplane mode on, send the message anyway, then turn airplane mode off; then attach the same file again. Also: a piece that arrives at the wrong place, a file whose bytes don't match what the phone said, and an upload left unfinished for a day | The CSV can be chosen (Android calls it text/comma-separated-values). The percentage moves every few seconds: pieces start at 64 KB and follow the line, up to 1 MB on a fast one. Offline, the message waits under its bubble with the file kept on the phone. With the network back, the waiting line and «no connection» go, and the upload goes on from the last piece the server kept (its `mobile_uploads` row grows from where it stopped, never from 0); the statement is read and answered once. The same file a second time sends no bytes and is attached at once (until it belongs to a voucher, then it is uploaded as a new file). A piece at the wrong place is told where to resume; a file that doesn't match its hash is refused and sent again from the start; nothing unfinished is kept after a day; nobody can add to or resume someone else's upload |
| N38 | The books' calendar on the phone [#332] | In the Default company (Gregorian) read in Persian, share a statement and open the conversations; switch the company to the Jalali calendar on the web and wait for the phone's next refresh; read in Spanish and Arabic; sign in to personal books; share a file the app can't open | The statement card, the conversations, the chart's labels and the widget's "as of" write dates in the books' calendar, the same words as the reply: «۲۳ سپتامبر – ۱۳ اکتبر» for a Gregorian company, «۱ مهر – ۲۱ مهر» once it is Jalali; Spanish says "10 oct" and Arabic «10 أكتوبر», no longer English months. Personal books show their saffron chip from the first sign-in. A file that can't be opened says so («این فایل باز نشد…») instead of nothing happening |
| N39 | A conversation opened again [#333] | From the phone, send a receipt photo with no words and a CSV statement with no words; close the app and open the conversation again; open the same conversation on the web | Each message comes back as what was sent: the file's name with its paperclip (on the web, its chip), and the words if there were any. Never the prompt written for the model («Attached document OCR (attachment_id=…)»), never an empty bubble. Going on with the conversation, the accountant still knows what the receipt said |
| N40 | A UK company in English [#344] | A UK company (the UK chart, GBP, Gregorian) with a bank, an unpaid sale, an unpaid bill, this month's spending and a budget; its owner signs in on a phone set to English. Open the first run, then ask "how much cash do we have?", "who owes us?", "what do we owe?", "what did we spend this month?", "how's the budget?" and "will we have enough cash?" | Every word in English with Western digits; amounts in pounds written the way the cards write them («£1,440», in the sentences, the chart and the statement card too — never «1,440 GBP» beside «£1,440»; the books keep whole pounds, pence await the owner); "1 sales invoice is", not "1 sales invoices are"; dates as "12 Oct 2026"; the UK chart's own account names; the forecast drawn over Gregorian weeks |
| N41 | A UK bank statement on the phone [#344] | Pick a Barclays CSV (Date, Description, Money out, Money in, Balance; days first) from Downloads — the books' bank is «Barclays Business» — and open Fix step by step. Its rows: rent, office supplies and a train fare already in the books, a monthly fee, and «ARIA DESIGN LTD INV-1001» paying £600 of a £1,440 sale. Post both; undo the payment, then post it again | The statement reads (HSBC's Paid out/Paid in and Monzo's Name/Amount too). The card and its summary in English: «Barclays», 1 Oct – 12 Oct, 3 matched and 2 not in the books, the gap «£592» in grey while rows are still to record. It is checked against Barclays Business (1201), not the chart's 1200. The fee proposes 8000 Bank charges. The customer's money is a payment on INV-1001 (Dr 1201, Cr 1100 Trade debtors, never 4000 Sales): the invoice then has £840 left, and Undo puts the £1,440 back and offers the row again |
| N42 | A draft voucher in UK books [#344] | A draft with VAT (seeded: the test server has no model): a £240 printer from Barclays. Open Edit, type the date the UK way («11/10/2026»), save, then Post | The card's lines carry the UK accounts and VAT in pounds (0020 Dr 200, 1400 Dr 40, 1201 Cr 240). The date is read as 11 Oct 2026, not 10 Nov. The stamp reads «Posted · 11 Oct 2026» and sits under the lines, not over the last amount |
| N43 | The app's own screens in English [#344] | The account sheet, the conversations sheet, airplane mode, the widget and the shortcuts, in light and dark. Choose a file without sending it. Post a voucher and undo it, then close the app and open it again. Ask "what do we owe?" twice | All in English; the widget shows cash as «£23,923» and the budget left in pounds; nothing in Persian except names the books themselves hold. The menu says «Choose a file (PDF, Excel, CSV)». The chosen file sits under the last message, which moves up for it (it was covered), and so does the offline notice. Opened again, a posted card keeps its seal's reference and date, and the undone one says «Undone», not «Posted». A spent undo leaves no empty ring. The question asked twice shows its table both times, with «£360» |
| N44 | A short statement shared with no words | From the bank's app, share «mellat-1405-07.csv» of two rows (date, description, debit, credit, balance) into the app, typing nothing | Imported as Mellat's statement and checked against the books, without the model. A file the bank named, with a running balance on dated rows, is its statement however short; it went to the model and failed with none set up (deep browser test, 2026-10-16). A price list named for a bank still goes to the assistant |

## O. Migrating a real Sepidar company (2026-10-12)

A web test of a real company's Sepidar export (kept outside the repo) imported cleanly at the trial balance, but left the books fragile. These scenarios use a made-up company with the same shape: 2-digit groups, 4-digit ledgers (کل), 6-digit sub-ledgers (معین) and floating detail accounts (تفصیلی).

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| O1 | A bank from the chart gets a code of its own [#339] | Import a chart whose bank sub-ledger 111005 «موجودی بانک‌های ریالی» holds 500,000,000 and whose detail accounts include bank 001 «بانک نمونه 0101234567001» with 500,000,000 | The bank's account is 111005001, a detail account under 111005 (it took the next free 4-digit code, 1113: a ledger code in Sepidar's numbering). The opening voucher debits it 500,000,000 and 111005 is left at 0 |
| O2 | Last year's chart doesn't rename a bank [#339] | A company migrated before this fix, whose bank «بانک نمونه» sits on 1113; import last year's chart, where 1113 is the ledger «سایر حساب‌ها و اسناد دریافتنی» with sub-ledger 111304 | The preview says 1113 is the bank «بانک نمونه» and the file's ledger is left out. After confirming, 1113 keeps the bank's name and balance, and 111304 sits under group 11 (it was renamed «سایر حساب‌ها و اسناد دریافتنی», with the bank's money in it) |
| O3 | Opening balances already in the books [#339] | After O1, import last year's chart, whose balances differ | The preview shows the books' opening balances (date and total) beside the file's and asks: keep them and import only the chart and contacts (the default when they differ), or replace them. Keep: new accounts and contacts arrive, the opening voucher is untouched and the result says so. Replace: the voucher is replaced, as before. The same files again: replace is the default and nothing doubles |
| O4 | The chat does the same [#339] | Drop last year's chart files into the chat with opening balances already in the books; confirm | The books' balances are kept and the chat says so; nothing is replaced without being asked |
| O5 | The balance sheet reads the imported chart [#340] | A Sepidar chart with 1110 «موجودی نقد و بانک», 1111 «سپرده‌های کوتاه مدت», 1112 «حساب‌ها و اسناد دریافتنی تجاری», 1114 «سرمایه‌گذاری‌های کوتاه مدت», 1118 «پیش پرداخت‌ها», 1210/1211/1212 (with software and its amortization), 2111, 2112 «پیش دریافت‌ها», 3110 «سرمایه», 3113 «سود و زیان انباشته», 3114 «سود و زیان جاری» | Each ledger lands on its own line: cash is 1110 only, prepayments are prepayments, software net of its amortization is intangible, advances received are advances, capital is 3110 only and accumulated profit takes 3113, 3114 and the year's result. The totals don't move. The seeded chart's statements are unchanged |
| O6 | One cash figure [#340] | The same books | The dashboard's cash, the balance sheet's cash and the cash flow's closing cash agree, petty cash included |
| O7 | Where new entries post after a migration [#341] | After O1 and O5's chart: raise an invoice, record a supplier bill, run and pay a month's payroll, top up petty cash, add a bank | They post to the imported accounts: receivables 111201, payables 2110/2111's sub-ledger, salaries payable 211101, insurance 211123, payroll tax 211128, petty cash 111003, the new bank under 111005, not to the seeded 1112/2110/2180/2170/2160/1160. The migration result lists where each kind of entry will post, and the owner can change it |
| O8 | Debtors and creditors after a migration [#341] | After O1 with parties holding opening balances (a client owing 4,200,000, an employee owed 3,000,000) | The debtors and creditors report shows each party with their own balance, debit balances as debtors and credit balances as creditors (it was empty) |
| O9 | A year's vouchers, bank by bank [#342] | After a chart import with bank «بانک نمونه» on 111005001 under 111005, import a year's voucher lines (Sepidar's «اقلام اسناد»: voucher, date, کد معین, نام تفصیلی, debit, credit) where the bank's lines are on 111005 with the bank as the detail account | The bank's lines post to 111005001, the bank's own account, and 111005 keeps only lines with no bank on them (the banks' own accounts stayed at 0, everything on 111005). The result says how many lines went to a bank |
| O10 | Debtors and creditors from the imported years [#342] | The same import: a client owing on 111304 «سایر حساب‌های دریافتنی», an employee owed on 211101 «حقوق و دستمزد پرداختنی», each named as the line's detail account | Every party on a line is linked to the voucher with its own share (one link per party, matched by name whatever its type, never a second «supplier» with the same name), and the debtors and creditors report shows each party's balance across every sub-ledger, as Sepidar's detail trial balance does (it read only the receivable and payable accounts, so it was empty) |
| O11 | Where entries post, with a full chart [#342] | Import a full Sepidar chart (twins: تنخواه ریالی/ارزی, موجودی بانک‌های ریالی/ارزی, مالیات/عوارض ارزش افزوده, a «سایر پیش دریافت‌ها» catch-all, five depreciation sub-ledgers), then a year's vouchers | Every kind of entry finds its account: the rial twin, the specific account over «سایر», VAT not its levy, the one the books use most, the plainest title. Suggestions run again after the vouchers, and never over the owner's choice |

## P. Hand-kept books: two sister schools (2026-10-12)

The second real company kept its books in Excel by hand: two schools, each with its own journal sheets, chart and trial balance in multi-sheet workbooks, codes that follow no standard (revenue 2000 or 1200, expenses 3000, 1300 or 4000). Made-up schools with the same shape.

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| P1 | The journal in a workbook [#343] | Read a workbook whose sheets are a bank statement, a chart, a trial balance and the journal | The journal sheet is read (it read the first sheet, a bank statement); the page lists the sheets and another can be picked; a one-sheet file reads as before |
| P2 | A hand-kept journal's columns [#343] | Headers «ردیف · تاریخ · کد حساب کل · عنوان حساب کل · کد حساب معین · عنوان حساب معین · شرح · مبلغ بدهکار (ریال) · مبلغ بستانکار (ریال)»; then a sheet whose header leaves the number and date blank | Every column is found without picking. «ردیف» is the voucher number when it repeats over a voucher's lines (a line counter otherwise); a blank-headed column of dates or of voucher numbers is found by what it holds |
| P3 | Lines with only a ledger [#343] | Some lines carry a sub-ledger code, others only a ledger code | Each line takes its sub-ledger when it has one, else its ledger: every voucher balances and the detail stays (reading only sub-ledger codes left 85 vouchers unbalanced) |
| P4 | Accounts the chart doesn't have [#343] | Import a school's journal into a new company | Each account found nowhere defaults to «create under …», under a parent its name points to: tuition revenue under revenue, petty cash beside cash, a bank as a new bank, a loan and the creditors under current liabilities, rent and supplies under expenses, debtors and deposits under current assets; «سود و زیان انباشته» maps to the chart's retained earnings. Applying creates them once, with the file's names; the next year's file finds them |
| P5 | Opening balances entered mid-year [#343] | A year whose «تراز افتتاحیه» voucher is dated 1404/03/31, after three months of vouchers, with nothing from an earlier year in the books; then the next year's file, whose opening repeats the year's closing balances | The first opening is posted (it was dropped as a repeat, leaving the bank, the petty cash and the creditors wrong for the year); the next year's opening is skipped, as before |
| P6 | Closing a bank account [#343] | A voucher «بابت بستن حساب بانک سپه»; another «بابت بستن حسابهای موقت»; another «اختتامیه» | The first posts (it was taken for a year-end closing and dropped); the other two are still left out |
| P7 | A date with the wrong year [#343] | A 1404 journal with vouchers typed 1401/01/20, 1403/03/08 and 1400/11/29 | They are held back as «outside the file's year»; «use the file's year» posts them on 1404/01/20, 1404/03/08 and 1404/11/29, «keep the dates as written» posts them as typed |
| P8 | A rial out [#343] | A voucher off by 1 rial; another off by 50 | The first posts with the rial on its largest line, counted as rounding; the second is refused as unbalanced |
| P9 | The next year's file, on the same page | Import 1404, whose apply creates 16 accounts; without reloading, read 1405 | Its accounts are offered in the boxes: the page reloads the chart after an import (it kept the list from before 1404, so 13 of 1405's boxes came up empty and the accounts had to be picked again) |
| P10 | Which voucher has the wrong year | فرهنگ نو's 1405 journal (2026-10-21): «تاریخ ۱ سند بیرون از سال فایل (۱۴۰۵) است؛ سال اشتباه تایپ شده؟», and the owner had to ask which one | Under the question, each voucher it means: its number, its date as written («۱۴۰۴/۰۱/۰۵»), its amount, its words and accounts («بانک دی → درآمد شهریه»). After «برای آن‌ها ۱۴۰۵ گذاشته شود» the list stays, with the date it now has beside the one written |

## Q. Keeping pence (2026-10-12, `docs/PLAN_PENCE.md`)

The books kept whole units, so a £7.50 bank fee went in as £7. Pounds, dollars and euros keep their pence; rials don't change. Each scenario names the phase that delivers it.

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| Q1 | Rial books don't move [P1] | Before and after the migration, open a rial company's dashboard, trial balance, balance sheet, P&L, ledger and debtors; export the journal | Every figure and every file is the same, byte for byte; no «.00» on a rial anywhere |
| Q2 | A pound with pence in the ledger [P2] | In a UK company, record a journal: Dr 8000 Bank charges £7.50, Cr 1201 Barclays £7.50 | The entry, the ledger, the trial balance and the P&L show £7.50; the bank's balance falls by £7.50 |
| Q3 | Too many places [P2] | Record £7.505 in a UK company, and 2,500,000.5 rials in an Iranian one | Both are refused with a sentence: pounds have 2 decimal places, rials none |
| Q4 | A statement to the penny [P2] | Send the Barclays CSV with its £7.50 fee and £24,515.50 closing balance (web and phone); post both differences | The fee is £7.50; the gap before posting is £592.50 and «explained»; after posting it is £0.00 |
| Q5 | Pence across currencies [P2] | In a rial company, record $12.34 at 600,000 rials per dollar; in a UK company, €10.01 at 0.85 | The rial company's base amount is 7,404,000 rials; the UK one's is £8.51 (8.5085 half-up); both entries balance in base |
| Q6 | The chat's voucher with pence [P2] | Tell the accountant (and the phone) «paid £3.99 for a coffee with a client from Barclays» | The draft shows £3.99 on both lines, the posted entry is 3.99, Edit can change it to £4.25; a photographed receipt or statement keeps its pence too (it was rounded to whole pounds) |
| Q6a | Pence everywhere the ledger is read [P2, P3] | Enter opening balances of £1,250.75; export the journal to CSV; open any page that reads a report as a plain list | The opening balance, the CSV and every figure are 1250.75, never «1,250» and never the text "1250.75" (a route returning a plain dict sent amounts as strings) |
| Q7 | An invoice to the penny [P3] | Invoice 3 × £19.99 + 20% VAT; record £30.00, then £41.96 | The total is £71.96 (59.97 + 11.99); the first payment leaves £41.96; the second closes it at £0.00; the PDF says «£71.96» and «seventy-one pounds and ninety-six pence»; the same £30.50 confirmed on the accountant's card posts £30.50; a quote's £99.99 line reads 99.99 |
| Q8 | VAT boxes to the penny [P3] | The quarter with Q7 and a £12.50 + VAT bill | Box 1 £11.99, box 4 £2.50, box 5 £9.49 (boxes 1–5 to the penny); box 6 £59 and box 7 £12 (boxes 6–9 in whole pounds, pence dropped, as HMRC asks) |
| Q9 | A payslip to the penny [P4] | An hourly employee at £12.21 for 37.5 hours a week, a month's run | The gross is £1,984.13; tax and NI to the penny; the payslip and the posting agree |
| Q10 | Mileage at 45p [P4] | Claim 123 miles at 45p | £55.35, not £55 |
| Q10a | Splits and settings to the penny [P4] | A £100 loan in 3 installments; a £1,000.01 dividend across three holders of a third each; a £1,000 laptop over 36 months; a £99.99 budget rolled forward +10%; a £499.99 approval threshold and a £120.50 cheque; opening balances of £1,250.75 typed on the chart page | £33.34 + £33.33 + £33.33; £333.33 + £333.34 + £333.34; months of £27.78 and £27.77 adding to £1,000.00; £109.99; the threshold and cheque are kept to the penny, £120.505 is refused; the opening balance posts £1,250.75 |
| Q11 | Words and files [P5] | Ask «how much cash do we have?», export the journal to Excel, print a cheque for £1,234.56 | «£23,915.50» in the answer; `#,##0.00` cells holding 7.5; the cheque reads «£1,234.56» and «one thousand two hundred and thirty-four pounds and fifty-six pence» |
| Q11a | The accountant's sentences [P5] | With pence in the books, ask: "how much cash do we have?", "who owes us?", "how much did we spend this month?", «بودجه چقدر مونده؟», the cash forecast, the report card (personal), a CFO and a CEO report | Every figure with pence is written with them: «£23,915.50», "7.50", never «£23,915» or "7.5"; whole amounts read as before («£950»); rial sentences are unchanged, byte for byte |
| Q11b | Averages to the penny [P5] | A UK company spending £1,000.10, £999.95 and £1,000.00 over three months; a subscription of £9.99 a month; a savings goal saving £33.33 a month | The burn rate is £1,000.02, the subscription's usual amount £9.99 (it was £9), the goal's pace £33.33; a rial company's averages are as before (whole rials) |
| Q11c | Notifications and digests [P5] | A budget 80% used at £799.99 of £999.99; a payment of £30.50; the daily digest with cash of £1,250.75 | The bell, the e-mail and the push say «£799.99», «£30.50», «£1,250.75» |
| Q11d | Files [P5] | Export the journal, the trial balance and a statement to CSV and Excel with pence in them | CSV cells read 7.50; Excel cells hold the number 7.5 formatted `#,##0.00` (a rial file `#,##0`); totals add up to the penny |
| Q11e | A cheque to the penny [P5] | Print a cheque for £1,234.56 (UK layout) and one for 1,234,560 rials (Iranian layout) | «£1,234.56» and «One thousand two hundred and thirty-four pounds and fifty-six pence»; the rial cheque as before («#۱٬۲۳۴٬۵۶۰#») |
| Q12 | The phone [P2, P5] | Q4 and Q6 on the phone; the widget | «£7.50» on the cards, «£23,915.50» on the widget; nothing is cut to whole pounds |
| Q13 | The web's figures [P5] | In a UK company open the dashboard, the ledger, the trial balance and statements, an invoice, payroll, the CFO page and the forecast; then an Iranian company's same pages; then Persian | Every amount in pounds shows its pence, whole ones too («£950.00», «1,138.17 GBP»), a unit price keeps its own places («0.125»); counts, percentages, quantities and chart axes don't («12 rows», «40%»); a rial page reads as before («2,500,000»); in Persian «۹۵۰٫۰۰» |

## R. The accountant on migrated books, AI on (2026-10-12)

The chat with a real model (Metis, `gpt-4.1-mini`) on the two migrated companies of O and P: a Sepidar company with three years of history (1403–1405, the last year's vouchers provisional) and the two schools' hand-kept books, whose codes follow no standard. Every figure the accountant gives is checked against the page that shows it; every card is checked for the accounts it would post to, and the ones confirmed are undone. The real books stay outside the repo; made-up books with the same shape reproduce any finding.

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| R1 | A bank's balance | Ask «موجودی بانک ملت چقدره؟» (a bank the books have), then the balance of the bank account by its code | The figure equals the ledger's balance of that bank's own account (not its parent sub-ledger), in rials with Persian digits, as of today |
| R2 | All the cash | «کل موجودی نقد و بانک چقدره؟» | The total equals the dashboard's cash and the balance sheet's cash, petty cash included, and names each account |
| R3 | A year's result | «سود و زیان امسال چقدر بوده؟», then «درآمد و هزینه سال ۱۴۰۴» | Revenue, expenses and the result equal the income statement for 1405 to date and for 1404; the year is the company's Jalali year |
| R4 | Where the money went | «بیشترین هزینه‌های شهریور چی بوده؟» | The categories and amounts equal the ledger for Shahrivar 1405 (the company's month), largest first |
| R5 | One month against another | «هزینه‌های مرداد رو با تیر مقایسه کن» | Both months' totals match the ledger; the change is computed from them, not guessed |
| R6 | Who owes us, whom we owe | «کی به ما بدهکاره؟», «به کی بدهکاریم؟» | The parties and balances equal the debtors and creditors report (filled from the imported years' detail accounts, O10) |
| R7 | An account's movement | «گردش حساب ۱۱۱۰۰۵ در شهریور» (an imported sub-ledger code), and the same for a school's ledger code | Debits, credits and the closing balance equal the general ledger for that account and month |
| R8 | One party | «حساب [a supplier or employee the books name] چقدر مونده؟» | The party's balance equals its row in the debtors and creditors report |
| R9 | Find an entry | «آخرین پرداخت اجاره کی بوده و چقدر؟» | The voucher it names exists, with that date and amount |
| R10 | An expense in tomans | «۲ میلیون تومان لوازم التحریر از بانک [a bank the books have] خریدیم» | One card: 20,000,000 rials, debit an expense account from the imported chart (a school's own supplies or general expense code, never a seed code the company doesn't use), credit that bank's own account; the date today in the company's calendar. Confirmed, it posts those lines; undone, the books are as before |
| R11 | Tuition received (school) | «شهریه ۱۵ میلیون تومانی از اولیای [a student] به حساب [bank] واریز شد» | Credit the school's tuition revenue account (its own code), debit that bank; 150,000,000 rials |
| R12 | Salary paid | «حقوق مهر [an employee the books name] رو ۸۰ میلیون ریال از بانک پرداخت کردیم» | Debit the imported salaries payable or salary expense the books use for that person, credit the bank; the employee linked |
| R13 | Bank to bank | «۵۰ میلیون از بانک [A] به [B] منتقل شد» | Debit B's own account, credit A's; nothing else |
| R14 | Not enough to go on | «یه پرداخت ثبت کن» | One question back (how much, to whom, from which bank); no card |
| R15 | A date it can't take | Ask for an entry dated next month; then one in a closed year, if any | Refused in the user's language with the reason; no card that can't post |
| R16 | Before the books began | «درآمد سال ۱۳۹۹ چقدر بود؟» | Says the books start in 1403 (or have nothing then); no figure invented |
| R17 | A follow-up | After R4, «ماه قبلش چی؟» | Answers for Mordad 1405 from the books, keeping the topic |
| R18 | Change a draft | After R10's card, «مبلغش رو ۲.۵ میلیون تومن کن» | A new card at 25,000,000 rials; the old one can no longer be posted |
| R19 | The balance sheet in a sentence | «وضعیت ترازنامه رو خلاصه بگو» | Total assets, liabilities and equity equal the balance sheet; assets equal liabilities plus equity |
| R20 | Cash runway | «با این روند چند ماه پول داریم؟» | The cash and the monthly burn it uses are the dashboard's; it says what it assumed |
| R21 | English on a Persian company | "What was our biggest expense last month?" | Answered in English with the same figures, the month named in the company's calendar |
| R22 | Persian number words | «دو میلیارد و صد میلیون ریال وام از بانک گرفتیم» | 2,100,000,000 rials on the card; a loan liability from the imported chart |
| R23 | Speed and cost | All of the above | Each turn answers within 30 s; the run's AI cost is recorded |

## S. Small fixes from the pence pass (2026-10-12)

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| S1 | A status in words on a PDF | Download the PDF of a partly paid invoice, a sent quote and a partly received purchase order, in an English and a Persian company | The status reads «Partially paid», «Sent», «Partially received» (Persian «بخشی پرداخت‌شده», «ارسال‌شده», «دریافت جزئی»), never «partially_paid» |
| S2 | The voucher form after saving | Enter a two-line voucher of £7.50, save it | The form clears and its totals bar reads 0 again, not the last voucher's £7.50 |
| S3 | A KPI card in a narrow column | The dashboard at desktop width and at 1024 px, a UK company with pence | «21,875.49 GBP» stays on one line in each card (the code moves under the figure only when the card is too narrow for both) |

## T. Stock in the books (2026-10-13; returns, counts, the assistant and the close 2026-10-14; `docs/PLAN_INVENTORY_GL.md`)

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| T1 | Switching it on | A UK company with 20 units of stock worth £1,500 by the movements; switch on stock posting from the first of this month | One opening entry: stock (1000) £1,500 debit, stock adjustment (5900) credit; the balance sheet shows Stocks £1,500; switching on again posts nothing more |
| T2 | Off by default | A company that never switched it on records movements | Nothing reaches the ledger, exactly as before |
| T3 | A write-off | Issue 2 damaged units by hand (OUT) | The sync posts the cost of 2 units: stock adjustment debit, stock credit; the books' stock equals the valuation |
| T4 | Stock found | Receive 5 units by hand at £12.50 | Stock £62.50 debit, stock adjustment credit |
| T5 | Production | Build 3 finished items from their components | Nothing posts: components out and the finished item in at their total; stock's value in the books unchanged and equal to the valuation |
| T6 | A back-dated purchase | After T3, record an IN dated earlier this month at a higher cost | The sync posts only the difference in the month's cost; the books equal the valuation again |
| T7 | Changing the method | Switch weighted average → FIFO with stock posting on | The difference is posted in the first open month, described as a method change; the books equal the FIFO valuation |
| T8 | A closed month | Close last month, then try a movement dated in it | Refused: the period is closed; the sync never touches a closed month |
| T9 | Running the sync twice | Run it from the page twice | The second run posts nothing |
| T10 | An Iranian company | Switch on in an Iranian company | Accounts 1145 and 5110/5190 are created under their groups (51 created if missing); the balance sheet's «موجودی مواد و کالا» and the income statement's «بهای تمام شده» carry the figures |
| T11 | Buying stock on an invoice [G2] | A purchase invoice with two stock lines (10 at £100) and a service line | The stock lines debit Stocks, the service line Purchases; one IN movement per stock line at its net cost |
| T12 | Selling stock [G2] | A sales invoice for 15 of the item after buying 10 at £100 and 10 at £200 | Revenue as before; an OUT of 15; cost of sales £2,250 (weighted average) or £2,000 (FIFO); stock £750 or £1,000 in the books and the valuation |
| T13 | Voiding [G2] | Void the sales invoice of T12 | Its OUT movement goes; the sync takes the cost of sales back; stock returns to £3,000 |
| T14 | A month whose cost of sales is a credit [G2] | Switch posting on from the 1st with £1,722 of stock on hand, in a month with £300 of purchases; sell 6 chairs (cost £454.98) | Cost of sales nets to a credit of £967.02 (the stock brought on is larger than the month's costs). The P&L, its PDF and its Excel show it as 967.02, a credit, and gross profit £1,687.02 adds up; it read «(967.02)», a cost, beside the same gross profit. An ordinary month still shows its cost of sales in brackets |
| T15 | A customer returns goods [G3] | After T12 (15 Widgets sold at £300 out of 10 at £100 and 10 at £200, weighted average), a credit note on the sale. The form lists the stock line: 15 sold, none back yet. Enter 5 back | The amount comes up as 5 × £300 = £1,500 (with the line's VAT when there is any), to change if the credit differs. Posting it: sales returns and VAT as before, and an IN of 5 Widgets at what they cost on that sale (£150 each). The month's cost of sales falls from £2,250 to £1,500; stock is £1,500 in the books and the valuation. The form then shows 5 back, 10 left |
| T16 | Goods go back to the supplier [G3] | A bill for 10 Widgets at £100 + 20% VAT; a credit note for 4 of them back, £480 | Trade creditors £480 debit; Stocks £400 credit and input VAT £80 credit (no purchases line: those Widgets were never an expense). An OUT of 4 at £100. Stock £600 in the books and the valuation |
| T17 | The supplier keeps a restocking fee [G3] | The same bill; 4 back, but the supplier credits £360 + VAT (£432) | Stocks £400 credit (the goods leave at what they cost), purchases £40 debit (the fee), input VAT £72 credit, trade creditors £432 debit. Stock £600 |
| T18 | What can't come back [G3] | After T15 (10 left on the sale): 11 back; an item that isn't on the invoice; a return while stock posting is off; a return on an invoice dated before the start | Each is refused in words («Only 10 Widget can come back on S-1.»), and nothing posts: no credit note, no movement, no journal |
| T19 | Voiding after a return [G3] | Void T15's sale | Every movement is reversed by its opposite: the 15 come back in, the 5 returned go out again. The sale's cost of sales nets to nothing; stock is back to £3,000, books equal to the valuation |
| T20 | A stock count [G3] | Inventory → Stock count, as of today: Widget on hand 10, Gadget 11, Sprocket 4. Count 8 Widgets and 12 Gadgets; leave Sprocket blank | The sheet shows each difference as typed (−2, +1). Posting it records two movements, «COUNT-<date>»: Widget OUT 2 at its cost, Gadget IN 1 at its unit cost; Sprocket is untouched. One stock adjustment journal for the month carries the net; books equal to the valuation. Posting the same count again changes nothing («Nothing to adjust: the count matches what's on hand») |
| T21 | A count in a closed month [G3] | Close last month, then post a count dated in it | Refused: the period is closed. Nothing is recorded |
| T22 | The assistant reads the books [G3] | With stock posting on, ask the chat «How much stock do we have?» | The answer gives the stock's value under the company's method and says it's what the books carry (the stock account's balance); if the two ever differ, it says by how much and to post the stock from the Inventory page |
| T23 | The month-end close [G3] | Manager reports → the close checklist for this month, with stock posting on; then lock the month | The checklist has a «Stock» line: the stock in the books equals the valuation at the month's end (or, if not, both figures and «Post now» on the Inventory page). Locking the month posts the stock first, so the month closes with its cost of sales in it. Without stock posting there is no «Stock» line |

**Run order:** A → B → C → D → E → F → G → H → J → L → M → N → O → P → Q → R → S → T; K on its own scratch database; I runs across all of it. Results go in `runs/<date>/RESULTS.md`.

## U. A party's transactions window (2026-10-13)

From the owner after the production imports: a party with years of history opened on its oldest entries, and nothing could be found in it.

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| U1 | Newest first | A party with transactions on 1403/04/19, 1403/05/08 and 1403/06/07; open «تراکنش‌ها» from the parties list | 1403/06/07 is the top row. Each row's «مانده» is still the balance after that row, so the top one is the balance today. The PDF statement still reads oldest first, as a statement does |
| U2 | Search | In the same window, type «طراحی» (the imported text reads «طراحي», with Sepidar's Arabic «ي»), then «۴۸٬۴۷۸٬۵۰۰», then «48478500», then «sepidar-6», then «۱۴۰۳/۰۵», then «zzz» | Only the matching rows show, with «۳ از ۴۱» beside the box; letters match in either form, digits in Persian or Latin, with or without separators. «zzz» shows «No transaction matches»; clearing the box shows every row again |
| U3 | Searching, then editing | Search «تنخواه», edit one of the rows and save; then close the window and open another party's | Back in the list the search is still «تنخواه» and only its rows show. Another party, or opening from the list again, starts with an empty box |
| U4 | The whole row in view | The same party at 1280 px, entries imported from Sepidar (references like «IMPORT-sepidar-6-2024-08-06», no attachments) | The window widens for a statement (up to 1240 px), each reference stays on one line, and the attachments column shows only when an entry has one: the «مانده» and the actions are in view without scrolling sideways (they sat past the edge, and each row was four lines tall). An account's detail and a shareholder's ledger keep the usual width |

## V. Persian figures and tidy buttons (2026-10-13)

From the owner's screenshots after the production imports.

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| V1 | The CFO's sentences in Persian | A rial company with a loss over the last 12 months; open the CFO page in Persian, and ask the chat «وضعیت سلامت مالی چطوره؟» | The summary, the insights and the answer write every figure in Persian digits with «٬» between thousands and «٫» for decimals, percentages as «۳۸٪», and the money as «ریال» («درآمد کل ۳۴٬۴۸۰٬۸۳۰٬۰۰۰ ریال»); it read «IRR 34,480,830,000». English, Spanish and Arabic keep Latin digits and the code |
| V2 | No spending lately | A company whose last three months have no expenses (the schools' books end in Khordad), with cash in the bank | The summary says nothing was spent in the last three months, so there is no burn to measure a runway against; the runway card shows «ندارد» / "N/A" and the chat says the same. It read «با نرخ سوخت فعلی 0 در ماه، حدود 999.0 ماه دوام می‌آورد». The runway is not marked as a risk |
| V3 | Chart labels | The dashboard's «بیشترین هزینه‌ها» and the other charts, in Persian and in English | Legends, axes and tooltips use the page's own font (they fell back to Helvetica/Arial, unlike every other label), and their numbers are in the page's digits |
| V4 | Buttons in a table cell | The companies console (AI budget · Suspend · Reset password) at 1280 px; a party's transactions (ویرایش · حذف) | A cell's buttons sit side by side with a gap and, when they must wrap, with a gap between the lines too; they touched, and the party's two were stacked |
| V5 | Buttons under a field | Fixed assets → Month-end depreciation | Preview and Post depreciation sit a little below the «Up to» date, not touching it |
## W. Recording a voice note (2026-10-14)

From the owner: no way to cancel, a recording that barely shows it is recording, and words that don't reliably come back. The same on the web chat and the Android app.

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| W1 | Tap, speak, done | Tap the microphone, speak for a few seconds, press ✓ | A recording bar takes the text box's place: a pulsing red dot, the time counting up, bars that rise and fall with the voice, «‹ Slide to cancel» (on a computer: «Esc to cancel»), ✕ and ✓. After ✓ the bar reads «Writing what you said…» and the words land in the text box, to check and send; nothing is sent by itself |
| W2 | Cancel | Tap the microphone, speak, then press ✕; again, and press Esc | The recording is thrown away: nothing is transcribed, the text box comes back as it was, the microphone is released |
| W3 | Hold to talk | Press and hold the microphone, speak, let go | The same bar while held; letting go writes the words into the box |
| W4 | Slide to cancel | Hold the microphone, speak, slide the finger toward the start side (left in English, right in Persian) | The hint follows the finger and turns red near the end; past it the recording is thrown away with a short vibration, nothing transcribed |
| W5 | Too short | Touch the microphone for a split second | Nothing is sent to be transcribed; a hint says to hold to record or tap for hands-free |
| W6 | Two minutes | Record for two minutes | It stops by itself and writes the words |
| W7 | Without the microphone | Refuse the browser's microphone permission | «The browser did not allow the microphone», the text box as it was |

## X. Parties from accounts (2026-10-14)

The owner asked why «اجاره مدرسه» and the rest weren't in اشخاص. Rent is an expense account and stays in the chart. But the schools' hand-kept journals keep their people and organisations as accounts («دبیرستان منادی حق», «آقای صنعتی», «خانم آیت الهی», «تامین اجتماعی»), so the import made them accounts and only the bank became a party.

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| X1 | What's offered | A school's imported books; open اشخاص → «Accounts that could be parties» | The debtor and creditor sub-accounts with entries are listed, with their balances: 115201 «دبیرستان منادی حق», 219603 «خانم آیت الهی», the loan 219701 «وام از بانک دی». Not offered: expenses («هزینه اجاره»), income, the bank, petty cash, fixed assets, accounts the posting roles use (trade debtors, wages payable, VAT), or an account that is already a party's |
| X2 | Making parties | Tick «دبیرستان منادی حق» as a customer and «خانم آیت الهی» as a supplier, then make them | Both appear in اشخاص, each tied to its own account, and they are offered no more. Their past entries are linked to them |
| X3 | Their transactions | Open «دبیرستان منادی حق»'s transactions; then «خانم آیت الهی»'s | Every entry on the account, newest first, with debtor, creditor and the running balance read off the account: the top row is what the school owes us (۹٬۲۳۸٬۴۸۷٬۴۵۲), and for «خانم آیت الهی» what we owe her (۸٬۵۴۱٬۴۵۹٬۰۱۳) |
| X4 | New entries | Post a voucher to 115201 by hand, by chat or by import | It shows in «دبیرستان منادی حق»'s transactions and is linked to the party, with nobody picking her |
| X5 | Their statement | The party's PDF statement for a period | The account's entries in the period, after a brought-forward balance; it was empty, because the statement read only invoices |
| X6 | Who can | A viewer or an employee asks to make parties | Refused (BOOKS_WRITE); اشخاص is the owner's, CFO's and accountant's page |
| X7 | Cash funds | منادی حق's books: «تنخواه گردان حساب بانک شهر», «تنخواه گردان حساب 4676400902 بانک ملت», «تنخواه گردان حساب بانک پارسیان», «تنخواه گردان خانم آیت الهی», a plain «صندوق» and the main bank account 1111 | The three bank funds are offered as banks, Mrs Ayatollahi's as an employee holding cash, each with its balance («موجودی»); the Shahr fund, at −5,504,920,857, says «منفی» in red, for the accountant to look at. Not offered: the «صندوق», the main bank account (a bank party's already), the chart's cash and petty-cash role accounts |
| X8 | A bank made from a fund | Make the Shahr fund a bank | It is listed as a bank with its account as its code (۱۱۵۱۰۱), the bank statement import offers it, and its transactions are the fund's entries with its balance. A fund can't be made a customer or a supplier, nor a debtor account a bank |

## Y. Bank SMS read on the phone (2026-10-14, `docs/ROADMAP_ANDROID_CHAT.md` P2.2, §7.1)

The owner decided (§11.5): apply for Play's exception, and read bank SMS automatically in the builds outside Play (`direct`, Bazaar if it allows it). Sharing an SMS into the app stays in every build. Only messages from senders the user has said are their bank are read; only the sender, the time and the text leave the phone, and each becomes a row to record, as a pasted SMS does.

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| Y1 | The phone sends bank SMS | `POST /api/mobile/v1/bank-sms` with two Mellat SMS (a withdrawal and a deposit, with balances), each with its sender and time | Both are rows of Mellat's SMS statement for the month, to record; the answer counts 2 added, 0 already on file, 0 unread, and names the statement. The audit log has «Bank SMS forwarded» with the phone's count. Sending them again adds nothing: 2 already on file |
| Y2 | Who may | An employee, a manager or a viewer sends one | Refused (403), as for the chat; the owner, the CFO, the accountant and a personal user may |
| Y3 | What isn't a movement | An SMS from the bank that is an OTP or an advert; 51 messages at once | The OTP is counted as unread and nothing is filed; 51 is refused in words and nothing is filed |
| Y4 | Told on the phone | After Y1, the phone's hourly feed and the next briefing | The feed has «Bank SMS to record — Mellat: 2» (once; again only when the count changes). The briefing says the same with Next, which offers the first row as a voucher; when both are in the books, the line goes |
| Y5 | The switch [app] | The `direct` build: Account → «Read bank SMS». The `play` build | `direct`: the switch asks for the SMS permission; refused, it stays off and says so. `play`: no switch, and no SMS permission in the build's manifest |
| Y6 | A sender not yet known [app] | With the switch on, an SMS from 98700717: «بانک ملت … برداشت ۱٬۲۵۰٬۰۰۰ مانده ۸٬۴۰۰٬۰۰۰»; then one from a friend | The bank's is kept on the phone (sealed) and a notification asks «Is 98700717 your bank? (Mellat)» with Read and Ignore; no amount is shown. The friend's message is not kept, sent or told at all |
| Y7 | Read [app] | Tap Read | The kept message goes to the books with its sender and time, and later ones from 98700717 go as they arrive. The sender is listed under the switch and can be removed |
| Y8 | Ignore [app] | Tap Ignore on another sender | The kept message is deleted; later ones from that sender are ignored. It is listed as ignored and can be changed |
| Y9 | Offline [app] | An SMS from a read sender with no network | It waits on the phone, sealed, and goes when the network is back (WorkManager); signing out forgets what waits and the senders |
| Y10 | The app lock [app] | The lock on, an SMS from a new bank sender | The notification says only that a message from a bank needs a choice, with no sender or bank, until the app is open |

## Z. A person's money in the chat (2026-10-15)

From the owner, on the phone with personal books: «من هشتاد و شش ریال و صد بیسه توی کیف پول سبزم دارم» was read as «۸۶٫۱ ریال» of the books' Iranian rials. The assistant then asked which account to set it against, twice; each suggested answer («افتتاحیه», «سرمایه») was "not found in the chart", and the last reply ended in a stray word. The card above, for «چقدر پول داریم؟», read "Cash and bank · 0 IRR" in English beside a Persian answer.

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| Z1 | Money I already have | Personal books in Omani rials: «۸۶ ریال و ۱۰۰ بیسه توی کیف پول سبزم دارم»; «۲۰۰ ریال تو حساب بانکم دارم» | One card each, no questions: the green wallet's starting balance, 86.100 OMR, to cash on hand against opening net worth (described «کیف پول سبز — موجودی اولیه»); the bank's 200.000 to the bank account. Said in plain words: no debit, credit or "counter-account" |
| Z2 | Money in another currency | The same wallet message in books that count in Iranian rials | Replaced by AB1–AB2 (2026-10-16): never refused. While the books can still change, one question with two answers to tap (count the books in Omani rials, or keep this in its own currency); once they have entries, kept in its own currency, as a place of its own |
| Z3 | Choosing the currency | Personal books: the phone's account sheet → «Your books' currency» → Omani rial; on the web, My finances → Your books' currency | The books now count in Omani rials: the dashboard, the chat and the cards say OMR, with three places (86.100). Once the books hold entries, the currency is shown but no longer offered («Your books already have entries, so their currency stays»), and the server refuses a change: those entries would count as nothing without a rate (three months of rials read 0.000 OMR in the first screenshots). Only a person's own books offer it; a company's currency stays with the platform admin |
| Z4 | The words for it | In books whose chart is in English, and in Persian: «افتتاحیه», «موجودی اولیه», «خالص دارایی», "opening balance" | Each finds Opening net worth (3110) first; none is "not found in the chart". «سرمایه» alone is left to the chart: in a person's books it is as often investment («سرمایه‌گذاری») |
| Z5 | One language on a card | The app in English, the question in Persian: «چقدر پول داریم الان؟» | The card and the sentence are both Persian: «موجودی نقد و بانک»; asked in English, both are English |

## AA. What changed on the balance sheet (2026-10-15)

From the owner, on Manager reports → the balance sheet: assets went from 27,954,918,967 to about 47 billion rials between the two dates, and nothing said why. Was it cash, or something else? The chart showed three totals; the table showed each line's percentage, not what each line added.

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| AA1 | What changed, by line | Click «دارایی‌ها» on the balance sheet's chart | A window: assets from the earlier figure to the later, the change and its percentage, and each asset line that moved, biggest change first: earlier, later, the change in rials and its share of the whole change («موجودی نقد +۱۲٬۳۰۰٬۰۰۰٬۰۰۰ — ۶۴٪ از افزایش»). Lines that didn't move are left out. Liabilities and equity the same |
| AA2 | Behind a line | Click «موجودی نقد» in that window, or its row in the table | The accounts that make up the line (each bank, the cash box), each with its earlier and later balance and change, biggest first; they add up to the line. Back returns to the lines |
| AA3 | The entries | Click one account | Its entries between the two dates (the day after the earlier date to the later one), searchable, as the other drill-downs |
| AA4 | The result not yet closed | Equity → retained earnings | The accounts under it, and a line for the profit or loss not yet closed into it, so the line still adds up |
| AA5 | At a glance | The balance sheet's charts | A chart «What changed» ranks every line by its change (increases one colour, decreases another); clicking a bar opens AA2 |
| AA6 | UK books | The same on a UK balance sheet (Cash at bank and in hand, Debtors…) | The same windows, in English, in pounds with pence |
| AA7 | A UK sheet on the dates chosen | Manager reports → Balance sheet in a UK company, From one month back, To yesterday; Run | The sheet is as of yesterday against a month back, and so are its windows. It was sent `to_date`, which the UK sheet ignored, so it always compared today with a year before (deep browser test, 2026-10-16) |
| AA8 | A UK sheet's liabilities | A UK company owing VAT (creditors within one year) and nothing long-term; the balance sheet's chart | The Liabilities bar is the creditors within one year and after it, the figure its window adds up; it was the long-term ones only and read 0 |

## AB. A person's money in several currencies (2026-10-16)

From the owner, on the phone: «۷۷.۵ ریال عمان توی کیف پول سبز داریم» in books that count in Iranian rials was refused ("I won't record it or convert it; change your books' currency first"). It should have asked whether to change the books' currency or add the new one: a person keeps several currencies, in different wallets, vaults and bank accounts, and wants them all kept as they are, not exchanged.

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| AB1 | Asked once, while it can still change | Personal books in Iranian rials with no entries yet: «۷۷.۵ ریال عمان توی کیف پول سبز داریم» | One short question with two answers to tap: count the books in Omani rials, or keep Iranian rials and keep this in Omani rials. Nothing posted yet |
| AB2 | Keep it in its own currency | Tap «keep it in Omani rials» (or the books already have entries, so nothing is asked) | A card: «کیف پول سبز — موجودی اولیه», 77.500 OMR, a new place under cash on hand. Confirmed, the wallet is an account of its own and holds 77.500 OMR, not converted |
| AB3 | Change the books' currency instead | AB1, tap «count the books in Omani rials» | A card «Count your books in Omani rials»; confirmed, the books count in OMR and the wallet's 77.500 is in the books' currency |
| AB4 | Many places, many currencies | Then «۵۰۰ دلار توی گاوصندوق دارم», «۳٬۰۰۰٬۰۰۰ ریال تو حساب ملت دارم» | Each its own place (a vault under cash, a bank account under the bank), each in its own currency |
| AB5 | How much money I have | «چقدر پول دارم؟» | Each currency on its own, nothing exchanged: «۷۷٫۵۰۰ ریال عمان · ۵۰۰ دلار · ۳٬۰۰۰٬۰۰۰ ریال», and a card listing each place with its currency |
| AB6 | The same place again | «۵ ریال عمان از کیف پول سبز ناهار خریدم»; then «۱۰ ریال عمان دیگه هم توی کیف پول سبز هست» | The lunch is paid from the green wallet, found by its name, in Omani rials. A second starting balance for a place that already has one is not a second wallet: the assistant says what the wallet holds and asks where the extra came from |
| AB7 | The reply on the phone | Any reply with bold words | Bold, not «**» around them; page names in the reply's language («امور مالی من», not "My finances") |

## AC. Reply, copy and share in the chat (2026-10-16)

From the owner, on the phone: "I want to be able to reply, share or copy a message in the app's chat and on the web as well." On the card in the same screenshot, a person's books showed "Dr" and "Cr" beside the amounts, the date read «مهر ۲۴۱۴۰۵», and the reply said to press «تأیید» on a card whose button says Post.

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| AC1 | Copy a message | Phone: long-press an answer (or your own message) → Copy. Web: point at a message → Copy | The words are on the clipboard as they read (bold words without «**»); the phone says Copied (Android 13 and later say it themselves), the web shows Copied |
| AC2 | Copy a card | Phone: long-press a voucher above its fold → Copy. Web: Copy under a card | What it is and its date, the amount with its currency, and each line: code, name, side and amount, one per line |
| AC3 | Share | Phone: long-press → Share. Web: Share | The phone's share sheet (Telegram, WhatsApp, Gmail…) with the same text. On the web, the browser's own share sheet where it has one (phones, Safari, Edge); otherwise Telegram, WhatsApp and e-mail |
| AC4 | Reply to an answer | Long-press (web: point at) an answer → Reply; «و به دلار؟»; send | Above the composer: replying to the accountant, the answer's first words, ✕ to drop it. The message goes with the quote above its words, and the accountant answers about the quoted message. The thread opened again, on the phone or the web, still shows the quote |
| AC5 | Reply to a card | Reply to a pending voucher with «مبلغش رو ۸۰ کن», while a newer card sits below it | One corrected card replaces the card replied to, not the newest one. A reply to a posted card is taken as a correction of what is posted, never a second copy of it |
| AC6 | Offline | Phone offline: reply to a message and send | The message waits in the outbox with its quote and goes with it when the network is back |
| AC7 | Only your own cards | A reply that names a card of someone else's | The quoted words go; the card is ignored, and nothing about it reaches the model |
| AC8 | Plain sides in a person's books | Personal books: any voucher card on the phone | Each line says To or From («به», «از») where company books say Dr and Cr («بد», «بس») |
| AC9 | The date in its place | The app in English, a Persian card dated ۲۴ مهر ۱۴۰۵ | The line reads «کیف پول سبز — موجودی اولیه · ۲۴ مهر ۱۴۰۵», right to left, the date in one piece |
| AC10 | No button names | A draft card, asked in Persian with the app in English | The reply says the card is ready; it never names a button («دکمهٔ تأیید»), as the buttons are in the app's own language (Post, ثبت, Confirm) |
| AC11 | Swipe to reply | Phone, or the web on a touch screen: swipe an answer, your own message or a card left | It follows the finger and a reply arrow comes up beside it; past the line the arrow fills (and the phone ticks). Let go there: replying to it, as AC4. A short swipe springs back and does nothing; a swipe right, or scrolling up and down, never replies. A card is swiped by its body, never from its buttons |

## AD. A person's card on the web (2026-10-16)

From the owner, after AC8: on the web, a draft card in a person's books still showed the server's own summary, in English, with "DR" and "CR" («Proposed journal entry on 2026-10-16 (OMR): DR 112001 کیف پول سبز: 77.5 …»). The phone already showed the card itself, with To and From.

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| AD1 | The card itself | Personal books on the web: «۷۷٫۵ ریال عمان توی کیف پول سبز داریم» → its draft card | The amount large with its currency (77.500 OMR), what it is and its date («کیف پول سبز — موجودی اولیه، ۲۴ مهر ۱۴۰۵»), and each line: its code, its name, «به» or «از» and the amount. No "Proposed journal entry", no DR or CR |
| AD2 | In English | The same with the question in English | "To" and "From", the date in English |
| AD3 | Copy and reply | Copy the card; reply to it | Copied as it reads (what it is and the date, the amount, each line with To or From); a reply names the card (AC5) |
| AD4 | Cards without lines | A card that posts nothing to the books (count the books in Omani rials, remember a preference) | Its sentence as before |
| AD5 | Company books | A company's draft card on the web | Replaced by AE3 (2026-10-16): the card itself too, its lines Dr and Cr («بد», «بس») |

## AE. The web chat keeps its cards (2026-10-16)

From the owner's list of what the web lacked beside the phone: a web conversation opened again brought back only its words, and every card in it was gone (the draft, the posted one, the cancelled one). The phone redraws them. And a company's draft card on the web was the server's English summary, «Proposed journal entry on … DR … CR …», in Persian too.

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| AE1 | A conversation opened again | Draft a card; open another conversation; come back to this one (or reload the page) | The card is under the answer it came with, as it was drawn: amount, what and when, lines. Still pending (and not past its ten minutes): Confirm and Cancel work |
| AE2 | Each card as it stands | A conversation with a posted card, a cancelled one, one replaced by a correction, one left past ten minutes, one sent for approval | Posted: «Recorded», its reference and date, no buttons («Undone» when it was undone since). Cancelled, replaced, expired: struck through with what happened, no buttons. Waiting for approval: says so, no buttons |
| AE3 | A company's card | A company's draft card on the web, in Persian and in English | The card itself: the amount with its currency, what it is and its date, each line with «بد» or «بس» (Dr / Cr) and its amount; parties it will create and the approval line as before. No "Proposed journal entry" |
| AE4 | Only yours | A conversation's cards | Only cards the user made in that conversation come back; another user's conversation is still refused |
| AE5 | A starting balance corrected | Personal books: a wallet's starting-balance card, then «مبلغش رو ۸۰ کن» (or a reply to the card) | One card for 80 replaces it; the first can no longer be confirmed, and comes back replaced. Before, the wallet's card took no correction and both stayed confirmable |
| AE6 | The briefing once a day | Open the web chat in a second browser, or on the laptop after the phone, the same day | The same briefing isn't said again in the conversation; one that changed is (deep test 2026-10-20: three in one conversation) |

## AF. A bank turnover posted by the user's rules (2026-10-18)

From the owner's father, at مدرسه فرهنگ نو, on production: he dropped «تنخواه پارسیان 3 ماهه دوم 1405.xlsx» (the bank's own export: ردیف, تاریخ with its time, توضیحات, واریز/ برداشت, مبلغ (ریال) signed «+ 28,000,000» / «- 5,241,000», موجودی, and a summary panel beside it) into the web chat with: «از فایل گردش ۳ ماهه دوم ۱۴۰۵ ستون مبلغ (ریال) مبالغی که علامت + دارند در بدهکار حساب بانک دی و بستانکار درآمد شهریه و مبالغی که علامت − دارند در ستون بستانکار بانک و بدهکار مخارج مدرسه کارمزد بانکی به ترتیب تاریخ ثبت شوند», and: «میخوام به تفکیک تاریخ سند حسابداری رو بزنه». The chat answered, in English: "Looks like a transaction list — 96 rows, 83 voucher(s), 95 unmapped account(s)." It had read the bank's export as a journal, every description an account, and his rules never reached the assistant.

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| AF1 | The bank's export is a statement | Drop the file with no words (web or phone) | Imported as the bank's statement: 95 rows, 1405/04/01 to 1405/06/31, checked against the books; never "a transaction list". A journal export (account codes and titles, debit and credit) still goes to the journal import |
| AF2 | Posted by his rules, a voucher a day | The same file with his message, in a company whose chart has «بانک دی …», «درآمد شهریه», «کارمزد های بانکی طی ماه» and an expense account for the school's spending | The statement card, and one card: the rows in date order as one voucher per day («۹۵ ردیف در … سند روزانه، ۱ تیر تا ۳۱ شهریور ۱۴۰۵»), what each account receives in all: بانک دی debit (the «+» rows) and credit (the «−» rows), درآمد شهریه credit, the bank fees («کارمزد») debited to the fees account, the rest to the expense account. Each voucher's lines carry their rows' own descriptions |
| AF3 | Confirmed | Confirm the card | One voucher per day, oldest first, each dated its day, balanced; every row is marked recorded on its statement; بانک دی moves by the net of the «+» and «−» rows |
| AF4 | A name not in the chart | «… بدهکار مخارج مدرسه» where the chart has no such account | The assistant offers the closest accounts to choose from (or to make a new one); it never posts to an account that isn't there and never makes one up |
| AF5 | What is left out | Some rows already in the books (posted, matched or a duplicate); some dated in a closed period; a «−» row when only the «+» side has a rule | Those rows stay out, and the card says how many and why; nothing in a closed period |
| AF6 | Undo | Undo inside its two minutes | Every voucher it made goes, and its rows are open again on the statement. After the window, Reverse posts the opposite of each |
| AF7 | Without the assistant | The same message when the AI isn't available | The statement is imported and reviewed as in AF1, and the reply says so; nothing is lost |
| AF8 | In the reader's language | A journal export dropped in Persian | «به نظر فهرست سندهاست — …», not "Looks like a transaction list" |
| AF9 | It really is Parsian's account | The same file and rules, and the books have no account for Parsian: «این حساب پارسیان است، نه بانک دی» (or tap «یک حساب تازه برای بانک پارسیان») | One card, its bank line «بانک پارسیان (تازه)». Confirmed: «بانک پارسیان» is made, a bank party with its own account beside the bank the books keep (111002 beside «بانک دی» 111001, the code the card showed); the statement is Parsian's; the vouchers post to it. The next Parsian statement finds that account by itself, with nothing asked |
| AF10 | The bank is in the books already | The books have «تنخواه گردان حساب بانک پارسیان» (or a Parsian bank party) | The assistant is told the books' account for Parsian and offers it; a «new» Parsian account is never made beside it |

## AG. Money between you and a person, in a person's books (2026-10-18)

From the owner, in his own personal books: «Amir payed for these:» and nine lines, «$333.77 - Muscat Airbnb» … «$350.00 - Yerevan Airbnb», then «i should pay him back someday keep track of the payments». Run with the real model on a throwaway book: nine cards in USD, each owed to Amir, 4,555.06 in all. But only the first card made and named Amir; the other eight named no one, nor did «I paid Amir back 1000 dollars in cash». «How much do I owe Amir?» was answered from the whole «بدهی به دیگران» account, right only while Amir is the one person in it. The stays went under «متفرقه», as a person's books had no travel category. And right after the nine were confirmed the assistant still called them pending.

| ID | Scenario | Steps | Expected |
|----|----------|-------|----------|
| AG1 | A list someone paid for | The message above, in books with no Amir | One card per line, in USD, each «— paid by Amir»: the stay or ticket under «سفر» (Travel), the same amount owed to Amir («بدهی به دیگران»). Every card names Amir |
| AG2 | Confirmed in any order | Confirm the nine cards, the last first | One Amir is made; all nine entries are his |
| AG3 | How much do I owe him | «How much do I owe Amir?» | 4,555.06 USD, from the entries that are Amir's |
| AG4 | Two people | Also «Sara paid 40 dollars for my taxi», then «how much do I owe Amir?» | Amir is still 4,555.06 USD; Sara is 40 USD; «who do I owe?» lists both, each in its currency |
| AG5 | Paying him back | «I paid Amir back 1000 dollars in cash today» | One card: 1,000 USD off what I owe, from the cash, naming Amir. Confirmed, Amir is 3,555.06 USD |
| AG6 | Someone owes me | «I lent Reza 200 dollars from my card», later «Reza paid me back 50» | Reza owes 200, then 150, both naming Reza; «who owes me?» says Reza 150 USD |
| AG7 | Travel in the books | Any person's books, new or kept from before this change | A «سفر» (Travel) category under living expenses; «hotel», «Airbnb», «flight» find it |
| AG8 | It knows what I confirmed | Confirm cards, then ask anything about them | The assistant speaks of them as in the books, never as waiting for confirmation; cancelled ones as cancelled |

## AH. A school's journals dropped in the chat, and its own codes (2026-10-19)

From the owner, on production, in منادی حق's books: «این فایل های اکسل دو سال گذشته مدرسه هست» with manadi-1404.xlsx and manadi-1405.xlsx. The chat read only the first file, with an old parser. It took the date column for the voucher number and the ledger code (1100, 1300) for a "day code", and called a 1404 file 1405. It dated 6 of 124 vouchers, all 2027-01-30, and «اعمال شد — 6 سند» posted those six with only the lines whose account had matched (2 of 18): one-sided vouchers a year ahead. Asked «سند هارو ببینم», the assistant said nothing was in the books and asked for the files again. In the same books, «کدینگ مخارج مدرسه اشتباه ست باید ۱۳۰۰ میبوده» found nothing. The import had given the school's accounts the app's codes (their 1300 «مخارج مدرسه» became 6211 and 6132, two headings, because its bank fees went under 62 and the rest under 61). The assistant offered 6211, which it had just said takes no entries, and offered to create an expense account at 1300 (assets, in the app's numbering). It typed its choices as bullets.

| ID | Scenario | Steps | Expected |
|----|----------|-------|----------|
| AH1 | Both years, read right | Drop manadi-1404.xlsx and manadi-1405.xlsx in the chat with «این فایل های اکسل دو سال گذشته مدرسه هست» | One card per file, by the journal importer the migration page uses: 1404 has 199 vouchers from ۲۹ فروردین ۱۴۰۴ to ۲۹ اسفند ۱۴۰۴, and 1405 has 53. Each card says how many are ready, how many accounts it will create, and what is held back (already in the books, an opening repeated) |
| AH2 | Import from the card | Tap «وارد کن» on a card whose vouchers are all ready | Every ready voucher is posted whole, on its own date. The answer, saved in the conversation, says how many and from when to when. «سند هارو ببینم» then lists them |
| AH3 | Accounts to match first | A file with an account the chart can't place | The card doesn't import. «تطبیق حساب‌ها» opens the journal import page on the same file, with nothing to upload again |
| AH4 | Never part of a voucher | The old Excel import page, with an account left unmatched | Vouchers with an unmatched line aren't posted at all; the others are, whole |
| AH5 | The six misread vouchers are gone | After the deploy | Vouchers whose number is a date (V1404/01/29) and that sit on another date are removed, with an audit row each. The import can then be done again |
| AH6 | One «مخارج مدرسه» | After the deploy, and on any new import | The two headings an import made of one ledger are one: its bank fees under the same «مخارج مدرسه» as the rest, the empty one put away |
| AH7 | My own codes | «کد ۱۳۰۰»، «۱۳۰۲ ملزومات»، search for 111004 | The account that had that code in the school's file is found; cards and the chart show both, «۶۱۳۲ مخارج مدرسه (۱۳۰۰)» |
| AH8 | A heading isn't offered | Ask to post to «مخارج مدرسه» | The assistant offers its accounts (کارمزد, ملزومات, پذیرایی, تعمیرات, قبوض…), never the heading, and never «create 1300» |
| AH9 | Choices are chips | Any question with options | Tappable choices, never a typed list of bullets |
| AH10 | One code, several accounts | A file whose 1305 is three accounts | The review says so; searching 1305 offers the three |
| AH11 | Said once, in the reader's language | The detection line and the card | «به نظر فهرست سندهاست» and «حساب نگاشت‌نشده» on both, in Persian digits |
## AI. A company's books cleared, to import them again (2026-10-19)

The two schools (منادی حق, فرهنگ نو) were imported before the journal importer kept their own codes and made one heading per ledger, and a misread chat import left one-sided vouchers in one of them. The owner chose to clear both and import the files again. A company could only be suspended; a login can't move to another company; and the owner's own «پاک کردن دفاتر» in Settings wiped a hand-kept list of tables (no chats, imports, petty cash, fixed assets, the settings naming the old accounts) and emptied every company's payment methods.

| ID | Scenario | Steps | Expected |
|----|----------|-------|----------|
| AI1 | What would go | Platform admin → Companies → «پاک کردن دفاتر» on a school | Before anything changes: what the school holds — vouchers, accounts, parties, statements, imports, chats… each with its count |
| AI2 | Typed, or nothing | Type a name that isn't the school's | Refused; nothing changes |
| AI3 | Cleared | Type the school's name | Every entry, account, party, statement, import, invoice, payroll and chat of that school goes; its chart is seeded again as a new company's; its journal account map, posting roles and closed period go with the old accounts |
| AI4 | What stays | After AI3 | The school itself, its logins (the father signs in as before), its profile and configuration (calendar, currency, AI, mail), its tax and exchange rates, its AI spending, and its audit trail — with a row for the reset |
| AI5 | Nobody else's | After AI3 | Every other company is as it was, payment methods included |
| AI6 | Import again | Drop the school's files in the chat after AI3 | Every voucher goes in, with the school's own codes beside the app's and one heading per ledger |
| AI7 | The owner's own wipe | Settings → «پاک کردن دفاتر» in the school | The same complete wipe of its own books, as before typed by its name, its chart re-seeded |

## AJ. A bank's statement as a scanned PDF, every page of it (2026-10-20)

From the owner: «20261015_133322_receipt.pdf», Day Bank's own statement of فرهنگ نو's account 0105222028000, ۱ تیر to ۳۱ شهریور ۱۴۰۵, 22 pages of pictures (no text in it), about ten rows a page, each with its time, a long narrative, a signed amount (+ / −) and the balance. The vision reader read it right — 40 rows, the balance agreeing from each row to the next — and stopped there: it reads the first 4 pages only, said nothing of the other 18, and took 73 s for those four in one call. The file is called a receipt, Day Bank wasn't a bank the app knew, and a scanned PDF is a statement only when its name or the message says so.

| ID | Scenario | Steps | Expected |
|----|----------|-------|----------|
| AJ1 | Every page | Bank statements → upload the 22-page PDF | It is read in the background, page by page, a few at a time; the page shows «در حال خواندن: ۷ از ۲۲ صفحه» until done. Every row of every page is imported, in the statement's order |
| AJ2 | The balance agrees | When it's read | «۲۲ صفحه، ۲۰۵ ردیف خوانده شد؛ مانده‌ها از هر ردیف به ردیف بعد جور است» |
| AJ3 | A misread is set right, or named | (a) An amount misread by a digit, its balances on either side vouched for by their own neighbours — the real file had two: 847,522,020 read as 47,522,020 (page 8, row 2) and 320,000 as 32,000 (page 21, row 9). (b) A balance misread, or a row the reader skipped | (a) The amount the balances say is kept, said («۲ مبلغ نادرست خوانده شده بود و از روی مانده‌های دو طرفش درست شد: صفحهٔ ۸، ردیف ۲: ۴۷٬۵۲۲٬۰۲۰ ← ۸۴۷٬۵۲۲٬۰۲۰ …»), and the row shown amber (60%) in the table; the balance then agrees on every link. (b) Never guessed: «مانده در صفحهٔ ۷، ردیف ۶ جور نیست — آن ردیف را بررسی کنید», the rows kept for review. Only a one-digit slip (or which way the money went) is mended, never across a page not read |
| AJ4 | A page it can't read | One page fails twice | The others are imported; «صفحهٔ ۱۲ خوانده نشد» is said, never silent |
| AJ5 | Its bank and account | The statement's header | Day Bank, account 0105222028000, read from its first page: the statement is the books' «بانک دی» |
| AJ6 | In the chat, with rules | Drop it in the chat with «واریزها به درآمد شهریه، برداشت‌ها به مخارج مدرسه، کارمزدها به کارمزد بانکی، به تفکیک تاریخ» | «صورتحساب بانک دی، ۲۲ صفحه، در حال خواندن…» with the pages as they're read; when done, «ادامه با دستور شما» makes the one card of AF2 — a voucher a day |
| AJ7 | A scanned statement called a receipt | Drop it with no words | Recognised as a bank statement from its first page, not read as a receipt |
| AJ8 | Stopped half-way | The server restarts while it reads | Not left «reading» for ever: said to have stopped, and the same file can be uploaded again |
| AJ9 | Short ones as before | A 1–2 page PDF or a photo | Read at once, as before, with its balance check |
| AJ10 | A big scan in the chat | The owner, 2026-10-21: the same 22-page statement, 10.4 MB, dropped in the web chat (and shared to the phone's chat) | Taken, as the Bank Statements page takes it: up to 20 MB on the web, the phone and the Bank Statements page alike. Over 20 MB, refused before it is sent, in words with the limit («حداکثر ۲۰ مگابایت»). It was refused at 8 MB: «حجم فایل زیاد است — حداکثر ۸ مگابایت» |
| AJ11 | Day is «بانک دی» | The owner, 2026-10-21: فرهنگ نو's Day Bank statement (the reader names it «Day») in books whose bank is «بانک دی» (made by the journal import, account 0105222028000) | Compared with «بانک دی»'s own account, never the chart's 1110: «Day» and «بانک دی» are one bank, as «Mellat» and «ملت» are. A bank with no party but an account named with its number («بانک دی حساب 0105222028000») is found by that number |
| AJ12 | Which account, said | Any statement in the chat | The card says which account it was compared with («مقایسه با ۱۱۱۲۰۱ بانک دی»). When that is only the chart's default, it says so and offers to choose the account; step by step waits for the choice rather than posting to a guess |
| AJ13 | Step by step on the web | «رفع قدم‌به‌قدم» on a statement with differences | One card at a time, without the model: the row's date and words, the bank's own account, the suggested other side, and «پس از این، ۵۸ ردیف دیگر مانده». Once a card is confirmed, the next one comes by itself; «بعدی» skips to it; when none is left: «همهٔ ردیف‌های این صورتحساب در دفاتر است…». The same as the phone's Next (N31) |

## AK. A lunch with a friend, from the phone (2026-10-20)

From the owner's phone, in their personal books, which count in Omani rials: «ناهار با امیر ۷ ریال عمان از کیف پول قشنگم». The card posted 7.000 OMR to 6110 «خوراک و سوپرمارکت» (Food & groceries) from 112001 «کیف پول سبز». The reply said «کیف پول قشنگم» and «امیر هم به‌عنوان طرف معامله جدید به کارت اضافه شده», but the card showed no Amir, and under the posted card the reply still said «تا تأیید کارت، چیزی ثبت نمی‌شود». «کیف پول قشنگم» is the green wallet: the owner had asked for it to be renamed, the assistant could only remember the new words for it, and the account kept its old name. The dining-out category 6180 had never been used, and in books with 30 or more lines an unused category ranked as a fallback, below groceries, which was in use.

| ID | Scenario | Steps | Expected |
|----|----------|-------|----------|
| AK1 | A meal out is dining out | Personal books with entries in 6110 and none in 6180: «ناهار با امیر ۷ ریال از کیف پول» | The card debits 6180 «تفریح و رستوران», never 6110. «شام»، «رستوران»، «کافه» are dining out too. «خرید سوپرمارکت» and «میوه» stay food & groceries. A category the person has never used still ranks as itself in their books |
| AK2 | Company is not a party | «ناهار با امیر …», paid from the wallet | No party on the card: Amir isn't made, linked or listed in People for a lunch he only shared. «امیر حساب کرد» (Amir paid), «به امیر قرض دادم» (lent to Amir) and «پول امیر رو دادم» (paid Amir back) still name him (AG1–AG6) |
| AK3 | One Amir | Amir is already in the books, as the person who paid for the trips (AG). A new card names «امیر» as a new person, of any type | The card links the Amir already there; nobody new is made, and People has one Amir |
| AK4 | The person on the phone's card | A card that names a person | Phone and web: «شخص: امیر» under the description, or «شخص جدید: امیر» when confirming makes them. In a company's books: «طرف حساب: …» |
| AK5 | A wallet renamed | «اسم کیف پول سبز رو بکن کیف پول قشنگم» | One confirm card: «112001 کیف پول سبز ← کیف پول قشنگم». Once confirmed, the account itself is «کیف پول قشنگم» on every card, balance and report, with its entries and starting balance kept. A wording is never only remembered in place of a rename |
| AK6 | Both names, put right | Books where 112001 is «کیف پول سبز» and «کیف پول قشنگم» was only remembered for it: ask for the rename again | The card renames 112001 to «کیف پول قشنگم»: one name, the one asked for |
| AK7 | A bank renamed with its account | A bank party whose account was made from its name (e.g. «حساب بانکی ملت»), renamed «ملت مرکزی» | The party and its account are renamed together. An account named some other way, such as an imported chart's «بانک ملت شعبه ۱۲ حساب ۱۲۳», keeps its name. The same on the Parties page: a bank renamed there renames its account too (deep test 2026-10-20) |
| AK8 | The reply doesn't go stale | A card is proposed, then confirmed | The reply says the card is ready. It doesn't add «تا تأیید کارت، چیزی ثبت نمی‌شود», which reads false under a posted card |
| AK9 | What's left, as worked out | Owing Amir 5,000,000, «۲۰۰ هزار تومان از پول امیر رو پس دادم» | The reply's «۳٬۰۰۰٬۰۰۰ ریال باقی می‌ماند» reaches the phone as written. The real model wrote 3,000,000, and the miscopy fix turned it into the card's 2,000,000, one digit off. Round figures, sums or differences of the tools' figures, and a different first digit are never treated as miscopies; 21,712,100,283 for 21,721,200,283 still is |

## AL. The accountant speaks first: weekly and monthly reviews, suggestions, a nudge (2026-10-20)

From the owner: «I like proactive features … each week or month the AI gives me a report in the chat, a message or notification about our spending, or suggestions», and «remind me to save if I set a goal». The app had a morning briefing of insights, the bell, web push and an opt-in daily cash digest, but nothing that reviews a week or a month on its own, and no suggestions. Reviews are worked out from the books, with no model call, so every figure is exact. Each one is a message in the chat with the figures, a table of categories and up to two suggestions, each with a button that asks the assistant. A phone notification (hidden on the lock screen) and a bell row with a web push say it's ready.

| ID | Scenario | Steps | Expected |
|----|----------|-------|----------|
| AL1 | A weekly review, personal | Personal books with entries last week (Saturday to Friday on the Jalali calendar, Monday to Sunday otherwise). The new week's first day comes | One review for last week: what was spent, against the average of the 4 weeks before («۱۸٪ کمتر از هفته‌های معمولتان»); income; the biggest spend; who you owe and who owes you; a table of the top categories, each against its usual. Made once, even if the job runs again |
| AL2 | A weekly review, a company | A company's books with last week's entries, read by the owner, CFO and accountant | Money in and out of the banks and cash; revenue and expenses; cash now and runway; overdue invoices (count, total, the oldest debtor); bills due in the next 7 days. Managers, employees and viewers get none |
| AL3 | A monthly review | The first day of a month in the company's calendar (1 آبان) | Last month's review: personal is the report card (income, spending, saved and the rate, against the month before, budgets kept or over, net worth's change, goals); a company gets revenue, expenses and profit against the month before, cash, receivables and payables. Top categories against the month before |
| AL4 | Where it shows | The phone app is opened; the web chat is opened | The review is the assistant's message in the open conversation, once per person, and stays in its history. A review made after the day's briefing still shows the next time the chat is opened |
| AL5 | A notification | A review is made | The phone shows «Your weekly review is ready» with its headline (contents hidden on the lock screen) and opens the chat on a tap. The web bell has it and sends a web push. Once seen, it leaves the bell |
| AL6 | A goal behind | A goal «لپ‌تاپ», 60,000,000 by Esfand, saving below what each month needs | «برای رسیدن به «لپ‌تاپ» تا اسفند ماهی ۸٬۰۰۰٬۰۰۰ لازم است؛ این سه ماه ماهی ۳٬۰۰۰٬۰۰۰ گذاشتید.» Button: «چطور به هدف لپ‌تاپ برسم؟» |
| AL7 | Paid this week | Income came in last week, and a goal isn't reached | «این هفته درآمد داشتید — ۸٬۰۰۰٬۰۰۰ برای «لپ‌تاپ» کنار بگذارید؟» Button: «۸٬۰۰۰٬۰۰۰ برای هدف لپ‌تاپ به حساب پس‌انداز لپ‌تاپ منتقل کن», which the assistant turns into a card |
| AL8 | A budget's pace | In the month, «خوراک» spends faster than the days pass and will pass its limit | «با این روند، «خوراک» حدود ۲۴ آبان از بودجه‌اش می‌گذرد (۶٬۰۰۰٬۰۰۰ از ۸٬۰۰۰٬۰۰۰).» Button: «چطور خرج خوراک را کم کنم؟» |
| AL9 | A category running hot | A category at 1.5× its usual or more, and a tenth of the spending or more, with no budget | «خرج «تفریح و رستوران» ۶۰٪ بیشتر از معمول بود — بودجهٔ ماهانه برایش بگذارید؟» Button: «برای تفریح و رستوران بودجهٔ ماهانه بگذار». Personal books can now set and read budgets in the chat |
| AL10 | Owed a while | Someone owed (either way) with no entry naming them in 30 days | «هنوز به امیر ۴٬۰۰۰٬۰۰۰ بدهکارید (آخرین بار ۲۸ مهر).» Button: «پس دادن پول امیر را ثبت کن» |
| AL11 | Subscriptions | Monthly review: 2 or more monthly repeating spends | «۳ پرداخت ماهانه تکراری دارید، روی هم ۲٬۴۰۰٬۰۰۰ در ماه: …» Button: «پرداخت‌های تکراری‌ام را نشان بده» |
| AL12 | A company's suggestions | Overdue invoices; runway under 3 months | «۴ فاکتور سررسیدگذشته، ۱۲۰٬۰۰۰٬۰۰۰ …» Button: «کدام فاکتورها سررسیدشان گذشته؟». «نقدینگی حدود ۲٫۵ ماه دوام دارد …» Button: «چطور دوام نقدینگی را بیشتر کنم؟». At most 2 suggestions a review, the most pressing first |
| AL13 | A nudge | Personal books with 5 or more entries; nothing recorded for 4 days; no nudge in the last 7 days | «از سه‌شنبه چیزی ثبت نشده — خرجی بوده که بنویسیم؟» in the chat, with a notification. Never on a day something was recorded, and never twice in a week |
| AL14 | Turned off | «دیگه مرور هفتگی نفرست» in the chat, or the web's settings | A confirm card turns weekly reviews off for this person only. Monthly reviews, suggestions and nudges are separate switches. Others in the company still get theirs. Turning one back on works the same way |
| AL15 | Nothing to say | Books with no entries in the last 5 weeks, or a new company | No review, no notification, no empty message |
| AL16 | In your language | The same review read by a Persian and an English reader | Each reads it in their own language, digits and calendar («۲۶ مهر تا ۲ آبان»; "18–24 Oct"). A name written the other way keeps its order: in English, "the oldest is فروشگاه مهرگان (شعبه ۲), 10 days"; a count of one reads right ("Overdue invoices: 1") (deep test 2026-10-20) |
| AL17 | A card to enjoy | The owner: «I want the UI of it to be playful and likeable, maybe charts, maybe animations». A review in the phone or web chat | A card, not words: a mood colour and emoji (🎉 a good week, 🏆 a good month, 👀 above the usual, 🌿 about as usual, ✏️ the nudge) that pops in; the figure counts up to the books' own; a pill gives the change against the usual (green when it's good); the weeks or months as bars that grow in, this one highlighted, with a dashed "usual" line; a month's savings ring filling to its rate; the categories as bars that slide in, each with a mark at its usual; the facts as chips; each idea its own card with a button. A good one throws a little confetti. Drawn from history it stands still; with reduced motion asked for (web) or animations off (phone), it is all there at once. An older phone app shows its words |
| AL18 | Amounts with places | UK books (pence) or a person's books in Omani rials (baisa): a week of 2,459.97, a category running hot | The review is made and kept with its figures as numbers; the bell, the web chat and the phone's briefing show it, and the briefing never fails (deep test 2026-10-20: «2459.97» kept as text, the card compared it with a number, and the phone's briefing answered 500). A review kept before the fix reads the same |
| AL19 | Easy to read | A review card on a phone, a tablet and a desktop, in Persian and in English | No word under 11 px; a long label under a bar («21 Shahrivar», "This week") goes on two lines, never cut; «معمول» sits at the oldest bar's side, never over this week's bar (deep test 2026-10-20) |

## AM. Can I afford it? (2026-10-20)

From the owner: «I want to buy something, and the system checks my finances and tells me whether I can or can't buy it, or by installments, or reminds me to save if I set a goal for saving». Investment suggestions are paused (the owner: «might need real work … later»). The answer is worked out from the books, not guessed: the money in the cash and bank places now; the last three months' average income and spending; the payments due in the next 30 days; and what the savings goals already need each month. A month's spending plus those payments is the cushion kept untouched. An installment may take at most half of what is left each month after the goals.

| ID | Scenario | Steps | Expected |
|----|----------|-------|----------|
| AM1 | Yes, now | 120,000,000 in the bank, spending 25,000,000 a month, income 40,000,000: «می‌خوام یه گوشی ۲۰ میلیونی بخرم، می‌تونم؟» | ✅ «بله، همین حالا»: after it 100,000,000 is left, above the 25,000,000 cushion. A card: the price, then "now" and "after" bars against the cushion line |
| AM2 | Wait a little | The same books, 105,000,000 | 🧘 «حدود ۱ ماه صبر کنید و نقد بخرید»: 95,000,000 is free, so 10,000,000 is short at 15,000,000 a month to spare. A short wait comes before a long installment plan |
| AM3 | Installments | A price that would take more than 3 months to save, where one of 3, 6, 12 or 24 equal monthly payments fits within half the monthly spare | 🗓️ «در ۱۲ قسط ماهی …»: the shortest plan that fits, with the plan's monthly payment («plus any interest the seller charges») and how long saving would take instead |
| AM4 | Save first | No plan fits | 🐷 «ماهی ۱۵٬۰۰۰٬۰۰۰ کنار بگذارید، حدود ۱۵ ماه». Its idea: «یک هدف پس‌انداز ۳۰۰ میلیونی برای ماشین تا … بساز» → a goal card (AM6) |
| AM5 | Not now | Spending above income on average over the last three months, and the cash less the cushion is short of it | ✋ Not now, saying why: nothing is left over each month. Its idea: «چطور خرجم را کم کنم؟». Never a lecture, never investment advice |
| AM6 | A goal from the chat | «یک هدف پس‌انداز ۳۰۰ میلیونی برای ماشین تا دی ۱۴۰۶ بساز» | A confirm card makes the goal, with a savings place of its own («پس‌انداز ماشین», under the bank accounts) unless one is named. The weekly review then reminds about it (AL6, AL7) |
| AM7 | What is already promised | Installments or cheques due in the next 30 days, and goals that each need money every month | The payments enlarge the cushion; the goals' monthly needs come off what is spare, and the card says so («ماهی ۵٬۰۰۰٬۰۰۰ برای هدف‌ها کنار گذاشته شده») |
| AM8 | Another currency | A price in dollars in books that count in rials | Converted at the latest rate on file and said so; with no rate, the assistant asks for the price in the books' currency |
| AM9 | As a card | Any answer | The review card's look (AL17): an emoji per answer (✅ 🧘 🗓️ 🐷 ✋), the price counted up, bars for "now" and "after" with the cushion line, a ring showing how much of the price the free money covers, the reasons as chips, and the ideas with their buttons. On the phone and the web, in the reader's language |
| AM10 | Too new to tell | Books with less than a month of history | The cash part only, saying the monthly picture needs a month of entries |
| AM11 | Amounts with places | A person's books in Omani rials: 900.500 against 150.125 spare a month; a price of $100.50 at 0.385 | 12 installments of 75.042; the dollar price is 38.693 rials; never an error (deep test 2026-10-20: half the spare and the rate were a Decimal times a float) |

## AN. A company's own account codes (2026-10-21, `docs/PLAN_OWN_CODES.md`)

The owner, after importing فرهنگ نو's journals: «the coding is still wrong in دفتر کل». Asked which codes the books should carry, they chose the school's codes only. Each account keeps the app's code for what it is (`class_code`), and every rule reads that instead of the code's digits.

| ID | Scenario | Steps | Expected |
|----|----------|-------|----------|
| AN1 | Nothing moves for anyone else | Any company with the app's chart | Every report, card and figure is as before; `class_code` is empty |
| AN2 | Renumbered, the same figures | A year of every kind of entry; renumber every account to arbitrary codes, classes kept | The trial balance, balance sheet, P&L, cash flow, dashboard, CFO figures, insights and reviews give the same figures (only the codes differ) |
| AN3 | The school's codes in the Ledger | فرهنگ نو, switched to its own codes | The Ledger reads 3000 «هزینه های عمومی» (دفتر کل) with 3001 «پذیرایی…», 3002 «ملزومات مدرسه», 3003…, 3006; 4000 «هزینه های پرسنلی» with 4001, 4002; 1602 «وزارت دارایی»; no 6132, 613201 or 219703 |
| AN4 | The statements still right | AN3's books | Tuition (2000) is revenue, creditors (1600…) liabilities, the expenses (3000–5000) on the P&L, the banks (1100, 1101) and petty cash (1110) cash: the same balance sheet and P&L as before the switch |
| AN5 | Clashes settled first | A journal giving 5000 to both «هزینه اجاره» and «سود و زیان», and 1310 to rent | The preview asks which keeps 5000 (the journal's own use first); nothing changes until it's settled |
| AN6 | Everything follows | A statement, a recurring rule, a bank party, a savings goal on an account that is renumbered | Each keeps pointing at the same account under its new code |
| AN7 | Typing their codes | «۵۰۰ هزار تومان ملزومات، کد ۳۰۰۲» in the chat; the voucher form's account box; the chart's search | 3002 is «ملزومات مدرسه» everywhere; a new account is made with the next code under its ledger (3007) |
| AN8 | The next year's file | 1406's journal in the same codes, dropped after the switch | Each code is its account: nothing to match by hand |
| AN9 | The pages by class | A company in its own codes: the CEO and manager charts' drill-downs (revenue 41–43, assets 11–15), the budget's category list, the commitments' and goals' account lists, the default cash account | Each finds the company's own accounts by what they are: a click on revenue lists the tuition lines (2000), the budget offers its expenses (3000…), the default cash account is its bank, not an unrelated 1110 |

## AO. Spending in the currency it was spent in (2026-10-21)

From the owner's phone: personal books that count in Iranian rials, a week of spending in Omani rials with no rate between the two. «هفته گذشته خرج ما چیا بوده و توی چه دسته بندی هایی؟» was answered "zero rials, the expenses are in Omani rials with no rate", and «خب ریال عمانش رو بگو» with "this report only gives the books' currency". The spending summary added every currency up at its value in the books' currency, and a line with no rate has none.

| ID | Scenario | Steps | Expected |
|----|----------|-------|----------|
| AO1 | A week in Omani rials | Personal books in IRR; last week 4 entries in OMR (a restaurant 12.500, groceries 20.250, a taxi 3.750, a café 5.000), no OMR rate. «هفته گذشته خرج ما چیا بوده و توی چه دسته بندی هایی؟» | «۴۱٫۵۰۰ ریال عمان» with the categories in Omani rials (خوراک و سوپرمارکت ۲۰٫۲۵۰، تفریح و رستوران ۱۷٫۵۰۰، حمل‌ونقل ۳٫۷۵۰); never "zero", never "can't tell" |
| AO2 | Asked for one currency | «خب ریال عمانش رو بگو» | The same week in Omani rials, from the tool asked for OMR |
| AO3 | Two currencies in one week | AO1 plus 3,000,000 IRR of groceries paid from a rial bank account | Each currency on its own, nothing added together: «۴۱٫۵۰۰ ریال عمان و ۳٬۰۰۰٬۰۰۰ ریال», each with its categories |
| AO4 | The quick answer | «این ماه چقدر خرج کردم؟» in AO3's books | Each currency listed, not «۰ ریال» |
| AO5 | The card on the phone | AO3 | Each row in its own currency: the OMR categories read «ریال عمان» with their baisa, the IRR one «ریال» |
| AO6 | A company with rates | A company in IRR with a USD expense whose rate is on file | The total in rials as before; the dollars are also listed in their own amounts |

## AP. Each currency in the reviews (2026-10-21, `docs/PLAN_CURRENCIES.md` phase 1)

The owner: «i want to have different currencies. isn't that ok?» It is. A person's weekly and monthly reviews totalled everything in the books' currency, so a week spent in Omani rials, in books that count in rials, read as nothing.

| ID | Scenario | Steps | Expected |
|----|----------|-------|----------|
| AP1 | A week in two currencies | Personal books in IRR; last week 3,000,000 IRR of groceries and 41.500 OMR (groceries 20.250, dining 17.500, transport 3.750); no rate between them | Two cards, the rials first: «… — ریال ایران» with 3,000,000, and «… — ریال عمان» with 41.500 and its three categories and bars; nothing added together. The bell and the phone notification say the first |
| AP2 | A week in Omani rials only | The same books, only the OMR entries last week | One card in Omani rials: «۴۱٫۵۰۰ ریال عمان», its categories and its own usual week; never «۰ ریال» |
| AP3 | A month in two currencies | AP1's entries in last month | The rials card keeps the budgets, the net worth and the goals; the Omani-rial card has its own income, spending, saved, savings rate, categories and four months of bars |
| AP4 | The ideas stay with the first | AP1, with a category at twice its usual in Omani rials | No «set a monthly budget of 70» in rials for an Omani-rial category (since phase 4 it is offered in Omani rials, AS4); the ideas are on the first card only |
| AP5 | The report card | «کارنامه ماه قبلم چطوره؟»; the My finances report card | The first currency's month, the others listed in their own amounts; asked for OMR, the Omani-rial month |
| AP6 | On the web | The chat after AP1, and again after a reload | Both cards, in order, each with its own figures |
| AP7 | A company stays as it was | A company in IRR with a USD expense whose rate is on file | One card, in rials, with the dollars counted at their rate |

## AQ. Each currency on My finances and the widget (2026-10-21, `docs/PLAN_CURRENCIES.md` phase 2)

My finances and the phone's widget showed one figure in the books' currency, so Omani rials with no rate showed as nothing there.

| ID | Scenario | Steps | Expected |
|----|----------|-------|----------|
| AQ1 | A row for each currency | Personal books in IRR: 10,000,000 IRR at the bank and 100 OMR in the wallet; this month 3,000,000 IRR of groceries and 41.500 OMR of food, dining and transport. Open My finances | A row for «ریال ایران» (cash 7,000,000, spent 3,000,000) and one for «ریال عمان» (cash 58.500, spent 41.500), the currencies that moved this month first; nothing added together |
| AQ2 | The details in one currency | AQ1; tap «ریال عمان» under «نمایش جزئیات به» | The categories, the months' spending and the report card in Omani rials (20.250, 17.500, 3.750); back to «ریال ایران», the rials' |
| AQ3 | Only Omani rials moved | Books in IRR with only OMR entries | No rows and no switch: the three figures as before, in Omani rials, never «۰ ریال» |
| AQ4 | The widget | AQ1, on the phone's home screen | «۵۸٫۵۰۰ ریال عمان» or the books' first currency large, the others under it («۷٬۰۰۰٬۰۰۰ ریال · …»), the budget left below |
| AQ5 | One currency | Books with entries in one currency | My finances and the widget as before |

## AR. Net worth and savings goals in each currency (2026-10-21, `docs/PLAN_CURRENCIES.md` phase 3)

Net worth added everything up in the books' currency at its base value, and a goal counted its account the same way. Omani rials with no rate counted as nothing, so a goal saving into an Omani-rial wallet read 0%.

| ID | Scenario | Steps | Expected |
|----|----------|-------|----------|
| AR1 | Net worth per currency | Personal books in IRR: 10,000,000 IRR at the bank, 100 OMR in the wallet, 30 OMR owed to a friend | Net worth 10,000,000 IRR and 70 OMR, each on its own; My finances shows the chosen currency's, with «ارزهای دیگر: ۷۰٫۰۰۰ ریال عمان» under it |
| AR2 | Asked in the chat | «دارایی خالصم چقدره؟» | «دارایی خالص شما امروز، هر ارز جداگانه: ۱۰٬۰۰۰٬۰۰۰ ریال، ۷۰٫۰۰۰ ریال عمان.», and a card listing each currency |
| AR3 | A goal in Omani rials | A goal of 500 OMR on the Omani-rial savings wallet holding 100 OMR, by a date 4 months away | 20%, 400 OMR to go, 100 OMR a month: in Omani rials, not 0% |
| AR4 | The review's goal idea | AR3, the goal behind | «… ۱۰۰٫۰۰۰ ریال عمان در ماه …»: the goal's own currency |
| AR5 | The report card and the month | A month in two currencies | Each card's net-worth change is in its own currency |
| AR6 | Gold at today's rate | Gold holdings on a rial account | Still valued at today's rate, in the rials' net worth |

## AS. Budgets in each currency (2026-10-21, `docs/PLAN_CURRENCIES.md` phase 4)

A budget was in the books' currency and counted spending at its base value, so Omani rials with no rate never touched it. The review's idea («set a monthly budget for dining») was held back for a category spent in Omani rials.

| ID | Scenario | Steps | Expected |
|----|----------|-------|----------|
| AS1 | A budget in Omani rials | Personal books in IRR; a 50 OMR budget for dining this month; 41.500 OMR spent on dining, and 3,000,000 IRR on dining from the bank | The budget reads 41.500 of 50 OMR (83%); the rials don't count in it |
| AS2 | Two budgets for one category | AS1, plus a 5,000,000 IRR budget for dining | Two budgets: 3,000,000 of 5,000,000 IRR and 41.500 of 50 OMR; setting one again changes only that one |
| AS3 | Set from the chat | «برای رستوران ماهی ۵۰ ریال عمان بودجه بذار» | A card for 50 OMR a month; confirmed, the budget is in Omani rials |
| AS4 | The review's idea | A week where dining in Omani rials ran hot, with no Omani-rial budget for it | «برای تفریح و رستوران بودجهٔ ماهانهٔ ۷۰٫۰۰۰ ریال عمان بگذار»: its own currency |
| AS5 | The alert | AS1 at 90% | «Budget at 90%: dining», «45.000 OMR of 50.000 OMR»; the rials' budget alerts on its own |
| AS6 | On My finances | Books with two currencies | The budget form offers the currency; each budget shows its own; the next months copy each in its own currency |
| AS7 | A company | A company's budget with a USD expense at its rate | As before: base currency, every expense at its base value |

## AT. Can I afford it, insights and repeating payments, in each currency (2026-10-21, `docs/PLAN_CURRENCIES.md` phase 5)

«Can I afford it?», the insights («What changed», the briefing) and the repeating payments all counted the books' currency at its base value. A price in Omani rials needed an exchange rate, and Omani-rial spending never showed as a spike.

| ID | Scenario | Steps | Expected |
|----|----------|-------|----------|
| AT1 | A price in Omani rials | Personal books in IRR with 300 OMR in the wallet and three months of Omani-rial income and spending, no OMR rate. «می‌تونم یه گوشی ۲۰۰ ریال عمانی بخرم؟» | Judged on the Omani rials: their cash, their months, the goals that save in them; the card in Omani rials, no rate asked for |
| AT2 | A price in a currency not held | The same books, a price in dollars, no USD held | As before: converted at the latest rate on file, or asked for in the books' currency |
| AT3 | A spike in Omani rials | Dining at 10 OMR a month for three months, then 40 OMR this month | «تفریح و رستوران حدود ۴٫۰ برابر معمول است», with «40.000 OMR» against «10.000 OMR» |
| AT4 | Each currency's income | Omani-rial salary falls by half, rial income steady | An income drop for the Omani rials only |
| AT5 | Round amounts | Fifty entries in rials and a few in Omani rials | Each currency against its own typical entry: 50 OMR isn't small beside rials |
| AT6 | Repeating payments in Omani rials | Two Omani-rial subscriptions each month on the Omani-rial card | «۲ پرداخت هر ماه تکرار می‌شود، روی هم ماهی … ریال عمان» on that card |
| AT7 | A company stays as it was | A company with a USD expense at its rate | Its insights at base value, one total |

## AU. The owner's phone, 2026-10-21 (five screenshots)

| ID | Scenario | Steps | Expected |
|----|----------|-------|----------|
| AU1 | The place they named | Places «کیف پول سبز» (112001) and «کیف پول قشنگم»; «where is Amir's cash?» answered «کیف پول قشنگم» | The card's cash line is «کیف پول قشنگم», whatever code the model gave; a name the person taught the assistant for a place counts as that place; two places named (a transfer) are left as they are |
| AU2 | An answer never drops the list | «These were lent to me: 300 Oman Rials from Amir, $333.77 Muscat Airbnb, … $350.00 Yerevan Airbnb», then «one Amir gave in cash, the rest Amin paid» | A card for every item: Amir's 300 OMR into the place, and one Travel card per dollar line against «بدهی به دیگران» with Amin on each; «چقدر به امین بدهکارم؟» before they are confirmed says what was told and makes the cards, never only "nothing recorded" |
| AU3 | Things for the home | «دو تا مبدل برق خریدم ۲ ریال عمان», stationery, a key cut | Under «لوازم و تعمیرات خانه» (Home supplies & repairs), not «خانواده و هدایا»; books kept from before get the category once |
| AU4 | What categories there are | «چه دسته‌بندی‌هایی داری؟» (even typed «چه دسته بندی هوایی داری؟») | The categories by name, spending and income, at once; «هفته گذشته خرج ما چیا بوده و توی چه دسته‌بندی‌هایی؟» is still the week's spending |
| AU5 | Baisa always written | Any Omani-rial amount in a reply | «۲٫۰۰۰ ریال عمان», «۷٫۴۵۰ ریال عمان», never «۲ ریال عمان» |
| AU6 | The card names its currency | A week's spending in Omani rials; a table in rials and Omani rials | «OMR» (or «ریال عمان») once above the rows; each row names its currency when there are two |
| AU7 | The card's date in order | A Persian card «دریافت ۳۰۰ ریال عمانی قرض از امیر، در کیف پول قشنگم» dated ۲۹ مهر ۱۴۰۵, in an English app | «…قشنگم، ۲۹ مهر ۱۴۰۵», not «۲۹۱۴۰۵ مهر» |
| AU8 | Nothing behind the composer | Scroll the conversation; tap a choice | The messages pass under the composer, not through it; the newest message is above it, whole |

## AV. A person's AI budget is their books' (2026-10-22)

The owner's phone said «Today's AI allowance is used up» at 2.1M tokens, while the console showed «2.1M / 10M *». The platform's 2M per user stopped them; the books' 10M was far off; a person's account can't open Settings to change it, and the console only showed and set the company's.

| ID | Scenario | Steps | Expected |
|----|----------|-------|----------|
| AV1 | One person, one budget | Personal books with no per-user budget set; 2.1M tokens used today; the books' budget 10M | The chat answers: the person's budget is the books' 10M, not the platform's 2M per user |
| AV2 | A company's users still have theirs | A company with no per-user budget; one user at 2.1M | That user is refused at the platform's 2M; a colleague is not |
| AV3 | The console says which limit | Companies → AI · 24 h | «2.1M / 10M *» (* set for this company, explained under the table); a company whose busiest user is near a tighter per-user budget shows a second line «per user 2.1M / 2M» |
| AV4 | Set both in the console | AI budget on a company | It asks for the company's budget, then each user's; on a person's books only the first |

## AW. A lean conversation (2026-10-22)

Each chat message sent the model ~11k tokens of instructions, ~13k of tool definitions and the whole thread — every earlier tool result's JSON included — on each of its two or three calls. A long thread cost 50–80k tokens a message; the owner's 2.1M a day was about thirty messages.

| ID | Scenario | Steps | Expected |
|----|----------|-------|----------|
| AW1 | The last exchanges as they were | A thread of many messages; a new message | The last two exchanges go to the model whole: tool calls, results, answers |
| AW2 | Older ones as what was said | The same | Each older exchange is the user's words, the assistant's last answer and one line per card with what became of it («… — executed (card …)»); no tool results |
| AW3 | The oldest left out | More than 30 older exchanges | They are left out, with a note that the books keep what was recorded |
| AW4 | Half or less | A 20-message thread of spending questions and cards | What goes back to the model is under half of the whole thread |
| AW5 | The same instructions every call | Messages on two days, in two languages | The instructions are identical up to «Today:» at their end; the date and the language come last, so a provider's prompt cache serves the rest |
| AW6 | A cached prompt counts as it costs | 1M prompt tokens of which 800k came from the cache | The budget counts 400k |
| AW7 | A card can still be changed | «change that card to 50» about a card from earlier in the thread | Its line still carries its card id |

## AX. Only the tools and instructions a message needs (2026-10-22)

Every call sent all the tools (34 in a person's books, 59 in a company's) and all the instructions, whatever the message. Now the core goes, plus the groups the message and the exchanges just before it are about; the model can add any other group with ``more_tools``.

| ID | Scenario | Steps | Expected |
|----|----------|-------|----------|
| AX1 | A lunch from a wallet | «امروز ۷ ریال عمان ناهار خوردم از کیف پول قشنگم» in a person's books | The core tools and the money tools, not budgets, statements or payroll; the instructions without invoices, payroll, equity or statements |
| AX2 | An answer carries on | After a turn that looked up people's balances, the answer «کیف پول قشنگم» | The people and money tools are still offered |
| AX3 | A tool it wasn't offered | A message whose words name no group, then the model needs a budget | It calls ``more_tools`` with «planning», and has the budget tools on its next call, in the same message |
| AX4 | The same start every time | Two different messages | The tools start with the same core in the same order; ``more_tools`` is last and always the same |
| AX5 | Nothing lost | The eval set on the real model, before and after | The pass rate holds; the cost per message falls |

## AY. What the eval set found the assistant getting wrong (2026-10-22)

The eval set with a person's books (#403) found these failing whether tool routing was on or off. Five runs each on gpt-4.1-mini.

| ID | Scenario | Steps | Expected |
|----|----------|-------|----------|
| AY1 | The last few days | «در ۱۰ روز گذشته چقدر خرج کردم و توی چه دسته‌هایی؟» | ``get_spending_summary`` with ``days`` 10: from nine days ago to today, labelled «۱۰ روز گذشته (… تا …)», each currency on its own. It used to say the period can't be asked for |
| AY2 | Categories for spending | «چه دسته‌بندی‌هایی برای خرج داری؟», "what spending categories do I have?" | In the app, answered from the books without the model: the categories, spending and income. «هفته گذشته توی چه دسته‌هایی خرج کردم؟» is still a spending question |
| AY3 | The places and categories are known | A person's books: wallets «کیف پول سبز» and «کیف پول قشنگم» in Omani rials, «حساب ملت» in rials | The instructions list each place with its currency, the categories by code and name, and the people. "I paid 3.5 OMR for a taxi from my green wallet" gives one card, Dr 6130 / Cr 112001, without asking which wallet |
| AY4 | Things for the home | «دو تا مبدل برق خریدم ۲ ریال عمان از کیف پول سبز» | One card: Dr 6125 «لوازم و تعمیرات خانه» / Cr 112001, 2.000 OMR. Searching «مبدل برق», "power adapter" or "home supplies" finds 6125 first. It found the electricity bill on «برق»: the longest phrase now wins, and «قبض برق» is still the bill |
| AY5 | Someone new on the list | After the Muscat list, «یکیش رو امیر بهم نقدی داده بقیه از حساب امین پرداخت شده» | Amin isn't in the books: a card for each Airbnb line, Dr «سفر» / Cr 2130, with Amin to be added. No question about whether to add him. «امیر» named in the same message isn't offered as Amin, though the names are one letter apart |
| AY6 | The eval's own books | The test statement covers 20 days to 2 days ago | It shows every movement of the bank account in that time: Aria's receipt, the rent and the wages as well as the fee. The fee is the one difference, so the review starts with it. The payroll question asks about Sara's last pay run, which is right whatever the date |

## AZ. Money changed from one currency to another, and the owner's phone on 2026-10-22

From the owner's phone (IMG_1201–1208): «کیف پول والای» stood at −389.935 OMR beside $2,400. «۱۹۰۰ دلار از کیف پول وال ای چنج کردم که تبدیل شد به ۷۰۲.۲ ریال عمان» had no card: there was nothing to record an exchange with, and the instructions said money is never converted. The dollars stayed and the rials never arrived, so the gym, the gym clothes and a transfer took the wallet below zero in Omani rials.

| ID | Scenario | Steps | Expected |
|----|----------|-------|----------|
| AZ1 | An exchange | «۱۹۰۰ دلار از کیف پول والای چنج کردم شد ۷۰۲.۲ ریال عمان» | One card «تبدیل ارز»: 1,900 USD out of «کیف پول والای», 702.200 OMR into it, the rate as information only. Confirmed, it posts two entries through «تبدیل ارز» (3120, net worth), one in each currency, nothing converted. The wallet shows −1,900 USD and +702.200 OMR. Spending doesn't move. Net worth per currency shows USD −1,900 and OMR +702.200. Undo takes both entries back |
| AZ2 | An exchange among payments | The gym message: 300 from one wallet, 1,900 USD changed to 702.2 OMR of which 200 paid, 160 from the drawer | Two cards: the exchange, and the 660 OMR gym fee from three places. The gym card's 200 OMR from «کیف پول والای» is fine, because the exchange card brings the Omani rials |
| AZ3 | Money out of a place in a currency it doesn't hold | «۷.۱ ریال از کیف پول قشنگم» (a wallet of Omani rials only), and 200 OMR from a wallet of dollars and dirhams with no exchange | The first is carded as 7.100 OMR: «ریال» from that wallet is Omani rials, never 7 IRR. The second is refused, saying what the wallet holds, so the exchange is recorded first or another place chosen. Money coming into a place may be in any currency |
| AZ4 | Two questions in one | «چقدر پول داریم کلا و چقدر بدهی داریم» | Not the quick money answer alone: the model answers both, money in each currency and what is owed person by person |
| AZ5 | The briefing in the language of the chat | Phone in English, chat in Persian; a $2,000 starting balance on a Friday | The briefing is in Persian. A person's books don't run the company audit checks: round amounts on a weekend, duplicate supplier payments, payments split under an approval limit, a large first supplier payment |
| AZ6 | A card with no amount | «اسم کیف پول سبز رو عوض کن به کیف پول قشنگم» on the phone | The rename card shows «کیف پول سبز ← کیف پول قشنگم» with no figure. It used to show «Voucher 0» (next APK) |

## BA. The owner's phone after the exchange (2026-10-22, IMG_1209–1211)

The exchange put «کیف پول والای» right (312.265 OMR, $500, AED 1,785), and «چقدر پول داریم کلا و چقدر بدهی داریم» was answered in both halves. Two things were still wrong.

| ID | Scenario | Steps | Expected |
|----|----------|-------|----------|
| BA1 | No total across currencies | A person's money in Omani rials, dollars and dirhams; «چقدر پول داریم کلا» | Each currency on its own: 454.160 OMR, 4,500 USD, 1,785 AED. The tool no longer gives a rial total. The reply said «۴٬۵۰۰٬۰۰۰٬۰۰۰ ریال… ۲۷ مورد ارزی هنوز نرخ تبدیل ندارند»: the dollars were valued at some rial rate, the Omani rials and dirhams at none |
| BA2 | Moving entries to another category | «این سه تا خرید رو اشتباه زیر «خانواده و هدایا» ثبت کردی، ببرشون زیر «لوازم و تعمیرات خانه»: …» (2 + 3 + 1 OMR) | One correcting card: Dr «لوازم و تعمیرات خانه» / Cr «خانواده و هدایا», 6.000 OMR, no wallet on it. It said it needed the entries' ids, and made a reminder card instead |

## BB. Dates, places and the review card (owner's phone, 2026-10-25, IMG_1214–1219)

| ID | Scenario | Steps | Expected |
|----|----------|-------|----------|
| BB1 | A place named for one thing only | Earlier, «۳۳ ریال از لولو مارکت خرید کردیم» (unanswered); then «هرچی توی پاکت توی کشو بود … ۱۵.۳ ریال آورد که میکنمش توی کیف پول سبزم» | The Lulu card keeps the place the assistant chose for it. A place named in a message moves only the cards of the amounts that message states. A bare answer («کیف پول قشنگم») still moves any card. The dinner and Lulu cards had been moved onto the envelope while the reply named the wallet |
| BB2 | Everything in a place | «هرچی توی کیف پول قشنگم بود دادم به همسرم، … ۹.۷۵ ریال عمان برگردوند …» (the wallet holds 39.750) | The balance is read. Cards: 30.000 spent under its category and 9.750 back to «کیف پول سبز», so the wallet ends at zero. A message with nothing named asks once what the rest was |
| BB3 | The review card's rows | A Persian category in an English app | The name starts where the row starts, with room before its amount. It read «۶۶۰.000اشتراک‌ها» (next APK) |
| BB4 | A wrong date, fixed from the chat | «اون Muscat Airbnb که امیر حساب کرد مال ۱۵ شهریور ۱۴۰۵ بود نه این هفته، تاریخش رو درست کن» | ``propose_change_date``: one card naming the entry and its new date. Confirmed, only the date changes; the closed-period lock and the audit trail hold as for an edit on the web. Undo puts the old dates back. A future date or a closed period is refused. The owner's nine trips were dated the day they were told, and the weekly review counted $4,555.06 of travel "this week" |
