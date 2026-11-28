# AI Accountant — developer notes

The AI accountant is a conversational bookkeeper that exposes a typed
tool catalogue to an LLM — 71 tools in all, 60 in a company's books and
38 in a person's. The LLM never writes to the books directly — every
write goes through a proposal → confirmation → execute loop with
idempotency tokens. A card waits 24 hours for its Confirm; once
confirmed, a 120-second **Undo** soft-deletes the entry, and **Reverse**
(no time limit) posts a compensating entry.

Not every message reaches the model: a blank turn, a "confirm" typed
under a waiting card, a bare file path, a chart or journal spreadsheet,
a bank statement and a handful of common questions ("how much cash?",
"who owes us?") are answered deterministically, without a model call.

The agent is **provider-neutral**: it works in a normalized
`ChatMessage` / `LLMResponse` vocabulary and dispatches to an
`LLMClient` adapter that speaks the wire format of whichever provider
is active (Anthropic Messages API or OpenAI Chat Completions). Adding
a new provider is ~150 LOC.

## Architecture (one screen)

```
 POST /ai-accountant/chat  (app/api/ai_accountant.py)
   before the model — each step can answer on its own, no model call:
     1. blank message, no file → 422
     2. «ولش» / "never mind" → the turn before's waiting cards are set aside
     3. "confirm" typed while a card waits → "use the card's button"
     4. a message that is only a file path → "attach the file itself"
     5. a spreadsheet: chart export / journals → confirm card;
        a bank statement → imported, review card
     6. a common question (fast_paths.py) → answered by the read tool
     7. guard_ai_request: rate limits + 24-hour token budget
     8. a statement PDF/image → imported (vision), review card;
        another image/PDF → OCR'd, its fields go to the model as context
                                    │
              ┌─────────────────── orchestrator ───────────────────┐
              │  run_chat_turn(db, user_id, user_message, …)       │
              │  tool_routing: core tools + the groups the message │
              │    is about (+ more_tools), and only the prompt    │
              │    sections those groups need                      │
              │  loop until stop_reason == "end_turn" (MAX_TURNS): │
              │    1. send (system + tools + history) via          │
              │       LLMClient.chat(…)   ◀── dispatches by shape  │
              │    2. for each tool_call, run the tool             │
              │       (a proposal also passes guardrails.review)   │
              │    3. append tool_result(s), repeat                │
              └─────────────────────┬──────────────────────────────┘
                                    │
              ┌───── LLMClient ─────┴──────────────────────┐
              │ AnthropicLLMClient (shape='anthropic')     │
              │   wraps anthropic_client.chat_once         │
              │   translates ChatMessage ⇄ content blocks  │
              │   keeps cache_control markers              │
              │                                            │
              │ OpenAILLMClient    (shape='openai')        │
              │   httpx → /v1/chat/completions             │
              │   translates ChatMessage ⇄ messages array  │
              │   handles tool_calls / role:'tool' shape   │
              └────────────────────────────────────────────┘
                                    │
              ┌─── tool catalogue (71) ┴─────────────┐
   read       │ find_entity  search_accounts         │
   tools      │ query_ledger  get_cash_position  …   │     ai_proposals
                                                      (status: pending)
   proposal   │ propose_create_transaction           │ ─────────────────
   tools      │ propose_record_invoice_payment  …    │ → confirmation_token
              └──────────────────────────────────────┘

         ┌───────────────── HTTP execute (frontend only) ────┐
         │ POST /ai-accountant/execute  (idempotent)         │
         │   • refuses a card older than 24 h (card_life)    │
         │   • dispatches on proposal.tool_name               │
         │     (execute_service.execute_proposal)             │
         │   • writes audit_logs row (actor_source='ai-…')    │
         │   • flips ai_proposals.status='executed'           │
         └────────────────────────────────────────────────────┘

         ┌─────────── HTTP undo (frontend only, 120 s) ──────┐
         │ POST /ai-accountant/undo  → undo_action            │
         │   • soft-deletes the entry (deleted_at); only an   │
         │     entry in a closed period is reversed instead   │
         │   • paired audit_logs row (action='undo')          │
         └────────────────────────────────────────────────────┘

         ┌─────────── HTTP reverse (any time later) ─────────┐
         │ POST /ai-accountant/reverse  → reverse_action      │
         │   • posts a compensating entry, no time limit      │
         │     (execute_service._perform_reversal)            │
         │   • paired audit_logs row (action='undo')          │
         └────────────────────────────────────────────────────┘
```

