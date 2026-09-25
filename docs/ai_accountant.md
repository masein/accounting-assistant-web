# AI Accountant — developer notes

The AI accountant is a conversational bookkeeper that exposes a small,
typed tool catalogue to an LLM. The LLM never writes to the books
directly — every write goes through a proposal → confirmation →
execute loop with idempotency tokens and a 120-second undo window (the quick undo soft-deletes the entry; the later Reverse posts a compensating entry).

The agent is **provider-neutral**: it works in a normalized
`ChatMessage` / `LLMResponse` vocabulary and dispatches to an
`LLMClient` adapter that speaks the wire format of whichever provider
is active (Anthropic Messages API or OpenAI Chat Completions). Adding
a new provider is ~150 LOC.

## Architecture (one screen)

```
              ┌─────────────────── orchestrator ───────────────────┐
 user msg → │  run_chat_turn(db, user_id, user_message, …)         │
              │  loop until stop_reason == "end_turn":             │
              │    1. send (system + tools + history) via          │
              │       LLMClient.chat(…)   ◀── dispatches by shape  │
              │    2. for each tool_call, run the tool             │
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
              ┌─── tool catalogue ──┴────────────────┐
   read       │ find_entity   list_entities         │
   tools      │ query_ledger  get_account_balance   │     ai_proposals
              │ get_company_defaults                │   (status: pending)
                                                      
   proposal   │ propose_create_transaction          │ ─────────────────
   tools      └─────────────────────────────────────┘ → confirmation_token

         ┌───────────────── HTTP execute (frontend only) ────┐
         │ POST /ai-accountant/execute  (idempotent)         │
         │   • builds TransactionCreate from tool_input       │
         │   • runs _create_transaction_from_payload          │
         │   • writes audit_logs row (actor_source='ai-…')    │
         │   • flips ai_proposals.status='executed'           │
         └────────────────────────────────────────────────────┘

         ┌─────────── HTTP undo (frontend only, 120s) ───────┐
         │ POST /ai-accountant/undo                          │
         │   • LedgerService.reverse_journal_entry            │
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
  requesting user, isn't expired (>10 minutes), and isn't already
  executed.
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

## File map

```
app/
├─ api/
│   ├─ ai_accountant.py            # POST /chat, /execute, /undo, /reverse, /briefing; GET /sessions, /proposals/{token}
│   └─ admin.py                    # /admin/anthropic-config, /admin/chat-provider-shape
├─ services/ai_accountant/
│   ├─ llm_protocol.py             # ChatMessage, ToolCall, LLMResponse, LLMClient (abstract)
│   ├─ anthropic_client.py         # AsyncAnthropic + AnthropicLLMClient (with prompt caching)
│   ├─ openai_client.py            # OpenAILLMClient (httpx, /v1/chat/completions)
│   ├─ base.py                     # BaseTool, ToolContext, ToolRegistry, ToolError
│   ├─ read_tools.py               # find_entity, list_entities, query_ledger, …
│   ├─ proposal_tools.py           # propose_create_transaction (+ bank_statement_row_id), …
│   ├─ statement_tools.py          # review_bank_statement (statement vs books findings)
│   ├─ insight_tools.py            # get_insights (proactive insights)
│   ├─ spending_tools.py           # get_spending_summary (period words → dates in the company calendar)
│   ├─ cash_tools.py               # get_cash_position (every cash + bank account, totalled)
│   ├─ invoice_tools.py            # list_invoices, get_invoice, propose_record_invoice_payment, propose_create_invoice
│   ├─ commitment_tools.py         # list_commitments, propose_settle_commitment, propose_bounce_cheque, propose_create_cheque
│   ├─ invoice_execute.py          # confirm handlers for the two modules above
│   ├─ statement_intake.py         # chat drop of a statement PDF/image → import + review card
│   ├─ execute_service.py          # execute_proposal(), undo_action() (soft-delete), reverse_action()
│   └─ orchestrator.py             # run_chat_turn(), SYSTEM_PROMPT, _resolve_chat_shape()
└─ models/
    ├─ ai_accountant.py             # AIProposal, AIChatSession, AIChatMessage
    └─ audit_log.py                 # AuditLog (with new columns from migration 005)

tests/
├─ test_ai_accountant_read_tools.py        # 15 read-tool unit tests
├─ test_ai_accountant_flow.py              # 12 proposal → execute → undo tests
├─ test_ai_accountant_orchestrator.py      # 9 mocked-LLMClient loop tests
├─ test_ai_accountant_openai_client.py     # 16 wire-translation + httpx tests
└─ test_chat_provider_shape_endpoint.py    # 10 shape-selector + auto-detect tests
```

## Adding a new tool

1. Define a Pydantic `InputSchema` and a `BaseTool` subclass in either
   `read_tools.py` (pure query) or `proposal_tools.py` (writes a
   pending proposal). Set `category = "read" | "proposal"`.
2. Implement `async def run(self, ctx: ToolContext, args: InputSchema)
   -> dict[str, Any]`. Raise `ToolError(msg, code=...)` for clean
   user-facing failures. Anything else becomes a 502 in the chat
   panel.
3. Register it in `register_read_tools()` / `register_proposal_tools()`.
4. **If the tool is a new proposal type**, add an executor branch in
   `execute_service._execute_create_transaction` — actually, write a
   new function and dispatch on `proposal.tool_name`. The brief lists
   `propose_create_invoice`, `propose_mark_invoice_paid`, etc.; each
   one needs its own executor.
5. Add unit tests in `tests/test_ai_accountant_*` covering happy path
   + at least one validation rejection.

The system prompt's last paragraph already nudges Claude toward the
right tool by category; you usually don't need to edit it when adding
new read tools. New proposal tools should be summarised in the
`# Resolution loop` section of `SYSTEM_PROMPT` (in `orchestrator.py`).

