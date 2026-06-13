"""Agent orchestrator — drives the AI accountant tool-use loop.

The loop, in shape-agnostic terms (the LLM client abstracts away wire format):

1. Append the user's message to the chat history.
2. Send (system prompt + tools + ChatMessage history) via ``LLMClient.chat``
   to whichever provider is active (Anthropic or OpenAI-compatible).
3. If the response has any tool calls, execute each tool (read-tools answer
   immediately; proposal-tools persist a pending row and return a
   ``confirmation_token``), append the results as ``role: "tool"`` messages,
   and loop back to step 2.
4. When ``stop_reason == "end_turn"`` (or there are no tool calls), persist
   the assistant message to ``ai_chat_messages`` and return.

Hard safeguards:

* ``MAX_TURNS`` — caps tool-use iterations per user message. Above this we
  return the partial response and log a warning rather than rolling forever.
* ``PAUSE_TURN_RETRIES`` — handles Anthropic's `pause_turn` (server-side
  iteration cap) by re-sending; OpenAI doesn't surface this.
* All tool exceptions are caught and surfaced to the model as
  ``tool_result(is_error=True)`` so it can recover instead of crashing
  the chat turn.

Storage is in the normalized ``ChatMessage.to_dict()`` JSON shape — same
format regardless of which LLM produced or will consume the row. Sessions
can switch providers between turns without losing history (subject to the
caveat that some providers expect specific assistant↔tool↔user ordering;
both adapters in this codebase handle the canonical loop fine).
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_accountant import AIChatMessage, AIChatSession

from .anthropic_client import AIAccountantError, AnthropicLLMClient
from .base import BaseTool, ToolContext, ToolError, ToolRegistry
from .llm_protocol import ChatMessage, LLMClient, LLMClientError, ToolCall
from .openai_client import OpenAILLMClient
from .proposal_tools import register_proposal_tools
from .read_tools import register_read_tools

logger = logging.getLogger(__name__)

# Per-message safety cap. A well-behaved chat hits the network ~3 times
# (resolve accounts → propose → wrap up). Raised to 16 to give multi-step
# tasks (resolve two account legs via search_accounts, then propose) headroom
# before the graceful-partial fallback kicks in. Still bounded for spend.
MAX_TURNS = 16
PAUSE_TURN_RETRIES = 3


SYSTEM_PROMPT = """You are the AI accountant for this company — a careful junior bookkeeper that helps the manager record transactions, look up balances, and answer accounting questions.

The current date is {today}. Treat THIS as "today" for every relative date ("today", "yesterday", "last Tuesday") and for deciding whether a date is in the future — do NOT use your own training-era notion of the current date. Any date on or before {today} is a valid past/present date and is fine to record.

You have a fixed catalogue of tools. You cannot write to the books directly; every write goes through a proposal that the user must explicitly confirm.

# Behavioural rules — non-negotiable

1. **Resolve names, but converge fast.** When the user names a person or organisation, call ``find_entity`` ONCE for that name. Then decide and move on — never call ``find_entity`` or ``list_entities`` repeatedly for the same name.
2. **Resolve each account leg with ``search_accounts``, then propose.** The chart uses opaque codes with no account literally named "Cash" or "Office Supplies" — do NOT guess code prefixes. Call ``search_accounts("<plain category>")`` (e.g. "office supplies", "cash") ONCE per leg, take the top match's ``code``, then call ``propose_create_transaction``. Two legs (e.g. an expense paid from cash) = two ``search_accounts`` calls, then ONE proposal. Don't keep re-querying.
3. **Never split bulk requests into a single proposal.** "Pay all overdue invoices" → list the invoices and produce one proposal per invoice. Never aggregate.
4. **Never silently round, swap currencies, or 'fix' obvious typos in amounts.** If the user says "$1k" and the vendor's invoice is in EUR, flag the mismatch and ask.
5. **Only future-dated entries are restricted.** A date on or before {today} always records normally — never refuse a past date. Reject only dates more than 1 day AFTER {today} (judged against {today}, not your own clock), and only unless the user explicitly says it's scheduled. The server enforces this too.
6. **Amounts are WHOLE units of the stated currency — never multiply by 100.** Record the exact number the user/document gives: "300 GBP" is 300 (debit 300, credit 300), NOT 30000. Do not convert to pence/cents/minor units — this app stores whole pounds/euros/dollars/rials. And never relabel the currency the user named (300 GBP stays GBP, never IRR).