Key invariants:

* **The LLM can't write.** Proposal tools only persist a row to
  `ai_proposals` — they never touch transactions / invoices / entities.
  Holds for both providers; the orchestrator validates `category=="proposal"`
  before treating a result as a confirmable action.
* **The user authorises.** The frontend calls `/ai-accountant/execute`
  on Confirm. Server-side it checks the proposal belongs to the
  requesting user, isn't expired (older than 24 hours —
  `PROPOSAL_TTL` in `card_life.py`; a card waiting for a second
  person's approval keeps the approval's own clock), and isn't already
  executed. A card the user set aside («ولش», "never mind"), or one a
  later card already recorded, is refused.
* **Undo and Reverse never edit the original lines.** The quick Undo
  (`undo_action`, within `UNDO_WINDOW` = 120 s) soft-deletes the entry
  so the books read as if it was never recorded; an entry dated in a
  closed period is reversed instead. After the window, Reverse
  (`reverse_action`, no time limit) posts a compensating entry. Both
  refuse an entry already undone or reversed.
* **Single source of truth for audit.** Every successful execute
  writes exactly one `audit_logs` row with
  `actor_source='ai-assistant'`, `tool_name`, `confirmation_token`,
  `session_id`, and `user_message`.
* **Storage is provider-agnostic.** `ai_chat_messages.content` holds
  `ChatMessage.to_dict()` JSON, not the wire-format of any specific
  vendor — so a session keeps replaying correctly even if the active
  provider changes between turns. (Legacy Anthropic-block rows from
  before this refactor are skipped on replay; users on old sessions
  can /reset.)

## Before the model: deterministic turns and fast paths

`POST /ai-accountant/chat` runs the steps in the diagram in that order.
Each of steps 1–6 can end the turn with a **deterministic turn**
(`_deterministic_turn`): the user's message and the reply are stored in
the conversation like any other, but no model is called and no tokens
are spent. Only from step 7 on (`guard_ai_request`) does a turn count
against the per-minute limits and the 24-hour token budget.

**Fast paths** (`fast_paths.py`, roadmap ROADMAP_ANDROID_CHAT P0.6)
answer eight common questions from the books with the same read tools
the model would call: cash, receivables, payables, budget, this
month's spending, net worth, the categories, and the cash forecast. The
answer is exact, comes back in well under a second, and works when the
AI is down; the tool results ride along as `tool_calls`, so a client
draws them as blocks like any other reply. The match is deliberately
narrow: at most 80 characters, no digits (an amount means "record
something"), no action words ("and record the rent"), not two
questions joined, nothing about the future (except the forecast
itself), and not a reply to an earlier message. Anything else goes to
the model.

A statement imported in steps 5 or 8 goes to the model only when the
user's message says what to do with its rows (`_statement_handoff`,
scenario AF2); otherwise the import's own summary is the reply.

## Tool routing: only the tools a message needs

Sending every tool on every call cost ~13–20k tokens of tool schemas,
plus ~11k of instructions, for "I had lunch for 7 Omani rials".
`tool_routing.py` (on by default; `AI_TOOL_ROUTING=false` offers every
tool) sends:

* the **core** — `find_entity`, `search_accounts`,
  `propose_create_transaction`, `ask_with_choices`, `query_ledger`,
  `get_account_balance`, `propose_reverse_transaction`,
  `get_company_defaults` — always, first;
* the **groups** the message is about, matched by keyword in Persian,
  English, Spanish and Arabic (`KEYWORDS`), plus the groups of the
  tools the conversation just used and, with an attachment, invoices,
  statements and parties. There are 15 groups: money, counting,
  spending, planning, parties, statements, commitments, invoices,
  time, equity, payroll, operations, close, memory, dates (a person's
  books have only the first seven and the last two);
