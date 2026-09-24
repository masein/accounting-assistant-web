# Full QA plan — accounting assistant (release 2026.09.22)

A complete re-assessment of every feature, page by page, role by role. Each
check has an id, steps, and the expected result, so a run can be logged as
pass / fail / blocked with a one-line note. Run order: **Part 0 → 1 → 2 → 3
→ 4 → 5**; later parts assume the data created earlier.

Conventions
- `IR` = Iranian-locale company (Persian UI, Jalali dates, IRR); `UK` = UK chart (English, GBP).
- Roles: owner, cfo, accountant, manager, employee, viewer, personal, plus the platform super-admin.
- "Card" = the confirm-gated proposal card in the AI chat. Nothing the assistant proposes may reach the books without Confirm.
- Every mutating check should be repeated in **Persian UI** at least once (RTL layout, Jalali dates, Persian digits) — column P in the log.

---

## Part 0 — Environment & prerequisites

| id | check | expected |
|---|---|---|
| 0.1 | `GET /health` on the target | 200 |
| 0.2 | Container logs on boot | migrations up to **034**, seed done, no tracebacks |
| 0.3 | Static assets | every `js/*.js` and `css/app.css` loads with `?v=<hash>`; hard reload picks up the new release (no stale JS) |
| 0.4 | Test tenants | one `IR` business company (owner + one user per other role), one `UK` business company, one `personal` tenant; super-admin login |
| 0.5 | Test files at hand | Mellat PDF statement, a CSV statement, an Excel statement, a receipt image, an invoice PDF, a chart-of-accounts Excel export, a transactions Excel |
| 0.6 | AI configured | Settings → AI providers shows the active provider/model; chat replies (not a 502) |
| 0.7 | Mail | Settings → test-send arrives (if SMTP configured); otherwise UI says mail is off, signup verification not enforced |

---

## Part 1 — Authentication, onboarding, shell

### 1.A Login / session
| id | check | expected |
|---|---|---|
| 1.1 | Login with wrong password ×6 | 429 rate limit after the limit; clear message |
| 1.2 | Login as each of the 7 roles | lands on the role's home (owner/cfo/accountant/viewer → dashboard, manager → expenses, employee → time, personal → AI chat) |
| 1.3 | Deep link to a page the role can't see (`#cfo` as accountant) | falls back to role home; API 403 if called directly |
| 1.4 | Session expiry / logout | logout clears the cookie; protected API returns 401 afterwards |
| 1.5 | Deactivate a user (Settings → users) then their old session | requests fail (token_version bump) |
| 1.6 | Password change → old password | rejected; login works with new; hash upgraded (no error) |

### 1.B Self-signup & email verification (only if `ALLOW_SELF_SIGNUP=true`)
| id | check | expected |
|---|---|---|
| 1.7 | Signup with valid data | personal tenant created, verification mail sent, login blocked until verified (when mail is on) |
| 1.8 | Signup with taken username / invalid email | clean 400, no orphan tenant created |
| 1.9 | Verify link → login | works; resend-verification is rate limited |
| 1.10 | Signup disabled | endpoint 404/403 and no signup link on the login page |

### 1.C What's-new tour (new)
| id | check | expected |
|---|---|---|
| 1.11 | First login of an existing user after the update | tour modal opens ~0.5 s after load, "Update 2026.09.22 · 1 of N", dots, Back hidden on step 1 |
| 1.12 | Next / Back / Show me | Show me closes the modal and opens the right page for the role (personal → My finances); Back/Next move steps; last step says "Got it" |
| 1.13 | Finish then reload | tour does not reappear; `last_seen_release` set |
| 1.14 | Settings → What's new button | reopens the full history without re-marking |
| 1.15 | Role filtering | personal user does not see the "Entity transactions" step; employee sees only the generic step |
| 1.16 | Brand-new user created after the release | no tour on first login |
| 1.17 | Persian / Spanish / Arabic UI | all copy localized, RTL correct |

