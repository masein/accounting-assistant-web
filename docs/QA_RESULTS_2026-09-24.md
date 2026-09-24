# QA results — production run, 2026-09-24

Target: `https://accounting.netixsystem.com` (release 2026.09.22, assets `d2af8974`).
Tenants: `QA Test Co` (Iran locale, IRR, users qa_owner / qa_accountant / qa_viewer /
qa_employee) and `QA Personal` (personal), created for the run via the Companies
console. Method: driven from the in-app browser pane at phone width (377 px) plus the
app's own API from the page context; the plan is `docs/QA_PLAN.md`.

**112 checks logged · 3 high · 15 medium · 19 low · rest pass.**

## Fix status (updated 2026-09-24, same day)

| finding | status | PR |
|---|---|---|
| default admin password active + advertised | fixed — default-password logins are locked to a change-password screen; hint removed | #89 |
| statement matching ignores the bank's own account | fixed — bank account resolved per statement for matching, missing-in-bank and posting | #88 |
| closed period only blocked creates | fixed — edits and deletes refused inside the lock | #87 |
| voucher live balance bar dead | fixed | #90 |
| viewer receives bank details | fixed — bank fields stripped for roles without bank:read | #92 |
| phone layouts (dashboard table, chat page) | fixed | #93 |
| chat: bank name from merchant rows / English reply to Persian / mismatched safety-net card | fixed | #94 |
| personal net worth ignores a holding | fixed | #95 |
| invoice over-payment accepted | **reclassified: by design** — the excess is booked to customer credit / supplier advance (see `tests/test_ar_ap_payments.py`); the only issue is that `amount_paid` shows the raw sum. Kept as LOW (display) | — |
| AI provider config editable per company / persisted per tenant | fixed — one platform-wide row, super-admin only; Settings → AI providers hidden for company roles | #97 |
| multi-currency face-value sums | fixed — ledger summary, account detail, trial balance and owner dashboard show one currency (the reporting currency by default) and list the others present as separate views; amounts are never summed across currencies | #98 |
| balance sheet imbalance | fixed — balances keep their sign (overdrawn cash shows negative), unclosed profit/loss to date is an equity line, `totals.liabilities_and_equity` + a balanced/not-balanced check in the UI; Iran statement no longer clamps buckets | #99 |
| personal chat: "این ماه چقدر خرج کردم؟" wrong + Jalali date hallucinated | fixed — new `get_spending_summary` tool resolves period words (this/last month, year, week) in the company calendar and returns the total, categories and period labels; prompt forbids the model from converting dates itself and gives today as «۲ مهر ۱۴۰۵» | #100 |
| validation lows: duplicate entity name (2.16), invalid IBAN (2.15), duplicate invoice number (3.1), zero budget (3.26), 25 h day (3.11), petty cash spend above the float (3.25), year-summary / my-summary 422 without params (3.22, 3.11) | fixed — 409 on a same-type duplicate party unless `allow_duplicate`, IBAN structure + mod-97 check (422), 409 on a reused invoice number per kind, budgets must be > 0, 24 h cap per entry and per day, petty-cash expenses limited to the float net of pending, summaries default to the current year / month | #101 |
| remaining lows (audit log edits, dashboard alert noise, statement row order + duplicate label, amount_paid display, Excel re-upload warning, budget link page, employee dropdown, CFO runway wording) | open | — |


## Findings to fix, in priority order

### High
1. **Default super-admin credentials live on production.** `admin / admin` logs in as the
   platform super-admin and the login page prints the hint. Rotate the password, remove
   the hint, and consider forcing a password change on first super-admin login.
2. **Statement reconciliation ignores the statement's own bank account.** Matching,
   "missing in bank" and the page-side Post all use the chart cash account (1110); a
   statement for a bank with its own GL account (1111) reports already-posted rows as
   unrecorded, lists every petty-cash voucher as missing, and page vs chat post the cash
   leg to different accounts. Resolve the bank account once per statement (entity by
   name → code) and use it for matching, missing-entry detection and posting.
3. **Closed period only blocks creates.** PATCH and DELETE of a voucher dated inside the
   closed period succeed; edits/deletes must go through `assert_period_open` too.

### Medium
- Multi-currency amounts summed at face value in the default ledger summary, trial
  balance and dashboard cash (120 USD counted as 120 IRR with a rate on file).