* **`more_tools`**, always last: the model calls it with the groups it
  finds missing and gets them in the same turn, so routing never leaves
  it without a tool.

The system prompt is routed the same way: a section tied to a group in
`SECTION_GROUPS` is sent only with that group; every other section
always is. Core first and groups in a fixed order keep the start of
what is sent the same from one message to the next, so a provider's
prompt cache still serves it.

## File map

```
app/
├─ api/
│   ├─ ai_accountant.py            # POST /chat (+ the pre-model steps), /execute, /undo, /reverse, /briefing,
│   │                              #   /reviews, /transcribe; sessions, approvals, preferences, review samples
│   └─ admin.py                    # /admin/anthropic-config, /admin/chat-provider-shape
├─ services/
│   ├─ ai_usage.py                 # metered_llm (every provider call), guard_ai_request (limits + budget)
│   ├─ ocr_extract.py              # the vision OCR chain for receipts, invoices and statements
│   └─ ai_accountant/
│       ├─ llm_protocol.py         # ChatMessage, ToolCall, LLMResponse, LLMClient (abstract)
│       ├─ anthropic_client.py     # AsyncAnthropic + AnthropicLLMClient (with prompt caching)
│       ├─ openai_client.py        # OpenAILLMClient (httpx, /v1/chat/completions)
│       ├─ base.py                 # BaseTool, ToolContext, ToolRegistry, ToolError
│       ├─ orchestrator.py         # run_chat_turn(), SYSTEM_PROMPT, the two registries, _resolve_chat_shape()
│       ├─ tool_routing.py         # CORE, GROUPS, KEYWORDS, more_tools, SECTION_GROUPS
│       ├─ fast_paths.py           # common questions answered without the model
│       ├─ card_life.py            # PROPOSAL_TTL (24 h): when a card can still be confirmed
│       ├─ guardrails.py           # every proposal: closed periods, two-person approval threshold
│       ├─ execute_service.py      # execute_proposal(), undo_action() (soft-delete), reverse_action()
│       ├─ read_tools.py           # find_entity, search_accounts, query_ledger, statements, tax, …
│       ├─ proposal_tools.py       # propose_create_transaction (+ bank_statement_row_id), entities, reverse
│       ├─ *_tools.py              # one module per area — cash, spending, insights, invoices, commitments,
│       │                          #   time, equity, payroll, periods, ops, assets, inventory, personal,
│       │                          #   afford, memory, review, dates, statements
│       ├─ *_execute.py            # Confirm handlers per area (invoice, time, equity, payroll, ops)
│       ├─ money_places.py         # a person's money places, currency exchange, ask_with_choices
│       ├─ statement_intake.py     # chat drop of a statement PDF/image/sheet → import + review card
│       └─ file_intake.py          # spreadsheets dropped in the chat (chart export, journals)
└─ models/
    ├─ ai_accountant.py            # AIProposal, AIChatSession, AIChatMessage
    └─ audit_log.py                # AuditLog (with new columns from migration 005)

tests/
├─ test_ai_accountant_read_tools.py        # read tools
├─ test_ai_accountant_flow.py              # proposal → execute → undo / reverse
├─ test_ai_accountant_orchestrator.py      # mocked-LLMClient loop
├─ test_ai_accountant_openai_client.py     # wire translation + httpx
├─ test_chat_provider_shape_endpoint.py    # shape selector + auto-detect
├─ test_tool_routing.py                    # routing (scenarios AX1–AX5)
├─ test_chat_fast_paths.py                 # fast paths
├─ test_ai_guardrails.py, test_ai_usage.py # guardrails; metering, limits and budgets
└─ test_ai_eval.py                         # the eval set's recorded trajectories, replayed
```

## Adding a new tool

1. Define a Pydantic `InputSchema` and a `BaseTool` subclass in the
   module for its area (`read_tools.py` for a pure query,
   `proposal_tools.py` or the area's `*_tools.py` for one that writes a
   pending proposal). Set `category = "read" | "proposal"`.