### 1.D Shell: navigation, language, bell
| id | check | expected |
|---|---|---|
| 1.18 | Switch UI language en → fa → es → ar → en | every label translates (no raw keys), RTL flips for fa/ar, choice persists after reload and is saved on the user |
| 1.19 | Sidebar on mobile width (375px) | drawer opens/closes, no horizontal scroll, page title updates |
| 1.20 | Browser back/forward between pages | hash navigation reloads page data (no empty "—" cards) |
| 1.21 | Bell: unread badge, list, click item → opens link page, mark read, read-all | works; poll refreshes every ~90 s |
| 1.22 | Bell kinds by role | employee sees only own reminders/approvals; personal sees budget/recurring/commitment/insight; accountant does not see CFO-only |
| 1.23 | Personal reminders (create / repeat / edit / delete) | appear in the bell, advance on repeat |
| 1.24 | Company badge + branding | company name/logo in sidebar & header after profile save |

---

## Part 2 — Core bookkeeping

### 2.A Vouchers (manual entry)
| id | check | expected |
|---|---|---|
| 2.1 | Balanced 2-line voucher, IRR | saved; appears in ledger, dashboard cache refreshes |
| 2.2 | Unbalanced / single-line / both debit&credit on a line | rejected with a clear message, nothing saved |
| 2.3 | Future date (> tomorrow) | rejected |
| 2.4 | Date inside a closed period | rejected with the closed-through date |
| 2.5 | Foreign currency voucher (USD) | saved with currency; reporting converts using FX rate; missing rate warns |
| 2.6 | Entity links (client/bank/payee/supplier) + AI-suggested links | saved and visible in entity view |
| 2.7 | Attachment upload (jpg/png/pdf; oversize 9 MB; wrong type) | ok / rejected / rejected; file opens from the voucher |
| 2.8 | OCR on an attached receipt | vendor/date/total extracted and offered to prefill |
| 2.9 | Jalali date hint next to the date input | correct conversion |
| 2.10 | Edit a voucher (PATCH) and delete (soft) | edit versioned in audit trail; deleted voucher disappears from every report but stays in the audit log |

### 2.B Excel / CSV import & migration
| id | check | expected |
|---|---|---|
| 2.11 | Import Journal from Excel → preview → confirm | vouchers created per document number, balanced check, unmapped accounts listed |
| 2.12 | Same file twice | second import warns / does not double post |
| 2.13 | Migration: chart export (group/kol/moein/tafsili) upload → preview → confirm | accounts + banks + counterparties created, opening journal balanced; re-apply is idempotent |
| 2.14 | "Complete imported records" queue → hand-off to chat | assistant proposes **update** (never a duplicate entity) |

### 2.C Entities (clients, banks, employees, suppliers, shareholders)
| id | check | expected |
|---|---|---|
| 2.15 | Create each type, with bank details / tax ids | saved; bank gets its own GL cash account |
| 2.16 | Edit, delete (with and without transactions) | delete blocked or cascades as designed; no orphan links |
| 2.17 | **View transactions → statement columns (new)** | Debtor / Creditor / Remaining per row; running balance matches a hand calculation for a bank, a client, a supplier |
| 2.18 | Bank without its own code | uses the chart bank account; rows still placed |
| 2.19 | Cash sale linked to a client | both columns filled, balance unchanged |
| 2.20 | **Add transaction (new)** from the entity view | editor opens with the entity pre-linked and its control account on line 1; save → row appears in the statement; validation same as vouchers |
| 2.21 | Edit / Delete from the statement view | works; statement refreshes |
| 2.22 | Headers in fa/es/ar | بدهکار / بستانکار / مانده etc. |

### 2.D Ledger & manager reports
| id | check | expected |
|---|---|---|
| 2.23 | Ledger summary, turnover, net position | totals equal trial balance; soft-deleted excluded |
| 2.24 | Manager reports: trial balance, P&L, balance sheet, cash flow, AR/AP aging, debtor/creditor, person running balance, journal | run for a period; balanced; export JSON/CSV/PDF downloads (PDF renders Persian glyphs) |
| 2.25 | Journal edit from manager reports | balanced validation; audit version stored |
| 2.26 | Reporting currency switch (Settings → FX) | dashboards/reports convert; rate table editable; GOLDG/GOLDC units accepted |

