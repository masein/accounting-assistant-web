# AI Accountant — developer notes

The AI accountant is a conversational bookkeeper that exposes a small,
typed tool catalogue to Claude. Claude never writes to the books
directly — every write goes through a proposal → confirmation →
execute loop with idempotency tokens and a 30-second undo window.

## Architecture (one screen)

```
              ┌─────────────────── orchestrator ───────────────────┐
 user msg → │  run_chat_turn(db, user_id, user_message, …)         │
              │  loop until stop_reason == "end_turn":             │
              │    1. send (system + tools + history) to Anthropic │
              │    2. for each tool_use block, run the tool        │
              │    3. append tool_result(s), repeat                │
              └─────────────────────┬──────────────────────────────┘
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

* **Claude can't write.** Proposal tools only persist a row to
  `ai_proposals` — they never touch transactions / invoices / entities.
* **The user authorises.** The frontend calls `/ai-accountant/execute`
  on Confirm. Server-side it checks the proposal belongs to the
  requesting user, isn't expired (>10 minutes), and isn't already
  executed.
* **Single source of truth for audit.** Every successful execute
  writes exactly one `audit_logs` row with
  `actor_source='ai-assistant'`, `tool_name`, `confirmation_token`,
  `session_id`, and `user_message`.

## File map

```
app/
├─ api/
│   └─ ai_accountant.py            # POST /chat, /execute, /undo; GET /sessions, /proposals/{token}
├─ services/ai_accountant/
│   ├─ anthropic_client.py         # AsyncAnthropic wrapper with prompt caching
│   ├─ base.py                     # BaseTool, ToolContext, ToolRegistry, ToolError
│   ├─ read_tools.py               # find_entity, list_entities, query_ledger, …
│   ├─ proposal_tools.py           # propose_create_transaction
│   ├─ execute_service.py          # execute_proposal(), undo_action()
│   └─ orchestrator.py             # run_chat_turn(), SYSTEM_PROMPT, build_default_registry()
└─ models/
    ├─ ai_accountant.py             # AIProposal, AIChatSession, AIChatMessage
    └─ audit_log.py                 # AuditLog (with new columns from migration 005)

tests/
├─ test_ai_accountant_read_tools.py     # 15 read-tool unit tests
├─ test_ai_accountant_flow.py           # 12 proposal → execute → undo tests
└─ test_ai_accountant_orchestrator.py   # 8 mocked-Anthropic loop tests
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

## Required environment variables

| Env var | Purpose | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Required at runtime. The orchestrator raises `AIAccountantError` until set. | — |
| `ANTHROPIC_MODEL` | Override the model used. Defaults to `claude-opus-4-7`. | `claude-opus-4-7` |
| `ANTHROPIC_BASE_URL` | Optional override for proxying or self-hosted gateways. | `https://api.anthropic.com` |
| `ANTHROPIC_MAX_TOKENS` | Per-turn output cap. | `8192` |

To swap to a cheaper model for testing, set `ANTHROPIC_MODEL=claude-haiku-4-5`
in `.env` and restart the API container.

## Cost notes

The system prompt + tool catalogue are cached (`cache_control:
{type: "ephemeral"}` on the last system block and the last tool). On
follow-up turns the cached portion costs ~0.1× input price. Watch
`cache_read_input_tokens` and `cache_creation_input_tokens` in the
service logs to verify the prefix is being reused (search for
`ai-accountant turn`).