2. Implement `async def run(self, ctx: ToolContext, args: InputSchema)
   -> dict[str, Any]`. Raise `ToolError(msg, code=...)` for clean
   user-facing failures. Anything else becomes a 502 in the chat
   panel.
3. Register it in its module's `register_*_tools()`, and make sure that
   is called from `build_default_registry()` and/or
   `build_personal_registry()` in `orchestrator.py`.
4. Put it in `tool_routing.CORE` or in one of `tool_routing.GROUPS`
   (`test_ax4_every_registered_tool_is_core_or_in_a_group` fails
   otherwise). A new group needs its keywords in `KEYWORDS`.
5. **If the tool is a new proposal type**, add its branch to the
   `proposal.tool_name` dispatch in `execute_service.execute_proposal`
   (or to the area's `*_execute.py`) — a proposal without an executor
   can't be confirmed.
6. Add unit tests in `tests/test_ai_accountant_*` covering happy path
   + at least one validation rejection.

The system prompt's last paragraph already nudges the model toward the
right tool by category; you usually don't need to edit it when adding
new read tools. New proposal tools should be summarised in the
`# Resolution loop` section of `SYSTEM_PROMPT` (in `orchestrator.py`),
or in the section of their area — and a section about one area belongs
in `tool_routing.SECTION_GROUPS`.

## Tuning the system prompt

`orchestrator.SYSTEM_PROMPT` is a single string of `# ` sections, sent
whole except for the sections `tool_routing.SECTION_GROUPS` ties to a
group the message isn't about. The instructions and the company's
standing lines (learned preferences) come first and stay the same from
call to call; today's date and the message's language are appended
last — the date used to sit at the top and changed the whole prompt
every day. Because that start is the cached prefix, **any byte change
invalidates the cache** for every subsequent request. Two implications:

* Don't interpolate per-request data into it (timestamps, user names,
  session IDs) — that defeats caching on every turn.
* Iterate on the prompt during a quiet hour; expect the first turn
  after a prompt change to pay the full input price.

Behavioural rules in the prompt are intentionally short and imperative
because current models follow literal instructions closely — avoid
"if you're not sure please consider whether…" phrasing; prefer "ask the
user before…".

## Personal-finance mode

A `kind='personal'` company is a single-user tenant: one person tracking
their own money, with the SME machinery (payroll, POs, approvals,
equity, time-billing) hidden. Its user has `role='personal'`.

The agent runs the *same* loop, tools and proposal→confirm→undo flow.
Two things differ, both selected by `run_chat_turn(..., mode="personal")`
— which `/ai-accountant/chat` passes when `user.role == Role.PERSONAL`:

| | business (default) | personal |
|---|---|---|
| registry | `build_default_registry()` (60 tools) | `build_personal_registry()` (38: reads and core proposals, money places, savings goals, net worth, the report card; no invoices, payroll, time-billing, equity or operations) |
| routing | all 15 tool groups | the 9 groups a person's books have |
| prompt | `SYSTEM_PROMPT` | `SYSTEM_PROMPT + PERSONAL_MODE_ADDENDUM` |

The two registries share 27 tools; with the 11 only a person has, the
catalogue is 71 tools. Routing adds `more_tools` to whichever is sent.

`PERSONAL_MODE_ADDENDUM` reframes tone and defaults only — plain
language instead of debit/credit, everyday categories, no counterparty
interrogation for a grocery run. **Every safety rule in the base prompt
still applies**: direction-of-money, the Toman→Rial conversion, the
refusal rules, and the "LLM can't write" invariant. The addendum is
appended, never substituted, so a change to the base rules reaches both
modes.

Note the addendum is a *suffix*: with prompt caching, business-mode
requests keep hitting the cached prefix unchanged. Adding personal mode
did not invalidate the business cache.

### Provisioning a personal account

There is no self-signup. A super-admin creates the tenant and its login
in one call — Companies console → **Type: Personal**, or:

```
POST /admin/companies
{"name": "Omid", "kind": "personal", "locale": "ir",
 "base_currency": "IRR", "username": "omid", "password": "…"}
```