### 2.E Period close & adjustments
| id | check | expected |
|---|---|---|
| 2.27 | Close period through date D | posting ≤ D blocked everywhere (voucher, import, statement post, AI card) |
| 2.28 | Accrual with auto-reverse, prepayment, depreciation | entries + reversals dated correctly |
| 2.29 | Reopen period | posting allowed again |

---

## Part 3 — Money in / money out modules

### 3.A Invoices
| id | check | expected |
|---|---|---|
| 3.1 | Itemized and simple invoice, VAT, due date default (+30) | totals correct; issue → AR/revenue posted |
| 3.2 | Record payment, partial payment, overpayment | AR reduced; status flow draft → issued → paid |
| 3.3 | Void, reverse payment (chargeback) | reversing entries; AR/revenue/cash back to zero |
| 3.4 | OCR scan of an invoice PDF → create | fields prefilled |
| 3.5 | Invoice due / overdue notifications | appear in the bell for owner/cfo/accountant |
| 3.6 | PDF / print preview | company branding, Persian text |

### 3.B Recurring
| id | check | expected |
|---|---|---|
| 3.7 | Create monthly rule → run due | voucher created once per period; reminder-only rules notify without posting |
| 3.8 | "Looks like these repeat" detector | shows genuine patterns, ignores one-offs; create rule from a suggestion |

### 3.C Installments & cheques
| id | check | expected |
|---|---|---|
| 3.9 | Installment plan (N months) → upcoming list → settle one | settlement posts via the canonical builder; remaining count updates |
| 3.10 | Cheque issue → clear / bounce | bounced ≠ settled; notification on due/bounced |

### 3.D Time & billing
| id | check | expected |
|---|---|---|
| 3.11 | Log time (owner for employee, employee for self), project, rate | entries listed; employee sees only own |
| 3.12 | Ready to invoice → preview → confirm | draft invoice grouped by project/employee; time marked invoiced; multi-currency refused |
| 3.13 | My pay (employee) | own payslips only |

### 3.E Expenses & mileage, approvals
| id | check | expected |
|---|---|---|
| 3.14 | Employee submits claim + mileage | pending; manager/cfo see approvals queue |
| 3.15 | Approve / reject; over-threshold routing | posting only on approval; settings threshold respected |

### 3.F Purchase orders & inventory & products
| id | check | expected |
|---|---|---|
| 3.16 | PO create → receive → three-way match | mismatches flagged; stock movement recorded |
| 3.17 | Inventory item, movements, price management, balance/movement reports + exports | quantities and valuations consistent |
| 3.18 | Products & relationships chart, product detail | loads for existing products |

### 3.G Payroll
| id | check | expected |
|---|---|---|
| 3.19 | Pay profile (salaried, hourly), rates | saved; validation |
| 3.20 | Run payroll → post → pay | gross/tax/social/net; journals; pay runs list; payslips |
| 3.21 | Payroll due notification | in bell ≤ 3 days before pay date |
| 3.22 | Year summary | totals per month |

### 3.H Shareholders & equity
| id | check | expected |
|---|---|---|
| 3.23 | Add shareholder, contribution, dividend declare/pay, capital increase, current account | cap table %, postings, changes-in-equity statement |
| 3.24 | Same via AI chat (contribution, dividend) | one card each; shareholder never created as employee |

### 3.I Petty cash
| id | check | expected |
|---|---|---|
| 3.25 | Create account for a user, deposit, expense with attachment, approve/reject, adjust | balances; employee sees only own |

