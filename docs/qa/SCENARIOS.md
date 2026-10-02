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
| B1 | Chart of accounts | Add 1110-01 «بانک ملت جاری» under 1110, rename it, try to delete a used account, deactivate an unused one | Tree updates; the code is suggested from its parent; deleting a used account is refused with the reason; inactive accounts leave the pickers |
| B2 | Opening balances | Enter cash 500,000,000; bank 1,200,000,000; capital 1,700,000,000 on the opening date (Jalali) | An unbalanced entry is refused; the balanced one posts; the trial balance shows them |
| B3 | Parties | Add 3 clients, 2 suppliers, a bank, 2 employees and 2 shareholders (codes and phones in Persian digits); edit one; delete an unused one | Lists show each type in Persian; search for «كافه» finds «کافه نارنج»; a duplicate name warns; Edit/Delete work; the bank's own account is named «حساب بانکی بانک ملت» [#262]; a code typed «۱۰۱» is stored and found as 101 [#263] |
| B4 | Products and stock | 3 products with SKU, unit and price; opening stock (IN) for 2 items; a barcode | Products page and inventory balance agree; valuation report in Persian; tables styled; a barcode or SKU typed in Persian digits is stored in 0–9 and a scan (0–9) finds the item [#263] |

## C. Daily bookkeeping

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| C1 | Manual voucher | Pick the date from the Jalali grid; rent 45,000,000 Dr 6xxx / Cr 1110-01 with a description; attach a receipt image; save | Unbalanced → Persian message; the save confirmation is Persian, with the Jalali date, each account's code and name, and amounts in the company's currency [#261]; balanced → "voucher saved"; it appears in the ledger with its Jalali date and attachment; an account code typed «۶۱۱۲» posts to 6112 and the confirmation names it [#263] |
| C2 | Voucher edit / delete | Link client and bank, edit the amount, then delete | Ledger and balances follow; the audit trail records each step in Persian |
| C3 | Sales invoice | Itemised, 2 lines, VAT, Jalali issue and due dates, client on «Net 30»; also in the evening, Tehran time [#258] | Due = issue + 30 on the user's own calendar day; the party list reads «name — مشتری» [#261]; totals right; it posts; Persian PDF (Jalali, Persian digits, amount in words); the tax-code list offers only Iran's codes (no UK_VAT_…) and the rate form suggests IR_VAT_STANDARD [#266] |
| C4 | Payments on it | Record a partial payment (typed «۲۰٬۰۰۰٬۰۰۰»), then the rest | Partially paid → paid, shown in Persian in the list (kind and status) [#261]; AR aging and the client's statement agree |
| C5 | Purchase bill and payment | Bill from پخش البرز, pay it | AP up then down; the supplier's statement agrees |
| C6 | Quote → invoice | Quote, mark sent and accepted, convert | The invoice carries the quote's lines; the quote shows "converted" |
| C7 | Credit note and void | Credit note on C3; void another invoice | Postings reversed; statuses in Persian |
| C8 | Recurring invoice | Monthly, Jalali calendar, auto-issue | Next date is Jalali; run due → invoice issued once, not twice |
| C9 | Cheques | Received cheque (Sayad ID) → deposit → clear; issued cheque → print preview; one bounces | Each step posts its entry; a bounced cheque is not settled; every refusal in Persian: a duplicate Sayad ID, depositing an issued cheque, returning one at the bank [#261]; cheque layout errors in Persian |
| C10 | Instalments | Loan of 12 instalments | Schedule with Jalali dates; the reminder appears in the bell |
| C11 | Recurring payment rule | Monthly rent, auto-post, run due | One voucher; next run date moves forward; the bank list offers every bank account: the banks' own accounts, the chart's bank account and the ones opened under it (111001), not a retired one [#269] |
| C12 | Bank statement import | Upload a 15-row CSV → map columns → categorise → approve; re-upload it | Rows posted; the list shows the status in Persian and the type in capitals; a row that can't post says why in Persian ("ردیف ۳: …"), and so does the summary after approving [#261]; reconciliation matches the existing rent voucher; the re-upload is flagged as a duplicate |
| C13 | Bank SMS | Paste 3 Persian SMS from Mellat | Parsed amount, date, type; posted or proposed |
| C14 | Petty cash | Float for علی; deposit «۵۰۰٬۰۰۰٬۰۰۰»; علی records an expense with a receipt; manager approves | Balance follows; app dialogs only; amounts in IRR; an expense over the float is refused in Persian [#261] |
| C15 | Expenses and mileage | Employee files a mileage claim → manager approves → reimbursed | Statuses in Persian; posting on approval and on reimbursement |
| C16 | Time | Project, rate, 3 billable entries → ready to invoice → invoice | The time is invoiced once; the preview range is Jalali |
| C17 | Purchase order | PO of 2 lines → partial receipt → bill → 3-way match | The order date defaults to today; a short receipt is flagged |
| C18 | Payroll | Profiles for علی (monthly) and مریم (hourly), 1405 rules; run Mehr → review → post → pay; payslip | Net = gross − withholdings; posting balanced; payslip in Persian |
| C19 | Fixed assets | Laptop and van; month-end depreciation; dispose of the laptop | Book value falls; disposal posts gain or loss |
| C20 | Equity | Cap table 60/40; contribution; declare and pay a dividend | Cap table 100%; the hint names IRR; statements show the movements |
| C21 | Budgets | Budgets for 3 expense accounts in Jalali months; overspend one | The alert fires; budget vs actual right |
| C22 | Multi-currency | USD rate; a USD bill; revaluation preview | Base values in IRR; realised gain or loss on payment |

## D. Reports, control and compliance

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| D1 | Ledger search | Filter by Jalali range, account, party; text search with Arabic letterforms | Results right; totals right; export works |
| D2 | Financial statements | Trial balance, balance sheet, P&L, cash flow (Iranian formats); PDF and XLSX | TB balances; A = L + E; P&L agrees with the vouchers; preview columns (turnover, balance) and every date in a preview (the general journal's too) in Persian and Jalali [#261]; Persian documents; a trial balance (or general ledger) asked with only an end date takes every posting up to it [#265] |
| D3 | Dashboard and manager reports | Dashboard KPIs and charts; sales by product and client; aging; inventory | Figures agree with C1–C22; charts labelled in Persian; Jalali months; the owner pack is a readable list (not monospace text) dated in the company's calendar [#267] |
| D4 | CEO / CFO | Both pages | KPIs, grade, runway; nothing English |
| D5 | Audit | Trail plus full audit | Every action of the run is in the trail; findings in Persian |
| D6 | Period lock | Lock through the end of Shahrivar; try a back-dated voucher | Refused, in Persian, naming the lock date; both dates in the refusal and the lock status itself («قفل تا 1405/06/31») are Jalali, in Persian and in English; a Gregorian company's stay Gregorian [#267] |
| D7 | Tax | TTMS season export; Moadian export of C3 | Files download; amounts agree with the invoices |
| D8 | Forecast and insights | 13-week forecast plus a what-if; insights after a duplicate payment | Forecast weeks in Jalali; the duplicate is flagged; on the dashboard each week's date stays on one line (it broke as «2026-09-» / «28») [#272] |
| D9 | The books' language [#262] | After C1–C22, open the general journal, an account's ledger and a party's statement; then do the same in Thames | Every description the app wrote itself (invoice, payment, bill, credit note, void, opening balance, equity, payroll, mileage, time billing, depreciation, petty cash, recurring, FX, fees, statement rows) reads in Persian in Arman, with Jalali dates inside the text; in Thames the same entries read in English; nothing already posted is rewritten |

## E. AI accountant and migration (no AI provider)

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| E1 | Chat without a provider | Ask a question | "The assistant can't answer right now" in Persian; nothing breaks |
| E2 | Statement intake in the chat | Attach the C12 CSV in the chat | The deterministic intake proposes rows without AI, or says what it needs; a CSV or Excel statement is imported and reviewed with no AI at all, even when its name and the message don't say "statement" (its header and dated rows do); a journal export (no running balance) is not taken for one; with no bank named, the reply and card don't say "Unknown" [#268] |
| E3 | Migration | Opening chart and balances from a Sepidar-style Excel; journal import CSV | Preview, map, confirm; balances agree; the closed period is respected |

## F. Roles

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| F1 | Employee | `arman_emp`: nav and direct URLs to invoices or settings | Only time, expenses and petty cash; other pages redirect to home |
| F2 | Viewer | `arman_view` tries to save anything | Read-only pages; any write refused in Persian (no blank failure) |
| F3 | Manager / CFO | `arman_mgr` approves claims; `arman_cfo` opens CEO/CFO | Exactly their permissions |

## G. UK company (English) and other languages

| ID | Scenario | Steps | Expected |
|---|---|---|---|
| G1 | UK bookkeeping | Opening balances; VAT 20% invoice; bill; VAT return boxes 1–9; FRS 102 statements; MTD ITSA quarter export | Box figures agree with the invoices; English PDFs; £ everywhere; nothing Iranian on its pages: no Moadian or TTMS panel, no Sayad id on cheques, only UK tax codes in the rate list and the invoice line, and the payroll rules show only the UK's figures [#266] |
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
| I1 | No horizontal page overflow; nothing clipped or overlapping; tables scroll inside their box on a phone; the browser suite checks every page at 768 px too, in English and Persian (the Ledger's toolbar was 49 px too wide) [#275] |
| I2 | Right-to-left mirrors correctly (icons, arrows, alignment, number and date runs); English stays left-to-right |
| I3 | No untranslated text, raw keys (`msgFoo`) or `{placeholders}`; consistent terms between pages |
| I4 | Every input and button has an accessible name; focus is visible; Tab order follows the reading order; dialogs close on Escape; a Jalali date field is one field to a screen reader (its hidden native input is aria-hidden) [#270] |
| I5 | Tap targets at least 32 px on a phone; the font is never under 11 px; checked in the browser suite on a phone in Persian: chips, small buttons, the invoice line's ×, checkboxes; the sidebar's section labels are at least 12 px, with no letter-spacing in Persian or Arabic [#270] |
| I6 | Every action gives feedback (success, error, loading); destructive actions confirm; disabled states are clear |
| I7 | Empty states say what to do next; loading states don't flash English or raw data |
| I8 | Money and dates follow the company's currency and calendar everywhere; Persian digit input works in every number field |
| I9 | No console errors, failed requests (4xx except expected 403) or slow pages (> 3 s to idle) |
| I10 | Visual consistency: one style for buttons, cards, tables and badges; spacing rhythm; heading hierarchy |

**Run order:** A → B → C → D → E → F → G → H; I runs across all of it. Results go in `runs/<date>/RESULTS.md`.