`provision_company(kind="personal")` then seeds the personal chart of
accounts (`PERSONAL_SEED_ACCOUNTS` — everyday categories, Persian names,
English for `locale='uk'`) and creates the login with `role='personal'`,
`is_admin=False`. The chart deliberately reuses the Iranian code scheme
(expenses under `61xx`/`62xx`) so every locale-aware expense predicate —
budgets, reports — works on it unchanged.

The user lands on the AI chat (`ROLE_HOME`), with a four-item nav: My
finances, AI Chat, Vouchers, Recurring.

## Bank statements in the chat

A statement dropped into the chat (PDF, image, CSV, Excel) is recognised
deterministically — `app/services/statement_import.py::looks_like_bank_statement`
looks at the filename, the user's message and the PDF's embedded text
(NFKC-normalised, so Iranian bank PDFs with presentation-form letters match) —
and routed through the same pipeline as the Bank Statements page upload
(`import_statement_bytes`). The reply is a **deterministic turn** (no model
call, nothing posted) carrying an `intake` of kind `bank_statement` that the
UI renders as a card with *Fix step by step* and *Open in Bank statements* —
unless the message says what to do with the rows («کارمزدها رو هزینه بانکی
بزن»): then the imported statement is handed to the model with those
rules (`_statement_handoff`, scenarios AF2–AF7), which proposes
`propose_post_statement`.