### 3.J Budgets, net worth, personal mode
| id | check | expected |
|---|---|---|
| 3.26 | Budget per category → spend to 85% / 100% | bell warning then high; dashboard budget vs actual |
| 3.27 | Net worth: holdings in gold/currency units, rates | valuation, unrealized gain, missing rate listed not zeroed |
| 3.28 | Personal dashboard: KPIs, category doughnut, monthly spend, budgets, **What changed** | numbers match reports; no SME panels |
| 3.29 | Personal nav | only My finances / AI Chat / Vouchers / Recurring / Installments / Bank statements |

---

## Part 4 — Bank statements & reconciliation (heavily changed)

| id | check | expected |
|---|---|---|
| 4.1 | Upload CSV / Excel / image / PDF on the Bank statements page | rows parsed, categories suggested, duplicates flagged; needs-mapping flow for unknown columns |
| 4.2 | Same file twice | "already imported" prompt with confirm option |
| 4.3 | Overlapping re-import | only shared rows flagged duplicate |
| 4.4 | Reconcile | matched / partial / unmatched / duplicates / missing-in-bank counts; unreconciled difference exact; fee/interest suggestions with Record button |
| 4.5 | **Check against books (new)** | panel lists findings with labels: Not in the books / Probably the same entry / Amount differs / In books, not on statement / Imported before / Closing balance gap; balance line bank vs books |
| 4.6 | Post from a finding | row posted with suggested account; finding disappears; re-check shows it settled |
| 4.7 | Approve match from a finding | row matched |
| 4.8 | Identical rows within one statement | second labelled as possible double charge, not "imported before" |
| 4.9 | Approve all matched / Post all suggested | batch works; duplicates never posted; period-lock respected per row |
| 4.10 | Category picker per row + history learning | changing a category teaches the next import of the same merchant |
| 4.11 | Post → delete the transaction (Vouchers) | statement row released (postable again) |

### 4.B Statement in the AI chat (new)
| id | check | expected |
|---|---|---|
| 4.12 | Attach the Mellat PDF, "these are last Mellat transactions, add them" | ~1 min; reply summarises rows / not in books / imported before / balance gap; card shows counts + buttons; **no** "please type the rows" |
| 4.13 | Same PDF again | "already imported" card pointing at the earlier statement |
| 4.14 | Receipt PDF/image attached | normal OCR-to-proposal path (no statement created) |
| 4.15 | CSV statement attached | statement intake (not the transactions-sheet intake) |
| 4.16 | Viewer/employee attaches a statement | falls through (no import) |
| 4.17 | "Fix step by step" | assistant describes finding 1 and a card for **that** row appears (date = bank date, narration verbatim, Dr suggested account / Cr bank) |
| 4.18 | Confirm → "next" | row marked posted on the statement page; next card is a different row |
| 4.19 | Undo within 2 min | entry removed (404), statement row released; "Removed ✓" in the card |
| 4.20 | Re-propose a posted row (say "post the first one again") | assistant refuses / moves on; execute of a stale card → 409 |
| 4.21 | "Open in Bank statements" | opens the statement with the review panel |
| 4.22 | Persian user does the same flow | Persian summary and hints, cards in Persian |
| 4.23 | Statement with a book entry the bank never saw | finding "missing in bank"; assistant asks before proposing a reversal |
| 4.24 | Closing balance gap when all rows posted | severity high, explained flag correct |

---

## Part 5 — AI accountant (chat)

### 5.A Core flows
| id | check | expected |
|---|---|---|
| 5.1 | New chat, sessions list, rename, archive, search, restore latest on reload | works |
| 5.2 | Record an expense in plain language (en, fa with تومان) | one card, correct Dr/Cr, Toman→Rial ×10 stated |
| 5.3 | Typed "confirm" while a card is pending | deterministic pointer to the button, no duplicate card |
| 5.4 | Confirm → Undo (≤120 s) | **entry removed**, receipt says Removed; after 120 s button becomes Reverse entry → compensating entry |
| 5.5 | Undo inside a closed period | reversal instead of delete, note says so |
| 5.6 | Receipt image → proposal with attachment linked | file follows the entry |
| 5.7 | Spreadsheet drops: chart export → migration card; transactions sheet → import card; other sheet → Q&A context | correct card kinds |
| 5.8 | Entity resolution: existing / ambiguous / new supplier / rename / type fix | one lookup, correct proposal kind (create vs update), never duplicates |
| 5.9 | Reports via chat: balance sheet, P&L, trial balance, tax summary (caveat verbatim), "who are our clients" | deterministic tool answers |
| 5.10 | Refusals (structuring, backdating fabrication) | one-sentence refusal, no plan |
| 5.11 | Proposal expiry (>10 min) | Confirm → expired message |
| 5.12 | Amount sanity guard (mis-scaled OCR total) | refused/asked, not proposed |

