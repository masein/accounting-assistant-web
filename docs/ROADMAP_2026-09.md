# Roadmap — what to build next (drafted 2026-09-24)

Sources: the 2026-09-24 production QA run (`QA_RESULTS_2026-09-24.md`), a
code inventory of every router, page, job and AI tool, a route-by-route test
coverage audit, a security/ops review of commit `5ac0cde`, and a look at what
Hesabfa, Sepidar, Holoo, Xero, FreeAgent and QuickBooks ship in 2026 plus the
Iranian and UK compliance calendar for 1405 / 2026-27.

Priorities are ordered by **risk to a customer's money or data first, then by
what wins or keeps customers**. Each item names the file(s) involved so it can
become a branch straight away. Items marked ✅ shipped on 2026-09-24.

---

## 0. Already fixed on 2026-09-24

✅ Security: tenant-scoped dashboard cache, private per-company snapshots,
attachment extensions from the validated type, soft-delete filters in
exports/budgets/running balance, payroll voided-run filter, statement upload
cap, CSV formula guard (#106). ✅ All 37 QA findings (#87–#105).

**"Now" block (§7 step 1) — shipped 2026-09-24:** ✅ 1.1 uploads behind auth
(#111) · ✅ 1.2/1.3 sessions + change-password (#108) · ✅ 1.4 admin audit +
append-only audit_logs (#118) · ✅ 1.5 closed period on every posting path,
chat undo soft-deletes (#110) · ✅ 1.9 backups (#112) · ✅ 1.10 CI-gated
publish, pinned Watchtower, Secure cookie, proxy headers, HSTS (#112) · ✅ 2.1
scheduler (#113) · ✅ test suites 1–3 (#114, #115, #116) · ✅ chat page
redesign incl. RTL (#109) · ✅ SQLite UUID affinity flake (#117).

**Next block (§7 step 2, security remainder) — shipped 2026-09-24/25:** ✅ 1.4
admin audit + append-only audit_logs (#118) · ✅ 1.7 protected prefixes derived
from routers, cross-site login/logout refused (#119) · ✅ 1.8 login limits per
username and per IP, dummy hash, X-Forwarded-For gated (#120) · ✅ 1.11 AI keys
encrypted at rest, env wins (#121) · ✅ 1.12 PostgreSQL CI job — bootstrap +
full suite (#122) · ✅ 1.13 /health 503, docs off in prod (#123) · ✅ 2.3
upload/parse handlers synchronous, middleware DB work off the loop (#124).
Still open in §1: API key scopes/expiry, 2FA, CSP without inline scripts,
non-superuser DB role, model/migration drift cleanup (alembic check).

**§7 step 2 (features) — in progress 2026-09-25:** ✅ 5.1 AI tools for
invoices, cheques and installments (#125) · ✅ 3.3 statutory payroll rules as
data (`payroll_rule_sets`, Iran 1405 + UK 2026/27 seeded, super-admin edits,
statutory pay profiles, employer share posted, لیست بیمه + salary-tax CSV
exports) — remaining in 3.3: عیدی/سنوات year-end runs, بیمه/مالیات portal
file formats once a customer supplies the templates. · ✅ boot fix: platform
AI settings kept out of the Default-company backfill, pre-start tracebacks
visible (#127) · ✅ 4.2 quotes → invoice (#128) · ✅ 4.2 invoice e-mail with
PDF + payment details, automatic overdue reminders (owner opt-in, default
3/10/20 days, one per stage, e-mail log) (#129) · ✅ 4.2 recurring invoices
with optional auto-send, anchored schedules incl. Jalali months, closed-period
retry. §4.2 done except SMS (no provider chosen yet). Next: §3.1 مودیان export.

---

## 1. Security and data safety (do before onboarding paying customers)

| # | Item | Why | Where |
|---|---|---|---|
| 1.1 | **Serve uploads through authenticated, company-checked routes** and drop the public `/uploads` static mount | every receipt, statement, logo and signature is downloadable without login today; the Default company's signature URL is fixed | `app/main.py:447,768`, `app/api/transactions.py:167`, `company_profile.py:157` → new `GET /files/{kind}/{id}` with `Content-Disposition: attachment` |
| 1.2 | **Session hardening**: missing user or DB error ⇒ invalid session; bump `token_version` on password change and logout; re-check `is_superadmin` from the DB each request | a deleted user keeps a working session for 24 h; stolen tokens survive logout/password change | `app/main.py:511-530`, `app/api/auth.py:334,423` |
| 1.3 | **Change-password requires the current password** + uses `validate_password_strength`; generate a random admin password at first boot and print it once | `admin/admin` + the forced-change flow lets anyone become super-admin on a fresh install | `app/api/auth.py:412-432`, `app/db/seed.py:324` |
| 1.4 | **Audit every admin / super-admin action**; make `audit_logs` append-only at the DB level (non-superuser DB role, no UPDATE/DELETE grants, drop the company cascade) | reopening a period, reset-db, user/API-key/company changes leave no trace; "immutable" is only a comment | `app/api/admin.py`, `companies.py`, `alembic/015:119` |
| 1.5 | **Every posting path through `ledger_posting`** with the closed-period check: bulk JSON import, Excel confirm, FX revaluation, journal reversal; make chat "undo" a soft delete with audit | four ways to write into a locked period; chat undo hard-deletes anyone's latest entry | `transactions.py:1585,1855,485`, `fx.py:278`, `reporting/ledger_service.py:213` |
| 1.6 | **Global soft-delete filter** (`with_loader_criteria(Transaction, deleted_at IS NULL)`) next to the tenant filter, explicit opt-out for audit views | 8 more queries in `operations_report_service`, `transaction_chat`, `commitment/equity/fee` services still count deleted rows | `app/db/tenant.py` |
| 1.7 | **CSRF/rate-limit/password-lock on every router** (build `PROTECTED_API_PREFIXES` from the registered routers) | `/commitments`, `/insights`, `/personal`, `/migration`, `/petty-cash` skip all three | `app/main.py:358-384` |
| 1.8 | **Login brute-force**: limit per username *and* IP in a shared store, dummy hash for unknown users, trust `X-Forwarded-For` only from the proxy | user enumeration by timing; lockout DoS of `admin`; forged audit IPs | `app/api/auth.py:50,118`, `core/audit.py:15` |
| 1.9 | **Backups**: nightly `pg_dump` + uploads tar to off-box storage, restore script, backup-before-migrate in `entrypoint.sh`, restore drill documented | there is no backup of any kind; Watchtower auto-migrates | `scripts/`, `docker-compose.prod.yml`, `DEPLOY.md` |
| 1.10 | **Deploy gate**: publish the image only after CI passes; pin `postgres`, `watchtower` and the app image by digest; `AUTH_COOKIE_SECURE=true`, uvicorn `--proxy-headers`, bind 127.0.0.1, HSTS | untested images reach prod within 2 minutes; Secure cookie flag likely off behind the proxy | `.github/workflows/publish-image.yml`, `Dockerfile:50`, compose |
| 1.11 | **AI keys out of the DB** (or encrypted) and env wins over the DB copy | provider keys sit in plain text in `app_settings`; rotating `.env` has no effect | `app/core/ai_runtime.py:98-133` |
| 1.12 | Fresh-DB schema parity: bootstrap with `alembic upgrade head`; Postgres CI job that diffs models vs migrations | fresh installs lack the NOT NULL / FK / unique constraints of 015 | `app/main.py:214-263`, `.github/workflows/ci.yml` |
| 1.13 | Small ones: `/health` → 503 when DB down; API docs off in prod; API-key scopes + expiry; 2FA (TOTP) for owner/super-admin; tighter CSP (move 14 inline handlers + `login.html` script into files) | | `main.py:681`, `models/api_key.py`, `index.html` |

## 2. Reliability and operations

| # | Item | Why | Where |
|---|---|---|---|
| 2.1 | **A real scheduler** (APScheduler in-process for now, a `scheduler` service later) with a service credential: recurring `run-due` daily, daily digest, notification refresh, period-end releases, snapshot cleanup, unlinked-attachment cleanup | nothing runs unless a browser is open; digest and recurring posting depend on an external cron that doesn't exist | `services/recurring_service.py`, `api/notifications.py:121`, new `app/jobs/` |
| 2.2 | **Shared state → Postgres/Redis**: login/chat limiters, dashboard cache, Excel upload tokens, AI config | all in-process; blocks running >1 worker and loses tokens on restart | `auth.py:50`, `reports.py:427`, `transactions.py:1641`, `ai_runtime.py:28` |
| 2.3 | **Don't block the event loop**: sync DB in async middleware and async upload endpoints → sync `def` or `run_in_threadpool`; then 2–4 workers | one slow parse stalls every user | `main.py:441-530`, `transactions.py:1685`, `brain.py:125`, `migration.py:47` |
| 2.4 | **Observability**: JSON logs with request id in a contextvar, Sentry (or GlitchTip) for exceptions, `/metrics` (Prometheus) for request latency / LLM calls / job runs, Docker log rotation | no error reporting, no metrics, request id not in log lines | `main.py:63-66,722` |
| 2.5 | **Per-user and per-company AI limits** with a daily token budget and cost log | one user can exhaust the global chat bucket; no cost visibility | `transactions.py:469`, `ai_accountant.py` |
| 2.6 | **Performance**: SQL aggregation for ledger summary and dashboard (they load every line), composite index `(company_id, date, currency)` + partial index on `deleted_at`, N+1 in `payroll.py:548` and `manager_reports.py:942`, gzip + `Cache-Control: immutable` for hashed static files, per-language i18n packs (02-i18n.js is 350 KB) | dashboard/ledger will not scale past a few thousand entries | `reports.py:166,456`, `models/transaction.py` |
| 2.7 | Dependency lock file with hashes (uv/pip-tools); raise `pypdf<5` cap; `offline-deploy.sh` must use the prod compose and exclude `*.dump`, `*.tgz` | prod and dev differ; a local DB dump could ship inside the image | `requirements.txt`, `.dockerignore`, `scripts/offline-deploy.sh` |

## 3. Compliance features (market entry blockers)

### Iran
| # | Item | Notes |
|---|---|---|
| 3.1 | **سامانه مودیان e-invoicing** — the #1 gap vs Hesabfa/Sepidar/Holoo. Since آذر 1404 paper invoices have no tax validity. Needs: per-company شناسه یکتای حافظه مالیاتی, 22-char شماره منحصربه‌فرد مالیاتی generator, 13-digit شناسه کالا/خدمت on products, invoice patterns (نوع ۱ B2B / نوع ۲ B2C, الگوها), signing with the company's private key/CSR, submission (direct or via a trusted provider such as the ones Mahak/Sepidar bundle), status tracking (pending / confirmed / rejected), 12/20-day deadline reminders as notifications, resend on rejection | New module `app/services/moadian/`, fields on `Company`, `Invoice`, `Product`; start with file export for a trusted provider, then direct API |
| 3.2 | **گزارش معاملات فصلی (ماده 169) — TTMS export** of purchases/sales per quarter (45-day deadline) reconciled to the VAT return; **اظهارنامه ارزش افزوده** quarterly figures (15-day deadline) from `tax_summary` | `app/api/reports.py:770` tax summary → add TTMS file layout + a "quarter close" checklist item |
| 3.3 | **Payroll 1405 parameters as data, not code**: minimum wage 5,541,850/day, حق مسکن 30,000,000, بن 22,000,000, حق اولاد, سنوات, insurance 7%/23% with the ceiling, income-tax brackets (exempt to 480 M/yr, 10/15/20/25/30 %), overtime 1.4×, عیدی 2–3× | `payroll_service.py` → a versioned `payroll_rules` table per Jalali year + UI to edit; **لیست بیمه (تامین اجتماعی) and salary-tax file exports** |
| 3.4 | **Cheque handling like Iranian books expect**: چک دریافتی/پرداختی lifecycle (in hand → deposited → cleared / bounced → returned), صیاد ID field, cheque print layout, reminder on sayad registration | extends `commitments` (already bounced ≠ settled) |
| 3.5 | **Jalali everywhere in reports**: monthly buckets, budgets and `year-summary` use Gregorian months for `ir` companies | `manager_reports.py:671`, `budget_service.py:26`, `payroll.py:681` |

### UK
| # | Item | Notes |
|---|---|---|
| 3.6 | **MTD for Income Tax (April 2026)** — quarterly updates for sole traders/landlords over £50k (£30k in 2027): digital records tag (self-employment / UK property), quarterly income/expense summary in HMRC's categories, export or (later) HMRC API submission; MTD VAT return figures (boxes 1–9) from the tax summary | new `app/services/uk_mtd/`; FreeAgent/Xero parity |
| 3.7 | UK FRS 102 statements: real depreciation/amortisation lines, OCI, dividends paid (currently placeholders) | `reporting/uk_statement_service.py:12,489,615,714` |

## 4. Product features (what customers compare against)

| # | Item | Why / competitor reference |
|---|---|---|
| 4.1 | **Bank feeds without a bank API**: parse bank SMS / push notifications forwarded by the user (Mahak does this on Android), plus scheduled statement e-mail ingestion; later Finnotech-style open-banking for balances/statements | Iranian banks have no Plaid; SMS capture is what personal-finance users expect |
| 4.2 | **Quotes / پیش‌فاکتور → invoice**, recurring invoices with auto-send, invoice e-mail/SMS with a "pay by card-to-card / payment link" line, **automatic overdue reminders** (Xero default: 3 reminders) | AR collection is the most-cited SME pain; nothing e-mails invoices today |
| 4.3 | **Fixed-asset register**: asset cards, depreciation methods (straight-line exists as an adjustment), disposal, Iranian tax useful-life table | adjustments exist but there is no register or disposal |
| 4.4 | **Inventory costing** (weighted average / FIFO), stock valuation report, reorder alerts, barcode field; production/BOM light | Hesabfa/Holoo core; ours is movements + average price only |
| 4.5 | **Chart-of-accounts management**: create / rename / deactivate accounts, تفصیلی groups, opening balances UI | accounts are only created implicitly today |
| 4.6 | **Multi-currency close**: unrealised FX revaluation exists; add realised gain/loss on settlement, rate feed (manual today, XE-style hourly for GBP/EUR/USD, a gold-price feed for personal holdings) | Xero parity; personal tenants hold gold/FX |
| 4.7 | **Budgets**: edit route, Jalali months, per-project budgets, roll-forward | `budgets.py` has no PATCH |
| 4.8 | **Purchase orders**: cancel/delete, partial receipts to bills, supplier price history | `purchase_orders.py` |
| 4.9 | **Documents**: statements of account e-mailed to clients, payslips e-mailed to employees, PDF/XLSX export of financial statements (server side), a "monthly close pack" zip | mail service exists but sends nothing to parties |
| 4.10 | **Mobile**: PWA (manifest + offline shell), camera receipt capture straight into the chat, bell push via Web Push; later native | competitors all have apps; ours is responsive only |
| 4.11 | **Migration importers**: Hesabfa/Holoo/Sepidar exports, Xero/QuickBooks CSV, historical transactions (not just opening balances) | switching cost is the main sales objection |
| 4.12 | **Personal mode**: installment/loan schedules with reminders, shared household tenants, savings goals, gold/FX valuation from a feed, monthly report card | matches the Iranian personal-finance apps (بانک، محک، فانوس) |

## 5. AI accountant

| # | Item | Why |
|---|---|---|
| 5.1 | **Tools for the modules the chat cannot reach**: invoices (list open/overdue, record payment, create invoice, credit note), payroll (run/post/pay, "what did we pay Sara"), commitments (upcoming cheques, settle/bounce), recurring & reminders (`/recurring/from-text` exists but no tool), budgets, petty cash, purchase orders, inventory, FX, cap table read, dividend pay, period-end (accrue/prepay/lock), audit trail | no tool touches `Invoice`, so half the product is invisible to the assistant |
| 5.2 | **Anomaly detection as insights**: duplicate supplier payment, payment just under an approval threshold, new vendor + large first payment, round-amount weekend entries, expense category drift, entity with sudden reversal pattern | industry-standard agent capability; `insight_service` has the hook points |
| 5.3 | **13-week cash forecast that learns**: recurring rules + open AR/AP + payroll dates + commitments → scenario ("what if the Mellat cheque bounces"); expose as a tool and on the dashboard | dashboard forecast today is a moving average |
| 5.4 | **Correction memory**: when the user edits a proposed category/entity, store the (description pattern → account/entity) preference per company and feed it to `search_accounts`/`find_entity` and statement categorisation | QuickBooks-style learning; cuts repeat questions |
| 5.5 | **Evaluation harness**: turn `scripts/model_eval.py` into a CI-able eval set (fa/en scenarios, expected tool trajectory + card contents), run nightly against the configured model, alert on regressions; sample 10 % of prod turns into an offline review queue (no PII beyond the tenant) | model/prompt changes are only checked by hand today |
| 5.6 | **Guardrails**: max proposal amount vs source amounts already exists — add per-company confirm thresholds (two-person approval above X), refuse to post into closed periods from chat (server-side), tool-call budget per turn | agent safety |
| 5.7 | **Voice notes** (Persian speech-to-text via Metis) into the chat; **WhatsApp/Telegram inbound bot** for personal tenants ("۵۰ هزار نان") | the daily-diary use case lives in messengers |

## 6. Quality engineering (tests to add)

Coverage today: 296 routes, 153 tested over HTTP, 24 only by direct function
call (skipping RBAC/CSRF/tenancy), **119 untested**; no coverage gate; no
browser tests; SQLite-only CI while prod is Postgres.

Highest-value suites, in order (see `QA_PLAN.md` Appendix B for the manual
counterparts):

1. `test_rbac_live_writes.py` — every non-GET route × every role over HTTP; 403 exactly when `user_can_access` denies.
2. `test_tenant_isolation_http.py` — two companies; GET/PATCH/DELETE/PDF on the other's invoice, PO, pay run, attachment, shareholding, FX rate, time entry, rule, adjustment ⇒ 404 and no change.
3. `test_invoice_lifecycle_http.py` — payments, credit notes, void, reverse, mark-paid, receipt/invoice PDF, timeline, delete; GL balanced after each step; double payment; void-after-payment 409.
4. `test_equity_api.py` — all 9 routes + `equity_execute` via `execute_proposal`; cap table sums to 100 %.
5. `test_payroll_lifecycle_http.py` — post twice 409, pay before post 409, PDFs.
6. `test_manager_reports_books.py` — 23 untested report routes; running balances tie to the GL; deleted rows excluded.
7. `test_fx_rates_and_revalue.py` — rounding at .5, 10^14 IRR through float, revalue idempotent.
8. `test_upload_limits.py` — every upload route just over its cap; spoofed types incl. logo/signature (which trust `content_type` today).
9. `test_jalali_calendar_edges.py` — `1404/12/30` rejected, Esfand 29/30 periods, no-year dates near Nowruz with a frozen clock, Arabic-Indic digits, Excel Jalali dates.
10. `test_time_billing_and_fees_http.py` — projects/rates CRUD, write-off, invoice preview saves nothing, fee basis points.

Plus: `--cov --cov-fail-under=80` in CI; a Postgres CI job that runs `alembic upgrade head` and the suite; a 10-page Playwright smoke suite for the five JS files no test reads (`05-reports-manager`, `08-entities-invoices`, `11-time-expenses-payroll`, `12-ops`, `13-companies-products`); property tests for `balance_from_turnovers` / `resolve_period`; a nightly AI eval (5.5).

Known defects found by the audits and **not yet fixed** (small, worth branches now):
- logo/signature uploads trust the browser `content_type` (`company_profile.py:147`) — use `validate_file_magic`;
- rate limiter memory never freed (`core/rate_limit.py`);
- `_EXCEL_UPLOAD_STORE` temp files never cleaned, token contains the raw filename (`transactions.py:1703`);
- `/health` returns 200 while degraded;
- login/logout have no CSRF/Origin check;
- Excel import confirm and bulk JSON import bypass the closed-period lock (1.5).

## 7. Suggested order of work

1. **Now (this week)**: 1.1 uploads behind auth · 1.2 sessions · 1.3 change-password · 1.5 closed-period on all paths · 1.9 backups · 1.10 deploy gate · 2.1 scheduler · test suites 1–3.
2. **Next**: 3.1 مودیان (start with export for a trusted provider) · 3.3 payroll rules table + بیمه export · 4.2 quotes/reminders/e-mail · 5.1 invoice + commitment tools · 2.2/2.3 shared state + workers · test suites 4–8.
3. **Then**: 3.2 TTMS/VAT · 3.6 MTD ITSA · 4.1 SMS bank capture · 5.2/5.3 anomalies + forecast · 4.3–4.5 · 4.10 PWA · 2.4 observability · Playwright + Postgres CI.
4. **Later**: 4.11 importers · 4.6 FX feeds · 5.4 correction memory · 5.7 messenger bots · 2FA · API scopes.
