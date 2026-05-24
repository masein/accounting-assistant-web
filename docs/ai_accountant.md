# AI Accountant — developer notes

The AI accountant is a conversational bookkeeper that exposes a small,
typed tool catalogue to an LLM. The LLM never writes to the books
directly — every write goes through a proposal → confirmation →
execute loop with idempotency tokens and a 30-second undo window.

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

         ┌─────────── HTTP undo (frontend only, 30s) ────────┐
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
│   ├─ ai_accountant.py            # POST /chat, /execute, /undo; GET /sessions, /proposals/{token}
│   └─ admin.py                    # /admin/anthropic-config, /admin/chat-provider-shape
├─ services/ai_accountant/
│   ├─ llm_protocol.py             # ChatMessage, ToolCall, LLMResponse, LLMClient (abstract)
│   ├─ anthropic_client.py         # AsyncAnthropic + AnthropicLLMClient (with prompt caching)
│   ├─ openai_client.py            # OpenAILLMClient (httpx, /v1/chat/completions)
│   ├─ base.py                     # BaseTool, ToolContext, ToolRegistry, ToolError
│   ├─ read_tools.py               # find_entity, list_entities, query_ledger, …
│   ├─ proposal_tools.py           # propose_create_transaction
│   ├─ execute_service.py          # execute_proposal(), undo_action()
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
METIS_MODEL=gpt-4o-mini
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

## Cost notes

The system prompt + tool catalogue are cached (`cache_control:
{type: "ephemeral"}` on the last system block and the last tool). On
follow-up turns the cached portion costs ~0.1× input price. Watch
`cache_read_input_tokens` and `cache_creation_input_tokens` in the
service logs to verify the prefix is being reused (search for
`ai-accountant turn`).