### 5.B Proactive insights (new)
| id | check | expected |
|---|---|---|
| 5.13 | Dashboard "What changed" with real data (payroll change, expense spike, vendor outlier, statement due) | items ranked; Open goes to page; Ask the AI sends the question |
| 5.14 | Bell kind `insight` | appears; auto-resolves when condition clears; dismiss stays dismissed |
| 5.15 | Chat briefing once/day on open | assistant-first message with the top items; none when steady; persisted in the session |
| 5.16 | "How are things going?" (en) / "چه خبر؟" (fa) | calls get_insights, relays items or says steady |
| 5.17 | Language | company-wide bell rows in fa for IR tenant; dashboard in the user's language |

---

## Part 6 — Reports, CFO/CEO, audit, settings, admin

| id | check | expected |
|---|---|---|
| 6.1 | Owner dashboard: KPIs, 13-week forecast, alerts, AR/AP aging, expense by category, spend by vendor, profitability, books health, month-end checklist, report pack, fix missing references, budget vs actual, export/notify | all render with data; currency filter |
| 6.2 | CFO mode: KPIs, insights, risk grade, Ask the CFO AI | localized; answers keyword questions |
| 6.3 | CEO mode: trends, top expenses, balance sheet summary, AR/AP | charts render; zoom plugin works (vendored) |
| 6.4 | Audit: run full audit (anomalies, duplicates, negative balances, backdated, liability threshold), integrity history, audit trail filters, transaction versions | findings sensible; threshold save |
| 6.5 | Settings: company profile & logo, language, period close, adjustments, AI providers (metis/lmstudio/anthropic/custom) + chat shape, user management (create/role/deactivate), daily digest (preview/send), API keys (create/copy/delete), FX & rates, reporting locale, display calendar, reset demo IR/UK/empty | each saves and takes effect; keys shown once |
| 6.6 | Daily digest delivery (slack/telegram/email) | received or clearly skipped when disabled |
| 6.7 | Integration API with an API key | authenticates; revoked key fails |
| 6.8 | Companies console (super-admin): create IR/UK/personal company, provisioning user, list | tenant isolation: data never leaks across companies (check ledger, entities, statements, notifications) |

---

## Part 7 — Cross-cutting

| id | check | expected |
|---|---|---|
| 7.1 | RBAC matrix spot checks: each role hits 3 forbidden endpoints directly | 403; UI hides the pages |
| 7.2 | Tenant isolation | user of company A cannot read/write company B objects by id (404/403) |
| 7.3 | CSRF: mutating call without header | 403 |
| 7.4 | File magic validation (renamed .exe as .pdf) | 400 |
| 7.5 | Persian digits & Jalali everywhere (inputs, tables, exports) | consistent |
| 7.6 | Performance: dashboard, ledger and statement review on the largest tenant | < 3 s; bell poll cheap (insights cached) |
| 7.7 | Error surfaces | no raw 500 text in the UI; JSON errors shown as messages |
| 7.8 | Console | no JS errors on any page in all four languages |
| 7.9 | Prod deploy | image tag matches main, migrations applied, uploads volume intact, old sessions still valid |

---

## Log template

| id | env | role | lang | result (P/F/B) | note / defect link |
|---|---|---|---|---|---|

Exit criteria: no F in Parts 0, 1, 2, 4, 5; every F elsewhere has an issue
and an owner; Part 7.1–7.4 all P.