- Balance sheet does not balance: negative asset balances shown as 0, period profit
  missing from equity (Iran BS metadata also flags it).
- Invoice over-payment accepted (8,000,000 paid on a 3,000,000 invoice).
- Voucher form live balance bar never updates (`#lines-body` vs `#lines-tbody`).
- Phone layout: owner dashboard 13-week table overflows horizontally; AI Chat page
  squeezes the message pane and quick-action chips beside the sessions sidebar.
- Chat: "paid … from QA Bank Mellat" posted to generic cash without resolving the bank;
  statement drop guessed the bank name from a merchant line ("Refah") and answered a
  Persian message in English; the step-by-step safety net attached a card the reply was
  not about (reply discussed a missing-in-bank entry).
- Personal: "این ماه چقدر خرج کردم؟" answered "nothing recorded" right after posting
  500,000 and dated today as 24 Mehr (it is 2 Mehr).
- Personal net worth ignores a saved gold holding even with a rate on file.
- Viewer role receives full entity records (bank account numbers, IBANs) — the
  "strip sensitive fields for viewer" intent is not implemented.
- AI provider config is one runtime state for all companies but persisted per tenant; a
  switch made in one company applies to all until restart, then whichever row loads
  first wins.

### Low
Duplicate entity names and invoice numbers accepted; IBAN not validated; 0-limit
budgets and 25 h/day time entries accepted; edits missing from the audit log (versions
are kept); budget notifications link to My finances for business tenants; dashboard
alerts fire on trivial data; same-day statement rows ordered by id; same-statement
duplicate labelled "Imported before" in the panel; user-management employee dropdown
filled only at login; CFO "runway −0.6 months" wording; `/payroll/year-summary` and
`/time/my-summary` 422 without params; personal "نقدی" posted to the bank account
instead of cash on hand; Persian lunch expense asked for an account instead of using a
general expense; petty-cash expense far above the float accepted as pending;
re-uploading the same Excel journal shows no warning.

## Not covered in this run
Statement drop inside the personal tenant (same code path as SME), SMS/Slack/Telegram
digest delivery, mail test-send, migration chart-export upload, inventory reports,
manager-report PDF exports through the UI, the UK-locale tenant, and load/performance.

## Full log