`app/services/statement_review.py::build_statement_review` turns a reconciled
statement into findings — `unrecorded`, `needs_confirmation`,
`amount_mismatch`, `missing_in_bank`, `duplicate`, `balance_gap` — each with a
`suggested_fix`. Three surfaces read it: `POST /brain/bank-statements/{id}/review`
(the page's *Check against books* panel), the `review_bank_statement` tool,
and the chat card's counts.

The system prompt tells the model to take findings **one per turn**: describe,
propose exactly one fix, stop. An `unrecorded` row is posted with
`propose_create_transaction(bank_statement_row_id=…)`; on Confirm the
statement row is marked posted, and a second card for the same row is refused
with 409, as is any row flagged `duplicate`.

## Proactive insights & the briefing

`app/services/insight_service.py` is LLM-free: payroll month-over-month with
joiners/leavers, fresh pay profiles, expense accounts above 1.5× their
3-month average, revenue drops, runway, supplier payments ≥ 3× that
supplier's median, statement overdue (40 days), receivables +25% in 30 days,
missed recurring payments. Wording lives in `_TEMPLATES` in en/fa/es/ar
(tested for parity); results are cached ~10 min per tenant because the
notification feed recomputes on every 90-second bell poll.

Surfaces: `GET /insights` (dashboard "What changed" panels), notification
kind `insight` (auto-resolves when the condition clears), the `get_insights`
tool, and `POST /ai-accountant/briefing` — a deterministic assistant-first
message the chat requests once a day when opened, persisted in the session
with `content.briefing = true`.

## Inspecting the audit trail

Every AI-initiated write tags the audit row with `actor_source =
'ai-assistant'`. To see what the assistant has done today:

```sql
SELECT timestamp, user_id, action, entity_type, entity_id, tool_name,
       confirmation_token, user_message
  FROM audit_logs
 WHERE actor_source = 'ai-assistant'
   AND timestamp > now() - interval '1 day'
 ORDER BY timestamp DESC;
```

To find every transaction that was undone or reversed:

```sql
SELECT entity_id AS original_txn, tool_name, detail
  FROM audit_logs
 WHERE actor_source = 'ai-assistant'
   AND action = 'undo'
 ORDER BY timestamp DESC;
```

Both paths write an `action = 'undo'` row; `tool_name` says which.
`undo_action` (the 120-second Undo) soft-deleted the entry, and its
`detail` JSON says `"mode": "deleted"` with the `deleted_transaction_id`.
`reverse_action` (and an Undo of an entry in a closed period) posted a
compensating entry, and its `detail` carries the `reversal_transaction_id`.

## Configuration

The AI Chat can run against either the **Anthropic Messages API** or any
**OpenAI Chat Completions**-compatible endpoint (OpenAI direct, Metis's
`/openai/v1`, LM Studio, OpenRouter, Together, Custom). Pick the shape
in **Settings → AI providers → AI Chat provider → Wire protocol**, or
let it auto-detect.

### Auto-detect rule

When the shape selector is set to *Auto-detect* (or the
`ai_chat_provider_shape` AppSetting is empty):

| Anthropic API key present? | Effective shape |
|---|---|
| Yes | `anthropic` |
| No  | `openai` (uses the OpenAI-shape provider configured in the same card) |

The orchestrator (`_resolve_chat_shape`) and the admin endpoint
(`/admin/chat-provider-shape`) read this identically — covered by the
no-drift tests in `test_chat_provider_shape_endpoint.py`.

### Anthropic-shape configuration

For Claude Opus / Sonnet / Haiku directly, or any third-party gateway
that speaks the Anthropic Messages API (Metis's `/anthropic/v1`,
LiteLLM, etc.).

**Env vars** (boot-time defaults):

| Env var | Purpose | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Required when shape=anthropic. Without it the chat returns `ANTHROPIC_API_KEY is not configured`. | — |
| `ANTHROPIC_MODEL` | Model ID. | `claude-opus-4-6` |
| `ANTHROPIC_BASE_URL` | Endpoint URL. | `https://api.anthropic.com` |
| `ANTHROPIC_MAX_TOKENS` | Per-turn output cap. | `8192` |

**Settings page**: `Settings → AI providers → AI Chat provider →` model /
base URL / API key. Writes to `PATCH /admin/anthropic-config`, persists
in `app_settings`, overrides the env vars on restart. API key is
write-only — empty input keeps the existing value; `-` clears it.

### OpenAI-shape configuration

For OpenAI direct, Metis's `/openai/v1`, LM Studio, OpenRouter, Together,
or any custom endpoint that conforms to the OpenAI Chat Completions
spec with `tools` / `tool_calls`.

The OpenAI-shape adapter reads from the *existing* default-provider
config (`AI_PROVIDER` + `<PROVIDER>_BASE_URL` / `_MODEL` / `_API_KEY`)
— no new env vars to learn:

| Active provider | Env vars used |
|---|---|
| `metis` | `METIS_BASE_URL`, `METIS_MODEL`, `METIS_API_KEY` |
| `lmstudio` | `LM_STUDIO_BASE_URL`, `LM_STUDIO_MODEL`; no API key needed |
| `custom` | `AI_BASE_URL`, `AI_MODEL`, `AI_API_KEY`, `AI_API_KEY_HEADER`, `AI_API_KEY_PREFIX` |

So pointing AI Chat at Metis's OpenAI endpoint just requires:

```bash
AI_PROVIDER=metis
METIS_BASE_URL=https://api.metisai.ir/openai/v1
METIS_MODEL=gpt-4.1-mini
METIS_API_KEY=tpsg-…
```

…plus picking *OpenAI Chat Completions* in the Settings dropdown (or
leaving on *Auto-detect* if no Anthropic key is configured).

**Local LM Studio caveat:** tool calling on local models is hit-and-miss.
Pick a tool-call-capable model — Qwen2.5-Coder, Llama 3.1+ Instruct,
Mistral Small 3, Hermes 3. Models without trained tool-calling support
will silently never call a tool, or hallucinate calls with malformed
JSON arguments. The adapter flags malformed arguments with
`_parse_error` so the orchestrator surfaces a clean error message
instead of crashing on Pydantic validation.

**URL normalization** (`openai_client._chat_completions_url`): the
adapter tolerates several base-URL flavours so the user doesn't have
to think about path suffixes — bare hostname → adds `/v1/chat/completions`;
URL ending in `/v1` → adds `/chat/completions`; already-suffixed URL is
returned as-is.

### Shape-selector endpoint

```
GET  /admin/chat-provider-shape
  → {shape: "" | "anthropic" | "openai",
     effective: "anthropic" | "openai",
     supported: ["anthropic", "openai"]}

PUT  /admin/chat-provider-shape  body: {shape: "" | "anthropic" | "openai"}
```

Empty string clears the explicit choice and re-enables auto-detection.
Unknown values 400.

## Choosing models (measured, not guessed)

Two benches, both against Metis pricing (`docs.metisai.ir/pricing`).

**Statement OCR** — `scripts/model_eval.py` (inside the api container):

```bash
# rows vs the first model, running-balance consistency, latency, USD/statement
docker compose run --rm -v "$PWD/tmp:/eval" api python scripts/model_eval.py ocr \
  --pdf /eval/statement.pdf --models gemini-3.7-flash,gemini-2.5-pro,gemini-3.1-flash-lite
```

**The chat eval set** (roadmap §5.5) — `app/services/ai_eval/`:
`scenarios.json` holds fa/en scenarios (expenses, the toman slip, receipts,
cash questions that must quote the right figure, suppliers, insights,
refusals, statement review, a new client, an invoice payment, cheques, a sales
invoice, spending, receivables). Each says what a good turn does — tools,
tool order, the cards and what is on them (total, which side the bank is on,
date, fields), reply language, figures the reply must quote — and carries a
recorded good trajectory. `fixture.py` seeds the small company they run
against (cash 2,070,000,000 IRR, INV-1001 open, a cheque due in five days,
a statement with one unrecorded fee).

* **Every CI run** replays each trajectory through the real agent loop and
  tools, no model and no network (`tests/test_ai_eval.py`): a tool renamed,
  an argument dropped or a card that no longer carries what a scenario checks
  fails the build. (The first replay found statement-review cards refused as
  `amount_mismatch` — the review message's UUID digits counted as amounts.)
* **Nightly** `.github/workflows/ai-eval.yml` runs the set three times per
  scenario against the live model (secret `AI_EVAL_API_KEY`; variable
  `AI_EVAL_MODEL`, default gpt-4.1-mini) and compares with the last good
  run. A scenario that passed ≥ 2/3 and now passes < 1/2, any failure of a
  critical one (the refusals), or the shared pass rate falling 10 points
  fails the job — GitHub mails the failure; the summary table shows each
  scenario against the previous run. Run it by hand from the Actions tab
  (`accept` keeps a changed run as the new baseline).
* **By hand**, on a scratch database (the script refuses any other):

```bash
DATABASE_URL=postgresql+psycopg://postgres:postgres@db:5432/aa_eval_scratch APP_ENV=test \
  python -m scripts.ai_eval run --models gpt-4.1-mini,gpt-5-mini --repeat 3 --out /tmp/eval.json
python -m scripts.ai_eval replay        # the recorded trajectories only
```

**The review queue** (roadmap §5.5, part 2) — `app/services/ai_review.py`:
about one real turn in ten (web and bots) is kept for the company's owner in
Settings → AI review: the question, the answer, each tool call (input, and
whether it worked), the cards, model and latency. The owner marks it good or
needs work, or downloads it as a draft scenario for `scenarios.json`. Samples
stay in the company (the platform console counts them, never reads them), go
with their conversation or after 90 days, and the owner can stop the sampling.
Tests keep it off (`tests/conftest.py`) except `tests/test_ai_review.py`.

Results on 2026-09-24 (5-page Mellat statement; Default company; the chat
rows are from the earlier 9-scenario bench):

| task | model | outcome | latency | cost |
|---|---|---|---|---|
| OCR | gemini-2.5-pro (old default) | 36 rows, 3 running-balance breaks (dropped zero, flipped direction) | 96 s | $0.20 / statement |
| OCR | **gemini-3.7-flash (new default)** | 36 rows, 0 breaks | 22 s | $0.03 |
| OCR | gemini-3.1-flash-lite | 36 rows, 2 breaks | 36 s | $0.006 |
| OCR | gemini-2.5-flash / flash-lite | returned non-JSON | — | — |
| chat | gpt-4o-mini (old default) | 7/9; Persian Toman card 0/3, statement card 0/3 | 13 s/turn | $6 / 1,000 turns |
| chat | **gpt-4.1-mini (new default)** | 8/9; Persian card 2/3, statement card 3/3 | 9 s/turn | $15 / 1,000 turns |
| chat | gpt-5.6-luna | 8/9 (needs `reasoning_effort: none` with tools); on 3× repeats Persian card 2/3, statement card 3/3, receipt 3/3 | 20 s/turn (14–40 s) | $8–10 / 1,000 turns |
| chat | gpt-5-mini (low effort) | 8/9 | 17 s/turn | $13 / 1,000 turns |
| chat | gpt-5-nano | 8/9 | 72 s/turn | $4 / 1,000 turns |
| chat | gpt-4.1-nano | 5/9 | 16 s/turn | $3 / 1,000 turns |

In that bench the "English expense from *test bank*" scenario failed on
every capable model because that bank did not exist in the test company —
the models correctly asked instead of guessing; only gpt-4o-mini posted
blindly. The eval books have a Test Bank.

The OpenAI-shape client adapts the request to the model family:
`max_completion_tokens` and `reasoning_effort` (`none` for gpt-5.6/6,
`low` for gpt-5.x and o-series) — see `openai_client.output_limit_param`
and `reasoning_effort_param`. The model actually used at runtime is the one
saved in Settings → AI providers (persisted in `app_settings`), so changing
the default in `config.py` only affects fresh installs; existing
deployments switch the model in Settings.

**The OCR chain** (`ocr_extract._vision_raw`, used for receipts and
invoices dropped in the chat and for statement images): `OCR_MODEL`
(default `gemini-3.7-flash`), then `OCR_GEMINI_FALLBACK_MODEL`
(`gemini-2.5-pro`), then the OpenAI-compatible `OCR_FALLBACK_MODEL`
(`gpt-4o`). The two Gemini steps run only when the active backend can
reach Metis's Gemini endpoint (`GEMINI_BASE_URL`, with the active
backend's key — `ai_runtime.gemini_wrapper_key`); otherwise OCR goes
straight to `gpt-4o` on the active backend. A used-up AI budget stops
the chain rather than trying the next model.

## Who configures the provider

AI provider wiring (OpenAI-shape provider + key, Anthropic config, chat shape)
is **platform-wide**: one runtime configuration for every company on the
server, stored in a single `app_settings` row with `company_id = NULL` and
editable only by the super-admin (`Perm.PLATFORM_ADMIN`, held by no company
role). Owners see a note in Settings instead of the controls. Decided
2026-09-24 after a per-company save was found to flip the model for every
tenant until the next restart.

## Cost notes

On the Anthropic shape the system prompt + tool catalogue are cached
(`cache_control: {type: "ephemeral"}` on the last system block and the
last tool); an OpenAI-shape gateway caches a repeated prefix by itself,
which is why routing keeps the core tools and the unrouted instructions
first and in a fixed order. On follow-up turns the cached portion costs
~0.1× input price. Watch `cache_read_input_tokens` and
`cache_creation_input_tokens` in the service logs to verify the prefix
is being reused (search for `ai-accountant turn`).

Tool routing (above) is the larger saving: a message is sent the core
tools and its own groups, not all 60 (a company) or 38 (a person); the
fast paths and the other deterministic turns cost nothing at all.

**Metering** (`app/services/ai_usage.py`): every provider call goes
through `metered_llm`, which refuses the call once the company or the
user is over its 24-hour token budget, counts it in Prometheus
(`aa_llm_*`) and writes one `ai_usage_events` row with the tokens and an
estimated cost. There are seven call sites: the chat's two clients
(`anthropic_client.py`, `openai_client.py`), the two OCR vision calls
(Gemini and OpenAI-compatible, `ocr_extract.py`), the two voice-note
transcription calls (Gemini and OpenAI-compatible, `speech.py`) and the
OpenAI-compatible post in `ai_suggest.py`. (The module's docstring
still says five; the two in `speech.py` aren't in it.) `guard_ai_request` runs the
same budget check, plus the per-minute request limits, at the top of
each AI endpoint — in the chat, after the deterministic steps.