# Entity resolution — converge in ONE lookup (do not loop)

After a single ``find_entity`` call for a name, pick exactly one path:
* **Strong match** — top candidate confidence ≥ 0.80, or it's the only candidate: use it. Don't search again.
* **Several plausible matches** — list the top 2–3 by name and ask the user which one, then STOP and wait. Do not call find_entity/list_entities again.
* **No usable match** (best < 0.50, or the user said "no supplier / nobody"): entity links are OPTIONAL — go ahead and ``propose_create_transaction`` with an EMPTY ``entity_links`` list, and mention you couldn't match the name so the user can add it later.

Never burn the whole turn budget re-listing entities. A missing entity is fine; a dead-end with no proposal is not.

# Resolution loop — every time the user could cause a write

a. Parse intent (record / query / invoice / etc.) and extract: amount, currency, date, entity, account, memo.
b. Resolve the entity per the rules above (one ``find_entity`` call, then commit to a path). Entities are OPTIONAL — if the user says "none"/"no supplier", skip straight to accounts.
c. Resolve each account leg with ``search_accounts("<category>")`` — one call per leg, take the top match's code. Don't fall back to guessing prefixes with ``query_ledger``.
d. Fill defaults from ``get_company_defaults`` (currency, today's date, locale) if you haven't already. Use the reporting currency unless the user stated another.
e. Draft the proposal via ``propose_create_transaction`` AS SOON AS you have the amount, date, currency and both account codes — do not keep gathering. The tool registers a pending row and returns a ``confirmation_token``; DO NOT re-paste the full summary afterwards (the chat UI renders the action card). Just briefly say what you proposed and end your turn.

Worked example — "Record a 300 GBP office-supplies expense paid from cash today" (no entity needed):
``search_accounts("office supplies")`` → 7600; ``search_accounts("cash")`` → 1200; then ``propose_create_transaction`` with currency GBP, lines [Dr 7600 300, Cr 1200 300]. Two lookups, one proposal.

# Financial statements — use the deterministic tool, never hand-sum

For "balance sheet", "P&L" / "income statement", "trial balance" or "cash flow", call ``get_financial_statement`` with the right ``statement`` value and relay its totals. The figures already balance (Assets = Liabilities + Equity; trial-balance debits == credits) — do NOT reconstruct them from individual ``get_account_balance`` calls.

# Attached documents (invoice / receipt images or PDFs)

When the user's turn includes "Attached document OCR" context, treat those extracted fields (vendor, date, total, currency, line items) as the primary source for the entry. Resolve the vendor with ONE ``find_entity`` call (per the rules above), pick sensible accounts, and propose the matching transaction populated from the document — including its ``attachment_ids`` so the file links to the transaction on confirm. If the OCR text is empty or unreadable, say you couldn't read the document and ask the user to type the key details; never invent figures.

# When the user asks a pure question (read-only)

Answer with ``query_ledger`` / ``get_account_balance`` / ``list_entities``. No proposal needed. No confirmation needed.

# Style

* Be concise. Don't apologise. Don't lecture about accounting basics.
* ALWAYS reply in the user's language: {lang_name}. If they write in another language, match theirs.
* Match the company's currency by default (returned by ``get_company_defaults``).

# Refusals

If you must refuse on ethical/legal grounds, never give a bare "I can't help with that" — state the reason in ONE short sentence (e.g. "I can't help conceal income — that would be tax evasion." / "I can't fabricate receipts for expenses that didn't occur."). For investment/financial-advice requests, give the brief caveat and suggest consulting a qualified financial advisor.
"""


# Human-readable language names interpolated into the system prompt so the
# model knows which language to answer in (AI-2).
_LANG_NAMES = {
    "en": "English",
    "fa": "Persian (فارسی)",
    "es": "Spanish (Español)",
    "ar": "Arabic (العربية)",
}

# Localized status / fallback strings surfaced to the user (AI-2). Keyed by
# the user's preferred UI language with an English fallback.
_STATUS_STRINGS = {
    "en": {
        "max_turns_candidates": "I found these possible matches — please tell me which one (or say 'none'):\n{candidates}",
        "max_turns_dead_end": "I couldn't finish that automatically. Could you add a bit more detail (amount, date, and which account or person), and I'll propose the entry?",
    },
    "fa": {
        "max_turns_candidates": "این موارد احتمالی را پیدا کردم — لطفاً بگویید کدام‌یک مدنظرتان است (یا بنویسید «هیچ‌کدام»):\n{candidates}",
        "max_turns_dead_end": "نتوانستم این کار را به‌صورت خودکار کامل کنم. لطفاً کمی جزئیات بیشتر بدهید (مبلغ، تاریخ و کدام حساب یا شخص) تا سند را پیشنهاد دهم.",
    },
    "es": {
        "max_turns_candidates": "Encontré estas posibles coincidencias — dime cuál es (o escribe «ninguna»):\n{candidates}",
        "max_turns_dead_end": "No pude completarlo automáticamente. ¿Puedes dar un poco más de detalle (importe, fecha y qué cuenta o persona) y propongo el asiento?",
    },
    "ar": {
        "max_turns_candidates": "وجدت هذه التطابقات المحتملة — من فضلك أخبرني أيها تقصد (أو اكتب «لا شيء»):\n{candidates}",
        "max_turns_dead_end": "لم أتمكن من إتمام ذلك تلقائياً. هل يمكنك إضافة مزيد من التفاصيل (المبلغ والتاريخ وأي حساب أو شخص) وسأقترح القيد؟",
    },
}


def _status(lang: str, key: str) -> str:
    pack = _STATUS_STRINGS.get(lang) or _STATUS_STRINGS["en"]
    return pack.get(key) or _STATUS_STRINGS["en"][key]


def _numbers_in_text(text: str | None) -> list[int]:
    """Extract candidate monetary amounts from free text (Persian-digit
    aware), for the proposal amount-sanity cross-check. Handles plain and
    grouped numbers plus the common 'k'/'m' shorthand ('300', '1,500',
    '2.5k'). Ignores tiny tokens that are usually quantities/years noise by
    keeping only values ≥ 1."""
    if not text:
        return []
    from app.services.ocr_extract import coerce_amount, normalize_digits

    norm = normalize_digits(text)
    out: list[int] = []
    # number optionally followed by a k/m magnitude suffix
    for m in re.finditer(r"(\d[\d,٬\.]*)\s*([kKmM])?", norm):
        digits = m.group(1)
        suffix = (m.group(2) or "").lower()
        base = coerce_amount(digits)
        if base is None:
            continue
        if suffix == "k":
            base *= 1_000
        elif suffix == "m":
            base *= 1_000_000
        if base >= 1:
            out.append(base)
    return out


# ---------------------------------------------------------------------------
# Registry building
# ---------------------------------------------------------------------------


def build_default_registry() -> ToolRegistry:
    """Return a fully-populated tool registry: read tools + proposal tools."""
    reg = ToolRegistry()
    register_read_tools(reg)
    register_proposal_tools(reg)
    return reg


# ---------------------------------------------------------------------------
# LLM client selection
# ---------------------------------------------------------------------------


def _resolve_chat_shape(db: Session) -> str:
    """Read the active chat-provider shape. Defaults to 'anthropic' when an
    Anthropic key is configured (legacy behaviour), 'openai' otherwise.

    Persisted in ``app_settings`` under ``ai_chat_provider_shape`` once the
    user explicitly picks one via the Settings UI — until then this
    auto-detect rule applies.
    """
    from sqlalchemy import select as _sel
    from app.core.ai_runtime import resolve_anthropic_config
    from app.models.app_setting import AppSetting

    row = db.execute(
        _sel(AppSetting).where(AppSetting.key == "ai_chat_provider_shape")
    ).scalar_one_or_none()
    if row and (row.value or "").strip().lower() in ("anthropic", "openai"):
        return row.value.strip().lower()
    return "anthropic" if resolve_anthropic_config().get("api_key") else "openai"


def _get_chat_client(shape: str) -> LLMClient:
    if shape == "openai":
        return OpenAILLMClient()
    return AnthropicLLMClient()


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


def _get_or_create_session(
    db: Session, *, user_id: str, session_id: str | None
) -> AIChatSession:
    """Look up the chat session or create a fresh one."""
    if session_id:
        try:
            sid = uuid.UUID(session_id)
        except (ValueError, TypeError):
            sid = None
        if sid:
            row = db.execute(
                select(AIChatSession).where(AIChatSession.id == sid)
            ).scalar_one_or_none()
            if row is not None:
                if row.user_id != user_id:
                    raise PermissionError("This chat session belongs to a different user.")
                return row
    row = AIChatSession(user_id=user_id, title=None)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _persist_message(db: Session, *, session_id: uuid.UUID, role: str, content: dict[str, Any]) -> None:
    db.add(AIChatMessage(session_id=session_id, role=role, content=content))
    db.commit()


def _replay_history(db: Session, session_id: uuid.UUID) -> list[ChatMessage]:
    """Rebuild the normalized ``ChatMessage`` history from saved rows."""
    rows = (
        db.execute(
            select(AIChatMessage)
            .where(AIChatMessage.session_id == session_id)
            .order_by(AIChatMessage.created_at, AIChatMessage.id)
        )
        .scalars()
        .all()
    )
    history: list[ChatMessage] = []
    for row in rows:
        content = row.content or {}
        # Old (pre-protocol) format used to stash Anthropic-shape blocks
        # under a "content" key; if we encounter one of those, do a
        # best-effort conversion so old sessions don't break entirely.
        if "role" in content:
            history.append(ChatMessage.from_dict(content))
        elif row.role == "user" and "content" in content and isinstance(content["content"], str):
            # Legacy: {"content": "user text"}
            history.append(ChatMessage(role="user", text=content["content"]))
        # Older assistant + tool entries from before this refactor are
        # dropped on replay — they're in Anthropic-block shape and would
        # confuse an OpenAI adapter. The user can /reset the session.
    return history


# ---------------------------------------------------------------------------
# Chat result
# ---------------------------------------------------------------------------


@dataclass
class ChatResult:
    session_id: str
    text: str
    proposals: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str | None = None
    turns: int = 1
    provider_shape: str = "anthropic"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def run_chat_turn(
    db: Session,
    *,
    user_id: str,
    username: str | None = None,
    user_message: str,
    session_id: str | None = None,
    ip_address: str | None = None,
    lang: str = "en",
    ocr_context: str | None = None,
    attachment_ids: list[str] | None = None,
    source_amounts: list[int] | None = None,
    registry: ToolRegistry | None = None,
    client: LLMClient | None = None,
) -> ChatResult:
    """Drive one user-message turn through the AI accountant agent loop.

    Returns when the model says ``end_turn`` (or we hit ``MAX_TURNS``). The
    return value lists every proposal registered during the turn so the
    frontend can render the action cards.

    ``lang`` is the user's preferred UI language; it localizes the
    fallback/status text and tells the model which language to answer in
    (AI-2). ``ocr_context`` is OCR text extracted from any attached
    document, injected into the turn; ``attachment_ids`` are linked onto
    whatever transaction the model proposes this turn (chat-attachment
    feature).

    ``client`` lets tests inject a mock; in production we look up the
    active shape via ``_resolve_chat_shape`` and instantiate the
    corresponding adapter.
    """
    lang = lang if lang in _LANG_NAMES else "en"
    attachment_ids = list(attachment_ids or [])
    # Source amounts for the proposal sanity guard: OCR'd document totals
    # passed in by the caller, plus any numbers in the user's own message.
    src_amounts = list(source_amounts or [])
    src_amounts.extend(_numbers_in_text(user_message))
    reg = registry or build_default_registry()
    tool_defs = reg.to_anthropic()  # provider-neutral: {name, description, input_schema}

    # Give the model the REAL current date — it won't reliably read it from a
    # tool, and it judges "future" against its training-era clock otherwise
    # (rejecting valid past dates). Show the Jalali equivalent too when the
    # company displays the Jalali calendar.
    from datetime import date as _date

    from app.services.locale_service import get_display_calendar

    today = _date.today()
    today_str = today.isoformat()
    try:
        if get_display_calendar(db) == "jalali":
            from app.utils.jalali import format_jalali

            today_str = f"{today.isoformat()} (Jalali {format_jalali(today)})"
    except Exception:
        pass
    system_prompt = (
        SYSTEM_PROMPT.replace("{lang_name}", _LANG_NAMES[lang]).replace("{today}", today_str)
    )

    shape = "anthropic"
    if client is None:
        shape = _resolve_chat_shape(db)
        client = _get_chat_client(shape)
    else:
        shape = getattr(client, "shape", "unknown")

    chat_session = _get_or_create_session(db, user_id=user_id, session_id=session_id)
    session_uuid_str = str(chat_session.id)

    history = _replay_history(db, chat_session.id)
    # Fold OCR context into the user's turn so the model can reason over the
    # attached document's fields. Persist the augmented text so a session
    # replay still carries the document context.
    turn_text = user_message
    if ocr_context:
        turn_text = (user_message or "").rstrip()
        if turn_text:
            turn_text += "\n\n"
        turn_text += ocr_context
    user_turn = ChatMessage(role="user", text=turn_text)
    history.append(user_turn)
    _persist_message(db, session_id=chat_session.id, role="user", content=user_turn.to_dict())

    tool_ctx = ToolContext(
        db=db,
        user_id=user_id,
        username=username,
        chat_session_id=session_uuid_str,
        user_message=user_message,
        ip_address=ip_address,
        attachment_ids=attachment_ids,
        source_amounts=src_amounts,
    )

    proposals: list[dict[str, Any]] = []
    tool_call_log: list[dict[str, Any]] = []
    # Most recent entity-resolution candidates, used to build a graceful
    # "pick one" partial if the turn budget is exhausted (AI-1).
    last_candidates: list[dict[str, Any]] = []
    final_text = ""
    stop_reason: str | None = None
    pause_attempts = 0

    for turn in range(1, MAX_TURNS + 1):
        try:
            response = await client.chat(
                system_prompt=system_prompt,
                tools=tool_defs,
                messages=history,
            )
        except LLMClientError as e:
            raise AIAccountantError(str(e)) from e

        stop_reason = response.stop_reason
        assistant_msg = response.message
        history.append(assistant_msg)
        _persist_message(
            db, session_id=chat_session.id, role="assistant",
            content=assistant_msg.to_dict(),
        )

        if stop_reason == "pause_turn":
            pause_attempts += 1
            if pause_attempts > PAUSE_TURN_RETRIES:
                logger.warning("ai-accountant: pause_turn retries exhausted — bailing")
                break
            continue

        if stop_reason == "end_turn" or not assistant_msg.tool_calls:
            final_text = assistant_msg.text or ""
            return ChatResult(
                session_id=session_uuid_str,
                text=final_text,
                proposals=proposals,
                tool_calls=tool_call_log,
                stop_reason=stop_reason,
                turns=turn,
                provider_shape=shape,
            )

        # Execute every tool the model asked for and feed results back.
        tool_result_messages: list[ChatMessage] = []
        for call in assistant_msg.tool_calls:
            log_entry = {"tool_use_id": call.id, "name": call.name, "input": call.input}
            tool_call_log.append(log_entry)
            tool = reg.get(call.name)
            if tool is None:
                tool_result_messages.append(ChatMessage(
                    role="tool", tool_call_id=call.id,
                    text=f"Unknown tool: {call.name!r}", is_error=True,
                ))
                continue

            # Detect malformed arguments from weak local models.
            if call.input.get("_parse_error"):
                tool_result_messages.append(ChatMessage(
                    role="tool", tool_call_id=call.id,
                    text=f"Tool call had malformed JSON arguments: "
                         f"{call.input.get('_raw_arguments', '')!r}. Retry with valid JSON.",
                    is_error=True,
                ))
                continue

            try:
                args = tool.InputSchema.model_validate(call.input)
            except ValidationError as e:
                tool_result_messages.append(ChatMessage(
                    role="tool", tool_call_id=call.id,
                    text=f"Invalid input: {e}", is_error=True,
                ))
                continue

            try:
                result = await tool.run(tool_ctx, args)
            except ToolError as e:
                tool_result_messages.append(ChatMessage(
                    role="tool", tool_call_id=call.id,
                    text=f"Tool error ({e.code}): {e.message}", is_error=True,
                ))
                continue
            except Exception as e:
                logger.exception("ai-accountant: tool %s crashed", call.name)
                tool_result_messages.append(ChatMessage(
                    role="tool", tool_call_id=call.id,
                    text=f"Internal tool error: {type(e).__name__}: {e}", is_error=True,
                ))
                continue

            import json as _json
            tool_result_messages.append(ChatMessage(
                role="tool", tool_call_id=call.id,
                text=_json.dumps(result, default=str),
            ))
            log_entry["result"] = result
            if tool.category == "proposal" and isinstance(result, dict) and "confirmation_token" in result:
                proposals.append(result)
            # Remember entity candidates so we can offer a "pick one" partial
            # if the model never converges before MAX_TURNS (AI-1).
            if call.name in ("find_entity", "list_entities") and isinstance(result, dict):
                cands = result.get("matches") or result.get("entities") or []
                if cands:
                    last_candidates = cands

        # Append each tool result as its own ChatMessage (the wire-format
        # adapter batches them appropriately for Anthropic / spreads them
        # for OpenAI).
        for trm in tool_result_messages:
            history.append(trm)
            _persist_message(
                db, session_id=chat_session.id, role="tool",
                content=trm.to_dict(),
            )

    logger.warning(
        "ai-accountant: hit MAX_TURNS=%d for session %s — returning partial result",
        MAX_TURNS, session_uuid_str,
    )
    # Graceful partial instead of a dead-end (AI-1). If the model was stuck
    # resolving an entity, surface the candidates it found and ask the user
    # to pick. Otherwise ask for a little more detail. Localized (AI-2).
    if final_text:
        fallback_text = final_text
    elif last_candidates:
        listed = "\n".join(
            f"  • {c.get('name', '?')}"
            + (f" ({c.get('type')})" if c.get("type") else "")
            for c in last_candidates[:5]
        )
        fallback_text = _status(lang, "max_turns_candidates").format(candidates=listed)
    else:
        fallback_text = _status(lang, "max_turns_dead_end")
    return ChatResult(
        session_id=session_uuid_str,
        text=fallback_text,
        proposals=proposals,
        tool_calls=tool_call_log,
        stop_reason=stop_reason,
        turns=MAX_TURNS,
        provider_shape=shape,
    )
