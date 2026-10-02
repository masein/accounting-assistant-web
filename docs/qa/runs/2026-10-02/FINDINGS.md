# Deep test findings, 2026-10-02

Each fix PR marks its rows here (Status: the PR that fixed it, or *question* when it waits on a decision).

Severity:
- **P1**: wrong data or money, safety risk, or a broken main flow.
- **P2**: wrong language or format, or a confusing main screen.
- **P3**: polish.

| # | Sev | Where | Finding | Seen in | Status |
|---|---|---|---|---|---|
| 1 | P2 | Sign-in page | A Persian browser gets the English sign-in page. With nothing saved it falls back to `en` instead of the browser's language, so the error "Invalid username or password" is English too | A6 | fixed #264 |
| 2 | P2 | First sign-in | An owner created by the super-admin has `preferred_language = en`, even for an `ir` company, so a new Iranian owner lands in English | A2 | fixed #264 |
| 3 | P2 | Password rules | "Password must be at least 8 characters" (and the other three rules) are English: `ValueError` text is passed through and never reaches the catalogue | A4 | fixed #261 |
| 4 | P1 | Super-admin | `GET /admin/company-profile` → 500 (no company context: tries to insert a profile with `company_id NULL`). `get_logo` with no company could serve another tenant's logo | A1 | not an app bug: the QA seed made a super-admin with no company (the real seed gives it the default company) |
| 5 | P3 | Companies console | One logo request per row → a 404 for every company without a logo | A1 | fixed #271 |
| 6 | P2 | Companies console | Region column shows raw `uk` / `ir`; no column for the kind (business / personal); the header "هوش مصنوعی · ۲۴ ساعت" reads as "240 hours" (the middle dot looks like the Persian zero) | A1 | fixed #271 |
| 7 | P3 | Sidebar | Section labels are 10.24 px, small for Persian script | all | fixed #270 |
| 8 | P1 | Parties | A party code typed in Persian digits is stored as «۱۰۱» (not 0–9), so lookups and exports by code miss it. #246 didn't cover `entities.code` | B3 | fixed #263 |
| 9 | P1 | Inventory | A barcode typed in Persian digits is stored as «۶۲۹۱…»; a scanner sends 0–9, so a scan never matches | B4 | fixed #263 |
| 10 | P2 | Parties form | About 15 inputs in "billing details" have no accessible name (labels not tied to their inputs) | B3 | not reproduced: every billing input has its label (checked on main by labels and by text); my QA check misread it |
| 11 | P1 | Parties page | **"Reset database" button** under the parties list (all roles see it; owner-only on the server). It deletes every transaction after one confirm, and sends no locale, so `/admin/reset-db` defaults to **`ir`**: a UK company reset from here would get the Iranian chart | B3 | fixed #259 |
| 12 | P3 | Chart of accounts | 3–4 action buttons on every row (noisy); the opening-balance grid is cramped in a half-width panel, with names wrapping | B1 | rows fixed #277 (add sub-account on the row, the rest under ⋯); the opening grid's width stays |
| 13 | P2 | Voucher save | The confirmation is English ("Date: … Currency: … Debit entries: … Total:"), the date is Gregorian in a Jalali company, it shows codes without names, and its digits follow the browser locale (`toLocaleString`) | C1 | fixed #261 |
| 14 | P1 | Invoices | Due date from a client's terms is a day early in Tehran (fixed by #258) | C3 | fixed #258 |
| 15 | P1? | VAT rates | The Iranian standard VAT in the rate table is 9%; it was raised to 10% from 1 Farvardin 1403 (budget law). **Needs the user's confirmation** | C3 | fixed #279 (owner confirmed 10%) |
| 16 | P2 | Invoices list | Type and status shown as raw `sales` / `issued` / `paid` | C3–C7 | fixed #261 |
| 17 | P2 | Invoice form | Party dropdown shows "client: شرکت پارس‌افزار" | C6 | fixed #261 |
| 18 | P3 | Invoice form | Totals use Persian digits ("IRR ۰") while the list uses 0–9; the tax-code select is cut off | C6 | fixed #273 |
| 19 | P3 | Invoices list | Number wraps ("ARM-" / "1002"); the amount cell shows both an `IRR` badge and "ریال"; up to 8 action buttons per row, on 3 lines | C6 | fixed #277 |
| 20 | P2 | Invoices page | An Iranian company sees the UK MTD/VAT sections (and presumably a UK company sees Moadian/TTMS) | C3 | fixed #266 |
| 21 | P2 | Cheques | "A cheque with Sayad id … is already recorded." is English | C9 | fixed #261 |
| 22 | P2 | Bank party | Creating a bank party auto-creates an account named "bank account — بانک ملت" (English in the stored name) and silently replaces the code the user typed (۳۰۱ → 1111) | C1, C11 | name fixed #262; the code fixed #276 |
| 23 | P2 | Recurring rules | The bank list only offers bank-party accounts. A bank account made in the chart (111001) can't be chosen | C11 | fixed #269 |
| 24 | P2 | Ledger summary | "IRR 0 بد": "بد" means "bad"; it should read بدهکار | C1 | fixed #261 |
| 25 | P3 | Ledger summary | 1110 takes postings and also has a sub-account, so it shows twice; the charts' axes show bare codes without names | C1 | axis names fixed #277; 1110 beside its sub-account is right (it has postings of its own) |
| 26 | P2 | Bank statements | The list's status column shows raw `parsed` | C12 | fixed #261 |
| 27 | P3 | Bank statements | After an upload the rows aren't opened; the user has to find "View" | C12 | fixed #274 |
| 28 | P3 | Budgets | The category is a typed code; the table shows only the code (6112), not the account name | C21 | fixed #274 |
| 29 | P2 | Reports | General ledger and trial balance previews: "Debit Turnover / Credit Turnover / Debit Balance / Credit Balance" in English | D2 | fixed #261 |
| 30 | P2 | Trial balance | Asked with only `to_date` (everything up to a date), it returns **0 rows** | D2 | fixed #265 |
| 31 | P2 | Dates in messages | The lock status ("قفل تا 2026-09-22") and the server's refusal ("دوره تا 2026-09-22 بسته است…") show Gregorian dates in a Jalali company | D6 | fixed #267 |
| 32 | P2? | AI chat | A CSV statement attached in the chat fails with "AI unavailable"; parsing a CSV doesn't need AI (to check: is the deterministic intake bypassed?) | E2 | fixed #268 |
| 33 | P1 | (confirms #11) | A UK owner's "Reset database" on the parties page gave the company the **Iranian chart** (36 Persian-named accounts) | G0 | fixed #259 |
| 34 | P3 | UK invoice form | Totals show "3,600 GBP" rather than "£3,600" (the list uses the symbol) | G1 | fixed #273 |
| 35 | **P1** | **Book language** | Every system-generated journal description is English in an Iranian company's books: "Invoice ARM-1805 issued / — revenue / — output VAT", "Payment for invoice …", "Opening balance", "Dividend declared — …", "Mileage claim — …", "Time billing — … (2026-10-01 → …)" (with Gregorian dates). About 246 English f-strings across the services. These are stored text that shows in the journal, ledger, statements and PDFs | dashboard, journal | fixed #262 |
| 36 | P2 | Dashboard | Overloaded: about 12 sections over 4,400 px (KPIs, a 13-week table, a 13-row explorer repeating it, what-if, aging ×2, spending ×2, profitability, health, owner pack, missing references, budgets, exports) | I | fixed #281 (owner approved: KPIs, alerts and what changed on top; four tabs — Cash, Receivables & payables, Spending & profit, Books; 700–1,300 px a tab) |
| 37 | P2 | Dashboard forecast | The week column cuts dates ("1405/07/0"); in en/ar they wrap | I | fixed #272 |
| 38 | P2 | Owner pack | Shown as a monospace text block with a Gregorian date "(2026-10-02)" in a Jalali company | I | fixed #267 |
| 39 | P3 | Ledger (tablet) | The page scrolls sideways at 768 px | I | fixed #275 |
| 40 | P3 | Jalali date field | The hidden native input is exposed to screen readers (no name, not `aria-hidden`) | I | fixed #270 |
| 41 | P3 | Phone | Tap targets under 28 px: checkboxes, the chat's quick chips, the CFO check buttons, the invoice line "×", PDF, the password eye | I | fixed #270 |
| 42 | P3 | Gregorian date fields | In es/ar the native date and month fields show the browser's "mm/dd/yyyy" | I | not a bug: a native date field follows the browser's own locale (a Spanish browser shows dd/mm/aaaa); the QA browser was en-US |
| 43 | P2 | AI chat | A CSV/XLSX bank statement attached in the chat goes to the AI (and fails with none set up). The no-AI statement path only runs for PDFs and images, which need AI to read anyway | E2 | fixed #268 |
| 44 | P3? | Credit notes | Disabled on a fully paid invoice (a refund case). After a credit note an invoice reads "partially paid". **Design question for the user** | C7 | fixed #280 (owner: best practice — a credit note goes against any issued invoice up to what is left to credit; its excess becomes the party's credit, refunded or used; a fully credited invoice reads "credited") |
| 45 | P2 | Report previews | The general journal (and every table preview) printed its dates as 2026-09-30 in a Jalali company; a report with only an end date showed it raw too | journal, while fixing #35 | fixed #261 |
| 46 | P3 | Manager reports | The "No currency filter selected… Pick a currency" banner was English (multi-line template text the static scan doesn't see) | code reading | fixed #261 |
| 47 | P2 | Bank statements | The statement detail's title was "Unknown — file.csv (12 rows)": English "rows", and "Unknown" for an unnamed bank | while fixing #27 | fixed #274 |
| 48 | P2 | Invoice history | The history showed a raw ISO timestamp in UTC and English sentences ("Credit note 600 GBP.", "Payment in") in every language | while fixing #44 | fixed #280 |
| 49 | P2 | Invoice edit | Saving a partly paid invoice sent an empty status (the select had no "partially paid" option), and the save was refused | while fixing #44 | fixed #280 |
