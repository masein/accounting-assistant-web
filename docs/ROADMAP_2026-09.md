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
✅ 1.13 API-key scopes (`time:read` / `time:write`) + expiry (default one
year, owner warned 14 days before, expired keys refused) (#146).
✅ 1.13 two-factor sign-in (TOTP + ten recovery codes, any role, recommended
for owners/super-admins, owner reset for a lost phone) (#147). Follow-up: a
policy switch that *requires* 2FA for owners.
✅ 1.13 strict CSP — `script-src 'self'`, `script-src-attr 'none'`,
`object-src 'none'`; every inline handler replaced by `data-action` +
`registerAction`, the login script in `/static/login.js`, a scanner test and a
browser test that injected inline code is refused (#148).
✅ 1.4 restricted database role — opt-in `APP_DB_PASSWORD`; pre-start
creates `aa_app` (rows only; audit_logs INSERT/SELECT; no DDL), the web
server connects as it; CI checks the refusals and runs the browser suite
under it (#149).
✅ 1.13 model/migration drift — 139 differences resolved: migration 045
converges fresh and migrated installs (BIGINT salaries, partial unique
app_settings keys, users→companies CASCADE, NOT NULL timestamps, one index per
purpose); `install_tenant_guards` gives fresh installs the tenant NOT NULL +
FKs migrated ones have had since 015 (FKs for the 16 later tenant tables too);
tax rates seeded per company; `alembic check` gating on a fresh bootstrap and
on a migrated 044 snapshot (#150). ✅ 1.6 global soft-delete filter (#160):
every SELECT leaves out undone journals and the lines of undone journals
(`app/models/transaction.py`, opt in with `include_deleted_transactions()`);
four audit checks — accounting equation, debit/credit balance, negative
balances, liability threshold — had been summing undone journals' lines.
No measurable cost on the 20k-journal bench. §1 is done.

**§2 continued — 2026-09-26:** ✅ 2.5 AI usage ledger + limits
(`ai_usage_events`, one row per provider call from the five choke points with
real token counts and an estimated cost; rolling 24-hour token budgets per
company (super-admin) and per user (owner), per-minute request limits per user
and per company replacing the single global chat bucket; 429 with a reason,
mid-turn stop; owner/CFO view in Settings → AI usage; owner notification at
80 %; `aa_llm_tokens_total`) (#151). ✅ 2.6 performance, first pass (#152),
measured with `scripts/perf_bench.py` on 20k journals / 60k lines: ledger
summary 1,083 → 23 ms (SQL GROUP BY), owner dashboard 3,145 → ~510 ms cold
(flat column reads, 132 → 13 queries, output byte-identical), journal list
98 → 7 queries; `ix_transactions_live (company_id, currency, date) WHERE
deleted_at IS NULL`; gzip (front end 1.1 MB → ~270 KB) and a year's
`immutable` cache for `?v=` assets; CI guards that query counts do not grow
with the books. ✅ Per-language i18n packs: 02-i18n.js carries English only
(588 → 141 KB, 162 → 41 KB gzipped); fa, es and ar live in `js/i18n/<lang>.js`
(~40 KB gzipped each). `00-lang.js` loads the saved language's pack before the
core scripts, so a Persian page draws in Persian from the first paint; switching
language fetches the pack once, then saves the choice to the profile and redraws
the open page (the top-bar switch used to be overridden at the next sign-in and
left script-drawn tables in the old language). ✅ Dashboard folds in SQL
(`app/services/reporting/dashboard_folds.py`): months, book quality, expense by
account, vendors and clients are GROUP BY queries over one per-journal subquery;
only journals that move a receivable or a current liability come back one by
one, in date order, for the aging. The 13-week forecast's baseline is summed per
day in SQL too. On the 20k-journal bench the owner dashboard went 679 → ~380 ms
(28 → 27 queries), output byte-identical; `tests/test_dashboard_folds.py` keeps
the old line-by-line folds as references and checks them on random books. Fixed
on the way: aging read journals in no set order, so a receipt entered before
the sale it settles was dropped and the sale stayed overdue. §2.6 done.

**§3 continued — 2026-09-26:** ✅ 3.2 seasonal filings (#153): per Jalali
season the TTMS figures per counterparty (identity fields in Latin digits,
person type, returns netted with their VAT share, sales accepted in مودیان
left out, other currencies and incomplete parties listed), the VAT return
(output, input, returns, payable / carried forward) reconciled against it,
an RTL Excel workbook (خلاصه / فروش / خرید / صورتحساب‌ها / اطلاعات ناقص),
deadlines season end + 45 / + 15 days with reminders, Invoices → Seasonal tax
reports for Iranian companies. The TTMS program's native Access file is not
written; its figures are. Follow-up: aggregate small retail sales below the
حد نصاب, per-purchase "already in the taxpayer portal" flag.
✅ 3.6 UK Making Tax Digital, export first (#154, #155): VAT return boxes
1–9 from invoices (reverse charge in 1 and 4, credit notes netted, NI boxes
0) with the MTD VAT API body and CSV; ITSA quarterly updates for
self-employment / UK property in HMRC's categories (field names from HMRC's
published schemas), cumulative from April on the standard or calendar basis,
default mapping from the UK chart with per-account overrides, disallowable
entertaining/depreciation, consolidated expenses below the VAT threshold,
the mandation test (£50k/£30k/£20k two years earlier), request bodies,
workbook, deadline reminders. Later: direct HMRC submission (OAuth, fraud
prevention headers, obligations/periodKey), final declaration.

**§4 — 2026-09-26:** ✅ 4.1 bank SMS capture (#156): a format-agnostic parser
for Iranian bank SMS (bank by name, direction by keyword or sign, amount vs
balance vs masked/dashed account numbers, Jalali dates in five forms always
resolved to the past, تومان → rials, OTP codes ignored); pasted batches split;
rows filed into monthly SMS-feed statements per bank/account so the usual
review → approve pipeline applies; exact repeats skipped, balance gaps
flagged; `POST /bank-sms` (+ preview) and `POST /api/v1/bank-sms` with the new
`bank_sms:write` key scope for phone automations. Later: statement e-mail
ingestion, open-banking balances. ✅ 4.3 fixed-asset register (#159):
`app/services/fixed_assets.py` + `/fixed-assets` — asset cards (migration
048), straight-line and declining-balance schedules in the company's calendar
(Jalali months for Iran, starting the month after use; the 5 % rule), a
month-end run that posts each ended month once (closed months caught up on
the first open day), acquisition from bank or on credit, disposal with gain
(IR 4310 / UK 4200) or loss (IR 6220 / UK 7860), the register report, a
`get_fixed_assets` AI tool and a Records → Fixed assets page. Iranian presets
are only the art. 149 rows two sources agree on (buildings 25/15 years,
vehicles 6, taxis/light vans 4, computers and software 3, office furniture 5);
the rest is entered per asset. Later: the full table from the official
circular, revaluation, idle-asset (70 %) extension, impairment. ✅ 4.4 inventory
costing (#161): `app/services/inventory_costing.py` — weighted average (the
old running average, now half-up) or FIFO per company, always recomputed from
the movements; stock valuation as of any date with the other method's total
beside it; reorder point + quantity per item, a low-stock insight on the
dashboard; barcode field and lookup (unique per company); bills of materials
and production runs (components out at cost, finished item in at their total,
short components refused unless confirmed); `get_inventory` AI tool; the
Inventory page panels (migration 049). Later: automatic movements from goods
receipts and sales invoices (opt-in, to avoid double counting manual ones),
perpetual COGS postings, stock counts. ✅ 4.5 chart-of-accounts
management (#162): `app/services/chart_service.py` + `/accounts` — add under
a parent (code suggested, must start with the parent's; level follows GROUP →
GENERAL → SUB → DETAIL, i.e. گروه/کل/معین/تفصیلی), rename, deactivate
(zero balance, no active children, never a posting default; inactive
accounts refuse postings and leave the pickers and the AI's account search),
delete only if never used; the tree with rolled-up balances; opening balances
as one replaceable journal (`OPENING-BALANCES`, replaces the migration
opening too; a difference goes to 3999). Setup → Chart of accounts page
(migration 050). Later: moving an account to another parent, floating
تفصیلی groups shared across معین accounts. ✅ 4.10 part 1, installable
app (#163): web-app manifest + icons, a root-scoped service worker (`/sw.js`,
versioned by the static assets' hashes) that caches only versioned static
files and answers a failed page load with the offline page — never an API
response or the app page; a share target (a photo/PDF shared to the app lands
in the chat), a camera button in the chat on touch devices, big photos shrunk
to ≤ 2000 px JPEG before upload, "Install the app" in the account menu. ✅ Part 2,
web push (#164): `app/services/web_push.py` speaks VAPID (RFC 8292) and
aes128gcm payload encryption (RFC 8291 — reproduces the RFC's example) with
`cryptography` + `httpx`, no new dependency (pywebpush wants cryptography ≥ 47
and aiohttp); the key pair is made once and kept encrypted in the platform
setting `web_push_vapid` (or `VAPID_PRIVATE_KEY`); subscriptions only for the
major push services (no SSRF); the 15-minute notifications job pushes each new
open alert to the devices of the people who see it in the bell, > 3 at once as
one summary, gone devices removed, `notifications.pushed_at` so nothing goes
twice (migration 051 marks existing alerts pushed). "Phone notifications" in
the account menu. §4.10 done except a native app.

**§4 continued — 2026-09-27:** ✅ 4.11 historical journals (#166):
`app/services/journal_import.py` + `/migration/journals/{preview,review,apply}`
— one importer for the journal exports of Hesabfa, Sepidar, Holoo, Xero,
QuickBooks and any one-line-per-row sheet (xlsx, SpreadsheetML .xls, CSV/TSV):
header row found by column names in English and Persian (under a report title
too), preset guessed from the headers (QuickBooks: month-first dates, a filled
date starts the next voucher; Iranian: Jalali dates, Persian digits), every
detected column editable; amounts with thousands separators, parentheses,
trailing minus, CR; whole units rounded half-up with a rounding plug so a
balanced voucher stays balanced; accounts matched by code, then name, then the
user's remembered choice; unbalanced / closed-period / future / already
imported (`IMPORT-<preset>-<voucher>`) / unmapped vouchers reported, never
posted; parties linked as client or supplier from the account they sit on.
Later: vendor-specific samples as customers send them, opening balances from
the same files, sales/purchase invoices as documents.

**§4 continued — 2026-09-28:** ✅ 4.6, part 1 — rate feeds and rates per
company: `app/services/rate_feeds.py` fetches the shared rates once a day
(scheduler job `rate_feeds`, one run for the platform, hourly retries up to 3
when a source fails) from the ECB's daily reference file (keyless; stored as
crosses into USD, EUR, GBP and every company's reporting currency; AED on its
3.6725 peg) and from JSON feeds the platform admin configures in Settings →
Currency & FX (URL with the key encrypted at rest and masked on screen; per
item unit, currency, JSON path, multiplier — ×10 for toman; GOLDG/GOLDC for
personal gold holdings). "Test" lists every number in a feed with its path.
Feed URLs must be https and resolve to public addresses on every redirect
hop (no SSRF), 2 MB cap. A rate typed by hand for a day is never
overwritten. Migration 052: `exchange_rates.company_id` — rates were one
table for every company, so any accountant could change another company's
rates; now a company's own rows are private and, for a pair it has priced,
replace the shared ones; shared rows are the platform admin's. Codes widened
to 16 characters. Lookups cross through USD/EUR/GBP/IRR when a pair has no
rate. Caches keyed on the shared-rate version too.

✅ 4.6, part 2 — base-currency values (decision 2026-09-28: option 2, as
Xero/QuickBooks): `transactions.fx_rate` + `transaction_lines.base_debit /
base_credit` (migration 053). A `before_flush` hook in
`app/services/fx_base.py` fills them for every writer: rate 1 in the base
currency, else the voucher's rate, else the rate on file *on or before* the
date (company's own, shared, crossed) — fixed at posting; half-up with a
rounding unit taken back so every entry balances in base. No rate → NULL,
counted in `/fx/metadata.unconverted`, converted when a rate arrives (rate
entry, daily feeds, boot). Base currency change → everything reconverted.
`currency=ALL` in the repository helpers = every currency at base value:
ledger summary, account detail, trial balance/GL/journal, balance sheet, P&L,
cash flow, Iran/UK statements, transaction search. Revaluation rewritten:
base-only lines per foreign currency (`fx_role='revaluation'`), monetary
accounts only; pre-existing mirror revaluations neutralised
(`legacy_revaluation`). Default currency is the base currency everywhere (was
a literal IRR in posting, recurring rules, petty cash, invoices, adjustments,
AI proposals, bulk import). The placeholder USD→IRR 150,000 seed is gone.
✅ 4.6, part 3 — realised gain/loss (`app/services/fx_settlement.py`): a
payment or credit note on a foreign invoice clears the AR/AP line at the
invoice's rate (the last one takes exactly the base left, so part payments
never leave a penny), the rest at its own rate, and the difference on one
base-only line to `fx_gain` / `fx_loss` (UK 4210/7950, IR 6240/6230,
created on first use); `fx_role='settlement'`. Overpayment excess is a new
balance at today's rate. Settled when both rates are known — immediately, or
by `settle_waiting` after a rate arrives, an edit, a base change. Reversals
now copy the original's rate and base values, so voiding undoes the FX too.
Closed periods are never re-settled. `PaymentRead.realised_fx`.
✅ 4.6, part 4 — the rest of the readers: a caller that names no currency
now sums base values (`repository.sums_base`) — statements, cash flow, CFO
data, cash on hand; the AI tools (query_ledger / get_account_balance take an
optional currency, get_cash_position, spending summary) report in the base
currency and say which entries wait for a rate; insights, anomaly drift,
budgets, net worth, person balances on base values. Bank statements are
checked against entries in their own currency; recurring suggestions come
from base-currency entries only. The dashboard has the combined view
(default when the books hold several currencies). New entries written with
no currency get the base currency (the column default was "IRR"); Settings →
Currency & FX lists entries waiting for a rate and offers to relabel IRR ones
in a non-IRR company (`POST /fx/relabel`, closed periods untouched). Still
per currency by design: invoice-based reports (sales/purchase by product,
tax summaries, aging from invoices). §4.6 done.

**§5 continued — 2026-09-28:** ✅ 5.4 correction memory
(`app/services/learned_preferences.py`, table `learned_preferences`,
migration 054): the normalised narration → account and/or party the user
chose when they overruled a suggestion — a statement row approved with
another account, an entry recategorised (one account swapped for another at
the same amount; restructurings teach nothing) or given another party (both
edit routes), the assistant told "always …" (`propose_remember_preference`,
a confirm card), or added by hand. Used first by the statement categoriser
(source `learned`, any nature — a refund back to its expense is right; an
inactive account is skipped), by `search_accounts` / `find_entity` (new
`description` input; the match is marked `learned` with a note) and in the
assistant's prompt (top 15). Matching: same narration, else ≥ 60 % word
overlap, else every learned word present. Chat page → "What I've learned"
lists and forgets them (`/ai-accountant/preferences`). Undo/Reverse now only
on confirmations that posted an entry.

✅ 5.7, part 1 — voice notes (`app/services/speech.py`,
`POST /ai-accountant/transcribe`): the chat's microphone button records
(MediaRecorder, 2 min max) and the text lands in the input to check and send.
Gemini through Metis first (`STT_GEMINI_MODEL`), then the active backend's
OpenAI-compatible `/audio/transcriptions` (`STT_MODEL`,
gpt-4o-mini-transcribe); format sniffed from the bytes, 10 MB cap, metered as
purpose `speech`, budget-guarded; audio never stored.
✅ 5.7, part 2 — Telegram / Bale bot (`app/services/messenger.py`,
`app/api/bots.py`, migration 055): one adapter for both (Bale speaks the
Telegram Bot API at tapi.bale.ai). The platform admin pastes a token
(Settings → Messenger bots): getMe + setWebhook to
`APP_PUBLIC_URL/bots/<platform>/webhook/<secret>` with the same
secret_token (both checked; wrong → 404); token encrypted in platform setting
`messenger_bots`. Users link a private chat from the account menu with a
one-time 10-minute code (`/start <code>`); a message is one assistant turn
as that user (company scope, actor for the AI budget, chat permission
re-checked, suspended companies refused), a voice note is transcribed first,
proposals come back with Confirm/Cancel inline buttons (callback executes as
the linked user, someone else's card refused, buttons removed after). /new,
/stop. Groups and unlinked chats get instructions only; re-delivered updates
ignored (`messenger_updates`); the webhook returns at once and handles the
update in the background. WhatsApp left out (needs Meta business
verification). §5.7 done.

**§3 continued — 2026-09-28:** ✅ 3.5 Jalali everywhere in reports
(`app/services/calendar_periods.py`): everything that buckets money by period
asks the company's display calendar (Jalali by default for `ir`). Month keys
are `"YYYY-MM"` in their own calendar — a year below 1700 is Jalali — so a key
says which calendar it is in and budgets saved before keep their Gregorian
months. Jalali months in budgets (the month picker lists them), budget alerts,
the dashboard's monthly series (with a `label`), balance-sheet / cash-flow /
sales trend periods (seasons are Jalali quarters, weeks start on Saturday),
insight comparisons, the CFO report, the ledger tool's `group_by=month` and
the payroll year summary (year 1405 = 21 Mar 2026 – 20 Mar 2027). Charts label
periods "Mehr 1405" / «مهر ۱۴۰۵». Found on the way: roles without settings
access (accountant, manager, employee, viewer) could not read the display
calendar and saw Gregorian dates in an Iranian company; report and invoice
date defaults were UTC, a day early in Tehran before 03:30; the budget table
did not reload on a month change and its headers were English only.

✅ Statements for the seeded Iranian chart (#176): the balance sheet and
cash-flow statement mapped accounts by the standard's 3-digit groups, which
the seed's 4-digit codes don't follow — VAT receivable showed as short-term
investments, accrued income as inventory, wages payable as liabilities of
assets held for sale, paying an accrued expense as a dividend. Seeded codes
are pinned first; petty cash is cash in both statements; cash in the flows is
the balance sheet's cash line, so opening + flows = closing.

✅ 3.4 cheque lifecycle (`app/services/cheques.py`, migration 056): received
cheque in hand → deposited (در جریان وصول) → cleared / bounced → deposited
again or returned (عودت); passed on to a supplier (خرج چک, optionally paying a
bill); issued cheque → cleared / bounced (presented again) / returned. In
Iranian business books (`ledger_mode = "notes"`) each step posts through
notes receivable 1113, cheques in collection 1114 and notes payable 2111
(posting categories, pinned on the statements as receivables / payables); a
cheque for an invoice is that invoice's payment (method `cheque`), a bounce or
a return takes the payment off so the invoice reopens, and the invoice
screen refuses to reverse or void it. A bounce moves the claim to the party
(AR/AP), never back into an expense or income account. UK and personal books
and older cheques post only when the cheque clears (`"direct"`). Sayad id
(16 digits, Persian digits accepted, unique) and registration date; a nudge
to register (issued) or confirm (received) in the month before it is due.
`commitment_events` is each cheque's history. Deposited cheques count in the
cash forecast; an explicit invoice link wins over the party/amount guess. AI:
`propose_create_cheque` takes the Sayad id and invoice, `propose_cheque_step`
deposits, returns and passes on.

✅ 3.4, part 2 — printing an issued cheque (`app/services/cheque_print.py`,
template `documents/templates/cheque.html`): a WeasyPrint PDF the size of the
leaf (Sayad 175 × 80 mm, UK 178 × 80 mm) with only what the drawer writes —
date in figures and (Iranian) in words («پنجم مهر ماه یک هزار و چهارصد و
پنج»), payee and national id, amount in words and in guarded figures
(#۱۲٬۵۰۰٬۰۰۰#); long lines shrink to their box. The layout is the company's
(app_settings `cheque_print_layout`: page size, global offset, each field's
box in mm, validated to stay on the leaf) because leaves differ by bank; a
guide print (outline + field names) on plain paper is how it is calibrated,
and a test print needs no cheque. Real prints go in the cheque's history.
Issued cheques only. §3.4 done.

✅ 3.7 UK FRS 102 statements from the ledger (`reporting/uk_statement_service.py`):
every seeded UK account is placed on purpose and each group ends in a
catch-all, so user-added accounts land on their line — accrued income (1410),
supplier prepayments (1500) and the opening-balance adjustment (3999) used to
drop off and the balance sheet stopped balancing. P&L sums signed (sales
returns reduced nothing: each account was clamped at zero), FX gains are other
operating income, and operating profit is "stated after charging"
depreciation and amortisation. OCI = the revaluation reserve moved against the
assets. Changes in equity: every movement for both years from the ledger
(profit, OCI, shares issued, dividends, transfers between reserves, other),
the comparative year chaining to the opening; the opening and closing
balances had always been zero (a tuple unpacked the wrong way round). Cash
flow: dividends paid (2750 or straight from the reserve), directors' loans,
overdraft, share issues as one line, and the reconciliation of operating
profit to cash generated from operations (depreciation, amortisation,
fixed-asset gains/losses, stocks, debtors, creditors, provisions) with any
difference shown as its own line. §3 done except مودیان phase 2 and HMRC
direct submission.

✅ 5.6 guardrails (`app/services/ai_accountant/guardrails.py`, migration 057):
every proposal passes `guardrails.review` in the orchestrator right after the
tool makes it (whichever of the seven creation paths) — one dated in a closed
period is cancelled and the model told why (only the entry tool checked
before; the posting path still refuses at execution); its base-currency
amount and summary are stored. Two-person approval: the owner's threshold
(`ai_guardrails` setting, Settings → AI approvals, lists who can approve);
at or above it the requester's Confirm (chat or messenger) returns 202 and
the proposal waits up to 7 days for someone else with `approvals:write`
(owner/CFO/manager) — "Waiting for approval" in the chat sidebar and an
"Approval needed" bell item; approve executes as the approver and writes an
append-only `approve` audit row naming both, reject cancels with a note. An
amount without a rate counts as over. Budget per message: `tool_calls_per_message`
(60) and `proposals_per_message` (20) in platform `ai_limits`; past them the
calls get error results, and a model that keeps calling is stopped with every
call answered so the next request stays valid.

✅ 5.5, part 1 — the eval set (`app/services/ai_eval/`): 17 fa/en scenarios in
`scenarios.json`, each with what a good turn does (tools, tool order, cards and
their total / bank side / date / fields, reply language, the figure the reply
must quote) and a recorded good trajectory; `fixture.py` seeds the books they
run against. Every CI run replays each trajectory through the real agent loop
and tools with no model (`tests/test_ai_eval.py`), so a tool or card change
that breaks a scenario fails the build. Nightly `.github/workflows/ai-eval.yml`
runs the set 3× against the live model (secret `AI_EVAL_API_KEY`) and compares
with the last good run: a scenario that passed ≥ 2/3 and now passes < 1/2, any
failure of a critical one (refusals), or the shared pass rate falling 10 points
fails the job; `scripts/ai_eval.py` runs it by hand on a scratch database and
compares models (`scripts/model_eval.py` keeps the OCR bench). The first replay
found the statement-review button's own message refusing its cards: the
statement's UUID digits counted as "source amounts", so every row card was
`amount_mismatch` — identifiers are no longer amounts, and a card that settles
a statement row is checked against that row.

✅ 5.5, part 2 — the review queue (`app/services/ai_review.py`, migration 058):
about one assistant turn in ten, web chat and the Telegram/Bale bots, is kept
as a snapshot (question, answer, each tool call with its input and whether it
worked, the cards, model, latency). Settings → AI review lists them for the
owner (`Perm.AI_REVIEW`, owner-only): good / needs work with a note, back to the
queue, discard, or download as a draft eval scenario (a good turn's trajectory
becomes the replay; the format drops into `scenarios.json` after adapting names
and ids). Privacy: samples never leave the company — the platform console shows
counts per verdict and model, never text; a sample cascades with its
conversation, the daily `ai_review_purge` job drops it after 90 days, the owner
can switch sampling off, personal tenants are never sampled, and the users who
chat are told (release note). §5.5 done.

✅ 4.7 budgets: `PATCH /budgets/{id}` (amount, category, month; one per
category per month, audited); `POST /budgets/roll-forward` copies a month's
budgets into the next 1–12 months of the company's calendar (Jalali keys roll
Esfand → Farvardin), changed by a percentage of the source (not compounded,
half-up, never below 1), keeping categories a month already has unless
`overwrite`; Edit / Delete on each dashboard row and "Copy to next month…".
Per-project budgets (migration 059: `projects.budget_hours`, `budget_amount`):
`PATCH /time/projects/{id}`; `GET /time/project-budgets` (books roles — the
picker `/time/projects` stays open to employees) gives hours logged (work and
travel) and fees — invoiced time at the rate billed, unbilled at today's rate,
written-off none, other currencies or no rate as "unpriced" — in one query
with rates resolved once per worker/client/project; Time → Projects and
budgets; bell alerts at 85 % / over. Jalali months were done in §3.5.

✅ 2.7 locked dependencies: `requirements.lock` / `requirements-dev.lock` /
`requirements-e2e.lock` (uv, CPython 3.12 on linux x86-64, every file's
hash; the runtime lock constrains the other two). The Dockerfile and every CI
job install them with `--require-hashes`; `requirements*.txt` keep the ranges
for the dev stack, and `scripts/lock-deps.sh [pkg …]` re-pins.
`tests/test_dependency_lock.py` fails when a lock falls outside its ranges,
misses a hash or drifts from the runtime lock. pypdf's cap raised (4.3 →
6.19). `offline-deploy.sh` now builds the image and ships only the images,
`docker-compose.prod.yml`, `.env.prod.example` and the backup scripts (it
used to tar the whole tree — `./backups` dumps included — and start the dev
stack); `.dockerignore` keeps dumps, SQL and `backups/` out of the image.

**§5 — 2026-09-27:** ✅ 5.2 anomaly detection as insights (#157):
`app/services/anomaly_detection.py` — duplicate supplier payments (same
amount within a week or same reference), payments/expense claims split just
under the approval threshold (or under a round number), a new supplier's
large first payment, round-amount weekend entries (Friday in Iran), expense
category drift (≥10 points of spending share), reversal runs per
counterparty; in the dashboard feed and the chat through the existing
insight tool. ✅ 5.3 13-week cash forecast that learns (#158):
`app/services/cash_forecast.py` — opening cash on hand; open sales invoices
on each customer's learned payment date (due + median lateness over the last
year, company median as fallback), bills on their scheduled date, pending
cheques/installments, unpaid pay runs + next months' payroll, recurring
rules and recurring invoices; plus the median unscheduled week of the last
26 without the journals those sources explain. Scenarios (bounce, unpaid
invoice, late payer, one-off) via `GET /reports/cash-forecast`,
`POST /reports/cash-forecast/scenario`, the `get_cash_forecast` AI tool
("what if the Mellat cheque bounces") and the dashboard's what-if explorer;
the dashboard table now shows the same figures.

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
retry (#130). §4.2 done except SMS (no provider chosen yet). ✅ 3.1 phase 1:
مودیان export for a trusted provider — 22-char tax number (Verhoeff), official
JSON packet, readiness checks, stable per-company serials, result tracking,
confirmed-invoice lock, 12-day deadline alerts. Phase 2 (direct API with the
company's signing key, return/cancel invoices) is still open (#131). ✅ boot fix:
append-only audit_logs skipped by the Default-company backfill (#132). ✅ 2.2
shared state: login/sign-up/resend/chat limits in Postgres, books version for
the dashboard + insights caches, tenant-scoped upload tokens, AI config
refresh across workers, scheduler tick behind an advisory lock;
`WEB_CONCURRENCY` in the prod compose (DEPLOY.md §9) (#133). ✅ fresh installs
get the audit guards (#134).

**§6 test suites 4–8 — shipped 2026-09-25, each with the defects it found:**
✅ 4 equity (#135: revaluation-surplus account on the Iranian chart, dividends
paid beyond declared, mixed percent/share weights, deleting an owed holder,
half-posted declarations, closed-period PUT with a wrong key cleared the lock) ·
✅ 5 payroll lifecycle (#136: overlapping runs paid a salaried employee twice) ·
✅ 6 manager reports (#137: AR/AP aging only saw this month's invoices,
mislabelled buckets, drafts as receivables, voided invoices counted as sales,
inventory list price dropped) · ✅ 7 FX (#138: half-to-even rounding and float
products on money, net-zero revaluations posted nothing) · ✅ 8 uploads (#139:
raw filename in the Excel temp path, import history never recorded, temp files
leaked, logo/signature/OCR trusted the content type, order-dependent inventory
on-hand; tests can no longer reach the network). Remaining: suites 9–10,
coverage gate, Playwright smoke suite. ✅ 2.4 observability: request id in a
context variable on every log line and response (also 401/429 answered by
the auth middleware, which were never logged), JSON logs in prod, 500s answer
with the request id, token-gated `/metrics` (HTTP by route template, LLM
calls, jobs; multi-worker aggregation), optional Sentry/GlitchTip with
scrubbing (DEPLOY.md §10) (#141). ✅ suites 9–10: Jalali edges (#142: Nowruz
year guessing, Arabic-Indic digits, Excel full codes, invalid Jalali days
parsed as Gregorian) and time billing + fees (#143: two-commit invoicing from
time, float/half-to-even fees). ✅ coverage gate: CI fails below 80 % of app/
(82 % at introduction). Least covered: demo_data 0 %, products API 13 %,
recurring API 43 %, transaction_chat 45 % (#144). ✅ Playwright smoke suite
(tests_e2e/, CI job "Browser smoke"): a CI-only owner clicks through all 23
owner pages and the Persian invoices page; any JS exception or 5xx fails.

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
| 2.7 | ✅ (2026-09-28) Dependency lock file with hashes (uv/pip-tools); raise `pypdf<5` cap; `offline-deploy.sh` must use the prod compose and exclude `*.dump`, `*.tgz` | prod and dev differ; a local DB dump could ship inside the image | `requirements.txt`, `.dockerignore`, `scripts/offline-deploy.sh` |

## 3. Compliance features (market entry blockers)

### Iran
| # | Item | Notes |
|---|---|---|
| 3.1 | **سامانه مودیان e-invoicing** — the #1 gap vs Hesabfa/Sepidar/Holoo. Since آذر 1404 paper invoices have no tax validity. Needs: per-company شناسه یکتای حافظه مالیاتی, 22-char شماره منحصربه‌فرد مالیاتی generator, 13-digit شناسه کالا/خدمت on products, invoice patterns (نوع ۱ B2B / نوع ۲ B2C, الگوها), signing with the company's private key/CSR, submission (direct or via a trusted provider such as the ones Mahak/Sepidar bundle), status tracking (pending / confirmed / rejected), 12/20-day deadline reminders as notifications, resend on rejection | New module `app/services/moadian/`, fields on `Company`, `Invoice`, `Product`; start with file export for a trusted provider, then direct API |
| 3.2 | **گزارش معاملات فصلی (ماده 169) — TTMS export** of purchases/sales per quarter (45-day deadline) reconciled to the VAT return; **اظهارنامه ارزش افزوده** quarterly figures (15-day deadline) from `tax_summary` | `app/api/reports.py:770` tax summary → add TTMS file layout + a "quarter close" checklist item |
| 3.3 | **Payroll 1405 parameters as data, not code**: minimum wage 5,541,850/day, حق مسکن 30,000,000, بن 22,000,000, حق اولاد, سنوات, insurance 7%/23% with the ceiling, income-tax brackets (exempt to 480 M/yr, 10/15/20/25/30 %), overtime 1.4×, عیدی 2–3× | `payroll_service.py` → a versioned `payroll_rules` table per Jalali year + UI to edit; **لیست بیمه (تامین اجتماعی) and salary-tax file exports** |
| 3.4 | ✅ **Cheque handling like Iranian books expect** (2026-09-28, lifecycle #177, print): چک دریافتی/پرداختی lifecycle (in hand → deposited → cleared / bounced → returned), صیاد ID field, cheque print layout, reminder on sayad registration | extends `commitments` (already bounced ≠ settled) |
| 3.5 | ✅ **Jalali everywhere in reports** (2026-09-28): monthly buckets, budgets and `year-summary` use Gregorian months for `ir` companies | `manager_reports.py:671`, `budget_service.py:26`, `payroll.py:681` |

### UK
| # | Item | Notes |
|---|---|---|
| 3.6 | **MTD for Income Tax (April 2026)** — quarterly updates for sole traders/landlords over £50k (£30k in 2027): digital records tag (self-employment / UK property), quarterly income/expense summary in HMRC's categories, export or (later) HMRC API submission; MTD VAT return figures (boxes 1–9) from the tax summary | new `app/services/uk_mtd/`; FreeAgent/Xero parity |
| 3.7 | ✅ UK FRS 102 statements (2026-09-28): real depreciation/amortisation lines, OCI, dividends paid (currently placeholders) | `reporting/uk_statement_service.py:12,489,615,714` |

## 4. Product features (what customers compare against)

| # | Item | Why / competitor reference |
|---|---|---|
| 4.1 | **Bank feeds without a bank API**: parse bank SMS / push notifications forwarded by the user (Mahak does this on Android), plus scheduled statement e-mail ingestion; later Finnotech-style open-banking for balances/statements | Iranian banks have no Plaid; SMS capture is what personal-finance users expect |
| 4.2 | **Quotes / پیش‌فاکتور → invoice**, recurring invoices with auto-send, invoice e-mail/SMS with a "pay by card-to-card / payment link" line, **automatic overdue reminders** (Xero default: 3 reminders) | AR collection is the most-cited SME pain; nothing e-mails invoices today |
| 4.3 | **Fixed-asset register**: asset cards, depreciation methods (straight-line exists as an adjustment), disposal, Iranian tax useful-life table | adjustments exist but there is no register or disposal |
| 4.4 | **Inventory costing** (weighted average / FIFO), stock valuation report, reorder alerts, barcode field; production/BOM light | Hesabfa/Holoo core; ours is movements + average price only |
| 4.5 | **Chart-of-accounts management**: create / rename / deactivate accounts, تفصیلی groups, opening balances UI | accounts are only created implicitly today |
| 4.6 | **Multi-currency close**: unrealised FX revaluation exists; add realised gain/loss on settlement, rate feed (manual today, XE-style hourly for GBP/EUR/USD, a gold-price feed for personal holdings) — ✅ feeds + per-company rates 2026-09-28; realised gain/loss open | Xero parity; personal tenants hold gold/FX |
| 4.7 | ✅ (2026-09-29) **Budgets**: edit route, Jalali months, per-project budgets, roll-forward | `budgets.py` has no PATCH |
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
| 5.5 | ✅ (2026-09-28/29) **Evaluation harness**: turn `scripts/model_eval.py` into a CI-able eval set (fa/en scenarios, expected tool trajectory + card contents), run nightly against the configured model, alert on regressions; sample 10 % of prod turns into an offline review queue (no PII beyond the tenant) | model/prompt changes are only checked by hand today |
| 5.6 | ✅ **Guardrails** (2026-09-28): max proposal amount vs source amounts already exists — add per-company confirm thresholds (two-person approval above X), refuse to post into closed periods from chat (server-side), tool-call budget per turn | agent safety |
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

Plus: `--cov --cov-fail-under=80` in CI; a Postgres CI job that runs `alembic upgrade head` and the suite; a 10-page Playwright smoke suite for the five JS files no test reads (`05-reports-manager`, `08-entities-invoices`, `11-time-expenses-payroll`, `12-ops`, `13-companies-products`); property tests for `balance_from_turnovers` / `resolve_period` (✅ 2026-09-29, `tests/test_properties.py`: seeded random inputs, thousands per property — balances flip and add, balanced journals over both seeded charts net to zero, every code has one nature, `convert_minor` = the exact product rounded half-up to 10^18, `to_base` keeps a balanced entry balanced with whole-unit nudges, report periods tile both calendars with no gap or overlap; no defects found); a nightly AI eval (5.5, ✅).

Known defects found by the audits and **not yet fixed** (small, worth branches now):
- ✅ logo/signature uploads trust the browser `content_type` — fixed in #139;
- ✅ rate limiter memory never freed — fixed in #120;
- ✅ `_EXCEL_UPLOAD_STORE` temp files never cleaned, token contains the raw filename — fixed in #133/#139;
- ✅ `/health` returns 200 while degraded — fixed in #123;
- ✅ login/logout have no CSRF/Origin check — fixed in #119;
- ✅ Excel import confirm and bulk JSON import bypass the closed-period lock — fixed in #110.

## 7. Suggested order of work

1. **Now (this week)**: 1.1 uploads behind auth · 1.2 sessions · 1.3 change-password · 1.5 closed-period on all paths · 1.9 backups · 1.10 deploy gate · 2.1 scheduler · test suites 1–3.
2. **Next**: 3.1 مودیان (start with export for a trusted provider) · 3.3 payroll rules table + بیمه export · 4.2 quotes/reminders/e-mail · 5.1 invoice + commitment tools · 2.2/2.3 shared state + workers · test suites 4–8.
3. **Then**: 3.2 TTMS/VAT · 3.6 MTD ITSA · 4.1 SMS bank capture · 5.2/5.3 anomalies + forecast · 4.3–4.5 · 4.10 PWA · 2.4 observability · Playwright + Postgres CI.
4. **Later**: 4.11 importers · 4.6 FX feeds · 5.4 correction memory · 5.7 messenger bots · 2FA · API scopes.