## Tuning the system prompt

`orchestrator.SYSTEM_PROMPT` is a single string. Because it's part of
the cached prefix, **any byte change invalidates the cache** for every
subsequent request. Two implications:

* Don't interpolate per-request data into it (timestamps, user names,
  session IDs) — that defeats caching on every turn.
* Iterate on the prompt during a quiet hour; expect the first turn
  after a prompt change to pay the full input price.

Behavioural rules in the prompt are intentionally short and imperative
because Claude 4.7 follows literal instructions more closely than
earlier models — avoid "if you're not sure please consider whether…"
phrasing; prefer "ask the user before…".

## Personal-finance mode

A `kind='personal'` company is a single-user tenant: one person tracking
their own money, with the SME machinery (payroll, POs, approvals,
equity, time-billing) hidden. Its user has `role='personal'`.

The agent runs the *same* loop, tools and proposal→confirm→undo flow.
Two things differ, both selected by `run_chat_turn(..., mode="personal")`
— which `/ai-accountant/chat` passes when `user.role == Role.PERSONAL`:

| | business (default) | personal |
|---|---|---|
| registry | `build_default_registry()` (22 tools) | `build_personal_registry()` (12: reads + core proposals; no time-billing, no equity) |
| prompt | `SYSTEM_PROMPT` | `SYSTEM_PROMPT + PERSONAL_MODE_ADDENDUM` |

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
UI renders as a card with *Fix step by step* and *Open in Bank statements*.

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

To find every transaction that was undone:

```sql
SELECT entity_id AS reversed_txn, detail
  FROM audit_logs
 WHERE actor_source = 'ai-assistant'
   AND action = 'undo'
 ORDER BY timestamp DESC;
```

The `detail` column carries the linked reversal transaction id in JSON.

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
| `ANTHROPIC_MODEL` | Model ID. | `claude-opus-4-7` |
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

`scripts/model_eval.py` benchmarks candidate models on **our** tasks
against Metis pricing (`docs.metisai.ir/pricing`). Run it inside the api
container; it never posts to the books (chat cards are cancelled).

```bash
# statement OCR: rows vs the first model, running-balance consistency, latency, USD/statement
docker compose run --rm -v "$PWD/tmp:/eval" api python scripts/model_eval.py ocr \
  --pdf /eval/statement.pdf --models gemini-3.7-flash,gemini-2.5-pro,gemini-3.1-flash-lite
# chat agent: 9 scenarios (en/fa expense, receipt, questions, insights, refusal, statement review, new client)
docker compose run --rm api python scripts/model_eval.py chat \
  --user-id <owner uuid> --company-id <company uuid> --statement-id <statement uuid> \
  --models gpt-4.1-mini,gpt-4o-mini --repeat 3
```

Results on 2026-09-24 (5-page Mellat statement; Default company):

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

The "English expense from *test bank*" scenario fails on every capable
model because that bank does not exist in the test company — the models
correctly ask instead of guessing; only gpt-4o-mini posts blindly.

The OpenAI-shape client adapts the request to the model family:
`max_completion_tokens` and `reasoning_effort` (`none` for gpt-5.6/6,
`low` for gpt-5.x and o-series) — see `openai_client.output_limit_param`
and `reasoning_effort_param`. The model actually used at runtime is the one
saved in Settings → AI providers (persisted in `app_settings`), so changing
the default in `config.py` only affects fresh installs; existing
deployments switch the model in Settings.

## Who configures the provider

AI provider wiring (OpenAI-shape provider + key, Anthropic config, chat shape)
is **platform-wide**: one runtime configuration for every company on the
server, stored in a single `app_settings` row with `company_id = NULL` and
editable only by the super-admin (`Perm.PLATFORM_ADMIN`, held by no company
role). Owners see a note in Settings instead of the controls. Decided
2026-09-24 after a per-company save was found to flip the model for every
tenant until the next restart.

## Cost notes

The system prompt + tool catalogue are cached (`cache_control:
{type: "ephemeral"}` on the last system block and the last tool). On
follow-up turns the cached portion costs ~0.1× input price. Watch
`cache_read_input_tokens` and `cache_creation_input_tokens` in the
service logs to verify the prefix is being reused (search for
`ai-accountant turn`).