| id | check | result | note |
|---|---|---|---|
| 0.1 | /health | P | 200 in 0.6 s |
| 0.3 | cache-busted assets | P | index → login redirect when logged out; js loads with ?v= (checked in pane) |
| 1.1 | default admin credentials | **F (HIGH)** | `admin / admin` logs in as platform super-admin on production; hint printed on the login page |
| 1.10 | self-signup disabled | P | POST /auth/signup → 403 "Self-signup is disabled on this server." |
| 0.4 | test tenants | P | created QA Test Co (ir, IRR, qa_owner) and QA Personal (personal, IRR, qa_personal) via Companies console |
| 1.21 | bell feed (admin@Default) | P | 4 invoice_overdue items, read state toggles |
| 1.3 | nav for super-admin | P (info) | super-admin sees every page incl. My finances by design (canSeePage bypass); re-check with qa_owner |
| 1.2 | qa_owner landing + nav | P | lands on #dashboard; nav hides My finances and Companies; badge shows QA Test Co |
| 1.16 | new user sees no tour | P | qa_owner: whats_new.seen=true on first login (stamped with current release) |
| 1.14 | Settings → What's new reopens history | P | /auth/whats-new → 1 release, 6 highlights; modal populated ("Update 2026.09.22 · 1 of 6", Back hidden on step 1, Show me visible) |
| 1.19 | phone width, no horizontal scroll | **F (MED)** | dashboard at 377px: page scrollWidth 588 — the 13-week forecast `.mini-table` inside its panel overflows (right edge 574px) |
| 1.11-1.13 | tour steps (via Settings reopen) | P | 6 steps in order, Back hidden on 1, "Got it" on 6, Show me closes and opens the page, dots track position |
| 1.18 | language switch en→fa→es→ar→en | P | dir flips rtl for fa/ar, html lang set, nav + page title translated, 0 raw i18n keys in data-i18n elements |
| 1.20 | hash navigation + back | P | #entities activates Entities and loads; history.back returns to dashboard |
| 1.21 | bell on empty company | P | 0 items, badge hidden |
| 2.1-2.3 | voucher API validation | P | balanced 201; unbalanced 400 with amounts; single line 422; debit+credit on one line 422; future 400; unknown code 400; UK code on IR chart 400; zero 400 |
| 2.9 | Jalali hint | P | today shown as 1405/07/02 |
| 2.10 | edit / soft delete | P / **F (LOW)** | PATCH 200 + version stored; DELETE 204 → 404; ledger excludes. But the audit log shows no `update` action for the edit (only create/delete) — verify in code |
| 2.5 / 2.26 | multi-currency in default views | **F (MED)** | USD voucher (120 USD) counted at face value: ledger-summary default 6112 = 1,120 (1,000 IRR + 120 USD); dashboard cash −1,120 IRR although a USD→IRR rate (150,000) exists. Per-currency filters are correct |
| 6.1 | dashboard alerts on trivial data | F (LOW) | fresh company with 3 tiny vouchers shows "Cash runway is short", "Book quality risk", "Expense spike" — no minimum activity threshold |
| 2.1 (UI) | voucher via the form | P | confirm dialog summarises Dr/Cr, save → "Voucher saved. Ledger updated.", form reset, voucher listed |
| 2.15 | create client/bank/supplier/employee | P | bank gets its own GL account 1111 |
| 2.16 | duplicate entity name (same type) | F (LOW) | second "QA Client Acme" client accepted silently (201) |
| 2.15 | invalid IBAN | F (LOW) | employee with iban "not-an-iban" accepted (201), no validation |
| 2.17 | bank statement columns | P | deposit → Creditor 5,000,000 / Remaining 5,000,000; payment → Debtor 1,200,000 / Remaining 3,800,000; control 1111 |
| 2.17 | supplier statement, paid from a bank with its own code | **F (MED)** | Dr 6112 / Cr 1111 linked to the supplier shows blank columns (unplaced): the cash fallback only recognises 1110, not bank entities' own accounts (111x) |
| 2.7 | attachments | P | png 201; txt 400 unsupported; PNG bytes labelled pdf → 400 MIME spoof; empty → 400 |
| 2.7 | oversize attachment | P | 9 MB PNG → 400 "Attachment too large. Max size is 8 MB." |
| 2.1 (UI) | live balance bar | **F (MED)** | typing 700,000 / 600,000 into the lines leaves the bar at "Debit: 0 · Credit: 0 · Balanced ✓" (also after blur/change); only the confirm dialog computes totals |
| 2.27 | closed period blocks new postings | P | PUT /admin/closed-period {closed_period} ; post dated inside → 422 with clear message; on the closed-through date → 422; day after → 201; reopen works |
| 2.27 | closed period blocks edits/deletes | **F (HIGH)** | PATCH (200) and DELETE (204) of a voucher dated inside the closed period both succeed — closed figures can be changed |
| 2.17 (UI) | entity view headers | P | Date · Reference · Ccy · Description · Attachments · Debtor · Creditor · Remaining · Actions; values as computed |
| 2.20 (UI) | Add transaction from entity view | P | editor titled "Add transaction with QA Bank Mellat", bank pre-linked, line 1 = 1111, date today; saved → row appears with running balance |
| 2.17 | same-day row order | F (LOW) | rows on the same date are ordered by id, not creation time — the new 250,000 row slotted between two earlier same-day rows, so the Remaining column's sequence is arbitrary within a day |
| 2.1 (UI) | balance bar root cause | — | `updateVoucherBalanceBar()` in 07-chat-reports.js queries `#lines-body tr` but the table body id is `lines-tbody` → totals always 0 |
| 2.24 | manager reports respond | P | trial balance, balance sheet, income statement, Iran statements (IS/BS/CF/equity), general journal, debtor-creditor, cash-bank statement, cash flow, tax summary → all 200 |
| 6.7 | API key create | P | POST /admin/api-keys → 201, key shown once (revoke after run) |
| 2.24 | trial balance vs ledger | F (MED, same root cause as 2.5) | trial balance turnover 7,151,820 vs IRR ledger 7,151,700: the 120 USD voucher is summed at face value into the IRR trial balance |
| 2.24 | balance sheet balances | **F (MED)** | standard BS: assets 4,050,000 / liabilities 0 / equity 0 — account 1110 (actual balance −701,820) shows 0 and the period's net profit (3,348,180) is absent from equity; Iran BS metadata reports assets ≠ equity + liabilities |
| 6.7 | API key scope | P (info) | keys authenticate only the /api/v1 integration surface; other routes → 401. Bad key → 401 |
| 2.11 | Excel journal import preview → confirm | P | 2 vouchers detected, Title paths suggested to 6112/1110, Jalali 1405/07/01–02 → 2026-09-23/24, 2 imported |
| 2.12 | double confirm of the same upload | P | second confirm → 400 "Upload expired or not found" (token consumed) |
| 2.12 | re-uploading the same Excel file | F (LOW, not exercised to completion) | second preview shows no "already imported" warning; a second confirm would import duplicates |
| 4.1 | statement PDF upload (page path) | P | generated 1-page PDF → 8 rows, source ocr_pdf, 55 s |
| 4.4/4.5 | reconcile + Check against books (page) | P (works) / **F (HIGH)** | panel renders counts, balance line and per-finding Post buttons. BUT matching and "missing in bank" use the chart cash account 1110 instead of the statement bank's own account (1111): the 5,000,000 deposit already posted on 1111 came back "not in the books", and 11 petty-cash (1110) vouchers were listed as "in books, not on statement" for a Mellat statement |
| 4.8 | identical rows in one statement | P / F (LOW) | second Shatel row flagged duplicate, review category same_statement; the panel label still reads "Imported before" (summary line counts it as such) |
| 4.13 | same PDF dropped in chat | P | deterministic "already imported on … as 'qa-bank-statement.pdf'" card pointing at the first statement, 1 s |
| 4.12 | fresh statement dropped in chat (Persian message) | P / F (MED) | imported in 25 s with review card; but bank name guessed as "Refah" from a merchant line inside the PDF (so the review fell back to 1110), and the summary came back in English for a Persian message (uses UI language, not message language) |
| 4.6 | Post from a finding (page) | P / F (part of the HIGH above) | bank fee posted with the suggested 6210 and the row turned matched; but the cash leg went to 1110, not the statement bank's 1111 (the chat card for the same statement used 1111) — page and chat post to different bank accounts |
| 4.17 | chat "review step by step" | P / F (MED) | card raised (POS purchase, Dr 6112 / Cr 1111, bank date) and Confirm posted it; but the assistant's text discussed a *missing-in-bank* entry (amount 100) while the card was for an unrecorded row — the safety net attached a card the text wasn't about |
| 4.18 | "next" | P | moved to the next finding; no stale card |
| 4.19 | undo the chat-posted row | P | mode=deleted, transaction 404, statement row released (only the page-posted fee stays posted) |
| 5.2 | "we paid 350000 for office supplies from QA Bank Mellat" | P / F (MED) | one card, correct amount, but Cr 1110 (generic cash) — the named bank entity was never looked up (no find_entity call), so the entry misses the bank's own account 1111 |
| 5.3 | typed "confirm" with a pending card | P | deterministic pointer to the card, no duplicate |
| 5.9 | "how much cash do we have?" | F (LOW) | answered −44,231,820 from account 1110 only, calling it an overdraft; the 1111 bank balance (≈4 M) was ignored — should sum cash/bank accounts |
| 5.2 (fa) | "۲۰۰ هزار تومان ناهار نقدی دادیم" | F (LOW) | no card: model asked which expense account to use because no account named "ناهار" exists, instead of taking a general expense account |
| 5.10 | structuring request | P | one-sentence refusal, no plan, no tool |
| 5.8 | "add Acme Trading as a client" | P | one propose_create_entity card noting missing phone/address |
| 5.16 | "چه خبر؟" | P | get_insights called, Persian reply "steady" |
| 7.3 | CSRF | P | POST without X-CSRF-Token (raw XHR) → 403; wrong header → 403; correct header → 201. (An earlier '201 without header' was my probe reusing the app's wrapped fetch, which injects the header.) |
| 3.1 | invoice create / issue | P | sales invoice 3,000,000 to client, issued; AR posted |
| 3.1 | duplicate invoice number | F (LOW) | second invoice "QA-INV-1" accepted (201) |
| 3.2 | partial payment | P | 1,000,000 on 3,000,000 → 201 |
| 3.2 | overpayment | F (LOW, reclassified) | excess is intentionally booked to customer credit (2120) — see test_ar_ap_payments; but `amount_paid` reports the raw 8,000,000 instead of 3,000,000 settled + 5,000,000 credit |
| 3.3 | reverse payment, void | P | reverse 200; void → status voided |
| 3.7 | recurring rule → run due | P | monthly rule posted once (2026-09-01), second run posts nothing, next_run advanced to 2026-10-01 |
| 3.9 | installment plan | P | 3 × 3,000,000 pending; settle one posts and settle-twice → 400 |
| 3.10 | cheque issue → bounce | P | status bounced; summary payable 8,500,000 (bounced still owed) |
| 3.19-3.20 | payroll profile → run → post → pay | P | gross 30,000,000 / tax 3,000,000 / social 2,100,000 / net 24,900,000; pay before post 409; post twice 409; payslip JSON + PDF (25 KB) |
| 3.22 | payroll year summary | F (LOW) | GET /payroll/year-summary → 422 without a year param (check the UI passes it) |
| 3.23 | equity | P | 100% holding, 100,000,000 contribution to 1111, dividend 10,000,000 declared, current-account out; cap table consistent |
| 3.25 | petty cash account | P (by design) | holder must be a user (username/user_id) → 400 with a bare name |
| 3.26 | budgets | P / F (LOW) | 50,000,000 budget → 102% utilisation reported; but a 0 limit is accepted (201) |
| 3.14-3.15 | mileage claim | P | 120 km × 5,000 = 600,000, reimburse 200; approve on an already-approved claim → 409 |
| 3.11-3.12 | time entry, rate, invoice preview | P / F (LOW) | 6.5 h entry, rate 1,200,000, preview total includes 9% VAT; but 25 h in one day accepted without warning |
| 3.26 / 1.21 | budget notification | P / F (LOW) | "Budget exceeded" (high) appears in the bell, but link_page is `personal-dashboard` even for a business tenant — clicking it bounces the owner to the dashboard home |
| 3.25 | petty cash (holder = qa_owner) | P / F (LOW) | deposit ok, zero deposit 422, expense pending → approve → balance 4,250,000; an expense far above the float (99,000,000) is accepted as pending |
| 3.22 | payroll year summary | P (note) | requires ?year=; 2026 and 1405 both 200 |
| 3.16 | purchase order → receipt → match | P | PO 1,500,000 issued; receive 8/10 → partially_received; receiving 5 more → 422 "exceeds outstanding"; 3-way match with purchase invoice → matched; PO PDF 24 KB |
| 5.13 | insights with real data | P | GET /insights → "New employee on payroll: QA Employee Sara — monthly payroll will rise by about 30,000,000" (page payroll) |
| 6.5 | FX rates | P | add USD 1,000,000 → 201; negative rate → 422; list ordered by date |
| 6.5 | digest | P | preview (deliver=false) 200; settings 200 |
| 6.5 | AI provider on production | note | active Metis model is still gpt-4o-mini — switch to gpt-4.1-mini in Settings → AI providers (PR #85 default only affects fresh installs) |
| 6.2 | CFO report + Ask | P / F (LOW) | grade D, 10 KPIs, insights localized; "Cash runway critical: -0.6 months" — negative runway text reads oddly when cash is negative (should say "no runway / overdrawn") |
| 6.3 | CEO report | P | totals, trends, 2 alerts |
| 6.4 | audit report | P | duplicate-payment findings (the 100-IRR probe vouchers) and negative cash balance detected; integrity history 200; 57 audit rows |
| 6.5 | company profile save | P | PUT /admin/company-profile → legal name persisted |
| 6.7 | API key revoke | P | DELETE → 204 (QA key removed) |
| 6.5 | Settings → User management → Link to employee | F (LOW) | dropdown is filled once at login; employees created afterwards are missing until the next login (showed only "— none —" although 2 employees exist) |
| 6.5 | AI model switched | done | PATCH /admin/ai-config → provider metis, model gpt-4.1-mini (active) |
| 6.5 | AI config scope | F (MED, design) | runtime AI config is one module-level state shared by every company in the process, but it is persisted in the tenant-scoped app_settings row of whoever saved it; after a container restart the first row found wins. A switch made in one company applies to all until restart, then may revert |
| 1.2/7.1 | accountant role | P | lands on dashboard; nav hides CFO/CEO/Settings/Companies; deep link #cfo bounces to dashboard; API: cfo/ceo reports, ai-config, users, create user, closed period, api keys, digest, approvals → 403; companies → 404; books/payroll/statements/insights/audit/migration/voucher → allowed |
| 1.22 | bell kinds (accountant) | P | budget, petty_cash, insight visible |
| 1.2/7.1 | viewer role | P | lands on dashboard; nav = Dashboard / Ledger / Manager reports only; deep links to #payroll and #entities bounce; writes (voucher, entity, attachment), chat, sessions, statements, payroll, CFO, users → 403; dashboard, ledger, trial balance, insights, feed → 200 |
| 7.1 | viewer sees bank details | **F (MED)** | GET /entities (REPORTS_READ) returns full entity records to a viewer — bank account number 1234567890 and the employee IBAN are present; the "sensitive fields stripped downstream" intent is not implemented. GET /invoices also readable by viewer (probably intended) |
| 1.22 | bell (viewer) | P | empty — no kinds granted to viewer |
| 1.2/7.1 | employee role | P | lands on Time; nav = Time / Expenses / Petty cash; deep link #dashboard bounces to time; entities, ledger, dashboard, voucher, chat, invoices, statements, insights → 403 |
| 3.13 | employee payslips | P | /payroll/my-payslips returns own run; own payslip 200; another employee's payslip → 404; all runs → 403 |
| 3.11 | employee time | P | own entry 201; logging for another employee → 403 "You can only log your own time." |
| 3.14 | employee mileage | P | 40 km → 200,000, auto-approved (below the 20,000,000 threshold); self-approve endpoint → 403 |
| 3.11 | /time/my-summary | F (LOW) | 422 without parameters (UI probably passes a period) |
| A.8-1 / 3.29 | personal tenant landing + nav | P | lands on AI Chat; nav = My finances / AI Chat / Vouchers / Recurring / Installments & cheques / Bank statements; SME chips hidden, 4 personal chips shown; .sme-only elements hidden |
| 7.2 | tenant isolation (personal → QA Test Co ids) | P | transaction, entity, entity statement, statement, review, invoice payment, chat session, entity delete, voucher patch → 404; pay run → 403 |
| 7.1 | personal role API surface | P (info) | SME reads (invoices, cap table, time entries) answer 200 with empty data because the personal role carries books:read; pages are hidden in the UI |
| 1.19 | AI Chat page at phone width | **F (MED)** | sessions sidebar takes most of the width; the message pane is a narrow tall strip and the quick-action chips wrap one word per line ("How / much / did / I / spend") |
| A.8-2 | personal chat: "۵۰ هزار تومان نان نقدی خریدم" | P / F (LOW) | one card, 500,000 IRR (Toman ×10 stated), plain-language Persian reply with no debit/credit jargon; but "نقدی" (cash) was posted to 1110 "حساب بانکی" instead of 1120 "Cash on hand" |
| A.8-3 | personal chat: salary income | P | 30,000,000 Toman → 300,000,000 IRR, Dr bank / Cr salary income, Persian reply |
| A.8-7 (question) | "این ماه چقدر خرج کردم؟" right after posting 500,000 | **F (MED)** | answered "no spending recorded this month" and dated today as ۲۴ مهر ۱۴۰۵ (today is ۲ مهر): wrong figure and a hallucinated Jalali date |
| A.8-5 | personal budget | P | 400,000 limit on خوراک و سوپرمارکت → 125% → bell "Budget exceeded" (high) linking to My finances |
| A.8-7 | personal dashboard | P | KPIs (cash, spent this month 500,000, saved), What changed panel, no horizontal overflow at 377px |
| 3.27 | personal net worth with a gold holding | **F (MED)** | 10 GOLDG holding saved on 1120 and a GOLDG→IRR rate (70,000,000) added, yet net worth lists no holding, unrealized gain 0 and no missing-rate warning — the holding is not valued at all |
| 5.4 (personal) | undo the bread entry | P | mode deleted; cash KPI back to 0 |
